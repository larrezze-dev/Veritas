"""Puente catálogo → núcleo: un ``CatalogProfile`` como sección asignable (ADR-010; T-024 E1).

Convierte una revisión de perfil del catálogo en un ``CatalogSnapshot`` (copia congelada con su
procedencia) y en una ``SectionRevision`` de la familia ``CATALOGO``. Es el único punto que une
``catalogs`` con ``core.sections``: ``core`` no importa este módulo y este módulo no abre el
archivo SQLite (recibe objetos de dominio).

No se deduce nada: las propiedades del perfil se copian con su estado y su procedencia; el
centroide y ``Iyz``, que el catálogo no aporta, quedan ``NO_DISPONIBLE`` (valor ``None``, D3 = C);
desde T-024 E2 solo se derivan, marcados como tales, en formas doblemente simétricas.
"""

from enum import StrEnum
from typing import Final
from uuid import UUID

from veritas.catalogs.errors import catalog_error
from veritas.catalogs.model import CatalogInfo, CatalogProfile, profile_sha256
from veritas.core.diagnostics import DiagnosticCode
from veritas.core.geometry.entities import new_entity_id
from veritas.core.geometry.model import StructuralModel
from veritas.core.sections.catalog import (
    BISYMMETRIC_SHAPES,
    SYMMETRY_METHOD,
    SYMMETRY_ORIGIN,
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


def _symmetric(unit: str) -> SectionProperty:
    return SectionProperty(0.0, unit, SYMMETRY_ORIGIN, SYMMETRY_METHOD, PropertyStatus.CALCULADO)


def section_from_snapshot(
    snapshot: CatalogSnapshot,
    *,
    section_id: UUID,
    revision: int = 1,
    name: str | None = None,
    derive_symmetry: bool = True,
) -> SectionRevision:
    """Sección de la familia ``CATALOGO``: A, Iy, Iz y J del snapshot.

    Con ``derive_symmetry`` y una forma doblemente simétrica (``BISYMMETRIC_SHAPES``), el
    centroide y ``Iyz`` son 0 derivados de la simetría (``CALCULADO``, marcados como tales:
    TK-026). Para el resto de formas, o sin ``derive_symmetry``, quedan ``NO_DISPONIBLE``.
    """
    derived = derive_symmetry and snapshot.shape in BISYMMETRIC_SHAPES
    centroid = _symmetric("m") if derived else _absent("m")
    return SectionRevision(
        id=section_id,
        revision=revision,
        name=snapshot.label if name is None else name,
        family=SectionFamily.CATALOGO,
        parameters=snapshot,
        properties=SectionProperties(
            area=snapshot.area,
            centroid_y=centroid,
            centroid_z=centroid,
            iy=snapshot.iy,
            iz=snapshot.iz,
            iyz=_symmetric("m⁴") if derived else _absent("m⁴"),
            torsion_constant=snapshot.torsion_constant,
        ),
    )


def section_from_profile(  # noqa: PLR0913 (identidad y opciones de la sección, por nombre)
    profile: CatalogProfile,
    catalog: CatalogInfo,
    *,
    section_id: UUID,
    revision: int = 1,
    name: str | None = None,
    derive_symmetry: bool = True,
) -> SectionRevision:
    """Sección asignable a una barra a partir de una revisión de perfil del catálogo."""
    return section_from_snapshot(
        snapshot_from_profile(profile, catalog),
        section_id=section_id,
        revision=revision,
        name=name,
        derive_symmetry=derive_symmetry,
    )


def ensure_section(model: StructuralModel, snapshot: CatalogSnapshot) -> SectionRevision:
    """Sección de catálogo del modelo para ``snapshot``: la existente idéntica o una nueva.

    Varias barras con el mismo perfil y la misma revisión comparten una única revisión de
    sección en el proyecto (no una copia por barra).
    """
    for existing in model.snapshot().sections:
        if existing.family is not SectionFamily.CATALOGO or existing.parameters != snapshot:
            continue
        rebuilt = section_from_snapshot(
            snapshot, section_id=existing.id, revision=existing.revision, name=existing.name
        )
        if rebuilt == existing:
            return existing
    return model.register_section(section_from_snapshot(snapshot, section_id=new_entity_id()))


class Freshness(StrEnum):
    """Estado de un snapshot frente al catálogo consultado (D8-A, solo informativo)."""

    CURRENT = "VIGENTE"
    NEWER_REVISION = "REVISION_POSTERIOR"
    CONTENT_DIFFERS = "CONTENIDO_DISTINTO"
    MISSING = "AUSENTE"


def compare_with_catalog(snapshot: CatalogSnapshot, latest: CatalogProfile | None) -> Freshness:
    """Compara el snapshot con la última revisión del perfil en el catálogo (o ``None``)."""
    if latest is None or latest.id != snapshot.profile_id:
        return Freshness.MISSING
    if latest.revision > snapshot.profile_revision:
        return Freshness.NEWER_REVISION
    if latest.revision == snapshot.profile_revision and profile_sha256(latest) == (
        snapshot.source_sha256
    ):
        return Freshness.CURRENT
    return Freshness.CONTENT_DIFFERS
