"""Grupo de comandos ``veritas norm`` (plan T-014, E2; D8; ADR-007).

Editor manual de configuraciones de normativas por línea de comandos (sin interfaz gráfica
hasta F4). Las configuraciones de usuario viajan en el archivo de proyecto (esquema v3); la
protegida del CTE vive en el código y solo se copia.

- ``norm list [ARCHIVO]``: protegidas y, con ARCHIVO, las de usuario y la seleccionada.
- ``norm show ARCHIVO ID [--revision N]``: una configuración con sus parámetros y fuentes.
- ``norm new ARCHIVO --nombre …``: crea una configuración de usuario (revisión 1).
- ``norm copy ARCHIVO ID --nombre … [--revision N]``: copia una revisión exacta (también la
  protegida); ``derived_from`` queda informativo.
- ``norm set ARCHIVO ID [campos] [--param CLAVE=VALOR UNIDAD --fuente TEXTO]…
  [--quitar-param CLAVE]``: revisión n+1; contenido idéntico = sin cambios.
- ``norm delete ARCHIVO ID --confirmar``: borra todas las revisiones (irreversible salvo el
  «.bak» del guardado). Sin ``--confirmar`` no borra.
- ``norm use ARCHIVO ID [--revision N]``: selecciona la normativa del proyecto.

Códigos de salida: 0; 2 diagnóstico de dominio (protegida, no encontrada, inválida; stderr);
3 archivo sin integridad (stderr). Ningún comando cambia el hash del modelo ni caduca
ejecuciones. La cobertura de verificación es siempre NO_COMPROBADO (no hay comprobaciones).
"""

import argparse
from pathlib import Path
from typing import Final
from uuid import UUID

from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticCode
from veritas.core.materials.material import MaterialFamily
from veritas.core.messages_es import render_message
from veritas.core.references import RevisionRef
from veritas.design_codes.builtin import coverage
from veritas.design_codes.norms import NormConfiguration, NormParameter, NormSystem
from veritas.design_codes.registry import NormRegistry
from veritas.persistence.errors import IntegrityError, value_error
from veritas.persistence.norms import NormsState
from veritas.persistence.project import open_project, update_norms

EXIT_OK: Final = 0
EXIT_INTEGRITY: Final = 3

UNSET: Final = "NO FIJADA"
_MIN_PREFIX: Final = 4
_WARNING: Final = (
    "Aviso: aplicación experimental; una configuración de normativa son datos con su fuente, "
    "no una comprobación: la cobertura de verificación es NO_COMPROBADO."
)
_FAMILY_LABELS: Final = {
    MaterialFamily.STEEL: "acero",
    MaterialFamily.ALUMINIUM: "aluminio",
    MaterialFamily.TIMBER: "madera",
}


def add_norm_parser(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Registra ``veritas norm`` y sus acciones."""
    parser = subparsers.add_parser(
        "norm", help="Configuraciones de normativas del proyecto: crear, editar, copiar, borrar."
    )
    sub = parser.add_subparsers(dest="norm_command", metavar="ACCIÓN", required=True)
    listing = sub.add_parser("list", help="Lista las protegidas y, con ARCHIVO, las del proyecto.")
    listing.add_argument("file", nargs="?", metavar="ARCHIVO", help="Archivo de proyecto.")
    show = sub.add_parser("show", help="Muestra una configuración (última revisión por defecto).")
    _target(show)
    show.add_argument("--revision", type=int, metavar="N", help="Revisión (la última).")
    new = sub.add_parser("new", help="Crea una configuración de usuario (revisión 1).")
    new.add_argument("file", metavar="ARCHIVO")
    new.add_argument("--nombre", required=True)
    new.add_argument("--sistema", choices=[s.value for s in NormSystem], default="OTHER")
    new.add_argument("--jurisdiccion", default=UNSET, help=f"Jurisdicción ({UNSET}).")
    new.add_argument("--edicion", default=UNSET, help=f"Edición ({UNSET}).")
    new.add_argument("--documento", action="append", help="Documento (repetible).")
    new.add_argument("--descripcion", default="")
    copy = sub.add_parser("copy", help="Copia una revisión (también la protegida).")
    _target(copy)
    copy.add_argument("--nombre", required=True)
    copy.add_argument("--revision", type=int, metavar="N", help="Revisión de origen (la última).")
    edit = sub.add_parser("set", help="Edita: crea la revisión n+1 (no las protegidas).")
    _target(edit)
    edit.add_argument("--nombre")
    edit.add_argument("--jurisdiccion")
    edit.add_argument("--edicion")
    edit.add_argument("--documento", action="append", help="Sustituye los documentos (repetible).")
    edit.add_argument("--descripcion")
    edit.add_argument(
        "--param",
        action="append",
        nargs=2,
        metavar=("CLAVE=VALOR", "UNIDAD"),
        help="Añade o reemplaza un parámetro; exige su --fuente.",
    )
    edit.add_argument("--fuente", action="append", help="Fuente (cláusula) del --param anterior.")
    edit.add_argument("--quitar-param", action="append", metavar="CLAVE")
    delete = sub.add_parser("delete", help="Borra la configuración con todas sus revisiones.")
    _target(delete)
    delete.add_argument(
        "--confirmar", action="store_true", help="Obligatorio: borrar no se deshace."
    )
    use = sub.add_parser("use", help="Selecciona la normativa del proyecto.")
    _target(use)
    use.add_argument("--revision", type=int, metavar="N", help="Revisión (la última).")


def _target(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("file", metavar="ARCHIVO")
    parser.add_argument("id", metavar="ID", help="UUID completo o prefijo único (4+ caracteres).")


def run_norm(args: argparse.Namespace) -> tuple[str, int]:  # noqa: PLR0911 (un retorno por acción)
    """Ejecuta una acción de ``veritas norm``; devuelve (texto, código de salida)."""
    try:
        if args.norm_command == "list" and args.file is None:
            return _list(NormRegistry(), None), EXIT_OK
        project = open_project(Path(args.file))
    except IntegrityError as error:
        return f"{error.code}: {render_message(error.diagnostic)}", EXIT_INTEGRITY
    registry = project.norms.registry()
    command = args.norm_command
    if command == "list":
        return _list(registry, args.file), EXIT_OK
    if command == "new":
        return _new(args, registry), EXIT_OK
    config = registry.latest(_resolve(registry, args.id))
    if command == "show":
        return _show(registry, config, args.revision), EXIT_OK
    if command == "copy":
        return _copy(args, registry, config), EXIT_OK
    if command == "set":
        return _set(args, registry, config), EXIT_OK
    if command == "delete":
        return _delete(args, registry, config), EXIT_OK
    return _use(args, registry, config), EXIT_OK


def _resolve(registry: NormRegistry, text: str) -> UUID:
    """UUID completo o prefijo único (4+ caracteres) de una configuración existente."""
    try:
        parsed = UUID(text)
    except ValueError:
        parsed = None
    known = [c.id for c in registry.all_configurations()]
    if parsed is not None:
        matches = [i for i in known if i == parsed]
    else:
        prefix = text.strip().lower()
        matches = [i for i in known if len(prefix) >= _MIN_PREFIX and str(i).startswith(prefix)]
    if len(matches) != 1:
        raise value_error(
            DiagnosticCode.REFERENCE_NOT_FOUND,
            "model.reference_not_found",
            {"kind": "norm", "entity_id": f"{text} ({len(matches)} coincidencias)"},
        )
    return matches[0]


def _revision(registry: NormRegistry, config: NormConfiguration, number: int | None) -> RevisionRef:
    return config.ref if number is None else RevisionRef(config.id, _positive(number))


def _positive(number: int) -> int:
    if number < 1:
        raise value_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE,
            "input.out_of_range",
            {"field": "revision", "value": str(number), "condition": ">= 1"},
        )
    return number


def _save(path: str, registry: NormRegistry) -> None:
    update_norms(path, NormsState.of(registry))


def _describe(config: NormConfiguration, selected: RevisionRef | None) -> str:
    mark = " ← seleccionada" if selected is not None and selected.entity_id == config.id else ""
    return (
        f"  {config.id} rev {config.revision} {config.kind} {config.system} "
        f"«{config.name}» · edición {config.edition} · {len(config.parameters)} parámetro(s){mark}"
    )


def _list(registry: NormRegistry, path: str | None) -> str:
    configs = registry.all_configurations()
    head = f"Proyecto: {path}" if path else "Configuraciones protegidas (sin proyecto)"
    lines = [head]
    lines += [_describe(c, registry.selected) for c in configs]
    if path:
        selected = registry.selected
        lines.append(
            "  normativa seleccionada: "
            + (f"{selected.entity_id} rev {selected.revision}" if selected else "ninguna")
        )
    lines.append(_WARNING)
    return "\n".join(lines)


def _fmt(parameter: NormParameter) -> str:
    shown = parameter.value / u.get_unit(parameter.unit).factor
    note = f" · nota: {parameter.note}" if parameter.note else ""
    return (
        f"    {parameter.key} = {shown:.10g} {parameter.unit} (SI {parameter.value!r}) "
        f"· fuente: {parameter.source}{note}"
    )


def _show(registry: NormRegistry, latest: NormConfiguration, number: int | None) -> str:
    config = registry.get(_revision(registry, latest, number))
    lines = [
        f"Configuración {config.id} — revisión {config.revision} de {latest.revision}",
        f"  nombre: {config.name}",
        f"  tipo: {config.kind} · sistema: {config.system}",
        f"  jurisdicción: {config.jurisdiction} · edición: {config.edition}",
        "  documentos: " + (", ".join(config.documents) or "ninguno"),
        f"  descripción: {config.description or '—'}",
        "  derivada de: "
        + (
            f"{config.derived_from.entity_id} rev {config.derived_from.revision}"
            if config.derived_from
            else "—"
        ),
    ]
    if config.parameters:
        lines.append("  parámetros:")
        lines += [_fmt(p) for p in config.parameters]
    else:
        lines.append("  sin parámetros")
    states = ", ".join(
        f"{label}: {coverage(config, family)}" for family, label in _FAMILY_LABELS.items()
    )
    lines += [f"  cobertura de verificación — {states}", _WARNING]
    return "\n".join(lines)


def _new(args: argparse.Namespace, registry: NormRegistry) -> str:
    config = registry.create(
        name=args.nombre,
        system=NormSystem(args.sistema),
        jurisdiction=args.jurisdiccion,
        edition=args.edicion,
        documents=tuple(args.documento or ()),
        description=args.descripcion,
    )
    _save(args.file, registry)
    return (
        f"Configuración creada: {config.id} (revisión 1, USER) «{config.name}»\n"
        "Proyecto guardado; la versión anterior queda en «.bak».\n" + _WARNING
    )


def _copy(args: argparse.Namespace, registry: NormRegistry, config: NormConfiguration) -> str:
    copy = registry.copy(_revision(registry, config, args.revision), name=args.nombre)
    _save(args.file, registry)
    origin = copy.derived_from
    assert origin is not None  # una copia siempre deriva de su origen
    return (
        f"Copia creada: {copy.id} (revisión 1, USER) «{copy.name}»\n"
        f"  derivada de {origin.entity_id} rev {origin.revision}\n"
        "Proyecto guardado; la versión anterior queda en «.bak»."
    )


def _parse_param(spec: str, unit: str, source: str) -> NormParameter:
    key, separator, text = spec.partition("=")
    try:
        if not separator:
            raise ValueError(spec)
        number = float(text)
    except ValueError:
        raise value_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE, "norm.cli_param_format", {"spec": spec}
        ) from None
    quantity = u.Quantity.of(number, unit)
    return NormParameter(
        key=key,
        value=quantity.si_value,
        dimension=quantity.dimension,
        unit=unit,
        source=source,
    )


def _parameters(
    args: argparse.Namespace, current: tuple[NormParameter, ...]
) -> tuple[NormParameter, ...] | None:
    params = args.param or []
    sources = args.fuente or []
    removals = args.quitar_param or []
    if len(params) != len(sources):
        raise value_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE,
            "norm.cli_param_source",
            {"params": len(params), "sources": len(sources)},
        )
    if not params and not removals:
        return None
    result = list(current)
    for key in removals:
        kept = [p for p in result if p.key != key]
        if len(kept) == len(result):
            raise value_error(
                DiagnosticCode.INPUT_OUT_OF_RANGE, "norm.cli_param_missing", {"key": key}
            )
        result = kept
    for (spec, unit), source in zip(params, sources, strict=True):
        new = _parse_param(spec, unit, source)
        index = next((i for i, p in enumerate(result) if p.key == new.key), None)
        if index is None:
            result.append(new)
        else:
            result[index] = new
    return tuple(result)


def _set(args: argparse.Namespace, registry: NormRegistry, config: NormConfiguration) -> str:
    changed = registry.edit(
        config.id,
        name=args.nombre,
        jurisdiction=args.jurisdiccion,
        edition=args.edicion,
        documents=None if args.documento is None else tuple(args.documento),
        description=args.descripcion,
        parameters=_parameters(args, config.parameters),
    )
    if changed.revision == config.revision:
        return f"Configuración {config.id}: sin cambios (revisión {config.revision})."
    _save(args.file, registry)
    return (
        f"Configuración {config.id} actualizada: revisión {changed.revision}.\n"
        "Las revisiones anteriores se conservan. Proyecto guardado (anterior en «.bak»)."
    )


def _delete(args: argparse.Namespace, registry: NormRegistry, config: NormConfiguration) -> str:
    if not args.confirmar:
        raise value_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE,
            "norm.cli_confirm_required",
            {"name": config.name},
        )
    removed = registry.delete(config.id)
    _save(args.file, registry)
    return (
        f"Configuración {config.id} «{config.name}» borrada ({removed} revisión(es)).\n"
        "Proyecto guardado; la versión anterior queda en «.bak»."
    )


def _use(args: argparse.Namespace, registry: NormRegistry, config: NormConfiguration) -> str:
    ref = _revision(registry, config, args.revision)
    registry.select(ref)
    _save(args.file, registry)
    chosen = registry.get(ref)
    states = ", ".join(
        f"{label}: {coverage(chosen, family)}" for family, label in _FAMILY_LABELS.items()
    )
    return (
        f"Normativa seleccionada: «{chosen.name}» ({chosen.id} rev {chosen.revision})\n"
        f"  cobertura de verificación — {states}\n"
        "Cambiar la normativa no modifica el hash del modelo ni caduca ejecuciones.\n" + _WARNING
    )
