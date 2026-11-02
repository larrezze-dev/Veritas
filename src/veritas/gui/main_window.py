"""Ventana principal: visor 3D y tablas de barras y nudos sincronizados (plan T-011, E2).

Solo lectura: las tablas no son editables y nada vuelve al dominio. La selección es única (un
nudo o una barra) y se refleja a la vez en el visor y en la tabla correspondiente, sea cual sea
el origen del cambio. Requiere el extra opcional ``gui``.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QMainWindow,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
)

from veritas.core.geometry.snapshot import ModelSnapshot
from veritas.gui.scene import Scene, build_scene
from veritas.gui.selection import Selection, SelectionKind
from veritas.gui.tables import MEMBER_HEADERS, NODE_HEADERS, Row, member_rows, node_rows
from veritas.gui.viewport import Viewport

DISCLAIMER = (
    "Visor de solo lectura · aplicación experimental, no apta para justificar obras reales "
    "sin validación independiente"
)


def _read_only_table(headers: tuple[str, ...], rows: tuple[Row, ...]) -> QTableWidget:
    table = QTableWidget(len(rows), len(headers))
    table.setHorizontalHeaderLabels(list(headers))
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.verticalHeader().setVisible(False)
    for row, values in enumerate(rows):
        for column, text in enumerate(values):
            item = QTableWidgetItem(text)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
            table.setItem(row, column, item)
    table.resizeColumnsToContents()
    return table


class MainWindow(QMainWindow):
    """Visor + tablas sobre un ``ModelSnapshot``; el modelo no se modifica."""

    def __init__(self, snapshot: ModelSnapshot, source: str = "modelo de demostración") -> None:
        super().__init__()
        self._scene: Scene = build_scene(snapshot)
        self._syncing = False
        self.setWindowTitle(f"Veritas Structural Calculation 3D — {source}")
        self.resize(1200, 700)

        self.viewport = Viewport(self)
        self.members_table = _read_only_table(MEMBER_HEADERS, member_rows(self._scene))
        self.nodes_table = _read_only_table(NODE_HEADERS, node_rows(self._scene))
        self.tabs = QTabWidget()
        self.tabs.addTab(self.members_table, f"Barras ({len(self._scene.members)})")
        self.tabs.addTab(self.nodes_table, f"Nudos ({len(self._scene.nodes)})")
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.addWidget(self.viewport)
        splitter.addWidget(self.tabs)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        self.setCentralWidget(splitter)
        self.statusBar().showMessage(DISCLAIMER)

        self.viewport.set_scene(self._scene)
        self.viewport.selection_changed.connect(self.select)
        self.members_table.itemSelectionChanged.connect(self._on_members_changed)
        self.nodes_table.itemSelectionChanged.connect(self._on_nodes_changed)

    @property
    def scene(self) -> Scene:
        """Escena mostrada (derivada del modelo)."""
        return self._scene

    @property
    def selection(self) -> Selection | None:
        """Selección actual, la misma en el visor y en las tablas."""
        return self.viewport.selection

    def select(self, selection: Selection | None) -> None:
        """Fija la selección en el visor y en la tabla correspondiente (y limpia la otra)."""
        if self._syncing:
            return
        self._syncing = True
        try:
            self.viewport.select(selection)
            self._show_in_tables(self.viewport.selection)
            self._update_status(self.viewport.selection)
        finally:
            self._syncing = False

    def _show_in_tables(self, selection: Selection | None) -> None:
        for table in (self.members_table, self.nodes_table):
            table.clearSelection()
        if selection is None:
            return
        if selection.kind is SelectionKind.MEMBER:
            table, row = self.members_table, self._scene.member_index[selection.id]
            self.tabs.setCurrentWidget(table)
        else:
            table, row = self.nodes_table, self._scene.node_index[selection.id]
            self.tabs.setCurrentWidget(table)
        table.selectRow(row)
        item = table.item(row, 0)
        if item is not None:
            table.scrollToItem(item)

    def _update_status(self, selection: Selection | None) -> None:
        if selection is None:
            self.statusBar().showMessage(DISCLAIMER)
            return
        if selection.kind is SelectionKind.MEMBER:
            member = self._scene.members[self._scene.member_index[selection.id]]
            what = (
                f"Barra {member.label or str(member.id)[:8]}: ejes locales x rojo, y verde, z azul"
            )
        else:
            node = self._scene.nodes[self._scene.node_index[selection.id]]
            what = f"Nudo {node.label or str(node.id)[:8]}"
        self.statusBar().showMessage(f"{what} · {DISCLAIMER}")

    def _on_members_changed(self) -> None:
        self._on_table_changed(self.members_table, SelectionKind.MEMBER)

    def _on_nodes_changed(self) -> None:
        self._on_table_changed(self.nodes_table, SelectionKind.NODE)

    def _on_table_changed(self, table: QTableWidget, kind: SelectionKind) -> None:
        if self._syncing:
            return
        rows = table.selectionModel().selectedRows()
        if not rows:
            return
        row = rows[0].row()
        entity = (
            self._scene.members[row] if kind is SelectionKind.MEMBER else self._scene.nodes[row]
        )
        self.select(Selection(kind, entity.id))

    def closeEvent(self, event: object) -> None:  # noqa: N802 (nombre de Qt)
        """Libera VTK antes de cerrar para no dejar el contexto OpenGL colgando."""
        self.viewport.close_viewport()
        super().closeEvent(event)
