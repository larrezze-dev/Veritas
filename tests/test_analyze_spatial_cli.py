"""CLI ``veritas analyze espacial`` (plan T-018, D7-A, pieza 7).

Casos ``referencias``, ``contraste-plano`` y ``mecanismo-torsion`` con sus códigos de salida,
y la comparación de la salida aceptada de ``analyze demo|uniones|referencias`` con
``tests/golden`` (normalizada en ``cli_golden``).
"""

import dataclasses

import pytest

import benchmark_cache
import cli_golden
from veritas import cli
from veritas.fem import RunStatus, benchmarks

pytestmark = pytest.mark.solver


@pytest.fixture(autouse=True)
def cached_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    """Las corridas de referencia y de contraste, una vez por sesión (``benchmark_cache``)."""
    monkeypatch.setattr(benchmarks, "run_spatial_reference", benchmark_cache.run_spatial_reference)
    monkeypatch.setattr(benchmarks, "run_cross_planar", benchmark_cache.run_cross_planar)


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = cli.main(["analyze", "espacial", *args])
    return code, capsys.readouterr().out


def test_references_all_match(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "--caso", "referencias", "--profile", "kN-m")
    assert code == cli.EXIT_OK
    assert "Referencias analíticas espaciales (plan T-018, D8):" in out
    for case in ("T-FEM3D-01 a", "T-FEM3D-02 b", "T-FEM3D-03 b", "T-FEM3D-05 a"):
        assert f"== {case} — " in out
    assert "Las 8 variantes coinciden" in out
    assert cli.REFERENCE_MISMATCH not in out
    assert "J (= 0.00073178 m⁴" in out  # hipótesis con la J de la sección
    assert " kN·m " in out
    assert cli.EXPERIMENTAL_WARNING in out


def test_default_case_is_references(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys)
    assert code == cli.EXIT_OK
    assert "Referencias analíticas espaciales" in out


def test_cross_planar_matches(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "--caso", "contraste-plano")
    assert code == cli.EXIT_OK
    assert "Contraste espacial-plano de T-FEM-001…004" in out
    assert out.count("Corrida plana del contraste: SUCCEEDED") == 5
    assert "fórmulas del plano: coincide" in out
    assert (
        "Las 5 variantes coinciden con las fórmulas del caso plano y con el adaptador plano" in out
    )
    assert " 0 (fuera del plano) " in out


def test_torsion_mechanism_exits_3_with_mode(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "--caso", "mecanismo-torsion")
    assert code == cli.EXIT_ANALYSIS_FAILED
    assert "Estado: FAILED" in out
    assert "ERROR ANALYSIS_SINGULAR" in out
    assert "Modo de mecanismo (nulidad 1" in out
    for node in ("N1", "N2", "N3"):
        assert f"nudo {node}, Rx" in out
    assert cli.SPATIAL_TORSION_EXPECTED in out
    assert cli.SPATIAL_TORSION_MISSED not in out
    assert cli.NO_RESULTS_FAILED in out


def test_mismatch_prints_no_coincide_and_exit_3(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = benchmarks.run_spatial_reference

    def wrong(case_id: str, timeout_s: float | None = None) -> tuple[object, ...]:
        outcomes = real(case_id, timeout_s)
        if case_id != "T-FEM3D-02":
            return outcomes
        first = outcomes[0]
        check = dataclasses.replace(first.checks[0], reference=first.checks[0].reference * 2)
        return (dataclasses.replace(first, checks=(check, *first.checks[1:])), *outcomes[1:])

    monkeypatch.setattr(benchmarks, "run_spatial_reference", wrong)
    code, out = run(capsys)
    assert code == cli.EXIT_ANALYSIS_FAILED
    assert f"{cli.REFERENCE_MISMATCH}: T-FEM3D-02 a." in out
    assert "Las 8 variantes coinciden" not in out


def test_failed_run_is_not_shown_as_match(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    real = benchmarks.run_cross_planar

    def failed(case_id: str, timeout_s: float | None = None) -> tuple[object, ...]:
        outcomes = real(case_id, timeout_s)
        if case_id != "T-FEM-001":
            return outcomes
        run_ = dataclasses.replace(outcomes[0].run, status=RunStatus.FAILED, raw=None)
        empty = dataclasses.replace(outcomes[0], run=run_, results=None, checks=())
        return (empty, *outcomes[1:])

    monkeypatch.setattr(benchmarks, "run_cross_planar", failed)
    code, out = run(capsys, "--caso", "contraste-plano")
    assert code == cli.EXIT_ANALYSIS_FAILED
    assert f"T-FEM-001: {cli.REFERENCE_NO_RESULTS}" in out
    assert f"{cli.REFERENCE_NO_RESULTS}: T-FEM-001." in out


def test_failed_planar_contrast_run_has_no_results(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revisión de E1: una corrida plana FAILED del contraste no dice «NO COINCIDE»."""
    real = benchmarks.run_cross_planar

    def failed(case_id: str, timeout_s: float | None = None) -> tuple[object, ...]:
        outcomes = real(case_id, timeout_s)
        if case_id != "T-FEM-002":
            return outcomes
        planar = outcomes[0].planar
        assert planar is not None
        run_ = dataclasses.replace(planar.run, status=RunStatus.FAILED, raw=None)
        empty = dataclasses.replace(planar, run=run_, results=None, checks=())
        return (dataclasses.replace(outcomes[0], planar=empty), *outcomes[1:])

    monkeypatch.setattr(benchmarks, "run_cross_planar", failed)
    code, out = run(capsys, "--caso", "contraste-plano")
    assert code == cli.EXIT_ANALYSIS_FAILED
    line = next(x for x in out.splitlines() if "Corrida plana del contraste: FAILED" in x)
    assert line.endswith(f"fórmulas del plano: {cli.REFERENCE_NO_RESULTS}")
    assert cli.REFERENCE_MISMATCH not in line


def test_unknown_case_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as error:
        cli.main(["analyze", "espacial", "--caso", "desconocido"])
    assert error.value.code == cli.EXIT_USAGE
    assert "invalid choice" in capsys.readouterr().err


def test_invalid_timeout_is_diagnostic(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["analyze", "espacial", "--timeout", "-1"]) == cli.EXIT_DIAGNOSTIC
    assert capsys.readouterr().err


def test_help_documents_oblique_components(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["analyze", "espacial", "--help"])
    out = capsys.readouterr().out
    for token in ("t1/t2", "r1/r2", "mecanismo-torsion", "contraste-plano", "3 corrida FAILED"):
        assert token in out


def test_mechanism_text_for_oblique_components() -> None:
    from uuid import uuid4  # noqa: PLC0415

    from veritas.fem import MechanismDof  # noqa: PLC0415

    node = uuid4()
    item = MechanismDof(node_id=node, component="t1", participation=0.5)
    text = cli._mechanism_dof_text(item, {node: "N7"})
    assert text == "nudo N7, traslación oblicua t1 (0.5)"
    item = MechanismDof(node_id=node, component="r2", participation=0.25)
    assert cli._mechanism_dof_text(item, {node: "N7"}) == "nudo N7, giro oblicuo r2 (0.25)"


@pytest.mark.parametrize("name", sorted(cli_golden.COMMANDS))
def test_accepted_analyze_output_is_unchanged(name: str) -> None:
    """D7-A: la salida aceptada de ``analyze demo|uniones|referencias`` no cambia."""
    golden = (cli_golden.GOLDEN_DIR / f"{name}.txt").read_text(encoding="utf-8")
    assert cli_golden.capture(cli_golden.COMMANDS[name]) == golden
