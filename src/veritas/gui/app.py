"""Arranque de la aplicación gráfica (plan T-011, E2): crea la ventana y el bucle de Qt.

Se importa solo desde ``veritas gui`` y requiere el extra opcional ``gui`` (ADR-008 §4).
"""

from PySide6.QtWidgets import QApplication

from veritas.core.geometry.snapshot import ModelSnapshot
from veritas.gui.main_window import MainWindow


def run_gui(snapshot: ModelSnapshot, source: str) -> int:
    """Abre la ventana con ``snapshot`` y bloquea hasta que se cierre; devuelve el código de Qt."""
    app = QApplication.instance() or QApplication([])
    window = MainWindow(snapshot, source)
    window.show()
    return int(app.exec())
