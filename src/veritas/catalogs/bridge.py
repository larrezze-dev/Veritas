"""Puente catálogo → núcleo: un ``CatalogProfile`` como sección asignable (ADR-010; T-024 E1).

Convierte una revisión de perfil del catálogo en un ``CatalogSnapshot`` (copia congelada con su
procedencia) y en una ``SectionRevision`` de la familia ``CATALOGO``. Es el único punto que une
``catalogs`` con ``core.sections``: ``core`` no importa este módulo y este módulo no abre el
archivo SQLite (recibe objetos de dominio).

No se deduce nada: las propiedades del perfil se copian con su estado y su procedencia; el
centroide y ``Iyz``, que el catálogo no aporta, quedan ``NO_DISPONIBLE`` con valor ``None`` (D3 = C:
E2 podrá declararlos derivados de la simetría en familias bisimétricas).
"""

from typing import Final
from uuid import UUID

from veritas.catalogs.errors import catalog_error
from veritas.catalogs.model import CatalogInfo, CatalogProfile, profile_sha256
from veritas.core.diagnostics import DiagnosticCode
from veritas.core.sections.catalog import (
    CatalogSnapshot,
    SnapshotDimension,
    SnapshotProvenance,
)
from veritas.core.sections.properties import PropertyStatus, SectionProperties, SectionProperty
from veritas.core.sections.section import SectionFamily, SectionRevision

ORIGIN_ABSENT: Final = "catálogo: sin dato"
METHOD_NOT_DERIVED: Final = "no deducido: el catálogo no lo aporta"


def snapshot_from_profile(profile: CatalogProfile, catalog: CatalogInfo) -> CatalogSnapshot:
    """Copia congelada de una revisión de perfil, con el hash de su documento canónico."""
    if profile.catalog_id != catalog.id:
        raise catalog_error(
            DiagnosticCode.REFERENCE_NOT_FOUND,
            "catalog.profile_not_in_catalog",
            {"profile": f"{profile.series}/{profile.designation}", "catalog": catalog.name},
        )
    props = profile.properties
    provenance = profile.provenance
    return CatalogSnapshot(
        catalog_id=catalog.id,
        catalog_name=catalog.name,
        catalog_source=catalog.source,
        catalog_license=catalog.license,
        catalog_edition=catalog.edition,
        profile_id=profile.id,
        profile_revision=profile.revision,
        series=profile.series,
        designation=profile.designation,
        shape=profile.family.value,
        source_sha256=profile_sha256(profile),
        dimensions=tuple(SnapshotDimension(d.name, d.value) for d in profile.data.dimensions),
        provenance=SnapshotProvenance(
            provenance.source, provenance.license, provenance.edition, provenance.method
        ),
        area=props.area,
        iy=props.iy,
        iz=props.iz,
        torsion_constant=props.torsion_constant,
        linear_mass=props.linear_mass,
        warnings=profile.data.warnings,
    )


def _absent(unit: str) -> SectionProperty:
    return SectionProperty(
        None, unit, ORIGIN_ABSENT, METHOD_NOT_DERIVED, PropertyStatus.NO_DISPONIBLE
    )


def section_from_snapshot(
    snapshot: CatalogSnapshot, *, section_id: UUID, revision: int = 1, name: str | None = None
) -> SectionRevision:
    """Sección de la familia ``CATALOGO``: A, Iy, Iz y J del snapshot; centroide e Iyz ausentes."""
    return SectionRevision(
        id=section_id,
        revision=revision,
        name=snapshot.label if name is None else name,
        family=SectionFamily.CATALOGO,
        parameters=snapshot,
        properties=SectionProperties(
            area=snapshot.area,
            centroid_y=_absent("m"),
            centroid_z=_absent("m"),
            iy=snapshot.iy,
            iz=snapshot.iz,
            iyz=_absent("m⁴"),
            torsion_constant=snapshot.torsion_constant,
        ),
    )


def section_from_profile(
    profile: CatalogProfile,
    catalog: CatalogInfo,
    *,
    section_id: UUID,
    revision: int = 1,
    name: str | None = None,
) -> SectionRevision:
    """Sección asignable a una barra a partir de una revisión de perfil del catálogo."""
    return section_from_snapshot(
        snapshot_from_profile(profile, catalog),
        section_id=section_id,
        revision=revision,
        name=name,
    )
