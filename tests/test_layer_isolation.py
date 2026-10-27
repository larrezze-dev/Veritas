"""Aislamiento de capas: el núcleo de dominio no depende de GUI ni de FEM.

Análisis estático con ``ast`` de la biblioteca estándar; no importa los módulos analizados.
"""

import ast
from pathlib import Path

import pytest

import veritas

PACKAGE_ROOT = Path(veritas.__file__).resolve().parent

# Bibliotecas externas prohibidas en ``veritas.core`` (CLAUDE.md, ARCHITECTURE.md).
FORBIDDEN_EXTERNAL = frozenset(
    {"PySide6", "shiboken6", "pyvista", "pyvistaqt", "vtk", "vtkmodules", "openseespy"}
)
# Capas internas de las que ``veritas.core`` no puede depender.
FORBIDDEN_INTERNAL = frozenset({"veritas.gui", "veritas.fem", "veritas.reports"})


def _module_name(path: Path, package_root: Path) -> str:
    relative = path.relative_to(package_root.parent).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_relative(module: str | None, level: int, current: str, is_package: bool) -> str:
    base = current.split(".")
    if not is_package:
        base.pop()
    if level > 1:
        base = base[: len(base) - (level - 1)]
    return ".".join([*base, module] if module else base)


def imported_modules(path: Path, package_root: Path) -> set[str]:
    """Devuelve los nombres absolutos de los módulos importados por ``path``."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    current = _module_name(path, package_root)
    is_package = path.name == "__init__.py"
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module is not None:
                base = node.module
            else:
                base = _resolve_relative(node.module, node.level, current, is_package)
            found.add(base)
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


def forbidden_imports(path: Path, package_root: Path) -> set[str]:
    """Importaciones de ``path`` que violan el aislamiento del núcleo."""
    violations: set[str] = set()
    for name in imported_modules(path, package_root):
        if name.split(".")[0] in FORBIDDEN_EXTERNAL:
            violations.add(name)
        if any(name == layer or name.startswith(f"{layer}.") for layer in FORBIDDEN_INTERNAL):
            violations.add(name)
    return violations


def test_core_package_exists() -> None:
    assert (PACKAGE_ROOT / "core" / "__init__.py").is_file()


def test_core_does_not_import_gui_or_fem() -> None:
    core_files = sorted((PACKAGE_ROOT / "core").rglob("*.py"))
    assert core_files
    violations = {
        str(path.relative_to(PACKAGE_ROOT)): found
        for path in core_files
        if (found := forbidden_imports(path, PACKAGE_ROOT))
    }
    assert violations == {}


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("import PySide6.QtWidgets\n", "PySide6.QtWidgets"),
        ("from pyvista import Plotter\n", "pyvista"),
        ("import openseespy.opensees as ops\n", "openseespy.opensees"),
        ("from vtkmodules import vtkCommonCore\n", "vtkmodules"),
        ("from veritas.gui import window\n", "veritas.gui"),
        ("from .. import fem\n", "veritas.fem"),
        ("from ..fem.adapters import run\n", "veritas.fem.adapters"),
    ],
)
def test_detector_flags_forbidden_imports(tmp_path: Path, source: str, expected: str) -> None:
    package_root = tmp_path / "veritas"
    module = package_root / "core" / "modulo.py"
    module.parent.mkdir(parents=True)
    module.write_text(source, encoding="utf-8")
    assert expected in forbidden_imports(module, package_root)


@pytest.mark.parametrize(
    "source",
    ["import math\n", "from dataclasses import dataclass\n", "from . import geometry\n"],
)
def test_detector_accepts_allowed_imports(tmp_path: Path, source: str) -> None:
    package_root = tmp_path / "veritas"
    module = package_root / "core" / "modulo.py"
    module.parent.mkdir(parents=True)
    module.write_text(source, encoding="utf-8")
    assert forbidden_imports(module, package_root) == set()
