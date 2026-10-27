"""Pruebas de la CLI ``veritas units`` (plan T-002)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from veritas import cli


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    code = cli.main(argv)
    out, err = capsys.readouterr()
    return code, out, err


def test_units_list(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(["units", "list"], capsys)
    assert code == cli.EXIT_OK
    assert err == ""
    assert "Rigidez rotacional" in out
    assert "N·m/rad" in out
    assert "M·L^2·T^-2·A^-1" in out


def test_units_profiles(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _ = _run(["units", "profiles"], capsys)
    assert code == cli.EXIT_OK
    assert "kN-m" in out
    assert "N·mm/rad" in out


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["12.5", "kN*m", "--to", "N*m"], "12.5 kN·m = 12500 N·m"),
        (["210000", "N/mm2", "--to", "GPa"], "210000 N/mm² = 210 GPa"),
        (["-3", "mm", "--to", "m"], "-3 mm = -0.003 m"),
        (["180", "deg", "--to", "rad"], "180 deg = 3.14159265358979 rad"),
        (["1e-280", "mm6", "--to", "m6"], "1e-280 mm⁶ = 1e-298 m⁶"),
    ],
)
def test_units_convert(argv: list[str], expected: str, capsys: pytest.CaptureFixture[str]) -> None:
    code, out, err = _run(["units", "convert", *argv], capsys)
    assert code == cli.EXIT_OK
    assert err == ""
    assert out.strip() == expected


@pytest.mark.parametrize(
    ("argv", "code_prefix"),
    [
        (["1", "kN", "--to", "m"], "UNITS_INCOMPATIBLE:"),
        (["1", "furlong", "--to", "m"], "UNITS_UNKNOWN:"),
        (["nan", "m", "--to", "mm"], "INPUT_NONFINITE:"),
        (["abc", "m", "--to", "mm"], "INPUT_INVALID_TYPE:"),
        (["1e308", "kN", "--to", "N"], "UNITS_UNREPRESENTABLE:"),
        (["1e-290", "mm6", "--to", "m6"], "UNITS_UNREPRESENTABLE:"),
    ],
)
def test_units_convert_errors(
    argv: list[str], code_prefix: str, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = _run(["units", "convert", *argv], capsys)
    assert code == cli.EXIT_DIAGNOSTIC
    assert out == ""
    assert err.startswith(code_prefix)


@pytest.mark.parametrize("argv", [["units"], ["units", "convert", "1", "m"]])
def test_units_usage_errors(argv: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(argv)
    assert exc_info.value.code == cli.EXIT_USAGE
    out, err = capsys.readouterr()
    assert out == ""
    assert "usage: veritas" in err


def test_piped_output_in_legacy_encoding_does_not_crash(tmp_path: Path) -> None:
    # En Windows una salida canalizada puede usar cp1252, que no tiene «⁴».
    result = subprocess.run(
        [sys.executable, "-P", "-m", "veritas", "units", "list"],
        cwd=tmp_path,
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "cp1252"},
        check=False,
    )
    assert result.returncode == cli.EXIT_OK
    assert result.stderr == b""
    assert "Rigidez rotacional".encode("cp1252") in result.stdout
    assert "N·m/rad".encode("cp1252") in result.stdout  # «·» existe en cp1252
