"""Perfiles de catálogo en el proyecto: ``veritas project assign-profile|replace-profile|sections``
y el aviso de «desactualizado» de ``verify --db`` (plan T-024 E2; ADR-010; D5, D6 y D8).

- ``assign-profile ARCHIVO (ETIQUETA | --todas) CAT/SERIE/DES[@REV] --db CAT.db [--parcial]``
- ``replace-profile ARCHIVO REFERENCIA (--barras E1 E2… | --todas) --db CAT.db [--parcial]``
- ``sections ARCHIVO [--db CAT.db]``: cada sección con sus dos dictámenes por separado.

El catálogo se lee solo en el momento de asignar; el proyecto guarda el snapshot y se abre sin él.
Modo estricto por defecto (todo o nada: código 2 y nada escrito); con ``--parcial`` se aplica solo
a las barras compatibles y el informe lista siempre las bloqueadas y las omitidas. Los avisos de
vigencia solo informan: no modifican el proyecto.
"""

import argparse
from pathlib import Path
from uuid import UUID

from veritas.catalogs.bridge import (
    Freshness,
    compare_with_catalog,
    ensure_section,
    snapshot_from_profile,
)
from veritas.catalogs.errors import CatalogError, catalog_error
from veritas.catalogs.model import CatalogInfo, CatalogProfile
from veritas.catalogs.query import ProfileRef, parse_reference
from veritas.catalogs.store import CatalogStore
from veritas.core.diagnostics import Diagnostic, DiagnosticCode, DiagnosticError, Severity
from veritas.core.geometry.replacement import ReplacementReport, ReplacementStatus
from veritas.core.messages_es import render_message
from veritas.core.sections.catalog import CatalogSnapshot
from veritas.core.sections.properties import PropertyStatus
from veritas.core.sections.readiness import assess_section
from veritas.core.sections.section import SectionFamily, SectionRevision
from veritas.persistence.errors import value_error
from veritas.persistence.project import Project, save_project
from veritas.persistence.runs import Validity, validity

EXIT_OK = 0
EXIT_REJECTED = 2

PROFILE_COMMANDS = ("assign-profile", "replace-profile", "sections")

_STATUS_TEXT = {
    ReplacementStatus.APPLIED: "aplicada(s)",
    ReplacementStatus.UNCHANGED: "sin cambio",
    ReplacementStatus.OMITTED: "omitida(s)",
    ReplacementStatus.BLOCKED: "bloqueada(s)",
}

_FRESHNESS_TEXT = {
    Freshness.CURRENT: "vigente: coincide con la última revisión del catálogo",
    Freshness.NEWER_REVISION: "el catálogo tiene una revisión posterior de este perfil",
    Freshness.CONTENT_DIFFERS: "el catálogo tiene un contenido distinto para esta revisión",
    Freshness.MISSING: "el catálogo no contiene este perfil",
}


def add_profile_parsers(sub: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Registra las acciones de perfiles bajo ``veritas project``."""

    def common(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("file", metavar="ARCHIVO")
        parser.add_argument(
            "--db",
            required=True,
            metavar="RUTA",
            help="Archivo SQLite del catálogo (solo lectura).",
        )
        parser.add_argument(
            "--parcial",
            action="store_true",
            help="Aplica solo a las barras compatibles; el informe lista las omitidas.",
        )

    assign = sub.add_parser(
        "assign-profile", help="Asigna un perfil de catálogo a una barra (o a todas)."
    )
    common(assign)
    assign.add_argument("--todas", action="store_true", help="Todas las barras del proyecto.")
    assign.add_argument(
        "targets", nargs="+", metavar="[ETIQUETA] REFERENCIA", help="Barra y CAT/SERIE/DES[@REV]."
    )
    replace = sub.add_parser(
        "replace-profile", help="Sustituye la sección de varias barras por un perfil de catálogo."
    )
    common(replace)
    replace.add_argument("--todas", action="store_true", help="Todas las barras del proyecto.")
    replace.add_argument("--barras", nargs="+", metavar="ETIQUETA", help="Barras a sustituir.")
    replace.add_argument("reference", metavar="REFERENCIA", help="CAT/SERIE/DES[@REV].")
    sections = sub.add_parser(
        "sections", help="Lista las secciones del proyecto con sus dictámenes FEM y de norma."
    )
    sections.add_argument("file", metavar="ARCHIVO")
    sections.add_argument(
        "--db", metavar="RUTA", help="Catálogo con el que comparar la vigencia (informativo)."
    )


def run_profile_command(args: argparse.Namespace, project: Project) -> tuple[str, int]:
    """Ejecuta ``assign-profile``, ``replace-profile`` o ``sections``."""
    if args.project_command == "sections":
        return _sections(args, project), EXIT_OK
    if args.project_command == "assign-profile":
        labels, reference = _assign_targets(args)
    else:
        labels, reference = _replace_targets(args)
    return _replace(args, project, labels, reference)


# --- Selección ----------------------------------------------------------------------------


def _usage(key: str) -> DiagnosticError:
    return value_error(DiagnosticCode.INPUT_OUT_OF_RANGE, key)


def _assign_targets(args: argparse.Namespace) -> tuple[list[str] | None, str]:
    targets: list[str] = args.targets
    if args.todas:
        if len(targets) != 1:
            raise _usage("project.assign_usage")
        return None, targets[0]
    if len(targets) != 2:  # noqa: PLR2004 (etiqueta y referencia)
        raise _usage("project.assign_usage")
    return [targets[0]], targets[1]


def _replace_targets(args: argparse.Namespace) -> tuple[list[str] | None, str]:
    if args.todas == bool(args.barras):  # exactamente una de las dos selecciones
        raise _usage("project.replace_usage")
    return (None if args.todas else list(args.barras)), args.reference


def _member_ids(project: Project, labels: list[str] | None) -> list[UUID]:
    members = project.model.members
    if labels is None:
        return sorted(members, key=lambda mid: (members[mid].label, str(mid)))
    ids: list[UUID] = []
    for label in labels:
        matches = [mid for mid, m in members.items() if m.label == label]
        if len(matches) != 1:
            raise value_error(
                DiagnosticCode.REFERENCE_NOT_FOUND,
                "model.reference_not_found",
                {"kind": "barra", "entity_id": f"{label} ({len(matches)} coincidencias)"},
            )
        ids.append(matches[0])
    return ids


# --- Catálogo (lectura) -------------------------------------------------------------------


def read_profile(db: str, ref: ProfileRef) -> tuple[CatalogInfo, CatalogProfile]:
    """Lee del catálogo la revisión pedida (la última sin ``@REV``). Solo lectura."""
    with CatalogStore.open(Path(db)) as store:
        info = store.catalog_by_name(ref.catalog)
        history = store.history(info.id, ref.series, ref.designation)
        if ref.revision is None:
            return info, history[-1]
        for profile in history:
            if profile.revision == ref.revision:
                return info, profile
    raise catalog_error(
        DiagnosticCode.REFERENCE_NOT_FOUND,
        "catalog.profile_not_found",
        {"profile": f"{ref.series} / {ref.designation} rev {ref.revision}"},
    )


def read_snapshot(db: str, reference: str) -> CatalogSnapshot:
    """Snapshot de la revisión ``CAT/SERIE/DES[@REV]`` del catálogo ``db`` (solo lectura)."""
    info, profile = read_profile(db, parse_reference(reference))
    return snapshot_from_profile(profile, info)


# --- Sustitución --------------------------------------------------------------------------


def _replace(
    args: argparse.Namespace, project: Project, labels: list[str] | None, reference: str
) -> tuple[str, int]:
    ref = parse_reference(reference)
    ids = _member_ids(project, labels)
    info, profile = read_profile(args.db, ref)
    snapshot = snapshot_from_profile(profile, info)
    model = project.model
    section = ensure_section(model, snapshot)
    report = model.replace_sections(ids, section.ref, partial=args.parcial)
    lines = [_report_header(snapshot, report), *_member_lines(report)]
    rejected = bool(report.rejected)
    nothing = not report.applied
    if rejected and (not args.parcial or nothing):
        reason = "partial" if args.parcial else "strict"
        lines.append(_rejection(reason))
        lines.extend(_verdicts(section))
        return "\n".join(lines), EXIT_REJECTED  # nada escrito: el archivo no cambia
    if report.changed:
        saved = save_project(
            args.file, model, project.user_profiles, runs=project.runs, norms=project.norms
        )
        states = [validity(r, model) for r in saved.runs]
        lines.append(
            f"Proyecto guardado: {args.file} · model_revision {report.model_revision_after}"
        )
        lines.append(f"  hash del modelo: {saved.model_hash}")
        lines.append(
            f"  ejecuciones: {states.count(Validity.CURRENT)} CURRENT, "
            f"{states.count(Validity.STALE)} STALE"
        )
    else:
        lines.append("Sin cambios: ninguna barra necesitaba la sustitución; el archivo no se toca.")
    lines.extend(_verdicts(section))
    return "\n".join(lines), EXIT_OK


def _rejection(reason: str) -> str:
    if reason == "strict":
        return (
            "Sustitución rechazada (modo estricto, todo o nada): no se ha aplicado ninguna barra "
            "ni se ha escrito el archivo. Use --parcial para aplicar solo a las compatibles."
        )
    return "Sustitución rechazada: ninguna barra es compatible; no se ha escrito el archivo."


def _report_header(snapshot: CatalogSnapshot, report: ReplacementReport) -> str:
    mode = "parcial" if report.partial else "estricto (todo o nada)"
    return (
        f"Perfil {snapshot.label} · {snapshot.shape} · procedencia: {snapshot.provenance.source}, "
        f"licencia {snapshot.provenance.license}, edición {snapshot.provenance.edition}\n"
        f"Sustitución en modo {mode}: {len(report.members)} barra(s) seleccionada(s)"
    )


def _member_lines(report: ReplacementReport) -> list[str]:
    lines: list[str] = []
    for item in report.members:
        lines.append(f"  barra «{item.label}»: {item.status}")
        lines.extend(f"    bloqueo: {render_message(d)}" for d in item.blocking)
        lines.extend(f"    aviso: {render_message(d)}" for d in item.warnings)
    counts = {s: sum(1 for m in report.members if m.status is s) for s in ReplacementStatus}
    summary = ", ".join(f"{n} {_STATUS_TEXT[s]}" for s, n in counts.items() if n)
    lines.append(f"Resultado: {summary}")
    return lines


# --- Dictámenes y vigencia ----------------------------------------------------------------


def _verdict_lines(section: SectionRevision) -> list[str]:
    ready = assess_section(section)
    fem = (
        "APTA para FEM"
        if ready.fem_ready
        else "NO apta para FEM (falta " + ", ".join(ready.fem_missing) + ")"
    )
    reason = render_message(
        Diagnostic.create(
            DiagnosticCode.UNSUPPORTED_CONFIGURATION,
            ready.code_reason_key,
            severity=Severity.WARNING,
        )
    )
    return [f"    FEM: {fem}", f"    Norma: {ready.code_coverage.value} — {reason}"]


def _verdicts(section: SectionRevision) -> list[str]:
    return ["Dictámenes de la sección (independientes):", *_verdict_lines(section)]


def _sections(args: argparse.Namespace, project: Project) -> str:
    model = project.model
    uses: dict[tuple[UUID, int], int] = {}
    for member in model.members.values():
        key = (member.section.entity_id, member.section.revision)
        uses[key] = uses.get(key, 0) + 1
    lines = [f"Secciones de {args.file} (revisiones: {len(model.snapshot().sections)}):"]
    for section in sorted(model.snapshot().sections, key=lambda s: (s.name, str(s.id), s.revision)):
        used = uses.get((section.id, section.revision), 0)
        lines.append(
            f"  {section.name or str(section.id)[:8]} · rev {section.revision} · "
            f"{section.family} · usada por {used} barra(s)"
        )
        lines.extend(_verdict_lines(section))
        if section.family is not SectionFamily.CATALOGO and (
            section.family is SectionFamily.PARAMETRICA
            or section.shear is not None
            or any(
                getattr(section.properties, n).status is PropertyStatus.CONTRASTADO
                for n in ("area", "iy", "iz", "torsion_constant")
            )
        ):
            from veritas.cli_section_editor import property_lines  # noqa: PLC0415 (ciclo)

            lines.append("    Propiedades:")
            lines.extend(property_lines(section))
        if (
            args.db is not None
            and section.family is SectionFamily.CATALOGO
            and isinstance(section.parameters, CatalogSnapshot)
        ):
            lines.append(f"    Vigencia: {_FRESHNESS_TEXT[freshness(section.parameters, args.db)]}")
    return "\n".join(lines)


def freshness(snapshot: CatalogSnapshot, db: str) -> Freshness:
    """Vigencia del snapshot frente al catálogo ``db``; solo lee."""
    with CatalogStore.open(Path(db)) as store:
        try:
            info = store.get_catalog(snapshot.catalog_id)
            latest = store.history(info.id, snapshot.series, snapshot.designation)[-1]
        except CatalogError:
            return Freshness.MISSING
    return compare_with_catalog(snapshot, latest)


def freshness_lines(project: Project, db: str) -> list[str]:
    """Líneas informativas de vigencia de cada perfil de catálogo usado por barras."""
    used = {(m.section.entity_id, m.section.revision) for m in project.model.members.values()}
    lines: list[str] = []
    for section in project.model.snapshot().sections:
        if (section.id, section.revision) not in used:
            continue
        if isinstance(section.parameters, CatalogSnapshot):
            state = freshness(section.parameters, db)
            lines.append(f"  perfil {section.parameters.label}: {_FRESHNESS_TEXT[state]}")
    return lines or ["  (el proyecto no usa perfiles de catálogo)"]
