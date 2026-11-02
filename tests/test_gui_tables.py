"""Contenido de las tablas de barras y nudos (plan T-011, E2): sin Qt ni PyVista."""

import ast
from pathlib import Path
from types import ModuleType
from uuid import UUID

import pytest

from gui_support import frame_model
from veritas.core.supports.support import RestraintKind
from veritas.gui import picking, selection, tables
from veritas.gui.scene import Scene, SceneSupport, build_scene
from veritas.gui.tables import entity_name, member_rows, node_rows, support_text


@pytest.fixture
def scene() -> Scene:
    model, _ = frame_model()
    return build_scene(model.snapshot())


def test_member_rows_follow_the_scene_order_and_use_presentation_units(scene: Scene) -> None:
    assert member_rows(scene) == (
        ("P1", "N1", "N2", "3.000 m", "(0.000, 0.000, 1.000)"),
        ("D", "N2", "N3", "4.000 m", "(1.000, 0.000, 0.000)"),
        ("P2", "N3", "N4", "3.000 m", "(0.000, 0.000, -1.000)"),
    )


def test_node_rows_show_coordinates_and_their_supports(scene: Scene) -> None:
    rows = node_rows(scene)
    assert rows[0] == ("N1", "0.000 m", "0.000 m", "0.000 m", "Empotrado")
    assert rows[1][4] == "—"
    assert rows[3] == ("N4", "4.000 m", "0.000 m", "0.000 m", "3 traslaciones y 0 giros fijos")


def test_rows_have_one_cell_per_header(scene: Scene) -> None:
    assert all(len(row) == len(tables.MEMBER_HEADERS) for row in member_rows(scene))
    assert all(len(row) == len(tables.NODE_HEADERS) for row in node_rows(scene))


def test_entity_name_falls_back_to_the_start_of_the_id() -> None:
    uid = UUID("12345678-1234-5678-1234-567812345678")
    assert entity_name("", uid) == "12345678"
    assert entity_name("N1", uid) == "N1"


def test_springs_and_tilted_bases_are_named_not_counted_as_fixed() -> None:
    restraints = (
        RestraintKind.FIXED,
        RestraintKind.FREE,
        RestraintKind.SPRING,
        RestraintKind.FREE,
        RestraintKind.SPRING,
        RestraintKind.FREE,
    )
    support = SceneSupport(UUID(int=1), "A", 0, restraints, global_basis=False)
    assert (
        support_text(support) == "1 traslaciones y 0 giros fijos + 2 con resorte (base inclinada)"
    )


@pytest.mark.parametrize("module", [selection, picking, tables])
def test_pure_gui_modules_import_neither_qt_nor_pyvista(module: ModuleType) -> None:
    assert module.__file__ is not None
    path = Path(module.__file__)
    forbidden = {"PySide6", "shiboken6", "pyvista", "pyvistaqt", "vtk", "vtkmodules", "numpy"}
    imported: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not imported & forbidden
