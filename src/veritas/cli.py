"""Interfaz de línea de comandos.

Contrato de frontera (ADR-001 §5, planes T-001 y T-002):

- ``veritas info``: informe en stdout, código 0.
- ``veritas --version``: versión en stdout, código 0.
- ``veritas`` sin argumentos o ``--help``: ayuda en stdout, código 0.
- ``veritas units list|profiles|convert``: tablas o conversión en stdout, código 0.
- ``veritas geometry axes``: base local de una barra I -> J (ADR-001 §1) y estados
  de sus extremos I/J en stdout, código 0 (plan T-003).
- ``veritas section rect``: propiedades de una sección rectangular maciza en stdout,
  código 0 (plan T-004).
- ``veritas model demo``: modelo fijo de tres barras con materiales distintos, una
  sección por barra, masa lineal y extremos I/J en stdout, código 0 (plan T-004).
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
from veritas.core.connections.end import Dof, EndConnection
from veritas.core.diagnostics import Diagnostic, DiagnosticCode, DiagnosticError, InputTypeError
from veritas.core.geometry.entities import Member
from veritas.core.geometry.local_axes import ReferenceSource, compute_local_axes
from veritas.core.geometry.model import StructuralModel
from veritas.core.geometry.vector import Vec3
from veritas.core.materials.material import MaterialFamily, MaterialRevision
from veritas.core.messages_es import render_message
from veritas.core.presentation import (
    BUILTIN_PROFILES,
    PresentationProfile,
    QuantityKind,
    format_quantity,
)
from veritas.core.sections.properties import SectionProperty
from veritas.core.sections.section import SectionRevision

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
    ("Materiales y secciones", f"{AVAILABLE} (T-004)"),
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

    profile_names = [profile.name for profile in BUILTIN_PROFILES]
    section_parser = subparsers.add_parser("section", help="Secciones.")
    section_sub = section_parser.add_subparsers(
        dest="section_command", metavar="ACCIÓN", required=True
    )
    rect = section_sub.add_parser("rect", help="Propiedades de una sección rectangular maciza.")
    rect.add_argument("--b", required=True, help="Ancho b, según y local.")
    rect.add_argument("--h", required=True, help="Canto h, según z local.")
    rect.add_argument("--unit", default="m", help="Unidad de b y h (m, cm, mm).")
    rect.add_argument("--profile", default="SI", choices=profile_names, help="Perfil de unidades.")

    model_parser = subparsers.add_parser("model", help="Modelos de demostración.")
    model_sub = model_parser.add_subparsers(dest="model_command", metavar="ACCIÓN", required=True)
    demo = model_sub.add_parser(
        "demo", help="Tres barras con materiales distintos y una sección por barra (sin cargas)."
    )
    demo.add_argument("--profile", default="SI", choices=profile_names, help="Perfil de unidades.")
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


def _end_lines(end_i: EndConnection, end_j: EndConnection) -> list[str]:
    lines = []
    for name, end in (("I", end_i), ("J", end_j)):
        states = " ".join(f"{dof}={end.state(dof)}" for dof in Dof)
        lines.append(f"Extremo {name}: {states}")
    return lines


def format_member_axes(
    point_i: str, point_j: str, unit: str, reference: str | None, alpha: str | None
) -> str:
    """Describe la base local de una barra I -> J y sus extremos rígidos por defecto.

    Solo geometría (sin material ni sección): usa la misma construcción que
    ``StructuralModel.add_member`` (ADR-001 §1) sin crear una barra.
    """
    length_unit = u.get_unit(unit)
    positions = [
        Vec3.from_lengths(*(u.Quantity.of(c, length_unit) for c in _parse_triple(text, field)))
        for text, field in ((point_i, "nudo I"), (point_j, "nudo J"))
    ]
    axes = compute_local_axes(
        positions[0],
        positions[1],
        reference=(
            None if reference is None else Vec3.direction(_parse_triple(reference, "referencia"))
        ),
        alpha=0.0 if alpha is None else _parse_alpha(alpha).require(u.ANGLE).si_value,
    )
    orientation = axes.orientation
    lines = [
        f"Barra I {_fmt_vec(positions[0])} m -> J {_fmt_vec(positions[1])} m",
        f"L  = {_fmt(axes.length)} m",
        f"ex = {_fmt_vec(axes.ex)}",
        f"ey = {_fmt_vec(axes.ey)}",
        f"ez = {_fmt_vec(axes.ez)}",
        f"Referencia de +z: {_fmt_vec(orientation.reference)} "
        f"({_SOURCE_LABELS[orientation.source]})",
        f"Giro alfa: {_fmt(orientation.alpha)} rad ({_fmt(math.degrees(orientation.alpha))} deg)",
        *_end_lines(EndConnection.rigid(), EndConnection.rigid()),
    ]
    return "\n".join(lines)


def _profile(name: str) -> PresentationProfile:
    return next(profile for profile in BUILTIN_PROFILES if profile.name == name)


_PROPERTY_ROWS: Final = (
    ("A", "area", QuantityKind.AREA),
    ("yc", "centroid_y", QuantityKind.LENGTH),
    ("zc", "centroid_z", QuantityKind.LENGTH),
    ("Iy", "iy", QuantityKind.SECOND_MOMENT),
    ("Iz", "iz", QuantityKind.SECOND_MOMENT),
    ("Iyz", "iyz", QuantityKind.SECOND_MOMENT),
    ("J", "torsion_constant", QuantityKind.SECOND_MOMENT),
)


def _fmt_property(prop: SectionProperty, kind: QuantityKind, profile: PresentationProfile) -> str:
    quantity = prop.quantity
    return "no disponible" if quantity is None else format_quantity(quantity, kind, profile)


def format_section_rect(b: str, h: str, unit: str, profile_name: str) -> str:
    """Propiedades de una sección rectangular b×h con estado y método."""
    length_unit = u.get_unit(unit)
    model = StructuralModel()
    section = model.add_rectangular_section(
        b=u.Quantity.of(_parse_number(b), length_unit),
        h=u.Quantity.of(_parse_number(h), length_unit),
    )
    profile = _profile(profile_name)
    params = section.parameters
    lines = [
        "Sección rectangular maciza "
        f"b = {format_quantity(u.Quantity(params.b, u.LENGTH), QuantityKind.LENGTH, profile)} (y)"
        f" × h = {format_quantity(u.Quantity(params.h, u.LENGTH), QuantityKind.LENGTH, profile)}"
        " (z)",
        "Sistema de referencia: origen en el centro del rectángulo, ejes y/z locales de la "
        "barra; Iy, Iz, Iyz centroidales (ADR-001 §1).",
    ]
    rows = []
    for label, attribute, kind in _PROPERTY_ROWS:
        prop: SectionProperty = getattr(section.properties, attribute)
        rows.append((label, _fmt_property(prop, kind, profile), str(prop.status), prop.method))
    lines.append(_table(("Propiedad", "Valor", "Estado", "Método"), rows))
    return "\n".join(lines)


_DEMO_EDITION: Final = "edición a confirmar"
_DEMO_NOTE: Final = (
    "Valores de material de EJEMPLO con fuente citada; no son un catálogo verificado. "
    "Contrástelos con la fuente antes de usarlos."
)


def _demo_model() -> tuple[StructuralModel, list[Member]]:
    model = StructuralModel()

    def m(value: float) -> u.Quantity:
        return u.Quantity.of(value, "m")

    def mpa(value: float) -> u.Quantity:
        return u.Quantity.of(value, "N/mm²")

    steel = model.add_material(
        name="Acero estructural (ejemplo)",
        family=MaterialFamily.STEEL,
        source="CTE DB-SE-A §4.2",
        standard_edition=_DEMO_EDITION,
        elastic_modulus=mpa(210_000),
        shear_modulus=mpa(81_000),
        poisson_ratio=0.3,
        density=u.Quantity.of(7850, "kg/m³"),
    )
    aluminium = model.add_material(
        name="Aluminio, valores comunes (ejemplo)",
        family=MaterialFamily.ALUMINIUM,
        source="UNE-EN 1999-1-1 §3.2.5",
        standard_edition=_DEMO_EDITION,
        elastic_modulus=mpa(70_000),
        shear_modulus=mpa(27_000),
        poisson_ratio=0.3,
        density=u.Quantity.of(2700, "kg/m³"),
    )
    timber = model.add_material(
        name="Madera C24, E0,mean / G_mean / ρ_mean (ejemplo)",
        family=MaterialFamily.TIMBER,
        source="UNE-EN 338; CTE DB-SE-M Anejo E",
        standard_edition=_DEMO_EDITION,
        elastic_modulus=mpa(11_000),
        shear_modulus=mpa(690),
        density=u.Quantity.of(420, "kg/m³"),
    )
    sections = [
        model.add_rectangular_section(b=m(0.2), h=m(0.4), name="R 200×400"),
        model.add_rectangular_section(b=m(0.1), h=m(0.2), name="R 100×200"),
        model.add_rectangular_section(b=m(0.16), h=m(0.24), name="R 160×240"),
    ]
    a = model.add_node(m(0), m(0), m(0), label="N1")
    b = model.add_node(m(4), m(0), m(0), label="N2")
    c = model.add_node(m(4), m(3), m(0), label="N3")
    d = model.add_node(m(4), m(3), m(3), label="N4")
    members = [
        model.add_member(start.id, end.id, material=material.ref, section=section.ref, label=label)
        for start, end, material, section, label in (
            (a, b, steel, sections[0], "B1"),
            (b, c, aluminium, sections[1], "B2"),
            (c, d, timber, sections[2], "B3"),
        )
    ]
    return model, members


def _material_label(material: MaterialRevision) -> str:
    return f"{material.name} [{material.family}] rev {material.revision}"


def _section_label(section: SectionRevision) -> str:
    return f"{section.name} rev {section.revision}"


def format_model_demo(profile_name: str) -> str:
    """Modelo fijo de tres barras: materiales, secciones, masa lineal y extremos."""
    profile = _profile(profile_name)
    model, members = _demo_model()
    lines = [_DEMO_NOTE, "", "Materiales:"]
    seen: list[MaterialRevision] = []
    for member in members:
        material = model.material(member.material)
        if material not in seen:
            seen.append(material)
    for material in seen:
        nu = "-" if material.poisson_ratio is None else f"{material.poisson_ratio:g}"
        check = (
            " · verificación normativa NO_COMPROBADA"
            if material.family is MaterialFamily.ALUMINIUM
            else ""
        )
        lines.append(
            f"  {_material_label(material)}: "
            f"E = {format_quantity(u.Quantity(material.elastic_modulus, u.STRESS), QuantityKind.STRESS, profile)}, "  # noqa: E501
            f"G = {format_quantity(u.Quantity(material.shear_modulus, u.STRESS), QuantityKind.STRESS, profile)}, "  # noqa: E501
            f"ν = {nu}, "
            f"ρ = {format_quantity(u.Quantity(material.density, u.DENSITY), QuantityKind.DENSITY, profile)}"  # noqa: E501
        )
        lines.append(f"    Fuente: {material.source} ({material.standard_edition}){check}")
    lines.extend(["", "Barras:"])
    rows = []
    for member in members:
        axes = model.axes(member.id)
        section = model.section(member.section)
        props = section.properties
        node_i, node_j = model.node(member.node_i), model.node(member.node_j)
        rows.append(
            (
                member.label,
                f"{node_i.label}->{node_j.label}",
                f"{_fmt(axes.length)} m",
                f"{model.material(member.material).family} rev {member.material.revision}",
                _section_label(section),
                _fmt_property(props.area, QuantityKind.AREA, profile),
                _fmt_property(props.iy, QuantityKind.SECOND_MOMENT, profile),
                _fmt_property(props.iz, QuantityKind.SECOND_MOMENT, profile),
                _fmt_property(props.torsion_constant, QuantityKind.SECOND_MOMENT, profile),
                format_quantity(model.linear_mass(member.id), QuantityKind.LINEAR_MASS, profile),
            )
        )
    lines.append(
        _table(("Barra", "I->J", "L", "Material", "Sección", "A", "Iy", "Iz", "J", "m"), rows)
    )
    lines.append("")
    for member in members:
        lines.append(f"{member.label}:")
        lines.extend(f"  {line}" for line in _end_lines(member.end_i, member.end_j))
    steel_ref = members[0].material
    revised = model.revise_material(
        steel_ref.entity_id, elastic_modulus=u.Quantity.of(200_000, "N/mm²")
    )
    kept = model.member(members[0].id).material
    lines.extend(
        [
            "",
            f"Revisión del acero: se crea la revisión {revised.revision} (E = 200000 N/mm²); "
            f"la barra {members[0].label} conserva la revisión {kept.revision}.",
            f"model_revision = {model.model_revision}",
        ]
    )
    return "\n".join(lines)


def _tolerant_streams() -> None:
    # Una salida canalizada en Windows usa la codificación local (p. ej. cp1252), sin
    # símbolos como «⁴». Se sustituyen en lugar de abortar con UnicodeEncodeError.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")


def _run(  # noqa: PLR0911 (un retorno por subcomando)
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> str | None:
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
    if args.command == "section":
        return format_section_rect(args.b, args.h, args.unit, args.profile)
    if args.command == "model":
        return format_model_demo(args.profile)
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
