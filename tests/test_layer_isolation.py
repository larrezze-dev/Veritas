"""Aislamiento de capas: el núcleo de dominio no depende de GUI ni de FEM.

Análisis estático con ``ast`` de la biblioteca estándar; no importa los módulos analizados.
"""

import ast
import sys
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


def test_supports_module_is_scanned_and_isolated() -> None:
    core_files = sorted((PACKAGE_ROOT / "core").rglob("*.py"))
    support_file = PACKAGE_ROOT / "core" / "supports" / "support.py"
    assert support_file in core_files
    assert (PACKAGE_ROOT / "core" / "supports" / "__init__.py") in core_files
    assert forbidden_imports(support_file, PACKAGE_ROOT) == set()
    assert "veritas.core.supports.support" in imported_modules(
        PACKAGE_ROOT / "core" / "geometry" / "model.py", PACKAGE_ROOT
    )


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


LOADS_ALLOWED_INTERNAL = (
    "veritas.core.diagnostics",
    "veritas.core.units",
    "veritas.core.validation",
    "veritas.core.references",
    "veritas.core.geometry.vector",
    "veritas.core.geometry.local_axes",
    "veritas.core.loads",
)


def test_loads_module_is_scanned_and_isolated() -> None:
    """``core/loads`` no importa el modelo, materiales ni secciones (plan T-006, D8)."""
    loads_dir = PACKAGE_ROOT / "core" / "loads"
    files = sorted(loads_dir.glob("*.py"))
    core_files = sorted((PACKAGE_ROOT / "core").rglob("*.py"))
    assert {loads_dir / "__init__.py", loads_dir / "load_case.py", loads_dir / "loads.py"} <= set(
        files
    )
    for path in files:
        assert path in core_files
        assert forbidden_imports(path, PACKAGE_ROOT) == set()
        internal = {
            name for name in imported_modules(path, PACKAGE_ROOT) if name.startswith("veritas")
        }
        assert all(
            any(
                name == allowed or name.startswith(f"{allowed}.")
                for allowed in LOADS_ALLOWED_INTERNAL
            )
            for name in internal
        ), internal


WORKER_FILE = PACKAGE_ROOT / "fem" / "adapters" / "opensees_worker.py"
WORKER_3D_FILE = PACKAGE_ROOT / "fem" / "adapters" / "opensees_worker_3d.py"


@pytest.mark.parametrize("worker", [WORKER_FILE, WORKER_3D_FILE], ids=["plano", "3d"])
def test_worker_imports_only_stdlib_and_openseespy(worker: Path) -> None:
    """Los workers no importan ``veritas`` ni dependencias ajenas (ADR-002 §4, ADR-003)."""
    tops = {name.split(".")[0] for name in imported_modules(worker, PACKAGE_ROOT)}
    assert "veritas" not in tops
    assert tops - {"openseespy"} <= sys.stdlib_module_names


def test_only_the_worker_imports_openseespy() -> None:
    importers = {
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in PACKAGE_ROOT.rglob("*.py")
        if any(name.split(".")[0] == "openseespy" for name in imported_modules(path, PACKAGE_ROOT))
    }
    assert importers == {"fem/adapters/opensees_worker.py", "fem/adapters/opensees_worker_3d.py"}
