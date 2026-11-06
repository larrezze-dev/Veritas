"""Esquema v3 y clones del catálogo (T-023 E2; enmienda de ADR-009): migración, clon y verify."""

import sqlite3
from pathlib import Path
from uuid import UUID

import pytest

from catalog_support import (
    assert_renders,
    id_factory,
    make_data,
    message_key,
    new_store,
)
from test_catalog_batches import raw, v1_file
from veritas.catalogs.errors import CatalogError
from veritas.catalogs.store import (
    _MIGRATION_1_TO_2,
    SCHEMA_VERSION,
    CatalogStore,
)
from veritas.core.diagnostics import DiagnosticError

PROFILE_COLUMNS = ("cloned_from_profile_id", "cloned_from_revision")


def make_origin(store: CatalogStore, *, lock: bool = True) -> UUID:
    info = store.create_catalog(
        name="Origen", source="Fuente X", license="Licencia Y", edition="2026", description="Desc"
    )
    store.put_profiles(info.id, [make_data("A"), make_data("B"), make_data("C", series="S2")])
    store.put_profile(info.id, make_data("B", area=2.0e-3))  # B queda en la revisión 2
    if lock:
        store.lock_catalog(info.id)
    return info.id


def table_columns(path: Path, table: str) -> list[str]:
    connection = raw(path)
    try:
        return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
    finally:
        connection.close()


def issues_of(store: CatalogStore) -> list[tuple[str, str]]:
    report = store.verify()
    return [(i.message_key, str(i.params_dict().get("field", ""))) for i in report.issues]


def update(path: Path, sql: str, *params: object) -> None:
    connection = raw(path)
    try:
        connection.execute("BEGIN")
        connection.execute(sql, params)
        connection.execute("COMMIT")
    finally:
        connection.close()


# --- Esquema v3 y migración ----------------------------------------------------------------


def test_the_schema_is_v4_and_a_new_file_has_the_clone_columns(tmp_path: Path) -> None:
    assert SCHEMA_VERSION == 4
    path = tmp_path / "nuevo.sqlite"
    CatalogStore.create(path).close()
    assert "cloned_from" in table_columns(path, "catalogs")
    assert set(PROFILE_COLUMNS) <= set(table_columns(path, "profile_revisions"))


def test_a_v2_file_migrates_to_v4_keeping_every_value_and_verifying(tmp_path: Path) -> None:
    """v1 → v2 con la migración real de T-022 y después v2 → v3 al abrirlo con el almacén actual."""
    path = tmp_path / "v2.sqlite"
    catalog_id = v1_file(path)
    connection = raw(path)
    for statement in _MIGRATION_1_TO_2:
        connection.execute(statement)
    connection.execute("PRAGMA user_version = 2")
    connection.close()
    before = (
        raw(path).execute("SELECT content_json, content_sha256 FROM profile_revisions").fetchall()
    )
    assert "cloned_from" not in table_columns(path, "catalogs")

    with CatalogStore.open(path) as store:
        assert store.version == 4
        (profile,) = store.list_profiles(catalog_id)
        assert profile.designation == "OLD"
        assert store.clone_source(catalog_id) is None
        assert store.revision_origin(profile.id, 1) is None
        assert store.verify().ok
    assert "cloned_from" in table_columns(path, "catalogs")
    after = (
        raw(path).execute("SELECT content_json, content_sha256 FROM profile_revisions").fetchall()
    )
    assert after == before


def test_a_migrated_file_has_the_same_schema_as_a_new_one(tmp_path: Path) -> None:
    old, new = tmp_path / "v1.sqlite", tmp_path / "nuevo.sqlite"
    v1_file(old)
    CatalogStore.open(old).close()
    CatalogStore.create(new).close()
    for table in ("catalogs", "profile_revisions", "import_batches"):
        assert table_columns(old, table) == table_columns(new, table)


def test_opening_a_v3_file_does_not_change_its_bytes(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    make_origin(store)
    store.close()
    path = tmp_path / "catalogo.sqlite"
    before = path.read_bytes()
    with CatalogStore.open(path) as reopened:
        reopened.list_catalogs()
        assert reopened.verify().ok
    assert path.read_bytes() == before


# --- Clon ----------------------------------------------------------------------------------


def test_a_protected_catalog_is_cloned_into_an_editable_one_with_equal_content(
    tmp_path: Path,
) -> None:
    store = new_store(tmp_path)
    origin_id = make_origin(store)
    origin_before = store.list_profiles(origin_id, all_revisions=True)
    result = store.clone_catalog(origin_id, "Copia")

    assert result.catalog.name == "Copia"
    assert not result.catalog.protected
    assert (result.catalog.source, result.catalog.license, result.catalog.edition) == (
        "Fuente X",
        "Licencia Y",
        "2026",
    )
    assert result.catalog.description == "Desc"
    cloned = store.list_profiles(result.catalog.id, all_revisions=True)
    latest = store.list_profiles(origin_id)
    assert len(cloned) == len(latest) == 3  # una revisión 1 por perfil
    for copy, source in zip(cloned, latest, strict=True):
        assert (copy.revision, copy.catalog_id) == (1, result.catalog.id)
        assert copy.id != source.id
        assert copy.data == source.data  # contenido y procedencia por perfil idénticos
    assert {p.designation for p in cloned} == {"A", "B", "C"}
    assert next(p for p in cloned if p.designation == "B").properties.area.value == 2.0e-3
    assert store.list_profiles(origin_id, all_revisions=True) == origin_before
    assert store.get_catalog(origin_id).protected
    assert store.verify().ok


def test_the_clone_records_its_catalog_origin_and_the_exact_source_revision(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    origin_id = make_origin(store)
    result = store.clone_catalog(origin_id, "Copia")
    source = store.clone_source(result.catalog.id)
    assert source is not None
    assert (source.id, source.name) == (origin_id, "Origen")
    assert store.clone_source(origin_id) is None
    for copy in result.profiles:
        origin = store.revision_origin(copy.id, copy.revision)
        assert origin is not None
        assert (origin.catalog_id, origin.designation) == (origin_id, copy.designation)
        assert origin.revision == (2 if copy.designation == "B" else 1)  # la última del origen


def test_a_clone_is_editable_and_reimporting_creates_revisions_without_a_link(
    tmp_path: Path,
) -> None:
    store = new_store(tmp_path)
    origin_id = make_origin(store)
    clone = store.clone_catalog(origin_id, "Copia").catalog
    store.put_profile(clone.id, make_data("A", area=9.0e-3))  # A → revisión 2 en el clon
    store.put_profile(clone.id, make_data("NUEVO"))  # perfil propio del clon
    assert store.latest(clone.id, "SERIE-A", "A").revision == 2
    first, second = store.history(clone.id, "SERIE-A", "A")
    assert store.revision_origin(first.id, 1) is not None
    assert store.revision_origin(second.id, 2) is None
    new = store.latest(clone.id, "SERIE-A", "NUEVO")
    assert store.revision_origin(new.id, 1) is None
    assert store.verify().ok  # lo posterior al clon es legítimo
    # el origen no se movió
    assert store.latest(origin_id, "SERIE-A", "A").revision == 1


def test_a_clone_of_a_clone_links_to_its_immediate_origin(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    origin_id = make_origin(store)
    first = store.clone_catalog(origin_id, "Copia 1").catalog
    second = store.clone_catalog(first.id, "Copia 2")
    source = store.clone_source(second.catalog.id)
    assert source is not None
    assert source.id == first.id
    origin = store.revision_origin(second.profiles[0].id, 1)
    assert origin is not None
    assert origin.catalog_id == first.id
    assert store.verify().ok


def test_cloning_an_empty_catalog_is_valid(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    empty = store.create_catalog(name="Vacio", source="s", license="l", edition="e")
    result = store.clone_catalog(empty.id, "Vacio 2", description="Otra")
    assert result.profiles == ()
    assert result.catalog.description == "Otra"
    source = store.clone_source(result.catalog.id)
    assert source is not None
    assert source.id == empty.id
    assert store.verify().ok


def test_clone_name_taken_or_unknown_origin_fail_and_leave_the_bytes_identical(
    tmp_path: Path,
) -> None:
    store = new_store(tmp_path)
    origin_id = make_origin(store)
    store.close()
    path = tmp_path / "catalogo.sqlite"
    before = path.read_bytes()
    with CatalogStore.open(path) as reopened:
        with pytest.raises(CatalogError) as taken:
            reopened.clone_catalog(origin_id, "origen")  # sin distinguir mayúsculas
        assert message_key(taken) == "catalog.name_taken"
        with pytest.raises(CatalogError) as missing:
            reopened.clone_catalog(UUID(int=5), "Otro")
        assert message_key(missing) == "catalog.not_found"
        for error in (taken, missing):
            assert_renders(error.value.diagnostic)
        with pytest.raises(DiagnosticError):
            reopened.clone_catalog(origin_id, "   ")  # nombre vacío
    assert path.read_bytes() == before


def test_a_failure_in_the_middle_of_the_copy_leaves_no_trace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = new_store(tmp_path)
    origin_id = make_origin(store)
    store.close()
    path = tmp_path / "catalogo.sqlite"
    before = path.read_bytes()
    original = CatalogStore._insert_revision
    calls = {"n": 0}

    def flaky(self: CatalogStore, con: sqlite3.Connection, profile: object) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("fallo inyectado")
        original(self, con, profile)  # type: ignore[arg-type]

    monkeypatch.setattr(CatalogStore, "_insert_revision", flaky)
    with (
        CatalogStore.open(path, id_factory=id_factory(900)) as reopened,
        pytest.raises(RuntimeError, match="inyectado"),
    ):
        reopened.clone_catalog(origin_id, "Copia")
    monkeypatch.undo()
    assert calls["n"] == 2
    assert path.read_bytes() == before
    with CatalogStore.open(path) as reopened:
        assert [c.name for c in reopened.list_catalogs()] == ["Origen"]


# --- verify --------------------------------------------------------------------------------


@pytest.fixture
def cloned(tmp_path: Path) -> tuple[Path, UUID, UUID]:
    store = new_store(tmp_path)
    origin_id = make_origin(store)
    clone_id = store.clone_catalog(origin_id, "Copia").catalog.id
    store.close()
    return tmp_path / "catalogo.sqlite", origin_id, clone_id


def check(path: Path) -> list[tuple[str, str]]:
    with CatalogStore.open(path) as store:
        return issues_of(store)


def test_a_clean_clone_verifies(cloned: tuple[Path, UUID, UUID]) -> None:
    path, _, _ = cloned
    assert check(path) == []


def test_verify_detects_a_link_to_a_missing_origin_revision(
    cloned: tuple[Path, UUID, UUID],
) -> None:
    path, _, clone_id = cloned
    update(
        path,
        "UPDATE profile_revisions SET cloned_from_revision = 99 WHERE catalog_id = ?"
        " AND designation = 'A'",
        str(clone_id),
    )
    assert check(path) == [("catalog.verify_clone", "revisión de origen inexistente")]


def test_verify_detects_a_half_written_link(cloned: tuple[Path, UUID, UUID]) -> None:
    path, _, clone_id = cloned
    update(
        path,
        "UPDATE profile_revisions SET cloned_from_revision = NULL WHERE catalog_id = ?"
        " AND designation = 'A'",
        str(clone_id),
    )
    assert check(path) == [("catalog.verify_clone", "enlace incompleto")]


def test_verify_detects_a_link_to_a_revision_of_another_catalog(
    cloned: tuple[Path, UUID, UUID], tmp_path: Path
) -> None:
    path, _, clone_id = cloned
    with CatalogStore.open(path) as store:
        other = store.create_catalog(name="Ajeno", source="s", license="l", edition="e")
        stranger = store.put_profile(other.id, make_data("A")).profile
    update(
        path,
        "UPDATE profile_revisions SET cloned_from_profile_id = ?, cloned_from_revision = 1"
        " WHERE catalog_id = ? AND designation = 'A'",
        str(stranger.id),
        str(clone_id),
    )
    assert check(path) == [("catalog.verify_clone", "catálogo de origen distinto")]


def test_verify_detects_a_link_to_the_wrong_profile_of_the_right_catalog(
    cloned: tuple[Path, UUID, UUID],
) -> None:
    """El enlace existe y apunta al catálogo de origen, pero su contenido no es el copiado."""
    path, origin_id, clone_id = cloned
    with CatalogStore.open(path) as store:
        wrong = store.latest(origin_id, "S2", "C")
    update(
        path,
        "UPDATE profile_revisions SET cloned_from_profile_id = ?, cloned_from_revision = ?"
        " WHERE catalog_id = ? AND designation = 'A'",
        str(wrong.id),
        wrong.revision,
        str(clone_id),
    )
    assert check(path) == [("catalog.verify_clone", "contenido distinto al del origen")]


def test_verify_detects_a_link_on_a_revision_after_the_first(
    cloned: tuple[Path, UUID, UUID],
) -> None:
    path, origin_id, clone_id = cloned
    with CatalogStore.open(path) as store:
        store.put_profile(clone_id, make_data("A", area=7.0e-3))
        revised = store.latest(clone_id, "SERIE-A", "A")
        source = store.latest(origin_id, "SERIE-A", "A")
    assert revised.revision == 2
    update(
        path,
        "UPDATE profile_revisions SET cloned_from_profile_id = ?, cloned_from_revision = 1"
        " WHERE profile_id = ? AND revision = 2",
        str(source.id),
        str(revised.id),
    )
    assert check(path) == [("catalog.verify_clone", "revisión posterior a la primera")]


def test_verify_detects_a_link_in_a_catalog_that_is_not_a_clone(
    cloned: tuple[Path, UUID, UUID],
) -> None:
    path, _, clone_id = cloned
    update(path, "UPDATE catalogs SET cloned_from = NULL WHERE id = ?", str(clone_id))
    found = check(path)
    assert found
    assert {item for item in found} == {("catalog.verify_clone", "catálogo de origen distinto")}


def test_verify_detects_a_catalog_cloned_from_itself(cloned: tuple[Path, UUID, UUID]) -> None:
    path, origin_id, _ = cloned
    update(path, "UPDATE catalogs SET cloned_from = id WHERE id = ?", str(origin_id))
    assert ("catalog.verify_clone", "origen") in check(path)


def test_verify_detects_a_clone_origin_that_does_not_exist(
    cloned: tuple[Path, UUID, UUID],
) -> None:
    path, _, clone_id = cloned
    connection = raw(path)
    connection.execute("PRAGMA foreign_keys = OFF")
    connection.execute(
        "UPDATE catalogs SET cloned_from = ? WHERE id = ?", (str(UUID(int=77)), str(clone_id))
    )
    connection.close()
    keys = [key for key, _ in check(path)]
    assert "catalog.verify_orphan" in keys  # la clave foránea de cloned_from


def test_every_clone_diagnostic_renders_completely(cloned: tuple[Path, UUID, UUID]) -> None:
    path, _, clone_id = cloned
    update(
        path,
        "UPDATE profile_revisions SET cloned_from_revision = 99 WHERE catalog_id = ?"
        " AND designation = 'A'",
        str(clone_id),
    )
    with CatalogStore.open(path) as store:
        (issue,) = store.verify().issues
    assert_renders(issue)
