"""Grupo de comandos ``veritas catalog`` (planes T-022 E2 y T-023 E1; ADR-009).

Catálogo de perfiles en un archivo SQLite independiente del proyecto. Todos los comandos llevan
``--db RUTA`` (sin estado oculto ni carpeta de usuario, D10).

- ``catalog create --db RUTA --nombre N --fuente F --licencia L --edicion E``: crea el archivo
  si no existe (o añade el catálogo a uno existente) con su procedencia.
- ``catalog import --db RUTA --catalogo N ARCHIVO [--mapeo MAPA] [--formato csv|json]
  [--sep ,|;] [--decimal .|,] [--estricto] [--simular]``: importa un CSV/JSON. Por defecto
  escribe las filas válidas, informa las rechazadas (línea, campo, clave y mensaje) y registra
  el lote (archivo, mapeo, fecha y recuentos); ``--estricto`` rechaza el lote entero si hay una
  sola fila mala; ``--simular`` no escribe y dice qué se crearía, revisaría o dejaría igual.
- ``catalog lock --db RUTA --catalogo N``: protege el catálogo (irreversible; rechaza importar).
- ``catalog list --db RUTA [--catalogo N [--lotes]]``: catálogos, o los perfiles (última
  revisión) o los lotes de uno.
- ``catalog show --db RUTA --catalogo N SERIE DESIGNACIÓN [--revision R]``: un perfil completo.
- ``catalog search --db RUTA [--catalogo N] [--serie S] [--designacion D] [--familia F]
  [--area R] [--iy R] [--iz R] [--j R] [--masa R] [--dim NOMBRE R] [--orden CAMPO[:desc]]
  [--limite N]``: busca y ordena perfiles (última revisión) en uno o todos los catálogos. Un
  rango ``R`` es ``A..B UNIDAD``, ``A.. UNIDAD``, ``..B UNIDAD``, ``>=A UNIDAD`` o
  ``<=B UNIDAD`` (límites inclusivos). Un perfil sin la propiedad filtrada no se muestra y se
  cuenta aparte: ausente no es 0.
- ``catalog ficha --db RUTA CATÁLOGO/SERIE/DESIGNACIÓN[@REV]``: ficha completa de un perfil con
  el historial de sus revisiones.
- ``catalog comparar --db RUTA REF REF [REF ...]``: compara de 2 a 8 perfiles (SI), con el
  cociente de cada uno respecto al primero.
- ``catalog verify --db RUTA``: integridad del archivo, de cada revisión y de cada lote. Abrir un
  catálogo de una versión anterior lo migra (ADR-009, enmienda E2-A).

Códigos de salida (ADR-001 §5): **0** correcto (también si ``import`` informa filas rechazadas);
**2** error de archivo, mapeo, uso o datos: archivo ilegible o ajeno, versión no soportada,
catálogo inexistente o protegido, mapeo inválido, ``--estricto`` con filas rechazadas;
**3** ``verify`` con problemas. Con 0 el texto va a stdout; con 2 o 3, a stderr (un diagnóstico
aislado se imprime como ``CÓDIGO: mensaje`` por la frontera de ``cli.main``).

Los fallos dejan el archivo idéntico byte a byte. Las propiedades importadas quedan
``IMPORTADO_SIN_VERIFICAR``: no son datos verificados.
"""

import argparse
import contextlib
from collections.abc import Sequence
from pathlib import Path
from typing import Final
from uuid import UUID

from veritas.catalogs.columns import ColumnMapping, parse_mapping
from veritas.catalogs.errors import catalog_error, file_error
from veritas.catalogs.importer import (
    DECIMALS,
    FORMATS,
    SEPARATORS,
    ParsedBatch,
    check_options,
    parse_batch,
    read_source,
)
from veritas.catalogs.model import (
    CatalogInfo,
    CatalogProfile,
    ImportBatch,
    ProfileData,
    ProfileFamily,
    profile_sha256,
    same_content,
)
from veritas.catalogs.query import (
    FILTER_FIELDS,
    MAX_COMPARED,
    PROPERTY_LABELS,
    Comparison,
    Criteria,
    Entry,
    ProfileRef,
    Range,
    SearchResult,
    compare,
    dimension_field,
    order_fields,
    parse_order,
    parse_range,
    parse_reference,
    search,
    sort_entries,
    value_of,
)
from veritas.catalogs.store import SCHEMA_VERSION, CatalogStore, PutStatus
from veritas.core.diagnostics import DiagnosticCode
from veritas.core.messages_es import render_message, render_text

EXIT_OK: Final = 0
EXIT_DATA: Final = 2
EXIT_VERIFY: Final = 3

_WARNING: Final = (
    "Aviso: aplicación experimental; las propiedades importadas quedan IMPORTADO_SIN_VERIFICAR "
    "y no son datos verificados."
)
_PROPERTY_LABELS: Final = PROPERTY_LABELS


def add_catalog_parser(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]") -> None:
    """Registra ``veritas catalog`` y sus acciones."""
    parser = subparsers.add_parser(
        "catalog", help="Catálogos de perfiles en SQLite: crear, importar, listar, verificar."
    )
    sub = parser.add_subparsers(dest="catalog_command", metavar="ACCIÓN", required=True)

    create = sub.add_parser("create", help="Crea un catálogo (y el archivo si no existe).")
    _db(create)
    create.add_argument("--nombre", required=True)
    create.add_argument("--fuente", required=True, help="Origen de los datos.")
    create.add_argument("--licencia", required=True, help="Licencia de los datos.")
    create.add_argument("--edicion", required=True, help="Edición o versión de la fuente.")
    create.add_argument("--descripcion", default="")

    importing = sub.add_parser("import", help="Importa un CSV o JSON de perfiles.")
    _db(importing)
    _catalog(importing)
    importing.add_argument("file", metavar="ARCHIVO", help="Archivo .csv o .json (UTF-8).")
    importing.add_argument(
        "--mapeo",
        metavar="MAPA",
        help="Mapeo de columnas y unidades (JSON); sin él, formato nativo.",
    )
    importing.add_argument("--formato", choices=sorted(f.lower() for f in FORMATS.values()))
    importing.add_argument(
        "--sep", default=",", help=f"Separador del CSV ({' o '.join(SEPARATORS)})."
    )
    importing.add_argument(
        "--decimal",
        default=".",
        help=f"Separador decimal ({' o '.join(DECIMALS)}; «,» exige --sep ;).",
    )
    importing.add_argument(
        "--estricto", action="store_true", help="Rechaza el lote entero si una fila falla."
    )
    importing.add_argument("--simular", action="store_true", help="No escribe: vista previa.")

    lock = sub.add_parser("lock", help="Protege el catálogo (irreversible).")
    _db(lock)
    _catalog(lock)

    listing = sub.add_parser("list", help="Lista catálogos, o los perfiles o lotes de uno.")
    _db(listing)
    listing.add_argument("--catalogo", metavar="NOMBRE")
    listing.add_argument("--lotes", action="store_true", help="Con --catalogo: lista sus lotes.")

    show = sub.add_parser("show", help="Muestra un perfil completo.")
    _db(show)
    _catalog(show)
    show.add_argument("series", metavar="SERIE")
    show.add_argument("designation", metavar="DESIGNACIÓN")
    show.add_argument("--revision", type=int, metavar="R", help="Revisión (la última).")

    _add_query_parsers(sub)

    verify = sub.add_parser("verify", help="Verifica la integridad del archivo.")
    _db(verify)


def _add_query_parsers(
    sub: "argparse._SubParsersAction[argparse.ArgumentParser]",
) -> None:
    """Acciones de consulta: search, ficha y comparar (T-023 E1)."""
    searching = sub.add_parser(
        "search", help="Busca y ordena perfiles por texto, familia, dimensiones y propiedades."
    )
    _db(searching)
    searching.add_argument("--catalogo", metavar="NOMBRE", help="Un catálogo (por defecto, todos).")
    searching.add_argument("--serie", metavar="TEXTO", help="Subcadena de la serie.")
    searching.add_argument("--designacion", metavar="TEXTO", help="Subcadena de la designación.")
    searching.add_argument("--familia", choices=[f.value for f in ProfileFamily])
    for option, label in (
        ("area", "área"),
        ("iy", "Iy"),
        ("iz", "Iz"),
        ("j", "J"),
        ("masa", "masa lineal"),
    ):
        searching.add_argument(
            f"--{option}", metavar="RANGO", help=f"Rango de {label}, p. ej. «20..40 cm2»."
        )
    searching.add_argument(
        "--dim",
        nargs=2,
        action="append",
        default=[],
        metavar=("NOMBRE", "RANGO"),
        help="Rango de una dimensión, p. ej. --dim h «200..300 mm».",
    )
    searching.add_argument("--orden", metavar="CAMPO[:desc]", help=f"Campos: {order_fields()}.")
    searching.add_argument("--limite", type=int, metavar="N", help="Muestra solo las N primeras.")

    ficha = sub.add_parser("ficha", help="Ficha de un perfil con el historial de sus revisiones.")
    _db(ficha)
    ficha.add_argument("reference", metavar="REF", help="CATÁLOGO/SERIE/DESIGNACIÓN[@REVISIÓN]")

    comparing = sub.add_parser("comparar", help="Compara de 2 a 8 perfiles.")
    _db(comparing)
    comparing.add_argument(
        "references", nargs="+", metavar="REF", help="CATÁLOGO/SERIE/DESIGNACIÓN[@REVISIÓN]"
    )


def _db(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--db", required=True, metavar="RUTA", help="Archivo SQLite del catálogo.")


def _catalog(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--catalogo", required=True, metavar="NOMBRE")


def run_catalog(args: argparse.Namespace) -> tuple[str, int]:
    """Ejecuta una acción de ``veritas catalog``; devuelve (texto, código de salida)."""
    command = args.catalog_command
    if command == "create":
        return _create(args), EXIT_OK
    with CatalogStore.open(Path(args.db)) as store:
        if command == "verify":
            return _verify(args, store)
        if command in _QUERIES:
            return _QUERIES[command](args, store), EXIT_OK
        info = store.catalog_by_name(args.catalogo)
        if command == "lock":
            return _lock(store, info), EXIT_OK
        if command == "show":
            return _show(args, store, info), EXIT_OK
        return _import(args, store, info)


# --- create / lock ----------------------------------------------------------------------------


def _create(args: argparse.Namespace) -> str:
    path = Path(args.db)
    is_new = not path.exists()
    store = CatalogStore.create(path) if is_new else CatalogStore.open(path)
    try:
        with store:
            info = store.create_catalog(
                name=args.nombre,
                source=args.fuente,
                license=args.licencia,
                edition=args.edicion,
                description=args.descripcion,
            )
    except BaseException:
        if is_new:  # no deja un archivo vacío si el alta falla
            for leftover in (path, path.with_name(path.name + "-journal")):
                with contextlib.suppress(OSError):
                    leftover.unlink()
        raise
    where = "creado" if is_new else "añadido al archivo existente"
    return "\n".join(
        [
            f"Catálogo «{info.name}» {where}: {args.db}",
            f"  id: {info.id}",
            f"  fuente: {info.source} · licencia: {info.license} · edición: {info.edition}",
            f"  protegido: no · esquema v{SCHEMA_VERSION}",
            "Importe perfiles con «veritas catalog import».",
        ]
    )


def _lock(store: CatalogStore, info: CatalogInfo) -> str:
    store.lock_catalog(info.id)
    return (
        f"Catálogo «{info.name}» protegido (irreversible): no admite importar perfiles; "
        "se edita mediante un clon (T-023)."
    )


# --- list / show ------------------------------------------------------------------------------


def _table(header: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    widths = [max(len(row[i]) for row in [header, *rows]) for i in range(len(header))]
    lines = [
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)).rstrip()
        for row in [header, *rows]
    ]
    lines.insert(1, "  ".join("-" * width for width in widths))
    return "\n".join(lines)


def _g(value: float | None) -> str:
    return "-" if value is None else f"{value:.6g}"


def _list(args: argparse.Namespace, store: CatalogStore) -> str:
    if args.catalogo is None:
        if args.lotes:
            raise catalog_error(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "catalog.cli_option",
                {"option": "--lotes", "requires": "--catalogo"},
            )
        catalogs = store.list_catalogs()
        if not catalogs:
            return f"Archivo: {args.db}\n  sin catálogos"
        rows = [
            (
                info.name,
                str(len(store.list_profiles(info.id))),
                "sí" if info.protected else "no",
                info.edition,
                info.source,
                info.license,
            )
            for info in catalogs
        ]
        header = ("Catálogo", "Perfiles", "Protegido", "Edición", "Fuente", "Licencia")
        return f"Archivo: {args.db}\n{_table(header, rows)}"
    info = store.catalog_by_name(args.catalogo)
    if args.lotes:
        return _batches(store, info)
    profiles = store.list_profiles(info.id)
    head = f"Catálogo «{info.name}» ({'protegido' if info.protected else 'editable'}): "
    if not profiles:
        return head + "sin perfiles"
    labels = [f"{label} [{unit}]" for _, label, unit in _PROPERTY_LABELS]
    profile_rows = [
        (
            p.series,
            p.designation,
            p.family.value,
            str(p.revision),
            *(_g(getattr(p.properties, name).value) for name, _, _ in _PROPERTY_LABELS),
            str(len(p.data.warnings)),
        )
        for p in profiles
    ]
    profile_header = ("Serie", "Designación", "Familia", "Rev", *labels, "Avisos")
    table = _table(profile_header, profile_rows)
    return f"{head}{len(profiles)} perfil(es), última revisión de cada uno\n{table}"


def _batches(store: CatalogStore, info: CatalogInfo) -> str:
    batches = store.list_batches(info.id)
    if not batches:
        return f"Catálogo «{info.name}»: sin lotes de importación"
    rows = [
        (
            str(batch.id),
            batch.imported_at,
            batch.source.file_name,
            batch.source.file_format,
            f"{batch.source.rows_read}/{batch.source.rows_accepted}/{batch.source.rows_rejected}",
            f"{batch.created}/{batch.revised}/{batch.unchanged}",
            batch.source.file_sha256[:12],
            batch.source.mapping_sha256[:12],
        )
        for batch in batches
    ]
    header = (
        "Lote",
        "Fecha (UTC)",
        "Archivo",
        "Formato",
        "Leídas/acept./rech.",
        "Creados/rev./igual",
        "SHA archivo",
        "SHA mapeo",
    )
    return f"Catálogo «{info.name}»: {len(batches)} lote(s)\n{_table(header, rows)}"


def _show(args: argparse.Namespace, store: CatalogStore, info: CatalogInfo) -> str:
    history = store.history(info.id, args.series, args.designation)
    profile = _revision(history, args.revision, args.series, args.designation)
    return _describe(store, info, profile, history[-1].revision)


def _revision(
    history: Sequence[CatalogProfile], revision: int | None, series: str, designation: str
) -> CatalogProfile:
    """La revisión pedida de un historial (la última si ``revision`` es ``None``)."""
    if revision is None:
        return history[-1]
    for profile in history:
        if profile.revision == revision:
            return profile
    raise catalog_error(
        DiagnosticCode.REFERENCE_NOT_FOUND,
        "catalog.profile_not_found",
        {"profile": f"{series} / {designation} rev {revision}"},
    )


def _describe(store: CatalogStore, info: CatalogInfo, profile: CatalogProfile, latest: int) -> str:
    return "\n".join([*_describe_lines(store, info, profile, latest), _WARNING])


def _describe_lines(
    store: CatalogStore, info: CatalogInfo, profile: CatalogProfile, latest: int
) -> list[str]:
    data = profile.data
    batch = store.revision_batch(profile.id, profile.revision)
    lines = [
        f"Perfil {data.series} / {data.designation} — revisión {profile.revision} de {latest}",
        f"  id: {profile.id}",
        f"  catálogo: «{info.name}» ({'protegido' if info.protected else 'editable'})",
        f"  familia: {data.family.value}",
        "  dimensiones: "
        + (", ".join(f"{d.name} = {d.value:.10g} m" for d in data.dimensions) or "ninguna"),
        "  propiedades (SI):",
    ]
    for name, label, unit in _PROPERTY_LABELS:
        prop = getattr(data.properties, name)
        shown = "no disponible" if prop.value is None else f"{prop.value:.10g} {unit}"
        lines.append(
            f"    {label} = {shown} · {prop.status} · origen: {prop.origin} · método: {prop.method}"
        )
    prov = data.provenance
    lines += [
        f"  procedencia: fuente {prov.source} · licencia {prov.license} · edición {prov.edition}",
        f"  método de importación: {prov.method}",
        "  avisos: " + (" | ".join(render_text(key) for key in data.warnings) or "ninguno"),
        "  lote que creó la revisión: " + _batch_text(batch),
        f"  hash SHA-256 de la revisión: {profile_sha256(profile)}",
    ]
    return lines


def _batch_text(batch: ImportBatch | None) -> str:
    if batch is None:
        return "anterior al registro de lotes"
    return f"{batch.id} ({batch.source.file_name}, {batch.imported_at})"


# --- search / ficha / comparar ----------------------------------------------------------------


def _option_error(option: str, value: str, allowed: str) -> Exception:
    return catalog_error(
        DiagnosticCode.INPUT_OUT_OF_RANGE,
        "catalog.import_options",
        {"option": option, "value": value, "allowed": allowed},
    )


def _criteria(args: argparse.Namespace) -> Criteria:
    ranges: list[tuple[str, Range]] = []
    for option, field in FILTER_FIELDS.items():
        text = getattr(args, option)
        if text is not None:
            ranges.append((field, parse_range(text, field, f"--{option}")))
    for name, text in args.dim:
        field = dimension_field(name)
        ranges.append((field, parse_range(text, field, f"--dim {name}")))
    family = None if args.familia is None else ProfileFamily(args.familia)
    return Criteria(args.serie, args.designacion, family, tuple(ranges))


def _entries(store: CatalogStore, name: str | None) -> list[Entry]:
    catalogs = store.list_catalogs() if name is None else (store.catalog_by_name(name),)
    return [Entry(info, p) for info in catalogs for p in store.list_profiles(info.id)]


def _search(args: argparse.Namespace, store: CatalogStore) -> str:
    if args.limite is not None and args.limite < 1:
        raise _option_error("--limite", str(args.limite), "entero mayor o igual que 1")
    criteria = _criteria(args)
    order = None if args.orden is None else parse_order(args.orden)
    entries = _entries(store, args.catalogo)
    result = search(entries, criteria)
    shown = sort_entries(result.matches, order)
    cut = shown if args.limite is None else shown[: args.limite]
    extra = [field for field, _ in criteria.ranges if field.startswith("dim_")]
    if order is not None and order.field.startswith("dim_") and order.field not in extra:
        extra.append(order.field)
    lines = [
        f"Búsqueda en {args.db}: {len(shown)} coincidencia(s) de {result.examined} "
        "perfil(es) examinados (última revisión de cada uno)"
    ]
    if cut:
        lines.append(_search_table(cut, extra))
    if len(cut) < len(shown):
        lines.append(f"Se muestran los {len(cut)} primeros de {len(shown)} (--limite).")
    lines += _missing_lines(result)
    lines.append(_WARNING)
    return "\n".join(lines)


def _search_table(entries: Sequence[Entry], extra: Sequence[str]) -> str:
    header = (
        "Catálogo",
        "Serie",
        "Designación",
        "Familia",
        "Rev",
        *(f"{label} [{unit}]" for _, label, unit in _PROPERTY_LABELS),
        *(f"{field[4:]} [m]" for field in extra),
    )
    rows = [
        (
            e.catalog.name,
            e.profile.series,
            e.profile.designation,
            e.profile.family.value,
            str(e.profile.revision),
            *(_g(value_of(e.profile, field)) for field, _, _ in _PROPERTY_LABELS),
            *(_g(value_of(e.profile, field)) for field in extra),
        )
        for e in entries
    ]
    return _table(header, rows)


def _missing_lines(result: SearchResult) -> list[str]:
    if not result.excluded_missing:
        return []
    names = {field: label for field, label, _ in _PROPERTY_LABELS}
    detail = ", ".join(f"{names.get(field, field[4:])}: {count}" for field, count in result.missing)
    return [
        f"Excluidos por falta de dato: {result.excluded_missing} perfil(es) cumplen lo "
        f"comprobable pero no tienen la propiedad filtrada ({detail}); ausente no es 0."
    ]


def _find(store: CatalogStore, ref: ProfileRef) -> tuple[CatalogInfo, CatalogProfile, int]:
    """(catálogo, revisión pedida, última revisión) de una referencia."""
    info = store.catalog_by_name(ref.catalog)
    history = store.history(info.id, ref.series, ref.designation)
    profile = _revision(history, ref.revision, ref.series, ref.designation)
    return info, profile, history[-1].revision


def _ficha(args: argparse.Namespace, store: CatalogStore) -> str:
    ref = parse_reference(args.reference)
    info, profile, latest = _find(store, ref)
    lines = _describe_lines(store, info, profile, latest)
    lines.append("  historial de revisiones:")
    for past in store.history(info.id, ref.series, ref.designation):
        batch = store.revision_batch(past.id, past.revision)
        lines.append(
            f"    rev {past.revision} · {profile_sha256(past)[:12]} · "
            + ("anterior al registro de lotes" if batch is None else batch.imported_at)
        )
    lines.append(_WARNING)
    return "\n".join(lines)


def _compare(args: argparse.Namespace, store: CatalogStore) -> str:
    refs = [parse_reference(text) for text in args.references]
    if len(refs) > MAX_COMPARED:  # el límite se comprueba antes de leer nada
        compare([])  # lanza catalog.compare_count
    entries = [Entry(info, profile) for info, profile, _ in (_find(store, r) for r in refs)]
    return _compare_text(compare(entries))


def _compare_text(comparison: Comparison) -> str:
    tags = [f"P{i}" for i in range(1, len(comparison.entries) + 1)]
    lines = [f"Comparación de {len(tags)} perfiles (valores en SI)"]
    for tag, entry in zip(tags, comparison.entries, strict=True):
        profile = entry.profile
        lines.append(
            f"  {tag} = {entry.catalog.name}/{profile.series}/{profile.designation} "
            f"rev {profile.revision} · {profile.family.value}"
        )
    header = ["Magnitud", "Unidad", tags[0]]
    for tag in tags[1:]:
        header += [tag, f"{tag}/{tags[0]}"]
    rows = []
    for row in comparison.rows:
        cells = [row.label, row.unit, _g(row.values[0])]
        for value, ratio in zip(row.values[1:], row.ratios[1:], strict=True):
            cells += [_g(value), "-" if ratio is None else f"{ratio:.4g}"]
        rows.append(cells)
    lines.append(_table(header, rows))
    lines.append("«-»: dato no disponible; el cociente no se calcula con datos ausentes.")
    lines.append(_WARNING)
    return "\n".join(lines)


_QUERIES: Final = {
    "list": _list,
    "search": _search,
    "ficha": _ficha,
    "comparar": _compare,
}


# --- verify -----------------------------------------------------------------------------------


def _verify(args: argparse.Namespace, store: CatalogStore) -> tuple[str, int]:
    report = store.verify()
    if report.ok:
        return (
            f"Catálogo íntegro: {args.db}\n"
            f"  esquema v{store.version} · {report.revisions_checked} revisión(es) comprobadas "
            f"· {len(store.list_batches())} lote(s)",
            EXIT_OK,
        )
    lines = [f"Catálogo CON PROBLEMAS: {args.db} ({len(report.issues)})"]
    for issue in report.issues:
        who = ""
        if issue.location is not None and issue.location.entity_id is not None:
            who = f" [perfil {issue.location.entity_id}]"
        lines.append(f"  {issue.code}: {render_message(issue)}{who}")
    return "\n".join(lines), EXIT_VERIFY


# --- import -----------------------------------------------------------------------------------


def _read_mapping(path: str | None) -> ColumnMapping | None:
    if path is None:
        return None
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        raise file_error(
            DiagnosticCode.REFERENCE_NOT_FOUND, "catalog.import_missing", {"path": str(source)}
        ) from None
    except (OSError, UnicodeDecodeError) as error:
        raise file_error(
            DiagnosticCode.UNSUPPORTED_CONFIGURATION,
            "catalog.import_unreadable",
            {"path": str(source)},
            cause=f"{type(error).__name__}: {error}",
        ) from error
    return parse_mapping(text)


def _import(args: argparse.Namespace, store: CatalogStore, info: CatalogInfo) -> tuple[str, int]:
    check_options(args.sep, args.decimal)
    if info.protected:
        raise catalog_error(
            DiagnosticCode.UNSUPPORTED_CONFIGURATION, "catalog.protected", {"catalog": info.name}
        )
    mapping = _read_mapping(args.mapeo)
    source = read_source(Path(args.file), requested_format=args.formato, sep=args.sep)
    parsed = parse_batch(source, mapping, info, decimal=args.decimal)
    label = Path(args.mapeo).name if args.mapeo else "formato nativo (SI)"
    if args.estricto and parsed.rejected:
        text = _report(
            parsed, info, label, "LOTE RECHAZADO ENTERO (--estricto): no se escribe nada"
        )
        return text, EXIT_DATA
    if args.simular:
        outcome = _classify(store, info.id, parsed.accepted)
        return _report(parsed, info, label, "SIMULACIÓN: no se escribe nada", outcome), EXIT_OK
    if not parsed.accepted:
        return _report(parsed, info, label, "Ninguna fila aceptada: no se escribe nada"), EXIT_OK
    result = store.import_batch(info.id, parsed.accepted, parsed.import_source)
    batch = result.batch
    counts = (batch.created, batch.revised, batch.unchanged)
    head = f"ESCRITA en el lote {batch.id} ({batch.imported_at})"
    return _report(parsed, info, label, head, counts), EXIT_OK


def _classify(
    store: CatalogStore, catalog_id: UUID, items: Sequence[ProfileData]
) -> tuple[int, int, int]:
    """(creados, revisados, sin cambios) que produciría ``put_profiles``, sin escribir."""
    latest = {p.data.key: p.data for p in store.list_profiles(catalog_id)}
    statuses: list[PutStatus] = []
    for data in items:
        current = latest.get(data.key)
        if current is None:
            statuses.append(PutStatus.CREATED)
        elif same_content(current, data):
            statuses.append(PutStatus.UNCHANGED)
        else:
            statuses.append(PutStatus.REVISED)
    return (
        statuses.count(PutStatus.CREATED),
        statuses.count(PutStatus.REVISED),
        statuses.count(PutStatus.UNCHANGED),
    )


def _report(
    parsed: ParsedBatch,
    info: CatalogInfo,
    mapping_label: str,
    head: str,
    counts: tuple[int, int, int] | None = None,
) -> str:
    source = parsed.source
    warned = [row for row in parsed.rows if row.data is not None and row.data.warnings]
    lines = [
        f"Importación en el catálogo «{info.name}» — {head}",
        f"  archivo: {source.name} ({source.file_format}, SHA-256 {source.sha256})",
        f"  mapeo: {mapping_label} (SHA-256 {parsed.mapping.sha256})",
        f"  filas leídas: {len(parsed.rows)} · aceptadas: {len(parsed.accepted)} · "
        f"rechazadas: {len(parsed.rejected)} · con aviso: {len(warned)}",
    ]
    if counts is not None:
        lines.append(f"  creados: {counts[0]} · revisados: {counts[1]} · sin cambios: {counts[2]}")
    if parsed.rejected:
        lines.append("Filas rechazadas:")
        lines += [f"  {issue.text()}" for row in parsed.rejected for issue in row.issues]
    if warned:
        lines.append("Avisos (filas aceptadas):")
        for row in warned:
            assert row.data is not None  # ``warned`` solo tiene filas aceptadas
            shown = " | ".join(render_text(key) for key in row.data.warnings)
            lines.append(
                f"  línea {row.line} [{row.data.series} / {row.data.designation}]: {shown}"
            )
    lines.append(_WARNING)
    return "\n".join(lines)
