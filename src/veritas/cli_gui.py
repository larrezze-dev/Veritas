"""Comando ``veritas gui [ARCHIVO]`` (plan T-011, E2; ADR-008).

Abre la ventana de solo lectura con el visor 3D y las tablas de barras y nudos. Sin ARCHIVO
muestra el modelo de demostración «portico» (el de ``veritas project demo --caso portico``).

- Código 0: la ventana se abrió y se cerró.
- Código 2: falta el extra opcional ``gui`` (mensaje en stderr con la orden de instalación) o el
  proyecto es inválido (diagnóstico de dominio).
- Código 3: el proyecto no supera la integridad.

Qt y PyVista se importan solo dentro de ``run_gui_command``: sin el extra el resto de la CLI no
se ve afectado. El visor no calcula nada ni modifica el archivo.
"""

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Final

from veritas.core.messages_es import render_message
from veritas.persistence.errors import IntegrityError
from veritas.persistence.project import open_project

EXIT_OK: Final = 0
EXIT_MISSING_EXTRA: Final = 2
EXIT_INTEGRITY: Final = 3

GUI_MODULES: Final = ("PySide6", "pyvista", "pyvistaqt")
INSTALL_HINT: Final = "uv sync --locked --extra gui"


def add_gui_parser(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Registra ``veritas gui``."""
    parser = subparsers.add_parser(
        "gui", help="Ventana de solo lectura: visor 3D y tablas de un proyecto (T-011)."
    )
    parser.add_argument(
        "file", nargs="?", metavar="ARCHIVO", help="Proyecto .veritas (modelo demo si se omite)."
    )


def missing_modules() -> list[str]:
    """Módulos del extra ``gui`` que no están instalados."""
    return [name for name in GUI_MODULES if importlib.util.find_spec(name) is None]


def run_gui_command(args: argparse.Namespace) -> int:
    """Ejecuta ``veritas gui``; devuelve el código de salida del proceso."""
    missing = missing_modules()
    if missing:
        print(
            f"GUI_NO_DISPONIBLE: faltan {', '.join(missing)} del extra opcional «gui». "
            f"Instálalo con: {INSTALL_HINT}",
            file=sys.stderr,
        )
        return EXIT_MISSING_EXTRA
    if args.file is None:
        from veritas.cli_project import _demo_model  # noqa: PLC0415 (evita ciclo)

        snapshot = _demo_model("portico").snapshot()
        source = "modelo de demostración (pórtico)"
    else:
        try:
            snapshot = open_project(Path(args.file)).model.snapshot()
        except IntegrityError as error:
            print(f"{error.code}: {render_message(error.diagnostic)}", file=sys.stderr)
            return EXIT_INTEGRITY
        source = str(args.file)
    from veritas.gui.app import run_gui  # noqa: PLC0415 (importación diferida, ADR-008 §4)

    return run_gui(snapshot, source)
