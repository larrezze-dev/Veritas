"""Editor paramétrico en la CLI (T-025 E2, D14-A; EDT-001, EDT-011, EDT-012).

- ``veritas section rect|i|rhs|shs|chs --dim nombre=valor… [--unidad mm]``: propiedades de la forma
  (valor, estado, método y, si no hay forma cerrada validada, el motivo de la ``NO_DISPONIBLE``).
  ``section rect`` (``--b/--h``) conserva su salida aceptada; el rectángulo del editor lleva además
  el área de cortante (D10-C).
- ``veritas section comparar FORMA --dim … --ref CAT/SERIE/DES[@REV] --db CAT.db [--tol 0.01]``:
  diferencia relativa con signo por propiedad frente al catálogo. Solo informa: no cambia estados.
- ``veritas project add-section ARCHIVO FORMA --dim … [--nombre N] [--contrastar REF --db CAT.db]``:
  añade el perfil al proyecto; con ``--contrastar`` registra CONTRASTADO en las propiedades dentro
  de tolerancia (el valor sigue siendo el de Veritas). Si ninguna está dentro: código 2 y nada se
  escribe. ``VERIFICADO`` nunca se emite.
- ``veritas project assign-section ARCHIVO (ETIQUETA | --todas) SECCIÓN [--parcial]``: reutiliza
  ``replace_sections`` (estricto todo o nada; ``--parcial`` solo a las compatibles).

Códigos de salida: 0 ejecutado; 2 diagnóstico (geometría inválida, uso, referencia no hallada,
sustitución estricta rechazada, contraste sin propiedades dentro de tolerancia).
"""

import argparse
from collections.abc import Sequence
from uuid import UUID

from veritas.cli_profiles import (
    EXIT_OK,
    EXIT_REJECTED,
    _member_ids,
    _member_lines,
    _rejection,
    _verdicts,
    read_snapshot,
)
from veritas.core import units as u
from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    InputValueError,
    Severity,
)
from veritas.core.geometry.entities import new_entity_id
from veritas.core.geometry.model import StructuralModel
from veritas.core.messages_es import render_message, render_text
from veritas.core.presentation import BUILTIN_PROFILES, QuantityKind, format_quantity
from veritas.core.sections.contrast import (
    DEFAULT_TOLERANCE,
    ComparisonReport,
    ContrastVerdict,
)
from veritas.core.sections.parametric import ParametricShape
from veritas.core.sections.properties import PropertyStatus, SectionProperty
from veritas.core.sections.section import SectionRevision, build_rectangular_section
from veritas.core.units import Quantity
from veritas.persistence.errors import value_error
from veritas.persistence.project import Project, save_project
from veritas.persistence.runs import Validity, validity
from veritas.section_editor.draft import SectionDraft

SHAPE_CHOICES = ("rect", "i", "rhs", "shs", "chs")
SECTION_EDITOR_COMMANDS = ("i", "rhs", "shs", "chs", "comparar")
PROJECT_EDITOR_COMMANDS = ("add-section", "assign-section")

_PROFILE = next(p for p in BUILTIN_PROFILES if p.name == "SI")
_ROWS = (
    ("A", "area", QuantityKind.AREA),
    ("yc", "centroid_y", QuantityKind.LENGTH),
    ("zc", "centroid_z", QuantityKind.LENGTH),
    ("Iy", "iy", QuantityKind.SECOND_MOMENT),
    ("Iz", "iz", QuantityKind.SECOND_MOMENT),
    ("Iyz", "iyz", QuantityKind.SECOND_MOMENT),
    ("J", "torsion_constant", QuantityKind.SECOND_MOMENT),
)
_COMPARED_LABEL = {"area": "A", "iy": "Iy", "iz": "Iz", "torsion_constant": "J"}


# --- Parsers ------------------------------------------------------------------------------


def _dim_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dim", nargs="+", required=True, metavar="NOMBRE=VALOR", help="Dimensiones: h=300 b=150…"
    )
    parser.add_argument(
        "--unidad", "--unit", dest="unidad", default="m", help="Unidad de longitud (m, cm, mm)."
    )


def add_section_parsers(section_sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Registra las formas del editor y ``comparar`` bajo ``veritas section``."""
    for shape in ("i", "rhs", "shs", "chs"):
        parser = section_sub.add_parser(shape, help=f"Propiedades de un perfil {shape.upper()}.")
        _dim_arguments(parser)
    compare = section_sub.add_parser(
        "comparar", help="Compara un perfil del editor con uno de catálogo (solo informa)."
    )
    compare.add_argument("shape", metavar="FORMA", choices=SHAPE_CHOICES)
    _dim_arguments(compare)
    compare.add_argument("--ref", required=True, metavar="CAT/SERIE/DES[@REV]")
    compare.add_argument("--db", required=True, metavar="RUTA", help="Catálogo SQLite (lectura).")
    compare.add_argument(
        "--tol", default=str(DEFAULT_TOLERANCE), help="Tolerancia relativa (0.01)."
    )


def add_project_section_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Registra ``add-section`` y ``assign-section`` bajo ``veritas project``."""
    add = sub.add_parser("add-section", help="Añade un perfil del editor al proyecto.")
    add.add_argument("file", metavar="ARCHIVO")
    add.add_argument("shape", metavar="FORMA", choices=SHAPE_CHOICES)
    _dim_arguments(add)
    add.add_argument("--nombre", metavar="TEXTO", help="Nombre de la sección (por defecto, auto).")
    add.add_argument("--contrastar", metavar="CAT/SERIE/DES[@REV]", help="Registra CONTRASTADO.")
    add.add_argument("--db", metavar="RUTA", help="Catálogo SQLite (obligatorio con --contrastar).")
    add.add_argument("--tol", default=str(DEFAULT_TOLERANCE), help="Tolerancia relativa (0.01).")
    assign = sub.add_parser("assign-section", help="Asigna una sección del proyecto a barras.")
    assign.add_argument("file", metavar="ARCHIVO")
    assign.add_argument("--todas", action="store_true", help="Todas las barras del proyecto.")
    assign.add_argument("--parcial", action="store_true", help="Solo a las barras compatibles.")
    assign.add_argument(
        "targets", nargs="+", metavar="[ETIQUETA] SECCIÓN", help="Barra y nombre de la sección."
    )


# --- Utilidades ---------------------------------------------------------------------------


def _usage(key: str, params: dict[str, str] | None = None) -> InputValueError:
    return InputValueError(Diagnostic.create(DiagnosticCode.INPUT_OUT_OF_RANGE, key, params))


def _parse_dimensions(texts: Sequence[str], unit: str) -> dict[str, Quantity]:
    length_unit = u.get_unit(unit)
    values: dict[str, Quantity] = {}
    for text in texts:
        name, separator, number = text.partition("=")
        try:
            value = float(number)
        except ValueError:
            value = float("nan")
        if not separator or not name or value != value:  # noqa: PLR0124 (NaN)
            raise _usage("editor.cli_dimension", {"text": text})
        if name in values:
            raise _usage(
                "editor.dimension_name",
                {"shape": "", "name": name, "problem": "repetida", "expected": ""},
            )
        values[name] = Quantity.of(value, length_unit)
    return values


def _parse_tolerance(text: str) -> float:
    try:
        return float(text)
    except ValueError:
        raise _usage("editor.compare_tolerance", {"value": text}) from None


def _fmt(prop: SectionProperty, kind: QuantityKind) -> str:
    quantity = prop.quantity
    return "no disponible" if quantity is None else format_quantity(quantity, kind, _PROFILE)


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    lines = [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        for row in [header, *rows]
    ]
    lines.insert(1, "  ".join("-" * width for width in widths))
    return "\n".join(lines)


def _build(
    shape: str, dimensions: dict[str, Quantity], *, section_id: UUID, name: str = ""
) -> SectionRevision:
    """Revisión (1) de la forma pedida; ``rect`` lleva el área de cortante (D10-C)."""
    if shape == "rect":
        if set(dimensions) != {"b", "h"}:
            raise _usage("editor.cli_rect_dimensions")
        return build_rectangular_section(
            section_id, 1, b=dimensions["b"], h=dimensions["h"], name=name, with_shear=True
        )
    if shape not in SHAPE_CHOICES:
        raise _usage("editor.cli_shape", {"shape": shape})
    draft = SectionDraft.of(ParametricShape(shape.upper()), dimensions)
    return draft.build(section_id, 1, name=name)


def _section_name(shape: str, texts: Sequence[str], unit: str) -> str:
    return f"{shape.upper()} {' '.join(texts)} {unit}"


def property_lines(section: SectionRevision) -> list[str]:
    """Líneas de estado de A, Iy, Iz, J y del cortante de una sección (sin catálogo)."""
    lines = []
    for label, attribute, kind in _ROWS:
        if attribute in ("centroid_y", "centroid_z", "iyz"):
            continue
        prop: SectionProperty = getattr(section.properties, attribute)
        lines.append(f"      {label}: {_fmt(prop, kind)} · {prop.status}")
    if section.shear is not None:
        for label, prop in (("Avy", section.shear.shear_y), ("Avz", section.shear.shear_z)):
            reason = f" ({prop.origin})" if prop.status is PropertyStatus.NO_DISPONIBLE else ""
            lines.append(f"      {label}: {_fmt(prop, QuantityKind.AREA)} · {prop.status}{reason}")
    else:
        lines.append("      Cortante (Avy, Avz): no calculado")
    return lines


def _properties_report(section: SectionRevision, title: str) -> str:
    rows = [
        (
            label,
            _fmt(getattr(section.properties, attr), kind),
            str(getattr(section.properties, attr).status),
            getattr(section.properties, attr).method,
        )
        for label, attr, kind in _ROWS
    ]
    lines = [
        title,
        "Sistema de referencia: origen en el centroide, ejes y/z locales de la barra; "
        "Iy, Iz, Iyz centroidales (ADR-001 §1).",
        _table(("Propiedad", "Valor", "Estado", "Método"), rows),
    ]
    if section.shear is not None:
        shear_rows = [
            (
                label,
                _fmt(prop, QuantityKind.AREA),
                str(prop.status),
                prop.method if prop.value is not None else f"{prop.method}: {prop.origin}",
            )
            for label, prop in (("Avy", section.shear.shear_y), ("Avz", section.shear.shear_z))
        ]
        lines.append("Área de cortante energética (corte recto; no es la de pared delgada):")
        lines.append(_table(("Propiedad", "Valor", "Estado", "Método"), shear_rows))
    return "\n".join(lines)


def _comparison_lines(report: ComparisonReport) -> list[str]:
    rows = []
    for item in report.items:
        label = _COMPARED_LABEL[item.name]
        kind = QuantityKind.AREA if item.name == "area" else QuantityKind.SECOND_MOMENT
        computed = (
            "no disponible"
            if item.computed is None
            else format_quantity(Quantity(item.computed, kind.dimension), kind, _PROFILE)
        )
        catalog = (
            "no disponible"
            if item.catalog is None
            else format_quantity(Quantity(item.catalog, kind.dimension), kind, _PROFILE)
        )
        difference = "—" if item.relative_difference is None else f"{item.relative_difference:+.4%}"
        verdict = str(item.verdict)
        if item.verdict is ContrastVerdict.NOT_COMPARABLE:
            side = "Veritas" if item.reason == "veritas" else "catálogo"
            verdict += f" (sin valor en {side}; no se rellena)"
        rows.append((label, computed, catalog, difference, verdict))
    lines = [
        f"Comparación con {report.snapshot_label} · tolerancia {report.tolerance:.4%} "
        "(parámetro de software, no normativo)",
        "Diferencia relativa con signo = (Veritas − catálogo) / catálogo.",
        _table(("Propiedad", "Veritas", "Catálogo", "Diferencia", "Dictamen"), rows),
    ]
    for key in report.notes:
        lines.append(
            "Aviso: "
            + render_message(
                Diagnostic.create(
                    DiagnosticCode.UNSUPPORTED_CONFIGURATION, key, severity=Severity.WARNING
                )
            )
        )
    return lines


# --- section ------------------------------------------------------------------------------


def run_section_command(args: argparse.Namespace) -> str:
    """``section i|rhs|shs|chs`` y ``section comparar``; el texto va a stdout."""
    dimensions = _parse_dimensions(args.dim, args.unidad)
    if args.section_command == "comparar":
        return _compare(args, dimensions)
    section = _build(args.section_command, dimensions, section_id=new_entity_id())
    title = f"Perfil {args.section_command.upper()}: {' '.join(args.dim)} {args.unidad}"
    return _properties_report(section, title)


def _compare(args: argparse.Namespace, dimensions: dict[str, Quantity]) -> str:
    tolerance = _parse_tolerance(args.tol)
    section = _build(args.shape, dimensions, section_id=new_entity_id())
    snapshot = read_snapshot(args.db, args.ref)
    model = StructuralModel()
    model.register_section(section)
    _, report = model.register_contrast(section.id, snapshot, tolerance=tolerance)
    return "\n".join(
        [
            f"Perfil {args.shape.upper()}: {' '.join(args.dim)} {args.unidad}",
            *_comparison_lines(report),
            "Solo informe: ningún estado cambia. Para registrar CONTRASTADO use "
            "«project add-section … --contrastar».",
        ]
    )


# --- project ------------------------------------------------------------------------------


def run_project_editor_command(args: argparse.Namespace, project: Project) -> tuple[str, int]:
    """``add-section`` o ``assign-section``."""
    if args.project_command == "add-section":
        return _add_section(args, project)
    return _assign_section(args, project)


def _save(args: argparse.Namespace, project: Project) -> list[str]:
    model = project.model
    saved = save_project(
        args.file, model, project.user_profiles, runs=project.runs, norms=project.norms
    )
    states = [validity(r, model) for r in saved.runs]
    return [
        f"Proyecto guardado: {args.file} · model_revision {model.model_revision}",
        f"  hash del modelo: {saved.model_hash}",
        f"  ejecuciones: {states.count(Validity.CURRENT)} CURRENT, "
        f"{states.count(Validity.STALE)} STALE",
    ]


def _add_section(args: argparse.Namespace, project: Project) -> tuple[str, int]:
    if (args.contrastar is None) != (args.db is None):
        raise _usage("editor.cli_contrast_usage")
    dimensions = _parse_dimensions(args.dim, args.unidad)
    name = args.nombre or _section_name(args.shape, args.dim, args.unidad)
    model = project.model
    section = model.register_section(
        _build(args.shape, dimensions, section_id=new_entity_id(), name=name)
    )
    lines = [f"Sección «{name}» añadida: {section.family} · rev {section.revision}"]
    if args.contrastar is not None:
        snapshot = read_snapshot(args.db, args.contrastar)
        section, report = model.register_contrast(
            section.id, snapshot, tolerance=_parse_tolerance(args.tol)
        )
        lines.extend(_comparison_lines(report))
        if section.revision == 1:
            lines.append(render_text("editor.cli_not_comparable"))
            lines.append("Nada escrito: el archivo no cambia.")
            return "\n".join(lines), EXIT_REJECTED
        lines.append(
            f"CONTRASTADO registrado en {', '.join(_COMPARED_LABEL[n] for n in report.within)} "
            f"(revisión {section.revision}); el valor sigue siendo el calculado por Veritas."
        )
    lines.extend(_save(args, project))
    lines.extend(["Propiedades de la sección:", *property_lines(section)])
    lines.extend(_verdicts(section))
    return "\n".join(lines), EXIT_OK


def _assign_section(args: argparse.Namespace, project: Project) -> tuple[str, int]:
    targets: list[str] = args.targets
    if args.todas:
        if len(targets) != 1:
            raise _usage("project.assign_section_usage")
        labels, name = None, targets[0]
    else:
        if len(targets) != 2:  # noqa: PLR2004 (etiqueta y sección)
            raise _usage("project.assign_section_usage")
        labels, name = [targets[0]], targets[1]
    model = project.model
    latest: dict[UUID, SectionRevision] = {}
    for revision in model.snapshot().sections:
        if revision.name == name and (
            revision.id not in latest or revision.revision > latest[revision.id].revision
        ):
            latest[revision.id] = revision
    if len(latest) != 1:
        raise value_error(
            DiagnosticCode.REFERENCE_NOT_FOUND,
            "editor.cli_section_not_found",
            {"name": f"{name} ({len(latest)} coincidencias)"},
        )
    section = next(iter(latest.values()))
    ids = _member_ids(project, labels)
    report = model.replace_sections(ids, section.ref, partial=args.parcial)
    mode = "parcial" if report.partial else "estricto (todo o nada)"
    lines = [
        f"Sección «{name}» · {section.family} · rev {section.revision}",
        f"Sustitución en modo {mode}: {len(report.members)} barra(s) seleccionada(s)",
        *_member_lines(report),
    ]
    if report.rejected and (not args.parcial or not report.applied):
        lines.append(_rejection("partial" if args.parcial else "strict"))
        lines.extend(_verdicts(section))
        return "\n".join(lines), EXIT_REJECTED
    if report.changed:
        lines.extend(_save(args, project))
    else:
        lines.append("Sin cambios: ninguna barra necesitaba la sustitución; el archivo no se toca.")
    lines.extend(_verdicts(section))
    return "\n".join(lines), EXIT_OK
