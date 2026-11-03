"""Campos del importador, mapeo de columnas y conversión de unidades (ADR-009; T-022 E2).

Un **mapeo** (``veritas-catalog-mapping/1``, JSON) dice qué columna del archivo de origen es
qué campo del perfil y en qué unidad viene. Es explícito: no hay detección automática de
columnas ni de unidades (plan T-022, D6). Sin mapeo se espera el formato nativo: columnas con
el nombre interno del campo y valores en SI.

Campos: ``series``, ``designation`` y ``family`` (texto); ``area`` (A), ``iy``, ``iz``,
``torsion_constant`` (J) y ``linear_mass`` (propiedades) y ``dim_<nombre>`` (dimensión nominal
en una unidad de longitud). Obligatorios en cada fila: ``REQUIRED_FIELDS``. Una celda vacía en un
campo opcional significa «no aportado» (``NO_DISPONIBLE``); un 0 nunca significa «no aportado».

Números estrictos: ``123``, ``1.5``, ``-2e-3`` (con ``--decimal ,``: ``1,5``). Se rechazan
``nan``/``inf``, ``1_000``, hexadecimal, separadores de miles y espacios internos.

Dominio puro: no importa ``sqlite3``, PySide6, PyVista, OpenSeesPy, ``fem`` ni ``persistence``.
"""

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final

from veritas.catalogs.errors import CatalogError, catalog_error
from veritas.catalogs.model import (
    PROPERTY_DIMENSIONS,
    ProfileFamily,
    canonical_json,
    sha256_of,
)
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticCode, DiagnosticError
from veritas.core.units import Dimension

MAPPING_FORMAT: Final = "veritas-catalog-mapping/1"
DEFAULT_METHOD: Final = "Importación de archivo con mapeo de columnas"
NATIVE_METHOD: Final = "Importación de archivo en formato nativo (columnas por nombre de campo, SI)"

DIMENSION_PREFIX: Final = "dim_"
TEXT_FIELDS: Final = ("series", "designation", "family")
PROPERTY_FIELDS: Final = tuple(PROPERTY_DIMENSIONS)
REQUIRED_PROPERTIES: Final = ("area", "iy", "iz")
"""Propiedades obligatorias en cada fila (A, Iy, Iz). J y la masa lineal son opcionales."""
REQUIRED_FIELDS: Final = ("series", "designation", *REQUIRED_PROPERTIES)

SI_UNIT: Final[Mapping[str, str]] = {
    "area": "m²",
    "iy": "m⁴",
    "iz": "m⁴",
    "torsion_constant": "m⁴",
    "linear_mass": "kg/m",
}
"""Unidad SI en la que se guarda cada propiedad."""

_DIMENSION_FIELD: Final = re.compile(r"dim_[a-z][a-z0-9_]*")
_NUMBER: Final = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_MAPPING_KEYS: Final = frozenset(
    {"format", "columns", "default_series", "default_family", "family_aliases", "steel", "method"}
)


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """Una columna del archivo: el campo que alimenta y, si es numérico, su unidad."""

    column: str
    field: str
    unit: str | None


@dataclass(frozen=True, slots=True)
class ColumnMapping:
    """Mapeo validado. ``document`` es su JSON canónico y ``sha256`` su hash (D6-A)."""

    columns: tuple[ColumnSpec, ...]
    default_series: str | None
    default_family: ProfileFamily | None
    family_aliases: tuple[tuple[str, ProfileFamily], ...]
    steel: bool
    method: str

    @property
    def document(self) -> str:
        """JSON canónico del mapeo: dos mapeos equivalentes tienen el mismo documento."""
        body: dict[str, object] = {
            "format": MAPPING_FORMAT,
            "columns": {
                spec.column: (
                    {"field": spec.field} | ({} if spec.unit is None else {"unit": spec.unit})
                )
                for spec in self.columns
            },
            "default_series": self.default_series,
            "default_family": None if self.default_family is None else self.default_family.value,
            "family_aliases": {text: family.value for text, family in self.family_aliases},
            "steel": self.steel,
            "method": self.method,
        }
        return canonical_json(body)

    @property
    def sha256(self) -> str:
        return sha256_of(self.document)

    def spec_for(self, field: str) -> ColumnSpec | None:
        return next((spec for spec in self.columns if spec.field == field), None)


# --- Campos y dimensiones -------------------------------------------------------------------


def is_field(name: str) -> bool:
    """¿``name`` es un campo soportado?"""
    return (
        name in TEXT_FIELDS
        or name in PROPERTY_FIELDS
        or _DIMENSION_FIELD.fullmatch(name) is not None
    )


def field_dimension(field: str) -> Dimension | None:
    """Dimensión que exige la unidad del campo; ``None`` en los campos de texto."""
    if field in PROPERTY_DIMENSIONS:
        return PROPERTY_DIMENSIONS[field]
    if field.startswith(DIMENSION_PREFIX):
        return u.LENGTH
    return None


def known_fields() -> str:
    return ", ".join([*TEXT_FIELDS, *PROPERTY_FIELDS, f"{DIMENSION_PREFIX}<nombre>"])


# --- Mapeo --------------------------------------------------------------------------------------


def _structure(path: str, expected: str) -> CatalogError:
    return catalog_error(
        DiagnosticCode.INPUT_INVALID_TYPE,
        "catalog.mapping_structure",
        {"path": path, "expected": expected},
    )


def _no_repeated_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"clave repetida en el JSON: {key}")
        result[key] = value
    return result


def _reject_constant(name: str) -> float:
    raise ValueError(f"constante JSON no admitida: {name}")


def load_json(text: str) -> object:
    """JSON estricto: sin claves repetidas ni ``NaN``/``Infinity``."""
    return json.loads(text, object_pairs_hook=_no_repeated_keys, parse_constant=_reject_constant)


def parse_mapping(text: str) -> ColumnMapping:
    """Mapeo desde su JSON. Cualquier defecto lanza ``CatalogError`` (``catalog.mapping_*``)."""
    try:
        document = load_json(text)
    except ValueError as error:
        raise catalog_error(
            DiagnosticCode.INPUT_INVALID_TYPE,
            "catalog.mapping_json",
            cause=f"{type(error).__name__}: {error}",
        ) from error
    if not isinstance(document, dict):
        raise _structure("$", "un objeto")
    extra = sorted(set(document) - _MAPPING_KEYS)
    if extra or "format" not in document or "columns" not in document:
        raise _structure(
            "$", "las claves format y columns, y solo " + ", ".join(sorted(_MAPPING_KEYS))
        )
    if document["format"] != MAPPING_FORMAT:
        raise catalog_error(
            DiagnosticCode.UNSUPPORTED_CONFIGURATION,
            "catalog.mapping_format",
            {"found": str(document["format"]), "expected": MAPPING_FORMAT},
        )
    columns = _parse_columns(document["columns"])
    mapping = ColumnMapping(
        columns=columns,
        default_series=_parse_default_series(document.get("default_series")),
        default_family=_parse_family(document.get("default_family"), "default_family"),
        family_aliases=_parse_aliases(document.get("family_aliases", {})),
        steel=_parse_bool(document.get("steel", False), "steel"),
        method=_parse_method(document.get("method", DEFAULT_METHOD)),
    )
    _check_required(mapping)
    return mapping


def _parse_columns(value: object) -> tuple[ColumnSpec, ...]:
    if not isinstance(value, dict) or not value:
        raise _structure("columns", "un objeto no vacío columna → campo y unidad")
    specs: list[ColumnSpec] = []
    seen: dict[str, str] = {}
    for column, entry in value.items():
        if not column.strip():
            raise _structure("columns", "nombres de columna no vacíos")
        spec = _parse_spec(column, entry)
        if spec.field in seen:
            raise catalog_error(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "catalog.mapping_field_repeated",
                {"field": spec.field, "first": seen[spec.field], "second": column},
            )
        seen[spec.field] = column
        specs.append(spec)
    return tuple(specs)


def _parse_spec(column: str, entry: object) -> ColumnSpec:
    path = f"columns/{column}"
    if not isinstance(entry, dict) or not set(entry) <= {"field", "unit"} or "field" not in entry:
        raise _structure(path, "un objeto con field y, si es numérico, unit")
    field = entry["field"]
    if not isinstance(field, str) or not is_field(field):
        raise catalog_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE,
            "catalog.mapping_field_unknown",
            {"column": column, "field": str(field), "known": known_fields()},
        )
    dimension = field_dimension(field)
    unit = entry.get("unit")
    if dimension is None:
        if unit is not None:
            raise _unit_error(column, field, str(unit), "un campo de texto no lleva unidad")
        return ColumnSpec(column, field, None)
    if not isinstance(unit, str):
        raise _unit_error(column, field, "", f"se exige una unidad de {dimension}")
    try:
        resolved = u.get_unit(unit)
    except DiagnosticError:
        raise _unit_error(
            column, field, unit, f"unidad desconocida; se exige {dimension}"
        ) from None
    if resolved.dimension != dimension:
        raise _unit_error(column, field, unit, f"dimensión incompatible; se exige {dimension}")
    return ColumnSpec(column, field, resolved.symbol)


def _unit_error(column: str, field: str, unit: str, expected: str) -> CatalogError:
    return catalog_error(
        DiagnosticCode.UNITS_INCOMPATIBLE,
        "catalog.mapping_unit",
        {"column": column, "field": field, "unit": unit, "expected": expected},
    )


def _parse_family(value: object, path: str) -> ProfileFamily | None:
    if value is None:
        return None
    try:
        if isinstance(value, str):
            return ProfileFamily(value)
    except ValueError:
        pass
    raise catalog_error(
        DiagnosticCode.INPUT_OUT_OF_RANGE,
        "catalog.mapping_family",
        {"path": path, "value": str(value), "known": ", ".join(f.value for f in ProfileFamily)},
    )


def _parse_aliases(value: object) -> tuple[tuple[str, ProfileFamily], ...]:
    if not isinstance(value, dict):
        raise _structure("family_aliases", "un objeto texto → familia")
    aliases: dict[str, ProfileFamily] = {}
    for text, family in value.items():
        key = text.strip().casefold()
        parsed = _parse_family(family, f"family_aliases/{text}")
        if not key or parsed is None:
            raise _structure("family_aliases", "textos no vacíos y familias válidas")
        if key in aliases:
            raise catalog_error(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "catalog.mapping_field_repeated",
                {"field": "family_aliases", "first": key, "second": text},
            )
        aliases[key] = parsed
    return tuple(sorted(aliases.items()))


def _parse_bool(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise _structure(path, "true o false")
    return value


def _parse_default_series(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise _structure("default_series", "un texto no vacío")
    return value.strip()


def _parse_method(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise _structure("method", "un texto no vacío")
    return value.strip()


def _check_required(mapping: ColumnMapping) -> None:
    for field in REQUIRED_FIELDS:
        if mapping.spec_for(field) is None and not (
            field == "series" and mapping.default_series is not None
        ):
            raise catalog_error(
                DiagnosticCode.INPUT_OUT_OF_RANGE, "catalog.mapping_required", {"field": field}
            )
    if mapping.spec_for("family") is None and mapping.default_family is None:
        raise catalog_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE, "catalog.mapping_required", {"field": "family"}
        )


# --- Mapeo nativo y enlace con la cabecera ---------------------------------------------------


def native_mapping(header: Sequence[str]) -> ColumnMapping:
    """Mapeo del formato nativo: cada columna se llama como su campo y viene en SI.

    Una columna que no es un campo soportado se rechaza (``catalog.mapping_column_unknown``):
    un nombre mal escrito no puede perder datos en silencio.
    """
    specs: list[ColumnSpec] = []
    for column in header:
        name = column.strip()
        if not is_field(name):
            raise catalog_error(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "catalog.mapping_column_unknown",
                {"column": column, "known": known_fields()},
            )
        unit = SI_UNIT.get(name, "m" if name.startswith(DIMENSION_PREFIX) else None)
        specs.append(ColumnSpec(column, name, unit))
    mapping = ColumnMapping(
        columns=tuple(specs),
        default_series=None,
        default_family=None,
        family_aliases=(),
        steel=False,
        method=NATIVE_METHOD,
    )
    _check_required(mapping)
    return mapping


def check_header(mapping: ColumnMapping, header: Sequence[str]) -> None:
    """Toda columna mapeada debe estar en la cabecera y la cabecera no repite nombres."""
    seen: set[str] = set()
    for column in header:
        if column in seen:
            raise catalog_error(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "catalog.import_duplicate_column",
                {"column": column},
            )
        seen.add(column)
    for spec in mapping.columns:
        if spec.column not in seen:
            raise catalog_error(
                DiagnosticCode.REFERENCE_NOT_FOUND,
                "catalog.mapping_column_missing",
                {"column": spec.column},
            )


# --- Valores -------------------------------------------------------------------------------------


def parse_number(text: str, decimal: str = ".") -> float:
    """Número estricto en la notación indicada; ``ValueError`` si no lo es o no es finito."""
    cell = text.strip()
    if decimal == ",":
        if "." in cell:
            raise ValueError(cell)
        cell = cell.replace(",", ".")
    elif "," in cell:
        raise ValueError(cell)
    if _NUMBER.fullmatch(cell) is None:
        raise ValueError(cell)
    number = float(cell)
    if not math.isfinite(number):
        raise ValueError(cell)
    return number


def to_si(number: float, unit: str) -> float:
    """Valor en SI. Lanza ``DiagnosticError`` si no es representable (desborda o llega a 0)."""
    return u.Quantity.of(number, unit).si_value


def resolve_family(text: str, mapping: ColumnMapping) -> ProfileFamily | None:
    """Familia de una celda: valor del enumerado o alias del mapeo, sin distinguir mayúsculas."""
    key = text.strip().casefold()
    for family in ProfileFamily:
        if family.value.casefold() == key:
            return family
    return dict(mapping.family_aliases).get(key)
