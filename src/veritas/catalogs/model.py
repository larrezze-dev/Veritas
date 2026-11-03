"""Dominio del catálogo de perfiles: entidades inmutables y hash de contenido (ADR-009; T-022 E1).

Un perfil de catálogo es una **entidad propia**, distinta de ``SectionRevision`` (plan T-022,
D4-A): lleva las propiedades que aporta el fabricante o la fuente, cada una con su estado, y
procedencia obligatoria. La conversión a sección asignable a barras es de T-024.

Convenciones:

- Todo valor numérico está en SI (m, m², m⁴, kg/m); la conversión desde las unidades de la
  fuente es del importador (E2). Una propiedad no aportada es ``NO_DISPONIBLE`` con valor
  ``None``, nunca 0, y J jamás se deduce de Iy + Iz.
- Un valor de catálogo solo puede ser ``IMPORTADO_SIN_VERIFICAR`` en T-022: ``CONTRASTADO`` y
  ``VERIFICADO`` (SRS §5.2) están reservados a F3A y este módulo los rechaza, de modo que
  ningún dato importado o manipulado se presenta como verificado.
- Clave natural de un perfil: (catálogo, serie, designación), con los textos sin espacios en
  los extremos. Distingue mayúsculas de minúsculas.
- Cada ``CatalogProfile`` es una revisión inmutable identificada por (UUID v4, revisión).
- El hash de contenido SHA-256 se calcula sobre un JSON canónico local (claves ordenadas, sin
  espacios, flotantes con su ``repr`` más corto exacto, NaN e infinitos prohibidos). No usa
  ``persistence``: ``catalogs`` queda por debajo de esa capa.
- Dominio puro: no importa ``sqlite3``, PySide6, PyVista, OpenSeesPy, ``fem`` ni ``persistence``.
"""

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from veritas.catalogs.errors import catalog_error
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticCode, Location
from veritas.core.references import check_identity
from veritas.core.sections.properties import PropertyStatus, SectionProperty
from veritas.core.units import Dimension, get_unit
from veritas.core.validation import checked_positive, invalid_type, positive_float, require_text

DOCUMENT_FORMAT: Final = "veritas-catalog-profile/1"
"""Etiqueta de la codificación canónica del documento de un perfil."""

_DIMENSION_NAME: Final = re.compile(r"[a-z][a-z0-9_]*")
_WARNING_KEY: Final = re.compile(r"[a-z]+\.[a-z_]+")

CATALOG_STATUSES: Final = frozenset(
    {PropertyStatus.NO_DISPONIBLE, PropertyStatus.IMPORTADO_SIN_VERIFICAR}
)
"""Estados admitidos en un perfil de catálogo en T-022 (ver el módulo)."""

PROPERTY_DIMENSIONS: Final[Mapping[str, Dimension]] = {
    "area": u.AREA,
    "iy": u.SECOND_MOMENT,
    "iz": u.SECOND_MOMENT,
    "torsion_constant": u.SECOND_MOMENT,
    "linear_mass": u.LINEAR_MASS,
}
"""Propiedades de un perfil de catálogo, en orden, y la dimensión de su unidad SI."""


class ProfileFamily(StrEnum):
    """Forma del perfil. ``OTRO`` cubre lo que la lista no nombra."""

    I = "I"  # noqa: E741 (designación de la forma en I, como en los prontuarios)
    U = "U"
    L = "L"
    T = "T"
    SHS = "SHS"
    RHS = "RHS"
    CHS = "CHS"
    RECT = "RECT"
    ROUND = "ROUND"
    OTRO = "OTRO"


@dataclass(frozen=True, slots=True)
class Provenance:
    """Origen del dato: fuente, licencia de los datos, edición y método. Todo obligatorio."""

    source: str
    license: str
    edition: str
    method: str

    def __post_init__(self) -> None:
        for name in self.__slots__:
            require_text(getattr(self, name), f"provenance.{name}")


@dataclass(frozen=True, slots=True)
class ProfileDimension:
    """Dimensión geométrica nominal del perfil, en metros y estrictamente positiva."""

    name: str
    value: float

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _DIMENSION_NAME.fullmatch(self.name) is None:
            raise catalog_error(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "catalog.invalid_dimension_name",
                {"name": str(self.name)},
            )
        object.__setattr__(self, "value", positive_float(self.value, f"dimension.{self.name}"))


@dataclass(frozen=True, slots=True)
class ProfileProperties:
    """Propiedades SI del perfil. Las no aportadas son ``NO_DISPONIBLE`` (valor ``None``)."""

    area: SectionProperty
    iy: SectionProperty
    iz: SectionProperty
    torsion_constant: SectionProperty
    linear_mass: SectionProperty

    def __post_init__(self) -> None:
        for name, dimension in PROPERTY_DIMENSIONS.items():
            prop = getattr(self, name)
            at = Location(field=name)
            if not isinstance(prop, SectionProperty):
                raise invalid_type(name, "SectionProperty", prop, at)
            if prop.status not in CATALOG_STATUSES:
                raise catalog_error(
                    DiagnosticCode.INPUT_OUT_OF_RANGE,
                    "catalog.invalid_status",
                    {"field": name, "status": str(prop.status)},
                    at,
                )
            if get_unit(prop.unit).dimension != dimension:
                raise catalog_error(
                    DiagnosticCode.UNITS_INCOMPATIBLE,
                    "units.incompatible",
                    {"left": str(dimension), "right": str(get_unit(prop.unit).dimension)},
                    at,
                )
            if prop.value is not None:
                checked_positive(prop.value, name, at)


@dataclass(frozen=True, slots=True)
class ProfileData:
    """Contenido de un perfil, sin identidad: lo que el importador produce y compara (D8)."""

    series: str
    designation: str
    family: ProfileFamily
    dimensions: tuple[ProfileDimension, ...]
    properties: ProfileProperties
    provenance: Provenance
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "series", _key_text(self.series, "series"))
        object.__setattr__(self, "designation", _key_text(self.designation, "designation"))
        if not isinstance(self.family, ProfileFamily):
            raise invalid_type("family", "ProfileFamily", self.family)
        if not isinstance(self.properties, ProfileProperties):
            raise invalid_type("properties", "ProfileProperties", self.properties)
        if not isinstance(self.provenance, Provenance):
            raise invalid_type("provenance", "Provenance", self.provenance)
        if not isinstance(self.dimensions, tuple):
            raise invalid_type("dimensions", "tupla de ProfileDimension", self.dimensions)
        seen: set[str] = set()
        for dimension in self.dimensions:
            if not isinstance(dimension, ProfileDimension):
                raise invalid_type("dimensions", "ProfileDimension", dimension)
            if dimension.name in seen:
                raise catalog_error(
                    DiagnosticCode.INPUT_OUT_OF_RANGE,
                    "catalog.duplicate_dimension",
                    {"name": dimension.name},
                )
            seen.add(dimension.name)
        object.__setattr__(
            self, "dimensions", tuple(sorted(self.dimensions, key=lambda item: item.name))
        )
        self._check_warnings()

    def _check_warnings(self) -> None:
        if not isinstance(self.warnings, tuple):
            raise invalid_type("warnings", "tupla de textos", self.warnings)
        seen: set[str] = set()
        for key in self.warnings:
            if not isinstance(key, str) or _WARNING_KEY.fullmatch(key) is None:
                raise catalog_error(
                    DiagnosticCode.INPUT_OUT_OF_RANGE, "catalog.invalid_warning", {"key": str(key)}
                )
            if key in seen:
                raise catalog_error(
                    DiagnosticCode.INPUT_OUT_OF_RANGE, "catalog.invalid_warning", {"key": key}
                )
            seen.add(key)

    @property
    def key(self) -> tuple[str, str]:
        """Clave natural dentro del catálogo: (serie, designación)."""
        return (self.series, self.designation)


@dataclass(frozen=True, slots=True)
class CatalogProfile:
    """Revisión inmutable de un perfil, con identidad (UUID v4, revisión) y su catálogo."""

    id: UUID
    revision: int
    catalog_id: UUID
    data: ProfileData

    def __post_init__(self) -> None:
        check_identity(self.id, self.revision, "CatalogProfile")
        check_identity(self.catalog_id, 1, "CatalogProfile.catalog_id")
        if not isinstance(self.data, ProfileData):
            raise invalid_type("data", "ProfileData", self.data, Location(entity_id=self.id))

    @property
    def series(self) -> str:
        return self.data.series

    @property
    def designation(self) -> str:
        return self.data.designation

    @property
    def family(self) -> ProfileFamily:
        return self.data.family

    @property
    def properties(self) -> ProfileProperties:
        return self.data.properties

    @property
    def provenance(self) -> Provenance:
        return self.data.provenance


@dataclass(frozen=True, slots=True)
class CatalogInfo:
    """Cabecera de un catálogo: nombre único y procedencia. ``protected`` impide importar."""

    id: UUID
    name: str
    description: str
    source: str
    license: str
    edition: str
    protected: bool

    def __post_init__(self) -> None:
        check_identity(self.id, 1, "CatalogInfo")
        for name in ("name", "source", "license", "edition"):
            require_text(getattr(self, name), name, Location(entity_id=self.id, field=name))
        if not isinstance(self.description, str):
            raise invalid_type(
                "description", "texto", self.description, Location(entity_id=self.id)
            )
        if not isinstance(self.protected, bool):
            raise invalid_type("protected", "bool", self.protected, Location(entity_id=self.id))
        object.__setattr__(self, "name", self.name.strip())


def _key_text(value: object, field: str) -> str:
    text = require_text(value, field)
    return text.strip()


# --- Documento canónico y hash --------------------------------------------------------------


def _property_document(prop: SectionProperty) -> dict[str, object]:
    return {
        "value": prop.value,
        "unit": prop.unit,
        "origin": prop.origin,
        "method": prop.method,
        "status": prop.status.value,
    }


def data_document(data: ProfileData) -> dict[str, object]:
    """Estructura JSON del contenido (sin identidad): base de la comparación de D8."""
    return {
        "series": data.series,
        "designation": data.designation,
        "family": data.family.value,
        "dimensions": [{"name": d.name, "value": d.value} for d in data.dimensions],
        "properties": {
            name: _property_document(getattr(data.properties, name)) for name in PROPERTY_DIMENSIONS
        },
        "provenance": {
            "source": data.provenance.source,
            "license": data.provenance.license,
            "edition": data.provenance.edition,
            "method": data.provenance.method,
        },
        "warnings": list(data.warnings),
    }


def profile_document(profile: CatalogProfile) -> dict[str, object]:
    """Estructura JSON completa de una revisión (identidad y contenido)."""
    return {
        "format": DOCUMENT_FORMAT,
        "id": str(profile.id),
        "revision": profile.revision,
        "catalog_id": str(profile.catalog_id),
        "data": data_document(profile.data),
    }


def canonical_json(document: object) -> str:
    """JSON canónico: claves ordenadas, sin espacios, UTF-8 sin escapes, NaN prohibido."""
    return json.dumps(
        document, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def sha256_of(text: str) -> str:
    """SHA-256 hexadecimal minúscula de un texto en UTF-8."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def profile_json(profile: CatalogProfile) -> str:
    """Documento canónico de una revisión."""
    return canonical_json(profile_document(profile))


def profile_sha256(profile: CatalogProfile) -> str:
    """Hash de contenido de una revisión (identidad y contenido)."""
    return sha256_of(profile_json(profile))


def same_content(left: ProfileData, right: ProfileData) -> bool:
    """Igualdad exacta de contenido (D8), por documento canónico: ``−0,0`` ≠ ``0,0``."""
    return canonical_json(data_document(left)) == canonical_json(data_document(right))


# --- Decodificación estricta ----------------------------------------------------------------


def _expect_object(value: object, keys: frozenset[str], path: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise invalid_type(path, "objeto", value)
    if set(value) != keys:
        missing = ", ".join(sorted(keys - set(value))) or "-"
        extra = ", ".join(sorted(set(value) - keys)) or "-"
        raise catalog_error(
            DiagnosticCode.INPUT_INVALID_TYPE,
            "catalog.invalid_document",
            {"path": path, "missing": missing, "extra": extra},
        )
    return value


def _decode_property(value: object, name: str) -> SectionProperty:
    obj = _expect_object(value, frozenset({"value", "unit", "origin", "method", "status"}), name)
    raw = obj["value"]
    if raw is not None and (isinstance(raw, bool) or not isinstance(raw, float)):
        raise invalid_type(f"{name}.value", "float o null", raw)
    return SectionProperty(
        raw,
        _as_text(obj["unit"], f"{name}.unit"),
        _as_text(obj["origin"], f"{name}.origin"),
        _as_text(obj["method"], f"{name}.method"),
        PropertyStatus(_as_text(obj["status"], f"{name}.status")),
    )


def _as_text(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise invalid_type(path, "texto", value)
    return value


def _decode_data(value: object) -> ProfileData:
    keys = frozenset(
        {"series", "designation", "family", "dimensions", "properties", "provenance", "warnings"}
    )
    obj = _expect_object(value, keys, "data")
    props = _expect_object(obj["properties"], frozenset(PROPERTY_DIMENSIONS), "data/properties")
    prov = _expect_object(
        obj["provenance"], frozenset({"source", "license", "edition", "method"}), "data/provenance"
    )
    dims = obj["dimensions"]
    warnings = obj["warnings"]
    if not isinstance(dims, list):
        raise invalid_type("data/dimensions", "lista", dims)
    if not isinstance(warnings, list):
        raise invalid_type("data/warnings", "lista", warnings)
    dimensions: list[ProfileDimension] = []
    for item in dims:
        entry = _expect_object(item, frozenset({"name", "value"}), "data/dimensions")
        raw = entry["value"]
        if isinstance(raw, bool) or not isinstance(raw, float):
            raise invalid_type("data/dimensions/value", "float", raw)
        dimensions.append(ProfileDimension(_as_text(entry["name"], "data/dimensions/name"), raw))
    return ProfileData(
        series=_as_text(obj["series"], "data/series"),
        designation=_as_text(obj["designation"], "data/designation"),
        family=ProfileFamily(_as_text(obj["family"], "data/family")),
        dimensions=tuple(dimensions),
        properties=ProfileProperties(
            **{name: _decode_property(props[name], f"data/properties/{name}") for name in props}
        ),
        provenance=Provenance(
            **{key: _as_text(prov[key], f"data/provenance/{key}") for key in prov}
        ),
        warnings=tuple(_as_text(item, "data/warnings") for item in warnings),
    )


def decode_profile(text: str) -> CatalogProfile:
    """Revisión desde su documento canónico, con validación estricta y todo o nada.

    Cualquier defecto (JSON inválido, claves de más o de menos, tipos, valores fuera de
    rango, estado no admitido) lanza ``catalog.invalid_document`` con la causa técnica.
    """
    try:
        document = json.loads(text, parse_constant=_reject_constant)
        obj = _expect_object(
            document, frozenset({"format", "id", "revision", "catalog_id", "data"}), "$"
        )
        if obj["format"] != DOCUMENT_FORMAT:
            raise catalog_error(
                DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                "catalog.invalid_document",
                {"path": "format", "missing": "-", "extra": str(obj["format"])},
            )
        revision = obj["revision"]
        if isinstance(revision, bool) or not isinstance(revision, int):
            raise invalid_type("revision", "entero", revision)
        return CatalogProfile(
            id=UUID(_as_text(obj["id"], "id")),
            revision=revision,
            catalog_id=UUID(_as_text(obj["catalog_id"], "catalog_id")),
            data=_decode_data(obj["data"]),
        )
    except (ValueError, TypeError, KeyError) as error:
        raise catalog_error(
            DiagnosticCode.INPUT_INVALID_TYPE,
            "catalog.invalid_document",
            {"path": "$", "missing": "-", "extra": "-"},
            cause=f"{type(error).__name__}: {error}",
        ) from error


def _reject_constant(name: str) -> float:
    raise ValueError(f"constante JSON no admitida: {name}")
