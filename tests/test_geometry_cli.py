"""Pruebas de la CLI ``veritas geometry axes`` (plan T-003)."""

import pytest

from veritas import cli

RIGID_END = "Ux=RIGID Uy=RIGID Uz=RIGID Rx=RIGID Ry=RIGID Rz=RIGID"


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(["geometry", "axes", *argv])
    out, err = capsys.readouterr()
    return code, out, err


def test_quarter_turn_on_x(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(["--i", "0,0,0", "--j", "2,0,0", "--alpha", "90deg"], capsys)
    assert code == cli.EXIT_OK
    assert err == ""
    assert out.splitlines() == [
        "Barra I (0, 0, 0) m -> J (2, 0, 0) m",
        "L  = 2 m",
        "ex = (1, 0, 0)",
        "ey = (0, 0, 1)",
        "ez = (0, -1, 0)",
        "Referencia de +z: (0, 0, 1) (por defecto, +Z global)",
        "Giro alfa: 1.570796327 rad (90 deg)",
        f"Extremo I: {RIGID_END}",
        f"Extremo J: {RIGID_END}",
    ]


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["--j", "0,2,0"], ["ex = (0, 1, 0)", "ey = (-1, 0, 0)", "ez = (0, 0, 1)"]),
        (
            ["--j", "0,0,300", "--unit", "cm"],
            ["L  = 3 m", "ex = (0, 0, 1)", "ey = (0, 1, 0)", "ez = (-1, 0, 0)", "fallback -X"],
        ),
        (["--j", "2,0,0", "--ref", "0,0,-1"], ["ez = (0, 0, -1)", "(explícita)"]),
        (["--j", "2,0,0", "--alpha", "1.5e0rad"], ["Giro alfa: 1.5 rad"]),
    ],
)
def test_axes_cases(
    argv: list[str], expected: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run(["--i", "0,0,0", *argv], capsys)
    assert code == cli.EXIT_OK
    assert err == ""
    for text in expected:
        assert text in out


@pytest.mark.parametrize(
    ("argv", "code_prefix"),
    [
        (["--i", "1,1,1", "--j", "1,1,1"], "GEOMETRY_ZERO_LENGTH:"),
        (["--i", "0,0,0", "--j", "2,0,0", "--ref", "1,0,0"], "GEOMETRY_INVALID_ORIENTATION:"),
        (["--i", "0,0,0", "--j", "2,0,0", "--alpha", "400deg"], "GEOMETRY_INVALID_ORIENTATION:"),
        (["--i", "0,0,0", "--j", "2,0,0", "--alpha", "90"], "INPUT_INVALID_TYPE:"),
        (["--i", "0,0,0", "--j", "2,0,0", "--alpha", "1m"], "UNITS_INCOMPATIBLE:"),
        (["--i", "0,0", "--j", "2,0,0"], "INPUT_INVALID_TYPE:"),
        (["--i", "0,nan,0", "--j", "2,0,0"], "INPUT_NONFINITE:"),
        (["--i", "0,0,0", "--j", "2,0,0", "--unit", "kN"], "UNITS_INCOMPATIBLE:"),
    ],
)
def test_axes_errors(argv: list[str], code_prefix: str, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(argv, capsys)
    assert code == cli.EXIT_DIAGNOSTIC
    assert out == ""
    assert err.startswith(code_prefix)


def test_missing_required_option_is_usage_error(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["geometry", "axes", "--i", "0,0,0"])
    assert info.value.code == cli.EXIT_USAGE
    out, err = capsys.readouterr()
    assert out == ""
    assert "--j" in err


def test_negative_coordinates_use_equals_form(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(["--i=-1,0,0", "--j=1,0,0", "--ref=0,0,-1"], capsys)
    assert code == cli.EXIT_OK
    assert err == ""
    assert "L  = 2 m" in out
    assert "ez = (0, 0, -1)" in out
