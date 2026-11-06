"""Fabricante y materiales compatibles en el proyecto y bloqueo (T-028 E2, ADR-012).

Snapshot, esquema v6 con migración 5 → 6 (proyecto v5 auténtico), reglas R1–R5 de
``compatibility_errors`` y su efecto en la asignación y sustitución. Datos sintéticos.
"""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from catalog_support import assert_renders, make_data, make_properties, new_store
from test_catalog_assign import (
    A_SYN,
    IY_SYN,
    IZ_SYN,
    J_SYN,
    REF,
    ipe_like,
    mixed_model,
    run,
)
from test_catalog_snapshot import CATALOG
from test_persistence_integrity import edit
from veritas.catalogs import bridge
from veritas.catalogs.model import CatalogProfile, ProfileFamily, profile_sha256
from veritas.core.diagnostics import DiagnosticError
from veritas.core.geometry.replacement import ReplacementStatus, compatibility_errors
from veritas.core.materials.material import MaterialFamily
from veritas.core.sections.catalog import CatalogSnapshot
from veritas.persistence.canonical import model_hash
from veritas.persistence.errors import IntegrityError
from veritas.persistence.migrations import CURRENT_SCHEMA_VERSION, MIGRATIONS
from veritas.persistence.project import open_project, save_project

REFERENCE_V5 = Path(__file__).parent / "data" / "proyecto_v5_ref.json"
REFERENCE_V5_HASH = "d059a420697346091d65ebb9ed0e22b2920a642e4703e103406d9f4561abec16"


def declared(
    materials: tuple[str, ...] = (),
    manufacturer: str | None = None,
    family: ProfileFamily = ProfileFamily.I,
) -> CatalogProfile:
    base: CatalogProfile = ipe_like(family=family)
    return replace(
        base,
        data=replace(base.data, manufacturer=manufacturer, compatible_materials=materials),
    )


def snap(
    *materials: str, manufacturer: str | None = None, family: ProfileFamily = ProfileFamily.I
) -> CatalogSnapshot:
    return bridge.snapshot_from_profile(declared(materials, manufacturer, family), CATALOG)


# --- Snapshot y puente -------------------------------------------------------------------


def test_snapshot_defaults_to_undeclared_and_copies_the_profile_fields() -> None:
    plain = bridge.snapshot_from_profile(ipe_like(), CATALOG)
    assert plain.manufacturer is None
    assert plain.compatible_materials == ()
    full = snap("TIMBER", "STEEL", "STEEL", manufacturer="  Acme  ")
    assert full.manufacturer == "Acme"
    assert full.compatible_materials == ("STEEL", "TIMBER")  # orden de MaterialFamily, sin repetir


def test_snapshot_rejects_invalid_fields() -> None:
    base = snap()
    for changes in (
        {"manufacturer": "  "},
        {"compatible_materials": ("CONCRETE",)},
        {"compatible_materials": ["STEEL"]},
    ):
        with pytest.raises(DiagnosticError):
            replace(base, **changes)


def test_source_hash_changes_only_when_the_profile_declares_something() -> None:
    assert profile_sha256(declared()) == profile_sha256(ipe_like())
    assert profile_sha256(declared(("STEEL",))) != profile_sha256(ipe_like())
    assert snap("STEEL").source_sha256 == profile_sha256(declared(("STEEL",)))


# --- Esquema v6 ----------------------------------------------------------------------------


def project_with(snapshot: CatalogSnapshot, tmp_path: Path) -> Path:
    model, _, _ = mixed_model()
    bridge.ensure_section(model, snapshot)
    path = tmp_path / "p.json"
    save_project(path, model)
    return path


def catalog_parameters(path: Path) -> dict[str, Any]:
    sections = json.loads(path.read_text(encoding="utf-8"))["content"]["model"]["sections"]
    found: list[dict[str, Any]] = [s["parameters"] for s in sections if s["family"] == "CATALOGO"]
    return found[-1]


def test_v6_round_trip_keeps_manufacturer_and_materials(tmp_path: Path) -> None:
    path = project_with(snap("STEEL", "TIMBER", manufacturer="Acme"), tmp_path)
    parameters = catalog_parameters(path)
    assert parameters["manufacturer"] == "Acme"
    assert parameters["compatible_materials"] == ["STEEL", "TIMBER"]
    opened = open_project(path)
    assert opened.schema_version == CURRENT_SCHEMA_VERSION == 6
    restored = [
        s.parameters
        for s in opened.model.snapshot().sections
        if isinstance(s.parameters, CatalogSnapshot)
    ][-1]
    assert (restored.manufacturer, restored.compatible_materials) == ("Acme", ("STEEL", "TIMBER"))
    assert opened.model_hash == model_hash(opened.model.snapshot())


def test_keys_are_absent_without_data_so_existing_hashes_do_not_move(tmp_path: Path) -> None:
    path = project_with(snap(), tmp_path)
    assert set(catalog_parameters(path)) == {
        "catalog",
        "profile",
        "dimensions",
        "provenance",
        "area",
        "iy",
        "iz",
        "torsion_constant",
        "linear_mass",
        "warnings",
    }


def test_a_real_v5_project_opens_under_v6_with_identical_hash_and_undeclared() -> None:
    raw = json.loads(REFERENCE_V5.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 5
    assert raw["model_hash"] == REFERENCE_V5_HASH  # guardado por el código v5, antes del cambio
    opened = open_project(REFERENCE_V5)
    assert opened.schema_version == 5
    assert opened.model_hash == model_hash(opened.model.snapshot()) == REFERENCE_V5_HASH
    snaps = [
        s.parameters
        for s in opened.model.snapshot().sections
        if isinstance(s.parameters, CatalogSnapshot)
    ]
    assert snaps
    assert all(s.manufacturer is None and s.compatible_materials == () for s in snaps)


def test_resaving_the_v5_project_as_v6_keeps_the_hash(tmp_path: Path) -> None:
    opened = open_project(REFERENCE_V5)
    saved = save_project(tmp_path / "v6.json", opened.model, opened.user_profiles, runs=opened.runs)
    assert saved.schema_version == 6
    assert open_project(tmp_path / "v6.json").model_hash == REFERENCE_V5_HASH


def test_migration_5_to_6_only_verifies_and_rejects_v6_keys_in_a_v5_file() -> None:
    migrate = MIGRATIONS[5]
    raw = json.loads(REFERENCE_V5.read_text(encoding="utf-8"))["content"]
    assert migrate(raw) == raw
    sections = raw["model"]["sections"]
    index = next(i for i, s in enumerate(sections) if s["family"] == "CATALOGO")
    sections[index]["parameters"]["compatible_materials"] = ["STEEL"]
    with pytest.raises(DiagnosticError):
        migrate(raw)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("compatible_materials", []),
        ("compatible_materials", ["CONCRETE"]),
        ("compatible_materials", "STEEL"),
        ("manufacturer", None),
    ],
)
def test_empty_or_invalid_v6_fields_are_rejected_on_open(
    tmp_path: Path, field: str, value: object
) -> None:
    path = project_with(snap("STEEL", manufacturer="Acme"), tmp_path)

    def mutate(data: dict[str, Any]) -> None:
        sections = data["content"]["model"]["sections"]
        [s for s in sections if s["family"] == "CATALOGO"][-1]["parameters"][field] = value

    with pytest.raises(DiagnosticError) as info:  # firmado de nuevo: llega al decodificador
        open_project(edit(path, mutate))
    assert not isinstance(info.value, IntegrityError)


# --- Reglas R1–R5 ----------------------------------------------------------------------------


def material_of(model: Any, family: MaterialFamily) -> Any:
    return next(m for m in model.snapshot().materials if m.family is family)


@pytest.mark.parametrize(
    ("declared_families", "family", "blocked"),
    [
        (("STEEL",), MaterialFamily.STEEL, False),
        (("ALUMINIUM",), MaterialFamily.STEEL, True),  # R1
        (("ALUMINIUM", "STEEL"), MaterialFamily.STEEL, False),
        ((), MaterialFamily.STEEL, False),  # R2: sin declarar
        (("STEEL",), MaterialFamily.TIMBER, True),  # R1 y R3 (forma metálica)
    ],
)
def test_declared_materials_block_when_the_bar_family_is_not_listed(
    declared_families: tuple[str, ...], family: MaterialFamily, blocked: bool
) -> None:
    model, _, _ = mixed_model()
    section = bridge.ensure_section(model, snap(*declared_families))
    errors = compatibility_errors(section, material_of(model, family), "B1")
    assert bool(errors) is blocked
    for error in errors:
        assert_renders(error)


def test_the_blocking_message_lists_the_declared_materials() -> None:
    model, _, _ = mixed_model()
    section = bridge.ensure_section(model, snap("ALUMINIUM", "TIMBER", family=ProfileFamily.RECT))
    (error,) = compatibility_errors(section, material_of(model, MaterialFamily.STEEL), "A")
    assert error.message_key == "section.replace_material_not_declared"
    assert dict(error.params)["declared"] == "ALUMINIUM, TIMBER"
    assert_renders(error)


def test_both_rules_accumulate_for_a_metal_shape_in_timber() -> None:
    model, _, _ = mixed_model()
    section = bridge.ensure_section(model, snap("STEEL"))
    errors = compatibility_errors(section, material_of(model, MaterialFamily.TIMBER), "T")
    assert [e.message_key for e in errors] == [
        "section.replace_incompatible_material",
        "section.replace_material_not_declared",
    ]


def test_declared_timber_does_not_unblock_a_metal_shape() -> None:  # R3
    model, _, _ = mixed_model()
    section = bridge.ensure_section(model, snap("TIMBER"))
    errors = compatibility_errors(section, material_of(model, MaterialFamily.TIMBER), "T")
    assert [e.message_key for e in errors] == ["section.replace_incompatible_material"]


def test_non_metal_shape_in_timber_without_declaration_is_not_blocked() -> None:
    model, _, _ = mixed_model()
    section = bridge.ensure_section(model, snap(family=ProfileFamily.RECT))
    assert compatibility_errors(section, material_of(model, MaterialFamily.TIMBER), "T") == ()


def test_replacement_strict_and_partial_use_the_declaration() -> None:
    model, ids, _ = mixed_model()
    target = bridge.ensure_section(model, snap("ALUMINIUM"))
    before = model.snapshot()
    strict = model.replace_sections([ids["A"], ids["B"]], target.ref)
    assert {m.status for m in strict.members} == {ReplacementStatus.BLOCKED}
    assert model.snapshot() == before
    partial = model.replace_sections([ids["A"], ids["T"]], target.ref, partial=True)
    assert {m.status for m in partial.members} == {ReplacementStatus.BLOCKED}
    assert not partial.changed


def test_the_block_is_kept_when_the_project_is_opened_without_the_catalog(
    tmp_path: Path,
) -> None:
    path = project_with(snap("ALUMINIUM"), tmp_path)
    opened = open_project(path)
    target = [
        s for s in opened.model.snapshot().sections if isinstance(s.parameters, CatalogSnapshot)
    ][-1]
    steel_ids = [m.id for m in opened.model.members.values() if m.label in ("A", "B")]
    report = opened.model.replace_sections(steel_ids, target.ref, partial=True)
    assert {m.status for m in report.members} == {ReplacementStatus.BLOCKED}


# --- CLI ---------------------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    store = new_store(tmp_path, "cat.sqlite")
    info = store.create_catalog(name="CAT", source="SINTÉTICO", license="Sin valor", edition="2026")
    props = make_properties(area=A_SYN, iy=IY_SYN, iz=IZ_SYN, torsion=J_SYN)
    only_alu = replace(
        make_data(
            "IPE-SINT",
            series="SINT-I",
            manufacturer="Acme",
            compatible_materials=("ALUMINIUM",),
        ),
        properties=props,
    )
    both = replace(
        make_data("IPE-MIX", series="SINT-I", compatible_materials=("ALUMINIUM", "STEEL")),
        properties=props,
    )
    store.put_profiles(info.id, [only_alu, both])
    store.close()
    model, _, _ = mixed_model()
    save_project(tmp_path / "p.json", model)
    return tmp_path


def test_cli_blocks_assignment_with_the_declared_list(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (workspace / "p.json").read_bytes()
    code, out, _ = run(capsys, "assign-profile", "p.json", "A", REF, "--db", "cat.sqlite")
    assert code == 2
    assert "ALUMINIUM" in out
    assert "bloqueo:" in out
    assert "Fabricante: Acme" in out
    assert (workspace / "p.json").read_bytes() == before  # no se escribe nada
    code, _, _ = run(
        capsys, "assign-profile", "p.json", "A", REF, "--db", "cat.sqlite", "--parcial"
    )
    assert code == 2
    assert (workspace / "p.json").read_bytes() == before


def test_cli_allows_a_profile_that_lists_the_bar_material(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(
        capsys, "assign-profile", "p.json", "A", "CAT/SINT-I/IPE-MIX", "--db", "cat.sqlite"
    )
    assert code == 0
    assert "barra «A»: APLICADA" in out
    code, out, _ = run(capsys, "sections", "p.json")
    assert code == 0
    assert "materiales compatibles: STEEL, ALUMINIUM" in out
