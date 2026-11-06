"""Auxiliares de las pruebas del catálogo (T-022 E1): datos sintéticos, sin perfiles reales."""

import itertools
import string
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from veritas.catalogs.errors import CatalogError
from veritas.catalogs.model import (
    ImportSource,
    ProfileData,
    ProfileDimension,
    ProfileFamily,
    ProfileProperties,
    Provenance,
    sha256_of,
)
from veritas.catalogs.store import CatalogStore
from veritas.core.diagnostics import Diagnostic
from veritas.core.messages_es import MESSAGES_ES, render_message
from veritas.core.sections.properties import PropertyStatus, SectionProperty

PROVENANCE = Provenance(
    source="SINTÉTICO (prueba)",
    license="Sin valor comercial",
    edition="2026",
    method="Generado en la prueba",
)


def uid(n: int) -> UUID:
    """UUID v4 determinista a partir de un entero."""
    return UUID(int=n | (4 << 76) | (0b10 << 62))


def id_factory(start: int = 1) -> Callable[[], UUID]:
    counter = itertools.count(start)
    return lambda: uid(next(counter))


def prop(value: float, unit: str) -> SectionProperty:
    return SectionProperty(
        value, unit, "SINTÉTICO", "importado", PropertyStatus.IMPORTADO_SIN_VERIFICAR
    )


def unavailable(unit: str) -> SectionProperty:
    return SectionProperty(None, unit, "SINTÉTICO", "no aportado", PropertyStatus.NO_DISPONIBLE)


def make_properties(
    area: float | None = 1.0e-3,
    iy: float | None = 2.0e-6,
    iz: float | None = 3.0e-7,
    torsion: float | None = None,
    mass: float | None = 7.85,
) -> ProfileProperties:
    def pick(value: float | None, unit: str) -> SectionProperty:
        return unavailable(unit) if value is None else prop(value, unit)

    return ProfileProperties(
        area=pick(area, "m²"),
        iy=pick(iy, "m⁴"),
        iz=pick(iz, "m⁴"),
        torsion_constant=pick(torsion, "m⁴"),
        linear_mass=pick(mass, "kg/m"),
    )


def make_data(  # noqa: PLR0913 (campos opcionales por nombre, para variar un caso)
    designation: str = "P-100",
    *,
    series: str = "SERIE-A",
    area: float | None = 1.0e-3,
    torsion: float | None = None,
    family: ProfileFamily = ProfileFamily.I,
    warnings: tuple[str, ...] = (),
    manufacturer: str | None = None,
    compatible_materials: tuple[str, ...] = (),
) -> ProfileData:
    return ProfileData(
        series=series,
        designation=designation,
        family=family,
        dimensions=(ProfileDimension("h", 0.1), ProfileDimension("b", 0.05)),
        properties=make_properties(area=area, torsion=torsion),
        provenance=PROVENANCE,
        warnings=warnings,
        manufacturer=manufacturer,
        compatible_materials=compatible_materials,
    )


def fixed_clock() -> datetime:
    """Reloj determinista de las pruebas (UTC)."""
    return datetime(2026, 11, 3, 12, 30, 45, tzinfo=UTC)


def make_source(
    rows_read: int = 1,
    rows_rejected: int = 0,
    *,
    file_name: str = "lote.csv",
    content: str = "contenido del archivo",
    mapping: str = '{"columns":{}}',
) -> ImportSource:
    """Origen de lote sintético, con los hashes coherentes con su contenido."""
    return ImportSource(
        file_name=file_name,
        file_format="CSV",
        file_sha256=sha256_of(content),
        mapping_json=mapping,
        mapping_sha256=sha256_of(mapping),
        rows_read=rows_read,
        rows_rejected=rows_rejected,
    )


def new_store(tmp_path: Path, name: str = "catalogo.sqlite") -> CatalogStore:
    return CatalogStore.create(tmp_path / name, id_factory=id_factory(), clock=fixed_clock)


def message_key(error: pytest.ExceptionInfo[CatalogError]) -> str:
    return error.value.diagnostic.message_key


def assert_renders(diagnostic: Diagnostic) -> None:
    """La clave existe, aporta todos los parámetros de su plantilla y se muestra completa."""
    template = MESSAGES_ES[diagnostic.message_key]
    needed = {name for _, name, _, _ in string.Formatter().parse(template) if name}
    assert needed <= set(diagnostic.params_dict()), (diagnostic.message_key, needed)
    assert "{" not in render_message(diagnostic)
