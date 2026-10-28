"""Runner y registro de corrida con workers simulados, sin OpenSees (plan T-007, NFR-003).

Un worker simulado es un script que lee la petición por stdin y escribe (o no) una
respuesta en la ruta recibida. Todo fallo debe dar FAILED sin resultados.
"""

import textwrap
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from fem_support import Frame
from veritas.core.diagnostics import DiagnosticCode, InputValueError
from veritas.core.supports.support import fixed_restraints, roller_restraints
from veritas.fem import analyze_planar_xz
from veritas.fem.results.run import AnalysisRun, RunStatus

HEADER = """
import json, sys, time
request = json.loads(sys.stdin.read())
path = sys.argv[1]

def respond(status="OK", code=0, nodes=None, aux=None, beams=None):
    data = {
        "protocol": "veritas.opensees.planar", "version": 2, "status": status,
        "analyze_code": code, "message": "simulado",
        "versions": {"python": "x", "openseespy": "y", "openseespywin": None},
        "nodes": nodes if nodes is not None else [
            [n[0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]] for n in request["nodes"]],
        "aux": aux if aux is not None else [[s[3], [0.0, 0.0, 0.0]] for s in request["springs"]],
        "beams": beams if beams is not None else [[b[0], [0.0] * 6] for b in request["beams"]],
        "end_aux": [[k[5], [0.0] * 3, [0.0] * 3] for k in request["end_links"]],
    }
    with open(path, "w", encoding="utf-8") as stream:
        json.dump(data, stream)
"""


def fake_worker(tmp_path: Path, body: str) -> Path:
    script = tmp_path / "fake_worker.py"
    script.write_text(HEADER + textwrap.dedent(body), encoding="utf-8")
    return script


def cantilever() -> Frame:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(2, 0)
    frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.nodal(b, fz=-1000.0)
    return frame


def run_with(tmp_path: Path, body: str, frame: Frame | None = None, **config: float) -> AnalysisRun:
    frame = frame or cantilever()
    revision = frame.model.model_revision
    run = analyze_planar_xz(frame.model, frame.config(**config), worker=fake_worker(tmp_path, body))
    assert frame.model.model_revision == revision  # El modelo no cambia (NFR-003).
    return run


def assert_failed(run: AnalysisRun, code: DiagnosticCode, key: str) -> None:
    assert run.status is RunStatus.FAILED
    assert run.raw is None
    assert run.diagnostics[0].code is code
    assert run.diagnostics[0].message_key == key


def test_timeout_kills_worker_and_publishes_nothing(tmp_path: Path) -> None:
    run = run_with(tmp_path, "time.sleep(30)\nrespond()\n", timeout_s=0.5)
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, "fem.timeout")
    assert run.duration_s < 20


def test_crash_without_response(tmp_path: Path) -> None:
    run = run_with(tmp_path, "sys.stderr.write('boom')\nsys.exit(1)\n")
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, "fem.worker_crashed")
    assert run.diagnostics[0].params_dict()["returncode"] == 1
    assert "boom" in run.worker_stderr


def test_corrupt_response(tmp_path: Path) -> None:
    run = run_with(tmp_path, "open(path, 'w').write('{roto')\n")
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, "fem.protocol_error")


def test_response_not_matching_request(tmp_path: Path) -> None:
    run = run_with(tmp_path, "respond(nodes=[])\n")
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, "fem.protocol_error")


def test_nonfinite_result(tmp_path: Path) -> None:
    body = """
    nodes = [[n[0], [float("nan"), 0.0, 0.0], [0.0, 0.0, 0.0]] for n in request["nodes"]]
    respond(nodes=nodes)
    """
    run = run_with(tmp_path, body)
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, "fem.nonfinite_result")


def test_analyze_failure_reported_by_worker(tmp_path: Path) -> None:
    run = run_with(
        tmp_path, "respond(status='ANALYZE_FAILED', code=-3, nodes=[], aux=[], beams=[])\n"
    )
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, "fem.solver_failed")
    assert run.diagnostics[0].params_dict()["code"] == -3
    assert run.versions["openseespy"] == "y"


def test_equilibrium_check_rejects_unbalanced_response(tmp_path: Path) -> None:
    # Reacciones nulas frente a una carga de 1000 N: no hay equilibrio.
    run = run_with(tmp_path, "respond()\n")
    assert_failed(run, DiagnosticCode.ANALYSIS_FAILED, "fem.equilibrium_failed")


def test_balanced_response_succeeds_with_provenance(tmp_path: Path) -> None:
    body = """
    fixed = {f[0] for f in request["fixes"]}
    nodes = [
        [n[0], [0.0, 0.0, 0.0], [0.0, 1000.0, 2000.0] if n[0] in fixed else [0.0, 0.0, 0.0]]
        for n in request["nodes"]
    ]
    respond(nodes=nodes)
    """
    frame = cantilever()
    first = run_with(tmp_path, body, frame)
    second = run_with(tmp_path, body, frame)
    assert first.status is RunStatus.SUCCEEDED
    assert first.raw is not None
    assert first.run_id != second.run_id
    assert first.request_json == second.request_json
    assert first.model_revision == frame.model.model_revision
    assert first.case_id == frame.case_id
    assert first.configuration["timeout_s"] == 60.0
    assert first.versions["adapter"] == "veritas.fem.planar_xz/2"  # T-010
    assert first.versions["protocol"] == "2"
    assert first.pivot is not None
    assert first.pivot.free_dofs == 3
    reactions = {n.reaction for n in first.raw.nodes}
    assert (None, None, None) in reactions  # Nudo libre: «no aplicable», nunca 0.


def test_singular_model_never_launches_worker(tmp_path: Path) -> None:
    marker = tmp_path / "launched"
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(4, 0)
    frame.member(a, b)
    frame.support(a, roller_restraints())
    frame.support(b, roller_restraints())
    frame.nodal(b, fz=-1.0)
    run = run_with(tmp_path, f"open({str(marker)!r}, 'w').write('x')\nrespond()\n", frame)
    assert_failed(run, DiagnosticCode.ANALYSIS_SINGULAR, "fem.singular")
    assert not marker.exists()
    assert run.versions["openseespy"] is None


def test_too_many_dofs_is_rejected_before_running(tmp_path: Path) -> None:
    frame = cantilever()
    with pytest.raises(InputValueError) as info:
        analyze_planar_xz(
            frame.model, frame.config(max_free_dofs=2), worker=fake_worker(tmp_path, "")
        )
    assert info.value.code is DiagnosticCode.UNSUPPORTED_CONFIGURATION
    assert info.value.diagnostic.message_key == "fem.too_many_dofs"


def test_structure_without_free_dofs_is_rejected_before_running(tmp_path: Path) -> None:
    # Plan T-009, D3: biempotrada de una barra; antes caía el worker (LAPACK DGBSV).
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(2, 0)
    frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.nodal(b, fz=-1000.0)
    marker = tmp_path / "launched"
    worker = fake_worker(tmp_path, f"open({str(marker)!r}, 'w').write('x')\nrespond()\n")
    revision = frame.model.model_revision
    with pytest.raises(InputValueError) as info:
        analyze_planar_xz(frame.model, frame.config(), worker=worker)
    assert info.value.code is DiagnosticCode.UNSUPPORTED_CONFIGURATION
    assert info.value.diagnostic.message_key == "fem.no_free_dofs"
    assert not marker.exists()
    assert frame.model.model_revision == revision


def test_run_record_invariant() -> None:
    with pytest.raises(ValueError, match="SUCCEEDED"):
        AnalysisRun(
            run_id=uuid4(),
            status=RunStatus.SUCCEEDED,
            diagnostics=(),
            model_revision=1,
            case_id=uuid4(),
            case_revision=1,
            configuration={},
            request_json=None,
            versions={},
            hypotheses=(),
            started_utc=datetime.now(UTC),
            duration_s=0.0,
            pivot=None,
            worker_stdout="",
            worker_stderr="",
            raw=None,
        )
