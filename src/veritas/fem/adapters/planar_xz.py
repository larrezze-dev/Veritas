"""Traductor determinista del modelo de dominio a una petición plana XZ (plan T-007).

Función pura: no importa OpenSeesPy ni hace E/S y no modifica el modelo. Rechaza con
``UNSUPPORTED_CONFIGURATION`` todo lo que no cabe en el plano (ADR-002 §1-§2; notas de
T-005 R5/M6 y T-006); nunca lo ignora en silencio.

Mapeo (gate G1-G4 del plan): x₁ = X, x₂ = Z, θ = −Ry; carga nodal (Fx, Fz, −My); carga
de barra ``-beamUniform`` (Wy, Wx) en la base 2D del elemento, con ŷ = x̂ girado +90°.
Tags (D6): por orden del texto UUID canónico, independiente del orden de inserción.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Final
from uuid import UUID

from veritas.core.connections.end import DofState
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
from veritas.core.loads.loads import LoadBasis, MemberUniformLoad, NodalLoad, SelfWeight
from veritas.core.materials.material import MaterialFamily
from veritas.core.sections.properties import PropertyStatus, SectionProperty
from veritas.core.supports.support import BasisKind, RestraintKind, Support, SupportDof
from veritas.fem.adapters.protocol import (
    Pair,
    PlanarBeam,
    PlanarBeamLoad,
    PlanarFix,
    PlanarNodalLoad,
    PlanarNode,
    PlanarRequest,
    PlanarSpring,
)
from veritas.fem.adapters.singularity import translational_diagonal
from veritas.fem.config import AnalysisConfiguration

SUPPORTED_FAMILIES: Final = frozenset({MaterialFamily.STEEL, MaterialFamily.ALUMINIUM})
OUT_OF_PLANE_DOFS: Final = (SupportDof.UY, SupportDof.RX, SupportDof.RZ)

HYPOTHESIS_PLANAR: Final = "fem.hypothesis_planar"
HYPOTHESIS_BEAM: Final = "fem.hypothesis_euler_bernoulli"
HYPOTHESIS_OUT_OF_PLANE: Final = "fem.hypothesis_implicit_out_of_plane"
HYPOTHESIS_PENALTY: Final = "fem.hypothesis_penalty"


@dataclass(frozen=True, slots=True)
class Translation:
    """Petición, avisos (WARNING) e hipótesis declaradas de la traducción."""

    request: PlanarRequest
    warnings: tuple[Diagnostic, ...]
    hypotheses: tuple[str, ...]


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
            for end, connection in (("I", member.end_i), ("J", member.end_j)):
                if any(state is not DofState.RIGID for state in connection.states):
                    raise _unsupported(
                        "fem.member_end_not_rigid", {"member": label, "end": end}, location
                    )
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

    def loads(self, case_id: UUID) -> tuple[list[PlanarNodalLoad], list[PlanarBeamLoad]]:
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
                for member_id, _ in _ordered(self.model.members):
                    q_z = self.model.self_weight_load(member_id)[2].si_value
                    beam.append(self._beam_load(member_id, Vec3(0.0, 0.0, q_z)))
        return nodal, beam

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
    nodal, beam_loads = builder.loads(case.id)
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
    )
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
    return Translation(request, _out_of_plane_warnings(model), tuple(hypotheses))
