"""Ventana principal editable (plan T-030 E2b-2; ADR-008, ADR-014).

Visor 3D y tablas de barras y nudos sincronizados sobre una ``EditSession``. TODA edición pasa por
el ``ModelEditor`` de la sesión (deshacer/rehacer incluidos); el visor y las tablas son vistas
derivadas que se reconstruyen cuando la sesión avisa. La selección es múltiple (Ctrl/Mayús en el
visor, selección extendida en las tablas) y nunca es fuente de verdad: se poda contra el modelo.

Lo que NO hay (E3): crear o arrastrar en el visor. Los diálogos, selectores de archivo y avisos
están en métodos pequeños (``_exec``, ``_choose_*``, ``_confirm_unsaved``, ``_show_error``) para
poder sustituirlos en las pruebas. Requiere el extra opcional ``gui``.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QDialog,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from veritas.core.diagnostics import DiagnosticError
from veritas.core.geometry.selection_logic import SelectionSet
from veritas.core.messages_es import render_message
from veritas.core.presentation import PROFILE_SI, PresentationProfile
from veritas.gui import forms
from veritas.gui.deletion import apply_deletion, describe, plan_deletion
from veritas.gui.dialogs import (
    ChoiceDialog,
    DeletionDialog,
    FormDialog,
    MaterialDialog,
    MemberDialog,
    NodeDialog,
    OrientationDialog,
    SectionDialog,
)
from veritas.gui.scene import Scene, build_scene
from veritas.gui.selection import Selection, SelectionKind
from veritas.gui.session import Attempt, EditSession
from veritas.gui.tables import (
    MEMBER_HEADERS,
    NODE_HEADERS,
    Row,
    entity_name,
    member_rows,
    node_rows,
)
from veritas.gui.viewport import Viewport

DISCLAIMER = (
    "Aplicación experimental, no apta para justificar obras reales sin validación independiente"
)
APP_NAME = "Veritas Structural Calculation 3D"
UNTITLED = "Proyecto sin título"
PROJECT_FILTER = "Proyecto Veritas (*.veritas *.json);;Todos los archivos (*)"
NODE_EDITABLE_COLUMNS = (0, 1, 2, 3)
"""Etiqueta, X, Y y Z son editables en la tabla de nudos; el apoyo no (E5)."""

SAVE, DISCARD, CANCEL = "save", "discard", "cancel"


def _make_table(headers: tuple[str, ...]) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(list(headers))
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
    table.setEditTriggers(
        QAbstractItemView.EditTrigger.DoubleClicked | QAbstractItemView.EditTrigger.EditKeyPressed
    )
    table.verticalHeader().setVisible(False)
    return table


class MainWindow(QMainWindow):
    """Visor + tablas editables sobre una ``EditSession``."""

    def __init__(
        self,
        session: EditSession,
        profile: PresentationProfile = PROFILE_SI,
    ) -> None:
        super().__init__()
        self.session = session
        self._profile = profile
        self._scene: Scene = build_scene(session.model.snapshot())
        self._selection = SelectionSet()
        self._syncing = False
        self._populating = False
        self._shown_editor = session.editor
        self.last_error = ""
        self._notice = ""
        self._closed = False
        self.resize(1200, 700)

        self.viewport = Viewport(self)
        self.members_table = _make_table(MEMBER_HEADERS)
        self.nodes_table = _make_table(NODE_HEADERS)
        self.inspector = QPlainTextEdit()
        self.inspector.setReadOnly(True)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.members_table, "Barras")
        self.tabs.addTab(self.nodes_table, "Nudos")
        side = QWidget()
        side_layout = QVBoxLayout(side)
        side_layout.setContentsMargins(0, 0, 0, 0)
        side_layout.addWidget(self.tabs, 3)
        side_layout.addWidget(self.inspector, 1)
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.viewport)
        splitter.addWidget(side)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.setCentralWidget(splitter)

        self._build_actions()
        self.viewport.selection_set_changed.connect(self.select_set)
        self.members_table.itemSelectionChanged.connect(self._on_table_selection)
        self.nodes_table.itemSelectionChanged.connect(self._on_table_selection)
        self.nodes_table.itemChanged.connect(self._on_node_cell_changed)
        session.subscribe(self._on_session_changed)
        self._refresh(reset_camera=True)

    # --- Propiedades -----------------------------------------------------------------------

    @property
    def scene(self) -> Scene:
        """Escena mostrada (derivada del modelo)."""
        return self._scene

    @property
    def selection(self) -> Selection | None:
        """La entidad seleccionada si es exactamente una; si no, ``None``."""
        return self._selection.single

    @property
    def selection_set(self) -> SelectionSet:
        return self._selection

    # --- Acciones y menús --------------------------------------------------------------------

    def _action(
        self,
        menu_text: str,
        slot: Callable[[], Any],
        shortcut: str | QKeySequence.StandardKey | None = None,
    ) -> QAction:
        action = QAction(menu_text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(lambda *_: slot())
        return action

    def _build_actions(self) -> None:
        std = QKeySequence.StandardKey
        self.act_new = self._action("&Nuevo proyecto", self.new_project, std.New)
        self.act_open = self._action("&Abrir…", self.open_project, std.Open)
        self.act_save = self._action("&Guardar", self.save, std.Save)
        self.act_save_as = self._action("Guardar &como…", self.save_as, std.SaveAs)
        self.act_undo = self._action("&Deshacer", self.undo, "Ctrl+Z")
        self.act_redo = self._action("&Rehacer", self.redo, "Ctrl+Y")
        self.act_node = self._action("Nuevo &nudo…", self.new_node)
        self.act_member = self._action("Nueva &barra…", self.new_member)
        self.act_material = self._action("Nuevo &material…", self.new_material)
        self.act_section = self._action("Nueva &sección…", self.new_section)
        self.act_orient = self._action("&Orientar barra…", self.orient_selected)
        self.act_assign_material = self._action("Asignar material…", self.assign_material)
        self.act_assign_section = self._action("Asignar sección…", self.assign_section)
        self.act_delete = self._action("&Borrar selección…", self.delete_selection, "Del")
        file_menu = self.menuBar().addMenu("&Archivo")
        for act in (self.act_new, self.act_open, self.act_save, self.act_save_as):
            file_menu.addAction(act)
        edit_menu = self.menuBar().addMenu("&Edición")
        for act in (self.act_undo, self.act_redo):
            edit_menu.addAction(act)
        edit_menu.addSeparator()
        for act in (
            self.act_node,
            self.act_member,
            self.act_material,
            self.act_section,
            self.act_orient,
            self.act_assign_material,
            self.act_assign_section,
        ):
            edit_menu.addAction(act)
        edit_menu.addSeparator()
        edit_menu.addAction(self.act_delete)

    # --- Puntos sustituibles (diálogos y selectores) ------------------------------------------

    def _exec(self, dialog: FormDialog) -> bool:
        return bool(dialog.exec() == QDialog.DialogCode.Accepted)

    def _choose_open_path(self) -> Path | None:
        name, _ = QFileDialog.getOpenFileName(self, "Abrir proyecto", "", PROJECT_FILTER)
        return Path(name) if name else None

    def _choose_save_path(self) -> Path | None:
        name, _ = QFileDialog.getSaveFileName(self, "Guardar proyecto como", "", PROJECT_FILTER)
        return Path(name) if name else None

    def _confirm_unsaved(self) -> str:
        box = QMessageBox(self)
        box.setWindowTitle(APP_NAME)
        box.setText("El proyecto tiene cambios sin guardar.")
        box.setStandardButtons(
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel
        )
        answer = box.exec()
        if answer == QMessageBox.StandardButton.Save:
            return SAVE
        if answer == QMessageBox.StandardButton.Discard:
            return DISCARD
        return CANCEL

    def _confirm_upgrade(self) -> bool:
        """Avisa de que guardar reescribe el archivo antiguo con el esquema vigente (H10)."""
        answer = QMessageBox.question(
            self,
            APP_NAME,
            "El archivo está en un esquema antiguo. Guardar lo reescribe con el esquema vigente "
            "y las versiones anteriores de la aplicación podrían no abrirlo. "
            "Use «Guardar como» para conservar el original. ¿Guardar igualmente?",
        )
        return bool(answer == QMessageBox.StandardButton.Yes)

    def _show_error(self, text: str) -> None:
        QMessageBox.warning(self, APP_NAME, text)

    def _report(self, text: str) -> None:
        """Guarda el último error, lo muestra en la barra de estado y avisa al usuario."""
        self.last_error = text
        self.statusBar().showMessage(text)
        self._show_error(text)

    # --- Archivo -----------------------------------------------------------------------------

    def _maybe_discard(self) -> bool:
        """``True`` si se puede continuar (sin cambios, guardado o descartado)."""
        if not self.session.dirty:
            return True
        answer = self._confirm_unsaved()
        if answer == CANCEL:
            return False
        if answer == SAVE:
            return self.save()
        return True

    def new_project(self) -> None:
        if self._maybe_discard():
            self.session.new()
            self._flush_listener_errors()

    def open_project(self, path: Path | None = None) -> None:
        if not self._maybe_discard():
            return
        target = path if path is not None else self._choose_open_path()
        if target is None:
            return
        try:
            self.session.open(target)
        except DiagnosticError as error:
            self._report(render_message(error.diagnostic))
        self._flush_listener_errors()

    def save(self) -> bool:
        """Guarda; sin ruta pide «Guardar como». ``False`` si se canceló o falló."""
        if self.session.path is None:
            return self.save_as()
        if self.session.upgrades_on_save and not self._confirm_upgrade():
            return False
        return self._write(self.session.save)

    def save_as(self, path: Path | None = None) -> bool:
        target = path if path is not None else self._choose_save_path()
        if target is None:
            return False
        if (
            self._same_file(target)
            and self.session.upgrades_on_save
            and not self._confirm_upgrade()
        ):
            return False  # R10: «Guardar como» sobre el mismo archivo antiguo también reescribe
        return self._write(lambda: self.session.save_as(target))

    def _same_file(self, target: Path) -> bool:
        current = self.session.path
        if current is None:
            return False
        try:
            return target.resolve() == current.resolve()
        except OSError:
            return target == current

    def _write(self, action: Callable[[], None]) -> bool:
        try:
            action()
        except DiagnosticError as error:
            self._report(render_message(error.diagnostic))
            return False
        self._flush_listener_errors()
        return True

    # --- Edición -----------------------------------------------------------------------------

    def _run[T](self, action: Callable[[Any], T], *, defer_error: bool = False) -> Attempt[T]:
        """Ejecuta una orden. Con ``defer_error`` el aviso y el refresco van al siguiente ciclo de
        eventos (una celda en edición no debe abrir un cuadro modal dentro de ``itemChanged``)."""
        self._notice = ""
        result = self.session.attempt(action)
        if not result.ok and defer_error:
            self.last_error = result.message
            self.statusBar().showMessage(result.message)
            QTimer.singleShot(0, lambda: self._after_bad_cell(result.message))
        elif not result.ok:
            self._report(result.message)
        self._flush_listener_errors()
        return result

    def undo(self) -> None:
        if self.session.editor.can_undo:
            self._run(lambda editor: editor.undo())

    def redo(self) -> None:
        if self.session.editor.can_redo:
            self._run(lambda editor: editor.redo())

    def new_node(self) -> None:
        dialog = NodeDialog(self._profile, self)
        if not self._exec(dialog):
            return
        result = self._run(lambda editor: forms.create_node(editor, dialog.result_input))
        if result.ok and result.value is not None:
            self.select_set(SelectionSet.of([Selection(SelectionKind.NODE, result.value.id)]))

    def new_member(self) -> None:
        dialog = MemberDialog(self.session.model, self)
        if not self._exec(dialog):
            return
        result = self._run(lambda editor: forms.create_member(editor, dialog.result_input))
        if result.ok and result.value is not None:
            self.select_set(SelectionSet.of([Selection(SelectionKind.MEMBER, result.value.id)]))

    def new_material(self) -> None:
        dialog = MaterialDialog(self._profile, self)
        if self._exec(dialog):
            self._run(lambda editor: forms.create_material(editor, dialog.result_input))

    def new_section(self) -> None:
        dialog = SectionDialog(self._profile, self)
        if not self._exec(dialog):
            return
        if isinstance(dialog.result_input, tuple):
            self._run(lambda e: forms.create_rectangular_section(e, dialog.result_input))
        else:
            self._run(lambda e: forms.create_parametric_section(e, dialog.result_input))

    def _selected_members(self) -> tuple[UUID, ...]:
        return tuple(sorted(self._selection.members, key=str))

    def orient_selected(self) -> None:
        members = self._selected_members()
        if not members:
            self.statusBar().showMessage("Seleccione al menos una barra para orientarla.")
            return
        defaults = forms.orientation_defaults(self.session.model, members)
        dialog = OrientationDialog(self, defaults=defaults)
        if not self._exec(dialog):
            return
        reference, alpha = dialog.result_input
        self._run(lambda editor: forms.orient_members(editor, members, reference, alpha, defaults))

    def assign_material(self) -> None:
        self._assign("Asignar material", "Material", forms.material_choices, "assign_material")

    def assign_section(self) -> None:
        self._assign("Asignar sección", "Sección", forms.section_choices, "assign_section")

    def _assign(self, title: str, label: str, choices: Callable[..., Any], command: str) -> None:
        members = self._selected_members()
        if not members:
            self.statusBar().showMessage("Seleccione al menos una barra.")
            return
        options = [(c.text, c.value) for c in choices(self.session.model)]
        dialog = ChoiceDialog(title, label, options, self)
        if self._exec(dialog):
            self._run(lambda editor: getattr(editor, command)(members, dialog.result_input))

    def delete_selection(self) -> None:
        selection = self._selection.pruned(self.session.model)
        if not selection:
            self.statusBar().showMessage("No hay nada seleccionado.")
            return
        plan = plan_deletion(self.session.model, selection)
        dialog = DeletionDialog(describe(self.session.model, plan), plan, self)
        if self._exec(dialog):
            self._run(lambda editor: apply_deletion(editor, selection))

    # --- Celdas editables ----------------------------------------------------------------------

    def _after_bad_cell(self, message: str) -> None:
        self._refresh()  # la celda recupera el valor del modelo
        self._show_error(message)

    def _on_node_cell_changed(self, item: QTableWidgetItem) -> None:
        if self._populating:
            return
        row, column = item.row(), item.column()
        if column not in NODE_EDITABLE_COLUMNS or row >= len(self._scene.nodes):
            return
        node = self._scene.nodes[row]
        text = item.text()
        if column == 0:
            if text == entity_name(node.label, node.id):
                return
            result = self._run(
                lambda editor: editor.relabel_node(node.id, text.strip()), defer_error=True
            )
        else:
            axis = forms.AXES[column - 1]
            result = self._run(
                lambda editor: forms.move_node_coordinate(
                    editor, node.id, axis, text, profile=self._profile
                ),
                defer_error=True,
            )
        if result.ok:
            self._refresh()  # sin cambio real, la celda recupera el formato del modelo

    # --- Selección -----------------------------------------------------------------------------

    def select(self, selection: Selection | None) -> None:
        """Selección de una sola entidad (compatibilidad con la ventana de solo lectura)."""
        self.select_set(SelectionSet.from_selection(selection))

    def select_set(self, selection: SelectionSet) -> None:
        """Fija la selección en el visor y en las tablas; lo que no existe se descarta."""
        if self._syncing:
            return
        self._syncing = True
        try:
            self._selection = selection.pruned(self.session.model)
            self.viewport.select_set(self._selection)
            self._show_in_tables()
            self._update_inspector()
            self._update_status()
        finally:
            self._syncing = False

    def _show_in_tables(self) -> None:
        for table, ids, index in (
            (self.members_table, self._selection.members, self._scene.member_index),
            (self.nodes_table, self._selection.nodes, self._scene.node_index),
        ):
            table.clearSelection()
            selection_model = table.selectionModel()
            for entity_id in ids:
                row = index.get(entity_id)
                if row is not None:
                    selection_model.select(
                        table.model().index(row, 0),
                        selection_model.SelectionFlag.Select | selection_model.SelectionFlag.Rows,
                    )
        single = self._selection.single
        if single is not None:
            table = self.members_table if single.kind is SelectionKind.MEMBER else self.nodes_table
            self.tabs.setCurrentWidget(table)
            row = (
                self._scene.member_index.get(single.id)
                if single.kind is SelectionKind.MEMBER
                else self._scene.node_index.get(single.id)
            )
            item = table.item(row, 0) if row is not None else None
            if item is not None:
                table.scrollToItem(item)

    def _on_table_selection(self) -> None:
        """Una selección en una tabla sustituye a la anterior; con Ctrl o Mayús se suma."""
        if self._syncing or self._populating:
            return
        additive = bool(
            QApplication.keyboardModifiers()
            & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        )
        origin = self.sender()
        members = self._table_ids(self.members_table, self._scene.members)
        nodes = self._table_ids(self.nodes_table, self._scene.nodes)
        if not additive and origin is self.members_table:
            nodes = frozenset()
        elif not additive and origin is self.nodes_table:
            members = frozenset()
        self.select_set(SelectionSet(nodes, members))

    @staticmethod
    def _table_ids(table: QTableWidget, entities: tuple[Any, ...]) -> frozenset[UUID]:
        return frozenset(
            entities[i.row()].id
            for i in table.selectionModel().selectedRows()
            if i.row() < len(entities)
        )

    # --- Reconstrucción desde el modelo ----------------------------------------------------------

    def _on_session_changed(self) -> None:
        self._refresh(reset_camera=self.session.editor is not self._shown_editor)

    def _refresh(self, *, reset_camera: bool = False) -> None:
        """Reconstruye escena, tablas e inspector desde el modelo vigente (vistas derivadas)."""
        self._shown_editor = self.session.editor
        self._scene = build_scene(self.session.model.snapshot())
        self._populating = True
        try:
            self._fill(self.members_table, member_rows(self._scene), ())
            self._fill(self.nodes_table, node_rows(self._scene), NODE_EDITABLE_COLUMNS)
        finally:
            self._populating = False
        self.tabs.setTabText(0, f"Barras ({len(self._scene.members)})")
        self.tabs.setTabText(1, f"Nudos ({len(self._scene.nodes)})")
        self.viewport.set_scene(self._scene, reset_camera=reset_camera)
        self._selection = self._selection.pruned(self.session.model)
        self._syncing = True
        try:
            self.viewport.select_set(self._selection)
            self._show_in_tables()
        finally:
            self._syncing = False
        self._update_inspector()
        self._update_chrome()

    @staticmethod
    def _fill(table: QTableWidget, rows: tuple[Row, ...], editable: tuple[int, ...]) -> None:
        """Actualiza las celdas en el sitio (sin destruir la que se esté editando)."""
        table.setRowCount(len(rows))
        for row, values in enumerate(rows):
            for column, text in enumerate(values):
                item = table.item(row, column)
                if item is None:
                    item = QTableWidgetItem(text)
                    flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
                    if column in editable:
                        flags |= Qt.ItemFlag.ItemIsEditable
                    item.setFlags(flags)
                    table.setItem(row, column, item)
                elif item.text() != text:
                    item.setText(text)
        table.resizeColumnsToContents()

    def _update_chrome(self) -> None:
        path = self.session.path
        name = path.name if path is not None else UNTITLED
        mark = "*" if self.session.dirty else ""
        self.setWindowTitle(f"{mark}{name} — {APP_NAME}")
        undo_names = self.session.editor.undo_names  # el editor aún está avisando: no usar can_*
        self.act_undo.setEnabled(bool(undo_names))
        self.act_redo.setEnabled(bool(self.session.editor.redo_names))
        self.act_undo.setText(f"&Deshacer {undo_names[-1]}" if undo_names else "&Deshacer")
        redo_names = self.session.editor.redo_names
        self.act_redo.setText(f"&Rehacer {redo_names[-1]}" if redo_names else "&Rehacer")
        self.statusBar().showMessage(self._notice + DISCLAIMER)

    def _flush_listener_errors(self) -> None:
        """Muestra los avisos rotos de la sesión: la edición ya está hecha y no se revierte."""
        errors = self.session.take_listener_errors()
        if errors:
            self._notice = f"Aviso interno con error: {errors[0]} · "
            self.statusBar().showMessage(self._notice + DISCLAIMER)

    def _update_status(self) -> None:
        single = self._selection.single
        if single is None:
            self.statusBar().showMessage(self._notice + DISCLAIMER)
            return
        model = self.session.model
        if single.kind is SelectionKind.MEMBER:
            member = model.member(single.id)
            what = (
                f"Barra {entity_name(member.label, member.id)}: "
                "ejes locales x rojo, y verde, z azul"
            )
        else:
            node = model.node(single.id)
            what = f"Nudo {entity_name(node.label, node.id)}"
        self.statusBar().showMessage(f"{self._notice}{what} · {DISCLAIMER}")

    def _update_inspector(self) -> None:
        """Descripción de la selección (los campos editables se cambian con las acciones)."""
        selection, model = self._selection, self.session.model
        if not selection:
            self.inspector.setPlainText("Sin selección.")
            return
        if len(selection) > 1:
            self.inspector.setPlainText(
                f"{len(selection.nodes)} nudo(s) y {len(selection.members)} barra(s) seleccionados."
            )
            return
        single = selection.single
        assert single is not None
        if single.kind is SelectionKind.NODE:
            node = model.node(single.id)
            p = node.position
            self.inspector.setPlainText(
                f"Nudo {entity_name(node.label, node.id)}\nX = {p.x:.6g} m\nY = {p.y:.6g} m\n"
                f"Z = {p.z:.6g} m\nApoyo: {'sí' if model.support_at(node.id) else 'no'}"
            )
            return
        member = model.member(single.id)
        material = model.material(member.material)
        section = model.section(member.section)
        self.inspector.setPlainText(
            f"Barra {entity_name(member.label, member.id)}\n"
            f"Material: {material.name} (rev {material.revision})\n"
            f"Sección: {section.name} (rev {section.revision})\n"
            "Ejes locales: x rojo, y verde, z azul en el visor."
        )

    # --- Cierre ----------------------------------------------------------------------------------

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 (nombre de Qt)
        """Pide confirmación si hay cambios y libera VTK antes de cerrar."""
        if self._closed:
            super().closeEvent(event)
            return
        if not self._maybe_discard():
            event.ignore()
            return
        self._closed = True
        self.session.unsubscribe(self._on_session_changed)
        self.viewport.close_viewport()
        super().closeEvent(event)
