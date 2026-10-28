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
- ``veritas support dofs``: seis GDL de un apoyo (preset, fijos, resortes; base global o
  inclinada) en stdout, código 0 (plan T-005).
- ``veritas support demo``: barra con apoyo articulado con resorte en Ry y rodillo
  inclinado, traza de ``model_revision``, código 0 (plan T-005). La estabilidad y los
  mecanismos no se comprueban y se dice expresamente.
- ``veritas load selfweight``: peso propio q = ρ·A·g de una barra prismática rectangular
  y su resultante, código 0 (plan T-006).
- ``veritas load demo``: dos barras, caso G1 con peso propio y caso Q1 con carga nodal y
  cargas uniformes locales; resultantes ΣF/ΣM de las acciones prescritas respecto al
  origen y traza de ``model_revision``, código 0 (plan T-006). No son resultados de
  análisis y se dice expresamente.
- ``veritas analyze demo [--caso viga|mecanismo|portico] [--timeout S] [--profile P]
  [--bruto]``: análisis plano XZ con OpenSeesPy en un proceso aislado (plan T-007).
  Imprime estado, procedencia, diagnósticos y, solo si la corrida es SUCCEEDED, tablas
  normalizadas a ADR-001 (plan T-008): desplazamientos, reacciones y fuerzas/esfuerzos de
  extremo; ``--bruto`` añade la tabla BRUTA del solver. Código 0 si SUCCEEDED; 3 si FAILED
  o si la normalización no supera sus postcondiciones (sin resultados).
- ``veritas analyze referencias [--caso T-FEM-001|…|T-FEM-004|todos] [--timeout S]
  [--profile P]``: contraste con las referencias analíticas T-FEM-001…004 (plan T-009):
  referencia, calculado, error relativo y tolerancia por magnitud. Código 0 si todas
  coinciden; 3 si alguna corrida falla, la normalización falla o alguna magnitud no
  coincide. «Coincide» no valida la aplicación.
- Subcomando u opción desconocidos: uso en stderr, stdout vacío, código 2.
- Error de dominio (diagnóstico): ``CÓDIGO: mensaje`` en stderr, stdout vacío, código 2.

Solo ``veritas analyze`` ejecuta un análisis estructural; sus valores están normalizados a
ADR-001. La formulación plana solo se ha contrastado con T-FEM-001…004
(``veritas analyze referencias``, T-009); ningún resultado está validado.
"""

import argparse
import io
import math
import platform
import re
import sys
from collections.abc import Sequence
from typing import Final
from uuid import UUID

from veritas import PRODUCT_NAME, __version__
from veritas.core import units as u
from veritas.core.connections.end import Dof, EndConnection
from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    DiagnosticError,
    InputTypeError,
    InputValueError,
)
from veritas.core.geometry.entities import Member
from veritas.core.geometry.local_axes import ReferenceSource, compute_local_axes
from veritas.core.geometry.model import StructuralModel
from veritas.core.geometry.vector import Vec3
from veritas.core.loads.load_case import LoadCategory
from veritas.core.loads.loads import (
    STANDARD_GRAVITY,
    LoadBasis,
    MemberUniformLoad,
    NodalLoad,
    self_weight_intensity,
    vector_quantities,
)
from veritas.core.materials.material import MaterialFamily, MaterialRevision
from veritas.core.messages_es import MESSAGES_ES, render_message, render_text
from veritas.core.presentation import (
    BUILTIN_PROFILES,
    PresentationProfile,
    QuantityKind,
    format_quantity,
)
from veritas.core.references import RevisionRef
from veritas.core.sections.properties import SectionProperty
from veritas.core.sections.section import SectionRevision
from veritas.core.supports.support import (
    BasisKind,
    Restraints,
    Support,
    SupportBasis,
    SupportDof,
    build_restraints,
    fixed_restraints,
    pinned_restraints,
    roller_restraints,
)
from veritas.core.validation import positive_si
from veritas.fem import (
    AnalysisConfiguration,
    AnalysisRun,
    ComponentStatus,
    PlanarResults,
    ReactionSource,
    RunStatus,
    Vector6,
    analyze_planar_xz,
    benchmarks,
    normalize_planar_xz,
)

EXPERIMENTAL_WARNING: Final = (
    "AVISO: aplicación experimental. No apta para justificar obras reales "
    "sin validación independiente por profesionales competentes."
)

EXIT_OK: Final = 0
EXIT_USAGE: Final = 2  # Código que emite argparse ante un uso incorrecto.
EXIT_DIAGNOSTIC: Final = 2  # Entrada rechazada con diagnóstico de dominio.
EXIT_ANALYSIS_FAILED: Final = 3  # Corrida de análisis FAILED: sin resultados (plan T-007).

AVAILABLE: Final = "DISPONIBLE"
PENDING: Final = "PENDIENTE"
NOT_CHECKED: Final = "NO_COMPROBADA"

# Estado declarado de cada módulo. No se simula funcionamiento de lo pendiente.
MODULE_STATUS: Final[tuple[tuple[str, str], ...]] = (
    ("Unidades SI", f"{AVAILABLE} (T-002)"),
    ("Geometría y barras", f"{AVAILABLE} (T-003)"),
    ("Materiales y secciones", f"{AVAILABLE} (T-004)"),
    ("Apoyos", f"{AVAILABLE} (T-005)"),
    ("Cargas", f"{AVAILABLE} (T-006)"),
    (
        "Análisis FEM",
        f"{AVAILABLE} (T-007): plano XZ, resultados normalizados (T-008), contrastados solo "
        "con T-FEM-001…004 (T-009), no validados",
    ),
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

    support_parser = subparsers.add_parser("support", help="Apoyos de nudo por GDL.")
    support_sub = support_parser.add_subparsers(
        dest="support_command", metavar="ACCIÓN", required=True
    )
    dofs = support_sub.add_parser("dofs", help="Seis GDL de un apoyo (sin juicio de estabilidad).")
    dofs.add_argument(
        "--preset", choices=("empotramiento", "articulacion", "rodillo"), help="Preset."
    )
    dofs.add_argument("--axis", default=None, help="Eje del rodillo: Ux, Uy o Uz (por defecto Uz).")
    dofs.add_argument("--fixed", default=None, help="GDL fijos separados por comas: Ux,Uz,Ry.")
    dofs.add_argument(
        "--spring",
        action="append",
        default=[],
        help='Resorte GDL=valor unidad, repetible: "Uz=5000 kN/m", "Ry=2000 kN*m/rad".',
    )
    dofs.add_argument("--base", default="global", help="Base del apoyo: global o inclinada.")
    dofs.add_argument("--x-dir", default=None, help="Base inclinada: dirección x' (X,Y,Z).")
    dofs.add_argument("--z-ref", default=None, help="Base inclinada: referencia de z' (X,Y,Z).")
    dofs.add_argument("--profile", default="SI", choices=profile_names, help="Perfil de unidades.")
    support_demo = support_sub.add_parser(
        "demo", help="Barra con apoyo articulado con resorte y rodillo inclinado (sin cargas)."
    )
    support_demo.add_argument(
        "--profile", default="SI", choices=profile_names, help="Perfil de unidades."
    )
    _add_load_parser(subparsers, profile_names)
    _add_analyze_parser(subparsers, profile_names)
    return parser


def _add_analyze_parser(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]", profile_names: list[str]
) -> None:
    analyze_parser = subparsers.add_parser("analyze", help="Análisis FEM plano XZ (OpenSeesPy).")
    analyze_sub = analyze_parser.add_subparsers(
        dest="analyze_command", metavar="ACCIÓN", required=True
    )
    analyze_demo = analyze_sub.add_parser(
        "demo", help="Voladizo (viga), mecanismo o pórtico; resultados normalizados a ADR-001."
    )
    analyze_demo.add_argument("--caso", default="viga", choices=["viga", "mecanismo", "portico"])
    analyze_demo.add_argument(
        "--timeout", default="60", help="Tiempo máximo de la corrida en s (por defecto 60)."
    )
    analyze_demo.add_argument(
        "--profile", default="SI", choices=profile_names, help="Perfil de unidades."
    )
    analyze_demo.add_argument(
        "--bruto", action="store_true", help="Añade la tabla BRUTA en la base del solver."
    )
    references = analyze_sub.add_parser(
        "referencias", help="Contraste con las referencias analíticas T-FEM-001…004 (T-009)."
    )
    references.add_argument(
        "--caso", default=REFERENCES_ALL, choices=[*benchmarks.CASES, REFERENCES_ALL]
    )
    references.add_argument(
        "--timeout", default="60", help="Tiempo máximo de cada corrida en s (por defecto 60)."
    )
    references.add_argument(
        "--profile", default="SI", choices=profile_names, help="Perfil de unidades."
    )


def _add_load_parser(
    subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]", profile_names: list[str]
) -> None:
    load_parser = subparsers.add_parser("load", help="Casos de carga y acciones prescritas.")
    load_sub = load_parser.add_subparsers(dest="load_command", metavar="ACCIÓN", required=True)
    selfweight = load_sub.add_parser(
        "selfweight", help="Peso propio de una barra prismática rectangular."
    )
    selfweight.add_argument("--b", required=True, help="Ancho b, según y local.")
    selfweight.add_argument("--h", required=True, help="Canto h, según z local.")
    selfweight.add_argument("--unit", default="m", help="Unidad de b y h (m, cm, mm).")
    selfweight.add_argument("--rho", required=True, help='Densidad con unidad: "7850 kg/m3".')
    selfweight.add_argument("--length", required=True, help='Longitud real: "2 m".')
    selfweight.add_argument(
        "--g", default=None, help='Gravedad con unidad (por defecto 9.80665 m/s2): "10 m/s2".'
    )
    selfweight.add_argument(
        "--profile", default="SI", choices=profile_names, help="Perfil de unidades."
    )
    load_demo = load_sub.add_parser(
        "demo", help="Casos G1 (peso propio) y Q1 con resultantes (sin análisis)."
    )
    load_demo.add_argument(
        "--profile", default="SI", choices=profile_names, help="Perfil de unidades."
    )


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


STABILITY_NOT_CHECKED: Final = "Estabilidad y mecanismos: NO COMPROBADOS (T-010)."
_SPRING_PATTERN: Final = re.compile(
    r"^\s*([A-Za-z]{2})\s*=\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[-+]?(?:nan|inf))"
    r"\s*([^\d\s.+-].*?)\s*$",  # La unidad no empieza por cifra: sin retroceso.
    re.IGNORECASE,
)
_DOF_BY_NAME: Final = {dof.label.lower(): dof for dof in SupportDof}


def _parse_dof(text: str) -> SupportDof:
    try:
        return _DOF_BY_NAME[text.strip().lower()]
    except KeyError:
        raise InputValueError(
            Diagnostic.create(
                DiagnosticCode.INPUT_OUT_OF_RANGE, "support.unknown_dof", {"dof": text.strip()}
            )
        ) from None


def _parse_spring(text: str) -> tuple[SupportDof, u.Quantity]:
    match = _SPRING_PATTERN.match(text)
    if match is None:
        raise InputTypeError(
            Diagnostic.create(
                DiagnosticCode.INPUT_INVALID_TYPE,
                "input.invalid_type",
                {"field": "--spring", "expected": "GDL=valor unidad", "received": repr(text)},
            )
        )
    dof = _parse_dof(match.group(1))
    return dof, u.Quantity.of(_parse_number(match.group(2)), match.group(3))


def _parse_basis(base: str, x_dir: str | None, z_ref: str | None) -> SupportBasis:
    name = base.strip().lower()
    if name == "global":
        return SupportBasis.GLOBAL
    if name == "inclinada" and x_dir is not None and z_ref is not None:
        return SupportBasis.inclined(_parse_triple(x_dir, "x_dir"), _parse_triple(z_ref, "z_ref"))
    raise InputValueError(
        Diagnostic.create(
            DiagnosticCode.UNSUPPORTED_CONFIGURATION, "support.unsupported_basis", {"basis": base}
        )
    )


def _dof_label(dof: SupportDof, basis: SupportBasis) -> str:
    return dof.label + ("'" if basis.kind is BasisKind.INCLINED else "")


def _basis_lines(basis: SupportBasis) -> list[str]:
    if basis.kind is BasisKind.GLOBAL:
        return ["Base: GLOBAL"]
    names = ("ex'", "ey'", "ez'")
    return [
        "Base: INCLINADA (filas de Q en base global; v_apoyo = Q v_global)",
        *(
            f"  {name} = (" + ", ".join(_fmt(c) for c in row) + ")"
            for name, row in zip(names, basis.matrix(), strict=True)
        ),
    ]


def _support_lines(
    restraints: Restraints, basis: SupportBasis, profile: PresentationProfile
) -> list[str]:
    rows = []
    for dof, restraint in zip(SupportDof, restraints, strict=True):
        stiffness = restraint.quantity(dof)
        kind = (
            QuantityKind.TRANSLATIONAL_STIFFNESS
            if dof.is_translation
            else QuantityKind.ROTATIONAL_STIFFNESS
        )
        shown = "-" if stiffness is None else format_quantity(stiffness, kind, profile)
        rows.append((_dof_label(dof, basis), str(restraint.kind), shown))
    return [*_basis_lines(basis), _table(("GDL", "Estado", "Rigidez"), rows)]


def _preset_restraints(preset: str, axis: str | None) -> Restraints:
    if preset == "empotramiento":
        return fixed_restraints()
    if preset == "articulacion":
        return pinned_restraints()
    return roller_restraints(SupportDof.UZ if axis is None else _parse_dof(axis))


def _custom_restraints(fixed_text: str | None, spring_texts: Sequence[str]) -> Restraints:
    names = [] if fixed_text is None else [n for n in fixed_text.split(",") if n.strip()]
    fixed = [_parse_dof(name) for name in names]
    springs: dict[SupportDof, u.Quantity] = {}
    for text in spring_texts:
        dof, stiffness = _parse_spring(text)
        if dof in springs:
            raise InputValueError(
                Diagnostic.create(
                    DiagnosticCode.INPUT_OUT_OF_RANGE, "support.duplicate_dof", {"dof": dof.label}
                )
            )
        springs[dof] = stiffness
    return build_restraints(fixed, springs)


def format_support_dofs(args: argparse.Namespace) -> str:
    """Seis GDL de un apoyo con su base; sin juicio de estabilidad."""
    profile = _profile(args.profile)
    if args.preset is not None:
        restraints = _preset_restraints(args.preset, args.axis)
    else:
        restraints = _custom_restraints(args.fixed, args.spring)
    basis = _parse_basis(args.base, args.x_dir, args.z_ref)
    return "\n".join([*_support_lines(restraints, basis, profile), "", STABILITY_NOT_CHECKED])


def _check_support_usage(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.preset is not None and (args.fixed is not None or args.spring):
        parser.error("--preset no se combina con --fixed ni con --spring")
    if args.preset is None and args.fixed is None and not args.spring:
        parser.error("indique --preset, --fixed o --spring")
    if args.axis is not None and args.preset != "rodillo":
        parser.error("--axis solo se usa con --preset rodillo")
    inclined = args.base.strip().lower() == "inclinada"
    if inclined and (args.x_dir is None or args.z_ref is None):
        parser.error("--base inclinada exige --x-dir y --z-ref")
    if not inclined and (args.x_dir is not None or args.z_ref is not None):
        parser.error("--x-dir y --z-ref solo se usan con --base inclinada")


_DEMO_ROLLER_ANGLE: Final = math.radians(30.0)


def _demo_hinge(stiffness_knm_per_rad: float) -> Restraints:
    return build_restraints(
        [SupportDof.UX, SupportDof.UY, SupportDof.UZ],
        {SupportDof.RY: u.Quantity.of(stiffness_knm_per_rad, "kN·m/rad")},
    )


def format_support_demo(profile_name: str) -> str:
    """Barra de 6 m: articulación con resorte en Ry (N1) y rodillo inclinado 30° (N2)."""
    profile = _profile(profile_name)
    model = StructuralModel()

    def m(value: float) -> u.Quantity:
        return u.Quantity.of(value, "m")

    steel = model.add_material(  # Mismo acero de ejemplo que ``veritas model demo``.
        name="Acero estructural (ejemplo)",
        family=MaterialFamily.STEEL,
        source="CTE DB-SE-A §4.2",
        standard_edition=_DEMO_EDITION,
        elastic_modulus=u.Quantity.of(210_000, "N/mm²"),
        shear_modulus=u.Quantity.of(81_000, "N/mm²"),
        poisson_ratio=0.3,
        density=u.Quantity.of(7850, "kg/m³"),
    )
    section = model.add_rectangular_section(b=m(0.2), h=m(0.4), name="R 200×400")
    node_i = model.add_node(m(0), m(0), m(0), label="N1")
    node_j = model.add_node(m(6), m(0), m(0), label="N2")
    model.add_member(node_i.id, node_j.id, material=steel.ref, section=section.ref, label="B1")
    trace = [f"Nudos y barra: model_revision = {model.model_revision}"]
    support_i = model.set_support(node_i.id, _demo_hinge(5000), label="A1")
    slope = SupportBasis.inclined(
        (math.cos(_DEMO_ROLLER_ANGLE), 0.0, math.sin(_DEMO_ROLLER_ANGLE)), (0.0, 0.0, 1.0)
    )
    support_j = model.set_support(node_j.id, roller_restraints(), basis=slope, label="A2")
    trace.append(f"Dos apoyos: model_revision = {model.model_revision}")
    lines = [
        _DEMO_NOTE,
        "",
        "Barra B1: N1 (0, 0, 0) m -> N2 (6, 0, 0) m, sin cargas.",
        "",
        f"Apoyo {support_i.label} en N1 (articulación con resorte rotacional en Ry):",
        *_support_lines(support_i.restraints, support_i.basis, profile),
        "",
        f"Apoyo {support_j.label} en N2 (rodillo: solo Uz' fijo, normal al plano de rodadura"
        " inclinado 30° en XZ):",
        *_support_lines(support_j.restraints, support_j.basis, profile),
        "",
    ]
    revised = model.set_support(node_i.id, _demo_hinge(8000))
    trace.append(
        f"Rigidez Ry de {revised.label} a 8000 kN·m/rad: revisión {revised.revision}, "
        f"model_revision = {model.model_revision}"
    )
    relabeled = model.relabel_support(node_i.id, "A1 resorte")
    trace.append(
        f"Solo etiqueta «{relabeled.label}»: revisión {relabeled.revision}, "
        f"model_revision = {model.model_revision}"
    )
    removed = model.remove_support(node_j.id)
    trace.append(f"Retirar {removed.label}: model_revision = {model.model_revision}")
    again: Support = model.set_support(node_j.id, roller_restraints(), basis=slope, label="A2")
    same_id = "mismo id" if again.id == removed.id else "id nuevo"
    trace.append(
        f"Volver a poner el rodillo: {same_id}, revisión {again.revision}, "
        f"model_revision = {model.model_revision}"
    )
    lines.extend(["Traza:", *(f"  {line}" for line in trace), "", STABILITY_NOT_CHECKED])
    return "\n".join(lines)


PRESCRIBED_NOT_ANALYSIS: Final = (
    "Resultantes de acciones PRESCRITAS (comprobación de entrada): no son resultados de "
    "análisis; reacciones y esfuerzos quedan para T-007 a T-009."
)
_QUANTITY_PATTERN: Final = re.compile(
    r"^\s*([-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[-+]?(?:nan|inf))"
    r"\s*([^\d\s.+-].*?)\s*$",  # La unidad no empieza por cifra: sin retroceso.
    re.IGNORECASE,
)


def _parse_quantity(text: str, field: str) -> u.Quantity:
    match = _QUANTITY_PATTERN.match(text)
    if match is None:
        raise InputTypeError(
            Diagnostic.create(
                DiagnosticCode.INPUT_INVALID_TYPE,
                "input.invalid_type",
                {"field": field, "expected": "valor con unidad", "received": repr(text)},
            )
        )
    return u.Quantity.of(_parse_number(match.group(1)), match.group(2))


def _fq(quantity: u.Quantity, kind: QuantityKind, profile: PresentationProfile) -> str:
    return format_quantity(quantity, kind, profile)


def _fq3(
    values: tuple[u.Quantity, u.Quantity, u.Quantity],
    kind: QuantityKind,
    profile: PresentationProfile,
) -> str:
    return "(" + "; ".join(_fq(value, kind, profile) for value in values) + ")"


def format_load_selfweight(  # noqa: PLR0913, PLR0917 (datos del prisma)
    b: str, h: str, unit: str, rho: str, length: str, gravity: str | None, profile_name: str
) -> str:
    """Peso propio de una barra prismática rectangular: q = ρ·A·g y resultante q·L."""
    profile = _profile(profile_name)
    length_unit = u.get_unit(unit)
    section = StructuralModel().add_rectangular_section(
        b=u.Quantity.of(_parse_number(b), length_unit),
        h=u.Quantity.of(_parse_number(h), length_unit),
    )
    density = _parse_quantity(rho, "--rho")
    positive_si(density, u.DENSITY, "--rho")
    span = _parse_quantity(length, "--length")
    positive_si(span, u.LENGTH, "--length")
    g = STANDARD_GRAVITY if gravity is None else _parse_quantity(gravity, "--g")
    area = section.properties.area.quantity
    if area is None:  # pragma: no cover (el rectángulo siempre tiene A)
        raise TypeError("sección rectangular sin área")
    q = self_weight_intensity((density * area).require(u.LINEAR_MASS), g)
    total = q * span
    return "\n".join(
        [
            "Peso propio de una barra prismática rectangular (dirección -Z global, por metro "
            "de longitud real de la barra; ADR-001 §1):",
            f"  A = {_fq(area, QuantityKind.AREA, profile)}",
            f"  rho = {_fq(density, QuantityKind.DENSITY, profile)}",
            f"  g = {_fq(g, QuantityKind.ACCELERATION, profile)}"
            + (" (por defecto: gravedad normal convencional)" if gravity is None else ""),
            f"  L = {_fq(span, QuantityKind.LENGTH, profile)}",
            f"  q = rho·A·g = {_fq(q, QuantityKind.FORCE_PER_LENGTH, profile)}"
            f"  ->  qz = {_fq(-q, QuantityKind.FORCE_PER_LENGTH, profile)}",
            f"  Resultante Fz = -q·L = {_fq(-total, QuantityKind.FORCE, profile)}",
            "",
            "Acción prescrita, no resultado de análisis.",
        ]
    )


_DEMO_GRAVITY: Final = u.Quantity.of(10.0, "m/s²")


def _load_rows(
    model: StructuralModel, case_id: UUID, names: dict[UUID, str], profile: PresentationProfile
) -> list[tuple[str, str, str, str]]:
    rows = []
    for load in model.loads_in_case(case_id):
        if isinstance(load, NodalLoad):
            rows.append(
                (
                    "nodal",
                    names[load.node_id],
                    "GLOBAL",
                    "F = "
                    + _fq3(vector_quantities(load.force, u.FORCE), QuantityKind.FORCE, profile)
                    + "  M = "
                    + _fq3(vector_quantities(load.moment, u.MOMENT), QuantityKind.MOMENT, profile),
                )
            )
        elif isinstance(load, MemberUniformLoad):
            q = vector_quantities(load.intensity, u.FORCE_PER_LENGTH)
            rows.append(
                (
                    "uniforme de barra",
                    names[load.member_id],
                    str(load.basis),
                    "q = " + _fq3(q, QuantityKind.FORCE_PER_LENGTH, profile),
                )
            )
        else:
            g = u.Quantity(load.gravity, u.ACCELERATION)
            rows.append(
                (
                    "peso propio",
                    "todas las barras",
                    "GLOBAL (-Z)",
                    f"g = {_fq(g, QuantityKind.ACCELERATION, profile)}",
                )
            )
    return rows


def format_load_demo(profile_name: str) -> str:
    """Dos barras, caso G1 (peso propio) y caso Q1 (carga nodal y cargas locales)."""
    profile = _profile(profile_name)
    model = StructuralModel()

    def m(value: float) -> u.Quantity:
        return u.Quantity.of(value, "m")

    material = model.add_material(
        name="Material sintético de demostración",
        family=MaterialFamily.STEEL,
        source="Sintético (plan T-006); sin valor normativo",
        elastic_modulus=u.Quantity.of(210, "GPa"),
        shear_modulus=u.Quantity.of(81, "GPa"),
        density=u.Quantity.of(1000, "kg/m³"),
    )
    section = model.add_rectangular_section(b=m(0.2), h=m(0.4), name="R 200×400")
    n1 = model.add_node(m(0), m(0), m(0), label="N1")
    n2 = model.add_node(m(4), m(0), m(0), label="N2")
    n3 = model.add_node(m(7), m(0), m(4), label="N3")
    b1 = model.add_member(n1.id, n2.id, material=material.ref, section=section.ref, label="B1")
    b2 = model.add_member(n2.id, n3.id, material=material.ref, section=section.ref, label="B2")
    trace = [f"Nudos, material, sección y barras: model_revision = {model.model_revision}"]
    g1 = model.add_load_case("G1", LoadCategory.PERMANENT, description="Peso propio")
    model.set_self_weight(g1.id, gravity=_DEMO_GRAVITY)
    trace.append(f"Caso G1 y peso propio: model_revision = {model.model_revision}")
    q1 = model.add_load_case("Q1", LoadCategory.VARIABLE, description="Sobrecarga de ejemplo")
    model.add_nodal_load(q1.id, n2.id, fz=u.Quantity.of(-10, "kN"))
    for member in (b1, b2):
        model.add_member_uniform_load(
            q1.id, member.id, basis=LoadBasis.LOCAL, qz=u.Quantity.of(-2, "kN/m")
        )
    trace.append(f"Caso Q1 y tres cargas: model_revision = {model.model_revision}")
    renamed = model.revise_load_case(q1.id, description="Sobrecarga de uso (ejemplo)")
    trace.append(
        f"Solo descripción de Q1: revisión {renamed.revision}, "
        f"model_revision = {model.model_revision}"
    )
    names = {n1.id: "N1", n2.id: "N2", n3.id: "N3", b1.id: "B1", b2.id: "B2"}
    lines = [
        "Material SINTÉTICO de demostración (rho = 1000 kg/m³) y g = 10 m/s² explícita, "
        "para obtener valores redondos; sin valor normativo.",
        "",
        "B1: N1 (0, 0, 0) m -> N2 (4, 0, 0) m;  B2: N2 -> N3 (7, 0, 4) m (L = 5 m).",
        f"Sección {section.name}; base local de B2: ez = "
        f"{_fmt_vec(model.axes(b2.id).ez)} (ADR-001 §1).",
    ]
    for case in model.load_cases.values():
        lines.extend(["", f"Caso {case.name} [{case.category}] - {case.description}"])
        lines.append(
            _table(("Carga", "Sobre", "Base", "Valor"), _load_rows(model, case.id, names, profile))
        )
    lines.extend(["", "Peso propio derivado (q = rho·A·g por metro de longitud real, global):"])
    for member in (b1, b2):
        sw = model.self_weight_load(member.id)
        lines.append(
            f"  {names[member.id]}: q = {_fq3(sw, QuantityKind.FORCE_PER_LENGTH, profile)}"
        )
    lines.extend(["", "Resultantes respecto al origen global:"])
    for case in model.load_cases.values():
        resultant = model.case_resultant(case.id)
        if resultant.load_count == 0:
            lines.append(f"  {case.name}: sin cargas")
            continue
        lines.append(
            f"  {case.name}: F total = {_fq3(resultant.force, QuantityKind.FORCE, profile)}"
            f"  M total = {_fq3(resultant.moment, QuantityKind.MOMENT, profile)}"
        )
    lines.extend(["", "Traza:", *(f"  {line}" for line in trace), "", PRESCRIBED_NOT_ANALYSIS])
    return "\n".join(lines)


ANALYZE_DEMO_P: Final = 10_000.0  # N, hacia -Z en la punta del voladizo.
ANALYZE_DEMO_L: Final = 2.0  # m
ANALYZE_DEMO_E: Final = 210_000.0  # N/mm²
NO_RESULTS_FAILED: Final = "Sin resultados: la corrida no es SUCCEEDED."
NO_RESULTS_NORMALIZATION: Final = (
    "Sin resultados: la normalización no superó sus comprobaciones (plan T-008)."
)
RAW_NOTE: Final = (
    "RESULTADOS BRUTOS: base del solver SOLVER_XZ_RAW (x₁ = X, x₂ = Z, θ = −Ry). "
    "NO normalizados a ADR-001 (T-008) ni validados."
)


PORTICO_Q: Final = 20_000.0  # N/m, hacia -Z sobre el dintel.
PORTICO_H: Final = 10_000.0  # N, en +X en la cabeza del pilar izquierdo.
PORTICO_K_RY: Final = 5e7  # N·m/rad, resorte de giro del rodillo.


def _analyze_demo_model(case: str) -> tuple[StructuralModel, UUID, dict[UUID, str], str]:
    model = StructuralModel()

    def m(value: float) -> u.Quantity:
        return u.Quantity.of(value, "m")

    steel = model.add_material(
        name="Acero estructural (ejemplo)",
        family=MaterialFamily.STEEL,
        source="CTE DB-SE-A §4.2",
        standard_edition=_DEMO_EDITION,
        elastic_modulus=u.Quantity.of(ANALYZE_DEMO_E, "N/mm²"),
        shear_modulus=u.Quantity.of(81_000, "N/mm²"),
        poisson_ratio=0.3,
        density=u.Quantity.of(7850, "kg/m³"),
    )
    section = model.add_rectangular_section(b=m(0.2), h=m(0.4), name="R 200×400")
    load_case = model.add_load_case("Q1", LoadCategory.VARIABLE)
    if case == "portico":
        description = _portico(model, steel.ref, section.ref, load_case.id)
    else:
        a = model.add_node(m(0), m(0), m(0), label="N1")
        b = model.add_node(m(ANALYZE_DEMO_L), m(0), m(0), label="N2")
        model.add_member(a.id, b.id, material=steel.ref, section=section.ref, label="B1")
        model.add_nodal_load(load_case.id, b.id, fz=u.Quantity.of(-ANALYZE_DEMO_P, "N"))
        if case == "viga":
            model.set_support(a.id, fixed_restraints(), label="Empotramiento")
            description = (
                "Voladizo B1 de 2 m en +X, R 200×400 de acero (E = 210 000 N/mm²), "
                "empotrado en N1, P = −10 kN en Z en N2."
            )
        else:
            model.set_support(a.id, roller_restraints(), label="Rodillo 1")
            model.set_support(b.id, roller_restraints(), label="Rodillo 2")
            description = (
                "Mecanismo: la misma barra sobre dos rodillos verticales, sin restricción en X."
            )
    labels = {node.id: node.label for node in model.nodes.values()}
    labels |= {member.id: member.label for member in model.members.values()}
    labels |= {support.id: support.label for support in model.supports.values()}
    return model, load_case.id, labels, description


def _portico(
    model: StructuralModel, material: RevisionRef, section: RevisionRef, case_id: UUID
) -> str:
    def node(x: float, z: float, label: str) -> UUID:
        m = u.Quantity.of
        return model.add_node(m(x, "m"), m(0, "m"), m(z, "m"), label=label).id

    n1, n2 = node(0, 0, "N1"), node(0, 3, "N2")
    n3, n4 = node(4, 3, "N3"), node(4, 0, "N4")
    for label, (i, j) in (("B1", (n1, n2)), ("B2", (n2, n3)), ("B3", (n3, n4))):
        model.add_member(i, j, material=material, section=section, label=label)
    beam = next(item for item in model.members.values() if item.label == "B2")
    model.set_support(n1, pinned_restraints(), label="Articulación")
    angle = math.radians(30)
    slope = SupportBasis.inclined(
        (math.cos(angle), 0.0, math.sin(angle)), (-math.sin(angle), 0.0, math.cos(angle))
    )
    roller = build_restraints(
        fixed=(SupportDof.UZ,),
        springs={SupportDof.RY: u.Quantity.of(PORTICO_K_RY, "N·m/rad")},
    )
    model.set_support(n4, roller, basis=slope, label="Rodillo 30°")
    model.add_member_uniform_load(case_id, beam.id, qz=u.Quantity.of(-PORTICO_Q, "N/m"))
    model.add_nodal_load(case_id, n2, fx=u.Quantity.of(PORTICO_H, "N"))
    return (
        "Pórtico N1-N2-N3-N4 (pilares de 3 m, dintel de 4 m), R 200×400 de acero. "
        "Articulación en N1; en N4, rodillo sobre plano a 30° (Uz′ fijo) con resorte de giro "
        "k = 5e7 N·m/rad. q = −20 kN/m en Z sobre B2 y H = +10 kN en X en N2."
    )


def _raw(value: float | None) -> str:
    return "—" if value is None else f"{value:.6e}"


def _run_lines(run: AnalysisRun, labels: dict[UUID, str]) -> list[str]:
    versions = " · ".join(f"{key} {value or '—'}" for key, value in run.versions.items())
    lines = [
        f"Estado: {run.status}",
        f"run_id: {run.run_id}",
        f"model_revision: {run.model_revision} · caso {run.case_id} rev {run.case_revision}",
        f"Versiones: {versions}",
        f"Inicio (UTC): {run.started_utc.isoformat(timespec='seconds')} · "
        f"duración {run.duration_s:.3f} s",
    ]
    if run.pivot is not None:
        node = "—" if run.pivot.node_id is None else labels.get(run.pivot.node_id, "—")
        pivot = "—" if run.pivot.min_pivot is None else f"{run.pivot.min_pivot:.3e}"
        lines.append(
            f"Salvaguarda de singularidad: {run.pivot.free_dofs} GDL libres, pivote escalado "
            f"mínimo {pivot} en {node} ({run.pivot.component or '—'})"
        )
    lines.append("Diagnósticos:" if run.diagnostics else "Diagnósticos: ninguno")
    lines.extend(f"  {d.severity} {d.code}: {render_message(d)}" for d in run.diagnostics)
    return lines


def _hypothesis_lines(hypotheses: Sequence[str]) -> list[str]:
    return ["Hipótesis:", *(f"  - {MESSAGES_ES.get(key, key)}" for key in hypotheses)]


def _raw_lines(run: AnalysisRun, labels: dict[UUID, str]) -> list[str]:
    if run.raw is None:
        return []
    rows = [
        [
            labels.get(n.node_id, str(n.node_id)),
            *(_raw(v) for v in n.displacement),
            *(_raw(v) for v in n.reaction),
        ]
        for n in run.raw.nodes
    ]
    header = ["Nudo", "u₁ [m]", "u₂ [m]", "θ [rad]", "R₁ [N]", "R₂ [N]", "M [N·m]"]
    return [
        RAW_NOTE,
        _table(header, rows),
        "(— = GDL no fijado en el nudo: reacción no aplicable o en un resorte, no es cero)",
    ]


NOT_APPLICABLE_MARK: Final = "—"
NOT_CALCULATED_MARK: Final = "n/c"
RESULTS_NOTE: Final = (
    "RESULTADOS normalizados a ADR-001 (Z arriba; reacción = acción del apoyo sobre la "
    "estructura; pI/pJ = acción del nudo sobre la barra en base local; N > 0 tracción). "
    "Formulación plana contrastada solo con T-FEM-001…004 (T-009); no validados."
)
MARKS_NOTE: Final = (
    f"({NOT_APPLICABLE_MARK} = GDL libre del apoyo: reacción no aplicable, no es cero; "
    f"{NOT_CALCULATED_MARK} = fuera del plano XZ: no calculado por el análisis plano. "
    "En un apoyo inclinado, RX y RZ son proyecciones de la reacción calculada: un 0 en ellas "
    "es un valor real. RY/MY = reacción; Ry = giro.)"
)
_REACTION_SOURCE_LABELS: Final = {
    ReactionSource.FIX: "fijo",
    ReactionSource.SPRING: "resorte",
    ReactionSource.PENALTY: "penalización",
}
_SI_SYMBOLS: Final = {
    QuantityKind.DISPLACEMENT: "m",
    QuantityKind.ANGLE: "rad",
    QuantityKind.FORCE: "N",
    QuantityKind.MOMENT: "N·m",
}
_DISPLACEMENT_KINDS: Final = (QuantityKind.DISPLACEMENT,) * 3 + (QuantityKind.ANGLE,) * 3
_FORCE_KINDS: Final = (QuantityKind.FORCE,) * 3 + (QuantityKind.MOMENT,) * 3


def _cells(
    vector: Vector6, kinds: Sequence[QuantityKind], profile: PresentationProfile
) -> list[str]:
    cells = []
    for component, kind in zip(vector.components, kinds, strict=True):
        if component.value is None:
            cells.append(
                NOT_APPLICABLE_MARK
                if component.status is ComponentStatus.NOT_APPLICABLE
                else NOT_CALCULATED_MARK
            )
        else:
            quantity = u.Quantity.of(component.value, _SI_SYMBOLS[kind])
            cells.append(format_quantity(quantity, kind, profile))
    return cells


def _results_lines(
    results: PlanarResults, labels: dict[UUID, str], profile: PresentationProfile
) -> list[str]:
    def name(entity: UUID) -> str:
        return labels.get(entity, str(entity))

    lines = [RESULTS_NOTE, f"Perfil de unidades: {profile.name}", ""]
    rows = [
        [name(n.node_id), *_cells(n.displacement, _DISPLACEMENT_KINDS, profile)]
        for n in sorted(results.nodes, key=lambda n: name(n.node_id))
    ]
    lines += [
        "Desplazamientos y giros de nudo (base GLOBAL):",
        _table(["Nudo", "Ux", "Uy", "Uz", "Rx", "Ry", "Rz"], rows),
        "",
    ]
    if "fem.hypothesis_penalty" in results.hypotheses:
        lines += [
            "Nota: en un rodillo inclinado rígido (penalización) el desplazamiento en la "
            "dirección restringida es del orden de R/k_pen, no exactamente 0.",
            "",
        ]
    rows = [
        [name(r.support_id), name(r.node_id), *_cells(r.global_, _FORCE_KINDS, profile)]
        for r in sorted(results.reactions, key=lambda r: name(r.node_id))
    ]
    lines += [
        "Reacciones (base GLOBAL):",
        _table(["Apoyo", "Nudo", "RX", "RY", "RZ", "MX", "MY", "MZ"], rows),
    ]
    rows = [
        [
            name(r.support_id),
            *_cells(r.support_basis, _FORCE_KINDS, profile),
            ", ".join(
                f"{dof.label}′: {_REACTION_SOURCE_LABELS[source]}" for dof, source in r.sources
            ),
        ]
        for r in sorted(results.reactions, key=lambda r: name(r.node_id))
        if r.support_basis is not None
    ]
    if rows:
        lines += [
            "",
            "Reacciones en la base del apoyo inclinado (x′, y′, z′):",
            _table(["Apoyo", "Rx′", "Ry′", "Rz′", "Mx′", "My′", "Mz′", "Origen"], rows),
        ]
    rows = [
        [name(m.member_id), end, *_cells(vector, _FORCE_KINDS, profile)]
        for m in sorted(results.members, key=lambda m: name(m.member_id))
        for end, vector in (("I", m.p_i), ("J", m.p_j))
    ]
    lines += [
        "",
        "Fuerzas de extremo pI/pJ (acción del nudo sobre la barra, base LOCAL):",
        _table(["Barra", "Extremo", "Fx", "Fy", "Fz", "Mx", "My", "Mz"], rows),
    ]
    rows = [
        [name(m.member_id), cut, *_cells(vector, _FORCE_KINDS, profile)]
        for m in sorted(results.members, key=lambda m: name(m.member_id))
        for cut, vector in (("0+", m.section_start), ("L−", m.section_end))
    ]
    lines += [
        "",
        "Esfuerzos de sección en los extremos q(0+) = −pI, q(L−) = pJ (base LOCAL):",
        _table(["Barra", "Corte", "N", "Vy", "Vz", "T", "My", "Mz"], rows),
        MARKS_NOTE,
    ]
    return lines


def _reference_line(profile: PresentationProfile) -> str:
    ei = ANALYZE_DEMO_E * 1e6 * 0.2 * 0.4**3 / 12
    deflection = -ANALYZE_DEMO_P * ANALYZE_DEMO_L**3 / (3 * ei)
    shown = format_quantity(u.Quantity.of(deflection, "m"), QuantityKind.DISPLACEMENT, profile)
    return f"Referencia a mano: Uz punta = −PL³/(3EI) = {shown}"


def run_analyze_demo(
    case: str, timeout_text: str, profile_name: str = "SI", *, raw: bool = False
) -> tuple[str, int]:
    """Demo de ``veritas analyze``: (texto, código de salida 0 o 3)."""
    timeout = _parse_number(timeout_text)
    profile = _profile(profile_name)
    model, case_id, labels, description = _analyze_demo_model(case)
    run = analyze_planar_xz(model, AnalysisConfiguration(case_id, timeout_s=timeout))
    lines = [description, "", *_run_lines(run, labels)]
    code = EXIT_ANALYSIS_FAILED
    if run.status is not RunStatus.SUCCEEDED:
        lines += [*_hypothesis_lines(run.hypotheses), "", NO_RESULTS_FAILED]
    else:
        try:
            results = normalize_planar_xz(run, model)
        except DiagnosticError as error:
            lines += [
                *_hypothesis_lines(run.hypotheses),
                "",
                f"ERROR {error.code}: {render_message(error.diagnostic)}",
                NO_RESULTS_NORMALIZATION,
            ]
        else:
            code = EXIT_OK
            lines += [*_hypothesis_lines(results.hypotheses), ""]
            lines += _results_lines(results, labels, profile)
            if case == "viga":
                lines.append(_reference_line(profile))
    if raw:
        lines += ["", *_raw_lines(run, labels)]
    lines.extend(["", EXPERIMENTAL_WARNING])
    return "\n".join(lines), code


REFERENCES_ALL: Final = "todos"
REFERENCE_MATCH: Final = "coincide"
REFERENCE_MISMATCH: Final = "NO COINCIDE"
REFERENCE_NO_RESULTS: Final = "SIN RESULTADOS"  # Corrida o normalización fallida (D1-bis).
REFERENCES_NOTE: Final = (
    "«coincide» = |calculado − referencia| ≤ tolerancia · max(|referencia|, S), con S la "
    "mayor |referencia| de la misma magnitud en el caso. Coincidir con la referencia "
    "analítica dentro de la tolerancia no valida la aplicación."
)


def _text(text: benchmarks.Text) -> str:
    return render_text(text.key, dict(text.params))


def _reference_table(
    outcome: benchmarks.ReferenceOutcome, profile: PresentationProfile
) -> list[str]:
    def shown(check: benchmarks.ReferenceCheck, value: float | None) -> str:
        # Seis cifras significativas: los decimales fijos ocultan giros y desplazamientos
        # pequeños (p. ej. 0,001 rad). Un 0 solo aparece si el valor es exactamente 0.
        if value is None:
            return NOT_CALCULATED_MARK
        unit = profile.unit_for(check.kind)
        return f"{check.quantity_value(value).to(unit) + 0.0:.6g} {unit.symbol}"  # −0 → 0

    rows = [
        [
            check.quantity,
            check.formula,
            shown(check, check.reference),
            shown(check, check.computed),
            "—" if check.relative_error is None else f"{check.relative_error:.1e}",
            f"{check.tolerance:.0e}",
            REFERENCE_MATCH if check.matches else REFERENCE_MISMATCH,
        ]
        for check in outcome.checks
    ]
    header = ["Magnitud", "Fórmula", "Referencia", "Calculado", "Error rel.", "Tol.", "Resultado"]
    return _table(header, rows).splitlines()


def _reference_lines(
    outcome: benchmarks.ReferenceOutcome, profile: PresentationProfile
) -> list[str]:
    lines = [
        f"== {outcome.case_id} — {_text(outcome.title)} ==",
        _text(outcome.description),
        "",
    ]
    lines += _run_lines(outcome.run, outcome.labels)
    if outcome.run.status is not RunStatus.SUCCEEDED:
        return [*lines, NO_RESULTS_FAILED, f"{outcome.case_id}: {REFERENCE_NO_RESULTS}"]
    if outcome.failure is not None:
        error = outcome.failure
        return [
            *lines,
            f"ERROR {error.code}: {render_message(error.diagnostic)}",
            NO_RESULTS_NORMALIZATION,
            f"{outcome.case_id}: {REFERENCE_NO_RESULTS}",
        ]
    assert outcome.results is not None  # SUCCEEDED y normalizado.
    lines += [*_hypothesis_lines(outcome.results.hypotheses), ""]
    lines += _reference_table(outcome, profile)
    lines += [f"  {_text(note)}" for note in outcome.notes]
    matched = sum(check.matches for check in outcome.checks)
    verdict = REFERENCE_MATCH if outcome.matches else REFERENCE_MISMATCH
    lines.append(f"{outcome.case_id}: {matched}/{len(outcome.checks)} magnitudes, {verdict}.")
    return lines


def format_reference_outcomes(
    outcomes: Sequence[benchmarks.ReferenceOutcome], profile: PresentationProfile
) -> tuple[str, int]:
    """Texto del contraste y código de salida (0 si todo coincide; si no, 3)."""
    lines = ["Referencias analíticas (plan T-009):"]
    lines += [f"  - {_text(text)}" for text in benchmarks.HYPOTHESES]
    lines.append("")
    for outcome in outcomes:
        lines += [*_reference_lines(outcome, profile), ""]
    lines.append(REFERENCES_NOTE)
    failed = [outcome.case_id for outcome in outcomes if not outcome.matches]
    empty = [outcome.case_id for outcome in outcomes if outcome.results is None]
    mismatched = [case_id for case_id in failed if case_id not in empty]
    if empty:
        lines.append(f"{REFERENCE_NO_RESULTS}: {', '.join(empty)}.")
    if mismatched:
        lines.append(f"{REFERENCE_MISMATCH}: {', '.join(mismatched)}.")
    if not failed:
        count = len(outcomes)
        subject = "La variante coincide" if count == 1 else f"Las {count} variantes coinciden"
        lines.append(
            f"{subject} con su referencia analítica dentro de la tolerancia; no valida la "
            "aplicación."
        )
    lines.extend(["", EXPERIMENTAL_WARNING])
    return "\n".join(lines), EXIT_ANALYSIS_FAILED if failed else EXIT_OK


def run_analyze_references(
    case: str, timeout_text: str, profile_name: str = "SI"
) -> tuple[str, int]:
    """``veritas analyze referencias``: (texto, código de salida 0 o 3)."""
    timeout = _parse_number(timeout_text)
    profile = _profile(profile_name)
    cases = benchmarks.CASES if case == REFERENCES_ALL else (case,)
    outcomes = [outcome for item in cases for outcome in benchmarks.run_reference(item, timeout)]
    return format_reference_outcomes(outcomes, profile)


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
    if args.command == "support":
        if args.support_command == "dofs":
            _check_support_usage(args, parser)
            return format_support_dofs(args)
        return format_support_demo(args.profile)
    if args.command == "load":
        if args.load_command == "selfweight":
            return format_load_selfweight(
                args.b, args.h, args.unit, args.rho, args.length, args.g, args.profile
            )
        return format_load_demo(args.profile)
    parser.print_help(sys.stdout)
    return None


def main(argv: Sequence[str] | None = None) -> int:
    """Punto de entrada. Devuelve el código de salida del proceso."""
    _tolerant_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "analyze":
            if args.analyze_command == "referencias":
                text, code = run_analyze_references(args.caso, args.timeout, args.profile)
            else:
                text, code = run_analyze_demo(args.caso, args.timeout, args.profile, raw=args.bruto)
            print(text)
            return code
        output = _run(args, parser)
    except DiagnosticError as error:
        print(f"{error.code}: {render_message(error.diagnostic)}", file=sys.stderr)
        return EXIT_DIAGNOSTIC
    if output is not None:
        print(output)
    return EXIT_OK
