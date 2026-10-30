"""CLI ``veritas load selfweight`` y ``veritas load demo`` (plan T-006, D9)."""

import pytest

from veritas import cli


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(argv)
    out, err = capsys.readouterr()
    return code, out, err


SELFWEIGHT = ["load", "selfweight", "--b", "0.2", "--h", "0.4", "--rho", "1000 kg/m3"]


def test_selfweight_fixture(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run([*SELFWEIGHT, "--length", "2 m", "--g", "10 m/s2"], capsys)
    assert (code, err) == (cli.EXIT_OK, "")
    assert "q = rho·A·g = 800.000 N/m  ->  qz = -800.000 N/m" in out
    assert "Fz = -q·L = -1600.000 N" in out
    assert "g = 10.000 m/s²" in out
    assert "por defecto" not in out


def test_selfweight_default_gravity_is_shown(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run([*SELFWEIGHT, "--length", "2 m"], capsys)
    assert code == cli.EXIT_OK
    assert "g = 9.807 m/s² (por defecto: gravedad normal convencional)" in out


def test_selfweight_profile_kn_m(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(
        [*SELFWEIGHT, "--length", "2 m", "--g", "10 m/s2", "--profile", "kN-m"], capsys
    )
    assert code == cli.EXIT_OK
    assert "-1.600 kN" in out


@pytest.mark.parametrize(
    ("extra", "code_text"),
    [
        (["--length", "2 m", "--g", "0 m/s2"], "INPUT_OUT_OF_RANGE"),
        (["--length", "2 m", "--g", "10 m"], "UNITS_INCOMPATIBLE"),
        (["--length", "0 m"], "INPUT_OUT_OF_RANGE"),
        (["--length", "2"], "INPUT_INVALID_TYPE"),
        (["--length", "2 parsecs"], "UNITS_UNKNOWN"),
    ],
)
def test_selfweight_errors(
    extra: list[str], code_text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run([*SELFWEIGHT, *extra], capsys)
    assert code == cli.EXIT_DIAGNOSTIC
    assert out == ""
    assert err.startswith(f"{code_text}:")


def test_selfweight_wrong_density_dimension(capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["load", "selfweight", "--b", "0.2", "--h", "0.4", "--rho", "1000 kN", "--length", "2 m"]
    code, out, err = _run(argv, capsys)
    assert (code, out) == (cli.EXIT_DIAGNOSTIC, "")
    assert err.startswith("UNITS_INCOMPATIBLE:")


def test_selfweight_missing_argument_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["load", "selfweight", "--b", "0.2"])
    assert info.value.code == cli.EXIT_USAGE
    assert capsys.readouterr().out == ""


def test_demo_values(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(["load", "demo"], capsys)
    assert (code, err) == (cli.EXIT_OK, "")
    assert "G1: F total = (0.000 N; 0.000 N; -7200.000 N)" in out
    assert "M total = (0.000 N·m; 28400.000 N·m; 0.000 N·m)" in out
    assert "Q1: F total = (8000.000 N; 0.000 N; -24000.000 N)" in out
    assert "M total = (0.000 N·m; 105000.000 N·m; 0.000 N·m)" in out
    assert "B2: q = (0.000 N/m; 0.000 N/m; -800.000 N/m)" in out
    assert "ez = (-0.8, 0, 0.6)" in out
    assert "Solo descripción de Q1: revisión 2, model_revision = 11" in out
    assert "asiento            N1     GLOBAL  u = (0.000 m; 0.000 m; -0.005 m)" in out
    assert "θ = (0.000 rad; 0.002 rad; 0.000 rad)" in out
    assert "Empotramiento en N1 y asiento en Q1: model_revision = 13" in out
    assert out.rstrip().endswith(cli.PRESCRIBED_NOT_ANALYSIS)


def test_demo_profile_kn_m(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(["load", "demo", "--profile", "kN-m"], capsys)
    assert code == cli.EXIT_OK
    assert "Q1: F total = (8.000 kN; 0.000 kN; -24.000 kN)" in out


def test_info_declares_loads(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(["info"], capsys)
    assert code == cli.EXIT_OK
    assert "Cargas" in out
    assert "DISPONIBLE (T-006, T-015)" in out  # D12 de T-015: línea ampliada
    assert "momento distribuido por barra" in out
