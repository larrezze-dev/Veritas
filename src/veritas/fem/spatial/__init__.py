"""Adaptador FEM espacial de seis GDL por nudo (ADR-003, plan T-018).

``analyze_spatial`` traduce el modelo, aplica la salvaguarda de singularidad con su modo,
ejecuta el worker 3D en un proceso nuevo y comprueba el equilibrio y los controles de
``spatial.postconditions``. Devuelve un ``SpatialAnalysisRun``: un fallo, una singularidad
o un tiempo agotado dan ``FAILED`` sin resultados. Una entrada no soportada se rechaza
antes con ``InputValueError``. Los resultados son BRUTOS; ``normalize_spatial`` los
publica con los ejes y signos de ADR-001.
"""

import time
from collections.abc import Sequence
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
    REFERENCE_MARGIN,
    equilibrium_residual,
    node_reactions,
    settlement_check,
    solution_checks,
)
from veritas.fem.spatial.protocol import (
    PROTOCOL_VERSION,
    STOP_STAGNATION,
    STOP_TOLERANCE,
    SpatialRequest,
    SpatialWorkerResponse,
    penalty_step,
)
from veritas.fem.spatial.results import (
    RAW_SPATIAL_BASIS,
    SPATIAL_ADAPTER_ID,
    SPATIAL_ADAPTER_VERSION,
    RawSpatialEndAux,
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
    SpatialDof,
    SpatialPivotReport,
    check_singularity,
    reduced_coordinates,
    solver_free_dofs,
    validate_request,
)
from veritas.fem.spatial.translator import (
    TOL_AXIAL_LOAD,
    SpatialTranslation,
    orphan_rotations,
    translate_spatial,
)

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


# Componentes de giro de un nudo de dominio en el modo (globales u oblicuas).
ROTATION_COMPONENTS: Final = frozenset((*GLOBAL_COMPONENTS[3:], "r1", "r2"))


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
        # Parada del worker y fiabilidad del contraste (ADR-004 §3-4; E2.5-e).
        "end_penalty_min_stagnation_iteration": opensees_worker_3d.MIN_STAGNATION_ITERATION,
        "end_penalty_stagnation_ratio": opensees_worker_3d.STAGNATION_IMPROVEMENT,
        "end_contrast_reference_margin": REFERENCE_MARGIN,
        # Rechazo de Wx ≠ 0 con Ux de extremo no rígido (E2-d; revisión de E2, m2).
        "end_axial_load_tolerance": TOL_AXIAL_LOAD,
    }


def _dof(request: SpatialRequest, label: SpatialDof, participation: float) -> MechanismDof:
    """GDL de dominio; una coordenada propia de aux es el extremo de su barra (E2-f)."""
    node_id = UUID(next(node.entity_id for node in request.nodes if node.tag == label.node))
    if label.member is None:
        return MechanismDof(node_id, label.component, participation)
    member = next(beam for beam in request.beams if beam.tag == label.member)
    return MechanismDof(node_id, label.component, participation, UUID(member.entity_id), label.end)


def _mechanism(
    model: StructuralModel, request: SpatialRequest, report: SpatialPivotReport
) -> tuple[MechanismDof, ...]:
    """Participantes (p ≥ MECHANISM_PARTICIPATION) en orden determinista: etiqueta de la
    entidad (barra para un extremo, nudo si no), id, extremo y componente, como el plano."""
    assert report.mode is not None
    items = [
        _dof(request, label, participation)
        for label, participation in zip(report.labels, report.mode.participation, strict=True)
        if participation >= MECHANISM_PARTICIPATION
    ]

    def key(item: MechanismDof) -> tuple[str, str, str, int]:
        if item.member_id is not None:
            label = _entity_label(model, item.member_id, True)
            return (label, str(item.member_id), item.end or "", _COMPONENT_ORDER[item.component])
        label = _entity_label(model, item.node_id, False)
        return (label, str(item.node_id), "", _COMPONENT_ORDER[item.component])

    return tuple(sorted(items, key=key))


def _pivot_summary(
    model: StructuralModel, request: SpatialRequest, report: SpatialPivotReport
) -> PivotSummary:
    nullity: int | None = None
    mechanism: tuple[MechanismDof, ...] = ()
    if report.mode is not None and report.mode.conclusive:
        nullity = report.mode.nullity
        mechanism = _mechanism(model, request, report)
    pivot = None if report.dof is None else _dof(request, report.dof, 0.0)
    return PivotSummary(
        free_dofs=report.free_dofs,
        min_pivot=report.min_pivot,
        node_id=None if pivot is None else pivot.node_id,
        component=None if pivot is None else pivot.component,
        member_id=None if pivot is None else pivot.member_id,
        end=None if pivot is None else pivot.end,
        nullity=nullity,
        mechanism=mechanism,
    )


def _principal(mechanism: Sequence[MechanismDof]) -> MechanismDof:
    """Participante principal del modo: el de mayor participación; en empate, el primero
    por etiqueta (T-017 D3-5)."""
    largest = max(item.participation for item in mechanism)
    return next(
        item for item in mechanism if item.participation >= largest - MECHANISM_TIE_TOLERANCE
    )


def _singular_failure(
    model: StructuralModel, pivot: PivotSummary, config: SpatialAnalysisConfiguration
) -> _Failure:
    """Ubicación en el participante de mayor participación (empate: el primero por
    etiqueta, T-017 D3-5); sin modo concluyente, la del pivote. Si es una coordenada propia
    de un aux, ``fem3d.singular_member_end`` con la barra y el extremo (E2.5-d)."""
    value = pivot.min_pivot if pivot.min_pivot is not None else 0.0
    node_id, component = pivot.node_id, pivot.component or ""
    member_id, end = pivot.member_id, pivot.end
    if pivot.mechanism:
        top = _principal(pivot.mechanism)
        node_id, component = top.node_id, top.component
        member_id, end = top.member_id, top.end
    if member_id is not None:
        member = model.member(member_id)
        return _Failure(
            DiagnosticCode.ANALYSIS_SINGULAR,
            "fem3d.singular_member_end",
            {
                "pivot": value,
                "component": component,
                "member": member.label or str(member.id),
                "end": end or "",
                "tolerance": config.tol_pivot,
            },
            Location(entity_id=member.id, revision=member.revision),
        )
    return _Failure(
        DiagnosticCode.ANALYSIS_SINGULAR,
        "fem.singular",
        {"pivot": value, "component": component, "tolerance": config.tol_pivot},
        None if node_id is None else Location(entity_id=node_id),
    )


def _orphan_failure(model: StructuralModel, key: str, node_ids: Sequence[UUID]) -> _Failure:
    """Mecanismo por giros huérfanos (E2.3-b): nudos por etiqueta, ubicación en el primero."""
    nodes = [model.node(node_id) for node_id in node_ids]
    return _Failure(
        DiagnosticCode.ANALYSIS_SINGULAR,
        key,
        {"node": ", ".join(n.label or str(n.id) for n in nodes)},
        Location(entity_id=nodes[0].id, revision=nodes[0].revision),
    )


def _singular_outcome(
    run: _Run,
    model: StructuralModel,
    request: SpatialRequest,
    report: SpatialPivotReport,
    config: SpatialAnalysisConfiguration,
) -> _Failure:
    """Diagnóstico de una salvaguarda singular: aviso de modo no concluyente y, si hay giros
    huérfanos oblicuos, su clave propia (E2.3-b) en lugar de ``fem.singular``."""
    assert run.pivot is not None
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
    oblique = [o.node for o in orphan_rotations(request, config.tol_axis) if o.axes is None]
    if oblique and run.pivot.mechanism:
        # E2.3-b: la clave propia solo si el participante principal del modo es un giro de
        # un nudo con huérfano oblicuo; si no, otra causa domina (revisión de E2.3, M3).
        node_ids = {node.tag: UUID(node.entity_id) for node in request.nodes}
        orphans = [node_ids[tag] for tag in oblique]
        # Mismo participante principal que ``_singular_failure``.
        top = _principal(run.pivot.mechanism)
        if (
            top.member_id is None
            and top.component in ROTATION_COMPONENTS
            and top.node_id in orphans
        ):
            return _orphan_failure(model, "fem3d.orphan_rotation_oblique", orphans)
    return _singular_failure(model, run.pivot, config)


def _raw_results(request: SpatialRequest, response: SpatialWorkerResponse) -> RawSpatialResults:
    """Salida bruta, tras comprobar que la respuesta corresponde a la petición."""
    node_ids = {node.tag: UUID(node.entity_id) for node in request.nodes}
    grounds = {spring.ground: spring.node for spring in request.springs}
    by_node = {tag: disp for tag, disp, _reaction in response.node_results}
    by_ground = dict(response.ground_reactions)
    by_beam = dict(response.beam_forces)
    chains = {item.tag for item in response.chain_results}
    auxes = {tag for tag, _disp, _reaction in response.aux_results}
    if (
        auxes != {aux.tag for aux in request.aux_nodes}
        or len(auxes) != len(response.aux_results)
        or set(by_node) != set(node_ids)
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
    aux_displacement = {tag: disp for tag, disp, _reaction in response.aux_results}
    beam_ids = {beam.tag: UUID(beam.entity_id) for beam in request.beams}
    end_aux = tuple(
        RawSpatialEndAux(beam_ids[aux.beam], aux.end, aux_displacement[aux.tag])
        for aux in request.aux_nodes
    )
    return RawSpatialResults(RAW_SPATIAL_BASIS, nodes, ground_reactions, members, end_aux)


# Controles cuyo texto nombra un sujeto (nudo o barra) en lugar de un nudo.
_SUBJECT_KEYS: Final = frozenset({"fem.residual_failed", "fem3d.end_penalty_contrast_failed"})


def _check_failure(model: StructuralModel, failure: CheckFailure) -> _Failure:
    """Añade la etiqueta y la ubicación de la barra o del nudo del control."""
    params = dict(failure.params)
    location = None
    if failure.member_id is not None:
        member = model.member(UUID(failure.member_id))
        params["member"] = member.label or str(member.id)
        if failure.key in _SUBJECT_KEYS:
            params["subject"] = render_text("fem3d.subject_member", {"member": params["member"]})
        location = Location(entity_id=member.id, revision=member.revision)
    elif failure.node_id is not None:
        node = model.node(UUID(failure.node_id))
        label = node.label or str(node.id)
        if failure.key in _SUBJECT_KEYS:
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


def _end_penalty_step(request: SpatialRequest, response: SpatialWorkerResponse) -> float:
    """P = máx_s |k_s·g_s| / S_s con la ``basicForce`` devuelta (ADR-004 §3)."""
    forces = {tag: force for tag, force, _deformation, _lam in response.end_spring_results}
    if set(forces) != {spring.tag for spring in request.end_springs} or len(forces) != len(
        response.end_spring_results
    ):
        raise ProtocolError("la respuesta no corresponde a la petición")
    return max(
        (penalty_step(forces[item.spring], item.scale) for item in request.end_penalties),
        default=0.0,
    )


def _end_penalty_failure(
    request: SpatialRequest, response: SpatialWorkerResponse, step: float
) -> _Failure | None:
    """Recomprobación de la penalización iterada en el padre (ADR-004 §3, defensa en
    profundidad): el motivo declarado debe cumplirse con el paso P recalculado."""
    iterations, stop = response.end_penalty_iterations, response.end_penalty_stop
    if not request.end_penalties:
        if iterations != 0:
            raise ProtocolError("iteraciones de extremo sin penalización de extremo")
        return None
    bound = {
        STOP_TOLERANCE: request.end_penalty_tolerance,
        STOP_STAGNATION: request.end_penalty_stagnation,
    }.get(stop)
    if (
        response.end_penalty_converged
        and bound is not None
        and step <= bound
        and iterations <= request.end_penalty_max_iterations
        and (stop != STOP_STAGNATION or iterations >= opensees_worker_3d.MIN_STAGNATION_ITERATION)
    ):
        return None
    return _Failure(
        DiagnosticCode.ANALYSIS_NONCONVERGENCE,
        "fem3d.end_penalty_not_converged",
        {
            "iterations": iterations,
            "stop": stop,
            "step": step,
            "bound": request.end_penalty_tolerance if bound is None else bound,
        },
    )


def _end_penalty_diagnostic(response: SpatialWorkerResponse, step: float) -> Diagnostic:
    """Iteraciones y motivo de parada (ADR-004 §8): informativo, no cambia el estado."""
    return Diagnostic.create(
        DiagnosticCode.ANALYSIS_NONCONVERGENCE,
        "fem3d.end_penalty_iterations",
        {
            "iterations": response.end_penalty_iterations,
            "stop": response.end_penalty_stop,
            "step": step,
        },
        severity=Severity.WARNING,
    )


def _end_penalty_outcome(
    run: _Run, request: SpatialRequest, response: SpatialWorkerResponse
) -> _Failure | None:
    """Recomprobación de la penalización iterada y, si converge, su diagnóstico."""
    step = _end_penalty_step(request, response)
    failure = _end_penalty_failure(request, response, step)
    if failure is None and request.end_penalties:
        run.warnings.append(_end_penalty_diagnostic(response, step))
    return failure


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
    run.translation = translation = translate_spatial(model, config)
    request = translation.request
    run.case_revision = request.case_revision
    if translation.orphan_loaded:  # E2.3-b: como el plano, antes de la salvaguarda y sin modo.
        return run.finish(
            _orphan_failure(model, "fem3d.mechanism_orphan_rotation", translation.orphan_loaded),
            None,
        )
    try:
        # Dentro del try: una petición mal formada es FAILED, no una excepción (revisión M2).
        # La validación va primero: una referencia de aux rota no llega a las coordenadas.
        validate_request(request)
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
        return run.finish(_singular_outcome(run, model, request, report, config), None)
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
        failure = _response_failure(response) or _end_penalty_outcome(run, request, response)
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
    return run.finish(_solution_failure(model, request, response, config, report), raw)


def _solution_failure(
    model: StructuralModel,
    request: SpatialRequest,
    response: SpatialWorkerResponse,
    config: SpatialAnalysisConfiguration,
    report: SpatialPivotReport,
) -> _Failure | None:
    """Controles de la solución en orden: asiento (T-016, D7; sin asientos no evalúa nada),
    equilibrio y ``solution_checks`` (T-018)."""
    check = settlement_check(request, response)
    if check is not None:
        return _check_failure(model, check)
    equilibrium = equilibrium_residual(request, response, config)
    if not equilibrium.ok:
        return _Failure(
            DiagnosticCode.ANALYSIS_FAILED,
            "fem.equilibrium_failed",
            {
                "force": _relative(equilibrium.force, equilibrium.force_scale),
                "moment": _relative(equilibrium.moment, equilibrium.moment_scale),
                "tolerance": config.tol_equilibrium,
            },
        )
    check = solution_checks(request, response, config, report)
    return None if check is None else _check_failure(model, check)


def _relative(value: float, scale: float) -> float:
    return value / scale if scale else value
