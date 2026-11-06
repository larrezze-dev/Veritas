"""Fabricante y materiales compatibles del catálogo (T-028 E1; ADR-012)."""

import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from catalog_support import make_data, new_store
from test_catalog_batches import raw, v1_file
from veritas.catalogs.columns import parse_mapping
from veritas.catalogs.errors import CatalogError
from veritas.catalogs.export import csv_text
from veritas.catalogs.model import (
    CatalogProfile,
    ProfileData,
    decode_profile,
    profile_document,
    profile_json,
    profile_sha256,
)
from veritas.catalogs.query import Criteria, Entry, search
from veritas.catalogs.store import (
    _MIGRATION_1_TO_2,
    _MIGRATION_2_TO_3,
    SCHEMA_VERSION,
    CatalogStore,
)
from veritas.cli import main

DB = "cat.sqlite"
HEADER = "series,designation,family,manufacturer,compatible_materials,area,iy,iz\n"
CATALOG_ID = UUID("00000000-0000-4000-8000-000000000002")
PROFILE_ID = UUID("00000000-0000-4000-8000-000000000001")


def profile_of(data: ProfileData) -> CatalogProfile:
    return CatalogProfile(PROFILE_ID, 1, CATALOG_ID, data)


# --- Modelo -------------------------------------------------------------------------------


def test_fields_are_optional_and_the_document_of_a_plain_profile_is_unchanged() -> None:
    plain = make_data("P1")
    document = profile_document(profile_of(plain))["data"]
    assert isinstance(document, dict)
    assert "manufacturer" not in document
    assert "compatible_materials" not in document
    assert plain.manufacturer is None
    assert plain.compatible_materials == ()


def test_declared_fields_enter_the_document_and_the_hash() -> None:
    plain = make_data("P1")
    rich = make_data(
        "P1", manufacturer="  Acme ", compatible_materials=("TIMBER", "STEEL", "STEEL")
    )
    assert rich.manufacturer == "Acme"
    assert rich.compatible_materials == ("STEEL", "TIMBER")  # orden canónico, sin duplicados
    assert profile_sha256(profile_of(plain)) != profile_sha256(profile_of(rich))
    assert decode_profile(profile_json(profile_of(rich))).data == rich


@pytest.mark.parametrize("manufacturer", ["", "   "])
def test_an_empty_manufacturer_is_rejected(manufacturer: str) -> None:
    with pytest.raises(CatalogError) as error:
        make_data("P1", manufacturer=manufacturer)
    assert error.value.diagnostic.message_key == "catalog.invalid_manufacturer"


def test_an_unknown_material_is_rejected() -> None:
    with pytest.raises(CatalogError) as error:
        make_data("P1", compatible_materials=("HORMIGON",))
    assert error.value.diagnostic.message_key == "catalog.invalid_material"


def test_a_non_canonical_document_is_rejected() -> None:
    text = profile_json(profile_of(make_data("P1", manufacturer="Acme")))
    document = json.loads(text)
    document["data"]["compatible_materials"] = []  # vacío: no es la forma canónica
    with pytest.raises(CatalogError):
        decode_profile(json.dumps(document))
    document["data"].pop("compatible_materials")
    document["data"]["manufacturer"] = None
    with pytest.raises(CatalogError):
        decode_profile(json.dumps(document))


# --- Almacén v4 ---------------------------------------------------------------------------


def test_the_store_is_v4_keeps_columns_in_step_and_verify_detects_a_drift(tmp_path: Path) -> None:
    assert SCHEMA_VERSION == 4
    store = new_store(tmp_path)
    info = store.create_catalog(name="C", source="S", license="L", edition="1")
    rich = make_data("P1", manufacturer="Acme", compatible_materials=("STEEL", "ALUMINIUM"))
    store.put_profile(info.id, rich)
    store.put_profile(info.id, make_data("P2"))
    assert store.verify().ok
    store.close()
    path = tmp_path / "catalogo.sqlite"
    con = sqlite3.connect(path)
    rows = con.execute(
        "SELECT designation, manufacturer, compatible_materials FROM profile_revisions"
    ).fetchall()
    assert sorted(rows) == [("P1", "Acme", "STEEL,ALUMINIUM"), ("P2", None, None)]
    con.execute("UPDATE profile_revisions SET manufacturer = 'Otro' WHERE designation = 'P1'")
    con.commit()
    con.close()
    with CatalogStore.open(path) as reopened:
        report = reopened.verify()
    assert not report.ok
    assert "manufacturer" in str(report.issues[0].params_dict())


def test_adding_the_fields_to_an_existing_profile_is_a_new_revision(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    info = store.create_catalog(name="C", source="S", license="L", edition="1")
    first = store.put_profile(info.id, make_data("P1"))
    second = store.put_profile(info.id, replace(make_data("P1"), manufacturer="Acme"))
    third = store.put_profile(info.id, replace(make_data("P1"), manufacturer="Acme"))
    assert (first.profile.revision, second.profile.revision, third.profile.revision) == (1, 2, 2)
    assert third.status.name == "UNCHANGED"


# --- Búsqueda -----------------------------------------------------------------------------


def entries_of(tmp_path: Path) -> list[Entry]:
    store = new_store(tmp_path)
    info = store.create_catalog(name="C", source="S", license="L", edition="1")
    both = ("STEEL", "ALUMINIUM")
    store.put_profile(info.id, make_data("A", manufacturer="Acme", compatible_materials=("STEEL",)))
    store.put_profile(info.id, make_data("B", manufacturer="ÁCME ", compatible_materials=both))
    store.put_profile(
        info.id, make_data("C", manufacturer="Beta", compatible_materials=("TIMBER",))
    )
    store.put_profile(info.id, make_data("D"))
    return [Entry(info, p) for p in store.list_profiles(info.id)]


def names(entries: list[Entry], criteria: Criteria) -> list[str]:
    return [e.profile.designation for e in search(entries, criteria).matches]


def test_manufacturer_is_exact_and_ignores_case_and_accents(tmp_path: Path) -> None:
    entries = entries_of(tmp_path)
    assert names(entries, Criteria(manufacturer="acme")) == ["A", "B"]


def test_manufacturer_is_not_a_substring_match(tmp_path: Path) -> None:
    entries = entries_of(tmp_path)
    assert names(entries, Criteria(manufacturer="Ac")) == []


def test_material_filter_hides_undeclared_unless_asked(tmp_path: Path) -> None:
    entries = entries_of(tmp_path)
    assert names(entries, Criteria(material="STEEL")) == ["A", "B"]
    assert names(entries, Criteria(material="TIMBER")) == ["C"]
    assert names(entries, Criteria(material="STEEL", include_undeclared=True)) == ["A", "B", "D"]
    assert names(entries, Criteria(manufacturer="Acme", material="ALUMINIUM")) == ["B"]


# --- Importador, CLI, exportación y clon --------------------------------------------------


def write_csv(tmp_path: Path, *rows: str) -> None:
    (tmp_path / "in.csv").write_text(HEADER + "\n".join(rows) + "\n", encoding="utf-8")


def create(name: str = "C") -> None:
    args = ["catalog", "create", "--db", DB, "--nombre", name, "--fuente", "S"]
    assert main([*args, "--licencia", "L", "--edicion", "1"]) == 0


def test_native_csv_imports_manufacturer_and_materials_and_searches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    create()
    write_csv(tmp_path, "S,P1,I,Acme,STEEL;ALUMINIUM,1e-3,1e-6,1e-7", "S,P2,I,,,1e-3,1e-6,1e-7")
    assert main(["catalog", "import", "--db", DB, "--catalogo", "C", "in.csv"]) == 0
    capsys.readouterr()
    assert main(["catalog", "search", "--db", DB, "--fabricante", "acme"]) == 0
    out = capsys.readouterr().out
    assert "P1" in out
    assert "P2" not in out
    assert main(["catalog", "search", "--db", DB, "--material", "ALUMINIUM"]) == 0
    assert "P1" in capsys.readouterr().out
    assert main(["catalog", "ficha", "--db", DB, "C/S/P1"]) == 0
    ficha = capsys.readouterr().out
    assert "fabricante: Acme" in ficha
    assert "materiales compatibles: STEEL, ALUMINIUM" in ficha
    assert main(["catalog", "ficha", "--db", DB, "C/S/P2"]) == 0
    assert "fabricante" not in capsys.readouterr().out


def test_an_unknown_material_rejects_the_row_and_reports_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    create()
    write_csv(
        tmp_path,
        "S,P1,I,Acme,STEEL;hormigon,1e-3,1e-6,1e-7",
        "S,P2,I,Acme,STEEL,1e-3,1e-6,1e-7",
    )
    capsys.readouterr()
    main(["catalog", "import", "--db", DB, "--catalogo", "C", "in.csv"])
    captured = capsys.readouterr()
    assert "hormigon" in captured.out + captured.err
    with CatalogStore.open(tmp_path / DB) as store:
        info = store.catalog_by_name("C")
        assert [p.designation for p in store.list_profiles(info.id)] == ["P2"]


def test_material_aliases_in_the_mapping_and_hash_stability() -> None:
    base = {
        "format": "veritas-catalog-mapping/1",
        "columns": {
            "Serie": {"field": "series"},
            "Des": {"field": "designation"},
            "Fam": {"field": "family"},
            "A": {"field": "area", "unit": "m2"},
            "Iy": {"field": "iy", "unit": "m4"},
            "Iz": {"field": "iz", "unit": "m4"},
            "Mat": {"field": "compatible_materials"},
        },
    }
    plain = parse_mapping(json.dumps(base))
    assert "material_aliases" not in plain.document  # hash de mapeos existentes intacto
    aliased = parse_mapping(json.dumps({**base, "material_aliases": {" Acero ": "STEEL"}}))
    assert aliased.material_aliases == (("acero", "STEEL"),)
    assert aliased.sha256 != plain.sha256
    with pytest.raises(CatalogError):
        parse_mapping(json.dumps({**base, "material_aliases": {"x": "HORMIGON"}}))


def test_export_csv_roundtrips_the_commercial_columns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    create()
    write_csv(tmp_path, "S,P1,I,Acme,STEEL;TIMBER,1e-3,1e-6,1e-7", "S,P2,I,,,1e-3,1e-6,1e-7")
    assert main(["catalog", "import", "--db", DB, "--catalogo", "C", "in.csv"]) == 0
    capsys.readouterr()
    with CatalogStore.open(tmp_path / DB) as store:
        text = csv_text(store.list_profiles(store.catalog_by_name("C").id))
    assert text.startswith("series,designation,family,manufacturer,compatible_materials,area")
    assert "Acme,STEEL;TIMBER" in text
    (tmp_path / "again.csv").write_text(text, encoding="utf-8")
    create("D")
    assert main(["catalog", "import", "--db", DB, "--catalogo", "D", "again.csv"]) == 0
    with CatalogStore.open(tmp_path / DB) as store:
        a = {p.designation: p.data for p in store.list_profiles(store.catalog_by_name("C").id)}
        b = {p.designation: p.data for p in store.list_profiles(store.catalog_by_name("D").id)}
    assert a["P1"].manufacturer == b["P1"].manufacturer == "Acme"
    assert a["P1"].compatible_materials == b["P1"].compatible_materials == ("STEEL", "TIMBER")
    assert b["P2"].manufacturer is None


def test_export_csv_without_commercial_data_keeps_the_old_header(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    info = store.create_catalog(name="C", source="S", license="L", edition="1")
    store.put_profile(info.id, make_data("P1"))
    text = csv_text(store.list_profiles(info.id))
    assert text.startswith("series,designation,family,area,iy,iz")


def test_clone_keeps_the_fields(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    info = store.create_catalog(name="C", source="S", license="L", edition="1")
    store.put_profile(
        info.id, make_data("P1", manufacturer="Acme", compatible_materials=("STEEL",))
    )
    copy = store.clone_catalog(info.id, "Copia")
    (profile,) = store.list_profiles(copy.catalog.id)
    assert (profile.data.manufacturer, profile.data.compatible_materials) == ("Acme", ("STEEL",))
    assert store.verify().ok


def test_a_v3_file_migrates_to_v4_keeping_content_and_verifying(tmp_path: Path) -> None:
    """v1 → v2 → v3 con las migraciones reales y v3 → v4 al abrir con el almacén actual."""
    path = tmp_path / "v3.sqlite"
    catalog_id = v1_file(path)
    connection = raw(path)
    for statement in (*_MIGRATION_1_TO_2, *_MIGRATION_2_TO_3):
        connection.execute(statement)
    connection.execute("PRAGMA user_version = 3")
    connection.close()
    before = (
        raw(path).execute("SELECT content_json, content_sha256 FROM profile_revisions").fetchall()
    )
    with CatalogStore.open(path) as store:
        assert store.version == 4
        (profile,) = store.list_profiles(catalog_id)
        assert profile.data.manufacturer is None
        assert profile.data.compatible_materials == ()
        assert store.verify().ok
    after = (
        raw(path).execute("SELECT content_json, content_sha256 FROM profile_revisions").fetchall()
    )
    assert after == before  # el contenido y su hash no cambian
    columns = [row[1] for row in raw(path).execute("PRAGMA table_info(profile_revisions)")]
    assert {"manufacturer", "compatible_materials"} <= set(columns)
