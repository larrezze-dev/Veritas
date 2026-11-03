"""Almacén SQLite del catálogo (T-022 E1): ciclo de vida, versiones, revisiones y protegido."""

import sqlite3
from pathlib import Path
from uuid import UUID

import pytest

from catalog_support import PROVENANCE, id_factory, make_data, message_key, new_store, uid
from veritas.catalogs.errors import CatalogError, CatalogFileError
from veritas.catalogs.model import ProfileFamily
from veritas.catalogs.store import (
    APPLICATION_ID,
    SCHEMA_VERSION,
    CatalogStore,
    PutStatus,
)
from veritas.core.diagnostics import DiagnosticCode, DiagnosticError


def raw(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path, isolation_level=None)


def pragma(path: Path, name: str) -> int:
    connection = raw(path)
    try:
        return int(connection.execute(f"PRAGMA {name}").fetchone()[0])
    finally:
        connection.close()


def make_catalog(store: CatalogStore, name: str = "Mi catálogo") -> UUID:
    return store.create_catalog(
        name=name, source=PROVENANCE.source, license=PROVENANCE.license, edition="2026"
    ).id


# --- Crear y abrir -----------------------------------------------------------------------------


def test_create_builds_a_versioned_catalog_file(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    with CatalogStore.create(path) as store:
        assert store.version == SCHEMA_VERSION
        assert store.path == path
        assert store.list_catalogs() == ()
    assert pragma(path, "application_id") == APPLICATION_ID
    assert pragma(path, "user_version") == SCHEMA_VERSION
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c.sqlite"]


def test_default_journal_mode_is_not_wal(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    CatalogStore.create(path).close()
    connection = raw(path)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    finally:
        connection.close()


def test_create_refuses_an_existing_file_without_touching_it(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    path.write_bytes(b"contenido previo")
    with pytest.raises(CatalogFileError) as error:
        CatalogStore.create(path)
    assert error.value.diagnostic.message_key == "catalog.file_exists"
    assert path.read_bytes() == b"contenido previo"
    assert [p.name for p in tmp_path.iterdir()] == ["c.sqlite"]


def test_failed_create_leaves_no_file(tmp_path: Path) -> None:
    path = tmp_path / "no_existe" / "c.sqlite"
    with pytest.raises(CatalogFileError):
        CatalogStore.create(path)
    assert not path.exists()
    assert not (tmp_path / "no_existe").exists()


def test_open_never_creates_a_file(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    with pytest.raises(CatalogFileError) as error:
        CatalogStore.open(path)
    assert error.value.code is DiagnosticCode.REFERENCE_NOT_FOUND
    assert error.value.diagnostic.message_key == "catalog.file_missing"
    assert list(tmp_path.iterdir()) == []


def test_open_rejects_a_foreign_sqlite_file_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "ajeno.sqlite"
    connection = raw(path)
    connection.execute("CREATE TABLE datos (x INTEGER)")
    connection.close()
    before = path.read_bytes()
    with pytest.raises(CatalogFileError) as error:
        CatalogStore.open(path)
    assert error.value.diagnostic.message_key == "catalog.not_a_catalog"
    assert path.read_bytes() == before
    assert pragma(path, "user_version") == 0


def test_open_rejects_a_blank_sqlite_file(tmp_path: Path) -> None:
    path = tmp_path / "vacio.sqlite"
    raw(path).close()
    with pytest.raises(CatalogFileError) as error:
        CatalogStore.open(path)
    assert error.value.diagnostic.message_key == "catalog.not_a_catalog"


def test_open_rejects_a_file_that_is_not_sqlite(tmp_path: Path) -> None:
    path = tmp_path / "texto.sqlite"
    path.write_bytes(b"esto no es una base de datos SQLite " * 10)
    before = path.read_bytes()
    with pytest.raises(CatalogFileError) as error:
        CatalogStore.open(path)
    assert error.value.diagnostic.message_key == "catalog.unreadable"
    assert error.value.diagnostic.cause is not None
    assert path.read_bytes() == before


def test_open_rejects_a_newer_version_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    CatalogStore.create(path).close()
    connection = raw(path)
    connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")
    connection.close()
    before = path.read_bytes()
    with pytest.raises(CatalogFileError) as error:
        CatalogStore.open(path)
    assert error.value.diagnostic.message_key == "catalog.unsupported_version"
    assert error.value.diagnostic.params_dict() == {
        "found": SCHEMA_VERSION + 1,
        "supported": SCHEMA_VERSION,
    }
    assert path.read_bytes() == before


def test_version_one_opens_and_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    with CatalogStore.create(path) as store:
        catalog = make_catalog(store)
    before = path.read_bytes()
    with CatalogStore.open(path) as store:
        assert store.version == 1
        assert store.get_catalog(catalog).name == "Mi catálogo"
    assert path.read_bytes() == before  # abrir sin migrar no escribe


def test_closed_store_rejects_use(tmp_path: Path) -> None:
    store = new_store(tmp_path)
    store.close()
    store.close()  # idempotente
    with pytest.raises(CatalogError) as error:
        store.list_catalogs()
    assert message_key(error) == "catalog.store_closed"


# --- Migraciones por user_version ---------------------------------------------------------------


def versioned_file(tmp_path: Path, version: int, name: str = "m.sqlite") -> Path:
    path = tmp_path / name
    connection = raw(path)
    connection.execute("CREATE TABLE datos (x INTEGER)")
    connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    connection.execute(f"PRAGMA user_version = {version}")
    connection.close()
    return path


def test_version_zero_with_the_mark_has_no_real_migration(tmp_path: Path) -> None:
    path = versioned_file(tmp_path, 0)
    before = path.read_bytes()
    with pytest.raises(CatalogFileError) as error:
        CatalogStore.open(path)
    assert error.value.diagnostic.message_key == "catalog.migration_gap"
    assert path.read_bytes() == before


def test_synthetic_migration_zero_to_one(tmp_path: Path) -> None:
    path = versioned_file(tmp_path, 0)

    def zero_to_one(connection: sqlite3.Connection) -> None:
        connection.execute("INSERT INTO datos (x) VALUES (42)")

    with CatalogStore.open(path, migrations={0: zero_to_one}) as store:
        assert store.version == 1
    assert pragma(path, "user_version") == 1
    connection = raw(path)
    assert connection.execute("SELECT x FROM datos").fetchall() == [(42,)]
    connection.close()


def test_synthetic_chain_to_a_higher_target(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    CatalogStore.create(path).close()

    def one_to_two(connection: sqlite3.Connection) -> None:
        connection.execute("ALTER TABLE catalogs ADD COLUMN nota TEXT")

    with CatalogStore.open(path, migrations={1: one_to_two}, target_version=2) as store:
        assert store.version == 2
    assert pragma(path, "user_version") == 2
    # Ya en la versión 2: no se vuelve a migrar.
    with CatalogStore.open(path, migrations={1: one_to_two}, target_version=2) as store:
        assert store.version == 2


def test_incomplete_chain_is_rejected_before_writing(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    CatalogStore.create(path).close()
    before = path.read_bytes()
    with pytest.raises(CatalogFileError) as error:
        CatalogStore.open(path, migrations={}, target_version=3)
    assert error.value.diagnostic.message_key == "catalog.migration_gap"
    assert path.read_bytes() == before


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
def test_failing_migration_leaves_the_file_identical(
    tmp_path: Path, failure: type[BaseException]
) -> None:
    path = tmp_path / "c.sqlite"
    CatalogStore.create(path).close()
    before = path.read_bytes()

    def broken(connection: sqlite3.Connection) -> None:
        connection.execute("ALTER TABLE catalogs ADD COLUMN nota TEXT")
        raise failure("fallo a mitad de la migración")

    with pytest.raises(failure):
        CatalogStore.open(path, migrations={1: broken}, target_version=2)
    assert path.read_bytes() == before
    assert pragma(path, "user_version") == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c.sqlite"]


# --- Catálogos ----------------------------------------------------------------------------


def test_catalog_crud_and_unique_names(tmp_path: Path) -> None:
    with new_store(tmp_path) as store:
        first = store.create_catalog(
            name="Acero", source="S", license="L", edition="E", description="d"
        )
        assert not first.protected
        assert store.catalog_by_name("acero") == first  # sin distinguir mayúsculas
        assert store.get_catalog(first.id) == first
        with pytest.raises(CatalogError) as error:
            store.create_catalog(name="ACERO", source="S", license="L", edition="E")
        assert message_key(error) == "catalog.name_taken"
        store.create_catalog(name="Aluminio", source="S", license="L", edition="E")
        assert [c.name for c in store.list_catalogs()] == ["Acero", "Aluminio"]
        with pytest.raises(CatalogError) as missing:
            store.get_catalog(uid(999))
        assert missing.value.code is DiagnosticCode.REFERENCE_NOT_FOUND
        with pytest.raises(CatalogError):
            store.catalog_by_name("no existe")


def test_catalog_requires_provenance(tmp_path: Path) -> None:
    with new_store(tmp_path) as store:
        for field in ("source", "license", "edition", "name"):
            args = {"name": "N", "source": "S", "license": "L", "edition": "E", field: " "}
            with pytest.raises(DiagnosticError):
                store.create_catalog(**args)
        assert store.list_catalogs() == ()


def test_lock_is_idempotent_and_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    with CatalogStore.create(path) as store:
        catalog = make_catalog(store)
        assert store.lock_catalog(catalog).protected
        assert store.lock_catalog(catalog).protected
    with CatalogStore.open(path) as store:
        assert store.get_catalog(catalog).protected
        with pytest.raises(CatalogError):
            store.lock_catalog(uid(777))


# --- Perfiles: creación, idempotencia y revisiones ---------------------------------------------


def test_put_creates_revision_one_and_reimport_is_idempotent(tmp_path: Path) -> None:
    with new_store(tmp_path) as store:
        catalog = make_catalog(store)
        first = store.put_profile(catalog, make_data("P-100"))
        assert first.status is PutStatus.CREATED
        assert first.profile.revision == 1
        again = store.put_profile(catalog, make_data("P-100"))
        assert again.status is PutStatus.UNCHANGED
        assert again.profile == first.profile
        assert len(store.list_profiles(catalog, all_revisions=True)) == 1


def test_changed_content_creates_a_new_revision_and_keeps_the_old_one(tmp_path: Path) -> None:
    with new_store(tmp_path) as store:
        catalog = make_catalog(store)
        first = store.put_profile(catalog, make_data("P-100")).profile
        second = store.put_profile(catalog, make_data("P-100", area=1.5e-3))
        assert second.status is PutStatus.REVISED
        assert second.profile.id == first.id
        assert second.profile.revision == 2
        assert store.get_profile(first.id, 1) == first  # la anterior no cambia
        assert store.get_profile(first.id, 2) == second.profile
        assert store.history(catalog, "SERIE-A", "P-100") == (first, second.profile)
        assert store.latest(catalog, "SERIE-A", "P-100") == second.profile
        third = store.put_profile(catalog, make_data("P-100", area=1.5e-3))
        assert third.status is PutStatus.UNCHANGED  # idéntico a la última, no a la primera
        back = store.put_profile(catalog, make_data("P-100"))
        assert back.status is PutStatus.REVISED  # volver al valor inicial es una revisión nueva
        assert back.profile.revision == 3


def test_list_profiles_returns_latest_revisions_in_order(tmp_path: Path) -> None:
    with new_store(tmp_path) as store:
        catalog = make_catalog(store)
        store.put_profiles(catalog, [make_data("B"), make_data("A"), make_data("A", series="S0")])
        store.put_profile(catalog, make_data("B", area=2e-3))
        latest = store.list_profiles(catalog)
        assert [(p.series, p.designation, p.revision) for p in latest] == [
            ("S0", "A", 1),
            ("SERIE-A", "A", 1),
            ("SERIE-A", "B", 2),
        ]
        every = store.list_profiles(catalog, all_revisions=True)
        assert [(p.designation, p.revision) for p in every if p.series == "SERIE-A"] == [
            ("A", 1),
            ("B", 1),
            ("B", 2),
        ]


def test_same_key_in_different_catalogs_is_independent(tmp_path: Path) -> None:
    with new_store(tmp_path) as store:
        one, two = make_catalog(store, "Uno"), make_catalog(store, "Dos")
        a = store.put_profile(one, make_data("P")).profile
        b = store.put_profile(two, make_data("P")).profile
        assert a.id != b.id
        assert (a.revision, b.revision) == (1, 1)


def test_keys_are_trimmed_and_case_sensitive(tmp_path: Path) -> None:
    with new_store(tmp_path) as store:
        catalog = make_catalog(store)
        store.put_profile(catalog, make_data(" P "))
        assert store.put_profile(catalog, make_data("P")).status is PutStatus.UNCHANGED
        assert store.put_profile(catalog, make_data("p")).status is PutStatus.CREATED
        assert store.latest(catalog, " SERIE-A ", " P ").designation == "P"


def test_duplicate_key_in_a_batch_is_rejected_whole(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    with CatalogStore.create(path) as store:
        catalog = make_catalog(store)
        before = len(store.list_profiles(catalog))
        with pytest.raises(CatalogError) as error:
            store.put_profiles(catalog, [make_data("A"), make_data("B"), make_data(" A ")])
        assert error.value.code is DiagnosticCode.REVISION_CONFLICT
        assert message_key(error) == "catalog.duplicate_key"
        assert len(store.list_profiles(catalog)) == before == 0


def test_put_rejects_non_profile_data_and_unknown_catalog(tmp_path: Path) -> None:
    with new_store(tmp_path) as store:
        catalog = make_catalog(store)
        with pytest.raises(TypeError):
            store.put_profiles(catalog, [make_data("A"), "no es un perfil"])  # type: ignore[list-item]
        assert store.list_profiles(catalog) == ()
        with pytest.raises(CatalogError) as error:
            store.put_profile(uid(555), make_data("A"))
        assert error.value.code is DiagnosticCode.REFERENCE_NOT_FOUND


def test_lookup_of_unknown_profiles_is_a_reference_error(tmp_path: Path) -> None:
    with new_store(tmp_path) as store:
        catalog = make_catalog(store)
        created = store.put_profile(catalog, make_data("A")).profile
        for action in (
            lambda: store.get_profile(created.id, 2),
            lambda: store.get_profile(uid(888), 1),
            lambda: store.history(catalog, "SERIE-A", "Z"),
            lambda: store.latest(catalog, "OTRA", "A"),
            lambda: store.list_profiles(uid(999)),
        ):
            with pytest.raises(CatalogError) as error:
                action()
            assert error.value.code is DiagnosticCode.REFERENCE_NOT_FOUND


# --- Protegido ------------------------------------------------------------------------------


def test_protected_catalog_rejects_import_and_keeps_the_file_identical(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    with CatalogStore.create(path) as store:
        catalog = make_catalog(store)
        store.put_profile(catalog, make_data("A"))
        store.lock_catalog(catalog)
    before = path.read_bytes()
    with CatalogStore.open(path) as store:
        with pytest.raises(CatalogError) as error:
            store.put_profiles(catalog, [make_data("B"), make_data("A", area=9e-3)])
        assert error.value.code is DiagnosticCode.UNSUPPORTED_CONFIGURATION
        assert message_key(error) == "catalog.protected"
    assert path.read_bytes() == before


# --- Reabrir: idéntico campo a campo -----------------------------------------------------------


def test_reopen_returns_identical_profiles_field_by_field(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    items = [
        make_data(
            f"P-{n:03d}", area=(n + 1) * 1.1e-4 / 3.0, torsion=None if n % 2 else (n + 1) * 1e-9
        )
        for n in range(25)
    ]
    items.append(make_data("OTRO", family=ProfileFamily.CHS, warnings=("catalog.aviso_masa",)))
    with CatalogStore.create(path, id_factory=id_factory()) as store:
        catalog = make_catalog(store)
        written = [r.profile for r in store.put_profiles(catalog, items)]
        before = store.list_profiles(catalog)
    with CatalogStore.open(path) as store:
        after = store.list_profiles(catalog)
        assert store.get_catalog(catalog).name == "Mi catálogo"
    assert len(after) == len(written) == 26
    by_key = {(p.series, p.designation): p for p in written}
    for stored in after:
        original = by_key[(stored.series, stored.designation)]
        assert repr(stored) == repr(original)  # campo a campo, con repr exacto de los float
    assert [repr(p) for p in after] == [repr(p) for p in before]


def test_reopened_store_keeps_assigning_distinct_ids(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    with CatalogStore.create(path, id_factory=id_factory(1)) as store:
        catalog = make_catalog(store)
        first = store.put_profile(catalog, make_data("A")).profile
    # Una fábrica que repite primero un id ya usado: debe saltárselo.
    repeated = iter([first.id, first.id, uid(1000)])
    with CatalogStore.open(path, id_factory=lambda: next(repeated)) as store:
        second = store.put_profile(catalog, make_data("B")).profile
    assert second.id == uid(1000)
