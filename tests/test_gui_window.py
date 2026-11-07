"""Ventana Qt, visor PyVista y selección sincronizada (plan T-011, E2).

Marca ``gui``: se omite si falta el extra opcional. Por defecto usa ``QT_QPA_PLATFORM=offscreen``,
donde VTK no tiene ventana real y no proyecta píxeles: la prueba de clic se omite ahí y se
ejecuta con la plataforma nativa (``QT_QPA_PLATFORM=windows uv run pytest -m gui``).
"""

from collections.abc import Iterator
from uuid import UUID, uuid4

import pytest

from gui_support import frame_model, require_gui

require_gui()

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from veritas.core.geometry.model import StructuralModel  # noqa: E402
from veritas.gui.main_window import MainWindow  # noqa: E402
from veritas.gui.scene import build_scene  # noqa: E402
from veritas.gui.selection import Selection, SelectionKind  # noqa: E402
from veritas.gui.session import EditSession  # noqa: E402

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    return app


@pytest.fixture
def window(qapp: QApplication) -> Iterator[MainWindow]:
    model, _ = frame_model()
    win = MainWindow(EditSession(model=model))
    win.show()
    qapp.processEvents()
    yield win
    win.close()


def _flags(table: object, row: int, column: int) -> Qt.ItemFlag:
    item = table.item(row, column)  # type: ignore[attr-defined]
    assert item is not None
    return item.flags()  # type: ignore[no-any-return]


def _member(window: MainWindow, index: int) -> Selection:
    return Selection(SelectionKind.MEMBER, window.scene.members[index].id)


def _node(window: MainWindow, index: int) -> Selection:
    return Selection(SelectionKind.NODE, window.scene.nodes[index].id)


def _selected_rows(window: MainWindow) -> tuple[list[int], list[int]]:
    return (
        [i.row() for i in window.members_table.selectionModel().selectedRows()],
        [i.row() for i in window.nodes_table.selectionModel().selectedRows()],
    )


def _text(window: MainWindow, table: str, row: int, column: int) -> str:
    widget = window.members_table if table == "members" else window.nodes_table
    item = widget.item(row, column)
    assert item is not None
    return str(item.text())


def test_window_shows_the_model_in_two_tables_with_editable_node_cells(
    window: MainWindow,
) -> None:
    assert window.members_table.rowCount() == 3
    assert window.nodes_table.rowCount() == 4
    assert _text(window, "members", 1, 0) == "D"
    assert _text(window, "nodes", 3, 4) == "3 traslaciones y 0 giros fijos"
    assert not _flags(window.members_table, 0, 0) & Qt.ItemFlag.ItemIsEditable
    for column in (0, 1, 2, 3):
        assert _flags(window.nodes_table, 0, column) & Qt.ItemFlag.ItemIsEditable
    assert not _flags(window.nodes_table, 0, 4) & Qt.ItemFlag.ItemIsEditable
    assert "experimental" in window.statusBar().currentMessage()
    assert window.selection is None


def test_selecting_a_member_highlights_it_in_viewer_and_table(window: MainWindow) -> None:
    window.select(_member(window, 1))
    assert window.selection == _member(window, 1)
    assert window.viewport.selection == window.selection
    assert _selected_rows(window) == ([1], [])
    assert window.tabs.currentWidget() is window.members_table
    assert "Barra D" in window.statusBar().currentMessage()


def test_selecting_a_node_clears_the_member_selection(window: MainWindow) -> None:
    window.select(_member(window, 0))
    window.select(_node(window, 2))
    assert _selected_rows(window) == ([], [2])
    assert window.tabs.currentWidget() is window.nodes_table
    assert "Nudo N3" in window.statusBar().currentMessage()


def test_choosing_a_table_row_selects_it_in_the_viewer(window: MainWindow) -> None:
    window.members_table.selectRow(2)
    assert window.viewport.selection == _member(window, 2)
    window.nodes_table.selectRow(1)
    assert window.viewport.selection == _node(window, 1)
    assert _selected_rows(window) == ([], [1])


def test_clearing_the_selection_empties_viewer_and_tables(window: MainWindow) -> None:
    window.select(_member(window, 0))
    window.select(None)
    assert window.selection is None
    assert _selected_rows(window) == ([], [])


def test_unknown_entities_are_ignored_instead_of_selected(window: MainWindow) -> None:
    window.select(Selection(SelectionKind.MEMBER, UUID(int=uuid4().int)))
    assert window.selection is None


def test_a_new_scene_drops_a_selection_that_no_longer_exists(window: MainWindow) -> None:
    window.select(_member(window, 0))
    window.viewport.set_scene(build_scene(StructuralModel().snapshot()))
    assert window.viewport.selection is None


def test_an_empty_model_opens_without_error(qapp: QApplication) -> None:
    win = MainWindow(EditSession())
    win.show()
    qapp.processEvents()
    assert win.members_table.rowCount() == 0
    assert win.nodes_table.rowCount() == 0
    win.close()


def test_click_on_a_projected_member_or_node_selects_it(window: MainWindow) -> None:
    viewport, scene = window.viewport, window.scene
    pixels = [viewport.display_position(node.position) for node in scene.nodes]
    if len(set(pixels)) < len(pixels):
        pytest.skip("La plataforma Qt offscreen no proyecta píxeles: ejecutar con la nativa.")
    emitted: list[object] = []
    viewport.selection_changed.connect(emitted.append)
    viewport.click_at(viewport.display_position(scene.members[1].midpoint))
    assert window.selection == _member(window, 1)
    assert _selected_rows(window) == ([1], [])
    viewport.click_at(pixels[3])
    assert window.selection == _node(window, 3)
    assert _selected_rows(window) == ([], [3])
    viewport.click_at((1.0, 1.0))
    assert window.selection is None
    assert len(emitted) == 3
