"""Copia congelada (snapshot) de un perfil de catálogo dentro del proyecto (ADR-010; T-024 E1).

Al asignar un perfil de catálogo a una barra, el proyecto no guarda una referencia al archivo
SQLite sino esta copia, con su procedencia: es lo que usan el solver, la persistencia y los
informes, y por eso el proyecto se abre sin el catálogo (DAT-003).

Reglas:

- Es un objeto de ``core``: no importa ``catalogs``, ``persistence`` ni ``sqlite3``. El puente
  desde ``CatalogProfile`` es ``veritas.catalogs.bridge``.
- Todo valor numérico está en SI. Una propiedad ausente es ``NO_DISPONIBLE`` con valor ``None``,
  nunca 0; el snapshot no deduce ni completa nada (J no sale de Iy + Iz).
- Solo admite los estados ``IMPORTADO_SIN_VERIFICAR`` y ``NO_DISPONIBLE``: un dato de catálogo
  nunca se presenta como calculado ni verificado por pasar por el proyecto.
- ``source_sha256`` es el hash de contenido de la revisión del catálogo de la que se copió. Aquí
  solo se valida su formato (``core`` no conoce el documento canónico del catálogo): lo calcula
  el puente y permite avisar de «desactualizado» sin abrir nada (T-024 E2).
"""

import re
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from veritas.core import units as u
from veritas.core.diagnostics import Diagnostic, DiagnosticCode, InputValueError, Location
from veritas.core.references import check_identity
from veritas.core.sections.properties import PropertyStatus, SectionProperty
from veritas.core.units import get_unit
from veritas.core.validation import checked_positive, invalid_type, positive_float, require_text

PROFILE_SHAPES: Final = frozenset(
    {"I", "U", "L", "T", "SHS", "RHS", "CHS", "RECT", "ROUND", "OTRO"}
)
"""Formas de perfil admitidas en un snapshot (las de ``catalogs.model.ProfileFamily``)."""

SNAPSHOT_STATUSES: Final = frozenset(
    {PropertyStatus.NO_DISPONIBLE, PropertyStatus.IMPORTADO_SIN_VERIFICAR}
)
"""Estados admitidos en las propiedades de un snapshot (ver el módulo)."""

SNAPSHOT_PROPERTY_DIMENSIONS: Final = {
    "area": u.AREA,
    "iy": u.SECOND_MOMENT,
    "iz": u.SECOND_MOMENT,
    "torsion_constant": u.SECOND_MOMENT,
    "linear_mass": u.LINEAR_MASS,
}
"""Propiedades que el snapshot copia del catálogo, en orden, y la dimensión de su unidad SI."""

_DIMENSION_NAME: Final = re.compile(r"[a-z][a-z0-9_]*")
_WARNING_KEY: Final = re.compile(r"[a-z]+\.[a-z_]+")
_SHA256: Final = re.compile(r"[0-9a-f]{64}")


def _invalid(key: str, field: str, params: dict[str, str] | None = None) -> InputValueError:
    return InputValueError(
        Diagnostic.create(
            DiagnosticCode.INPUT_OUT_OF_RANGE,
            key,
            {"field": field, **(params or {})},
            location=Location(field=field),
        )
    )


@dataclass(frozen=True, slots=True)
class SnapshotDimension:
    """Dimensión geométrica nominal copiada del perfil: en m y estrictamente positiva."""

    name: str
    value: float

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or _DIMENSION_NAME.fullmatch(self.name) is None:
            raise _invalid("section.catalog_invalid_name", "dimensions", {"name": str(self.name)})
        object.__setattr__(self, "value", positive_float(self.value, f"dimension.{self.name}"))


@dataclass(frozen=True, slots=True)
class SnapshotProvenance:
    """Procedencia del dato del perfil: fuente, licencia, edición y método. Todo obligatorio."""

    source: str
    license: str
    edition: str
    method: str

    def __post_init__(self) -> None:
        for name in self.__slots__:
            require_text(getattr(self, name), f"provenance.{name}")


@dataclass(frozen=True, slots=True)
class CatalogSnapshot:
    """Perfil de catálogo congelado: identidad de origen, contenido y procedencia."""

    catalog_id: UUID
    catalog_name: str
    catalog_source: str
    catalog_license: str
    catalog_edition: str
    profile_id: UUID
    profile_revision: int
    series: str
    designation: str
    shape: str
    source_sha256: str
    dimensions: tuple[SnapshotDimension, ...]
    provenance: SnapshotProvenance
    area: SectionProperty
    iy: SectionProperty
    iz: SectionProperty
    torsion_constant: SectionProperty
    linear_mass: SectionProperty
    warnings: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        check_identity(self.catalog_id, 1, "CatalogSnapshot.catalog_id")
        check_identity(self.profile_id, self.profile_revision, "CatalogSnapshot.profile")
        for name in ("catalog_name", "catalog_source", "catalog_license", "catalog_edition"):
            require_text(getattr(self, name), name)
        object.__setattr__(self, "series", require_text(self.series, "series").strip())
        object.__setattr__(
            self, "designation", require_text(self.designation, "designation").strip()
        )
        if not isinstance(self.shape, str) or self.shape not in PROFILE_SHAPES:
            raise _invalid("section.catalog_invalid_shape", "shape", {"shape": str(self.shape)})
        if not isinstance(self.source_sha256, str) or _SHA256.fullmatch(self.source_sha256) is None:
            raise _invalid("section.catalog_invalid_hash", "source_sha256")
        if not isinstance(self.provenance, SnapshotProvenance):
            raise invalid_type("provenance", "SnapshotProvenance", self.provenance)
        self._check_dimensions()
        for name in SNAPSHOT_PROPERTY_DIMENSIONS:
            check_snapshot_property(name, getattr(self, name))
        self._check_warnings()

    def _check_dimensions(self) -> None:
        if not isinstance(self.dimensions, tuple):
            raise invalid_type("dimensions", "tupla de SnapshotDimension", self.dimensions)
        seen: set[str] = set()
        for dimension in self.dimensions:
            if not isinstance(dimension, SnapshotDimension):
                raise invalid_type("dimensions", "SnapshotDimension", dimension)
            if dimension.name in seen:
                raise _invalid(
                    "section.catalog_duplicate_dimension", "dimensions", {"name": dimension.name}
                )
            seen.add(dimension.name)
        object.__setattr__(
            self, "dimensions", tuple(sorted(self.dimensions, key=lambda item: item.name))
        )

    def _check_warnings(self) -> None:
        if not isinstance(self.warnings, tuple):
            raise invalid_type("warnings", "tupla de textos", self.warnings)
        seen: set[str] = set()
        for key in self.warnings:
            if not isinstance(key, str) or _WARNING_KEY.fullmatch(key) is None or key in seen:
                raise _invalid("section.catalog_invalid_warning", "warnings", {"key": str(key)})
            seen.add(key)

    @property
    def key(self) -> tuple[str, str]:
        """Clave natural del perfil dentro de su catálogo: (serie, designación)."""
        return (self.series, self.designation)

    @property
    def label(self) -> str:
        """Texto de la referencia de catálogo: ``CATÁLOGO/SERIE/DESIGNACIÓN@REVISIÓN``."""
        return f"{self.catalog_name}/{self.series}/{self.designation}@{self.profile_revision}"


def check_snapshot_property(name: str, prop: object) -> None:
    """Estado, unidad SI y positividad de una propiedad copiada del catálogo."""
    at = Location(field=name)
    if not isinstance(prop, SectionProperty):
        raise invalid_type(name, "SectionProperty", prop, at)
    if prop.status not in SNAPSHOT_STATUSES:
        raise _invalid("section.catalog_invalid_status", name, {"status": str(prop.status)})
    if get_unit(prop.unit).dimension != SNAPSHOT_PROPERTY_DIMENSIONS[name]:
        raise _invalid("section.catalog_invalid_unit", name, {"unit": prop.unit})
    if prop.value is not None:
        checked_positive(prop.value, name, at)
