"""Adaptador FEM espacial de seis GDL por nudo (ADR-003, plan T-018).

``analyze_spatial`` traduce el modelo, aplica la salvaguarda de singularidad con su modo,
ejecuta el worker 3D en un proceso nuevo y comprueba el equilibrio y los controles de
``spatial.postconditions``. Devuelve un ``SpatialAnalysisRun``: un fallo, una singularidad
o un tiempo agotado dan ``FAILED`` sin resultados. Una entrada no soportada se rechaza
antes con ``InputValueError``. Los resultados son BRUTOS; ``normalize_spatial`` los
publica con los ejes y signos de ADR-001.
"""

import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Final
from uuid import UUID, uuid4

from veritas import __version__
from veritas.core.diagnostics import Diagnostic, DiagnosticCode, Location, Severity
from veritas.core.geometry.model import StructuralModel
from veritas.core.messages_es import render_text
from veritas.fem import (
    MECHANISM_TIE_TOLERANCE,
    _entity_label,
    _Failure,
    _no_free_dofs,
    _too_many_dofs,
    _worker_failure,
)
from veritas.fem.adapters import opensees_worker_3d
from veritas.fem.adapters.protocol import NonFiniteValueError, ProtocolError, WorkerStatus
from veritas.fem.adapters.singularity import (
    MECHANISM_GUARD_BAND,
    MECHANISM_PARTICIPATION,
    MECHANISM_RESIDUAL_FACTOR,
    InvalidStiffnessError,
)
from veritas.fem.postconditions import PENALTY_EQUILIBRIUM_FACTOR, CheckFailure
from veritas.fem.results.run import MechanismDof, PivotSummary, RunStatus
from veritas.fem.runners.subprocess_runner import WorkerOutcome, run_worker
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.postconditions import (
    CHAIN_ALIAS_FACTOR,
    equilibrium_residual,
    node_reactions,
    solution_checks,
)
from veritas.fem.spatial.protocol import (
    PROTOCOL_VERSION,
    SpatialRequest,
    SpatialWorkerResponse,
)
from veritas.fem.spatial.results import (
    RAW_SPATIAL_BASIS,
    SPATIAL_ADAPTER_ID,
    SPATIAL_ADAPTER_VERSION,
    RawSpatialGroundReaction,
    RawSpatialMemberForces,
    RawSpatialNodeResult,
    RawSpatialResults,
    SpatialAnalysisRun,
    SpatialResults,
    normalize_spatial,
)
from veritas.fem.spatial.stiffness import (
    GLOBAL_COMPONENTS,
    REDUNDANCY_TOLERANCE,
    SpatialPivotReport,
    check_singularity,
    reduced_coordinates,
    solver_free_dofs,
)
from veritas.fem.spatial.translator import SpatialTranslation, translate_spatial

__all__ = [
    "SpatialAnalysisConfiguration",
    "SpatialAnalysisRun",
    "SpatialResults",
    "analyze_spatial",
    "normalize_spatial",
]

WORKER_3D_PATH: Final = Path(opensees_worker_3d.__file__).resolve()
# Única salida de stderr de una corrida limpia de OpenSeesPy 3.8 en Linux (CI de E1).
LINUX_EXIT_BANNER: Final = "Process 0 Terminating"

# Orden de las componentes del modo dentro de un nudo: traslaciones (globales y oblicuas)
# y luego giros (O3: las oblicuas se publican como t1/t2/r1/r2, extensión del «t» plano).
_COMPONENT_ORDER: Final = {
    name: position
    for position, name in enumerate(
        (*GLOBAL_COMPONENTS[:3], "t1", "t2", *GLOBAL_COMPONENTS[3:], "r1", "r2")
    )
}


class _Run:
    """Acumula la procedencia de una corrida espacial."""

    def __init__(self, model: StructuralModel, config: SpatialAnalysisConfiguration) -> None:
        self.run_id = uuid4()
        self.started = datetime.now(UTC)
        self.clock = time.perf_counter()
        self.model_revision = model.model_revision
        self.config = config
        self.case_revision: int | None = None
        self.translation: SpatialTranslation | None = None
        self.pivot: PivotSummary | None = None
        self.outcome: WorkerOutcome | None = None
        self.warnings: list[Diagnostic] = []
        self.versions: dict[str, str | None] = {
            "veritas": __version__,
            "adapter": f"{SPATIAL_ADAPTER_ID}/{SPATIAL_ADAPTER_VERSION}",
            "protocol": str(PROTOCOL_VERSION),
            "python": None,
            "openseespy": None,
            "openseespywin": None,
        }

    def finish(self, failure: _Failure | None, raw: RawSpatialResults | None) -> SpatialAnalysisRun:
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
        diagnostics.extend(self.warnings)
        translation = self.translation
        if translation is not None:
            diagnostics.extend(translation.warnings)
        return SpatialAnalysisRun(
            run_id=self.run_id,
            status=RunStatus.SUCCEEDED if failure is None else RunStatus.FAILED,
            diagnostics=tuple(diagnostics),
            model_revision=self.model_revision,
            case_id=self.config.load_case_id,
            case_revision=self.case_revision,
            configuration={**self.config.snapshot(), **provenance_constants()},
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


def provenance_constants() -> dict[str, float]:
    """Constantes de los controles y del modo registradas en la procedencia."""
    return {
        "penalty_equilibrium_factor": PENALTY_EQUILIBRIUM_FACTOR,
        "chain_alias_factor": CHAIN_ALIAS_FACTOR,
        "mechanism_participation": MECHANISM_PARTICIPATION,
        "mechanism_guard_low": MECHANISM_GUARD_BAND[0],
        "mechanism_guard_high": MECHANISM_GUARD_BAND[1],
        "mechanism_residual_factor": MECHANISM_RESIDUAL_FACTOR,
        "redundancy_tolerance": REDUNDANCY_TOLERANCE,
    }


def _mechanism(
    model: StructuralModel, request: SpatialRequest, report: SpatialPivotReport
) -> tuple[MechanismDof, ...]:
    """Participantes (p ≥ MECHANISM_PARTICIPATION) ordenados por etiqueta del nudo, id y
    componente. Sin extremos de barra en E1."""
    assert report.mode is not None
    node_ids = {node.tag: UUID(node.entity_id) for node in request.nodes}
    items = [
        MechanismDof(node_ids[label.node], label.component, participation)
        for label, participation in zip(report.labels, report.mode.participation, strict=True)
        if participation >= MECHANISM_PARTICIPATION
    ]

    def key(item: MechanismDof) -> tuple[str, str, int]:
        label = _entity_label(model, item.node_id, False)
        return (label, str(item.node_id), _COMPONENT_ORDER[item.component])

    return tuple(sorted(items, key=key))


def _pivot_summary(
    model: StructuralModel, request: SpatialRequest, report: SpatialPivotReport
) -> PivotSummary:
    node_ids = {node.tag: UUID(node.entity_id) for node in request.nodes}
    nullity: int | None = None
    mechanism: tuple[MechanismDof, ...] = ()
    if report.mode is not None and report.mode.conclusive:
        nullity = report.mode.nullity
        mechanism = _mechanism(model, request, report)
    return PivotSummary(
        free_dofs=report.free_dofs,
        min_pivot=report.min_pivot,
        node_id=None if report.dof is None else node_ids[report.dof.node],
        component=None if report.dof is None else report.dof.component,
        nullity=nullity,
        mechanism=mechanism,
    )


def _singular_failure(pivot: PivotSummary, config: SpatialAnalysisConfiguration) -> _Failure:
    """Ubicación en el participante de mayor participación (empate: el primero por
    etiqueta, T-017 D3-5); sin modo concluyente, la del pivote."""
    value = pivot.min_pivot if pivot.min_pivot is not None else 0.0
    node_id, component = pivot.node_id, pivot.component or ""
    if pivot.mechanism:
        largest = max(item.participation for item in pivot.mechanism)
        top = next(
            item
            for item in pivot.mechanism
            if item.participation >= largest - MECHANISM_TIE_TOLERANCE
        )
        node_id, component = top.node_id, top.component
    return _Failure(
        DiagnosticCode.ANALYSIS_SINGULAR,
        "fem.singular",
        {"pivot": value, "component": component, "tolerance": config.tol_pivot},
        None if node_id is None else Location(entity_id=node_id),
    )


def _raw_results(request: SpatialRequest, response: SpatialWorkerResponse) -> RawSpatialResults:
    """Salida bruta, tras comprobar que la respuesta corresponde a la petición."""
    node_ids = {node.tag: UUID(node.entity_id) for node in request.nodes}
    grounds = {spring.ground: spring.node for spring in request.springs}
    by_node = {tag: disp for tag, disp, _reaction in response.node_results}
    by_ground = dict(response.ground_reactions)
    by_beam = dict(response.beam_forces)
    chains = {item.tag for item in response.chain_results}
    if (
        set(by_node) != set(node_ids)
        or len(by_node) != len(response.node_results)
        or set(by_ground) != set(grounds)
        or len(by_ground) != len(response.ground_reactions)
        or chains != {chain.tag for chain in request.chains}
        or len(chains) != len(response.chain_results)
        or set(by_beam) != {beam.tag for beam in request.beams}
        or len(by_beam) != len(response.beam_forces)
    ):
        raise ProtocolError("la respuesta no corresponde a la petición")
    reactions = node_reactions(request, response)
    nodes = tuple(
        RawSpatialNodeResult(node_ids[node.tag], by_node[node.tag], reactions[node.tag])
        for node in request.nodes
    )
    ground_reactions = tuple(
        RawSpatialGroundReaction(node_ids[grounds[tag]], by_ground[tag]) for tag in sorted(grounds)
    )
    members = tuple(
        RawSpatialMemberForces(UUID(beam.entity_id), by_beam[beam.tag])  # type: ignore[arg-type]
        for beam in request.beams
    )
    return RawSpatialResults(RAW_SPATIAL_BASIS, nodes, ground_reactions, members)


def _check_failure(model: StructuralModel, failure: CheckFailure) -> _Failure:
    """Añade la etiqueta y la ubicación de la barra o del nudo del control."""
    params = dict(failure.params)
    location = None
    if failure.member_id is not None:
        member = model.member(UUID(failure.member_id))
        params["member"] = member.label or str(member.id)
        location = Location(entity_id=member.id, revision=member.revision)
    elif failure.node_id is not None:
        node = model.node(UUID(failure.node_id))
        label = node.label or str(node.id)
        if failure.key == "fem.residual_failed":
            params["subject"] = render_text("fem.subject_node", {"node": label})
        else:
            params["node"] = label
        location = Location(entity_id=node.id, revision=node.revision)
    return _Failure(DiagnosticCode.ANALYSIS_FAILED, failure.key, params, location)


def _response_failure(response: SpatialWorkerResponse) -> _Failure | None:
    """Estado declarado por el worker 3D, con las mismas claves que el plano."""
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


def _stderr_warning(outcome: WorkerOutcome) -> Diagnostic | None:
    """Una corrida limpia no escribe en stderr (medido): un aviso del solver se registra y
    los controles deciden (D2-A, revisión 6). En Linux, OpenSeesPy escribe siempre
    ``LINUX_EXIT_BANNER`` al cerrar (CI de E1): un stderr cuyas líneas no vacías son todas
    exactamente esa no es un aviso; cualquier otra línea sí."""
    text = outcome.stderr.strip()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if all(line == LINUX_EXIT_BANNER for line in lines):
        return None
    return Diagnostic.create(
        DiagnosticCode.ANALYSIS_FAILED,
        "fem3d.solver_stderr",
        {"text": text[-500:]},
        severity=Severity.WARNING,
    )


def analyze_spatial(  # noqa: PLR0911 (un retorno por causa de fallo)
    model: StructuralModel,
    config: SpatialAnalysisConfiguration,
    *,
    worker: Path = WORKER_3D_PATH,
) -> SpatialAnalysisRun:
    """Análisis estático lineal espacial de un caso (ADR-003). No modifica el modelo.

    ``worker`` solo se sustituye en pruebas.
    """
    run = _Run(model, config)
    # E2.2-d: el rechazo de E1 hasta que la salvaguarda (E2.3) y la normalización (E2.5)
    # admitan extremos no rígidos, en el mismo punto de la traducción que en E1.
    run.translation = translation = translate_spatial(model, config, e1_end_guard=True)
    request = translation.request
    run.case_revision = request.case_revision
    try:
        # Dentro del try: una petición mal formada es FAILED, no una excepción (revisión M2).
        if not reduced_coordinates(request):
            raise _no_free_dofs()
        # D6: el límite cuenta los GDL del modelo del solver, cadenas incluidas.
        count = solver_free_dofs(request)
        if count > config.max_free_dofs:
            raise _too_many_dofs(count, config.max_free_dofs)
        report = check_singularity(request, config.tol_pivot)
    except InvalidStiffnessError as error:
        return run.finish(
            _Failure(DiagnosticCode.ANALYSIS_FAILED, "fem.invalid_stiffness", {}, cause=str(error)),
            None,
        )
    run.pivot = _pivot_summary(model, request, report)
    if report.singular:
        mode = report.mode
        if mode is not None and not mode.conclusive:
            run.warnings.append(
                Diagnostic.create(
                    DiagnosticCode.ANALYSIS_SINGULAR,
                    "fem.mechanism_mode_inconclusive",
                    {
                        "residual": mode.residual,
                        "limit": MECHANISM_RESIDUAL_FACTOR * config.tol_pivot,
                    },
                    severity=Severity.WARNING,
                )
            )
        return run.finish(_singular_failure(run.pivot, config), None)
    run.outcome = outcome = run_worker(request.to_json(), config.timeout_s, worker)
    failure = _worker_failure(outcome, config.timeout_s)
    if failure is not None or outcome.response_text is None:
        return run.finish(failure, None)
    warning = _stderr_warning(outcome)
    if warning is not None:
        run.warnings.append(warning)
    try:
        response = SpatialWorkerResponse.from_json(outcome.response_text)
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
    equilibrium = equilibrium_residual(request, response, config)
    if not equilibrium.ok:
        return run.finish(
            _Failure(
                DiagnosticCode.ANALYSIS_FAILED,
                "fem.equilibrium_failed",
                {
                    "force": _relative(equilibrium.force, equilibrium.force_scale),
                    "moment": _relative(equilibrium.moment, equilibrium.moment_scale),
                    "tolerance": config.tol_equilibrium,
                },
            ),
            None,
        )
    check = solution_checks(request, response, config)
    if check is not None:
        return run.finish(_check_failure(model, check), None)
    return run.finish(None, raw)


def _relative(value: float, scale: float) -> float:
    return value / scale if scale else value
