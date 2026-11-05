"""Snapshot de perfil de catálogo en el proyecto (T-024 E1, ADR-010).

Dominio, puente, ida y vuelta exacta sin SQLite, esquema v4, migración 3 → 4, integridad ante
manipulación y arquitectura. Datos sintéticos (``catalog_support``): ningún perfil real.
"""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from catalog_support import (
    PROVENANCE,
    assert_renders,
    make_data,
    make_properties,
    prop,
    uid,
    unavailable,
)
from test_layer_isolation import PACKAGE_ROOT, imported_modules
from veritas.catalogs import bridge
from veritas.catalogs.errors import CatalogError
from veritas.catalogs.model import (
    CatalogInfo,
    CatalogProfile,
    ProfileFamily,
    profile_sha256,
)
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticCode, DiagnosticError
from veritas.core.geometry.model import StructuralModel
from veritas.core.materials.material import MaterialFamily
from veritas.core.sections.catalog import (
    PROFILE_SHAPES,
    CatalogSnapshot,
    SnapshotDimension,
    SnapshotProvenance,
)
from veritas.core.sections.properties import PropertyStatus, SectionProperty
from veritas.core.sections.section import SectionFamily, SectionRevision
from veritas.persistence.canonical import canonical_bytes, sha256_hex
from veritas.persistence.errors import IntegrityError
from veritas.persistence.migrations import CURRENT_SCHEMA_VERSION, MIGRATIONS, migrate
from veritas.persistence.project import open_project, save_project

CATALOG = CatalogInfo(
    id=uid(900),
    name="CATALOGO-SINTETICO",
    description="",
    source="SINTÉTICO (prueba)",
    license="Sin valor comercial",
    edition="2026",
    protected=False,
)


def profile(
    designation: str = "P-100",
    *,
    torsion: float | None = 4.0e-8,
    family: ProfileFamily = ProfileFamily.I,
    warnings: tuple[str, ...] = (),
    revision: int = 1,
) -> CatalogProfile:
    data = make_data(designation, family=family, warnings=warnings)
    return CatalogProfile(
        id=uid(700),
        revision=revision,
        catalog_id=CATALOG.id,
        data=replace(data, properties=make_properties(torsion=torsion)),
    )


def snapshot(**changes: Any) -> CatalogSnapshot:
    return replace(bridge.snapshot_from_profile(profile(), CATALOG), **changes)


def section(**changes: Any) -> SectionRevision:
    return bridge.section_from_snapshot(snapshot(**changes), section_id=uid(800))


def model_with(sec: SectionRevision) -> tuple[StructuralModel, UUID]:
    model = StructuralModel()
    a = model.add_node(u.Quantity.of(0, "m"), u.Quantity.of(0, "m"), u.Quantity.of(0, "m"))
    b = model.add_node(u.Quantity.of(3, "m"), u.Quantity.of(0, "m"), u.Quantity.of(0, "m"))
    steel = model.add_material(
        name="Acero",
        family=MaterialFamily.STEEL,
        source="CTE DB-SE-A",
        standard_edition="2019",
        elastic_modulus=u.Quantity.of(210_000, "N/mm²"),
        shear_modulus=u.Quantity.of(81_000, "N/mm²"),
        poisson_ratio=0.3,
        density=u.Quantity.of(7850, "kg/m³"),
        specific_weight=u.Quantity.of(78.5, "kN/m³"),
    )
    registered = model.register_section(sec)
    member = model.add_member(a.id, b.id, material=steel.ref, section=registered.ref)
    return model, member.id


# --- Puente y dominio -------------------------------------------------------------------


def test_bridge_copies_profile_with_provenance_and_hash() -> None:
    prof = profile()
    snap = bridge.snapshot_from_profile(prof, CATALOG)
    assert snap.catalog_id == CATALOG.id
    assert (snap.catalog_name, snap.catalog_license, snap.catalog_edition) == (
        CATALOG.name,
        CATALOG.license,
        CATALOG.edition,
    )
    assert (snap.profile_id, snap.profile_revision) == (prof.id, prof.revision)
    assert snap.key == ("SERIE-A", "P-100")
    assert snap.shape == "I"
    assert snap.source_sha256 == profile_sha256(prof)
    assert snap.provenance == SnapshotProvenance(
        PROVENANCE.source, PROVENANCE.license, PROVENANCE.edition, PROVENANCE.method
    )
    assert [(d.name, d.value) for d in snap.dimensions] == [("b", 0.05), ("h", 0.1)]
    assert snap.area == prof.properties.area
    assert snap.torsion_constant == prof.properties.torsion_constant
    assert snap.linear_mass == prof.properties.linear_mass
    assert snap.label == "CATALOGO-SINTETICO/SERIE-A/P-100@1"


def test_shapes_match_catalog_families() -> None:
    assert {f.value for f in ProfileFamily} == PROFILE_SHAPES


def test_bridge_rejects_a_profile_of_another_catalog() -> None:
    other = replace(CATALOG, id=uid(901), name="OTRO")
    with pytest.raises(CatalogError) as info:
        bridge.snapshot_from_profile(profile(), other)
    assert info.value.diagnostic.message_key == "catalog.profile_not_in_catalog"
    assert_renders(info.value.diagnostic)


def test_section_of_a_catalog_profile_deduces_nothing() -> None:
    sec = bridge.section_from_profile(
        profile(torsion=None), CATALOG, section_id=uid(800), derive_symmetry=False
    )
    assert sec.family is SectionFamily.CATALOGO
    assert isinstance(sec.parameters, CatalogSnapshot)
    props = sec.properties
    assert props.torsion_constant.value is None  # J nunca se deduce de Iy + Iz
    assert props.torsion_constant.status is PropertyStatus.NO_DISPONIBLE
    for name in ("centroid_y", "centroid_z", "iyz"):
        absent = getattr(props, name)
        assert absent.value is None
        assert absent.status is PropertyStatus.NO_DISPONIBLE
    assert props.area.status is PropertyStatus.IMPORTADO_SIN_VERIFICAR
    assert sec.name == "CATALOGO-SINTETICO/SERIE-A/P-100@1"


def test_require_rectangular_is_explicit_for_catalog_sections() -> None:
    with pytest.raises(DiagnosticError) as info:
        section().require_rectangular()
    assert info.value.code is DiagnosticCode.UNSUPPORTED_CONFIGURATION
    assert info.value.diagnostic.message_key == "section.not_rectangular"
    assert_renders(info.value.diagnostic)


def test_revising_a_catalog_section_as_rectangle_is_rejected() -> None:
    model, _ = model_with(section())
    with pytest.raises(DiagnosticError) as info:
        model.revise_rectangular_section(uid(800), name="otra")
    assert info.value.diagnostic.message_key == "section.not_rectangular"


def test_rectangular_sections_are_unchanged() -> None:
    model = StructuralModel()
    rect = model.add_rectangular_section(b=u.Quantity.of(0.2, "m"), h=u.Quantity.of(0.4, "m"))
    assert rect.require_rectangular().h == 0.4


@pytest.mark.parametrize(
    ("changes", "key"),
    [
        ({"shape": "ZETA"}, "section.catalog_invalid_shape"),
        ({"source_sha256": "AB" * 32}, "section.catalog_invalid_hash"),
        ({"source_sha256": "ab"}, "section.catalog_invalid_hash"),
        (
            {"dimensions": (SnapshotDimension("b", 0.1), SnapshotDimension("b", 0.2))},
            "section.catalog_duplicate_dimension",
        ),
        ({"warnings": ("sin-punto",)}, "section.catalog_invalid_warning"),
        ({"warnings": ("a.b", "a.b")}, "section.catalog_invalid_warning"),
        (
            {"area": SectionProperty(1.0e-3, "m²", "x", "y", PropertyStatus.CALCULADO)},
            "section.catalog_invalid_status",
        ),
        (
            {"iy": SectionProperty(2.0e-6, "m⁴", "x", "y", PropertyStatus.VERIFICADO)},
            "section.catalog_invalid_status",
        ),
        ({"linear_mass": prop(7.85, "m²")}, "section.catalog_invalid_unit"),
        ({"area": prop(1.0e-3, "m⁴")}, "section.catalog_invalid_unit"),
    ],
)
def test_snapshot_rejects_invalid_content(changes: dict[str, Any], key: str) -> None:
    with pytest.raises(DiagnosticError) as info:
        snapshot(**changes)
    assert info.value.diagnostic.message_key == key
    assert_renders(info.value.diagnostic)


def test_snapshot_rejects_nonpositive_values_and_bad_types() -> None:
    with pytest.raises(DiagnosticError):
        snapshot(area=prop(-1.0, "m²"))
    with pytest.raises(DiagnosticError):
        SnapshotDimension("b", 0.0)
    with pytest.raises(DiagnosticError):
        SnapshotDimension("B", 0.1)
    with pytest.raises(DiagnosticError):
        snapshot(provenance="no")
    with pytest.raises(DiagnosticError):
        snapshot(catalog_name="")
    with pytest.raises((DiagnosticError, ValueError)):
        snapshot(profile_revision=0)


def test_section_rejects_properties_that_differ_from_the_snapshot() -> None:
    sec = section()
    tampered = replace(sec.properties, area=prop(9.9e-3, "m²"))
    with pytest.raises(DiagnosticError) as info:
        replace(sec, properties=tampered)
    assert info.value.diagnostic.message_key == "section.catalog_properties_mismatch"
    assert_renders(info.value.diagnostic)


@pytest.mark.parametrize("name", ["centroid_y", "centroid_z", "iyz"])
def test_section_rejects_derived_centroid_and_product_of_inertia(name: str) -> None:
    sec = section()
    unit = "m⁴" if name == "iyz" else "m"
    derived = SectionProperty(0.0, unit, "simetría", "deducido", PropertyStatus.CALCULADO)
    with pytest.raises(DiagnosticError) as info:
        replace(sec, properties=replace(sec.properties, **{name: derived}))
    assert info.value.diagnostic.message_key == "section.catalog_not_derivable"
    assert_renders(info.value.diagnostic)


def test_family_and_parameters_must_agree() -> None:
    sec = section()
    with pytest.raises(DiagnosticError) as info:
        replace(sec, family=SectionFamily.RECTANGULAR)
    assert info.value.diagnostic.message_key == "section.family_mismatch"
    assert_renders(info.value.diagnostic)
    rect = StructuralModel().add_rectangular_section(
        b=u.Quantity.of(0.2, "m"), h=u.Quantity.of(0.4, "m")
    )
    with pytest.raises(DiagnosticError):
        replace(rect, family=SectionFamily.CATALOGO)
    with pytest.raises(DiagnosticError):
        replace(sec, parameters="x")  # type: ignore[arg-type]


# --- Ida y vuelta, sin SQLite ------------------------------------------------------------


def test_round_trip_is_exact_and_needs_no_catalog(tmp_path: Path) -> None:
    prof = profile(warnings=("importer.missing_j",))
    sec = bridge.section_from_profile(prof, CATALOG, section_id=uid(800))
    model, member_id = model_with(sec)
    path = tmp_path / "p.json"
    saved = save_project(path, model)
    opened = open_project(path)  # no existe ningún archivo SQLite: el proyecto es autocontenido
    assert not list(tmp_path.glob("*.sqlite"))
    assert not list(tmp_path.glob("*.db"))
    assert opened.schema_version == CURRENT_SCHEMA_VERSION == 4
    assert opened.model_hash == saved.model_hash
    assert opened.integrity == saved.integrity
    member = opened.model.member(member_id)
    loaded = opened.model.section(member.section)
    assert loaded == sec
    assert repr(loaded) == repr(sec)
    assert isinstance(loaded.parameters, CatalogSnapshot)
    assert loaded.parameters.source_sha256 == profile_sha256(prof)
    assert loaded.parameters.warnings == ("importer.missing_j",)


def test_missing_properties_stay_none_after_round_trip(tmp_path: Path) -> None:
    sec = bridge.section_from_profile(
        profile(torsion=None), CATALOG, section_id=uid(800), derive_symmetry=False
    )
    model, member_id = model_with(sec)
    path = tmp_path / "p.json"
    save_project(path, model)
    loaded = open_project(path).model.section(sec.ref)
    assert loaded.properties.torsion_constant.value is None
    assert loaded.properties.iyz.value is None
    data = json.loads(path.read_text(encoding="utf-8"))
    section_json = data["content"]["model"]["sections"][0]
    assert section_json["family"] == "CATALOGO"
    assert section_json["properties"]["torsion_constant"]["value"] is None
    assert member_id


def test_negative_zero_and_floats_survive_exactly(tmp_path: Path) -> None:
    sec = bridge.section_from_profile(profile(), CATALOG, section_id=uid(800))
    model, _ = model_with(sec)
    path = tmp_path / "p.json"
    save_project(path, model)
    loaded = open_project(path).model.section(sec.ref)
    assert loaded.parameters == sec.parameters
    assert [d.value for d in loaded.parameters.dimensions] == [0.05, 0.1]  # type: ignore[union-attr]


# --- Integridad ante manipulación -------------------------------------------------------


def _saved(tmp_path: Path) -> Path:
    sec = bridge.section_from_profile(profile(), CATALOG, section_id=uid(800))
    model, _ = model_with(sec)
    path = tmp_path / "p.json"
    save_project(path, model)
    return path


def _edit(path: Path, mutate: Any, *, sign: bool) -> Path:
    data = json.loads(path.read_text(encoding="utf-8"))
    mutate(data["content"]["model"]["sections"][0])
    if sign:
        body = {k: v for k, v in data.items() if k != "integrity"}
        data["integrity"] = {"algorithm": "sha256", "digest": sha256_hex(canonical_bytes(body))}
    target = path.with_name("edited.json")
    target.write_text(json.dumps(data), encoding="utf-8")
    return target


def test_tampered_snapshot_without_resigning_fails_integrity(tmp_path: Path) -> None:
    def change(sec: dict[str, Any]) -> None:
        sec["parameters"]["area"]["value"] = 9.9e-3
        sec["properties"]["area"]["value"] = 9.9e-3

    with pytest.raises(DiagnosticError) as info:
        open_project(_edit(_saved(tmp_path), change, sign=False))
    assert info.value.diagnostic.message_key.startswith("project.")


def test_resigned_snapshot_inconsistent_with_properties_is_rejected(tmp_path: Path) -> None:
    def change(sec: dict[str, Any]) -> None:
        sec["parameters"]["area"]["value"] = 9.9e-3  # propiedades de la sección sin tocar

    with pytest.raises(DiagnosticError) as info:
        open_project(_edit(_saved(tmp_path), change, sign=True))
    assert info.value.diagnostic.message_key == "section.catalog_properties_mismatch"


def test_resigned_snapshot_value_change_is_caught_by_the_model_hash(tmp_path: Path) -> None:
    """Un cambio coherente y refirmado del snapshot no coincide con el hash del modelo."""

    def change(sec: dict[str, Any]) -> None:
        sec["parameters"]["area"]["value"] = 9.9e-3
        sec["properties"]["area"]["value"] = 9.9e-3

    with pytest.raises(IntegrityError):
        open_project(_edit(_saved(tmp_path), change, sign=True))


@pytest.mark.parametrize(
    ("mutate", "key"),
    [
        (lambda s: s["parameters"].update(extra=1), "project.unknown_fields"),
        (lambda s: s["parameters"].pop("catalog"), "project.missing_fields"),
        (lambda s: s["parameters"]["profile"].pop("source_sha256"), "project.missing_fields"),
        (lambda s: s["parameters"].update(dimensions="x"), "project.invalid_field"),
        (lambda s: s["parameters"].update(warnings=[1]), "project.invalid_field"),
        (
            lambda s: s["parameters"]["profile"].update(shape="ZETA"),
            "section.catalog_invalid_shape",
        ),
        (
            lambda s: s["parameters"]["profile"].update(source_sha256="x"),
            "section.catalog_invalid_hash",
        ),
        (
            lambda s: s["parameters"]["area"].update(status="VERIFICADO"),
            "section.catalog_invalid_status",
        ),
        (lambda s: s.update(family="RECTANGULAR"), "project.unknown_fields"),
        (lambda s: s.update(family="OTRA"), "project.invalid_field"),
    ],
)
def test_malformed_snapshot_is_rejected(tmp_path: Path, mutate: Any, key: str) -> None:
    with pytest.raises(DiagnosticError) as info:
        open_project(_edit(_saved(tmp_path), mutate, sign=True))
    assert info.value.diagnostic.message_key == key


# --- Migración 3 → 4 --------------------------------------------------------------------


def _v3_content(family: str = "RECTANGULAR") -> dict[str, object]:
    return {"model": {"sections": [{"family": family}]}, "settings": {}, "runs": [], "norms": {}}


def test_migration_3_to_4_keeps_rectangular_content() -> None:
    content = _v3_content()
    migrated = migrate(content, 3)
    assert migrated == content
    assert migrated is not content
    assert 3 in MIGRATIONS
    assert CURRENT_SCHEMA_VERSION == 4


def test_migration_3_to_4_rejects_a_foreign_family() -> None:
    with pytest.raises(DiagnosticError) as info:
        migrate(_v3_content("CATALOGO"), 3)
    assert info.value.diagnostic.message_key == "project.invalid_field"


def test_v3_file_opens_and_resaves_as_v4(tmp_path: Path) -> None:
    model = StructuralModel()
    model.add_rectangular_section(b=u.Quantity.of(0.2, "m"), h=u.Quantity.of(0.4, "m"))
    path = tmp_path / "p.json"
    save_project(path, model)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["schema_version"] = 3
    body = {k: v for k, v in data.items() if k != "integrity"}
    data["integrity"] = {"algorithm": "sha256", "digest": sha256_hex(canonical_bytes(body))}
    old = tmp_path / "v3.json"
    old.write_text(json.dumps(data), encoding="utf-8")
    opened = open_project(old)
    assert opened.schema_version == 3  # versión del archivo; el contenido se migró al abrir
    assert len(opened.model.snapshot().sections) == 1


# --- Arquitectura ------------------------------------------------------------------------


def _tops(path: Path) -> set[str]:
    return {name.split(".")[0] for name in imported_modules(path, PACKAGE_ROOT)}


def test_snapshot_module_is_core_only() -> None:
    path = PACKAGE_ROOT / "core" / "sections" / "catalog.py"
    names = imported_modules(path, PACKAGE_ROOT)
    forbidden = ("catalogs", "persistence", "fem", "gui", "reports")
    for layer in forbidden:
        assert not any(n == f"veritas.{layer}" or n.startswith(f"veritas.{layer}.") for n in names)
    assert "sqlite3" not in _tops(path)
    assert "PySide6" not in _tops(path)


def test_only_the_bridge_joins_catalogs_and_sections() -> None:
    names = imported_modules(PACKAGE_ROOT / "catalogs" / "bridge.py", PACKAGE_ROOT)
    assert "veritas.core.sections.catalog" in names
    assert "veritas.catalogs.model" in names
    assert "sqlite3" not in _tops(PACKAGE_ROOT / "catalogs" / "bridge.py")
    importers = {
        path.relative_to(PACKAGE_ROOT).as_posix()
        for path in PACKAGE_ROOT.rglob("*.py")
        if "veritas.catalogs.bridge" in imported_modules(path, PACKAGE_ROOT)
    }
    assert "core" not in {p.split("/")[0] for p in importers}


def test_unavailable_helper_is_consistent() -> None:
    assert unavailable("m²").status is PropertyStatus.NO_DISPONIBLE
