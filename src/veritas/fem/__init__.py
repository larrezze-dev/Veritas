"""Adaptador FEM plano XZ con OpenSeesPy en proceso aislado (plan T-007; ADR-002).

``analyze_planar_xz`` traduce el modelo, aplica la salvaguarda de singularidad (D3),
ejecuta el worker en un proceso nuevo y comprueba el equilibrio y, desde T-017, los
controles de la solución de ``postconditions`` (D1 y D2). Devuelve un
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
    Severity,
)
from veritas.core.geometry.model import StructuralModel
from veritas.core.messages_es import render_text
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
    MECHANISM_GUARD_BAND,
    MECHANISM_PARTICIPATION,
    MECHANISM_RESIDUAL_FACTOR,
    DofLabel,
    InvalidStiffnessError,
    MechanismMode,
    PivotReport,
    beam_stiffness,
    check_singularity,
    element_displacement,
    reduced_coordinates,
)
from veritas.fem.config import AnalysisConfiguration
from veritas.fem.postconditions import (
    PENALTY_EQUILIBRIUM_FACTOR,
    CheckFailure,
    member_checks,
    residual_check,
    settlement_check,
)
from veritas.fem.results.normalized import (
    Component,
    ComponentStatus,
    MemberEndForces,
    NodeDisplacement,
    PlanarResults,
    ReactionSource,
    ResultBasis,
    ResultsNormalizationError,
    SupportReaction,
    Vector6,
    normalize_planar_xz,
)
from veritas.fem.results.run import (
    ADAPTER_ID,
    ADAPTER_VERSION,
    RAW_BASIS,
    AnalysisRun,
    MechanismDof,
    PivotSummary,
    RawEndRotation,
    RawMemberForces,
    RawNodeResult,
    RawPlanarResults,
    RawSpringReaction,
    RunStatus,
)
from veritas.fem.runners.subprocess_runner import WORKER_PATH, WorkerOutcome, run_worker

__all__ = [
    "MECHANISM_PARTICIPATION",
    "AnalysisConfiguration",
    "AnalysisRun",
    "Component",
    "ComponentStatus",
    "MechanismDof",
    "MemberEndForces",
    "NodeDisplacement",
    "PlanarResults",
    "ReactionSource",
    "ResultBasis",
    "ResultsNormalizationError",
    "RunStatus",
    "SupportReaction",
    "Vector6",
    "analyze_planar_xz",
    "normalize_planar_xz",
]

# PENALTY_EQUILIBRIUM_FACTOR (postconditions): constante c de la ampliación de los controles
# por redondeo de K·u, error hacia atrás de la solución directa (R2) y de la penalización
# (R1). Se registra en la procedencia.


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
        self.warnings: list[Diagnostic] = []
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
        diagnostics.extend(self.warnings)
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
                "mechanism_participation": MECHANISM_PARTICIPATION,
                "mechanism_guard_low": MECHANISM_GUARD_BAND[0],
                "mechanism_guard_high": MECHANISM_GUARD_BAND[1],
                "mechanism_residual_factor": MECHANISM_RESIDUAL_FACTOR,
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


# Diferencia de participación que se trata como empate al elegir la ubicación (D3-5): muy
# por debajo de la banda de guarda y por encima del redondeo de p_i.
MECHANISM_TIE_TOLERANCE: Final = 1e-9

# Componentes de la salvaguarda (base del solver) en base de dominio (T-017, D3-4).
DOMAIN_COMPONENT: Final = {"u1": "Ux", "u2": "Uz", "theta": "Ry", "t": "t"}
_COMPONENT_ORDER: Final = {"Ux": 0, "Uz": 1, "t": 2, "Ry": 3}


def _entity_label(model: StructuralModel, entity_id: UUID, member: bool) -> str:
    entity = model.member(entity_id) if member else model.node(entity_id)
    return entity.label or str(entity.id)


def _mechanism(
    model: StructuralModel,
    request: PlanarRequest,
    labels: tuple[DofLabel, ...],
    mode: MechanismMode,
) -> tuple[MechanismDof, ...]:
    """Participantes (p ≥ MECHANISM_PARTICIPATION) en orden determinista: etiqueta de la
    entidad (barra para un extremo, nudo si no), extremo y componente."""
    node_ids = {node.tag: UUID(node.entity_id) for node in request.nodes}
    links = {link.aux: link for link in request.end_links}
    items: list[MechanismDof] = []
    for label, participation in zip(labels, mode.participation, strict=True):
        if participation < MECHANISM_PARTICIPATION:
            continue
        component = DOMAIN_COMPONENT[label.component]
        link = links.get(label.node)
        if link is None:
            items.append(MechanismDof(node_ids[label.node], component, participation))
        else:
            items.append(
                MechanismDof(
                    node_ids[link.master],
                    component,
                    participation,
                    UUID(link.member_id),
                    link.end,
                )
            )

    def key(item: MechanismDof) -> tuple[str, str, str, int]:
        # Etiquetas repetidas: desempate por el id de la entidad (revisión E2, 8).
        if item.member_id is not None:
            label = _entity_label(model, item.member_id, True)
            return (label, str(item.member_id), item.end or "", 3)
        label = _entity_label(model, item.node_id, False)
        return (label, str(item.node_id), "", _COMPONENT_ORDER[item.component])

    return tuple(sorted(items, key=key))


def _pivot_summary(
    model: StructuralModel, request: PlanarRequest, report: PivotReport
) -> PivotSummary:
    node_ids = {node.tag: UUID(node.entity_id) for node in request.nodes}
    links = {link.aux: link for link in request.end_links}
    nullity: int | None = None
    mechanism: tuple[MechanismDof, ...] = ()
    if report.mode is not None and report.mode.conclusive:
        nullity = report.mode.nullity
        mechanism = _mechanism(model, request, report.labels, report.mode)
    if report.dof is not None and report.dof.node in links:
        link = links[report.dof.node]
        return PivotSummary(
            free_dofs=report.free_dofs,
            min_pivot=report.min_pivot,
            node_id=node_ids[link.master],
            component=report.dof.component,
            member_id=UUID(link.member_id),
            end=link.end,
            nullity=nullity,
            mechanism=mechanism,
        )
    return PivotSummary(
        free_dofs=report.free_dofs,
        min_pivot=report.min_pivot,
        node_id=None if report.dof is None else node_ids[report.dof.node],
        component=None if report.dof is None else report.dof.component,
        nullity=nullity,
        mechanism=mechanism,
    )


def _singular_failure(
    model: StructuralModel, pivot: PivotSummary, config: AnalysisConfiguration
) -> _Failure:
    """Clave y ubicación del mecanismo (T-017, D3-5); sin modo, las del pivote (T-010)."""
    value = pivot.min_pivot if pivot.min_pivot is not None else 0.0
    ends = [item for item in pivot.mechanism if item.member_id is not None]
    member_id, end = (ends[0].member_id, ends[0].end) if ends else (None, None)
    if not pivot.mechanism:
        member_id, end = pivot.member_id, pivot.end
    if member_id is not None:
        member = model.member(member_id)
        return _Failure(
            DiagnosticCode.ANALYSIS_SINGULAR,
            "fem.mechanism_member_end",
            {
                "member": member.label or str(member.id),
                "end": end or "",
                "pivot": value,
                "tolerance": config.tol_pivot,
            },
            Location(entity_id=member.id, revision=member.revision),
        )
    # Componente siempre en base de dominio, también al volver al pivote (usuario, E2).
    node_id = pivot.node_id
    component = DOMAIN_COMPONENT.get(pivot.component or "", pivot.component or "")
    if pivot.mechanism:
        largest = max(item.participation for item in pivot.mechanism)
        # Empate (D3-5) con tolerancia: participaciones iguales en aritmética exacta difieren
        # en redondeo según el orden de tags (revisión E2, 1). Gana el primero por etiqueta.
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


def _fixed_reactions(
    request: PlanarRequest, response: WorkerResponse
) -> dict[int, tuple[float | None, float | None, float | None]]:
    """Reacción por nudo en sus GDL con ``fix`` (T-010, D2; revisión M3).

    En traslación se suma la de los aux de extremo de ese nudo: con Transformation, la
    fuerza transmitida por ``equalDOF`` aparece en ``nodeReaction(aux)``. En un GDL sin
    ``fix`` esa fuerza es interna y no se suma. El giro del aux nunca se suma: el momento
    de un resorte de extremo ya llega al maestro por el ``zeroLength``.
    """
    fixity = {fix.node: fix.fixity for fix in request.fixes}
    extra: dict[int, list[float]] = {}
    aux_reaction = {tag: reaction for tag, _disp, reaction in response.end_aux}
    for link in request.end_links:
        entry = extra.setdefault(link.master, [0.0, 0.0])
        entry[0] += aux_reaction[link.aux][0]
        entry[1] += aux_reaction[link.aux][1]
    result: dict[int, tuple[float | None, float | None, float | None]] = {}
    for tag, _disp, reaction in response.node_results:
        fixed = fixity.get(tag, (0, 0, 0))
        add = extra.get(tag, [0.0, 0.0])
        result[tag] = (
            reaction[0] + add[0] if fixed[0] else None,
            reaction[1] + add[1] if fixed[1] else None,
            reaction[2] if fixed[2] else None,
        )
    return result


def _raw_results(request: PlanarRequest, response: WorkerResponse) -> RawPlanarResults:
    node_ids = {node.tag: UUID(node.entity_id) for node in request.nodes}
    by_node = {tag: disp for tag, disp, _reaction in response.node_results}
    by_aux = dict(response.aux_reactions)
    by_beam = dict(response.beam_forces)
    by_end = {tag: disp for tag, disp, _reaction in response.end_aux}
    if (
        set(by_node) != set(node_ids)
        or set(by_aux) != {s.aux_node for s in request.springs}
        or set(by_beam) != {b.tag for b in request.beams}
        or set(by_end) != {link.aux for link in request.end_links}
        or len(by_end) != len(response.end_aux)
    ):
        raise ProtocolError("la respuesta no corresponde a la petición")
    reactions = _fixed_reactions(request, response)
    nodes = [
        RawNodeResult(
            node_ids[node.tag],
            (by_node[node.tag][0], by_node[node.tag][1], by_node[node.tag][2]),
            reactions[node.tag],
        )
        for node in request.nodes
    ]
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
    rotations = tuple(
        RawEndRotation(UUID(link.member_id), link.end, by_end[link.aux][2])
        for link in request.end_links
    )
    return RawPlanarResults(RAW_BASIS, tuple(nodes), springs, members, rotations)


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
    # Cargas equivalentes de barra (T-015): el aux está en las coordenadas de su maestro.
    master = {link.aux: link.master for link in request.end_links}
    for end_load in request.member_end_loads:
        point = coords[master.get(end_load.node, end_load.node)]
        forces.append((*point, end_load.p1, end_load.p2, end_load.m))
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
    # propia solución e incluirlo haría el control tautológico. Incluye los aux de extremo.
    for tag, reaction in _fixed_reactions(request, response).items():
        x1, x2 = coords[tag]
        r1, r2, r3 = (0.0 if value is None else value for value in reaction)
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
    displacement |= {tag: disp for tag, disp, _reaction in response.end_aux}
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
        u = element_displacement(request, beam, displacement)
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
    for link in request.end_links:
        if link.stiffness is not None:
            # Σ|k_ij·u_j| de las dos filas de k·[[1, −1], [−1, 1]] (revisión E2, hallazgo 1).
            rotations = abs(displacement[link.master][2]) + abs(displacement[link.aux][2])
            moment += 2.0 * link.stiffness * rotations
    scale = PENALTY_EQUILIBRIUM_FACTOR * sys.float_info.epsilon
    return scale * force, scale * moment


def _check_failure(model: StructuralModel, failure: CheckFailure) -> _Failure:
    """Controles de T-017 (D1, D2): añade la etiqueta y la ubicación de la entidad."""
    params = dict(failure.params)
    location = None
    if failure.member_id is not None:
        member = model.member(UUID(failure.member_id))
        params["member"] = member.label or str(member.id)
        location = Location(entity_id=member.id, revision=member.revision)
    if failure.key == "fem.settlement_mismatch" and failure.node_id is not None:
        node = model.node(UUID(failure.node_id))
        params["node"] = node.label or str(node.id)
        location = Location(entity_id=node.id, revision=node.revision)
    if failure.key == "fem.residual_failed":
        if failure.member_id is not None:
            params["subject"] = render_text(
                "fem.subject_member_end", {"end": params.pop("end"), "member": params.pop("member")}
            )
        elif failure.node_id is not None:
            node = model.node(UUID(failure.node_id))
            params["subject"] = render_text(
                "fem.subject_node", {"node": node.label or str(node.id)}
            )
            location = Location(entity_id=node.id, revision=node.revision)
    return _Failure(DiagnosticCode.ANALYSIS_FAILED, failure.key, params, location)


def _solution_failure(
    model: StructuralModel,
    request: PlanarRequest,
    response: WorkerResponse,
    config: AnalysisConfiguration,
) -> _Failure | None:
    """Controles de la solución en orden: asiento (T-016, D7; sin asientos no evalúa nada),
    equilibrio, esfuerzos por barra y residuo (T-017)."""
    check = settlement_check(request, response)
    if check is not None:
        return _check_failure(model, check)
    ok, force_residual, moment_residual = equilibrium_residual(request, response, config)
    if not ok:
        return _Failure(
            DiagnosticCode.ANALYSIS_FAILED,
            "fem.equilibrium_failed",
            {
                "force": force_residual,
                "moment": moment_residual,
                "tolerance": config.tol_equilibrium,
            },
        )
    check = member_checks(request, response, config) or residual_check(request, response, config)
    return None if check is None else _check_failure(model, check)


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


def _no_free_dofs() -> InputValueError:
    # Plan T-009, D3: con K reducida vacía el worker caería en LAPACK sin diagnóstico útil.
    return InputValueError(
        Diagnostic.create(DiagnosticCode.UNSUPPORTED_CONFIGURATION, "fem.no_free_dofs", {})
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
    if translation.orphan_loaded:
        nodes = [model.node(node_id) for node_id in translation.orphan_loaded]
        node = nodes[0]
        return run.finish(
            _Failure(
                DiagnosticCode.ANALYSIS_SINGULAR,
                "fem.mechanism_orphan_rotation",
                {"node": ", ".join(n.label or str(n.id) for n in nodes)},
                Location(entity_id=node.id, revision=node.revision),
            ),
            None,
        )
    free = len(reduced_coordinates(request))
    if free == 0:
        raise _no_free_dofs()
    if free > config.max_free_dofs:
        raise _too_many_dofs(free, config.max_free_dofs)
    try:
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
        return run.finish(_singular_failure(model, run.pivot, config), None)
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
    return run.finish(_solution_failure(model, request, response, config), raw)
