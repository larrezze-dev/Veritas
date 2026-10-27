"""Pruebas de la frontera CLI (contrato del plan T-001)."""

import os
import subprocess
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

import veritas
from veritas import cli


def test_package_version_matches_distribution_metadata() -> None:
    assert veritas.__version__ == version("veritas")


def test_info_reports_version_warning_and_module_status(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["info"]) == cli.EXIT_OK
    out, err = capsys.readouterr()
    assert err == ""
    assert f"{veritas.PRODUCT_NAME} {veritas.__version__}" in out
    assert cli.EXPERIMENTAL_WARNING in out
    for name, status in cli.MODULE_STATUS:
        assert name in out
        assert status in out


def test_only_implemented_modules_are_declared_available() -> None:
    status = dict(cli.MODULE_STATUS)
    implemented = {"Unidades SI", "Geometría y barras"}
    for name in implemented:
        assert status[name].startswith(cli.AVAILABLE)
    others = {value for name, value in status.items() if name not in implemented}
    assert others <= {cli.PENDING, cli.NOT_CHECKED}
    assert status["Verificación normativa"] == cli.NOT_CHECKED


def test_no_arguments_prints_help_and_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == cli.EXIT_OK
    out, _ = capsys.readouterr()
    assert "usage: veritas" in out


def test_version_option_exits_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(["--version"])
    assert exc_info.value.code == cli.EXIT_OK
    out, _ = capsys.readouterr()
    assert out.strip() == f"veritas {veritas.__version__}"


@pytest.mark.parametrize("argv", [["desconocido"], ["--opcion-inexistente"], ["info", "extra"]])
def test_invalid_usage_exits_two_with_stderr_only(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        cli.main(argv)
    assert exc_info.value.code == cli.EXIT_USAGE
    out, err = capsys.readouterr()
    assert out == ""
    assert "usage: veritas" in err


def _run_module(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    # -P evita anteponer el directorio actual a sys.path: se prueba el paquete instalado.
    # No se usa -I porque ignoraría PYTHONIOENCODING y la salida canalizada no sería UTF-8.
    return subprocess.run(
        [sys.executable, "-P", "-m", "veritas", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
        check=False,
    )


def test_installed_module_info_exit_code(tmp_path: Path) -> None:
    result = _run_module(["info"], tmp_path)
    assert result.returncode == cli.EXIT_OK
    assert cli.EXPERIMENTAL_WARNING in result.stdout
    assert result.stderr == ""


def test_installed_module_invalid_command_exit_code(tmp_path: Path) -> None:
    result = _run_module(["desconocido"], tmp_path)
    assert result.returncode == cli.EXIT_USAGE
    assert result.stdout == ""
    assert "usage: veritas" in result.stderr
