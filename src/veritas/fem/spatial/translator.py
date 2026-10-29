"""Traductor determinista del modelo de dominio a una petición espacial (plan T-018, D3-D4).

Función pura: no importa OpenSeesPy ni hace E/S y no modifica el modelo. Rechaza con
``UNSUPPORTED_CONFIGURATION`` todo lo que E1 no admite (madera, propiedad no disponible,
Iyz ≠ 0, extremo no rígido, tipo de carga desconocido); nunca lo ignora en silencio.

Mapeo: base global de ADR-001 con seis GDL por nudo; ``vecxz`` = ez de la barra (hecho 1);
carga nodal global (Fx, Fy, Fz, Mx, My, Mz); carga de barra ``-beamUniform`` (Wy, Wz, Wx)
en la base local de ADR-001 (hecho 9). Apoyos descompuestos eje a eje de su base (D4):
``fix`` en los ejes alineados con un eje global, cadena exacta en las traslaciones fijas
según una normal no global, ``zeroLength`` orientado en los resortes y penalización solo en
los giros fijos parciales de una base oblicua (D4-A), salvo que la fila libre esté alineada
con un eje global: entonces ``fix`` exacto en los otros dos (PROP-1). Tags (D6 de T-007):
nudos y barras por orden del texto UUID canónico; los nudos auxiliares (tierras, C, R) y
los elementos de apoyo siguen, en el orden de tag de su nudo.
"""

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from veritas.core.connections.end import Dof, DofState
from veritas.core.diagnostics import Diagnostic, DiagnosticCode, InputValueError, Location
from veritas.core.geometry.model import StructuralModel
from veritas.core.geometry.vector import Vec3
from veritas.core.loads.loads import LoadBasis, MemberUniformLoad, NodalLoad, SelfWeight
from veritas.core.supports.support import RestraintKind, Support, SupportDof
from veritas.fem.adapters.planar_xz import (
    SUPPORTED_FAMILIES,
    _available,
    _ordered,
    _unsupported,
)
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.protocol import (
    AXES,
    SpatialBeam,
    SpatialBeamLoad,
    SpatialChain,
    SpatialFix,
    SpatialNodalLoad,
    SpatialNode,
    SpatialRequest,
    SpatialSpring,
)

HYPOTHESIS_SPATIAL: Final = "fem3d.hypothesis_spatial"
HYPOTHESIS_BEAM: Final = "fem3d.hypothesis_euler_bernoulli"
HYPOTHESIS_LAGRANGE: Final = "fem3d.hypothesis_lagrange"
HYPOTHESIS_CHAIN: Final = "fem3d.hypothesis_inclined_chain"
HYPOTHESIS_PENALTY: Final = "fem3d.hypothesis_penalty"

type Fixity = tuple[int, int, int, int, int, int]


@dataclass(frozen=True, slots=True)
class SpatialTranslation:
    """Petición, avisos e hipótesis, con los datos que necesitan la salvaguarda y la
    normalización: ``max_diagonal`` (D de la calibración de D3) y ``chain_length`` (a de la
    cadena, D4).
    """

    request: SpatialRequest
    warnings: tuple[Diagnostic, ...]
    hypotheses: tuple[str, ...]
    max_diagonal: float
    chain_length: float


def aligned_axis(row: Vec3, tol_axis: float) -> int | None:
    """Eje global (0, 1, 2) con el que coincide ``row`` (unitario) en forma seno, o ``None``.

    La componente dominante k cuenta como alineada si la norma de las otras dos es
    ≤ ``tol_axis`` (seno del ángulo entre ``row`` y el eje k).
    """
    values = row.components()
    k = max(range(AXES), key=lambda i: abs(values[i]))
    others = [values[i] for i in range(AXES) if i != k]
    return k if (others[0] ** 2 + others[1] ** 2) ** 0.5 <= tol_axis else None


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
        # Escala de la penalización de giro por nudo: Σ máx(4EIy/L, 4EIz/L, GJ/L).
        self.rotation_scale: dict[int, float] = {}
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
            for end, connection in (("I", member.end_i), ("J", member.end_j)):
                if not connection.is_rigid:  # E1 (D9-A): los extremos llegan en E2.
                    dof = next(d for d in Dof if connection.state(d) is not DofState.RIGID)
                    raise _unsupported(
                        "fem3d.member_end_not_rigid",
                        {
                            "member": label,
                            "end": end,
                            "dof": str(dof),
                            "state": str(connection.state(dof)),
                        },
                        location,
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
            for node in (node_i, node_j):
                self.rotation_scale[node] = self.rotation_scale.get(node, 0.0) + rotation
            self.chain_length = max(self.chain_length, length)
            result.append(
                SpatialBeam(
                    tag,
                    str(member_id),
                    node_i,
                    node_j,
                    area,
                    e,
                    g,
                    torsion,
                    inertia_y,
                    inertia_z,
                    axes.ez.components(),
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

    def loads(self, case_id: UUID) -> tuple[list[SpatialNodalLoad], list[SpatialBeamLoad]]:
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
            else:  # Nunca se descarta en silencio.
                raise _unsupported(
                    "fem3d.load_type_unsupported",
                    {"type": type(load).__name__},
                    Location(entity_id=load.id, revision=load.revision),
                )
        return nodal, beam

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
    nodal, beam_loads = builder.loads(case.id)
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
    )
    hypotheses = [HYPOTHESIS_SPATIAL, HYPOTHESIS_BEAM, HYPOTHESIS_LAGRANGE]
    if builder.chains:
        hypotheses.append(HYPOTHESIS_CHAIN)
    if any(s.penalty for s in builder.springs):
        hypotheses.append(HYPOTHESIS_PENALTY)
    return SpatialTranslation(
        request,
        (),
        tuple(hypotheses),
        builder.max_diagonal,
        builder.chain_length,
    )


def _fixity(bits: list[int]) -> Fixity:
    a, b, c, d, e, f = bits
    return a, b, c, d, e, f
