"""Interfaz de línea de comandos informativa.

Contrato de frontera (ADR-001 §5, plan T-001):

- ``veritas info``: informe en stdout, código 0.
- ``veritas --version``: versión en stdout, código 0.
- ``veritas`` sin argumentos o ``--help``: ayuda en stdout, código 0.
- Subcomando u opción desconocidos: uso y diagnóstico en stderr, stdout vacío, código 2.

Esta CLI no ejecuta cálculos ni emite códigos de diagnóstico de dominio.
"""

import argparse
import platform
import sys
from collections.abc import Sequence
from typing import Final

from veritas import PRODUCT_NAME, __version__

EXPERIMENTAL_WARNING: Final = (
    "AVISO: aplicación experimental. No apta para justificar obras reales "
    "sin validación independiente por profesionales competentes."
)

EXIT_OK: Final = 0
EXIT_USAGE: Final = 2  # Código que emite argparse ante un uso incorrecto.

PENDING: Final = "PENDIENTE"
NOT_CHECKED: Final = "NO_COMPROBADA"

# Estado declarado de cada módulo. Ninguno está implementado en T-001; no se simula
# funcionamiento.
MODULE_STATUS: Final[tuple[tuple[str, str], ...]] = (
    ("Unidades SI", PENDING),
    ("Geometría y barras", PENDING),
    ("Materiales y secciones", PENDING),
    ("Análisis FEM", PENDING),
    ("Interfaz gráfica", PENDING),
    ("Verificación normativa", NOT_CHECKED),
)


def build_parser() -> argparse.ArgumentParser:
    """Construye el parser de la CLI."""
    parser = argparse.ArgumentParser(
        prog="veritas",
        description=f"{PRODUCT_NAME} (experimental).",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"veritas {__version__}",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMANDO")
    subparsers.add_parser("info", help="Muestra versión, entorno y estado de los módulos.")
    return parser


def format_info() -> str:
    """Devuelve el informe de ``veritas info``."""
    width = max(len(name) for name, _ in MODULE_STATUS) + 2
    lines = [
        f"{PRODUCT_NAME} {__version__}",
        f"Python {platform.python_version()} ({platform.python_implementation()})"
        f" · {platform.platform()}",
        EXPERIMENTAL_WARNING,
        "",
        "Estado de módulos:",
    ]
    lines.extend(f"  {name.ljust(width, '.')} {status}" for name, status in MODULE_STATUS)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada. Devuelve el código de salida del proceso."""
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "info":
        print(format_info())
        return EXIT_OK
    parser.print_help(sys.stdout)
    return EXIT_OK
