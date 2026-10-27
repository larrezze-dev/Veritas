"""Adaptador FEM plano XZ con OpenSeesPy en proceso aislado (plan T-007; ADR-002).

``analyze_planar_xz`` traduce el modelo, aplica la salvaguarda de singularidad (D3),
ejecuta el worker en un proceso nuevo y comprueba el equilibrio. Devuelve un
``AnalysisRun``: un fallo, una singularidad o un tiempo agotado dan ``FAILED`` sin
resultados. Una entrada no soportada se rechaza antes con ``InputValueError``.

Los resultados son BRUTOS (base ``SOLVER_XZ_RAW``) y no están normalizados (T-008).
"""

import math
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

from veritas import __version__
from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    InputValueError,
    Location,
    ParamValue,
)
from veritas.core.geometry.model import StructuralModel
from veritas.fem.adapters.planar_xz import Translation, translate_planar_xz
from veritas.fem.adapters.protocol import (
    PROTOCOL_VERSION,
    NonFiniteValueError,
    PlanarRequest,
    ProtocolError,
    WorkerResponse,
    WorkerStatus,
)
from veritas.fem.adapters.singularity import (
    InvalidStiffnessError,
    PivotReport,
    beam_stiffness,
    check_singularity,
    reduced_coordinates,
)
from veritas.fem.config import AnalysisConfiguration
from veritas.fem.results.run import (
    ADAPTER_ID,
    ADAPTER_VERSION,
    RAW_BASIS,
    AnalysisRun,
    PivotSummary,
    RawMemberForces,
    RawNodeResult,
    RawPlanarResults,
    RawSpringReaction,
    RunStatus,
)
from veritas.fem.runners.subprocess_runner import WORKER_PATH, WorkerOutcome, run_worker

__all__ = ["AnalysisConfiguration", "AnalysisRun", "RunStatus", "analyze_planar_xz"]

# Constante c de la ampliación del control de equilibrio por redondeo de K·u: error hacia
# atrás de la solución directa (R2) y de la penalización (R1). Se registra en la procedencia.
PENALTY_EQUILIBRIUM_FACTOR: Final = 10.0


@dataclass(frozen=True, slots=True)
class _Failure:
    code: DiagnosticCode
    key: str
    params: dict[str, ParamValue]
    location: Location | None = None
    cause: str | None = None


class _Run:
    """Acumula la procedencia de una corrida."""

    def __init__(self, model: StructuralModel, config: AnalysisConfiguration) -> None:
        self.run_id = uuid4()
        self.started = datetime.now(UTC)
        self.clock = time.perf_counter()
        self.model_revision = model.model_revision
        self.config = config
        self.case_revision: int | None = None
        self.translation: Translation | None = None
        self.pivot: PivotSummary | None = None
        self.outcome: WorkerOutcome | None = None
        self.versions: dict[str, str | None] = {
            "veritas": __version__,
            "adapter": f"{ADAPTER_ID}/{ADAPTER_VERSION}",
            "protocol": str(PROTOCOL_VERSION),
            "python": None,
            "openseespy": None,
            "openseespywin": None,
        }

    def finish(self, failure: _Failure | None, raw: RawPlanarResults | None) -> AnalysisRun:
        diagnostics: list[Diagnostic] = []
        if failure is not None:
            diagnostics.append(
                Diagnostic.create(
                    failure.code,
                    failure.key,
                    failure.params,
                    location=failure.location,
                    cause=failure.cause,
                )
            )
        translation = self.translation
        if translation is not None:
            diagnostics.extend(translation.warnings)
        return AnalysisRun(
            run_id=self.run_id,
            status=RunStatus.SUCCEEDED if failure is None else RunStatus.FAILED,
            diagnostics=tuple(diagnostics),
            model_revision=self.model_revision,
            case_id=self.config.load_case_id,
            case_revision=self.case_revision,
            configuration={
                **self.config.snapshot(),
                "penalty_equilibrium_factor": PENALTY_EQUILIBRIUM_FACTOR,
            },
            request_json=None if translation is None else translation.request.to_json(),
            versions=dict(self.versions),
            hypotheses=() if translation is None else translation.hypotheses,
            started_utc=self.started,
            duration_s=time.perf_counter() - self.clock,
            pivot=self.pivot,
            worker_stdout="" if self.outcome is None else self.outcome.stdout,
            worker_stderr="" if self.outcome is None else self.outcome.stderr,
            raw=raw if failure is None else None,
        )


def _pivot_summary(request: PlanarRequest, report: PivotReport) -> PivotSummary:
    node_ids = {node.tag: UUID(node.entity_id) for node in request.nodes}
    return PivotSummary(
        free_dofs=report.free_dofs,
        min_pivot=report.min_pivot,
        node_id=None if report.dof is None else node_ids[report.dof.node],
        component=None if report.dof is None else report.dof.component,
    )


def _raw_results(request: PlanarRequest, response: WorkerResponse) -> RawPlanarResults:
    fixity = {fix.node: fix.fixity for fix in request.fixes}
    node_ids = {node.tag: UUID(node.entity_id) for node in request.nodes}
    by_node = {tag: (disp, reaction) for tag, disp, reaction in response.node_results}
    by_aux = dict(response.aux_reactions)
    by_beam = dict(response.beam_forces)
    if (
        set(by_node) != set(node_ids)
        or set(by_aux) != {s.aux_node for s in request.springs}
        or set(by_beam) != {b.tag for b in request.beams}
    ):
        raise ProtocolError("la respuesta no corresponde a la petición")
    nodes = []
    for node in request.nodes:
        disp, reaction = by_node[node.tag]
        fixed = fixity.get(node.tag, (0, 0, 0))
        nodes.append(
            RawNodeResult(
                node_ids[node.tag],
                (disp[0], disp[1], disp[2]),
                (
                    reaction[0] if fixed[0] else None,
                    reaction[1] if fixed[1] else None,
                    reaction[2] if fixed[2] else None,
                ),
            )
        )
    springs = tuple(
        RawSpringReaction(
            UUID(s.support_id),
            node_ids[s.node],
            s.dof,
            s.penalty,
            (by_aux[s.aux_node][0], by_aux[s.aux_node][1], by_aux[s.aux_node][2]),
        )
        for s in request.springs
    )
    members = tuple(
        RawMemberForces(UUID(b.entity_id), tuple(by_beam[b.tag]))  # type: ignore[arg-type]
        for b in request.beams
    )
    return RawPlanarResults(RAW_BASIS, tuple(nodes), springs, members)


def equilibrium_residual(
    request: PlanarRequest, response: WorkerResponse, config: AnalysisConfiguration
) -> tuple[bool, float, float]:
    """ΣR + ΣP = 0 en base del solver, fuerzas y momentos por separado (revisión M4).

    Devuelve (correcto, residuo de fuerza relativo, residuo de momento relativo).
    """
    coords = {node.tag: (node.x1, node.x2) for node in request.nodes}
    forces: list[tuple[float, float, float, float, float]] = []  # x1, x2, F1, F2, M
    for load in request.nodal_loads:
        forces.append((*coords[load.node], load.p1, load.p2, load.m))
    beams = {beam.tag: beam for beam in request.beams}
    for beam_load in request.beam_loads:
        beam = beams[beam_load.beam]
        (xi, zi), (xj, zj) = coords[beam.node_i], coords[beam.node_j]
        length = math.hypot(xj - xi, zj - zi)
        ux, uz = (xj - xi) / length, (zj - zi) / length
        q1 = beam_load.wx * ux - beam_load.wy * uz
        q2 = beam_load.wx * uz + beam_load.wy * ux
        forces.append(((xi + xj) / 2, (zi + zj) / 2, q1 * length, q2 * length, 0.0))
    # Solo reacciones de GDL fijados: en un GDL libre, nodeReaction es el residuo de la
    # propia solución e incluirlo haría el control tautológico.
    fixity = {fix.node: fix.fixity for fix in request.fixes}
    for tag, _disp, reaction in response.node_results:
        fixed = fixity.get(tag, (0, 0, 0))
        x1, x2 = coords[tag]
        r1, r2, r3 = (value if fixed[k] else 0.0 for k, value in enumerate(reaction))
        forces.append((x1, x2, r1, r2, r3))
    aux_node = {s.aux_node: s.node for s in request.springs}
    for tag, reaction in response.aux_reactions:
        forces.append((*coords[aux_node[tag]], *reaction))
    sum1 = sum(f[2] for f in forces)
    sum2 = sum(f[3] for f in forces)
    xs = [c[0] for c in coords.values()]
    zs = [c[1] for c in coords.values()]
    # Momentos respecto al centroide de los nudos (R3): con coordenadas grandes, respecto
    # al origen el redondeo de x·F superaría la tolerancia relativa a la diagonal.
    xc, zc = sum(xs) / len(xs), sum(zs) / len(zs)
    moment = sum((f[0] - xc) * f[3] - (f[1] - zc) * f[2] + f[4] for f in forces)
    l_ref = math.hypot(max(xs) - min(xs), max(zs) - min(zs))
    force_scale = sum(abs(f[2]) + abs(f[3]) for f in forces)
    moment_scale = sum(abs(f[4]) for f in forces) + l_ref * force_scale
    force_extra, moment_extra = _rounding_bound(request, response)
    force_residual = max(abs(sum1), abs(sum2))
    force_ok = force_residual <= config.tol_equilibrium * force_scale + force_extra
    moment_ok = abs(moment) <= (
        config.tol_equilibrium * moment_scale + moment_extra + force_extra * l_ref
    )
    return (
        force_ok and moment_ok,
        force_residual / force_scale if force_scale else 0.0,
        abs(moment) / moment_scale if moment_scale else 0.0,
    )


def _rounding_bound(request: PlanarRequest, response: WorkerResponse) -> tuple[float, float]:
    """Cota c·eps·Σ|k_ij·u_j| del redondeo de K·u, por filas de fuerza y de momento (R1, R2).

    Una solución directa estable deja residuos de ese orden; con barras esbeltas giradas y
    subdivididas superan una tolerancia relativa fija sin que haya error. Las filas de
    fuerza (N) y de momento (N·m) se acotan por separado para no mezclar unidades.
    """
    displacement = {tag: disp for tag, disp, _reaction in response.node_results}
    coords = {node.tag: (node.x1, node.x2) for node in request.nodes}
    force = moment = 0.0
    for beam in request.beams:
        element = beam_stiffness(
            coords[beam.node_i],
            coords[beam.node_j],
            beam.area,
            beam.elastic_modulus,
            beam.inertia,
        )
        u = (*displacement[beam.node_i], *displacement[beam.node_j])
        for row, values in enumerate(element):
            bound = sum(abs(k * v) for k, v in zip(values, u, strict=True))
            if row % 3 == 2:  # noqa: PLR2004 (filas de giro)
                moment += bound
            else:
                force += bound
    for spring in request.springs:
        node_u = displacement[spring.node]
        if spring.dof == 3:  # noqa: PLR2004 (dirección 3 = giro)
            moment += spring.stiffness * abs(node_u[2])
        else:
            force += spring.stiffness * math.hypot(node_u[0], node_u[1])
    scale = PENALTY_EQUILIBRIUM_FACTOR * sys.float_info.epsilon
    return scale * force, scale * moment


def _worker_failure(outcome: WorkerOutcome, timeout_s: float) -> _Failure | None:
    if outcome.timed_out:
        return _Failure(DiagnosticCode.ANALYSIS_FAILED, "fem.timeout", {"timeout": timeout_s})
    if outcome.returncode != 0 or outcome.response_text is None:
        return _Failure(
            DiagnosticCode.ANALYSIS_FAILED,
            "fem.worker_crashed",
            {"returncode": -1 if outcome.returncode is None else outcome.returncode},
            cause=outcome.stderr[-2000:] or None,
        )
    return None


def _response_failure(response: WorkerResponse) -> _Failure | None:
    if response.status == WorkerStatus.OK:
        return None
    if response.status == WorkerStatus.PROTOCOL_ERROR:
        return _Failure(
            DiagnosticCode.ANALYSIS_FAILED, "fem.protocol_error", {}, cause=response.message
        )
    code = -1 if response.analyze_code is None else response.analyze_code
    return _Failure(
        DiagnosticCode.ANALYSIS_FAILED,
        "fem.solver_failed",
        {"code": code},
        cause=response.message or None,
    )


def _too_many_dofs(count: int, limit: int) -> InputValueError:
    return InputValueError(
        Diagnostic.create(
            DiagnosticCode.UNSUPPORTED_CONFIGURATION,
            "fem.too_many_dofs",
            {"count": count, "limit": limit},
        )
    )


def analyze_planar_xz(  # noqa: PLR0911 (un retorno por causa de fallo)
    model: StructuralModel, config: AnalysisConfiguration, *, worker: Path = WORKER_PATH
) -> AnalysisRun:
    """Análisis estático lineal plano XZ de un caso (ADR-002). No modifica el modelo.

    ``worker`` solo se sustituye en pruebas del runner.
    """
    run = _Run(model, config)
    run.translation = translation = translate_planar_xz(model, config)
    request = translation.request
    run.case_revision = request.case_revision
    free = len(reduced_coordinates(request))
    if free > config.max_free_dofs:
        raise _too_many_dofs(free, config.max_free_dofs)
    try:
        report = check_singularity(request, config.tol_pivot)
    except InvalidStiffnessError as error:
        return run.finish(
            _Failure(DiagnosticCode.ANALYSIS_FAILED, "fem.invalid_stiffness", {}, cause=str(error)),
            None,
        )
    run.pivot = _pivot_summary(request, report)
    if report.singular:
        location = None if run.pivot.node_id is None else Location(entity_id=run.pivot.node_id)
        return run.finish(
            _Failure(
                DiagnosticCode.ANALYSIS_SINGULAR,
                "fem.singular",
                {
                    "pivot": report.min_pivot if report.min_pivot is not None else 0.0,
                    "component": run.pivot.component or "",
                    "tolerance": config.tol_pivot,
                },
                location,
            ),
            None,
        )
    run.outcome = outcome = run_worker(request.to_json(), config.timeout_s, worker)
    failure = _worker_failure(outcome, config.timeout_s)
    if failure is not None or outcome.response_text is None:
        return run.finish(failure, None)
    try:
        response = WorkerResponse.from_json(outcome.response_text)
        run.versions.update(response.versions)
        failure = _response_failure(response)
        if failure is not None:
            return run.finish(failure, None)
        raw = _raw_results(request, response)
    except NonFiniteValueError as error:
        return run.finish(
            _Failure(DiagnosticCode.ANALYSIS_FAILED, "fem.nonfinite_result", {}, cause=str(error)),
            None,
        )
    except ProtocolError as error:
        return run.finish(
            _Failure(DiagnosticCode.ANALYSIS_FAILED, "fem.protocol_error", {}, cause=str(error)),
            None,
        )
    ok, force_residual, moment_residual = equilibrium_residual(request, response, config)
    if not ok:
        return run.finish(
            _Failure(
                DiagnosticCode.ANALYSIS_FAILED,
                "fem.equilibrium_failed",
                {
                    "force": force_residual,
                    "moment": moment_residual,
                    "tolerance": config.tol_equilibrium,
                },
            ),
            None,
        )
    return run.finish(None, raw)
