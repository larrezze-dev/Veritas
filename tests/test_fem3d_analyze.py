"""Orquestación de la corrida espacial ``analyze_spatial`` (plan T-018, pieza 5a).

Corridas reales de extremo a extremo y workers envoltorio que ejecutan el worker 3D real y
después tocan su respuesta o su stderr: el padre debe dar FAILED con la clave del control,
o un aviso registrado. La salvaguarda y los límites actúan antes de lanzar el worker.
"""

import dataclasses
import textwrap
from collections.abc import Callable
from pathlib import Path

import pytest

import test_fem3d_singularity as cases
from fem_support import Frame
from veritas.core.diagnostics import DiagnosticCode, InputValueError, Severity
from veritas.core.geometry.entities import Node
from veritas.core.messages_es import render_message
from veritas.core.supports.support import build_restraints, fixed_restraints, pinned_restraints
from veritas.core.units import Quantity
from veritas.fem import spatial
from veritas.fem.adapters import singularity
from veritas.fem.results.run import RunStatus
from veritas.fem.spatial import (
    LINUX_EXIT_BANNER,
    WORKER_3D_PATH,
    analyze_spatial,
    provenance_constants,
)
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.postconditions import CONTROL_CHAIN_SUPPORT_MOMENT
from veritas.fem.spatial.results import (
    RAW_SPATIAL_BASIS,
    SPATIAL_ADAPTER_ID,
    SPATIAL_ADAPTER_VERSION,
    SpatialAnalysisRun,
)
from veritas.fem.spatial.stiffness import solver_free_dofs
from veritas.fem.spatial.translator import translate_spatial

pytestmark = pytest.mark.solver


def wrapper(tmp_path: Path, before: str = "", after: str = "") -> Path:
    """Worker que ejecuta el worker 3D real; ``after`` edita ``data`` (la respuesta)."""
    script = tmp_path / "wrapped_worker.py"
    body = f"""
        import json, runpy, sys
        path = sys.argv[1]
        {before}
        sys.argv = [{str(WORKER_3D_PATH)!r}, path]
        try:
            runpy.run_path({str(WORKER_3D_PATH)!r}, run_name="__main__")
        except SystemExit:
            pass
        with open(path, encoding="utf-8") as stream:
            data = json.load(stream)
        {after}
        with open(path, "w", encoding="utf-8") as stream:
            json.dump(data, stream)
        """
    script.write_text(textwrap.dedent(body), encoding="utf-8")
    return script


def analyze(frame: Frame, worker: Path = WORKER_3D_PATH, **config: float) -> SpatialAnalysisRun:
    revision = frame.model.model_revision
    run = analyze_spatial(
        frame.model,
        SpatialAnalysisConfiguration(frame.case_id, **config),  # type: ignore[arg-type]
        worker=worker,
    )
    assert frame.model.model_revision == revision  # El modelo no cambia.
    return run


def assert_failed(run: SpatialAnalysisRun, code: DiagnosticCode, key: str) -> None:
    assert run.status is RunStatus.FAILED
    assert run.raw is None
    assert run.diagnostics[0].code is code
    assert run.diagnostics[0].message_key == key
    render_message(run.diagnostics[0])  # La clave existe y sus parámetros bastan.


# --- Corridas correctas -------------------------------------------------------------------


def test_successful_run_has_raw_results_and_provenance() -> None:
    frame = cases._spring_tip()
    run = analyze(frame)
    assert run.status is RunStatus.SUCCEEDED, [d.message_key for d in run.diagnostics]
    assert run.diagnostics == ()
    raw = run.raw
    assert raw is not None
    request = translate_spatial(frame.model, SpatialAnalysisConfiguration(frame.case_id)).request
    assert run.request_json == request.to_json()
    assert raw.basis == RAW_SPATIAL_BASIS
    assert [str(n.node_id) for n in raw.nodes] == [n.entity_id for n in request.nodes]
    assert [str(m.member_id) for m in raw.members] == [b.entity_id for b in request.beams]
    assert len(raw.grounds) == 1  # Una tierra para los resortes del extremo.
    assert raw.nodes[0].reaction is not None  # Raíz empotrada.
    assert run.versions["adapter"] == f"{SPATIAL_ADAPTER_ID}/{SPATIAL_ADAPTER_VERSION}"
    assert run.versions["openseespy"] is not None
    for key, value in provenance_constants().items():
        assert run.configuration[key] == value
    assert run.configuration["tol_penalty"] == 1e-6
    assert run.pivot is not None
    assert run.pivot.min_pivot is not None
    assert run.pivot.nullity is None
    assert (
        run.hypotheses
        == translate_spatial(frame.model, SpatialAnalysisConfiguration(frame.case_id)).hypotheses
    )
    assert run.worker_stderr.strip() in ("", LINUX_EXIT_BANNER)  # Bruto, en la procedencia.


@pytest.mark.parametrize(
    ("name", "build"),
    [*cases.CONTRAST, *cases.PENALTY],
    ids=[name for name, _ in (*cases.CONTRAST, *cases.PENALTY)],
)
def test_battery_succeeds(name: str, build: object) -> None:
    run = analyze(build())  # type: ignore[operator]
    assert run.status is RunStatus.SUCCEEDED, (name, [d.message_key for d in run.diagnostics])


def test_solver_stderr_is_recorded_as_warning(tmp_path: Path) -> None:
    run = analyze(
        cases._loaded_cantilever(cases.OBLIQUE),
        wrapper(tmp_path, before="sys.stderr.write('WARNING aviso de prueba\\n')"),
    )
    assert run.status is RunStatus.SUCCEEDED
    assert "aviso de prueba" in run.worker_stderr
    (warning,) = run.diagnostics
    assert warning.severity is Severity.WARNING
    assert warning.message_key == "fem3d.solver_stderr"
    render_message(warning)


@pytest.mark.parametrize(
    ("text", "warned"),
    [
        ("Process 0 Terminating\n", False),
        ("Process 0 Terminating\n\nProcess 0 Terminating\n", False),
        ("WARNING aviso de prueba\nProcess 0 Terminating\n", True),
        ("Process 0 Terminating otra cosa\n", True),
    ],
    ids=["solo-cierre-linux", "cierre-repetido", "cierre-y-aviso", "linea-distinta"],
)
def test_linux_exit_banner_alone_is_not_a_warning(tmp_path: Path, text: str, warned: bool) -> None:
    """CI de E1: OpenSeesPy en Linux escribe «Process 0 Terminating» al cerrar (en Linux el
    worker real la añade otra vez). Solo esa línea exacta no es un aviso; cualquier otra sí."""
    run = analyze(
        cases._loaded_cantilever(cases.OBLIQUE),
        wrapper(tmp_path, after=f"sys.stderr.write({text!r})"),
    )
    assert run.status is RunStatus.SUCCEEDED
    keys = [d.message_key for d in run.diagnostics]
    assert keys == (["fem3d.solver_stderr"] if warned else [])


# --- Fallos antes del solver --------------------------------------------------------------


def test_mechanism_is_singular_with_mode_and_never_launches_worker(tmp_path: Path) -> None:
    marker = tmp_path / "launched"
    worker = wrapper(tmp_path, before=f"open({str(marker)!r}, 'w').write('x')")
    run = analyze(cases._tangent_slide(), worker)
    assert_failed(run, DiagnosticCode.ANALYSIS_SINGULAR, "fem.singular")
    assert not marker.exists()
    assert run.pivot is not None
    assert run.pivot.nullity is not None
    assert run.pivot.mechanism
    # O3: las coordenadas oblicuas se publican como t1/t2 (o r1/r2).
    components = {item.component for item in run.pivot.mechanism}
    assert components & {"t1", "t2"}, components
    assert run.diagnostics[0].params_dict()["component"] in components


def test_torsion_mechanism_points_to_rx() -> None:
    run = analyze(cases._simply_supported())
    assert_failed(run, DiagnosticCode.ANALYSIS_SINGULAR, "fem.singular")
    assert run.pivot is not None
    assert {item.component for item in run.pivot.mechanism} == {"Rx"}
    assert run.diagnostics[0].params_dict()["component"] == "Rx"


def labelled(frame: Frame, label: str, x: float, z: float, y: float = 0.0) -> Node:
    m = Quantity.of
    return frame.model.add_node(m(x, "m"), m(y, "m"), m(z, "m"), label=label)


def labelled_portal() -> Frame:
    """``_portal_overturning`` con etiquetas: vuelco fuera del plano (Uy, Rx)."""
    frame = Frame()
    a, b = labelled(frame, "A", 0.0, 0.0), labelled(frame, "B", 0.0, 3.0)
    c, d = labelled(frame, "C", 4.0, 3.0), labelled(frame, "D", 4.0, 0.0)
    frame.member(a, b)
    frame.member(b, c)
    frame.member(d, c)
    frame.support(a, pinned_restraints())
    frame.support(d, pinned_restraints())
    frame.nodal(b, fx=1.0)
    return frame


def labelled_simply_supported() -> Frame:
    """``_simply_supported`` con etiquetas: Rx de A y B con participación empatada (0,5)."""
    frame = Frame()
    b, a = labelled(frame, "B", 5.0, 0.0), labelled(frame, "A", 0.0, 0.0)
    frame.member(a, b)
    frame.support(a, pinned_restraints())
    frame.support(b, pinned_restraints())
    frame.nodal(b, fz=1.0)
    return frame


@pytest.mark.parametrize(
    ("build", "location"),
    [(labelled_portal, None), (labelled_simply_supported, ("A", "Rx"))],
    ids=["vuelco", "empate"],
)
def test_mechanism_is_reproducible_with_new_uuids(
    build: Callable[[], Frame], location: tuple[str, str] | None
) -> None:
    """Revisión de E1, M2 (como T-017): 20 reconstrucciones con UUID nuevos dan el mismo modo
    (etiqueta, componente, participación) y la misma ubicación de ``fem.singular``. En el
    empate gana la primera etiqueta (T-017 D3-5), no el primer UUID."""
    seen = set()
    for _ in range(20):
        frame = build()
        run = analyze(frame)
        assert_failed(run, DiagnosticCode.ANALYSIS_SINGULAR, "fem.singular")
        assert run.pivot is not None
        assert run.pivot.mechanism
        mode = tuple(
            (frame.model.node(m.node_id).label, m.component, round(m.participation, 12))
            for m in run.pivot.mechanism
        )
        error = run.diagnostics[0]
        assert error.location is not None
        assert error.location.entity_id is not None
        node = frame.model.node(error.location.entity_id)
        seen.add((mode, (node.label, error.params_dict()["component"])))
    assert len(seen) == 1, seen
    ((mode, where),) = seen
    labels = [label for label, _component, _p in mode]
    assert labels == sorted(labels)
    if location is not None:
        assert where == location
        assert {p for _l, _c, p in mode} == {0.5}


def test_too_many_dofs_counts_solver_dofs() -> None:
    """D6: el límite cuenta los GDL del solver, con 9 por cadena (aclaración de la pieza 4)."""
    frame = cases._roller_frame(2.0)
    request = translate_spatial(frame.model, SpatialAnalysisConfiguration(frame.case_id)).request
    count = solver_free_dofs(request)
    assert count == 6 + 9
    with pytest.raises(InputValueError) as error:
        analyze(frame, max_free_dofs=count - 1)
    assert error.value.diagnostic.code is DiagnosticCode.UNSUPPORTED_CONFIGURATION
    assert error.value.diagnostic.message_key == "fem.too_many_dofs"
    assert error.value.diagnostic.params_dict()["count"] == count
    assert analyze(frame, max_free_dofs=count).status is RunStatus.SUCCEEDED


def test_structure_without_free_dofs_is_rejected() -> None:
    frame = Frame()
    a, b = frame.node(0.0, 0.0), frame.node(2.0, 0.0)
    frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.nodal(b, fz=-1000.0)
    with pytest.raises(InputValueError) as error:
        analyze(frame)
    assert error.value.diagnostic.message_key == "fem.no_free_dofs"


def test_inconclusive_mode_falls_back_to_pivot_with_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Revisión de E1, M1: modo no concluyente → aviso, sin modo, ubicación del pivote."""
    monkeypatch.setattr(singularity, "MECHANISM_GUARD_BAND", (0.0, 1.0))
    run = analyze(cases._simply_supported())
    assert_failed(run, DiagnosticCode.ANALYSIS_SINGULAR, "fem.singular")
    assert run.pivot is not None
    assert run.pivot.nullity is None
    assert run.pivot.mechanism == ()
    keys = [d.message_key for d in run.diagnostics]
    warning = run.diagnostics[keys.index("fem.mechanism_mode_inconclusive")]
    assert warning.severity is Severity.WARNING
    render_message(warning)
    error = run.diagnostics[0]
    assert error.location is not None
    assert error.location.entity_id == run.pivot.node_id
    assert error.params_dict()["component"] == run.pivot.component


def test_invalid_stiffness_is_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Revisión de E1, M1: una petición que la salvaguarda rechaza da FAILED, no excepción."""
    real = translate_spatial

    def zero_area(model: object, config: SpatialAnalysisConfiguration) -> object:
        translation = real(model, config)  # type: ignore[arg-type]
        request = translation.request
        beams = (dataclasses.replace(request.beams[0], area=0.0), *request.beams[1:])
        return dataclasses.replace(translation, request=dataclasses.replace(request, beams=beams))

    monkeypatch.setattr(spatial, "translate_spatial", zero_area)
    run = analyze(cases._loaded_cantilever(cases.OBLIQUE))
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, "fem.invalid_stiffness")
    assert run.pivot is None
    assert "propiedad no positiva" in (run.diagnostics[0].cause or "")


# --- Fallos del worker --------------------------------------------------------------------


def test_timeout_is_failed(tmp_path: Path) -> None:
    run = analyze(
        cases._loaded_cantilever(cases.OBLIQUE),
        wrapper(tmp_path, before="import time; time.sleep(30)"),
        timeout_s=0.5,
    )
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, "fem.timeout")
    assert run.diagnostics[0].params_dict()["timeout"] == 0.5
    assert run.duration_s < 20


def test_worker_crash_is_failed(tmp_path: Path) -> None:
    run = analyze(
        cases._loaded_cantilever(cases.OBLIQUE),
        wrapper(tmp_path, before="sys.stderr.write('boom'); sys.exit(1)"),
    )
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, "fem.worker_crashed")
    assert run.diagnostics[0].params_dict()["returncode"] == 1
    assert "boom" in run.worker_stderr


_EMPTY = "nodes=[], grounds=[], chains=[], beams=[]"


@pytest.mark.parametrize(
    ("status", "code", "expected", "key"),
    [
        ("ANALYZE_FAILED", "-3", -3, "fem.solver_failed"),
        ("SOLVER_ERROR", "None", -1, "fem.solver_failed"),
        ("PROTOCOL_ERROR", "None", None, "fem.protocol_error"),
    ],
)
def test_declared_worker_failure_is_failed(
    tmp_path: Path, status: str, code: str, expected: int | None, key: str
) -> None:
    """Revisión de E1, M1: estado no OK declarado por el worker 3D (función local, D-c)."""
    after = f"data.update(status={status!r}, analyze_code={code}, message='fallo', {_EMPTY})"
    run = analyze(cases._loaded_cantilever(cases.OBLIQUE), wrapper(tmp_path, after=after))
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, key)
    assert run.diagnostics[0].cause == "fallo"
    if expected is not None:
        assert run.diagnostics[0].params_dict()["code"] == expected


# --- Respuestas manipuladas ---------------------------------------------------------------


def test_tampered_beam_force_fails(tmp_path: Path) -> None:
    after = "data['beams'][0][1][0] += 1.0"
    run = analyze(cases._loaded_cantilever(cases.OBLIQUE), wrapper(tmp_path, after=after))
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, "fem.member_force_check_failed")
    assert run.diagnostics[0].location is not None


def test_tampered_reaction_fails_equilibrium(tmp_path: Path) -> None:
    after = "max(data['nodes'], key=lambda n: abs(n[2][2]))[2][2] += 1.0"
    run = analyze(cases._loaded_cantilever(cases.OBLIQUE), wrapper(tmp_path, after=after))
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, "fem.equilibrium_failed")


def test_tampered_chain_fails(tmp_path: Path) -> None:
    after = "data['chains'][0][1][0] += 1e-6"
    run = analyze(cases._roller_frame(2.0), wrapper(tmp_path, after=after))
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, "fem3d.chain_postcondition_failed")
    assert run.diagnostics[0].location is not None


def test_reaction_at_r_fails(tmp_path: Path) -> None:
    """D-a (hecho 8): una reacción en R distinta de 0 da FAILED con el control de R."""
    after = "data['chains'][0][3][4] += 1.0"
    run = analyze(cases._roller_frame(2.0), wrapper(tmp_path, after=after))
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, "fem3d.chain_postcondition_failed")
    assert run.diagnostics[0].params_dict()["control"] == CONTROL_CHAIN_SUPPORT_MOMENT


def test_tampered_displacement_fails(tmp_path: Path) -> None:
    """localForce ya no corresponde a u: lo detecta el primer control de la secuencia
    (esfuerzos por barra); el residuo se prueba aparte en test_fem3d_postconditions."""
    after = "max(data['nodes'], key=lambda n: abs(n[1][0]))[1][0] *= 1.0 + 1e-6"
    run = analyze(cases._portal_with_self_weight(), wrapper(tmp_path, after=after))
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, "fem.member_force_check_failed")


def test_response_not_matching_request(tmp_path: Path) -> None:
    run = analyze(cases._spring_tip(), wrapper(tmp_path, after="data['grounds'] = []"))
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, "fem.protocol_error")


def test_nonfinite_result(tmp_path: Path) -> None:
    after = "data['nodes'][-1][1][0] = float('nan')"
    run = analyze(cases._loaded_cantilever(cases.OBLIQUE), wrapper(tmp_path, after=after))
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, "fem.nonfinite_result")


def test_stiff_penalty_fails_estimator() -> None:
    """eps·1e10 ≈ 2,2e-6 > tol_penalty: el estimador a priori lo rechaza (pieza 5a)."""
    run = analyze(cases._penalty_tip(2.0, (cases.RX, cases.RY)), penalty_factor=1e10)
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, "fem3d.penalty_estimate_failed")
    assert run.diagnostics[0].location is not None


def test_pure_torsion_with_two_chains_succeeds() -> None:
    """Revisión 5a, O4 y DEV-2: con torsión pura ‖u_tras‖∞ es ruido; la cota escala con
    a·‖θ‖∞ y la corrida legítima pasa."""
    frame = Frame()
    a = frame.node(0.0, 0.0)
    b = cases.along(frame, a, cases.OBLIQUE, 2.0)
    member = frame.member(a, b, alpha=0.37)
    frame.support(a, fixed_restraints())
    frame.support(b, build_restraints((cases.UX, cases.UY)), cases.PENALTY_BASIS)
    ex = frame.model.axes(member.id).ex.components()
    frame.nodal(b, mx=1000.0 * ex[0], my=1000.0 * ex[1], mz=1000.0 * ex[2])
    assert analyze(frame).status is RunStatus.SUCCEEDED
