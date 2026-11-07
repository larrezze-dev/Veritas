"""T-030 E2b-2: ventana editable con Qt offscreen: montar un pórtico solo con diálogos y tablas,
deshacer/rehacer, guardar y reabrir con el mismo hash, borrado con vista previa, avisos y cierre.

Los diálogos se pilotan sustituyendo ``MainWindow._exec`` (rellenan campos y pulsan Aceptar de
verdad: pasa por ``accept`` y la validación de ``forms``).
"""

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from gui_support import frame_model, require_gui

require_gui()

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from veritas.core.geometry.selection_logic import (  # noqa: E402
    Selection,
    SelectionKind,
    SelectionSet,
)
from veritas.gui.dialogs import (  # noqa: E402
    DeletionDialog,
    MaterialDialog,
    MemberDialog,
    NodeDialog,
    OrientationDialog,
    SectionDialog,
)
from veritas.gui.main_window import CANCEL, DISCARD, SAVE, MainWindow  # noqa: E402
from veritas.gui.session import EditSession  # noqa: E402
from veritas.persistence.canonical import model_hash  # noqa: E402
from veritas.persistence.project import open_project, save_project  # noqa: E402

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def qapp() -> QApplication:
    app = QApplication.instance() or QApplication([])
    assert isinstance(app, QApplication)
    return app


class Rig:
    """Ventana con los puntos sustituibles reemplazados y un registro de lo ocurrido."""

    def __init__(self, win: MainWindow) -> None:
        self.win = win
        self.errors: list[str] = []
        self.unsaved_answer = DISCARD
        self.open_path: Path | None = None
        self.save_path: Path | None = None
        self.fill: Callable[[Any], None] = lambda dialog: None
        self.dialogs: list[Any] = []
        win._show_error = self.errors.append  # type: ignore[method-assign,assignment]
        win._exec = self._exec  # type: ignore[method-assign]
        win._confirm_unsaved = lambda: self.unsaved_answer  # type: ignore[method-assign]
        win._choose_open_path = lambda: self.open_path  # type: ignore[method-assign]
        win._choose_save_path = lambda: self.save_path  # type: ignore[method-assign]

    def _exec(self, dialog: Any) -> bool:
        self.dialogs.append(dialog)
        self.fill(dialog)
        dialog.accept()
        return bool(dialog.result() == QDialog.DialogCode.Accepted)

    def run(self, action: Callable[[], None], fill: Callable[[Any], None]) -> None:
        self.fill = fill
        action()

    @property
    def digest(self) -> str:
        return model_hash(self.win.session.model.snapshot())


@pytest.fixture
def rig(qapp: QApplication) -> Iterator[Rig]:
    win = MainWindow(EditSession())
    win.show()
    qapp.processEvents()
    handle = Rig(win)
    yield handle
    assert win.session.take_listener_errors() == ()  # ningún aviso roto en estos flujos
    win.close()
    win.close()  # cerrar dos veces no falla


def add_node(rig: Rig, label: str, x: str, y: str, z: str) -> None:
    def fill(dialog: NodeDialog) -> None:
        dialog.label.setText(label)
        dialog.x_edit.setText(x)
        dialog.y_edit.setText(y)
        dialog.z_edit.setText(z)

    rig.run(rig.win.new_node, fill)


def cell(table: Any, row: int, column: int) -> Any:
    item = table.item(row, column)
    assert item is not None
    return item


def pick(combo: Any, text: str) -> None:
    index = combo.findText(text, flags=Qt.MatchFlag.MatchContains)
    assert index >= 0, text
    combo.setCurrentIndex(index)


def add_member(rig: Rig, i: str, j: str, label: str) -> None:
    def fill(dialog: MemberDialog) -> None:
        pick(dialog.node_i, i)
        pick(dialog.node_j, j)
        pick(dialog.material, "Acero")
        pick(dialog.section, "R 20x40")
        dialog.label.setText(label)

    rig.run(rig.win.new_member, fill)


def add_material_and_section(rig: Rig) -> None:
    def material(dialog: MaterialDialog) -> None:
        dialog.name.setText("Acero S275")
        pick(dialog.family, "Acero")  # rellena los valores iniciales editables

    def section(dialog: SectionDialog) -> None:
        dialog.name.setText("R 20x40")
        pick(dialog.kind, "Rectangular")
        dialog.fields["b"].setText("20 cm")
        dialog.fields["h"].setText("40 cm")

    rig.run(rig.win.new_material, material)
    rig.run(rig.win.new_section, section)


def build_two_storey_frame(rig: Rig) -> None:
    """Pórtico plano de 2 plantas y 1 vano: 6 nudos, 4 pilares y 2 vigas, solo con diálogos."""
    add_material_and_section(rig)
    for label, x, z in (
        ("A0", "0", "0"),
        ("B0", "4", "0"),
        ("A1", "0", "3"),
        ("B1", "4", "3"),
        ("A2", "0", "6"),
        ("B2", "4", "6"),
    ):
        add_node(rig, label, x, "0", z)
    for i, j, label in (
        ("A0", "A1", "PA1"),
        ("B0", "B1", "PB1"),
        ("A1", "A2", "PA2"),
        ("B1", "B2", "PB2"),
        ("A1", "B1", "V1"),
        ("A2", "B2", "V2"),
    ):
        add_member(rig, i, j, label)


def test_a_two_storey_frame_is_built_from_dialogs_and_survives_undo_redo_save_reopen(
    rig: Rig, tmp_path: Path
) -> None:
    win = rig.win
    build_two_storey_frame(rig)
    assert rig.errors == []
    assert (len(win.scene.nodes), len(win.scene.members)) == (6, 6)
    assert win.members_table.rowCount() == 6
    assert win.nodes_table.rowCount() == 6
    assert win.session.dirty
    assert win.windowTitle().startswith("*")
    final = rig.digest
    steps = len(win.session.editor.undo_names)
    assert steps == 14  # material, sección, 6 nudos, 6 barras: un paso cada uno
    for _ in range(steps):
        win.undo()
    assert (len(win.scene.nodes), len(win.scene.members)) == (0, 0)
    assert not win.session.dirty  # vuelve al estado inicial: limpio
    for _ in range(steps):
        win.redo()
    assert rig.digest == final
    target = tmp_path / "porticos.veritas"
    rig.save_path = target
    assert win.save_as()
    assert not win.session.dirty
    assert not win.windowTitle().startswith("*")
    assert "porticos.veritas" in win.windowTitle()
    win.new_project()
    assert win.session.path is None
    assert len(win.scene.nodes) == 0
    win.open_project(target)
    assert rig.digest == final
    assert open_project(target).model_hash == final
    assert (len(win.scene.nodes), len(win.scene.members)) == (6, 6)
    assert [cell(win.members_table, r, 0).text() for r in range(6)][:2] == ["PA1", "PB1"]


def test_invalid_dialog_input_keeps_the_dialog_open_and_shows_the_diagnostic(
    rig: Rig, qapp: QApplication
) -> None:
    dialog = NodeDialog()
    dialog.x_edit.setText("abc")
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert "abc" in dialog.error_label.text()
    dialog.x_edit.setText("2,5 m")
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert dialog.error_label.text() == ""
    assert dialog.result_input.label == ""
    # el material pide familia y unidades coherentes
    material = MaterialDialog()
    material.name.setText("X")
    material.accept()
    assert material.result() != QDialog.DialogCode.Accepted
    assert material.error_label.text()
    # la madera no trae valores sugeridos; el acero sí
    pick(material.family, "Madera")
    assert material.e.text() == ""
    pick(material.family, "Acero")
    assert material.e.text().startswith("210000")


def test_editing_a_node_cell_moves_only_that_coordinate_and_is_undoable(rig: Rig) -> None:
    win = rig.win
    add_node(rig, "N", "0.123456789", "0.987654321", "2")
    row = 0
    before = rig.digest
    cell(win.nodes_table, row, 3).setText("250 cm")
    node = win.session.model.node(win.scene.nodes[row].id)
    assert (node.position.x, node.position.y) == (0.123456789, 0.987654321)
    assert abs(node.position.z - 2.5) < 1e-12
    assert cell(win.nodes_table, row, 3).text().startswith("2.5")
    win.undo()
    assert rig.digest == before
    assert rig.errors == []


def test_a_bad_cell_edit_is_reported_and_the_cell_recovers_its_value(rig: Rig) -> None:
    win = rig.win
    add_node(rig, "N", "1", "2", "3")
    shown = cell(win.nodes_table, 0, 1).text()
    before = rig.digest
    cell(win.nodes_table, 0, 1).setText("no es un número")
    assert rig.errors == []  # el aviso modal se difiere: no se abre dentro de itemChanged
    assert win.last_error
    QApplication.processEvents()
    assert len(rig.errors) == 1
    assert "no es un número" in rig.errors[0]
    assert rig.digest == before
    assert cell(win.nodes_table, 0, 1).text() == shown


def test_renaming_a_node_from_its_label_cell(rig: Rig) -> None:
    win = rig.win
    add_node(rig, "N", "0", "0", "0")
    cell(win.nodes_table, 0, 0).setText("Base")
    assert win.session.model.node(win.scene.nodes[0].id).label == "Base"
    win.undo()
    assert win.session.model.node(win.scene.nodes[0].id).label == "N"


def test_delete_shows_the_preview_and_is_one_undo_step(rig: Rig) -> None:
    win = rig.win
    build_two_storey_frame(rig)
    names = {n.label: n.id for n in win.scene.nodes}
    win.select_set(SelectionSet.of([Selection(SelectionKind.NODE, names["B1"])]))
    before = rig.digest
    steps = len(win.session.editor.undo_names)
    rig.fill = lambda dialog: None
    win.delete_selection()
    dialog = rig.dialogs[-1]
    assert isinstance(dialog, DeletionDialog)
    text = dialog.preview.toPlainText()
    assert "B1" in text
    assert "PB1" in text
    assert "V1" in text  # las barras que arrastra el nudo
    assert len(win.scene.nodes) == 5
    assert len(win.session.editor.undo_names) == steps + 1
    assert win.selection_set.nodes == frozenset()  # lo borrado sale de la selección
    win.undo()
    assert rig.digest == before


def test_multi_selection_from_the_tables_and_assigning_a_section(rig: Rig) -> None:
    win = rig.win
    build_two_storey_frame(rig)

    def new_section(dialog: SectionDialog) -> None:
        dialog.name.setText("R 30x30")
        pick(dialog.kind, "Rectangular")
        dialog.fields["b"].setText("0.3")
        dialog.fields["h"].setText("0.3")

    rig.run(win.new_section, new_section)
    win.select_set(
        SelectionSet.of([Selection(SelectionKind.MEMBER, m.id) for m in win.scene.members[:3]])
    )
    assert len(win.selection_set.members) == 3
    assert win.selection is None  # varias: no hay «la» selección
    selected_rows = {i.row() for i in win.members_table.selectionModel().selectedRows()}
    assert selected_rows == {0, 1, 2}
    assert "3 barra(s)" in win.inspector.toPlainText()
    steps = len(win.session.editor.undo_names)

    def choose(dialog: Any) -> None:
        pick(dialog.combo, "R 30x30")

    rig.run(win.assign_section, choose)
    assert len(win.session.editor.undo_names) == steps + 1  # UN paso para las tres
    sections = {m.section for m in win.session.model.members.values()}
    assert len(sections) == 2
    rig.errors.clear()
    # selección desde la tabla: sustituye, y con Ctrl se suma (se comprueba la sustitución)
    win.members_table.selectRow(5)
    assert win.selection_set.members == frozenset({win.scene.members[5].id})


def test_orientation_of_several_members_is_one_step(rig: Rig) -> None:
    win = rig.win
    build_two_storey_frame(rig)
    win.select_set(
        SelectionSet.of([Selection(SelectionKind.MEMBER, m.id) for m in win.scene.members[:2]])
    )
    before = rig.digest
    steps = len(win.session.editor.undo_names)

    def fill(dialog: OrientationDialog) -> None:
        dialog.reference.setText("0; 1; 0")
        dialog.alpha.setText("90")

    rig.run(win.orient_selected, fill)
    assert rig.errors == []
    assert len(win.session.editor.undo_names) == steps + 1
    assert rig.digest != before
    win.undo()
    assert rig.digest == before


def test_a_domain_rejection_is_reported_without_changing_the_model(rig: Rig) -> None:
    win = rig.win
    add_material_and_section(rig)
    add_node(rig, "A", "0", "0", "0")
    add_node(rig, "B", "0", "0", "0")  # mismo punto: barra de longitud nula
    before = rig.digest
    add_member(rig, "A", "B", "M")
    assert rig.errors  # el dominio rechaza la barra de longitud nula
    assert rig.digest == before
    assert len(win.scene.members) == 0


def test_closing_with_changes_asks_and_can_be_cancelled_saved_or_discarded(
    rig: Rig, tmp_path: Path
) -> None:
    win = rig.win
    add_node(rig, "N", "1", "1", "1")
    rig.unsaved_answer = CANCEL
    assert not win.close()  # la ventana sigue abierta
    assert win.isVisible()
    rig.unsaved_answer = SAVE
    rig.save_path = tmp_path / "cierre.veritas"
    assert win.close()
    assert open_project(tmp_path / "cierre.veritas").model.snapshot().nodes[0].label == "N"


def test_closing_a_clean_project_does_not_ask(rig: Rig) -> None:
    rig.unsaved_answer = "no debe preguntarse"
    assert rig.win.close()


def test_open_asks_before_discarding_changes_and_a_cancelled_dialog_changes_nothing(
    rig: Rig, tmp_path: Path
) -> None:
    win = rig.win
    model, _ = frame_model()
    path = tmp_path / "otro.veritas"
    save_project(path, model)
    add_node(rig, "N", "5", "5", "5")
    before = rig.digest
    rig.unsaved_answer = CANCEL
    win.open_project(path)
    assert rig.digest == before
    rig.unsaved_answer = DISCARD
    rig.open_path = None  # selector de archivos cancelado
    win.open_project()
    assert rig.digest == before
    rig.open_path = path
    win.open_project()
    assert len(win.scene.nodes) == 4


def test_a_broken_file_is_a_message_and_leaves_the_session_as_it_was(
    rig: Rig, tmp_path: Path
) -> None:
    win = rig.win
    add_node(rig, "N", "1", "2", "3")
    before = rig.digest
    broken = tmp_path / "roto.veritas"
    broken.write_text("{ no es un proyecto", encoding="utf-8")
    rig.unsaved_answer = DISCARD
    win.open_project(broken)
    assert rig.errors
    assert rig.digest == before
    assert win.session.dirty
    rig.errors.clear()
    rig.save_path = tmp_path / "no" / "hay" / "p.veritas"
    assert not win.save_as()
    assert rig.errors
    assert win.session.path is None


def test_save_without_a_path_asks_for_one(rig: Rig, tmp_path: Path) -> None:
    win = rig.win
    add_node(rig, "N", "0", "0", "0")
    rig.save_path = None
    assert not win.save()  # el selector se cancela: nada se guarda
    assert win.session.dirty
    rig.save_path = tmp_path / "p.veritas"
    assert win.save()
    assert not win.session.dirty
    add_node(rig, "M", "1", "1", "1")
    rig.save_path = None
    assert win.save()  # ya hay ruta: guarda sin preguntar
    assert not win.session.dirty


def test_undo_and_redo_actions_follow_the_history(rig: Rig) -> None:
    win = rig.win
    assert not win.act_undo.isEnabled()
    assert not win.act_redo.isEnabled()
    add_node(rig, "N", "0", "0", "0")
    assert win.act_undo.isEnabled()
    assert "Crear nudo" in win.act_undo.text()
    win.undo()
    assert win.act_redo.isEnabled()
    assert "Crear nudo" in win.act_redo.text()
    win.redo()
    assert not win.act_redo.isEnabled()


def test_selection_that_no_longer_exists_is_dropped_after_undo(rig: Rig) -> None:
    win = rig.win
    add_node(rig, "N", "0", "0", "0")
    assert win.selection is not None  # el nudo recién creado queda seleccionado
    win.undo()
    assert win.selection is None
    assert win.selection_set == SelectionSet()
    assert win.viewport.selection_set == SelectionSet()


def test_a_listener_error_does_not_break_the_edit_and_is_shown(rig: Rig) -> None:
    win = rig.win

    def broken() -> None:
        raise RuntimeError("aviso roto")

    win.session.subscribe(broken)
    add_node(rig, "N", "0", "0", "0")
    assert len(win.scene.nodes) == 1  # la edición se hizo
    assert rig.errors == []  # no se cuenta como edición fallida
    assert "aviso roto" in win.statusBar().currentMessage()
    win.session.unsubscribe(broken)
    assert win.session.take_listener_errors() == ()  # ya se mostraron y se vaciaron


def test_a_cell_edit_that_changes_nothing_restores_the_model_format(rig: Rig) -> None:
    win = rig.win
    add_node(rig, "N", "0", "0", "6.5")
    cell(win.nodes_table, 0, 3).setText("6,5")  # mismo valor: sin cambio en el modelo
    assert cell(win.nodes_table, 0, 3).text() != "6,5"
    assert cell(win.nodes_table, 0, 3).text().startswith("6.5")
    assert len(win.session.editor.undo_names) == 1
    assert rig.errors == []
