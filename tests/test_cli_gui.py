"""``veritas gui`` (plan T-011, E2): arranque, extra ausente e importación diferida."""

import subprocess
import sys
import types
from pathlib import Path

import pytest

from gui_support import frame_model
from veritas import cli, cli_gui
from veritas.core.geometry.snapshot import ModelSnapshot
from veritas.persistence.project import save_project


def test_without_the_extra_the_command_explains_how_to_install_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli_gui, "missing_modules", lambda: ["PySide6", "pyvista"])
    assert cli.main(["gui"]) == cli_gui.EXIT_MISSING_EXTRA
    out, err = capsys.readouterr()
    assert out == ""
    assert "GUI_NO_DISPONIBLE" in err
    assert "PySide6, pyvista" in err
    assert "uv sync --locked --extra gui" in err


def test_the_rest_of_the_cli_does_not_import_qt_or_pyvista() -> None:
    code = (
        "import sys, veritas.cli\n"
        "veritas.cli.main(['info'])\n"
        "bad = [m for m in ('PySide6', 'pyvista', 'pyvistaqt', 'vtk') if m in sys.modules]\n"
        "sys.exit(1 if bad else 0)\n"
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr.decode()


def _fake_app(monkeypatch: pytest.MonkeyPatch) -> list[tuple[ModelSnapshot, str]]:
    calls: list[tuple[ModelSnapshot, str]] = []
    module = types.ModuleType("veritas.gui.app")

    def run_gui(snapshot: ModelSnapshot, source: str) -> int:
        calls.append((snapshot, source))
        return 0

    module.run_gui = run_gui  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "veritas.gui.app", module)
    monkeypatch.setattr(cli_gui, "missing_modules", list)
    return calls


def test_without_a_file_it_opens_the_demo_model(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _fake_app(monkeypatch)
    assert cli.main(["gui"]) == cli_gui.EXIT_OK
    assert len(calls) == 1
    snapshot, source = calls[0]
    assert len(snapshot.members) > 0
    assert "demostración" in source


def test_with_a_project_file_it_opens_its_model(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls = _fake_app(monkeypatch)
    model, _ = frame_model()
    path = tmp_path / "porticos.veritas"
    save_project(path, model)
    assert cli.main(["gui", str(path)]) == cli_gui.EXIT_OK
    assert len(calls) == 1
    snapshot, source = calls[0]
    assert [n.label for n in snapshot.nodes] == ["N1", "N2", "N3", "N4"]
    assert source == str(path)


def test_a_tampered_project_is_refused_before_opening_the_window(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    calls = _fake_app(monkeypatch)
    model, _ = frame_model()
    path = tmp_path / "alterado.veritas"
    save_project(path, model)
    path.write_text(path.read_text(encoding="utf-8").replace('"N1"', '"N9"'), encoding="utf-8")
    assert cli.main(["gui", str(path)]) == cli_gui.EXIT_INTEGRITY
    assert calls == []
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip()
