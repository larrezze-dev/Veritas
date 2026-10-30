"""Traductor determinista del modelo de dominio a una petición espacial (plan T-018, D3-D4).

Función pura: no importa OpenSeesPy ni hace E/S y no modifica el modelo. Rechaza con
``UNSUPPORTED_CONFIGURATION`` todo lo que no admite (madera, propiedad no disponible,
Iyz ≠ 0, Ux de extremo no rígido con carga axial distribuida, tipo de carga desconocido);
nunca lo ignora en silencio.

Extremos (D5-A, E2-a, plan T-018 pieza E2.2), una representación por componente: Ry/Rz
liberados con ``-releasey``/``-releasez``; Rx y Ux liberados o con resorte en serie (J y A
efectivos); Uy/Uz no rígidos y Ry/Rz con resorte en un nudo aux, exacto si sus direcciones
generan un subespacio coordenado global y, si no, con penalización iterada en las rígidas
(ADR-004: ``end_penalty_factor``, dirección global b_s y escala S_s del caso). Los giros
huérfanos coordenados sin momento se fijan con aviso (R4).

Mapeo: base global de ADR-001 con seis GDL por nudo; ``vecxz`` = ez de la barra (hecho 1);
carga nodal global (Fx, Fy, Fz, Mx, My, Mz); carga de barra ``-beamUniform`` (Wy, Wz, Wx)
en la base local de ADR-001 (hecho 9). Cargas de barra de Veritas (T-015, ADR-005): p0
local de ``member_loads``, condensado en los GDL de ``-release*`` y aplicado como −Tᵀ·p0_c en
los nudos del elemento (``member_end_loads``). Apoyos descompuestos eje a eje de su base (D4):
``fix`` en los ejes alineados con un eje global, cadena exacta en las traslaciones fijas
según una normal no global, ``zeroLength`` orientado en los resortes y penalización solo en
los giros fijos parciales de una base oblicua (D4-A), salvo que la fila libre esté alineada
con un eje global: entonces ``fix`` exacto en los otros dos (PROP-1). Tags (D6 de T-007):
nudos y barras por orden del texto UUID canónico; los nudos auxiliares (tierras, C, R) y
los elementos de apoyo siguen, en el orden de tag de su nudo.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Final
from uuid import UUID

from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    InputValueError,
    Location,
    Severity,
)
from veritas.core.geometry.model import StructuralModel
from veritas.core.geometry.vector import Vec3
from veritas.core.loads.loads import (
    LoadBasis,
    MemberDistributedLoad,
    MemberDistributedMoment,
    MemberPointLoad,
    MemberUniformLoad,
    NodalLoad,
    SelfWeight,
    SupportSettlement,
)
from veritas.core.supports.support import BasisKind, RestraintKind, Support, SupportDof
from veritas.fem.adapters.planar_xz import (
    NEAR_RIGID_RATIO,
    SUPPORTED_FAMILIES,
    _available,
    _ordered,
    _unsupported,
)
from veritas.fem.member_loads import (
    distributed_fixed_end_forces,
    distributed_moment_fixed_end_forces,
    point_fixed_end_forces,
)
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.protocol import (
    AXES,
    DOFS_PER_NODE,
    SpatialAuxNode,
    SpatialBeam,
    SpatialBeamLoad,
    SpatialChain,
    SpatialEndEqual,
    SpatialEndPenalty,
    SpatialEndSpring,
    SpatialFix,
    SpatialMemberEndLoad,
    SpatialNodalLoad,
    SpatialNode,
    SpatialOrphanFix,
    SpatialRequest,
    SpatialSettlement,
    SpatialSpring,
)
from veritas.fem.spatial.protocol import Vec3 as Triple
from veritas.fem.spatial.stiffness import (
    UNIT_AXES,
    beam_axes,
    beam_matrices,
    condense_beam,
    cross,
    dot,
    force_scale,
    local_beam_stiffness,
    released_indices,
    spring_direction,
)

HYPOTHESIS_SPATIAL: Final = "fem3d.hypothesis_spatial"
HYPOTHESIS_BEAM: Final = "fem3d.hypothesis_euler_bernoulli"
HYPOTHESIS_LAGRANGE: Final = "fem3d.hypothesis_lagrange"
HYPOTHESIS_CHAIN: Final = "fem3d.hypothesis_inclined_chain"
HYPOTHESIS_PENALTY: Final = "fem3d.hypothesis_penalty"  # Apoyos D4.
HYPOTHESIS_END_PENALTY: Final = "fem3d.hypothesis_end_penalty_iterated"  # ADR-004 §8.
HYPOTHESIS_END_RELEASES: Final = "fem3d.hypothesis_end_releases"
HYPOTHESIS_END_SERIES: Final = "fem3d.hypothesis_end_series"
HYPOTHESIS_END_LINKS: Final = "fem3d.hypothesis_end_links"
HYPOTHESIS_ORPHAN: Final = "fem3d.hypothesis_orphan_rotation"
# E2.2-a (R9): Wx = 0 de una barra con Ux de extremo no rígido, relativo a Σ|q| de sus cargas.
TOL_AXIAL_LOAD: Final = 1e-9
# Componentes que representa el aux (E2-a, R3): Uy/Uz no rígidos y Ry/Rz con resorte.
AUX_TRANSLATIONS: Final = (Dof.UY, Dof.UZ)
AUX_ROTATIONS: Final = (Dof.RY, Dof.RZ)
DOFS: Final = tuple(Dof)  # Orden local de ADR-001: Ux, Uy, Uz, Rx, Ry, Rz.

type Fixity = tuple[int, int, int, int, int, int]
type MemberLoad = MemberPointLoad | MemberDistributedLoad | MemberDistributedMoment


@dataclass(frozen=True, slots=True)
class SpatialTranslation:
    """Petición, avisos e hipótesis, y dos datos de la traducción que solo leen los tests:
    ``max_diagonal`` (D de la calibración de D3, que la petición lleva como alpha) y
    ``chain_length`` (a de la cadena, D4, que la petición lleva en cada cadena).
    """

    request: SpatialRequest
    warnings: tuple[Diagnostic, ...]
    hypotheses: tuple[str, ...]
    max_diagonal: float
    chain_length: float
    orphan_loaded: tuple[UUID, ...] = ()


def aligned_axis(row: Vec3, tol_axis: float) -> int | None:
    """Eje global (0, 1, 2) con el que coincide ``row`` (unitario) en forma seno, o ``None``.

    La componente dominante k cuenta como alineada si la norma de las otras dos es
    ≤ ``tol_axis`` (seno del ángulo entre ``row`` y el eje k).
    """
    values = row.components()
    k = max(range(AXES), key=lambda i: abs(values[i]))
    others = [values[i] for i in range(AXES) if i != k]
    return k if (others[0] ** 2 + others[1] ** 2) ** 0.5 <= tol_axis else None


def coordinate_axes(directions: list[Triple], tol_axis: float) -> frozenset[int] | None:
    """Ejes globales cuyo subespacio generan una o dos direcciones ortonormales, o ``None``.

    Una dirección: el eje con el que está alineada. Dos: los dos ejes ortogonales a su
    normal, si la normal está alineada (E2-a). Mismo criterio seno que D4 (``tol_axis``).
    """
    if len(directions) == 1:
        k = aligned_axis(Vec3(*directions[0]), tol_axis)
        return None if k is None else frozenset({k})
    first, second = directions
    k = aligned_axis(Vec3(*cross(first, second)), tol_axis)
    return None if k is None else frozenset(range(AXES)) - {k}


def end_released(code: int, end: str) -> bool:
    """True si el código de ``-release*`` (0-3) libera ese extremo (bit 1 = I, bit 2 = J)."""
    return bool(code & (1 if end == "I" else 2))


def _span(directions: list[Triple], tol_axis: float) -> list[Triple]:
    """Base ortonormal de lo que generan las direcciones (Gram-Schmidt, dos pasadas).

    Una dirección con residuo ≤ ``tol_axis`` cuenta como dependiente (E2.2-b)."""
    basis: list[Triple] = []
    for direction in directions:
        residual = direction
        for _ in range(2):
            for b in basis:
                c = dot(residual, b)
                residual = (residual[0] - c * b[0], residual[1] - c * b[1], residual[2] - c * b[2])
        norm = math.sqrt(dot(residual, residual))
        if norm > tol_axis:
            basis.append((residual[0] / norm, residual[1] / norm, residual[2] / norm))
        if len(basis) == AXES:
            break
    return basis


@dataclass(frozen=True, slots=True)
class OrphanRotation:
    """Subespacio de giro sin rigidez de un nudo (R1, R4): base ortonormal y, si es
    coordenado global, sus ejes (0-2); ``None`` si es oblicuo (mecanismo en E2.3)."""

    node: int
    basis: tuple[Triple, ...]
    axes: frozenset[int] | None


def orphan_rotations(request: SpatialRequest, tol_axis: float) -> tuple[OrphanRotation, ...]:
    """Giros huérfanos por nudo de dominio, como subespacio (plan T-018, E2.2; R1).

    Dan rigidez de giro: cada extremo incidente en sus direcciones locales no liberadas
    (ex solo si la barra entera tiene J > 0, R1; ey/ez salvo ``-release*`` en ese extremo;
    los resortes del aux sí cuentan) y cada apoyo en sus giros fijos o con resorte.
    """
    coordinates = {n.tag: (n.x, n.y, n.z) for n in request.nodes}
    stiff: dict[int, list[Triple]] = {tag: [] for tag in coordinates}
    for fix in request.fixes:
        stiff[fix.node] += [UNIT_AXES[k] for k in range(AXES) if fix.fixity[AXES + k]]
    for spring in request.springs:
        if spring.dof > AXES:
            stiff[spring.node].append(spring_direction(spring.axis_x, spring.axis_yp, spring.dof))
    for beam in request.beams:
        _, (ex, ey, ez) = beam_axes(coordinates[beam.node_i], coordinates[beam.node_j], beam.vecxz)
        for end, node in (("I", beam.node_i), ("J", beam.node_j)):
            if beam.torsion_constant > 0.0:
                stiff[node].append(ex)
            if not end_released(beam.release_y, end):
                stiff[node].append(ey)
            if not end_released(beam.release_z, end):
                stiff[node].append(ez)
    result: list[OrphanRotation] = []
    for node in sorted(stiff):
        span = _span(stiff[node], tol_axis)
        if len(span) == AXES:
            continue
        orphan = _complement(span)
        axes = frozenset(range(AXES)) if not span else coordinate_axes(orphan, tol_axis)
        result.append(OrphanRotation(node, tuple(orphan), axes))
    return tuple(result)


def _complement(span: list[Triple]) -> list[Triple]:
    """Base ortonormal del complemento de un subespacio de dimensión 0, 1 o 2."""
    if not span:
        return list(UNIT_AXES)
    if len(span) == AXES - 1:
        normal = cross(span[0], span[1])
        norm = math.sqrt(dot(normal, normal))
        return [(normal[0] / norm, normal[1] / norm, normal[2] / norm)]
    (s,) = span
    # El eje global menos alineado con s da el residuo mejor condicionado.
    axis = min(UNIT_AXES, key=lambda a: abs(dot(a, s)))
    c = dot(axis, s)
    first = (axis[0] - c * s[0], axis[1] - c * s[1], axis[2] - c * s[2])
    norm = math.sqrt(dot(first, first))
    first = (first[0] / norm, first[1] / norm, first[2] / norm)
    return [first, cross(s, first)]


@dataclass(frozen=True, slots=True)
class _PendingEnd:
    """Extremo con aux pendiente de tags (tras los auxiliares de apoyo)."""

    member_id: UUID
    label: str
    beam: int
    end: MemberEnd
    master: int
    connection: EndConnection
    axes: tuple[Triple, Triple, Triple]
    length: float
    elastic_modulus: float
    inertia_y: float
    inertia_z: float


class _Builder:
    def __init__(self, model: StructuralModel, config: SpatialAnalysisConfiguration) -> None:
        self.model = model
        self.config = config
        self.node_tags: dict[UUID, int] = {}
        self.beam_tags: dict[UUID, int] = {}
        self.fixity: dict[int, list[int]] = {}
        self.springs: list[SpatialSpring] = []
        self.chains: list[SpatialChain] = []
        self.grounds: dict[int, int] = {}  # Una tierra por nudo apoyado, con el primer resorte.
        # Escala de la penalización de giro por nudo: Σ máx(4EIy/L, 4EIz/L, GJ/L); de
        # traslación (E2-a): Σ máx(EA/L, 12EIy/L³, 12EIz/L³). Con A y J de la sección.
        self.rotation_scale: dict[int, float] = {}
        self.translation_scale: dict[int, float] = {}
        self.pending_ends: list[_PendingEnd] = []
        self.axial_ends: dict[int, UUID] = {}  # Barras con Ux de extremo no rígido (E2-d).
        self.twist_ends: set[int] = set()  # Barras con Rx de extremo no rígido (T-015, D8).
        self.member_loads: list[MemberLoad] = []  # Cargas de barra de Veritas (T-015).
        self.aux_nodes: list[SpatialAuxNode] = []
        self.end_equals: list[SpatialEndEqual] = []
        self.end_springs: list[SpatialEndSpring] = []
        self.end_warnings: list[Diagnostic] = []
        self.uses_series = False
        self.max_diagonal = 0.0
        self.chain_length = 0.0
        self.next_node = 0
        self.next_element = 0

    def nodes(self) -> list[SpatialNode]:
        if not self.model.members:
            raise _unsupported("fem.no_members", {})
        connected = {m.node_i for m in self.model.members.values()}
        connected |= {m.node_j for m in self.model.members.values()}
        result: list[SpatialNode] = []
        for tag, (node_id, node) in enumerate(_ordered(self.model.nodes), start=1):
            if node_id not in connected:
                raise _unsupported(
                    "fem.isolated_node",
                    {"node": node.label or str(node_id)},
                    Location(entity_id=node_id, revision=node.revision),
                )
            self.node_tags[node_id] = tag
            p = node.position
            result.append(SpatialNode(tag, str(node_id), p.x, p.y, p.z))
        self.next_node = len(result) + 1
        return result

    def beams(self) -> list[SpatialBeam]:
        result: list[SpatialBeam] = []
        for tag, (member_id, member) in enumerate(_ordered(self.model.members), start=1):
            location = Location(entity_id=member_id, revision=member.revision)
            label = member.label or str(member_id)
            material = self.model.material(member.material)
            if material.family not in SUPPORTED_FAMILIES:
                raise _unsupported(
                    "fem.material_family_unsupported",
                    {"member": label, "family": str(material.family)},
                    location,
                )
            props = self.model.section(member.section).properties
            area = _available(props.area, "A", location)
            inertia_y = _available(props.iy, "Iy", location)
            inertia_z = _available(props.iz, "Iz", location)
            torsion = _available(props.torsion_constant, "J", location)
            iyz = _available(props.iyz, "Iyz", location)
            if iyz != 0.0:
                raise _unsupported(
                    "fem.section_coupled_bending", {"member": label, "iyz": iyz}, location
                )
            axes = self.model.axes(member_id)
            e, g, length = material.elastic_modulus, material.shear_modulus, axes.length
            node_i, node_j = self.node_tags[member.node_i], self.node_tags[member.node_j]
            self.beam_tags[member_id] = tag
            # D de la calibración de D3: diagonales locales de la barra, sin resortes.
            self.max_diagonal = max(
                self.max_diagonal,
                e * area / length,
                12.0 * e * inertia_y / length**3,
                12.0 * e * inertia_z / length**3,
                4.0 * e * inertia_y / length,
                4.0 * e * inertia_z / length,
                g * torsion / length,
            )
            rotation = max(4.0 * e * inertia_y, 4.0 * e * inertia_z, g * torsion) / length
            translation = max(e * area / length, 12.0 * e * max(inertia_y, inertia_z) / length**3)
            for node in (node_i, node_j):
                self.rotation_scale[node] = self.rotation_scale.get(node, 0.0) + rotation
                self.translation_scale[node] = self.translation_scale.get(node, 0.0) + translation
            self.chain_length = max(self.chain_length, length)
            ends = ((MemberEnd.I, member.end_i, node_i), (MemberEnd.J, member.end_j, node_j))
            frame = (axes.ex.components(), axes.ey.components(), axes.ez.components())
            for end, connection, node in ends:
                if any(_represented(connection, d) for d in (*AUX_TRANSLATIONS, *AUX_ROTATIONS)):
                    self.pending_ends.append(
                        _PendingEnd(
                            member_id,
                            label,
                            tag,
                            end,
                            node,
                            connection,
                            frame,
                            length,
                            e,
                            inertia_y,
                            inertia_z,
                        )
                    )
            connections = (member.end_i, member.end_j)
            # Por estado, nunca comparando floats (revisión de E2.2, M1): un extremo rígido
            # conserva el valor de la sección bit a bit, como en E1.
            axial = _non_rigid(connections, Dof.UX)
            twist = _non_rigid(connections, Dof.RX)
            effective_area = _effective(connections, Dof.UX, length, area, e)
            effective_torsion = _effective(connections, Dof.RX, length, torsion, g)
            if axial:
                self.axial_ends[tag] = member_id
            if twist:
                self.twist_ends.add(tag)
            self.uses_series |= axial or twist
            result.append(
                SpatialBeam(
                    tag,
                    str(member_id),
                    node_i,
                    node_j,
                    effective_area,
                    e,
                    g,
                    effective_torsion,
                    inertia_y,
                    inertia_z,
                    axes.ez.components(),
                    _release_code(connections, Dof.RY),
                    _release_code(connections, Dof.RZ),
                    _released_any(connections, Dof.UX),
                    _released_any(connections, Dof.RX),
                )
            )
        self.next_element = len(result) + 1
        return result

    def _node_tag(self) -> int:
        tag = self.next_node
        self.next_node += 1
        return tag

    def _element_tag(self) -> int:
        tag = self.next_element
        self.next_element += 1
        return tag

    def supports(self) -> None:
        ordered = sorted(self.model.supports.values(), key=lambda s: self.node_tags[s.node_id])
        for support in ordered:
            self._support(support, self.node_tags[support.node_id])

    def _support(self, support: Support, node: int) -> None:
        """Descomposición eje a eje de la base del apoyo (D4 y PROP-1).

        Con ``tol_axis`` ≤ ``MAX_TOL_AXIS`` dos filas ortonormales nunca se alinean con el
        mismo eje global.
        """
        rows = tuple(Vec3(*row) for row in support.basis.matrix())
        fixity = self.fixity.setdefault(node, [0] * 6)
        for offset in (0, AXES):  # Traslaciones y giros.
            restraints = [support.restraint(SupportDof(offset + i)) for i in range(AXES)]
            if all(r.kind is RestraintKind.FIXED for r in restraints):
                fixity[offset : offset + AXES] = [1, 1, 1]
                continue
            fixed = [i for i, r in enumerate(restraints) if r.kind is RestraintKind.FIXED]
            if len(fixed) == AXES - 1:
                # PROP-1: si la fila no fija está alineada con e_k, fijar las otras dos
                # filas equivale exactamente a fijar los otros dos ejes globales.
                (other,) = set(range(AXES)) - set(fixed)
                k = aligned_axis(rows[other], self.config.tol_axis)
                if k is not None:
                    for other_axis in set(range(AXES)) - {k}:
                        fixity[offset + other_axis] = 1
                    fixed = []
            for i, restraint in enumerate(restraints):
                if restraint.kind is RestraintKind.FREE:
                    continue
                if restraint.kind is RestraintKind.SPRING:
                    assert restraint.stiffness is not None
                    self._spring(support, node, rows, dof=offset + i, stiffness=restraint.stiffness)
                    continue
                if i not in fixed:  # Ya representada por PROP-1.
                    continue
                axis = aligned_axis(rows[i], self.config.tol_axis)
                if axis is not None:
                    fixity[offset + axis] = 1
                elif offset == 0:
                    self._chain(support, node, rows[i], rows[(i + 1) % AXES])
                else:  # Giro fijo parcial en base oblicua: penalización (D4-A).
                    stiffness = self.config.penalty_factor * self.rotation_scale[node]
                    self._spring(
                        support, node, rows, dof=offset + i, stiffness=stiffness, penalty=True
                    )

    def _spring(  # noqa: PLR0913 (campos del resorte, por nombre)
        self,
        support: Support,
        node: int,
        rows: tuple[Vec3, ...],
        *,
        dof: int,
        stiffness: float,
        penalty: bool = False,
    ) -> None:
        if node not in self.grounds:
            self.grounds[node] = self._node_tag()
        self.springs.append(
            SpatialSpring(
                self._element_tag(),
                str(support.id),
                node,
                self.grounds[node],
                dof + 1,
                stiffness,
                rows[0].components(),
                rows[1].components(),
                penalty,
            )
        )

    def _chain(self, support: Support, node: int, normal: Vec3, tangent: Vec3) -> None:
        tag = self._element_tag()
        c_node, r_node, ground = self._node_tag(), self._node_tag(), self._node_tag()
        self.chains.append(
            SpatialChain(
                tag,
                str(support.id),
                node,
                c_node,
                r_node,
                ground,
                normal.components(),
                tangent.components(),
                self.chain_length,
                self.max_diagonal,
            )
        )

    def finish_ends(self) -> None:
        """Aux de extremo tras los auxiliares de apoyo, por (barra, extremo) (E2-a, R3)."""
        for pending in sorted(self.pending_ends, key=lambda p: (p.beam, str(p.end))):
            aux = self._node_tag()
            self.aux_nodes.append(
                SpatialAuxNode(aux, pending.master, pending.beam, str(pending.end))
            )
            equal: list[int] = []
            for offset, dofs in ((0, AUX_TRANSLATIONS), (AXES, AUX_ROTATIONS)):
                equal += self._terna(pending, aux, offset, dofs)
            if equal:
                self.end_equals.append(SpatialEndEqual(aux, tuple(sorted(equal))))

    def _terna(
        self, pending: _PendingEnd, aux: int, offset: int, dofs: tuple[Dof, ...]
    ) -> list[int]:
        """Una terna del aux; devuelve sus GDL globales (1-6) atados con ``equalDOF``."""
        connection = pending.connection
        represented = [d for d in dofs if _represented(connection, d)]
        if not represented:
            return [offset + k + 1 for k in range(AXES)]
        directions = [pending.axes[d.position - offset] for d in represented]
        coordinate = coordinate_axes(directions, self.config.tol_axis)
        if coordinate is not None:  # Exacto: los rígidos son los ejes globales restantes.
            for d in represented:
                self._end_spring(pending, aux, d)
            return [offset + k + 1 for k in range(AXES) if k not in coordinate]
        for position in range(offset, offset + AXES):  # Penalización en las rígidas (E2-a).
            d = DOFS[position]
            if d in represented:
                self._end_spring(pending, aux, d)
            else:
                scale = self.rotation_scale if offset else self.translation_scale
                stiffness = self.config.end_penalty_factor * scale[pending.master]
                self._add_end_spring(pending, aux, position + 1, stiffness, penalty=True)
        return []

    def _end_spring(self, pending: _PendingEnd, aux: int, dof: Dof) -> None:
        stiffness = pending.connection.stiffness[dof.position]
        if stiffness is None:  # Uy/Uz liberados: el aux no tiene resorte en esa dirección.
            return
        self._add_end_spring(pending, aux, dof.position + 1, stiffness, penalty=False)
        self._near_rigid(pending, dof, stiffness)

    def _add_end_spring(
        self, pending: _PendingEnd, aux: int, dof: int, stiffness: float, *, penalty: bool
    ) -> None:
        self.end_springs.append(
            SpatialEndSpring(
                self._element_tag(),
                aux,
                dof,
                stiffness,
                pending.axes[0],
                pending.axes[1],
                penalty,
            )
        )

    def _near_rigid(self, pending: _PendingEnd, dof: Dof, stiffness: float) -> None:
        """E2.2-c: resorte de aux casi rígido (Uy flecta con Iz y Uz con Iy)."""
        e, ell = pending.elastic_modulus, pending.length
        reference = {
            Dof.RY: e * pending.inertia_y / ell,
            Dof.RZ: e * pending.inertia_z / ell,
            Dof.UY: 12.0 * e * pending.inertia_z / ell**3,
            Dof.UZ: 12.0 * e * pending.inertia_y / ell**3,
        }[dof]
        ratio = stiffness / reference
        if ratio <= NEAR_RIGID_RATIO:
            return
        member = self.model.member(pending.member_id)
        self.end_warnings.append(
            Diagnostic.create(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "fem3d.end_spring_near_rigid",
                {
                    "member": pending.label,
                    "end": str(pending.end),
                    "dof": str(dof),
                    "ratio": ratio,
                    "limit": NEAR_RIGID_RATIO,
                },
                severity=Severity.WARNING,
                location=Location(entity_id=member.id, revision=member.revision),
            )
        )

    def check_axial_loads(self, beam_loads: list[SpatialBeamLoad]) -> None:
        """E2-d (R9): Ux de extremo no rígido exige Wx = 0 en el caso activo."""
        for beam, member_id in sorted(self.axial_ends.items()):
            loads = [w for w in beam_loads if w.beam == beam]
            wx = sum(w.wx for w in loads)
            total = sum(math.sqrt(w.wx**2 + w.wy**2 + w.wz**2) for w in loads)
            if abs(wx) > TOL_AXIAL_LOAD * total:
                member = self.model.member(member_id)
                raise _unsupported(
                    "fem3d.end_axial_with_axial_load",
                    {"member": member.label or str(member_id), "wx": wx},
                    Location(entity_id=member_id, revision=member.revision),
                )

    def check_member_loads(self) -> None:
        """D8 (T-015, E2.2-a): con Ux no rígido, ninguna fuerza axial, y con Rx no rígido,
        ningún par torsor, carga a carga y vector a vector en base local (|c_x| ≤
        ``TOL_AXIAL_LOAD``·‖c‖): A y J efectivos solo son exactos con N y T constantes."""
        for load in self.member_loads:
            beam = self.beam_tags[load.member_id]
            forces, moments = self._local_vectors(load)
            for vectors, applies, key, param in (
                (forces, beam in self.axial_ends, "fem3d.end_axial_with_axial_load", "wx"),
                (moments, beam in self.twist_ends, "fem3d.end_torsion_with_torsional_load", "mx"),
            ):
                for vector in vectors if applies else ():
                    if abs(vector.x) > TOL_AXIAL_LOAD * vector.norm():
                        member = self.model.member(load.member_id)
                        raise _unsupported(
                            key,
                            {"member": member.label or str(load.member_id), param: vector.x},
                            Location(entity_id=load.id, revision=load.revision),
                        )

    def _local_vectors(self, load: MemberLoad) -> tuple[list[Vec3], list[Vec3]]:
        """(fuerzas, pares o momentos) de la carga en la base local de ADR-001."""
        axes = self.model.axes(load.member_id)

        def local(vector: Vec3) -> Vec3:
            return axes.to_local(vector) if load.basis is LoadBasis.GLOBAL else vector

        if isinstance(load, MemberPointLoad):
            return [local(load.force)], [local(load.moment)]
        ends = [local(load.intensity_start), local(load.intensity_end)]
        return (ends, []) if isinstance(load, MemberDistributedLoad) else ([], ends)

    def member_end_loads(self, request: SpatialRequest) -> list[SpatialMemberEndLoad]:
        """−Tᵀ·p0_c por barra en los nudos del elemento (T-015, D6-A): p0 local de
        ``member_loads`` con la L y la T de ``beam_matrices`` (las de la salvaguarda),
        condensado con ``condense_beam(…, length=L)`` si la barra tiene ``-release*``."""
        beams = {beam.tag: beam for beam in request.beams}
        totals: dict[int, list[float]] = {}
        for load in self.member_loads:
            tag = self.beam_tags[load.member_id]
            length = beam_matrices(request, beams[tag]).length
            p0 = _fixed_end_forces(load, self._local_vectors(load), length)
            current = totals.setdefault(tag, [0.0] * 2 * DOFS_PER_NODE)
            for k, value in enumerate(p0):
                current[k] += value
        result: list[SpatialMemberEndLoad] = []
        for tag in sorted(totals):
            beam, p0 = beams[tag], totals[tag]
            matrices = beam_matrices(request, beam)
            if released_indices(beam):
                stiffness = local_beam_stiffness(beam, matrices.length)
                _, condensed = condense_beam(beam, stiffness, p0, length=matrices.length)
                assert condensed is not None
                p0 = condensed
            t = matrices.transformation
            size = 2 * DOFS_PER_NODE
            g = [-sum(t[m][i] * p0[m] for m in range(size)) for i in range(size)]
            for end, offset in (("I", 0), ("J", DOFS_PER_NODE)):
                a, b, c, d, e, f = g[offset : offset + DOFS_PER_NODE]
                result.append(
                    SpatialMemberEndLoad(tag, request.element_node(beam, end), (a, b, c, d, e, f))
                )
        return result

    def loads(
        self, case_id: UUID
    ) -> tuple[list[SpatialNodalLoad], list[SpatialBeamLoad], list[SpatialSettlement]]:
        loads = sorted(self.model.loads_in_case(case_id), key=lambda load: str(load.id))
        case = self.model.load_case(case_id)
        if not loads:
            raise InputValueError(
                Diagnostic.create(
                    DiagnosticCode.INPUT_OUT_OF_RANGE,
                    "fem.empty_case",
                    {"case": case.name},
                    location=Location(entity_id=case.id, revision=case.revision),
                )
            )
        nodal: list[SpatialNodalLoad] = []
        beam: list[SpatialBeamLoad] = []
        settlements: list[SpatialSettlement] = []
        for load in loads:
            if isinstance(load, NodalLoad):
                values = (*load.force.components(), *load.moment.components())
                nodal.append(SpatialNodalLoad(self.node_tags[load.node_id], values))
            elif isinstance(load, MemberUniformLoad):
                q = load.intensity
                if load.basis is LoadBasis.GLOBAL:
                    q = self.model.axes(load.member_id).to_local(q)
                beam.append(self._beam_load(load.member_id, q))
            elif isinstance(load, SelfWeight):
                for member_id, _ in _ordered(self.model.members):
                    weight = Vec3(*(c.si_value for c in self.model.self_weight_load(member_id)))
                    beam.append(
                        self._beam_load(member_id, self.model.axes(member_id).to_local(weight))
                    )
            elif isinstance(load, SupportSettlement):
                settlements.extend(self._settlement(load))
            elif isinstance(
                load, MemberPointLoad | MemberDistributedLoad | MemberDistributedMoment
            ):
                self.member_loads.append(load)  # T-015: −Tᵀ·p0_c tras formar la petición.
            else:  # Nunca se descarta en silencio.
                raise _unsupported(
                    "fem3d.load_type_unsupported",
                    {"type": type(load).__name__},
                    Location(entity_id=load.id, revision=load.revision),
                )
        # D4-bis-A: con asientos, ninguna penalización ni cadena en toda la petición.
        penalized = any(s.penalty for s in self.springs) or any(s.penalty for s in self.end_springs)
        if settlements and (penalized or self.chains):
            raise _unsupported(
                "fem.settlement_with_penalty_unsupported",
                {},
                Location(entity_id=case.id, revision=case.revision),
            )
        return nodal, beam, sorted(settlements, key=lambda t: (t.node, t.dof))

    def _settlement(self, load: SupportSettlement) -> list[SpatialSettlement]:
        """Asiento (T-016): componentes globales en los GDL 1-6; solo apoyos de base global
        (D4-A)."""
        support = self.model.supports[load.support_id]
        if support.basis.kind is not BasisKind.GLOBAL:
            raise _unsupported(
                "fem.settlement_inclined_unsupported",
                {},
                Location(entity_id=load.id, revision=load.revision),
            )
        node = self.node_tags[support.node_id]
        values = (*load.translation.components(), *load.rotation.components())
        return [
            SpatialSettlement(node, dof, value)
            for dof, value in enumerate(values, start=1)
            if value != 0.0
        ]

    def _beam_load(self, member_id: UUID, local: Vec3) -> SpatialBeamLoad:
        return SpatialBeamLoad(self.beam_tags[member_id], local.y, local.z, local.x)


def translate_spatial(
    model: StructuralModel, config: SpatialAnalysisConfiguration
) -> SpatialTranslation:
    """Traduce el modelo y el caso ``config.load_case_id`` a una petición espacial."""
    if not isinstance(model, StructuralModel):
        raise TypeError("model debe ser StructuralModel")
    if not isinstance(config, SpatialAnalysisConfiguration):
        raise TypeError("config debe ser SpatialAnalysisConfiguration")
    case = model.load_case(config.load_case_id)
    builder = _Builder(model, config)
    nodes = builder.nodes()
    beams = builder.beams()
    builder.supports()
    builder.finish_ends()
    nodal, beam_loads, settlements = builder.loads(case.id)
    builder.check_axial_loads(beam_loads)
    builder.check_member_loads()
    fixity = sorted(builder.fixity.items())
    fixes = tuple(SpatialFix(node, _fixity(bits)) for node, bits in fixity if any(bits))
    # Lagrange con alphaSP = alphaMP = D (calibración de D3, hecho 13).
    request = SpatialRequest(
        model.model_revision,
        str(case.id),
        case.revision,
        builder.max_diagonal,
        builder.max_diagonal,
        tuple(nodes),
        tuple(beams),
        fixes,
        tuple(builder.springs),
        tuple(builder.chains),
        tuple(nodal),
        tuple(beam_loads),
        aux_nodes=tuple(builder.aux_nodes),
        end_equals=tuple(builder.end_equals),
        end_springs=tuple(builder.end_springs),
        settlements=tuple(settlements),
    )
    if builder.member_loads:  # Antes de huérfanos y escalas: ambos leen el campo (D7).
        request = replace(request, member_end_loads=tuple(builder.member_end_loads(request)))
    restrained, loaded = _orphan_fixes(request, config.tol_axis)
    request = replace(
        request,
        orphan_fixes=restrained,
        end_penalties=_end_penalties(request),
        end_penalty_tolerance=config.end_penalty_tolerance,
        end_penalty_stagnation=config.tol_penalty,
        end_penalty_max_iterations=config.end_penalty_max_iterations,
    )
    node_ids = {tag: node_id for node_id, tag in builder.node_tags.items()}
    orphan_warnings = [_orphan_warning(model, node_ids[o.node], o.rotations) for o in restrained]
    hypotheses = [HYPOTHESIS_SPATIAL, HYPOTHESIS_BEAM, HYPOTHESIS_LAGRANGE]
    if builder.chains:
        hypotheses.append(HYPOTHESIS_CHAIN)
    if any(s.penalty for s in builder.springs):
        hypotheses.append(HYPOTHESIS_PENALTY)
    if request.end_penalties:
        hypotheses.append(HYPOTHESIS_END_PENALTY)
    if any(b.release_y or b.release_z for b in beams):
        hypotheses.append(HYPOTHESIS_END_RELEASES)
    if builder.uses_series:
        hypotheses.append(HYPOTHESIS_END_SERIES)
    if builder.aux_nodes:
        hypotheses.append(HYPOTHESIS_END_LINKS)
    if restrained:
        hypotheses.append(HYPOTHESIS_ORPHAN)
    return SpatialTranslation(
        request,
        (*builder.end_warnings, *orphan_warnings),
        tuple(hypotheses),
        builder.max_diagonal,
        builder.chain_length,
        tuple(node_ids[tag] for tag in loaded),
    )


def _end_penalties(request: SpatialRequest) -> tuple[SpatialEndPenalty, ...]:
    """b_s y S_s de cada resorte de penalización de extremo (ADR-004 §1 y §3): S_s = F en
    traslaciones y F·L_máx en giros."""
    penalized = [s for s in request.end_springs if s.penalty]
    if not penalized:
        return ()
    force, longest = force_scale(request)
    return tuple(
        SpatialEndPenalty(
            s.tag,
            spring_direction(s.axis_x, s.axis_yp, s.dof),
            force if s.dof <= AXES else force * longest,
        )
        for s in penalized
    )


def _fixed_end_forces(
    load: MemberLoad, vectors: tuple[list[Vec3], list[Vec3]], length: float
) -> list[float]:
    """p0 local 12 de una carga de barra de Veritas (``member_loads``, ADR-005)."""
    forces, moments = vectors
    if isinstance(load, MemberPointLoad):
        return point_fixed_end_forces(
            load.position, forces[0].components(), moments[0].components(), length
        )
    start, end = forces or moments
    function = (
        distributed_fixed_end_forces
        if isinstance(load, MemberDistributedLoad)
        else distributed_moment_fixed_end_forces
    )
    return function(load.start, load.end, start.components(), end.components(), length)


def _represented(connection: EndConnection, dof: Dof) -> bool:
    """Componente que representa el aux (E2-a): Uy/Uz no rígidos, Ry/Rz con resorte."""
    state = connection.state(dof)
    if dof in AUX_TRANSLATIONS:
        return state is not DofState.RIGID
    return dof in AUX_ROTATIONS and state is DofState.SPRING


def _release_code(connections: tuple[EndConnection, EndConnection], dof: Dof) -> int:
    """Código de ``-releasey``/``-releasez``: bit 1 = I liberado, bit 2 = J liberado."""
    return sum(
        bit for bit, c in zip((1, 2), connections, strict=True) if c.state(dof) is DofState.RELEASED
    )


def _non_rigid(connections: tuple[EndConnection, EndConnection], dof: Dof) -> bool:
    return any(c.state(dof) is not DofState.RIGID for c in connections)


def _released_any(connections: tuple[EndConnection, EndConnection], dof: Dof) -> bool:
    return any(c.state(dof) is DofState.RELEASED for c in connections)


def _effective(
    connections: tuple[EndConnection, EndConnection],
    dof: Dof,
    length: float,
    value: float,
    modulus: float,
) -> float:
    """A o J efectivos con los extremos en serie (hechos 5 y 15); 0 si se libera.

    Exacta porque N y T son constantes en la barra (Rx sin torsor distribuido; Ux con
    Wx = 0, E2-d): L/(M·P_ef) = L/(M·P) + Σ 1/k, es decir P_ef = L/(L/P + M·Σ 1/k).
    Con los dos extremos rígidos devuelve ``value`` intacto (revisión de E2.2, M1).
    """
    if _released_any(connections, dof):
        return 0.0
    springs = [c.stiffness[dof.position] for c in connections if c.state(dof) is DofState.SPRING]
    if not springs:
        return value
    assert all(k is not None for k in springs)  # SPRING exige rigidez (EndConnection).
    return length / (length / value + modulus * sum(1.0 / k for k in springs if k is not None))


def _orphan_fixes(
    request: SpatialRequest, tol_axis: float
) -> tuple[tuple[SpatialOrphanFix, ...], list[int]]:
    """Huérfanos coordenados sin momento aplicado → ``fix`` (R4); con momento sobre ellos,
    sin fijar (mecanismo de la salvaguarda). Los oblicuos los trata E2.3.

    Un momento de ``member_end_loads`` (T-015) carga el eje solo si su componente supera
    ``tol_axis``·‖m‖ (E2.2-b): está en el subespacio rígido de la barra, y el huérfano se
    clasifica como coordenado con esa misma tolerancia."""
    moments: dict[int, list[float]] = {}
    for load in request.nodal_loads:
        current = moments.setdefault(load.node, [0.0] * AXES)
        for k in range(AXES):
            current[k] += load.values[AXES + k]
    end_moments: dict[int, list[Sequence[float]]] = {}
    for end_load in request.member_end_loads:
        end_moments.setdefault(end_load.node, []).append(end_load.values[AXES:])
    restrained: list[SpatialOrphanFix] = []
    loaded: list[int] = []
    for orphan in orphan_rotations(request, tol_axis):
        if orphan.axes is None:
            continue
        moment = moments.get(orphan.node, [0.0] * AXES)
        from_ends = any(
            abs(m[k]) > tol_axis * math.sqrt(dot(m, m))
            for m in end_moments.get(orphan.node, [])
            for k in orphan.axes
        )
        if from_ends or any(moment[k] != 0.0 for k in orphan.axes):
            loaded.append(orphan.node)
            continue
        x, y, z = (int(k in orphan.axes) for k in range(AXES))
        restrained.append(SpatialOrphanFix(orphan.node, (x, y, z)))
    return tuple(restrained), loaded


def _orphan_warning(
    model: StructuralModel, node_id: UUID, rotations: tuple[int, int, int]
) -> Diagnostic:
    node = model.node(node_id)
    names = ", ".join(n for n, bit in zip(("Rx", "Ry", "Rz"), rotations, strict=True) if bit)
    return Diagnostic.create(
        DiagnosticCode.UNSUPPORTED_CONFIGURATION,
        "fem3d.orphan_rotation_restrained",
        {"node": node.label or str(node_id), "rotations": names},
        severity=Severity.WARNING,
        location=Location(entity_id=node_id, revision=node.revision),
    )


def _fixity(bits: list[int]) -> Fixity:
    a, b, c, d, e, f = bits
    return a, b, c, d, e, f
