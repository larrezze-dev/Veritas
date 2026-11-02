"""Interfaz gráfica PySide6 y visor PyVista: solo lectura sobre el dominio (plan T-011).

- Sin Qt ni PyVista (se prueban en toda la suite): ``scene`` (escena derivada, E1), ``selection``,
  ``picking`` y ``tables`` (E2).
- Con el extra opcional ``gui``: ``viewport`` (PyVista), ``main_window`` (PySide6) y ``app``.
  ``veritas gui`` los importa solo al abrir la ventana (ADR-008 §4).
"""

import os

# pyvistaqt/qtpy eligen el enlace Qt al importarse; el extra ``gui`` instala solo PySide6.
os.environ.setdefault("QT_API", "pyside6")
