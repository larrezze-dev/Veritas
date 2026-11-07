"""Interfaz gráfica PySide6 y visor PyVista (plan T-011; editable desde T-030 E2b).

- Sin Qt ni PyVista (se prueban en toda la suite): ``scene`` (escena derivada, E1), ``selection``,
  ``picking`` y ``tables`` (E2); ``session``, ``forms`` y ``deletion`` (T-030 E2b: sesión de
  edición, formularios y borrado con vista previa).
- Con el extra opcional ``gui``: ``viewport`` (PyVista), ``main_window`` y ``dialogs`` (PySide6)
  y ``app``.
  ``veritas gui`` los importa solo al abrir la ventana (ADR-008 §4).
"""

import os

# pyvistaqt/qtpy eligen el enlace Qt al importarse; el extra ``gui`` instala solo PySide6.
os.environ.setdefault("QT_API", "pyside6")
