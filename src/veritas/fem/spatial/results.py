"""Registro de corrida y salida bruta espacial (plan T-018, D2-A y D10; ADR-001 §4).

``raw`` existe solo con ``SUCCEEDED``; un fallo nunca lleva resultados ni ceros. La base
del solver es la base global de ADR-001 con seis GDL por nudo, así que los desplazamientos
brutos ya están en esa base; las fuerzas de extremo siguen en la base local de la barra
(hecho 2).

``normalize_spatial`` (pieza 5b) publica los resultados con los tipos públicos del plano
(``Vector6``, ``NodeDisplacement``, ``SupportReaction``, ``MemberEndForces``), con ejes y
signos de ADR-001 y sin componentes NOT_CALCULATED:

1. Desplazamientos: los brutos, ya en base global.
2. Fuerzas de extremo: pI = ``localForce[:6]`` y pJ = ``localForce[6:]`` en la base local
   (hecho 2), más el p0_c de las cargas de barra de Veritas (T-015, D10-A);
   section_start = −pI y section_end = pJ. Se elevan a global con la Q del
   dominio, así que el equilibrio de nudos controla también su coherencia.
3. Reacción de apoyo = reacción exacta del nudo (``fix`` y cadena) + la de su tierra
   (resortes y penalización), en base global y, si el apoyo es inclinado, Q_apoyo·R (exacta
   en 3D: sin la proyección del plano). Una fila de la base alineada con un eje global dentro
   de ``tol_axis`` se representa con ``fix`` en ese eje (D4), así que la componente libre
   cercana puede llevar hasta ``tol_axis``·|R|; se publica como NOT_APPLICABLE. Fuente por
   GDL restringido: SPRING o PENALTY si la petición tiene ese resorte; si no, FIX (también la
   cadena, que es una restricción exacta).

Postcondiciones: equilibrio de nudos (con tierras y cadenas, revisión 5a O1), de barras y
global, con las cargas del dominio y la cota de redondeo de ``spatial.postconditions``. Si
fallan, no hay resultados.

Componentes del modo de mecanismo (O3, O6): Ux…Rz si la coordenada reducida es un eje
global; ``t1``/``t2`` (traslaciones) y ``r1``/``r2`` (giros) si es oblicua, en la base
ortonormal libre que deja en el nudo su apoyo inclinado.
"""

import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    InputValueError,
    Location,
    ParamValue,
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
    linear_contribution,
    linear_moment_contribution,
    point_contribution,
)
from veritas.core.supports.support import BasisKind, RestraintKind, Support, SupportDof
from veritas.fem.results.normalized import (
    Component,
    ComponentStatus,
    EndJump,
    MemberEndForces,
    NodeDisplacement,
    ReactionSource,
    ResultBasis,
    ResultsNormalizationError,
    SupportReaction,
    Vector6,
)
from veritas.fem.results.run import PivotSummary, RunStatus
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.jumps import end_jumps
from veritas.fem.spatial.postconditions import member_fixed_end_forces, rounding_bounds
from veritas.fem.spatial.protocol import AXES, Six, SpatialRequest
from veritas.fem.spatial.translator import translate_spatial

RAW_SPATIAL_BASIS: Final = "GLOBAL"
HYPOTHESIS_SPATIAL_NORMALIZED: Final = "fem3d.hypothesis_normalized"
# Valor de ``MemberEndForces.inertia_axis`` en 3D: las seis componentes son CALCULATED
# (aprobado por el usuario el 2026-10-29; conjunto {"Iy", "Iz", "3D"}: los consumidores
# distinguen plano y espacial por el tipo de resultado, no por esta cadena).
SPATIAL_INERTIA_AXIS: Final = "3D"
# Constantes de procedencia que ``analyze_spatial`` añade al snapshot de la configuración.
PROVENANCE_KEYS: Final = frozenset(
    {
        "penalty_equilibrium_factor",
        "chain_alias_factor",
        "mechanism_participation",
        "mechanism_guard_low",
        "mechanism_guard_high",
        "mechanism_residual_factor",
        "redundancy_tolerance",
        "end_penalty_min_stagnation_iteration",
        "end_penalty_stagnation_ratio",
        "end_contrast_reference_margin",
        "end_axial_load_tolerance",
    }
)
SPATIAL_ADAPTER_ID: Final = "veritas.fem.spatial"
# D10: "1" en E1, "2" en E2, "3" con ADR-004, "4" T-016, "5" T-015 (cargas de barra).
SPATIAL_ADAPTER_VERSION: Final = "5"

type Twelve = tuple[
    float, float, float, float, float, float, float, float, float, float, float, float
]


@dataclass(frozen=True, slots=True)
class RawSpatialNodeResult:
    """Desplazamiento global y reacción de las restricciones exactas del nudo.

    ``reaction`` es ``nodeReaction`` del nudo, sumada con la de sus aux de extremo (el
    multiplicador del ``equalDOF`` aux−nudo se cancela, hecho 17), proyectada sobre las
    direcciones restringidas por ``fix`` o por una cadena (en las libres es el residuo del
    propio solver); ``None`` si el nudo no tiene ninguna. La de los resortes está en
    ``RawSpatialGroundReaction``.
    """

    node_id: UUID
    displacement: Six
    reaction: Six | None


@dataclass(frozen=True, slots=True)
class RawSpatialGroundReaction:
    """Suma de los resortes de apoyo de un nudo (``nodeReaction`` de su tierra): fuerza
    sobre la estructura en base global (hecho 10)."""

    node_id: UUID
    reaction: Six


@dataclass(frozen=True, slots=True)
class RawSpatialEndAux:
    """Desplazamiento global del nudo auxiliar de un extremo de barra (T-020, E2.2: el
    salto de extremo lo necesita). Es el movimiento del extremo de la barra."""

    member_id: UUID
    end: str
    displacement: Six


@dataclass(frozen=True, slots=True)
class RawSpatialMemberForces:
    """``eleResponse(tag, 'localForce')`` = [pI, pJ] en la base local de ADR-001 (hecho 2)."""

    member_id: UUID
    local_force: Twelve


@dataclass(frozen=True, slots=True)
class RawSpatialResults:
    """Salida bruta del solver espacial. Sin los nudos auxiliares de tierras, C y R; los aux
    de extremo solo con su desplazamiento (``end_aux``, T-020)."""

    basis: str
    nodes: tuple[RawSpatialNodeResult, ...]
    grounds: tuple[RawSpatialGroundReaction, ...]
    members: tuple[RawSpatialMemberForces, ...]
    end_aux: tuple[RawSpatialEndAux, ...] = ()


@dataclass(frozen=True, slots=True)
class SpatialAnalysisRun:
    """Registro de una corrida espacial, solo en memoria (mismos campos que ``AnalysisRun``).

    ``model_revision`` es el contador del dominio, leído y no generado (ADR-003, extensión
    de la excepción de procedencia de ADR-002 §6).
    """

    run_id: UUID
    status: RunStatus
    diagnostics: tuple[Diagnostic, ...]
    model_revision: int
    case_id: UUID
    case_revision: int | None
    configuration: dict[str, str | float | int]
    request_json: str | None
    versions: dict[str, str | None]
    hypotheses: tuple[str, ...]
    started_utc: datetime
    duration_s: float
    pivot: PivotSummary | None
    worker_stdout: str
    worker_stderr: str
    raw: RawSpatialResults | None

    def __post_init__(self) -> None:
        if (self.status is RunStatus.SUCCEEDED) != (self.raw is not None):
            raise ValueError("Solo una corrida SUCCEEDED lleva resultados")


@dataclass(frozen=True, slots=True)
class SpatialResults:
    """Resultados normalizados de una corrida espacial. Sin validación de referencia."""

    run_id: UUID
    model_revision: int
    case_id: UUID
    case_revision: int | None
    nodes: tuple[NodeDisplacement, ...]
    reactions: tuple[SupportReaction, ...]
    members: tuple[MemberEndForces, ...]
    hypotheses: tuple[str, ...]
    end_jumps: tuple[EndJump, ...] = ()


# --- Errores ---------------------------------------------------------------------------


def _unavailable(run: SpatialAnalysisRun) -> InputValueError:
    return InputValueError(
        Diagnostic.create(
            DiagnosticCode.ANALYSIS_FAILED, "fem.results_unavailable", {"status": str(run.status)}
        )
    )


def _mismatch(reason: str) -> InputValueError:
    return InputValueError(
        Diagnostic.create(
            DiagnosticCode.REVISION_CONFLICT, "fem.results_model_mismatch", {"reason": reason}
        )
    )


def _postcondition(
    key: str, params: dict[str, ParamValue], location: Location | None = None
) -> ResultsNormalizationError:
    return ResultsNormalizationError(
        Diagnostic.create(DiagnosticCode.ANALYSIS_FAILED, key, params, location=location)
    )


# --- Correspondencia modelo ↔ corrida -----------------------------------------------------


def _configuration(run: SpatialAnalysisRun) -> SpatialAnalysisConfiguration:
    snapshot = {k: v for k, v in run.configuration.items() if k not in PROVENANCE_KEYS}
    case = snapshot.pop("load_case_id")
    return SpatialAnalysisConfiguration(UUID(str(case)), **snapshot)  # type: ignore[arg-type]


def _checked_request(run: SpatialAnalysisRun, model: StructuralModel) -> SpatialRequest:
    if model.model_revision != run.model_revision:
        raise _mismatch("model_revision")
    if run.request_json is None:
        raise _mismatch("request")
    try:
        config = _configuration(run)
    except (TypeError, KeyError, ValueError, InputValueError) as error:
        raise _mismatch("configuration") from error
    try:
        request = translate_spatial(model, config).request
    except InputValueError as error:
        raise _mismatch(str(error.code)) from error
    if request.to_json() != run.request_json:
        raise _mismatch("request")
    return request


def _check_finite(raw: RawSpatialResults) -> None:
    """Un valor bruto no finito (solo alcanzable fuera del protocolo) no da resultados."""
    values = [v for n in raw.nodes for v in (*n.displacement, *(n.reaction or ()))]
    values += [v for g in raw.grounds for v in g.reaction]
    values += [v for m in raw.members for v in m.local_force]
    if not all(math.isfinite(v) for v in values):
        raise _postcondition("fem.nonfinite_result", {})


# --- Componentes ---------------------------------------------------------------------------

_NA: Final = Component(None, ComponentStatus.NOT_APPLICABLE)


def _calc(value: float) -> Component:
    return Component(value, ComponentStatus.CALCULATED)


def _vector(basis: ResultBasis, values: Iterable[Component]) -> Vector6:
    c = tuple(values)
    return Vector6(basis, (c[0], c[1], c[2], c[3], c[4], c[5]))


def _calculated(basis: ResultBasis, values: Iterable[float]) -> Vector6:
    return _vector(basis, (_calc(v) for v in values))


def _rows(q: tuple[Vec3, Vec3, Vec3], v: Vec3) -> tuple[float, float, float]:
    return (q[0].dot(v), q[1].dot(v), q[2].dot(v))


def _zero() -> Vec3:
    return Vec3(0.0, 0.0, 0.0)


@dataclass(frozen=True, slots=True)
class _End:
    """Extremo de barra en base global: fuerza y momento (acción del nudo sobre la barra)."""

    force: Vec3
    moment: Vec3


# --- Normalización ---------------------------------------------------------------------------


class _Normalizer:
    def __init__(
        self, run: SpatialAnalysisRun, model: StructuralModel, request: SpatialRequest
    ) -> None:
        assert run.raw is not None
        self.raw = run.raw
        self.model = model
        self.request = request
        self.config = _configuration(run)
        self.tag_of = {UUID(n.entity_id): n.tag for n in request.nodes}
        # Coordenadas del dominio, ya comprobadas al comparar la petición.
        self.position = {UUID(n.entity_id): Vec3(n.x, n.y, n.z) for n in request.nodes}
        self.ends: dict[UUID, tuple[_End, _End]] = {}
        self.published: dict[UUID, tuple[float, ...]] = {}  # pI y pJ locales publicados
        self.reaction_force: dict[UUID, tuple[Vec3, Vec3]] = {}  # por nudo
        # p0_c local de las cargas de barra de Veritas por barra (T-015, D10-A).
        fixed_end = member_fixed_end_forces(request)
        self.fixed_end = {
            UUID(beam.entity_id): fixed_end[beam.tag]
            for beam in request.beams
            if beam.tag in fixed_end
        }

    def nodes(self) -> tuple[NodeDisplacement, ...]:
        return tuple(
            NodeDisplacement(raw.node_id, _calculated(ResultBasis.GLOBAL, raw.displacement))
            for raw in self.raw.nodes
        )

    # Reacciones: la exacta del nudo más la de su tierra (revisión 5a, O1).
    def reactions(self) -> tuple[SupportReaction, ...]:
        exact = {n.node_id: n.reaction for n in self.raw.nodes}
        grounds = {g.node_id: g.reaction for g in self.raw.grounds}
        result = []
        for support in sorted(self.model.supports.values(), key=lambda s: self.tag_of[s.node_id]):
            total = [0.0] * 6
            for part in (exact[support.node_id], grounds.get(support.node_id)):
                if part is not None:
                    total = [a + b for a, b in zip(total, part, strict=True)]
            force, moment = Vec3(*total[:AXES]), Vec3(*total[AXES:])
            self.reaction_force[support.node_id] = (force, moment)
            result.append(self._reaction(support, force, moment))
        return tuple(result)

    def _reaction(self, support: Support, force: Vec3, moment: Vec3) -> SupportReaction:
        restrained = [support.restraint(dof).kind is not RestraintKind.FREE for dof in SupportDof]
        penalty = {
            spring.dof - 1: spring.penalty
            for spring in self.request.springs
            if spring.support_id == str(support.id)
        }
        sources: list[tuple[SupportDof, ReactionSource]] = []
        for dof in SupportDof:
            if not restrained[dof.value]:
                continue
            if dof.value not in penalty:
                sources.append((dof, ReactionSource.FIX))
            else:
                source = ReactionSource.PENALTY if penalty[dof.value] else ReactionSource.SPRING
                sources.append((dof, source))
        values = (*force.components(), *moment.components())
        inclined = support.basis.kind is BasisKind.INCLINED and support.basis.rows is not None
        shown = restrained
        if inclined:
            # Q_apoyoᵀ mezcla las tres componentes de cada terna: todas cuentan si alguna
            # de la terna está restringida.
            triples = (any(restrained[:AXES]), any(restrained[AXES:]))
            shown = [triples[k // AXES] for k in range(6)]
        global_ = _vector(
            ResultBasis.GLOBAL,
            (_calc(v) if s else _NA for v, s in zip(values, shown, strict=True)),
        )
        local: Vector6 | None = None
        axes: tuple[Vec3, Vec3, Vec3] | None = None
        if inclined and support.basis.rows is not None:
            axes = support.basis.rows
            local_values = (*_rows(axes, force), *_rows(axes, moment))
            local = _vector(
                ResultBasis.SUPPORT,
                (_calc(v) if r else _NA for v, r in zip(local_values, restrained, strict=True)),
            )
        return SupportReaction(support.id, support.node_id, global_, local, axes, tuple(sources))

    # Fuerzas de extremo (hecho 2), elevadas a global con la Q del dominio.
    def members(self) -> tuple[MemberEndForces, ...]:
        result = []
        for raw in self.raw.members:
            q = self.model.axes(raw.member_id)
            local_force: tuple[float, ...] = raw.local_force
            p0 = self.fixed_end.get(raw.member_id)
            if p0 is not None:  # D10-A: publicado = localForce + p0_c; sin p0, intacto (−0,0).
                local_force = tuple(f + v for f, v in zip(local_force, p0, strict=True))
            p_i, p_j = local_force[:6], local_force[6:]
            end_i, end_j = (
                _End(q.to_global(Vec3(*p[:AXES])), q.to_global(Vec3(*p[AXES:]))) for p in (p_i, p_j)
            )
            self.ends[raw.member_id] = (end_i, end_j)
            self.published[raw.member_id] = local_force
            result.append(
                MemberEndForces(
                    raw.member_id,
                    SPATIAL_INERTIA_AXIS,
                    (q.ex, q.ey, q.ez),
                    _calculated(ResultBasis.MEMBER_LOCAL, p_i),
                    _calculated(ResultBasis.MEMBER_LOCAL, p_j),
                    _calculated(ResultBasis.MEMBER_LOCAL, (-v for v in p_i)),
                    _calculated(ResultBasis.MEMBER_LOCAL, p_j),
                )
            )
        return tuple(result)

    def end_jumps(self) -> tuple[EndJump, ...]:
        """Salto de los extremos no rígidos (T-020, E2.2), con las fuerzas publicadas."""
        return end_jumps(self.model, self.request, self.raw, self.published)

    # Postcondición de equilibrio con vectores 3D completos.
    def check_equilibrium(self) -> None:
        tol = self.config.tol_equilibrium
        nodal, distributed, resultants = self._domain_loads()
        bounds = self._bounds()
        members = self.model.members
        for node_id in self.tag_of:
            f_sum, m_sum = _zero(), _zero()
            f_scale = m_scale = 0.0
            actions = list(nodal.get(node_id, []))
            if node_id in self.reaction_force:
                actions.append(self.reaction_force[node_id])
            for force, moment in actions:
                f_sum, m_sum = f_sum + force, m_sum + moment
                f_scale, m_scale = f_scale + force.norm(), m_scale + moment.norm()
            for member_id, member in members.items():
                for end_node, end in zip(
                    (member.node_i, member.node_j), self.ends[member_id], strict=True
                ):
                    if end_node == node_id:
                        f_sum, m_sum = f_sum - end.force, m_sum - end.moment
                        f_scale += end.force.norm()
                        m_scale += end.moment.norm()
            f_extra, m_extra = bounds.get(node_id, (0.0, 0.0))
            self._require(
                "node",
                node_id,
                (f_sum.norm(), tol * f_scale + f_extra),
                (m_sum.norm(), tol * m_scale + m_extra),
            )
        for member_id, member in members.items():
            self._member_equilibrium(
                member_id, (member.node_i, member.node_j), (distributed, resultants), bounds
            )
        self._global_equilibrium(nodal, (distributed, resultants), bounds)

    def _member_equilibrium(
        self,
        member_id: UUID,
        nodes: tuple[UUID, UUID],
        member_loads: tuple[dict[UUID, Vec3], dict[UUID, tuple[Vec3, Vec3]]],
        bounds: dict[UUID, tuple[float, float]],
    ) -> None:
        tol = self.config.tol_equilibrium
        node_i, node_j = nodes
        distributed, resultants = member_loads
        end_i, end_j = self.ends[member_id]
        arm = self.position[node_j] - self.position[node_i]
        length = arm.norm()
        load = distributed.get(member_id, _zero()).scaled(length)
        force = end_i.force + end_j.force + load
        moment = end_i.moment + end_j.moment + arm.cross(end_j.force) + arm.scaled(0.5).cross(load)
        f_scale = end_i.force.norm() + end_j.force.norm() + load.norm()
        m_scale = end_i.moment.norm() + end_j.moment.norm() + length * f_scale
        if member_id in resultants:  # T-015, D10-A: resultante respecto a I.
            r_force, r_moment = resultants[member_id]
            force, moment = force + r_force, moment + r_moment
            f_scale += r_force.norm()
            m_scale += r_moment.norm() + length * r_force.norm()
        fi, mi = bounds.get(node_i, (0.0, 0.0))
        fj, mj = bounds.get(node_j, (0.0, 0.0))
        self._require(
            "member",
            member_id,
            (force.norm(), tol * f_scale + fi + fj),
            (moment.norm(), tol * m_scale + mi + mj + (fi + fj) * length),
        )

    def _global_equilibrium(
        self,
        nodal: dict[UUID, list[tuple[Vec3, Vec3]]],
        member_loads: tuple[dict[UUID, Vec3], dict[UUID, tuple[Vec3, Vec3]]],
        bounds: dict[UUID, tuple[float, float]],
    ) -> None:
        """ΣP + ΣR + Σq·L + Σ resultantes de las cargas de barra de Veritas = 0, momentos
        respecto al centroide de los nudos (la resultante, aplicada en I)."""
        distributed, resultants = member_loads
        points = self.position
        centre = _zero()
        for point in points.values():
            centre = centre + point.scaled(1.0 / len(points))
        actions: list[tuple[Vec3, Vec3, Vec3]] = []  # punto, fuerza, momento
        for node_id, loads in nodal.items():
            actions += [(points[node_id], f, m) for f, m in loads]
        for node_id, (force, moment) in self.reaction_force.items():
            actions.append((points[node_id], force, moment))
        for member_id, member in self.model.members.items():
            q = distributed.get(member_id)
            if q is not None:
                i, j = points[member.node_i], points[member.node_j]
                actions.append(((i + j).scaled(0.5), q.scaled((j - i).norm()), _zero()))
        for member_id, (r_force, r_moment) in resultants.items():
            actions.append((points[self.model.member(member_id).node_i], r_force, r_moment))
        force = moment = _zero()
        f_scale = m_scale = 0.0
        for point, f, m in actions:
            force, moment = force + f, moment + (point - centre).cross(f) + m
            f_scale += f.norm()
            m_scale += m.norm() + (point - centre).norm() * f.norm()
        f_extra = sum(f for f, _m in bounds.values())
        m_extra = sum(m for _f, m in bounds.values())
        l_ref = max((p - centre).norm() for p in points.values())
        tol = self.config.tol_equilibrium
        self._require(
            "global",
            UUID(self.request.case_id),
            (force.norm(), tol * f_scale + f_extra),
            (moment.norm(), tol * m_scale + m_extra + f_extra * l_ref),
        )

    def _require(
        self, where: str, entity: UUID, force: tuple[float, float], moment: tuple[float, float]
    ) -> None:
        """Residuo ≤ cota, para (residuo, cota) de fuerza y de momento; un NaN falla."""
        if not (force[0] <= force[1]) or not (moment[0] <= moment[1]):
            raise _postcondition(
                "fem.results_equilibrium",
                {"where": where, "force": force[0], "moment": moment[0]},
                Location(entity_id=entity),
            )

    def _bounds(self) -> dict[UUID, tuple[float, float]]:
        """Cota c·eps·Σ|k_ij·u_j| por nudo de dominio (la de los controles de 5a)."""
        displacement = {self.tag_of[n.node_id]: n.displacement for n in self.raw.nodes}
        node_of = {tag: node_id for node_id, tag in self.tag_of.items()}
        bounds = rounding_bounds(self.request, displacement)
        return {node_of[tag]: (f, m) for tag, (f, m) in bounds.items()}

    def _domain_loads(
        self,
    ) -> tuple[
        dict[UUID, list[tuple[Vec3, Vec3]]], dict[UUID, Vec3], dict[UUID, tuple[Vec3, Vec3]]
    ]:
        """Cargas del caso tomadas del dominio en base global, no de la petición.

        Devuelve las nodales, la q uniforme por barra y, para las cargas de barra de Veritas
        (T-015, D10-A), la resultante por barra (F, M respecto al extremo I) en forma cerrada
        del núcleo y no con ``member_loads``: es el control independiente de p0.
        """
        nodal: dict[UUID, list[tuple[Vec3, Vec3]]] = {}
        distributed: dict[UUID, Vec3] = {}
        resultants: dict[UUID, tuple[Vec3, Vec3]] = {}
        for load in self.model.loads_in_case(UUID(self.request.case_id)):
            if isinstance(load, NodalLoad):
                nodal.setdefault(load.node_id, []).append((load.force, load.moment))
            elif isinstance(load, MemberUniformLoad):
                q = load.intensity
                if load.basis is LoadBasis.LOCAL:
                    q = self.model.axes(load.member_id).to_global(q)
                distributed[load.member_id] = distributed.get(load.member_id, _zero()) + q
            elif isinstance(load, SelfWeight):
                included = self.model.self_weight_loads()  # T-019, D4-A: solo las incluidas.
                for member_id in self.model.members:
                    if member_id not in included:
                        continue
                    weight = Vec3(*(c.si_value for c in included[member_id]))
                    distributed[member_id] = distributed.get(member_id, _zero()) + weight
            elif isinstance(load, SupportSettlement):
                continue  # T-016, D7: acción sin fuerza prescrita; no aporta q ni P.
            elif isinstance(
                load, MemberPointLoad | MemberDistributedLoad | MemberDistributedMoment
            ):
                force, moment = self._member_resultant(load)
                previous = resultants.get(load.member_id)
                if previous is not None:
                    force, moment = previous[0] + force, previous[1] + moment
                resultants[load.member_id] = (force, moment)
            else:  # pragma: no cover (el traductor ya la habría rechazado)
                raise _postcondition("fem.results_unsupported_load", {"load": str(load.id)})
        return nodal, distributed, resultants

    def _member_resultant(
        self, load: MemberPointLoad | MemberDistributedLoad | MemberDistributedMoment
    ) -> tuple[Vec3, Vec3]:
        """(F, M respecto a I) en base global, con los tres ejes (sin proyección)."""
        member = self.model.member(load.member_id)
        arm = self.position[member.node_j] - self.position[member.node_i]

        def to_global(vector: Vec3) -> Vec3:
            if load.basis is LoadBasis.LOCAL:
                return self.model.axes(load.member_id).to_global(vector)
            return vector

        if isinstance(load, MemberPointLoad):
            return point_contribution(
                arm.scaled(load.position), to_global(load.force), to_global(load.moment)
            )
        r_a, r_b = arm.scaled(load.start), arm.scaled(load.end)
        a, b = to_global(load.intensity_start), to_global(load.intensity_end)
        contribution = (
            linear_contribution
            if isinstance(load, MemberDistributedLoad)
            else linear_moment_contribution
        )
        return contribution(r_a, r_b, a, b)


def normalize_spatial(run: SpatialAnalysisRun, model: StructuralModel) -> SpatialResults:
    """Resultados de ``run`` con ejes y signos de ADR-001. Función pura (plan T-018, 5b).

    Errores: corrida no SUCCEEDED → ``InputValueError`` ``ANALYSIS_FAILED``
    (``fem.results_unavailable``); modelo que no corresponde a la corrida →
    ``InputValueError`` ``REVISION_CONFLICT`` (``fem.results_model_mismatch``); bruto no
    finito o postcondición no superada → ``ResultsNormalizationError`` ``ANALYSIS_FAILED``.
    """
    if not isinstance(run, SpatialAnalysisRun):
        raise TypeError("run debe ser SpatialAnalysisRun")
    if not isinstance(model, StructuralModel):
        raise TypeError("model debe ser StructuralModel")
    if run.status is not RunStatus.SUCCEEDED or run.raw is None:
        raise _unavailable(run)
    request = _checked_request(run, model)
    _check_finite(run.raw)
    normalizer = _Normalizer(run, model, request)
    nodes = normalizer.nodes()
    reactions = normalizer.reactions()
    members = normalizer.members()
    jumps = normalizer.end_jumps()
    normalizer.check_equilibrium()
    return SpatialResults(
        run.run_id,
        run.model_revision,
        run.case_id,
        run.case_revision,
        nodes,
        reactions,
        members,
        (*run.hypotheses, HYPOTHESIS_SPATIAL_NORMALIZED),
        jumps,
    )
