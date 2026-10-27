"""CLI ``veritas section rect`` y ``veritas model demo`` (plan T-004)."""

import pytest

from veritas import cli

RIGID_END = "Ux=RIGID Uy=RIGID Uz=RIGID Rx=RIGID Ry=RIGID Rz=RIGID"


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def test_section_rect_in_mm_with_kn_m_profile(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(
        ["section", "rect", "--b", "200", "--h", "400", "--unit", "mm", "--profile", "kN-m"],
        capsys,
    )
    assert code == cli.EXIT_OK
    assert err == ""
    lines = out.splitlines()
    assert lines[0] == "Sección rectangular maciza b = 0.200 m (y) × h = 0.400 m (z)"
    rows = {line.split()[0]: line.split()[1:] for line in lines[4:]}
    assert rows["A"] == ["800.000", "cm²", "CALCULADO", "rect-closed-form/1"]
    assert rows["Iy"] == ["106666.667", "cm⁴", "CALCULADO", "rect-closed-form/1"]
    assert rows["Iz"] == ["26666.667", "cm⁴", "CALCULADO", "rect-closed-form/1"]
    assert rows["Iyz"] == ["0.000", "cm⁴", "CALCULADO", "rect-closed-form/1"]
    assert rows["yc"][:2] == rows["zc"][:2] == ["0.000", "m"]
    assert rows["J"] == ["73178.137", "cm⁴", "CALCULADO", "saint-venant-series/1"]


@pytest.mark.parametrize(
    ("argv", "code_text"),
    [
        (["--b", "0", "--h", "400", "--unit", "mm"], "INPUT_OUT_OF_RANGE"),
        (["--b", "-1", "--h", "1"], "INPUT_OUT_OF_RANGE"),
        (["--b", "abc", "--h", "1"], "INPUT_INVALID_TYPE"),
        (["--b", "1", "--h", "1", "--unit", "kg"], "UNITS_INCOMPATIBLE"),
        (["--b", "1", "--h", "1", "--unit", "xx"], "UNITS_UNKNOWN"),
        (["--b", "nan", "--h", "1"], "INPUT_NONFINITE"),
        (["--b", "inf", "--h", "1"], "INPUT_NONFINITE"),
        (["--b", "1e-160", "--h", "1e-160"], "UNITS_UNREPRESENTABLE"),
    ],
)
def test_section_rect_errors(
    argv: list[str], code_text: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run(["section", "rect", *argv], capsys)
    assert code == cli.EXIT_DIAGNOSTIC
    assert out == ""
    assert err.startswith(f"{code_text}: ")


def test_model_demo(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(["model", "demo", "--profile", "kN-m"], capsys)
    assert code == cli.EXIT_OK
    assert err == ""
    assert "EJEMPLO" in out
    assert "[STEEL] rev 1" in out
    assert "[ALUMINIUM] rev 1" in out
    assert "[TIMBER] rev 1" in out
    assert "verificación normativa NO_COMPROBADA" in out
    assert out.count("(edición a confirmar)") == 3
    assert "UNE-EN 338; CTE DB-SE-M Anejo E" in out
    bars = {line.split()[0]: line for line in out.splitlines() if line.startswith("B")}
    assert "STEEL rev 1" in bars["B1"]
    assert "628.000 kg/m" in bars["B1"]
    assert "54.000 kg/m" in bars["B2"]
    assert "16.128 kg/m" in bars["B3"]
    assert out.count(RIGID_END) == 6
    assert "se crea la revisión 2" in out
    assert "la barra B1 conserva la revisión 1" in out
    assert "model_revision = 7" in out


def test_info_declares_materials_available(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(["info"], capsys)
    assert code == cli.EXIT_OK
    assert "Materiales y secciones" in out
    assert "DISPONIBLE (T-004)" in out
