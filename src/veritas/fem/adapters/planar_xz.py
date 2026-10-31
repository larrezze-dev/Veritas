"""Traductor determinista del modelo de dominio a una petición plana XZ (plan T-007).

Función pura: no importa OpenSeesPy ni hace E/S y no modifica el modelo. Rechaza con
``UNSUPPORTED_CONFIGURATION`` todo lo que no cabe en el plano (ADR-002 §1-§2; notas de
T-005 R5/M6 y T-006); nunca lo ignora en silencio.

Mapeo (gate G1-G4 del plan): x₁ = X, x₂ = Z, θ = −Ry; carga nodal (Fx, Fz, −My); asiento
(Ux, Uz, −Ry) en apoyos de base global (T-016); carga
de barra ``-beamUniform`` (Wy, Wx) en la base 2D del elemento, con ŷ = x̂ girado +90°; cargas
de barra de Veritas (T-015) como carga nodal equivalente −Tᵀ·p0 en ``member_end_loads``.
Tags (D6): por orden del texto UUID canónico, independiente del orden de inserción.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final
from uuid import UUID

from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    InputValueError,
    Location,
    ParamValue,
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
from veritas.core.materials.material import MaterialFamily
from veritas.core.sections.properties import PropertyStatus, SectionProperty
from veritas.core.supports.support import BasisKind, RestraintKind, Support, SupportDof
from veritas.fem.adapters.protocol import (
    Pair,
    PlanarBeam,
    PlanarBeamLoad,
    PlanarEndLink,
    PlanarFix,
    PlanarMemberEndLoad,
    PlanarNodalLoad,
    PlanarNode,
    PlanarRequest,
    PlanarSettlement,
    PlanarSpring,
)
from veritas.fem.adapters.singularity import beam_rotation, translational_diagonal
from veritas.fem.config import AnalysisConfiguration
from veritas.fem.member_loads import (
    distributed_fixed_end_forces,
    distributed_moment_fixed_end_forces,
    planar_view,
    point_fixed_end_forces,
)

type _MemberLoad = MemberPointLoad | MemberDistributedLoad | MemberDistributedMoment

SUPPORTED_FAMILIES: Final = frozenset({MaterialFamily.STEEL, MaterialFamily.ALUMINIUM})
OUT_OF_PLANE_DOFS: Final = (SupportDof.UY, SupportDof.RX, SupportDof.RZ)

HYPOTHESIS_PLANAR: Final = "fem.hypothesis_planar"
HYPOTHESIS_BEAM: Final = "fem.hypothesis_euler_bernoulli"
HYPOTHESIS_OUT_OF_PLANE: Final = "fem.hypothesis_implicit_out_of_plane"
HYPOTHESIS_PENALTY: Final = "fem.hypothesis_penalty"
HYPOTHESIS_END_LINKS: Final = "fem.hypothesis_member_end_links"
HYPOTHESIS_ORPHAN: Final = "fem.hypothesis_orphan_rotation"

# Giro de flexión en el plano según la rama de inercia (T-010, D2): Iy → Ry, Iz → Rz.
IN_PLANE_ROTATION: Final = {"Iy": Dof.RY, "Iz": Dof.RZ}
# D8: un resorte de extremo con k·L/(E·I) mayor que esto degrada el condicionamiento.
NEAR_RIGID_RATIO: Final = 1e8


@dataclass(frozen=True, slots=True)
class Translation:
    """Petición, avisos (WARNING) e hipótesis declaradas de la traducción."""

    request: PlanarRequest
    warnings: tuple[Diagnostic, ...]
    hypotheses: tuple[str, ...]
    # Nudos con giro huérfano y momento aplicado: mecanismo (T-010, D3); no se restringen.
    orphan_loaded: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class _PendingLink:
    member_id: UUID
    beam: int
    end: MemberEnd
    master: int
    stiffness: float | None


def _unsupported(
    key: str, params: Mapping[str, ParamValue], location: Location | None = None
) -> InputValueError:
    return InputValueError(
        Diagnostic.create(DiagnosticCode.UNSUPPORTED_CONFIGURATION, key, params, location=location)
    )


def _ordered[T](items: Mapping[UUID, T]) -> list[tuple[UUID, T]]:
    return sorted(items.items(), key=lambda item: str(item[0]))


def _available(prop: SectionProperty, name: str, location: Location) -> float:
    if prop.status is PropertyStatus.NO_DISPONIBLE or prop.value is None:
        raise _unsupported("fem.section_property_unavailable", {"property": name}, location)
    return prop.value


class _Builder:
    def __init__(self, model: StructuralModel, config: AnalysisConfiguration) -> None:
        self.model = model
        self.config = config
        self.node_tags: dict[UUID, int] = {}
        self.beam_tags: dict[UUID, int] = {}
        self.coordinates: dict[int, Pair] = {}
        self.springs: list[PlanarSpring] = []
        self.fixes: list[PlanarFix] = []
        self.uses_penalty = False
        self.pending_links: list[_PendingLink] = []
        self.link_warnings: list[Diagnostic] = []
        # p0 plano (N, Vy, Mz de I y J) de las cargas de barra de Veritas por barra (T-015).
        self.fixed_end: dict[int, list[float]] = {}

    def nodes(self) -> list[PlanarNode]:
        if not self.model.members:
            raise _unsupported("fem.no_members", {})
        connected = {m.node_i for m in self.model.members.values()}
        connected |= {m.node_j for m in self.model.members.values()}
        result: list[PlanarNode] = []
        for tag, (node_id, node) in enumerate(_ordered(self.model.nodes), start=1):
            location = Location(entity_id=node_id, revision=node.revision)
            if abs(node.position.y) > self.config.tol_plane:
                raise _unsupported(
                    "fem.node_out_of_plane",
                    {
                        "node": node.label or str(node_id),
                        "y": node.position.y,
                        "tolerance": self.config.tol_plane,
                    },
                    location,
                )
            if node_id not in connected:
                raise _unsupported(
                    "fem.isolated_node", {"node": node.label or str(node_id)}, location
                )
            self.node_tags[node_id] = tag
            self.coordinates[tag] = (node.position.x, node.position.z)
            result.append(PlanarNode(tag, str(node_id), node.position.x, node.position.z))
        return result

    def beams(self) -> list[PlanarBeam]:
        result: list[PlanarBeam] = []
        tol = self.config.tol_axis
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
            axes = self.model.axes(member_id)
            if abs(axes.ex.y) <= tol and abs(axes.ez.y) <= tol:
                inertia_axis, inertia_prop = "Iy", props.iy
            elif abs(axes.ex.y) <= tol and abs(axes.ey.y) <= tol:
                inertia_axis, inertia_prop = "Iz", props.iz
            else:
                raise _unsupported(
                    "fem.member_axis_not_plane",
                    {
                        "member": label,
                        "ex_y": axes.ex.y,
                        "ey_y": axes.ey.y,
                        "ez_y": axes.ez.y,
                        "tolerance": tol,
                    },
                    location,
                )
            area = _available(props.area, "A", location)
            inertia = _available(inertia_prop, inertia_axis, location)
            iyz = _available(props.iyz, "Iyz", location)
            if iyz != 0.0:
                raise _unsupported(
                    "fem.section_coupled_bending", {"member": label, "iyz": iyz}, location
                )
            self.beam_tags[member_id] = tag
            for end, connection in ((MemberEnd.I, member.end_i), (MemberEnd.J, member.end_j)):
                self._end(
                    member_id, label, tag, end, connection, branch=inertia_axis, location=location
                )
            result.append(
                PlanarBeam(
                    tag,
                    str(member_id),
                    self.node_tags[member.node_i],
                    self.node_tags[member.node_j],
                    area,
                    material.elastic_modulus,
                    inertia,
                    inertia_axis,
                )
            )
        return result

    def _end(  # noqa: PLR0913 (identificación del extremo, por nombre)
        self,
        member_id: UUID,
        label: str,
        beam: int,
        end: MemberEnd,
        connection: EndConnection,
        *,
        branch: str,
        location: Location,
    ) -> None:
        """Extremo I/J: solo el giro en el plano puede no ser rígido (D2-A)."""
        if connection.is_rigid:
            return
        rotation = IN_PLANE_ROTATION[branch]
        params: dict[str, ParamValue] = {"member": label, "end": str(end)}
        for dof in Dof:
            state = connection.state(dof)
            if state is DofState.RIGID or dof is rotation:
                continue
            key = (
                "fem.member_end_translation_unsupported"
                if dof.is_translation
                else "fem.member_end_out_of_plane_unsupported"
            )
            raise _unsupported(key, {**params, "dof": str(dof), "state": str(state)}, location)
        member = self.model.member(member_id)
        node = member.node_i if end is MemberEnd.I else member.node_j
        stiffness = connection.stiffness[rotation.position]
        self.pending_links.append(
            _PendingLink(member_id, beam, end, self.node_tags[node], stiffness)
        )

    def finish_links(self, beams: list[PlanarBeam]) -> list[PlanarEndLink]:
        """Etiquetas de enlaces tras los resortes de apoyo: elementos B+S+k, aux N+S+k."""
        by_tag = {beam.tag: beam for beam in beams}
        first_element = len(self.beam_tags) + len(self.springs) + 1
        first_aux = len(self.node_tags) + len(self.springs) + 1
        links = []
        ordered = sorted(self.pending_links, key=lambda p: (p.beam, str(p.end)))
        for k, pending in enumerate(ordered):
            links.append(
                PlanarEndLink(
                    first_element + k,
                    str(pending.member_id),
                    pending.beam,
                    str(pending.end),
                    pending.master,
                    first_aux + k,
                    pending.stiffness,
                )
            )
            if pending.stiffness is not None:
                self._near_rigid(pending, by_tag[pending.beam])
        return links

    def _near_rigid(self, pending: _PendingLink, beam: PlanarBeam) -> None:
        xi, xj = self.coordinates[beam.node_i], self.coordinates[beam.node_j]
        length = math.hypot(xj[0] - xi[0], xj[1] - xi[1])
        assert pending.stiffness is not None
        ratio = pending.stiffness * length / (beam.elastic_modulus * beam.inertia)
        if ratio <= NEAR_RIGID_RATIO:
            return
        member = self.model.member(pending.member_id)
        self.link_warnings.append(
            Diagnostic.create(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "fem.end_spring_near_rigid",
                {
                    "member": member.label or str(member.id),
                    "end": str(pending.end),
                    "ratio": ratio,
                    "limit": NEAR_RIGID_RATIO,
                },
                severity=Severity.WARNING,
                location=Location(entity_id=member.id, revision=member.revision),
            )
        )

    def _add_spring(  # noqa: PLR0913 (campos del resorte, por nombre)
        self,
        support: Support,
        node: int,
        dof: int,
        stiffness: float,
        axes: tuple[Pair, Pair],
        *,
        penalty: bool,
    ) -> None:
        tag = len(self.beam_tags) + len(self.springs) + 1
        aux = len(self.node_tags) + len(self.springs) + 1
        self.springs.append(
            PlanarSpring(tag, str(support.id), node, aux, dof, stiffness, axes[0], axes[1], penalty)
        )
        self.uses_penalty |= penalty

    def _plane_axes(self, support: Support, location: Location) -> tuple[Pair, Pair]:
        if support.basis.kind is BasisKind.GLOBAL or support.basis.rows is None:
            return (1.0, 0.0), (0.0, 1.0)
        ex, _ey, ez = support.basis.rows
        tol = self.config.tol_axis
        if abs(ex.y) > tol or abs(ez.y) > tol:
            raise _unsupported(
                "fem.support_basis_not_plane",
                {"ex_y": ex.y, "ez_y": ez.y, "tolerance": tol},
                location,
            )
        # Normalizadas en el plano (O5): D3 usa k·vvᵀ y OpenSees normaliza -orient.
        nx, nz = math.hypot(ex.x, ex.z), math.hypot(ez.x, ez.z)
        return (ex.x / nx, ex.z / nx), (ez.x / nz, ez.z / nz)

    def supports(self) -> None:  # noqa: PLR0912 (una rama por estado de GDL)
        for _, support in sorted(
            self.model.supports.items(), key=lambda item: self.node_tags[item[1].node_id]
        ):
            location = Location(entity_id=support.id, revision=support.revision)
            node = self.node_tags[support.node_id]
            for dof in OUT_OF_PLANE_DOFS:
                if support.restraint(dof).kind is RestraintKind.SPRING:
                    raise _unsupported(
                        "fem.support_spring_out_of_plane", {"dof": dof.label}, location
                    )
            axes = self._plane_axes(support, location)
            fixity = [0, 0, 0]
            translations = (support.restraint(SupportDof.UX), support.restraint(SupportDof.UZ))
            if all(r.kind is RestraintKind.FIXED for r in translations):
                fixity[0] = fixity[1] = 1
            else:
                pairs = zip(translations, axes, strict=True)
                for direction, (restraint, axis) in enumerate(pairs, 1):
                    if restraint.kind is RestraintKind.FIXED:
                        aligned = _global_index(axis)
                        if aligned is None:
                            self._add_spring(support, node, direction, 0.0, axes, penalty=True)
                        else:
                            fixity[aligned] = 1
                    elif restraint.kind is RestraintKind.SPRING and restraint.stiffness:
                        self._add_spring(
                            support, node, direction, restraint.stiffness, axes, penalty=False
                        )
            rotation = support.restraint(SupportDof.RY)
            if rotation.kind is RestraintKind.FIXED:
                fixity[2] = 1
            elif rotation.kind is RestraintKind.SPRING and rotation.stiffness:
                self._add_spring(support, node, 3, rotation.stiffness, axes, penalty=False)
            if any(fixity):
                self.fixes.append(PlanarFix(node, (fixity[0], fixity[1], fixity[2])))

    def _beam_load(self, member_id: UUID, q: Vec3) -> PlanarBeamLoad:
        member = self.model.member(member_id)
        tag = self.beam_tags[member_id]
        xi = self.coordinates[self.node_tags[member.node_i]]
        xj = self.coordinates[self.node_tags[member.node_j]]
        length = math.hypot(xj[0] - xi[0], xj[1] - xi[1])
        ux, uz = (xj[0] - xi[0]) / length, (xj[1] - xi[1]) / length
        wx = q.x * ux + q.z * uz
        wy = q.x * -uz + q.z * ux
        return PlanarBeamLoad(tag, wy, wx)

    def loads(
        self, case_id: UUID
    ) -> tuple[list[PlanarNodalLoad], list[PlanarBeamLoad], list[PlanarSettlement]]:
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
        nodal: list[PlanarNodalLoad] = []
        beam: list[PlanarBeamLoad] = []
        settlements: list[PlanarSettlement] = []
        for load in loads:
            location = Location(entity_id=load.id, revision=load.revision)
            if isinstance(load, NodalLoad):
                if load.force.y != 0.0 or load.moment.x != 0.0 or load.moment.z != 0.0:
                    raise _unsupported(
                        "fem.load_out_of_plane",
                        {"fy": load.force.y, "mx": load.moment.x, "mz": load.moment.z},
                        location,
                    )
                nodal.append(
                    PlanarNodalLoad(
                        self.node_tags[load.node_id], load.force.x, load.force.z, -load.moment.y
                    )
                )
            elif isinstance(load, MemberUniformLoad):
                beam.append(self._beam_load(load.member_id, self._member_q(load, location)))
            elif isinstance(load, SelfWeight):
                # Fuente única (T-019, D4-A): solo las barras incluidas, con γ·A o ρ·A·g.
                for member_id, weight in _ordered(self.model.self_weight_loads()):
                    beam.append(self._beam_load(member_id, Vec3(0.0, 0.0, weight[2].si_value)))
            elif isinstance(load, SupportSettlement):
                settlements.extend(self._settlement(load, location))
            elif isinstance(
                load, MemberPointLoad | MemberDistributedLoad | MemberDistributedMoment
            ):
                self._fixed_end(load, location)
            else:  # Nunca se descarta en silencio.
                raise _unsupported(
                    "fem.load_type_unsupported", {"type": type(load).__name__}, location
                )
        # D4-bis-A: con asientos, ninguna penalización en toda la petición (apoyos inclinados).
        if settlements and self.uses_penalty:
            raise _unsupported(
                "fem.settlement_with_penalty_unsupported",
                {},
                Location(entity_id=case.id, revision=case.revision),
            )
        return nodal, beam, sorted(settlements, key=lambda t: (t.node, t.dof))

    def _settlement(self, load: SupportSettlement, location: Location) -> list[PlanarSettlement]:
        """Asiento (T-016): (Ux, Uz, −Ry) en u₁, u₂, θ; rechazos de lo específico a lo general."""
        if load.translation.y != 0.0 or load.rotation.x != 0.0 or load.rotation.z != 0.0:
            raise _unsupported(
                "fem.settlement_out_of_plane",
                {"uy": load.translation.y, "rx": load.rotation.x, "rz": load.rotation.z},
                location,
            )
        support = self.model.supports[load.support_id]
        if support.basis.kind is not BasisKind.GLOBAL:
            raise _unsupported("fem.settlement_inclined_unsupported", {}, location)
        node = self.node_tags[support.node_id]
        values = (load.translation.x, load.translation.z, -load.rotation.y)
        return [
            PlanarSettlement(node, dof, value)
            for dof, value in enumerate(values, start=1)
            if value != 0.0
        ]

    def _in_plane(self, load: _MemberLoad, vector: Vec3) -> tuple[Vec3, float]:
        """Vector global de la carga y su límite fuera del plano (D9 de T-015).

        Base GLOBAL: límite 0; LOCAL: ``tol_member_load_y``·‖v‖, como ``_member_q``.
        """
        if load.basis is LoadBasis.GLOBAL:
            return vector, 0.0
        v = self.model.axes(load.member_id).to_global(vector)
        return v, self.config.tol_member_load_y * v.norm()

    def _fixed_end(self, load: _MemberLoad, location: Location) -> None:
        """p0 plano de una carga de barra de Veritas (T-015, D6-A y D9-A), sumado por barra.

        Base 2D del elemento (la de ``_beam_load`` y ``planar_view``): fuerza (wx, wy) y par o
        momento «z» = −My, porque θ = −Ry.
        """
        member = self.model.member(load.member_id)
        tag = self.beam_tags[load.member_id]
        xi = self.coordinates[self.node_tags[member.node_i]]
        xj = self.coordinates[self.node_tags[member.node_j]]
        length = math.hypot(xj[0] - xi[0], xj[1] - xi[1])
        ux, uz = (xj[0] - xi[0]) / length, (xj[1] - xi[1]) / length

        def force(q: Vec3) -> tuple[float, float, float]:
            v, bound = self._in_plane(load, q)
            if abs(v.y) > bound:
                raise _unsupported(
                    "fem.member_load_out_of_plane", {"qy": v.y, "limit": bound}, location
                )
            return (v.x * ux + v.z * uz, v.x * -uz + v.z * ux, 0.0)

        def moment(m: Vec3, key: str) -> tuple[float, float, float]:
            v, bound = self._in_plane(load, m)
            if abs(v.x) > bound or abs(v.z) > bound:
                raise _unsupported(key, {"mx": v.x, "mz": v.z, "limit": bound}, location)
            return (0.0, 0.0, -v.y)

        if isinstance(load, MemberPointLoad):
            f, f_bound = self._in_plane(load, load.force)
            m, m_bound = self._in_plane(load, load.moment)
            if abs(f.y) > f_bound or abs(m.x) > m_bound or abs(m.z) > m_bound:
                raise _unsupported(
                    "fem.member_point_load_out_of_plane",
                    {
                        "fy": f.y,
                        "mx": m.x,
                        "mz": m.z,
                        "force_limit": f_bound,
                        "moment_limit": m_bound,
                    },
                    location,
                )
            p0 = point_fixed_end_forces(
                load.position,
                (f.x * ux + f.z * uz, f.x * -uz + f.z * ux, 0.0),
                (0.0, 0.0, -m.y),
                length,
            )
        elif isinstance(load, MemberDistributedLoad):
            p0 = distributed_fixed_end_forces(
                load.start,
                load.end,
                force(load.intensity_start),
                force(load.intensity_end),
                length,
            )
        else:
            key = "fem.member_moment_out_of_plane"
            p0 = distributed_moment_fixed_end_forces(
                load.start,
                load.end,
                moment(load.intensity_start, key),
                moment(load.intensity_end, key),
                length,
            )
        current = self.fixed_end.setdefault(tag, [0.0] * 6)
        for k, value in enumerate(planar_view(p0)):
            current[k] += value

    def _member_q(self, load: MemberUniformLoad, location: Location) -> Vec3:
        if load.basis is LoadBasis.GLOBAL:
            q = load.intensity
            limit = 0.0
        else:
            q = self.model.axes(load.member_id).to_global(load.intensity)
            limit = self.config.tol_member_load_y * q.norm()
        if abs(q.y) > limit:
            raise _unsupported(
                "fem.member_load_out_of_plane",
                {"qy": q.y, "limit": limit},
                location,
            )
        return Vec3(q.x, 0.0, q.z)


def _orphans(request: PlanarRequest) -> tuple[list[int], list[int]]:
    """Giros huérfanos (D3): ni extremo rígido ni resorte, ni giro fijo o resorte de apoyo.

    Criterio estructural sobre la petición. Devuelve (restringibles, con momento aplicado).
    """
    links = {(link.beam, link.end): link for link in request.end_links}
    stiffened: set[int] = {fix.node for fix in request.fixes if fix.fixity[2]}
    stiffened |= {s.node for s in request.springs if s.dof == 3}  # noqa: PLR2004 (giro)
    incident: set[int] = set()
    for beam in request.beams:
        for end, node in (("I", beam.node_i), ("J", beam.node_j)):
            incident.add(node)
            link = links.get((beam.tag, end))
            if link is None or link.stiffness is not None:
                stiffened.add(node)
    loaded = {load.node for load in request.nodal_loads if load.m != 0.0}
    # Regla de lectura de D7 (T-015). No cambia nada: un extremo liberado carga su aux y un
    # maestro con extremo rígido nunca es huérfano.
    loaded |= {load.node for load in request.member_end_loads if load.m != 0.0}
    orphans = sorted(incident - stiffened)
    return [t for t in orphans if t not in loaded], [t for t in orphans if t in loaded]


def _member_end_loads(
    request: PlanarRequest, fixed_end: Mapping[int, list[float]]
) -> tuple[PlanarMemberEndLoad, ...]:
    """−Tᵀ·p0 por barra (T-015, D6-A), en el nudo del elemento de cada extremo (D7, hecho 7).

    Sin condensación (D9-A): el aux de un extremo no rígido recibe la carga del extremo.
    """
    coords = {node.tag: (node.x1, node.x2) for node in request.nodes}
    beams = {beam.tag: beam for beam in request.beams}
    result: list[PlanarMemberEndLoad] = []
    for tag in sorted(fixed_end):
        beam, p0 = beams[tag], fixed_end[tag]
        _, rotation = beam_rotation(coords[beam.node_i], coords[beam.node_j])
        load = [-sum(rotation[m][i] * p0[m] for m in range(6)) for i in range(6)]
        for offset, end in enumerate(("I", "J")):
            node = request.element_node(beam, end)
            result.append(PlanarMemberEndLoad(tag, node, *load[3 * offset : 3 * offset + 3]))
    return tuple(result)


def _global_index(axis: Pair) -> int | None:
    if axis[1] == 0.0 and abs(axis[0]) == 1.0:
        return 0
    if axis[0] == 0.0 and abs(axis[1]) == 1.0:
        return 1
    return None


def _out_of_plane_warnings(model: StructuralModel) -> tuple[Diagnostic, ...]:
    """D10-A: aviso por GDL fuera del plano que ningún apoyo fija (mecanismo 3D).

    El GDL se lee en la base de cada apoyo: con base inclinada (ey′ = ±Y), Uy′ equivale a
    Uy, pero Rx′/Rz′ fijos se cuentan como Rx/Rz aunque cada uno cubra una sola dirección
    del plano. Es un aviso orientativo; la estabilidad 3D no se comprueba (O2).
    """
    warnings: list[Diagnostic] = []
    for dof in OUT_OF_PLANE_DOFS:
        if not any(s.restraint(dof).kind is RestraintKind.FIXED for s in model.supports.values()):
            warnings.append(
                Diagnostic.create(
                    DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                    "fem.out_of_plane_implicit_restraint",
                    {"dof": dof.label, "nodes": len(model.nodes)},
                    severity=Severity.WARNING,
                )
            )
    return tuple(warnings)


def translate_planar_xz(model: StructuralModel, config: AnalysisConfiguration) -> Translation:
    """Traduce el modelo y el caso ``config.load_case_id`` a una petición plana."""
    if not isinstance(model, StructuralModel):
        raise TypeError("model debe ser StructuralModel")
    if not isinstance(config, AnalysisConfiguration):
        raise TypeError("config debe ser AnalysisConfiguration")
    case = model.load_case(config.load_case_id)
    builder = _Builder(model, config)
    nodes = builder.nodes()
    beams = builder.beams()
    builder.supports()
    links = builder.finish_links(beams)
    nodal, beam_loads, settlements = builder.loads(case.id)
    request = PlanarRequest(
        model.model_revision,
        str(case.id),
        case.revision,
        tuple(nodes),
        tuple(beams),
        tuple(builder.fixes),
        tuple(builder.springs),
        tuple(nodal),
        tuple(beam_loads),
        tuple(links),
        settlements=tuple(settlements),
    )
    if builder.fixed_end:
        request = replace(request, member_end_loads=_member_end_loads(request, builder.fixed_end))
    node_ids = {tag: node_id for node_id, tag in builder.node_tags.items()}
    restrained, loaded = _orphans(request)
    request = replace(request, orphan_fixes=tuple(restrained))
    orphan_warnings = [
        Diagnostic.create(
            DiagnosticCode.UNSUPPORTED_CONFIGURATION,
            "fem.orphan_rotation_restrained",
            {"node": model.node(node_ids[tag]).label or str(node_ids[tag])},
            severity=Severity.WARNING,
            location=Location(entity_id=node_ids[tag], revision=model.node(node_ids[tag]).revision),
        )
        for tag in restrained
    ]
    if builder.uses_penalty:
        local = translational_diagonal(request)
        request = replace(
            request,
            springs=tuple(
                replace(s, stiffness=config.penalty_factor * local[s.node]) if s.penalty else s
                for s in request.springs
            ),
        )
    hypotheses = [HYPOTHESIS_PLANAR, HYPOTHESIS_BEAM, HYPOTHESIS_OUT_OF_PLANE]
    if builder.uses_penalty:
        hypotheses.append(HYPOTHESIS_PENALTY)
    if links:
        hypotheses.append(HYPOTHESIS_END_LINKS)
    if restrained:
        hypotheses.append(HYPOTHESIS_ORPHAN)
    warnings = (*_out_of_plane_warnings(model), *builder.link_warnings, *orphan_warnings)
    return Translation(request, warnings, tuple(hypotheses), tuple(node_ids[t] for t in loaded))
