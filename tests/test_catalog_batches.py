"""Lotes de importación y esquema v2 del catálogo (T-022 E2-A; enmienda de ADR-009)."""

import sqlite3
from pathlib import Path
from uuid import UUID

import pytest

from catalog_support import (
    PROVENANCE,
    fixed_clock,
    id_factory,
    make_data,
    make_source,
    message_key,
    new_store,
)
from veritas.catalogs.errors import CatalogError
from veritas.catalogs.model import (
    ImportBatch,
    ImportSource,
    profile_json,
    profile_sha256,
    sha256_of,
)
from veritas.catalogs.store import (
    _SCHEMA_V1,
    APPLICATION_ID,
    SCHEMA_VERSION,
    CatalogStore,
    PutStatus,
)
from veritas.core.diagnostics import DiagnosticCode


def make_catalog(store: CatalogStore, name: str = "Lotes") -> UUID:
    return store.create_catalog(
        name=name, source=PROVENANCE.source, license=PROVENANCE.license, edition="2026"
    ).id


def raw(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(path, isolation_level=None)


def tamper(path: Path, statement: str, *params: object) -> None:
    connection = raw(path)
    try:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute(statement, params)
    finally:
        connection.close()


# --- Esquema v2: nuevo y migrado ---------------------------------------------------------------


def v1_file(path: Path) -> UUID:
    """Archivo escrito como lo dejaba E1 (esquema v1) con un perfil, sin lotes."""
    store_ids = id_factory(500)
    catalog_id, profile_id = store_ids(), store_ids()
    connection = raw(path)
    for statement in _SCHEMA_V1:
        connection.execute(statement)
    connection.execute(
        "INSERT INTO catalogs VALUES (?, 'Antiguo', '', 'S', 'L', 'E', 0)", (str(catalog_id),)
    )
    from veritas.catalogs.model import CatalogProfile  # noqa: PLC0415 (solo este auxiliar)

    profile = CatalogProfile(profile_id, 1, catalog_id, make_data("OLD"))
    props = profile.properties
    connection.execute(
        "INSERT INTO profile_revisions (profile_id, revision, catalog_id, series, designation,"
        " family, area, iy, iz, torsion_constant, linear_mass, content_json, content_sha256)"
        " VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            str(profile_id),
            str(catalog_id),
            profile.series,
            profile.designation,
            profile.family.value,
            props.area.value,
            props.iy.value,
            props.iz.value,
            props.torsion_constant.value,
            props.linear_mass.value,
            profile_json(profile),
            profile_sha256(profile),
        ),
    )
    connection.execute(f"PRAGMA application_id = {APPLICATION_ID}")
    connection.execute("PRAGMA user_version = 1")
    connection.close()
    return catalog_id


def columns(path: Path, table: str) -> list[tuple[object, ...]]:
    connection = raw(path)
    try:
        return connection.execute(f"PRAGMA table_info({table})").fetchall()
    finally:
        connection.close()


def test_a_new_file_has_the_batch_schema(tmp_path: Path) -> None:
    path = tmp_path / "nuevo.sqlite"
    CatalogStore.create(path).close()
    assert [c[1] for c in columns(path, "import_batches")][:3] == [
        "id",
        "catalog_id",
        "imported_at",
    ]
    assert "batch_id" in [c[1] for c in columns(path, "profile_revisions")]


def test_a_v1_file_migrates_to_the_same_schema_and_keeps_its_data(tmp_path: Path) -> None:
    old, new = tmp_path / "v1.sqlite", tmp_path / "nuevo.sqlite"
    catalog_id = v1_file(old)
    CatalogStore.create(new).close()
    with CatalogStore.open(old) as store:
        assert store.version == SCHEMA_VERSION == 2
        (profile,) = store.list_profiles(catalog_id)
        assert profile.designation == "OLD"
        assert store.revision_batch(profile.id, 1) is None  # anterior a la migración
        assert store.list_batches() == ()
        assert store.verify().ok
    for table in ("import_batches", "profile_revisions", "catalogs"):
        assert columns(old, table) == columns(new, table)


def test_migrating_does_not_touch_the_profiles(tmp_path: Path) -> None:
    path = tmp_path / "v1.sqlite"
    v1_file(path)
    connection = raw(path)
    before = connection.execute("SELECT * FROM profile_revisions").fetchall()
    connection.close()
    CatalogStore.open(path).close()
    connection = raw(path)
    after = connection.execute("SELECT * FROM profile_revisions").fetchall()
    connection.close()
    assert [row[:13] for row in after] == before
    assert [row[13] for row in after] == [None]


# --- Escribir un lote --------------------------------------------------------------------------


def test_a_batch_records_its_source_its_outcome_and_links_its_revisions(tmp_path: Path) -> None:
    with new_store(tmp_path) as store:
        catalog = make_catalog(store)
        source = make_source(rows_read=4, rows_rejected=1)
        items = [make_data(f"P-{n}") for n in range(3)]
        outcome = store.import_batch(catalog, items, source)
        batch = outcome.batch
        assert [r.status for r in outcome.results] == [PutStatus.CREATED] * 3
        assert (batch.created, batch.revised, batch.unchanged) == (3, 0, 0)
        assert batch.imported_at == "2026-11-03T12:30:45Z"
        assert batch.source == source
        assert batch.catalog_id == catalog
        assert store.list_batches() == (batch,)
        assert store.list_batches(catalog) == (batch,)
        for result in outcome.results:
            assert store.revision_batch(result.profile.id, 1) == batch
        assert store.verify().ok


def test_a_second_batch_revises_and_leaves_unchanged_rows_unlinked(tmp_path: Path) -> None:
    with new_store(tmp_path) as store:
        catalog = make_catalog(store)
        first = store.import_batch(catalog, [make_data("A"), make_data("B")], make_source(2))
        second = store.import_batch(
            catalog, [make_data("A"), make_data("B", area=9e-3)], make_source(2)
        ).batch
        assert (second.created, second.revised, second.unchanged) == (0, 1, 1)
        a, b = (r.profile for r in first.results)
        assert store.revision_batch(a.id, 1) == first.batch
        assert store.revision_batch(b.id, 1) == first.batch
        assert store.revision_batch(b.id, 2) == second
        with pytest.raises(CatalogError) as error:
            store.revision_batch(a.id, 2)  # A no tiene revisión 2: nada se enlazó
        assert message_key(error) == "catalog.profile_not_found"
        assert [x.id for x in store.list_batches()] == [first.batch.id, second.id]
        assert store.verify().ok


def test_an_unchanged_reimport_still_records_its_batch(tmp_path: Path) -> None:
    with new_store(tmp_path) as store:
        catalog = make_catalog(store)
        store.import_batch(catalog, [make_data("A")], make_source(1))
        again = store.import_batch(catalog, [make_data("A")], make_source(1)).batch
        assert (again.created, again.revised, again.unchanged) == (0, 0, 1)
        assert again.written == 0
        assert len(store.list_batches()) == 2
        assert [p.revision for p in store.list_profiles(catalog, all_revisions=True)] == [1]
        assert store.verify().ok


def test_batches_belong_to_their_catalog(tmp_path: Path) -> None:
    with new_store(tmp_path) as store:
        one, two = make_catalog(store, "Uno"), make_catalog(store, "Dos")
        store.import_batch(one, [make_data("A")], make_source(1))
        second = store.import_batch(two, [make_data("A")], make_source(1)).batch
        assert store.list_batches(two) == (second,)
        assert len(store.list_batches()) == 2
        with pytest.raises(CatalogError) as error:
            store.list_batches(UUID(int=0))
        assert message_key(error) == "catalog.not_found"


# --- Fallos: el archivo no cambia ----------------------------------------------------------------


def stored_bytes(path: Path) -> bytes:
    return path.read_bytes()


def test_a_wrong_row_count_is_rejected_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    store = new_store(tmp_path, "c.sqlite")
    catalog = make_catalog(store)
    store.close()
    before = stored_bytes(path)
    with CatalogStore.open(path) as reopened:
        with pytest.raises(CatalogError) as error:
            reopened.import_batch(catalog, [make_data("A")], make_source(rows_read=3))
        assert message_key(error) == "catalog.invalid_batch"
    assert stored_bytes(path) == before


def test_an_unknown_catalog_is_rejected_without_writing(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    new_store(tmp_path, "c.sqlite").close()
    before = stored_bytes(path)
    with CatalogStore.open(path) as store:
        with pytest.raises(CatalogError) as error:
            store.import_batch(UUID(int=7), [make_data("A")], make_source(1))
        assert message_key(error) == "catalog.not_found"
    assert stored_bytes(path) == before


def test_a_protected_catalog_rejects_the_batch_and_its_record(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    store = new_store(tmp_path, "c.sqlite")
    catalog = make_catalog(store)
    store.lock_catalog(catalog)
    store.close()
    before = stored_bytes(path)
    with CatalogStore.open(path) as store:
        with pytest.raises(CatalogError) as error:
            store.import_batch(catalog, [make_data("A")], make_source(1))
        assert message_key(error) == "catalog.protected"
        assert store.list_batches() == ()
    assert stored_bytes(path) == before


def test_a_repeated_key_rejects_the_whole_batch_and_its_record(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    store = new_store(tmp_path, "c.sqlite")
    catalog = make_catalog(store)
    store.close()
    before = stored_bytes(path)
    with CatalogStore.open(path) as store:
        with pytest.raises(CatalogError) as error:
            store.import_batch(catalog, [make_data("A"), make_data("A")], make_source(2))
        assert error.value.code is DiagnosticCode.REVISION_CONFLICT
    assert stored_bytes(path) == before


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize("fail_on_call", [1, 3])
def test_a_failure_in_the_middle_leaves_neither_profiles_nor_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: type[BaseException],
    fail_on_call: int,
) -> None:
    path = tmp_path / "c.sqlite"
    store = new_store(tmp_path, "c.sqlite")
    catalog = make_catalog(store)
    store.close()
    before = stored_bytes(path)
    calls: list[int] = []
    original = CatalogStore._insert_revision

    def flaky(self: CatalogStore, con: sqlite3.Connection, profile: object) -> None:
        calls.append(1)
        if len(calls) == fail_on_call:
            raise failure("fallo inyectado")
        original(self, con, profile)  # type: ignore[arg-type]

    monkeypatch.setattr(CatalogStore, "_insert_revision", flaky)
    with CatalogStore.open(path) as reopened:
        items = [make_data(f"N-{n}") for n in range(4)]
        with pytest.raises(failure):
            reopened.import_batch(catalog, items, make_source(4))
        monkeypatch.undo()
        assert reopened.list_batches() == ()
        assert reopened.list_profiles(catalog) == ()
    assert stored_bytes(path) == before


def test_a_naive_clock_is_rejected_and_nothing_is_written(tmp_path: Path) -> None:
    from datetime import datetime  # noqa: PLC0415 (solo esta prueba)

    path = tmp_path / "c.sqlite"
    store = CatalogStore.create(path, clock=lambda: datetime(2026, 1, 1))
    catalog = make_catalog(store)
    store.close()
    before = stored_bytes(path)
    with (
        CatalogStore.open(path, clock=lambda: datetime(2026, 1, 1)) as store,
        pytest.raises(RuntimeError),
    ):
        store.import_batch(catalog, [make_data("A")], make_source(1))
    assert stored_bytes(path) == before


def test_the_default_clock_stamps_in_utc(tmp_path: Path) -> None:
    with CatalogStore.create(tmp_path / "c.sqlite", id_factory=id_factory()) as store:
        catalog = make_catalog(store)
        stamp = store.import_batch(catalog, [make_data("A")], make_source(1)).batch.imported_at
    assert stamp.endswith("Z")
    assert len(stamp) == len("2026-11-03T12:30:45Z")


# --- Verificación de lotes --------------------------------------------------------------------


def verified(tmp_path: Path) -> tuple[Path, UUID]:
    path = tmp_path / "v.sqlite"
    store = new_store(tmp_path, "v.sqlite")
    catalog = make_catalog(store)
    batch = store.import_batch(catalog, [make_data("A"), make_data("B")], make_source(2)).batch
    store.close()
    return path, batch.id


def problems(path: Path) -> list[tuple[str, object]]:
    with CatalogStore.open(path) as store:
        report = store.verify()
    return [(d.message_key, d.params_dict().get("field")) for d in report.issues]


def test_verify_detects_a_mapping_that_no_longer_matches_its_hash(tmp_path: Path) -> None:
    path, batch_id = verified(tmp_path)
    tamper(
        path, "UPDATE import_batches SET mapping_json = ? WHERE id = ?", '{"otro":1}', str(batch_id)
    )
    assert problems(path) == [("catalog.verify_batch", "mapping_sha256")]


def test_verify_detects_inconsistent_counts(tmp_path: Path) -> None:
    path, _ = verified(tmp_path)
    tamper(path, "UPDATE import_batches SET created = 1, unchanged = 1")
    assert [key for key, _ in problems(path)] == ["catalog.verify_batch"]
    tamper(path, "UPDATE import_batches SET created = 2, unchanged = 0, rows_accepted = 5")
    assert problems(path) == [("catalog.verify_batch", "rows_accepted")]


def test_verify_detects_a_lost_link(tmp_path: Path) -> None:
    path, _ = verified(tmp_path)
    tamper(path, "UPDATE profile_revisions SET batch_id = NULL WHERE designation = 'A'")
    assert problems(path) == [("catalog.verify_batch", "enlaces")]


def test_verify_detects_a_revision_linked_to_another_catalogs_batch(tmp_path: Path) -> None:
    path, _ = verified(tmp_path)
    connection = raw(path)
    other = "00000000-0000-4000-8000-0000000000aa"
    connection.execute("INSERT INTO catalogs VALUES (?, 'Otro', '', 'S', 'L', 'E', 0)", (other,))
    connection.execute("UPDATE import_batches SET catalog_id = ?", (other,))
    connection.close()
    assert problems(path) == [("catalog.verify_batch", "catálogo")]


def test_verify_reports_a_batch_pointing_to_a_missing_catalog(tmp_path: Path) -> None:
    path, _ = verified(tmp_path)
    tamper(path, "UPDATE import_batches SET catalog_id = '00000000-0000-4000-8000-0000000000bb'")
    keys = [key for key, _ in problems(path)]
    assert "catalog.verify_orphan" in keys


def test_verify_reports_an_invalid_timestamp(tmp_path: Path) -> None:
    path, _ = verified(tmp_path)
    tamper(path, "UPDATE import_batches SET imported_at = 'ayer'")
    assert problems(path) == [("catalog.verify_batch", "imported_at")]


# --- Dominio del lote ---------------------------------------------------------------------------


def source_args(**changes: object) -> dict[str, object]:
    mapping = '{"columns":{}}'
    base: dict[str, object] = {
        "file_name": "a.csv",
        "file_format": "CSV",
        "file_sha256": sha256_of("x"),
        "mapping_json": mapping,
        "mapping_sha256": sha256_of(mapping),
        "rows_read": 3,
        "rows_rejected": 1,
    }
    return {**base, **changes}


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"file_name": "dir/a.csv"}, "file_name"),
        ({"file_name": "dir\\a.csv"}, "file_name"),
        ({"file_format": "XML"}, "file_format"),
        ({"file_sha256": "abc"}, "file_sha256"),
        ({"file_sha256": "A" * 64}, "file_sha256"),
        ({"mapping_sha256": "0" * 64}, "mapping_sha256"),
        ({"rows_rejected": 4}, "rows_rejected"),
    ],
)
def test_import_source_validates(changes: dict[str, object], field: str) -> None:
    with pytest.raises(CatalogError) as error:
        ImportSource(**source_args(**changes))  # type: ignore[arg-type]
    assert error.value.diagnostic.params_dict() == {"field": field}


@pytest.mark.parametrize("changes", [{"rows_read": -1}, {"rows_read": True}, {"rows_read": 1.5}])
def test_import_source_rejects_bad_counts(changes: dict[str, object]) -> None:
    with pytest.raises((CatalogError, TypeError)):
        ImportSource(**source_args(**changes))  # type: ignore[arg-type]


def test_import_batch_validates_counts_and_stamp() -> None:
    source = ImportSource(**source_args())  # type: ignore[arg-type]
    ids = id_factory(900)
    ok = ImportBatch(ids(), ids(), "2026-11-03T12:30:45Z", source, 1, 1, 0)
    assert ok.written == 2
    with pytest.raises(CatalogError):
        ImportBatch(ids(), ids(), "2026-11-03T12:30:45Z", source, 1, 0, 0)  # 1 != 2 aceptadas
    with pytest.raises(CatalogError):
        ImportBatch(ids(), ids(), "2026-11-03 12:30:45", source, 1, 1, 0)
    assert fixed_clock().tzinfo is not None
