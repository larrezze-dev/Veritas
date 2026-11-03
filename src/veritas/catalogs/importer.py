"""Importador CSV/JSON de perfiles: lectura, validación por fila y coherencias (ADR-009; T-022 E2).

Módulo **puro**: lee un archivo y produce filas validadas (``RowResult``); no abre SQLite ni
escribe. El almacén recibe solo los ``ProfileData`` aceptados.

Política (plan T-022, D7, D8 y D11):

- **Error de fila** (fila rechazada, con su línea y campo): campo obligatorio vacío, valor no
  numérico o no finito, valor ≤ 0, no representable en SI, familia desconocida, número de
  celdas distinto del de la cabecera, valor JSON de tipo inesperado. Se informan **todos** los
  errores de la fila.
- **Clave repetida**: dos filas con la misma clave natural (serie y designación recortadas,
  distinguiendo mayúsculas) se rechazan **todas**, aunque tengan otros errores, antes de llegar
  al almacén.
- **Aviso de coherencia** (fila aceptada y marcada en ``ProfileData.warnings``): masa lineal
  frente a A·ρ del acero (±``MASS_TOLERANCE``; solo si el mapeo declara ``steel``), A frente al
  área de una sección en I con ``dim_h``, ``dim_b``, ``dim_tw`` y ``dim_tf`` (±``AREA_TOLERANCE``;
  sin ``dim_r`` no cuenta los acuerdos y con él suma (4 − π)·r², por eso solo avisa) e
  ``Iy < Iz`` en una I (posible intercambio de ejes). Heurísticos: no certifican el dato.
- Las propiedades importadas son ``IMPORTADO_SIN_VERIFICAR``; una celda vacía es
  ``NO_DISPONIBLE``. J no se deduce nunca.

La «línea» de una fila es la línea física del archivo CSV donde empieza el registro (la cabecera
es la 1) o, en JSON, la posición del registro en la lista (la primera es la 1).
"""

import csv
import hashlib
import io
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from veritas.catalogs.columns import (
    DIMENSION_PREFIX,
    PROPERTY_FIELDS,
    REQUIRED_PROPERTIES,
    SI_UNIT,
    ColumnMapping,
    ColumnSpec,
    check_header,
    load_json,
    native_mapping,
    parse_number,
    resolve_family,
    to_si,
)
from veritas.catalogs.errors import CatalogFileError, file_error
from veritas.catalogs.model import (
    CatalogInfo,
    ImportSource,
    ProfileData,
    ProfileDimension,
    ProfileFamily,
    ProfileProperties,
    Provenance,
)
from veritas.core.diagnostics import Diagnostic, DiagnosticCode, DiagnosticError
from veritas.core.messages_es import render_message
from veritas.core.sections.properties import PropertyStatus, SectionProperty

MASS_TOLERANCE: Final = 0.02
"""Diferencia relativa admitida entre la masa lineal y A·ρ (acero) antes de avisar."""
AREA_TOLERANCE: Final = 0.01
"""Diferencia relativa admitida entre A y el área de las chapas de una I antes de avisar."""
FILLET_FACTOR: Final = 4.0 - math.pi
"""Área de los cuatro acuerdos de una I por radio² (cada uno (1 − π/4)·r²)."""
STEEL_DENSITY: Final = 7850.0
"""Densidad del acero (kg/m³) para el aviso de masa (CTE DB-SE-A, valor habitual)."""

SEPARATORS: Final = (",", ";")
DECIMALS: Final = (".", ",")
FORMATS: Final = {".csv": "CSV", ".json": "JSON"}
_MAX_SHOWN: Final = 40
NOT_PROVIDED: Final = "No aportado en la fuente"

WARN_MASS: Final = "catalog.warn_mass_area"
WARN_AREA: Final = "catalog.warn_area_dimensions"
WARN_AXES: Final = "catalog.warn_iy_less_than_iz"

type Cell = str | float


@dataclass(frozen=True, slots=True)
class RawRow:
    """Fila leída: línea, celdas por nombre de columna y, si no se pudo leer, su problema."""

    line: int
    cells: Mapping[str, Cell]
    problem: Diagnostic | None = None


@dataclass(frozen=True, slots=True)
class SourceFile:
    """Archivo leído: nombre, formato, hash de sus bytes, cabecera y filas."""

    name: str
    file_format: str
    sha256: str
    header: tuple[str, ...]
    rows: tuple[RawRow, ...]


@dataclass(frozen=True, slots=True)
class RowIssue:
    """Un error de una fila: línea, campo (si lo hay) y diagnóstico ``catalog.row_*``."""

    line: int
    field: str | None
    diagnostic: Diagnostic

    def text(self) -> str:
        where = "" if self.field is None else f" [{self.field}]"
        return (
            f"línea {self.line}{where}: {self.diagnostic.code}: {render_message(self.diagnostic)}"
        )


@dataclass(frozen=True, slots=True)
class RowResult:
    """Resultado de una fila: ``data`` si se acepta; si no, sus ``issues`` (no vacíos)."""

    line: int
    key: tuple[str, str] | None
    data: ProfileData | None
    issues: tuple[RowIssue, ...]


@dataclass(frozen=True, slots=True)
class ParsedBatch:
    """Lote leído y validado, listo para escribirse (o para simularse)."""

    source: SourceFile
    mapping: ColumnMapping
    rows: tuple[RowResult, ...]

    @property
    def accepted(self) -> tuple[ProfileData, ...]:
        return tuple(row.data for row in self.rows if row.data is not None)

    @property
    def rejected(self) -> tuple[RowResult, ...]:
        return tuple(row for row in self.rows if row.data is None)

    @property
    def import_source(self) -> ImportSource:
        return ImportSource(
            file_name=self.source.name,
            file_format=self.source.file_format,
            file_sha256=self.source.sha256,
            mapping_json=self.mapping.document,
            mapping_sha256=self.mapping.sha256,
            rows_read=len(self.rows),
            rows_rejected=len(self.rejected),
        )


# --- Lectura del archivo -----------------------------------------------------------------------


def _options_error(option: str, value: str, allowed: str) -> CatalogFileError:
    return file_error(
        DiagnosticCode.INPUT_OUT_OF_RANGE,
        "catalog.import_options",
        {"option": option, "value": value, "allowed": allowed},
    )


def check_options(sep: str, decimal: str) -> None:
    """``sep`` ∈ {«,», «;»}, ``decimal`` ∈ {«.», «,»} y coma decimal solo con «;»."""
    if sep not in SEPARATORS:
        raise _options_error("sep", sep, f"admitidos: {' '.join(SEPARATORS)}")
    if decimal not in DECIMALS:
        raise _options_error("decimal", decimal, f"admitidos: {' '.join(DECIMALS)}")
    if decimal == "," and sep == ",":
        raise _options_error("decimal", decimal, "«,» exige --sep «;»")


def detect_format(path: Path, requested: str | None) -> str:
    """``CSV`` o ``JSON``, el pedido o el de la extensión del archivo."""
    if requested is not None:
        return requested.upper()
    found = FORMATS.get(path.suffix.lower())
    if found is None:
        raise file_error(
            DiagnosticCode.UNSUPPORTED_CONFIGURATION,
            "catalog.import_format",
            {"path": str(path)},
        )
    return found


def read_source(
    path: Path,
    *,
    requested_format: str | None = None,
    sep: str = ",",
) -> SourceFile:
    """Lee un CSV o JSON (UTF-8, con o sin BOM). No interpreta los valores.

    Un archivo ilegible, vacío o mal formado lanza ``CatalogFileError``; un defecto de una fila
    queda en ``RawRow.problem``. No modifica nada.
    """
    file_format = detect_format(path, requested_format)
    if file_format not in FORMATS.values():
        raise _options_error("formato", file_format, f"admitidos: {' '.join(FORMATS.values())}")
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        raise file_error(
            DiagnosticCode.REFERENCE_NOT_FOUND, "catalog.import_missing", {"path": str(path)}
        ) from None
    except OSError as error:
        raise file_error(
            DiagnosticCode.UNSUPPORTED_CONFIGURATION,
            "catalog.import_unreadable",
            {"path": str(path)},
            cause=f"{type(error).__name__}: {error}",
        ) from error
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise file_error(
            DiagnosticCode.INPUT_INVALID_TYPE,
            "catalog.import_encoding",
            {"path": str(path)},
            cause=f"{type(error).__name__}: {error}",
        ) from error
    digest = hashlib.sha256(data).hexdigest()
    if file_format == "CSV":
        header, rows = _read_csv(text, sep, path)
    else:
        header, rows = _read_json(text, path)
    if not rows:
        raise file_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE, "catalog.import_empty", {"path": str(path)}
        )
    return SourceFile(path.name, file_format, digest, header, rows)


def _read_csv(text: str, sep: str, path: Path) -> tuple[tuple[str, ...], tuple[RawRow, ...]]:
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=sep, strict=True)
    header: tuple[str, ...] | None = None
    rows: list[RawRow] = []
    previous = 0
    try:
        for record in reader:
            start, previous = previous + 1, reader.line_num
            if not any(cell.strip() for cell in record):
                continue  # línea en blanco
            if header is None:
                header = tuple(cell.strip() for cell in record)
                continue
            if len(record) != len(header):
                problem = Diagnostic.create(
                    DiagnosticCode.INPUT_INVALID_TYPE,
                    "catalog.row_width",
                    {"expected": len(header), "found": len(record)},
                )
                rows.append(RawRow(start, {}, problem))
                continue
            rows.append(RawRow(start, dict(zip(header, record, strict=True))))
    except csv.Error as error:
        raise file_error(
            DiagnosticCode.INPUT_INVALID_TYPE,
            "catalog.import_csv",
            {"path": str(path), "line": reader.line_num},
            cause=f"{type(error).__name__}: {error}",
        ) from error
    if header is None:
        raise file_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE, "catalog.import_empty", {"path": str(path)}
        )
    return header, tuple(rows)


def _read_json(text: str, path: Path) -> tuple[tuple[str, ...], tuple[RawRow, ...]]:
    try:
        document = load_json(text)
    except ValueError as error:
        raise file_error(
            DiagnosticCode.INPUT_INVALID_TYPE,
            "catalog.import_json",
            {"path": str(path)},
            cause=f"{type(error).__name__}: {error}",
        ) from error
    if not isinstance(document, list):
        raise file_error(
            DiagnosticCode.INPUT_INVALID_TYPE, "catalog.import_structure", {"path": str(path)}
        )
    header: dict[str, None] = {}
    rows: list[RawRow] = []
    for position, record in enumerate(document, start=1):
        if not isinstance(record, dict):
            rows.append(RawRow(position, {}, _value_type("(registro)")))
            continue
        cells: dict[str, Cell] = {}
        problem: Diagnostic | None = None
        for name, value in record.items():
            header.setdefault(name, None)
            converted = _json_cell(value)
            if converted is None:
                problem = problem or _value_type(name)
            else:
                cells[name] = converted
        rows.append(RawRow(position, cells, problem))
    return tuple(header), tuple(rows)


def _json_cell(value: object) -> Cell | None:
    """Celda de un valor JSON; ``None`` si el tipo no es admisible (booleano, lista, objeto)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    try:
        return float(value)
    except OverflowError:
        return None


def _value_type(field: str) -> Diagnostic:
    return Diagnostic.create(
        DiagnosticCode.INPUT_INVALID_TYPE, "catalog.row_value_type", {"field": field}
    )


# --- Validación por fila -----------------------------------------------------------------------


def parse_batch(
    source: SourceFile,
    mapping: ColumnMapping | None,
    info: CatalogInfo,
    *,
    decimal: str = ".",
) -> ParsedBatch:
    """Valida todas las filas. Sin ``mapping`` se espera el formato nativo en SI.

    Lanza ``CatalogError`` si el mapeo no encaja con la cabecera (columna mapeada ausente,
    nombre repetido o, en el formato nativo, columna desconocida).
    """
    resolved = native_mapping(source.header) if mapping is None else mapping
    check_header(resolved, source.header)
    provenance = Provenance(info.source, info.license, info.edition, resolved.method)
    results = [_validate_row(row, resolved, provenance, decimal) for row in source.rows]
    return ParsedBatch(source, resolved, tuple(_reject_repeated(results)))


def _validate_row(
    row: RawRow, mapping: ColumnMapping, provenance: Provenance, decimal: str
) -> RowResult:
    if row.problem is not None:
        field = row.problem.params_dict().get("field")
        return RowResult(row.line, None, None, (RowIssue(row.line, _opt(field), row.problem),))
    key = _natural_key(row, mapping)
    issues: list[RowIssue] = []
    values: dict[str, float] = {}
    texts: dict[str, str] = {}
    for spec in mapping.columns:
        cell = row.cells.get(spec.column, "")
        if spec.unit is None:
            if isinstance(cell, str):
                texts[spec.field] = cell.strip()
            else:
                issues.append(
                    _issue(row.line, "catalog.row_value_type", spec.field, {"field": spec.field})
                )
            continue
        number = _number(row.line, spec, cell, decimal, issues)
        if number is not None:
            values[spec.field] = number
    if not texts.get("series") and mapping.default_series is not None:
        texts["series"] = mapping.default_series
    _check_text_fields(row.line, mapping, texts, issues)
    for field in REQUIRED_PROPERTIES:
        if field not in values and not _issue_for(issues, field):
            issues.append(_issue(row.line, "catalog.row_required", field, {"field": field}))
    family = _family(row.line, mapping, texts, issues)
    if issues or family is None:
        return RowResult(row.line, key, None, tuple(issues))
    try:
        data = _build(mapping, provenance, texts, values, family)
    except DiagnosticError as error:
        detail = render_message(error.diagnostic)
        issues.append(_issue(row.line, "catalog.row_invalid", None, {"detail": detail}))
        return RowResult(row.line, key, None, tuple(issues))
    return RowResult(row.line, key, data, ())


def _opt(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _issue_for(issues: Sequence[RowIssue], field: str) -> bool:
    return any(issue.field == field for issue in issues)


_ROW_CODES: Final = {
    "catalog.row_required": DiagnosticCode.INPUT_OUT_OF_RANGE,
    "catalog.row_not_number": DiagnosticCode.INPUT_INVALID_TYPE,
    "catalog.row_not_positive": DiagnosticCode.INPUT_OUT_OF_RANGE,
    "catalog.row_unrepresentable": DiagnosticCode.UNITS_UNREPRESENTABLE,
    "catalog.row_family_unknown": DiagnosticCode.INPUT_OUT_OF_RANGE,
    "catalog.row_invalid": DiagnosticCode.INPUT_OUT_OF_RANGE,
    "catalog.row_value_type": DiagnosticCode.INPUT_INVALID_TYPE,
}


def _issue(line: int, key: str, field: str | None, params: Mapping[str, str | int]) -> RowIssue:
    return RowIssue(line, field, Diagnostic.create(_ROW_CODES[key], key, params))


def _shown(cell: Cell) -> str:
    text = cell if isinstance(cell, str) else repr(cell)
    text = text.strip()
    return text if len(text) <= _MAX_SHOWN else text[: _MAX_SHOWN - 1] + "…"


def _number(
    line: int, spec: ColumnSpec, cell: Cell, decimal: str, issues: list[RowIssue]
) -> float | None:
    """Valor SI de una celda numérica; ``None`` si falta (sin error) o es inválida (con error)."""
    field = spec.field
    if spec.unit is None or (isinstance(cell, str) and not cell.strip()):
        return None
    shown = _shown(cell)
    params = {"field": field, "value": shown}
    if isinstance(cell, float):
        number = cell
    else:
        try:
            number = parse_number(cell, decimal)
        except ValueError:
            issues.append(_issue(line, "catalog.row_not_number", field, params))
            return None
    if not number > 0.0:
        issues.append(_issue(line, "catalog.row_not_positive", field, params))
        return None
    try:
        return to_si(number, spec.unit)
    except DiagnosticError:
        issues.append(_issue(line, "catalog.row_unrepresentable", field, params))
        return None


def _natural_key(row: RawRow, mapping: ColumnMapping) -> tuple[str, str] | None:
    parts: list[str] = []
    for field in ("series", "designation"):
        spec = mapping.spec_for(field)
        cell = row.cells.get(spec.column, "") if spec is not None else ""
        text = (cell if isinstance(cell, str) else str(cell)).strip()
        if not text and field == "series":
            text = mapping.default_series or ""
        parts.append(text)
    series, designation = parts
    return (series, designation) if series and designation else None


def _check_text_fields(
    line: int, mapping: ColumnMapping, texts: Mapping[str, str], issues: list[RowIssue]
) -> None:
    for field in ("series", "designation"):
        if (
            mapping.spec_for(field) is not None
            and not texts.get(field)
            and not _issue_for(issues, field)
        ):
            issues.append(_issue(line, "catalog.row_required", field, {"field": field}))


def _family(
    line: int, mapping: ColumnMapping, texts: Mapping[str, str], issues: list[RowIssue]
) -> ProfileFamily | None:
    text = texts.get("family", "")
    if not text:
        if mapping.default_family is None:
            issues.append(_issue(line, "catalog.row_required", "family", {"field": "family"}))
        return mapping.default_family
    family = resolve_family(text, mapping)
    if family is None:
        known = ", ".join(f.value for f in ProfileFamily)
        issues.append(
            _issue(
                line,
                "catalog.row_family_unknown",
                "family",
                {"value": _shown(text), "known": known},
            )
        )
    return family


def _build(
    mapping: ColumnMapping,
    provenance: Provenance,
    texts: Mapping[str, str],
    values: Mapping[str, float],
    family: ProfileFamily,
) -> ProfileData:
    origin = provenance.source
    props: dict[str, SectionProperty] = {}
    for name in PROPERTY_FIELDS:
        spec = mapping.spec_for(name)
        value = values.get(name)
        if spec is None or value is None:
            props[name] = SectionProperty(
                None, SI_UNIT[name], origin, NOT_PROVIDED, PropertyStatus.NO_DISPONIBLE
            )
        else:
            method = f"Importado de la columna «{spec.column}» ({spec.unit}) y convertido a SI"
            props[name] = SectionProperty(
                value, SI_UNIT[name], origin, method, PropertyStatus.IMPORTADO_SIN_VERIFICAR
            )
    dimensions = tuple(
        ProfileDimension(field[len(DIMENSION_PREFIX) :], value)
        for field, value in values.items()
        if field.startswith(DIMENSION_PREFIX)
    )
    properties = ProfileProperties(**props)
    return ProfileData(
        series=texts["series"],
        designation=texts["designation"],
        family=family,
        dimensions=dimensions,
        properties=properties,
        provenance=provenance,
        warnings=coherence_warnings(
            family=family,
            dimensions={d.name: d.value for d in dimensions},
            area=values.get("area"),
            iy=values.get("iy"),
            iz=values.get("iz"),
            mass=values.get("linear_mass"),
            steel=mapping.steel,
        ),
    )


# --- Coherencias (D11) ------------------------------------------------------------------------


def coherence_warnings(  # noqa: PLR0913 (valores SI de la fila, por nombre)
    *,
    family: ProfileFamily,
    dimensions: Mapping[str, float],
    area: float | None,
    iy: float | None,
    iz: float | None,
    mass: float | None,
    steel: bool,
) -> tuple[str, ...]:
    """Claves de aviso de una fila aceptada. Función pura sobre valores SI."""
    found: list[str] = []
    if steel and area is not None and mass is not None:
        expected = area * STEEL_DENSITY
        if abs(mass - expected) > MASS_TOLERANCE * expected:
            found.append(WARN_MASS)
    if family is ProfileFamily.I:
        plates = [dimensions.get(name) for name in ("h", "b", "tw", "tf")]
        if area is not None and all(value is not None for value in plates):
            h, b, tw, tf = (float(value) for value in plates if value is not None)
            radius = dimensions.get("r", 0.0)  # acuerdo alma-ala, si la fuente lo da
            computed = 2.0 * b * tf + (h - 2.0 * tf) * tw + FILLET_FACTOR * radius**2
            if computed <= 0.0 or abs(area - computed) > AREA_TOLERANCE * computed:
                found.append(WARN_AREA)
        if iy is not None and iz is not None and iy < iz:
            found.append(WARN_AXES)
    return tuple(found)


def _reject_repeated(results: list[RowResult]) -> list[RowResult]:
    """Rechaza **todas** las filas que comparten clave natural, aunque ya tengan otros errores."""
    lines_by_key: dict[tuple[str, str], list[int]] = {}
    for result in results:
        if result.key is not None:
            lines_by_key.setdefault(result.key, []).append(result.line)
    checked: list[RowResult] = []
    for result in results:
        lines = lines_by_key.get(result.key, []) if result.key is not None else []
        if result.key is None or len(lines) < 2:  # noqa: PLR2004 (dos o más repeticiones)
            checked.append(result)
            continue
        series, designation = result.key
        repeated = RowIssue(
            result.line,
            "designation",
            Diagnostic.create(
                DiagnosticCode.REVISION_CONFLICT,
                "catalog.row_duplicate_key",
                {"series": series, "designation": designation, "first_line": min(lines)},
            ),
        )
        checked.append(RowResult(result.line, result.key, None, (*result.issues, repeated)))
    return checked
