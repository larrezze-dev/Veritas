"""Consulta del catálogo: filtros, orden, referencias y comparación (plan T-023, E1; ADR-009).

Dominio puro sobre perfiles ya leídos del almacén (``Entry`` = catálogo + perfil): no importa
``sqlite3``, PySide6, PyVista, OpenSeesPy, ``fem`` ni ``persistence`` y no lee ni escribe nada.

Reglas (decisiones D2–D6 del plan):

- **Filtros**: texto (serie y designación por subcadena, sin distinguir mayúsculas), familia y
  rangos numéricos con unidad explícita (``"20..40 cm2"``, ``">=1000 cm4"``, ``"..30 kg/m"``).
  Los límites son **inclusivos**, con una tolerancia relativa de 1e-12 que solo absorbe el
  redondeo de la conversión de unidades.
- **Propiedad ausente** (``None``): un perfil al que le falta una propiedad filtrada no se
  muestra nunca como coincidencia (ausente ≠ 0); se cuenta aparte, por propiedad.
- **Orden** estable: por el campo pedido y, de forma determinista, por catálogo, serie y
  designación; los valores ausentes van siempre al final, también en orden descendente.
- **Comparación** de 2 a 8 perfiles: los cocientes respecto al primero se calculan en SI y
  nunca con un valor ausente.
"""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from veritas.catalogs.columns import DIMENSION_PREFIX, field_dimension, is_field, to_si
from veritas.catalogs.errors import CatalogError, catalog_error
from veritas.catalogs.model import CatalogInfo, CatalogProfile, ProfileFamily
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticCode, DiagnosticError

REL_TOL: Final = 1e-12
MIN_COMPARED: Final = 2
MAX_COMPARED: Final = 8

PROPERTY_LABELS: Final = (
    ("area", "A", "m²"),
    ("iy", "Iy", "m⁴"),
    ("iz", "Iz", "m⁴"),
    ("torsion_constant", "J", "m⁴"),
    ("linear_mass", "m", "kg/m"),
)
"""Campo, etiqueta y unidad SI de cada propiedad numérica, en el orden de las tablas."""

FILTER_FIELDS: Final = {
    "area": "area",
    "iy": "iy",
    "iz": "iz",
    "j": "torsion_constant",
    "masa": "linear_mass",
}
"""Nombre de la opción de la CLI → campo del perfil."""

_ORDER_ALIASES: Final = {**FILTER_FIELDS, "serie": "series", "designacion": "designation"}
_TEXT_ORDER: Final = frozenset({"series", "designation"})
_DIMENSION_NAME: Final = re.compile(r"[a-z][a-z0-9_]*")

_NUMBER: Final = r"(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?"
_RANGE: Final = re.compile(
    rf"(?:(?P<lo>{_NUMBER})?\s*\.\.\s*(?P<hi>{_NUMBER})?"
    rf"|>=\s*(?P<ge>{_NUMBER})|<=\s*(?P<le>{_NUMBER}))\s+(?P<unit>\S+)"
)
_REFERENCE_REVISION: Final = re.compile(r"(?P<designation>.*)@(?P<revision>\d+)")


@dataclass(frozen=True)
class Entry:
    """Un perfil con el catálogo del que procede."""

    catalog: CatalogInfo
    profile: CatalogProfile


def value_of(profile: CatalogProfile, field: str) -> float | None:
    """Valor SI de una propiedad (``area``, ``iy``…) o de una dimensión (``dim_<nombre>``)."""
    if field.startswith(DIMENSION_PREFIX):
        name = field[len(DIMENSION_PREFIX) :]
        for dimension in profile.data.dimensions:
            if dimension.name == name:
                return dimension.value
        return None
    return getattr(profile.data.properties, field).value  # type: ignore[no-any-return]


# --- Rangos ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Range:
    """Intervalo cerrado en SI; un extremo ``None`` queda abierto."""

    minimum: float | None
    maximum: float | None

    def contains(self, value: float) -> bool:
        if self.minimum is not None and value < self.minimum * (1 - REL_TOL):
            return False
        return self.maximum is None or value <= self.maximum * (1 + REL_TOL)


def _range_error(option: str, value: str, reason: str, code: DiagnosticCode) -> CatalogError:
    return catalog_error(
        code, "catalog.search_range", {"option": option, "value": value, "reason": reason}
    )


def parse_range(text: str, field: str, option: str) -> Range:
    """Interpreta ``A..B UNIDAD``, ``A.. UNIDAD``, ``..B UNIDAD``, ``>=A UNIDAD`` o ``<=B UNIDAD``.

    La unidad debe existir y ser de la dimensión del campo (m² para el área, m⁴ para las
    inercias, longitud para las dimensiones, kg/m para la masa). Los límites son inclusivos.
    """
    dimension = field_dimension(field)
    if dimension is None:
        raise _range_error(
            option, text, "el campo no es numérico", DiagnosticCode.INPUT_OUT_OF_RANGE
        )
    match = _RANGE.fullmatch(text.strip())
    if match is None or (
        match["lo"] is None and match["hi"] is None and match["ge"] is None and match["le"] is None
    ):
        raise _range_error(
            option,
            text,
            "use A..B UNIDAD, A.. UNIDAD, ..B UNIDAD, >=A UNIDAD o <=B UNIDAD",
            DiagnosticCode.INPUT_OUT_OF_RANGE,
        )
    try:
        unit = u.get_unit(match["unit"])
    except DiagnosticError:
        raise _range_error(
            option,
            text,
            f"unidad desconocida; se exige {dimension}",
            DiagnosticCode.UNITS_INCOMPATIBLE,
        ) from None
    if unit.dimension != dimension:
        raise _range_error(
            option,
            text,
            f"dimensión incompatible; se exige {dimension}",
            DiagnosticCode.UNITS_INCOMPATIBLE,
        )
    lower = _bound(match["lo"] or match["ge"], unit.symbol, option, text)
    upper = _bound(match["hi"] or match["le"], unit.symbol, option, text)
    if lower is not None and upper is not None and lower > upper:
        raise _range_error(
            option, text, "el mínimo es mayor que el máximo", DiagnosticCode.INPUT_OUT_OF_RANGE
        )
    return Range(lower, upper)


def _bound(number: str | None, unit: str, option: str, text: str) -> float | None:
    if number is None:
        return None
    value = float(number)
    if not math.isfinite(value):
        raise _range_error(option, text, "número fuera de rango", DiagnosticCode.INPUT_OUT_OF_RANGE)
    if value == 0.0:
        return 0.0
    try:
        return to_si(value, unit)
    except DiagnosticError:
        raise _range_error(
            option, text, "valor no representable", DiagnosticCode.INPUT_OUT_OF_RANGE
        ) from None


def dimension_field(name: str, option: str = "--dim") -> str:
    """Campo ``dim_<nombre>`` de una dimensión; el nombre sigue la regla del importador."""
    field = f"{DIMENSION_PREFIX}{name}"
    if _DIMENSION_NAME.fullmatch(name) is None or not is_field(field):
        raise _range_error(
            option,
            name,
            "nombre de dimensión no válido (minúsculas, cifras y «_»)",
            DiagnosticCode.INPUT_OUT_OF_RANGE,
        )
    return field


# --- Búsqueda ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Criteria:
    """Condiciones de la búsqueda; todas deben cumplirse."""

    series: str | None = None
    designation: str | None = None
    family: ProfileFamily | None = None
    ranges: tuple[tuple[str, Range], ...] = ()


@dataclass(frozen=True)
class SearchResult:
    """Coincidencias, perfiles examinados y excluidos por falta de dato.

    ``missing`` cuenta, por campo, los perfiles que cumplen todo lo que se les puede comprobar
    pero no tienen la propiedad filtrada: no se muestran porque no puede afirmarse que cumplan.
    """

    examined: int
    matches: tuple[Entry, ...]
    excluded_missing: int
    missing: tuple[tuple[str, int], ...]


def _text_matches(needle: str | None, haystack: str) -> bool:
    return needle is None or needle.strip().casefold() in haystack.casefold()


def search(entries: Sequence[Entry], criteria: Criteria) -> SearchResult:
    """Filtra ``entries``; el resultado conserva el orden de entrada."""
    matches: list[Entry] = []
    missing_by_field: dict[str, int] = {}
    excluded = 0
    for entry in entries:
        profile = entry.profile
        if (
            not _text_matches(criteria.series, profile.series)
            or not _text_matches(criteria.designation, profile.designation)
            or (criteria.family is not None and profile.family is not criteria.family)
        ):
            continue
        outside = False
        absent: list[str] = []
        for field, interval in criteria.ranges:
            value = value_of(profile, field)
            if value is None:
                absent.append(field)
            elif not interval.contains(value):
                outside = True
        if outside:
            continue
        if absent:
            excluded += 1
            for field in absent:
                missing_by_field[field] = missing_by_field.get(field, 0) + 1
            continue
        matches.append(entry)
    return SearchResult(
        examined=len(entries),
        matches=tuple(matches),
        excluded_missing=excluded,
        missing=tuple(sorted(missing_by_field.items())),
    )


# --- Orden -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Order:
    """Campo de orden y sentido."""

    field: str
    descending: bool = False


def order_fields() -> str:
    return ", ".join([*_ORDER_ALIASES, f"{DIMENSION_PREFIX}<nombre>"])


def parse_order(text: str) -> Order:
    """Interpreta ``CAMPO``, ``CAMPO:asc`` o ``CAMPO:desc`` (``dim_h``: por la dimensión h)."""
    name, separator, direction = text.strip().partition(":")
    name = name.strip().casefold()
    direction = direction.strip().casefold()
    field = _ORDER_ALIASES.get(name)
    if field is None and name.startswith(DIMENSION_PREFIX) and is_field(name):
        field = name
    if field is None or (direction not in {"asc", "desc"} and (separator or direction)):
        raise catalog_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE,
            "catalog.search_order",
            {"value": text, "allowed": f"{order_fields()}; sentido :asc o :desc"},
        )
    return Order(field, direction == "desc")


def _natural(entry: Entry) -> tuple[str, str, str, int]:
    profile = entry.profile
    return (entry.catalog.name.casefold(), profile.series, profile.designation, profile.revision)


def sort_entries(entries: Sequence[Entry], order: Order | None = None) -> tuple[Entry, ...]:
    """Orden estable; sin ``order``, por catálogo, serie y designación. Los ausentes, al final."""
    base = sorted(entries, key=_natural)
    if order is None:
        return tuple(base)
    if order.field in _TEXT_ORDER:
        return tuple(
            sorted(
                base,
                key=lambda e: getattr(e.profile, order.field).casefold(),
                reverse=order.descending,
            )
        )
    present = [(e, v) for e in base if (v := value_of(e.profile, order.field)) is not None]
    absent = [e for e in base if value_of(e.profile, order.field) is None]
    present.sort(key=lambda pair: pair[1], reverse=order.descending)
    return tuple([e for e, _ in present] + absent)


# --- Referencias y comparación -----------------------------------------------------------------


@dataclass(frozen=True)
class ProfileRef:
    """Referencia ``CATÁLOGO/SERIE/DESIGNACIÓN[@REVISIÓN]``; sin revisión, la última."""

    catalog: str
    series: str
    designation: str
    revision: int | None = None

    def __str__(self) -> str:
        suffix = "" if self.revision is None else f"@{self.revision}"
        return f"{self.catalog}/{self.series}/{self.designation}{suffix}"


def _reference_error(text: str) -> CatalogError:
    return catalog_error(
        DiagnosticCode.INPUT_OUT_OF_RANGE, "catalog.reference_invalid", {"value": text}
    )


def parse_reference(text: str) -> ProfileRef:
    """Interpreta una referencia; el catálogo y la serie no pueden contener «/»."""
    parts = text.split("/", 2)
    if len(parts) != 3:  # noqa: PLR2004 (catálogo, serie y designación)
        raise _reference_error(text)
    catalog, series, rest = (part.strip() for part in parts)
    revision: int | None = None
    tagged = _REFERENCE_REVISION.fullmatch(rest)
    if tagged is not None:
        rest = tagged["designation"].strip()
        revision = int(tagged["revision"])
        if revision < 1:
            raise _reference_error(text)
    if not catalog or not series or not rest:
        raise _reference_error(text)
    return ProfileRef(catalog, series, rest, revision)


@dataclass(frozen=True)
class CompareRow:
    """Una magnitud con el valor de cada perfil y su cociente respecto al primero."""

    label: str
    unit: str
    values: tuple[float | None, ...]
    ratios: tuple[float | None, ...]


@dataclass(frozen=True)
class Comparison:
    entries: tuple[Entry, ...]
    rows: tuple[CompareRow, ...]


def _row(label: str, unit: str, values: tuple[float | None, ...]) -> CompareRow:
    first = values[0]
    ratios: list[float | None] = [None]
    for value in values[1:]:
        ratios.append(value / first if value is not None and first else None)
    return CompareRow(label, unit, values, tuple(ratios))


def compare(entries: Sequence[Entry]) -> Comparison:
    """Compara 2 a 8 perfiles distintos: propiedades y la unión de sus dimensiones."""
    if not MIN_COMPARED <= len(entries) <= MAX_COMPARED:
        raise catalog_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE,
            "catalog.compare_count",
            {"minimum": MIN_COMPARED, "maximum": MAX_COMPARED, "count": len(entries)},
        )
    seen: set[tuple[object, int]] = set()
    for entry in entries:
        key = (entry.profile.id, entry.profile.revision)
        if key in seen:
            raise catalog_error(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "catalog.compare_repeated",
                {
                    "profile": f"{entry.catalog.name}/{entry.profile.series}/"
                    f"{entry.profile.designation}@{entry.profile.revision}"
                },
            )
        seen.add(key)
    rows = [
        _row(label, unit, tuple(value_of(e.profile, field) for e in entries))
        for field, label, unit in PROPERTY_LABELS
    ]
    names = sorted({d.name for e in entries for d in e.profile.data.dimensions})
    rows += [
        _row(name, "m", tuple(value_of(e.profile, f"{DIMENSION_PREFIX}{name}") for e in entries))
        for name in names
    ]
    return Comparison(tuple(entries), tuple(rows))
