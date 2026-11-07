"""Arranque de la aplicación gráfica (plan T-011, E2; T-030 E2b): crea la ventana y el bucle de Qt.

Se importa solo desde ``veritas gui`` y requiere el extra opcional ``gui`` (ADR-008 §4).
"""

from PySide6.QtWidgets import QApplication

from veritas.gui.main_window import MainWindow
from veritas.gui.session import EditSession


def run_gui(session: EditSession) -> int:
    """Abre la ventana editable sobre ``session`` y bloquea hasta que se cierre."""
    app = QApplication.instance() or QApplication([])
    window = MainWindow(session)
    window.show()
    return int(app.exec())
