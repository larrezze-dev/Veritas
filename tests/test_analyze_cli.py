"""CLI ``veritas analyze demo`` (planes T-007 y T-008): tablas normalizadas, códigos 0/2/3."""

import pytest

from veritas import cli
from veritas.core.diagnostics import Diagnostic, DiagnosticCode
from veritas.fem.results.normalized import ResultsNormalizationError


@pytest.mark.solver
def test_demo_beam_shows_normalized_tables(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["analyze", "demo"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Estado: SUCCEEDED" in out
    assert cli.RESULTS_NOTE in out
    assert cli.MARKS_NOTE in out
    assert cli.RAW_NOTE not in out
    assert "openseespy 3.8.0.0" in out
    # Reacción y fuerzas de extremo del voladizo (ADR-001 §2), perfil SI.
    assert "10000.000 N" in out
    assert "-20000.000 N·m" in out
    assert "Referencia a mano: Uz punta = −PL³/(3EI) = -1.190e-04 m" in out
    assert cli.NOT_CALCULATED_MARK in out
    assert cli.EXPERIMENTAL_WARNING in out


@pytest.mark.solver
def test_demo_beam_profile_shows_deflection_in_mm(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["analyze", "demo", "--profile", "kN-m"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Perfil de unidades: kN-m" in out
    assert "-0.119 mm" in out
    assert "8.929e-05 rad" in out  # Ry = −θ > 0 en la punta
    assert "-20.000 kN·m" in out
    assert "20.000 kN·m" in out  # My(0+)


@pytest.mark.solver
def test_demo_raw_flag_appends_raw_table(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["analyze", "demo", "--bruto"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert cli.RESULTS_NOTE in out
    assert cli.RAW_NOTE in out
    assert "-1.190476e-04" in out  # u₂ bruto de la punta (gate G1)


@pytest.mark.solver
def test_demo_portico_shows_inclined_support_basis(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["analyze", "demo", "--caso", "portico", "--profile", "kN-m"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Reacciones en la base del apoyo inclinado" in out
    assert "Uz′: penalización, Ry′: resorte" in out
    assert cli.NOT_APPLICABLE_MARK in out  # Ux′ libre del rodillo y Ry de la articulación
    assert "R/k_pen" in out
    assert "fem.hypothesis" not in out  # Hipótesis traducidas, no claves


def test_demo_mechanism_fails_without_results(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["analyze", "demo", "--caso", "mecanismo"]) == cli.EXIT_ANALYSIS_FAILED
    out = capsys.readouterr().out
    assert "Estado: FAILED" in out
    assert "ANALYSIS_SINGULAR" in out
    assert cli.RESULTS_NOTE not in out
    assert cli.RAW_NOTE not in out
    assert cli.NO_RESULTS_FAILED in out


@pytest.mark.solver
def test_normalization_failure_exits_3_without_tables(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revisión M5: SUCCEEDED con postcondición no superada → código 3, sin tablas."""

    def broken(*_args: object) -> None:
        raise ResultsNormalizationError(
            Diagnostic.create(
                DiagnosticCode.ANALYSIS_FAILED,
                "fem.results_equilibrium",
                {"where": "node", "force": 1.0, "moment": 0.0},
            )
        )

    monkeypatch.setattr(cli, "normalize_planar_xz", broken)
    assert cli.main(["analyze", "demo"]) == cli.EXIT_ANALYSIS_FAILED
    out = capsys.readouterr().out
    assert "Estado: SUCCEEDED" in out
    assert "ERROR ANALYSIS_FAILED" in out
    assert cli.NO_RESULTS_NORMALIZATION in out
    assert cli.RESULTS_NOTE not in out


@pytest.mark.parametrize("timeout", ["0", "-1", "abc"])
def test_invalid_timeout_is_rejected(timeout: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["analyze", "demo", "--timeout", timeout]) == cli.EXIT_DIAGNOSTIC
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err


@pytest.mark.parametrize("args", [["--caso", "otro"], ["--profile", "imperial"]])
def test_unknown_option_value_is_usage_error(
    args: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["analyze", "demo", *args])
    assert info.value.code == cli.EXIT_USAGE
    assert capsys.readouterr().out == ""


def test_info_declares_fem_normalized_but_not_validated() -> None:
    status = dict(cli.MODULE_STATUS)["Análisis FEM"]
    assert status.startswith(cli.AVAILABLE)
    assert "normalizados (T-008)" in status
    assert "contrastados solo con T-FEM-001…004 (T-009)" in status
    assert status.endswith("no validados")


# --- T-010, D7: uniones, tres materiales y biempotrada --------------------------------


@pytest.mark.solver
def test_unions_compares_three_ends_and_diagnoses_mechanism(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["analyze", "uniones", "--profile", "kN-m"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    for name in ("Rígida", "Liberada", "Resorte", "Ref. Rígida", "Ref. Liberada"):
        assert name in out
    assert out.count("Estado: SUCCEEDED") == 3
    assert "[Mecanismo] Estado: FAILED" in out
    assert "ANALYSIS_SINGULAR" in out
    assert "Ubicación (orientativa):" in out
    assert "6.875 kN" in out  # 11P/16 de la variante liberada.
    assert cli.COMPARISON_NOTE in out
    assert cli.EXPERIMENTAL_WARNING in out


@pytest.mark.solver
def test_three_materials_demo(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["analyze", "demo", "--caso", "tres-materiales"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    for text in ("Acero S235", "Acero S355", "Aluminio", "[ALUMINIUM]", "J de B2 liberado"):
        assert text in out
    assert out.count("Estado: SUCCEEDED") == 2
    assert "Los dos aceros tienen el mismo E" in out


def test_fixed_fixed_demo_is_rejected_before_solver(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["analyze", "demo", "--caso", "biempotrada"]) == cli.EXIT_DIAGNOSTIC
    out, err = capsys.readouterr()
    assert out == ""
    assert err.startswith("UNSUPPORTED_CONFIGURATION:")
