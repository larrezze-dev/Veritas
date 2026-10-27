"""Interfaz de línea de comandos.

Contrato de frontera (ADR-001 §5, planes T-001 y T-002):

- ``veritas info``: informe en stdout, código 0.
- ``veritas --version``: versión en stdout, código 0.
- ``veritas`` sin argumentos o ``--help``: ayuda en stdout, código 0.
- ``veritas units list|profiles|convert``: tablas o conversión en stdout, código 0.
- Subcomando u opción desconocidos: uso en stderr, stdout vacío, código 2.
- Error de dominio (diagnóstico): ``CÓDIGO: mensaje`` en stderr, stdout vacío, código 2.

Esta CLI no ejecuta cálculos estructurales.
"""

import argparse
import io
import platform
import sys
from collections.abc import Sequence
from typing import Final

from veritas import PRODUCT_NAME, __version__
from veritas.core import units as u
from veritas.core.diagnostics import Diagnostic, DiagnosticCode, DiagnosticError, InputTypeError
from veritas.core.messages_es import render_message
from veritas.core.presentation import BUILTIN_PROFILES, QuantityKind

EXPERIMENTAL_WARNING: Final = (
    "AVISO: aplicación experimental. No apta para justificar obras reales "
    "sin validación independiente por profesionales competentes."
)

EXIT_OK: Final = 0
EXIT_USAGE: Final = 2  # Código que emite argparse ante un uso incorrecto.
EXIT_DIAGNOSTIC: Final = 2  # Entrada rechazada con diagnóstico de dominio.

AVAILABLE: Final = "DISPONIBLE"
PENDING: Final = "PENDIENTE"
NOT_CHECKED: Final = "NO_COMPROBADA"

# Estado declarado de cada módulo. No se simula funcionamiento de lo pendiente.
MODULE_STATUS: Final[tuple[tuple[str, str], ...]] = (
    ("Unidades SI", f"{AVAILABLE} (T-002)"),
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

    units_parser = subparsers.add_parser("units", help="Unidades y conversión.")
    units_sub = units_parser.add_subparsers(dest="units_command", metavar="ACCIÓN", required=True)
    units_sub.add_parser("list", help="Magnitudes, dimensiones y unidades registradas.")
    units_sub.add_parser("profiles", help="Perfiles de presentación predefinidos.")
    convert = units_sub.add_parser("convert", help="Convierte un valor entre unidades.")
    convert.add_argument("value", help="Valor numérico (punto decimal).")
    convert.add_argument("unit", help="Unidad de origen (p. ej. kN*m, mm4, N/mm2).")
    convert.add_argument("--to", required=True, dest="target", help="Unidad de destino.")
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


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    lines = [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        for row in [header, *rows]
    ]
    lines.insert(1, "  ".join("-" * width for width in widths))
    return "\n".join(lines)


def format_units_list() -> str:
    """Tabla de magnitudes con su dimensión y unidades registradas."""
    rows = [
        (
            kind.label,
            str(kind.dimension),
            ", ".join(unit.symbol for unit in u.units_with_dimension(kind.dimension)),
        )
        for kind in QuantityKind
    ]
    return _table(("Magnitud", "Dimensión", "Unidades"), rows)


def format_profiles() -> str:
    """Tabla de los perfiles de presentación predefinidos."""
    header = ("Magnitud", *(profile.name for profile in BUILTIN_PROFILES))
    rows = [
        (kind.label, *(profile.unit_for(kind).symbol for profile in BUILTIN_PROFILES))
        for kind in QuantityKind
    ]
    return _table(header, rows)


def _parse_number(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        raise InputTypeError(
            Diagnostic.create(
                DiagnosticCode.INPUT_INVALID_TYPE,
                "input.invalid_type",
                {"field": "valor", "expected": "número", "received": repr(text)},
            )
        ) from None


def format_conversion(value_text: str, source: str, target: str) -> str:
    """Convierte y devuelve ``"<v> <origen> = <w> <destino>"``."""
    source_unit = u.get_unit(source)
    target_unit = u.get_unit(target)
    number = _parse_number(value_text)
    converted = u.Quantity.of(number, source_unit).to(target_unit)
    return f"{number:.15g} {source_unit.symbol} = {converted:.15g} {target_unit.symbol}"


def _tolerant_streams() -> None:
    # Una salida canalizada en Windows usa la codificación local (p. ej. cp1252), sin
    # símbolos como «⁴». Se sustituyen en lugar de abortar con UnicodeEncodeError.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")


def _run(args: argparse.Namespace, parser: argparse.ArgumentParser) -> str | None:
    if args.command == "info":
        return format_info()
    if args.command == "units":
        if args.units_command == "list":
            return format_units_list()
        if args.units_command == "profiles":
            return format_profiles()
        return format_conversion(args.value, args.unit, args.target)
    parser.print_help(sys.stdout)
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada. Devuelve el código de salida del proceso."""
    _tolerant_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        output = _run(args, parser)
    except DiagnosticError as error:
        print(f"{error.code}: {render_message(error.diagnostic)}", file=sys.stderr)
        return EXIT_DIAGNOSTIC
    if output is not None:
        print(output)
    return EXIT_OK
