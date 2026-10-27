"""Interfaz de línea de comandos.

Contrato de frontera (ADR-001 §5, planes T-001 y T-002):

- ``veritas info``: informe en stdout, código 0.
- ``veritas --version``: versión en stdout, código 0.
- ``veritas`` sin argumentos o ``--help``: ayuda en stdout, código 0.
- ``veritas units list|profiles|convert``: tablas o conversión en stdout, código 0.
- ``veritas geometry axes``: base local de una barra I -> J (ADR-001 §1) y estados
  de sus extremos I/J en stdout, código 0 (plan T-003).
- Subcomando u opción desconocidos: uso en stderr, stdout vacío, código 2.
- Error de dominio (diagnóstico): ``CÓDIGO: mensaje`` en stderr, stdout vacío, código 2.

Esta CLI no ejecuta análisis estructurales.
"""

import argparse
import io
import math
import platform
import re
import sys
from collections.abc import Sequence
from typing import Final

from veritas import PRODUCT_NAME, __version__
from veritas.core import units as u
from veritas.core.connections.end import Dof
from veritas.core.diagnostics import Diagnostic, DiagnosticCode, DiagnosticError, InputTypeError
from veritas.core.geometry.local_axes import ReferenceSource
from veritas.core.geometry.model import StructuralModel
from veritas.core.geometry.vector import Vec3
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
    ("Geometría y barras", f"{AVAILABLE} (T-003)"),
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

    geometry_parser = subparsers.add_parser("geometry", help="Geometría de barras.")
    geometry_sub = geometry_parser.add_subparsers(
        dest="geometry_command", metavar="ACCIÓN", required=True
    )
    axes = geometry_sub.add_parser("axes", help="Base local de una barra I -> J (ADR-001 §1).")
    axes.add_argument(
        "--i", required=True, dest="point_i", help="Nudo I: X,Y,Z (con negativos: --i=-1,0,0)."
    )
    axes.add_argument(
        "--j", required=True, dest="point_j", help="Nudo J: X,Y,Z (con negativos: --j=-1,0,0)."
    )
    axes.add_argument("--unit", default="m", help="Unidad de las coordenadas (m, cm, mm).")
    axes.add_argument(
        "--ref",
        default=None,
        help="Dirección local +z (adimensional): X,Y,Z; con negativos --ref=0,0,-1.",
    )
    axes.add_argument("--alpha", default=None, help="Giro alrededor de ex con unidad: 90deg.")
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


_SOURCE_LABELS: Final[dict[ReferenceSource, str]] = {
    ReferenceSource.DEFAULT_Z: "por defecto, +Z global",
    ReferenceSource.FALLBACK_NEG_X: "fallback -X global (barra vertical)",
    ReferenceSource.EXPLICIT: "explícita",
}
_ALPHA_PATTERN: Final = re.compile(
    r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?)\s*([a-zA-Z]+)\s*$"
)
_PRESENTATION_ZERO: Final = 5e-13  # Residuo de redondeo que se muestra como 0.


def _parse_triple(text: str, field: str) -> tuple[float, float, float]:
    parts = text.split(",")
    if len(parts) != len("xyz"):
        raise InputTypeError(
            Diagnostic.create(
                DiagnosticCode.INPUT_INVALID_TYPE,
                "input.invalid_type",
                {"field": field, "expected": "X,Y,Z", "received": repr(text)},
            )
        )
    x, y, z = (_parse_number(part) for part in parts)
    return x, y, z


def _parse_alpha(text: str) -> u.Quantity:
    match = _ALPHA_PATTERN.match(text)
    if match is None:
        raise InputTypeError(
            Diagnostic.create(
                DiagnosticCode.INPUT_INVALID_TYPE,
                "input.invalid_type",
                {"field": "alfa", "expected": "valor con unidad (90deg)", "received": repr(text)},
            )
        )
    return u.Quantity.of(_parse_number(match.group(1)), match.group(2))


def _fmt(value: float) -> str:
    # Solo presentación: el residuo de redondeo y -0 se muestran como 0.
    shown = 0.0 if abs(value) < _PRESENTATION_ZERO else value
    return f"{shown:.10g}"


def _fmt_vec(vector: Vec3) -> str:
    return "(" + ", ".join(_fmt(c) for c in vector.components()) + ")"


def format_member_axes(
    point_i: str, point_j: str, unit: str, reference: str | None, alpha: str | None
) -> str:
    """Crea los dos nudos y la barra en un modelo temporal y describe su base local."""
    length_unit = u.get_unit(unit)
    model = StructuralModel()
    nodes = [
        model.add_node(*(u.Quantity.of(c, length_unit) for c in _parse_triple(text, field)))
        for text, field in ((point_i, "nudo I"), (point_j, "nudo J"))
    ]
    member = model.add_member(
        nodes[0].id,
        nodes[1].id,
        reference=None if reference is None else _parse_triple(reference, "referencia"),
        alpha=None if alpha is None else _parse_alpha(alpha),
    )
    axes = model.axes(member.id)
    orientation = axes.orientation
    lines = [
        f"Barra I {_fmt_vec(nodes[0].position)} m -> J {_fmt_vec(nodes[1].position)} m",
        f"L  = {_fmt(axes.length)} m",
        f"ex = {_fmt_vec(axes.ex)}",
        f"ey = {_fmt_vec(axes.ey)}",
        f"ez = {_fmt_vec(axes.ez)}",
        f"Referencia de +z: {_fmt_vec(orientation.reference)} "
        f"({_SOURCE_LABELS[orientation.source]})",
        f"Giro alfa: {_fmt(orientation.alpha)} rad ({_fmt(math.degrees(orientation.alpha))} deg)",
    ]
    for name, end in (("I", member.end_i), ("J", member.end_j)):
        states = " ".join(f"{dof}={end.state(dof)}" for dof in Dof)
        lines.append(f"Extremo {name}: {states}")
    return "\n".join(lines)


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
    if args.command == "geometry":
        return format_member_axes(args.point_i, args.point_j, args.unit, args.ref, args.alpha)
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
