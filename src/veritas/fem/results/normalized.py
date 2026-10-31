"""Resultados normalizados a ADR-001 de una corrida plana XZ (plan T-008).

``normalize_planar_xz`` convierte la salida BRUTA (base ``SOLVER_XZ_RAW``) en vectores de
seis componentes con ejes y signos de ADR-001:

1. Elevación a 3D global: (a₁, a₂) → (a₁, 0, a₂) y m → (0, −m, 0), porque x₁ × x₂ = −Y.
2. ``localForce`` = (N_I, V_I, M_I, N_J, V_J, M_J): acciones del nudo sobre el elemento en
   la base 2D (x̂ de I a J, ŷ = x̂ girado +90°), con las fuerzas de empotramiento de
   ``-beamUniform`` (gates G-N1…G-N3), más el p0 de las cargas de barra de Veritas (T-015,
   D10-A). Se elevan y se rotan con la Q de la barra.
3. Reacción de apoyo = ``nodeReaction`` en los GDL con ``fix`` (con la de los aux de
   extremo de barra en traslación, T-010) + reacciones de sus nudos auxiliares de resorte;
   en base inclinada, además Q_apoyo·R.
4. Extremos no rígidos (T-010): las fuerzas de extremo actúan sobre el nudo (el aux solo
   comparte traslaciones y transmite el momento del resorte, o ninguno si está liberado).
   El giro de un nudo huérfano restringido para el cálculo es NOT_APPLICABLE.

El estado de cada componente se decide por la estructura, nunca por su valor. Postcondiciones
(D7, B1): equilibrio de nudos, barras y global, y coherencia de Q. Si fallan, no hay
resultados. La formulación plana solo se ha contrastado con T-FEM-001…004 (T-009) y
T-CON-REF-01…04 (T-010); los valores no están validados.
"""

import math
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
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
from veritas.fem.adapters.planar_xz import translate_planar_xz
from veritas.fem.adapters.protocol import PlanarRequest
from veritas.fem.adapters.singularity import beam_stiffness, element_displacement
from veritas.fem.config import AnalysisConfiguration
from veritas.fem.postconditions import member_fixed_end_forces
from veritas.fem.results.run import AnalysisRun, RunStatus

HYPOTHESIS_NORMALIZED: Final = "fem.hypothesis_normalized"
HYPOTHESIS_SUPPORT_PROJECTION: Final = "fem.hypothesis_support_projection"

# Constante c de la cota de redondeo c·eps·Σ|k_ij·u_j| (la de T-007, R1-R2).
ROUNDING_FACTOR: Final = 10.0
# Fila de momento de un nudo con resorte de penalización (T-021): la eliminación con un nudo
# 1e2…1e12 veces más rígido amplifica el redondeo de esa fila hasta 33,5·eps·Σ|k·u| (sonda de
# E1, 900 corridas); 200 deja un margen de 6×. No depende de k_pen: el residuo no escala con él.
ROUNDING_FACTOR_PENALTY_MOMENT: Final = 200.0
# Coherencia de Q (B1): |componente fuera del plano| ≤ (2·tol_axis + 4·eps)·‖v‖. El factor 2
# acota |ex·Y| + |ez·Y| (≈ √2·tol_axis con Q casi ortogonal). Es una defensa: rama y Q salen
# del mismo modelo ya verificado, así que no es alcanzable por la API pública. No distingue
# alfa de alfa + π ni el intercambio ex↔ez (D8). Con tol_axis < ~1e-12 el defecto de ortogonalidad
# admitido por ADR-001 §1.6 podría dar un falso fallo.
AXES_FACTOR: Final = 2.0
AXES_EPS_FACTOR: Final = 4.0

_EPS: Final = sys.float_info.epsilon
_SNAPSHOT_EXTRA: Final = frozenset(
    {
        "penalty_equilibrium_factor",
        # Constantes del modo de mecanismo (T-017, D3), registradas en la procedencia.
        "mechanism_participation",
        "mechanism_guard_low",
        "mechanism_guard_high",
        "mechanism_residual_factor",
    }
)


class ResultsNormalizationError(InputValueError):
    """Postcondición de la normalización no superada (D7, B1): sin resultados."""


class ComponentStatus(StrEnum):
    """Estado de una componente de resultado (plan T-008)."""

    CALCULATED = "CALCULATED"
    NOT_APPLICABLE = "NOT_APPLICABLE"  # GDL libre del apoyo: no es cero.
    NOT_CALCULATED = "NOT_CALCULATED"  # Fuera del plano: el análisis plano no la obtiene.


class ResultBasis(StrEnum):
    """Base declarada de un vector de resultado (ADR-001 §1)."""

    GLOBAL = "GLOBAL"
    SUPPORT = "SUPPORT"
    MEMBER_LOCAL = "MEMBER_LOCAL"


class ReactionSource(StrEnum):
    """Cómo se obtiene la reacción de un GDL del apoyo."""

    FIX = "FIX"
    SPRING = "SPRING"
    PENALTY = "PENALTY"


@dataclass(frozen=True, slots=True)
class Component:
    """Valor SI; ``None`` si y solo si el estado no es ``CALCULATED``."""

    value: float | None
    status: ComponentStatus

    def __post_init__(self) -> None:
        if (self.value is None) == (self.status is ComponentStatus.CALCULATED):
            raise ValueError("Component: value es None si y solo si no está calculada")


@dataclass(frozen=True, slots=True)
class Vector6:
    """Seis componentes en el orden ADR-001: (Ux, Uy, Uz, Rx, Ry, Rz) o (Fx…Mz)."""

    basis: ResultBasis
    components: tuple[Component, Component, Component, Component, Component, Component]

    def values(self) -> tuple[float | None, ...]:
        return tuple(c.value for c in self.components)


@dataclass(frozen=True, slots=True)
class NodeDisplacement:
    """Desplazamientos (m) y giros (rad) del nudo en base global."""

    node_id: UUID
    displacement: Vector6


@dataclass(frozen=True, slots=True)
class SupportReaction:
    """Acción del apoyo sobre la estructura (N, N·m; ADR-001 §2).

    ``support_basis`` y ``axes`` (filas de Q_apoyo) solo con base INCLINADA. ``sources``
    va por GDL restringido del apoyo, en su propia base (M4).
    """

    support_id: UUID
    node_id: UUID
    global_: Vector6
    support_basis: Vector6 | None
    axes: tuple[Vec3, Vec3, Vec3] | None
    sources: tuple[tuple[SupportDof, ReactionSource], ...]


@dataclass(frozen=True, slots=True)
class MemberEndForces:
    """Fuerzas de extremo pI/pJ (acción del nudo sobre la barra) en base local.

    ``section_start`` = q(0+) = −pI y ``section_end`` = q(L−) = pJ, en el orden
    (N, Vy, Vz, T, My, Mz); válidos porque no hay offsets ni cargas puntuales en los
    extremos (T-015, D2-A). Con cargas de barra de Veritas, pI/pJ = localForce + p0 (D10-A).
    Con extremos liberados o con resorte (T-010) siguen siendo las acciones del extremo de
    la barra; en un giro liberado el momento es nulo salvo redondeo. ``axes`` son las filas
    de la Q usada (D8, trazabilidad).
    """

    member_id: UUID
    inertia_axis: str
    axes: tuple[Vec3, Vec3, Vec3]
    p_i: Vector6
    p_j: Vector6
    section_start: Vector6
    section_end: Vector6


@dataclass(frozen=True, slots=True)
class PlanarResults:
    """Resultados normalizados de una corrida plana XZ. Sin validación de referencia."""

    run_id: UUID
    model_revision: int
    case_id: UUID
    case_revision: int | None
    nodes: tuple[NodeDisplacement, ...]
    reactions: tuple[SupportReaction, ...]
    members: tuple[MemberEndForces, ...]
    hypotheses: tuple[str, ...]


# --- Errores ---------------------------------------------------------------------------


def _unavailable(run: AnalysisRun) -> InputValueError:
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


# --- Correspondencia modelo ↔ corrida (M3) ------------------------------------------------


def _configuration(run: AnalysisRun) -> AnalysisConfiguration:
    snapshot = {k: v for k, v in run.configuration.items() if k not in _SNAPSHOT_EXTRA}
    case = snapshot.pop("load_case_id")
    return AnalysisConfiguration(UUID(str(case)), **snapshot)  # type: ignore[arg-type]


def _checked_request(run: AnalysisRun, model: StructuralModel) -> PlanarRequest:
    if model.model_revision != run.model_revision:
        raise _mismatch("model_revision")
    if run.request_json is None:
        raise _mismatch("request")
    try:
        config = _configuration(run)
    except (TypeError, KeyError, ValueError) as error:
        raise _mismatch("configuration") from error
    try:
        request = translate_planar_xz(model, config).request
    except InputValueError as error:
        raise _mismatch(str(error.code)) from error
    if request.to_json() != run.request_json:
        raise _mismatch("request")
    return request


# --- Componentes ---------------------------------------------------------------------------

_NC: Final = Component(None, ComponentStatus.NOT_CALCULATED)
_NA: Final = Component(None, ComponentStatus.NOT_APPLICABLE)


def _calc(value: float) -> Component:
    return Component(value, ComponentStatus.CALCULATED)


def _vector(basis: ResultBasis, values: Iterable[Component]) -> Vector6:
    c = tuple(values)
    return Vector6(basis, (c[0], c[1], c[2], c[3], c[4], c[5]))


def _rows(q: tuple[Vec3, Vec3, Vec3], v: Vec3) -> tuple[float, float, float]:
    return (q[0].dot(v), q[1].dot(v), q[2].dot(v))


# Componentes en el plano de cada rama (índices de (Fx, Fy, Fz, Mx, My, Mz)).
_IN_PLANE: Final = {"Iy": frozenset({0, 2, 4}), "Iz": frozenset({0, 1, 5})}


@dataclass(frozen=True, slots=True)
class _End:
    """Extremo elevado a 3D global: fuerza y momento."""

    force: Vec3
    moment: Vec3


def _member_vector(
    end: _End, q: tuple[Vec3, Vec3, Vec3], branch: str, config: AnalysisConfiguration
) -> tuple[float, ...]:
    local = (*_rows(q, end.force), *_rows(q, end.moment))
    factor = AXES_FACTOR * config.tol_axis + AXES_EPS_FACTOR * _EPS
    for index in range(6):
        if index in _IN_PLANE[branch]:
            continue
        norm = end.force.norm() if index < 3 else end.moment.norm()  # noqa: PLR2004
        if abs(local[index]) > factor * norm:
            raise _postcondition(
                "fem.results_axes_inconsistent",
                {"component": index, "value": local[index], "limit": factor * norm},
            )
    return local


def _to_vector(local: tuple[float, ...], branch: str, sign: float = 1.0) -> Vector6:
    return _vector(
        ResultBasis.MEMBER_LOCAL,
        (_calc(sign * v) if i in _IN_PLANE[branch] else _NC for i, v in enumerate(local)),
    )


# --- Normalización ---------------------------------------------------------------------------


def _elevate_force(a1: float, a2: float) -> Vec3:
    return Vec3(a1, 0.0, a2)


def _elevate_moment(m: float) -> Vec3:
    return Vec3(0.0, -m, 0.0)


class _Normalizer:
    def __init__(self, run: AnalysisRun, model: StructuralModel, request: PlanarRequest) -> None:
        assert run.raw is not None
        self.run = run
        self.raw = run.raw
        self.model = model
        self.request = request
        self.config = _configuration(run)
        self.tag_of = {UUID(n.entity_id): n.tag for n in request.nodes}
        self.coords = {n.tag: (n.x1, n.x2) for n in request.nodes}
        self.beams = {UUID(b.entity_id): b for b in request.beams}
        self.displacement = {n.node_id: n.displacement for n in self.raw.nodes}
        self.ends: dict[UUID, tuple[_End, _End]] = {}
        # p0 de las cargas de barra de Veritas (T-015), de la petición (fuente única).
        self.fixed_end = member_fixed_end_forces(request)
        self.reaction_force: dict[UUID, tuple[Vec3, Vec3]] = {}  # por nudo

    # Desplazamientos (D3-A: fuera del plano NOT_CALCULATED).
    def nodes(self) -> tuple[NodeDisplacement, ...]:
        result = []
        orphan_tags = set(self.request.orphan_fixes)
        orphans = {node_id for node_id, tag in self.tag_of.items() if tag in orphan_tags}
        for raw in self.raw.nodes:
            u1, u2, theta = raw.displacement
            # Giro huérfano (T-010, D3): restringido para el cálculo, sin valor propio.
            ry = _NA if raw.node_id in orphans else _calc(_elevate_moment(theta).y)
            result.append(
                NodeDisplacement(
                    raw.node_id,
                    _vector(ResultBasis.GLOBAL, (_calc(u1), _NC, _calc(u2), _NC, ry, _NC)),
                )
            )
        return tuple(result)

    # Reacciones (M4).
    def reactions(self) -> tuple[SupportReaction, ...]:
        raw_nodes = {n.node_id: n for n in self.raw.nodes}
        springs: dict[UUID, list[tuple[int, bool, tuple[float, float, float]]]] = {}
        for spring in self.raw.springs:
            springs.setdefault(spring.support_id, []).append(
                (spring.dof, spring.penalty, spring.reaction)
            )
        result = []
        for support in sorted(self.model.supports.values(), key=lambda s: self.tag_of[s.node_id]):
            raw = raw_nodes[support.node_id]
            r1, r2, rm = (0.0 if v is None else v for v in raw.reaction)
            for _dof, _penalty, (a1, a2, am) in springs.get(support.id, []):
                r1, r2, rm = r1 + a1, r2 + a2, rm + am
            force, moment = _elevate_force(r1, r2), _elevate_moment(rm)
            self.reaction_force[support.node_id] = (force, moment)
            result.append(self._reaction(support, force, moment, springs.get(support.id, [])))
        return tuple(result)

    def _reaction(
        self,
        support: Support,
        force: Vec3,
        moment: Vec3,
        springs: list[tuple[int, bool, tuple[float, float, float]]],
    ) -> SupportReaction:
        def restrained(dof: SupportDof) -> bool:
            return support.restraint(dof).kind is not RestraintKind.FREE

        sources: list[tuple[SupportDof, ReactionSource]] = []
        by_direction = {dof: penalty for dof, penalty, _ in springs}
        for direction, dof in ((1, SupportDof.UX), (2, SupportDof.UZ), (3, SupportDof.RY)):
            if not restrained(dof):
                continue
            if direction in by_direction:
                penalty = by_direction[direction]
                sources.append((dof, ReactionSource.PENALTY if penalty else ReactionSource.SPRING))
            else:
                sources.append((dof, ReactionSource.FIX))
        ry = _calc(moment.y) if restrained(SupportDof.RY) else _NA
        inclined = support.basis.kind is BasisKind.INCLINED and support.basis.rows is not None
        if inclined:
            translation = restrained(SupportDof.UX) or restrained(SupportDof.UZ)
            fx = _calc(force.x) if translation else _NA
            fz = _calc(force.z) if translation else _NA
        else:
            fx = _calc(force.x) if restrained(SupportDof.UX) else _NA
            fz = _calc(force.z) if restrained(SupportDof.UZ) else _NA
        global_ = _vector(ResultBasis.GLOBAL, (fx, _NC, fz, _NC, ry, _NC))
        local: Vector6 | None = None
        axes: tuple[Vec3, Vec3, Vec3] | None = None
        if inclined and support.basis.rows is not None:
            axes = support.basis.rows
            f_loc, m_loc = _rows(axes, force), _rows(axes, moment)
            local = _vector(
                ResultBasis.SUPPORT,
                (
                    _calc(f_loc[0]) if restrained(SupportDof.UX) else _NA,
                    _NC,
                    _calc(f_loc[2]) if restrained(SupportDof.UZ) else _NA,
                    _NC,
                    _calc(m_loc[1]) if restrained(SupportDof.RY) else _NA,
                    _NC,
                ),
            )
        return SupportReaction(support.id, support.node_id, global_, local, axes, tuple(sources))

    # Fuerzas de extremo.
    def members(self) -> tuple[MemberEndForces, ...]:
        result = []
        for raw in self.raw.members:
            beam = self.beams[raw.member_id]
            (xi, zi), (xj, zj) = self.coords[beam.node_i], self.coords[beam.node_j]
            length = math.hypot(xj - xi, zj - zi)
            c, s = (xj - xi) / length, (zj - zi) / length
            x_hat, y_hat = Vec3(c, 0.0, s), Vec3(-s, 0.0, c)
            local_force: tuple[float, ...] = raw.local_force
            p0 = self.fixed_end.get(beam.tag)
            if p0 is not None:  # D10-A: publicado = localForce + p0; sin p0, intacto (−0,0).
                local_force = tuple(f + v for f, v in zip(local_force, p0, strict=True))
            n_i, v_i, m_i, n_j, v_j, m_j = local_force
            end_i = _End(x_hat.scaled(n_i) + y_hat.scaled(v_i), _elevate_moment(m_i))
            end_j = _End(x_hat.scaled(n_j) + y_hat.scaled(v_j), _elevate_moment(m_j))
            self.ends[raw.member_id] = (end_i, end_j)
            q = self.model.axes(raw.member_id).matrix()
            rows = (Vec3(*q[0]), Vec3(*q[1]), Vec3(*q[2]))
            branch = beam.inertia_axis
            local_i = _member_vector(end_i, rows, branch, self.config)
            local_j = _member_vector(end_j, rows, branch, self.config)
            result.append(
                MemberEndForces(
                    raw.member_id,
                    branch,
                    rows,
                    _to_vector(local_i, branch),
                    _to_vector(local_j, branch),
                    _to_vector(local_i, branch, -1.0),
                    _to_vector(local_j, branch),
                )
            )
        return tuple(result)

    # Postcondición de equilibrio (D7), con los vectores globales elevados (M1).
    def check_equilibrium(self) -> None:
        tol = self.config.tol_equilibrium
        nodal, distributed, resultants = self._domain_loads()
        bounds = self._rounding_bounds()
        for node_id, tag in self.tag_of.items():
            f_sum, m_sum = Vec3(0.0, 0.0, 0.0), Vec3(0.0, 0.0, 0.0)
            f_scale = m_scale = 0.0
            for force, moment in nodal.get(node_id, []):
                f_sum, m_sum = f_sum + force, m_sum + moment
                f_scale, m_scale = f_scale + force.norm(), m_scale + moment.norm()
            if node_id in self.reaction_force:
                force, moment = self.reaction_force[node_id]
                f_sum, m_sum = f_sum + force, m_sum + moment
                f_scale, m_scale = f_scale + force.norm(), m_scale + moment.norm()
            for member_id, beam in self.beams.items():
                for end_tag, end in zip(
                    (beam.node_i, beam.node_j), self.ends[member_id], strict=True
                ):
                    if end_tag == tag:
                        f_sum, m_sum = f_sum - end.force, m_sum - end.moment
                        f_scale += end.force.norm()
                        m_scale += end.moment.norm()
            f_extra, m_extra = bounds.get(tag, (0.0, 0.0))
            self._require(
                "node",
                node_id,
                (f_sum.norm(), tol * f_scale + f_extra),
                (m_sum.norm(), tol * m_scale + m_extra),
            )
        for member_id, beam in self.beams.items():
            self._member_equilibrium(
                member_id, beam.node_i, beam.node_j, (distributed, resultants), bounds
            )
        self._global_equilibrium(nodal, (distributed, resultants), bounds)

    def _global_equilibrium(
        self,
        nodal: dict[UUID, list[tuple[Vec3, Vec3]]],
        member_loads: tuple[dict[UUID, Vec3], dict[UUID, tuple[Vec3, Vec3]]],
        bounds: dict[int, tuple[float, float]],
    ) -> None:
        """ΣP + ΣR + Σq·L = 0, momentos respecto al centroide de los nudos (como en T-007)."""
        distributed, resultants = member_loads
        points = {self.tag_of[k]: Vec3(x, 0.0, z) for k, (x, z) in self._node_xz().items()}
        centre = Vec3(0.0, 0.0, 0.0)
        for point in points.values():
            centre = centre + point.scaled(1.0 / len(points))
        actions: list[tuple[Vec3, Vec3, Vec3]] = []  # punto, fuerza, momento
        for node_id, loads in nodal.items():
            actions += [(points[self.tag_of[node_id]], f, m) for f, m in loads]
        for node_id, (force, moment) in self.reaction_force.items():
            actions.append((points[self.tag_of[node_id]], force, moment))
        for member_id, beam in self.beams.items():
            q = distributed.get(member_id)
            if q is not None:
                i, j = points[beam.node_i], points[beam.node_j]
                load = Vec3(q.x, 0.0, q.z).scaled((j - i).norm())
                actions.append(((i + j).scaled(0.5), load, Vec3(0.0, 0.0, 0.0)))
            if member_id in resultants:  # T-015: resultante (F, M respecto a I).
                actions.append((points[beam.node_i], *resultants[member_id]))
        force = moment = Vec3(0.0, 0.0, 0.0)
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
            self.request_case(),
            (force.norm(), tol * f_scale + f_extra),
            (moment.norm(), tol * m_scale + m_extra + f_extra * l_ref),
        )

    def _node_xz(self) -> dict[UUID, tuple[float, float]]:
        return {node_id: self.coords[tag] for node_id, tag in self.tag_of.items()}

    def _member_equilibrium(
        self,
        member_id: UUID,
        tag_i: int,
        tag_j: int,
        member_loads: tuple[dict[UUID, Vec3], dict[UUID, tuple[Vec3, Vec3]]],
        bounds: dict[int, tuple[float, float]],
    ) -> None:
        tol = self.config.tol_equilibrium
        distributed, resultants = member_loads
        end_i, end_j = self.ends[member_id]
        (xi, zi), (xj, zj) = self.coords[tag_i], self.coords[tag_j]
        arm = Vec3(xj - xi, 0.0, zj - zi)
        length = arm.norm()
        load = distributed.get(member_id, Vec3(0.0, 0.0, 0.0)).scaled(length)
        load = Vec3(load.x, 0.0, load.z)  # Solo el plano: q_Y residual no se transmite.
        force = end_i.force + end_j.force + load
        moment = end_i.moment + end_j.moment + arm.cross(end_j.force) + arm.scaled(0.5).cross(load)
        f_scale = end_i.force.norm() + end_j.force.norm() + load.norm()
        m_scale = end_i.moment.norm() + end_j.moment.norm() + length * f_scale
        if member_id in resultants:  # T-015, D10-A: resultante respecto a I.
            r_force, r_moment = resultants[member_id]
            force, moment = force + r_force, moment + r_moment
            f_scale += r_force.norm()
            m_scale += r_moment.norm() + length * r_force.norm()
        fi, mi = bounds.get(tag_i, (0.0, 0.0))
        fj, mj = bounds.get(tag_j, (0.0, 0.0))
        self._require(
            "member",
            member_id,
            (force.norm(), tol * f_scale + fi + fj),
            (moment.norm(), tol * m_scale + mi + mj + (fi + fj) * length),
        )

    def _require(
        self, where: str, entity: UUID, force: tuple[float, float], moment: tuple[float, float]
    ) -> None:
        """Residuo ≤ cota, para (residuo, cota) de fuerza y de momento."""
        if force[0] > force[1] or moment[0] > moment[1]:
            raise _postcondition(
                "fem.results_equilibrium",
                {"where": where, "force": force[0], "moment": moment[0]},
                Location(entity_id=entity),
            )

    def _domain_loads(
        self,
    ) -> tuple[
        dict[UUID, list[tuple[Vec3, Vec3]]], dict[UUID, Vec3], dict[UUID, tuple[Vec3, Vec3]]
    ]:
        """Cargas del caso tomadas del dominio, no de la petición (M2).

        Devuelve las nodales, la q uniforme por barra y, para las cargas de barra de Veritas
        (T-015, D10-A), la resultante por barra (F, M respecto al extremo I) en el plano, en
        forma cerrada del núcleo y no con ``member_loads``: es el control independiente de p0.
        """
        nodal: dict[UUID, list[tuple[Vec3, Vec3]]] = {}
        distributed: dict[UUID, Vec3] = {}
        resultants: dict[UUID, tuple[Vec3, Vec3]] = {}
        for load in self.model.loads_in_case(self.request_case()):
            if isinstance(load, NodalLoad):
                nodal.setdefault(load.node_id, []).append((load.force, load.moment))
            elif isinstance(load, MemberUniformLoad):
                q = load.intensity
                if load.basis is LoadBasis.LOCAL:
                    q = self.model.axes(load.member_id).to_global(q)
                distributed[load.member_id] = distributed.get(load.member_id, Vec3(0, 0, 0)) + q
            elif isinstance(load, SelfWeight):
                for member_id in self.beams:
                    q_z = self.model.self_weight_load(member_id)[2].si_value
                    total = distributed.get(member_id, Vec3(0, 0, 0)) + Vec3(0.0, 0.0, q_z)
                    distributed[member_id] = total
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
            else:  # pragma: no cover (O1: otro tipo de carga invalida q(0+) = −pI)
                raise _postcondition("fem.results_unsupported_load", {"load": str(load.id)})
        return nodal, distributed, resultants

    def _member_resultant(
        self, load: MemberPointLoad | MemberDistributedLoad | MemberDistributedMoment
    ) -> tuple[Vec3, Vec3]:
        """(F, M respecto a I) en el plano: F en X, Z y M en Y (lo residual no se transmite)."""
        beam = self.beams[load.member_id]
        (xi, zi), (xj, zj) = self.coords[beam.node_i], self.coords[beam.node_j]
        arm = Vec3(xj - xi, 0.0, zj - zi)

        def to_global(vector: Vec3) -> Vec3:
            if load.basis is LoadBasis.LOCAL:
                return self.model.axes(load.member_id).to_global(vector)
            return vector

        if isinstance(load, MemberPointLoad):
            force, moment = point_contribution(
                arm.scaled(load.position), to_global(load.force), to_global(load.moment)
            )
        else:
            r_a, r_b = arm.scaled(load.start), arm.scaled(load.end)
            a, b = to_global(load.intensity_start), to_global(load.intensity_end)
            contribution = (
                linear_contribution
                if isinstance(load, MemberDistributedLoad)
                else linear_moment_contribution
            )
            force, moment = contribution(r_a, r_b, a, b)
        return Vec3(force.x, 0.0, force.z), Vec3(0.0, moment.y, 0.0)

    def request_case(self) -> UUID:
        return UUID(self.request.case_id)

    def _rounding_bounds(self) -> dict[int, tuple[float, float]]:
        """Cota c·eps·Σ|k_ij·u_j| por nudo, en filas de fuerza y de momento (M2).

        c = ``ROUNDING_FACTOR``; en la fila de momento de un nudo con resorte de penalización,
        ``ROUNDING_FACTOR_PENALTY_MOMENT`` (T-021).
        """
        displacement: dict[int, tuple[float, ...]] = {
            self.tag_of[k]: v for k, v in self.displacement.items()
        }
        theta = {(str(r.member_id), r.end): r.theta for r in self.raw.end_rotations}
        for link in self.request.end_links:
            displacement[link.aux] = (0.0, 0.0, theta[(link.member_id, link.end)])
        bounds: dict[int, list[float]] = {}
        for beam in self.request.beams:
            element = beam_stiffness(
                self.coords[beam.node_i],
                self.coords[beam.node_j],
                beam.area,
                beam.elastic_modulus,
                beam.inertia,
            )
            u = element_displacement(self.request, beam, displacement)
            for row, values in enumerate(element):
                tag = beam.node_i if row < 3 else beam.node_j  # noqa: PLR2004
                entry = bounds.setdefault(tag, [0.0, 0.0])
                entry[1 if row % 3 == 2 else 0] += sum(  # noqa: PLR2004 (filas de giro)
                    abs(k * v) for k, v in zip(values, u, strict=True)
                )
        penalized: set[int] = set()
        for spring in self.request.springs:
            node_u = displacement[spring.node]
            entry = bounds.setdefault(spring.node, [0.0, 0.0])
            if spring.penalty:
                penalized.add(spring.node)
            if spring.dof == 3:  # noqa: PLR2004 (dirección 3 = giro)
                entry[1] += spring.stiffness * abs(node_u[2])
            else:
                entry[0] += spring.stiffness * math.hypot(node_u[0], node_u[1])
        for link in self.request.end_links:
            if link.stiffness is not None:
                # Σ|k_ij·u_j| de las dos filas del resorte, en el nudo maestro.
                rotations = abs(displacement[link.master][2]) + abs(displacement[link.aux][2])
                entry = bounds.setdefault(link.master, [0.0, 0.0])
                entry[1] += 2.0 * link.stiffness * rotations
        scale = ROUNDING_FACTOR * _EPS
        moment_scale = ROUNDING_FACTOR_PENALTY_MOMENT * _EPS
        return {
            tag: (scale * f, (moment_scale if tag in penalized else scale) * m)
            for tag, (f, m) in bounds.items()
        }


def normalize_planar_xz(run: AnalysisRun, model: StructuralModel) -> PlanarResults:
    """Resultados de ``run`` con ejes y signos de ADR-001. Función pura (plan T-008).

    Errores: corrida no SUCCEEDED → ``InputValueError`` ``ANALYSIS_FAILED``
    (``fem.results_unavailable``); modelo que no corresponde a la corrida →
    ``InputValueError`` ``REVISION_CONFLICT`` (``fem.results_model_mismatch``);
    postcondición no superada → ``ResultsNormalizationError`` ``ANALYSIS_FAILED``.
    """
    if not isinstance(run, AnalysisRun):
        raise TypeError("run debe ser AnalysisRun")
    if not isinstance(model, StructuralModel):
        raise TypeError("model debe ser StructuralModel")
    if run.status is not RunStatus.SUCCEEDED or run.raw is None:
        raise _unavailable(run)
    request = _checked_request(run, model)
    normalizer = _Normalizer(run, model, request)
    nodes = normalizer.nodes()
    reactions = normalizer.reactions()
    members = normalizer.members()
    normalizer.check_equilibrium()
    hypotheses = [*run.hypotheses, HYPOTHESIS_NORMALIZED]
    if any(r.support_basis is not None for r in reactions):
        hypotheses.append(HYPOTHESIS_SUPPORT_PROJECTION)
    return PlanarResults(
        run.run_id,
        run.model_revision,
        run.case_id,
        run.case_revision,
        nodes,
        reactions,
        members,
        tuple(hypotheses),
    )
