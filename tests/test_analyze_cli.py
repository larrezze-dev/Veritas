"""CLI ``veritas analyze demo`` (plan T-007): salida, códigos 0/2/3 y marca de valores brutos."""

import pytest

from veritas import cli


@pytest.mark.solver
def test_demo_beam_succeeds_with_raw_table(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["analyze", "demo"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "Estado: SUCCEEDED" in out
    assert cli.RAW_NOTE in out
    assert "openseespy 3.8.0.0" in out
    assert "-1.190476e-04" in out  # u₂ punta = −PL³/(3EI) (gate G1)
    assert "Referencia a mano (gate G1): u₂ punta = −PL³/(3EI) = -1.190476e-04 m" in out
    assert "—" in out  # Reacciones no aplicables en el nudo libre, no ceros.
    assert cli.EXPERIMENTAL_WARNING in out


def test_demo_mechanism_fails_without_results(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["analyze", "demo", "--caso", "mecanismo"]) == cli.EXIT_ANALYSIS_FAILED
    out = capsys.readouterr().out
    assert "Estado: FAILED" in out
    assert "ANALYSIS_SINGULAR" in out
    assert cli.RAW_NOTE not in out
    assert "Sin resultados" in out


@pytest.mark.parametrize("timeout", ["0", "-1", "abc"])
def test_invalid_timeout_is_rejected(timeout: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["analyze", "demo", "--timeout", timeout]) == cli.EXIT_DIAGNOSTIC
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err


def test_unknown_case_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["analyze", "demo", "--caso", "otro"])
    assert info.value.code == cli.EXIT_USAGE
    assert capsys.readouterr().out == ""


def test_info_declares_fem_available_but_raw() -> None:
    status = dict(cli.MODULE_STATUS)["Análisis FEM"]
    assert status.startswith(cli.AVAILABLE)
    assert "BRUTOS" in status
