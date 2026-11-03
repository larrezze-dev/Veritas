"""Transaccionalidad del almacén (T-022 E1): un fallo deja el archivo idéntico byte a byte."""

import sqlite3
from pathlib import Path
from uuid import UUID

import pytest

from catalog_support import id_factory, make_data, message_key
from veritas.catalogs.errors import CatalogError
from veritas.catalogs.model import CatalogProfile
from veritas.catalogs.store import CatalogStore, PutStatus


def build(path: Path) -> tuple[CatalogStore, UUID]:
    store = CatalogStore.create(path, id_factory=id_factory())
    catalog = store.create_catalog(name="Mi catálogo", source="S", license="L", edition="E").id
    store.put_profiles(catalog, [make_data("A"), make_data("B")])
    return store, catalog


def files(path: Path) -> list[str]:
    return sorted(p.name for p in path.parent.iterdir())


@pytest.mark.parametrize("failure", [RuntimeError, KeyboardInterrupt])
@pytest.mark.parametrize("fail_on_call", [1, 3, 5])
def test_failure_in_the_middle_of_a_batch_leaves_the_file_identical(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: type[BaseException],
    fail_on_call: int,
) -> None:
    path = tmp_path / "c.sqlite"
    store, catalog = build(path)
    store.close()
    before = path.read_bytes()

    calls: list[int] = []
    original = CatalogStore._insert_revision

    def flaky(self: CatalogStore, con: sqlite3.Connection, profile: CatalogProfile) -> None:
        calls.append(1)
        if len(calls) == fail_on_call:
            raise failure("fallo inyectado en el almacén")
        original(self, con, profile)

    monkeypatch.setattr(CatalogStore, "_insert_revision", flaky)
    batch = [make_data(f"N-{n}") for n in range(4)] + [make_data("A", area=5e-3)]
    with CatalogStore.open(path) as reopened:
        with pytest.raises(failure):
            reopened.put_profiles(catalog, batch)
        assert len(calls) == fail_on_call
        monkeypatch.undo()
        # El almacén sigue usable y no vio ninguna fila del lote fallido.
        assert [p.designation for p in reopened.list_profiles(catalog)] == ["A", "B"]
    assert path.read_bytes() == before
    assert files(path) == ["c.sqlite"]  # sin diario ni archivos auxiliares


def test_a_store_keeps_working_after_a_failed_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "c.sqlite"
    store, catalog = build(path)
    original = CatalogStore._insert_revision
    state = {"fail": True}

    def flaky(self: CatalogStore, con: sqlite3.Connection, profile: CatalogProfile) -> None:
        if state["fail"]:
            raise RuntimeError("fallo inyectado")
        original(self, con, profile)

    monkeypatch.setattr(CatalogStore, "_insert_revision", flaky)
    with pytest.raises(RuntimeError):
        store.put_profile(catalog, make_data("C"))
    state["fail"] = False
    assert store.put_profile(catalog, make_data("C")).status is PutStatus.CREATED
    assert [p.designation for p in store.list_profiles(catalog)] == ["A", "B", "C"]
    store.close()


def test_rejected_batches_do_not_change_the_file(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    store, catalog = build(path)
    locked = store.create_catalog(name="Fijo", source="S", license="L", edition="E").id
    store.lock_catalog(locked)
    store.close()
    before = path.read_bytes()
    with CatalogStore.open(path) as reopened:
        rejected = [
            (catalog, [make_data("N"), make_data("N")], "catalog.duplicate_key"),
            (locked, [make_data("N")], "catalog.protected"),
        ]
        for target, batch, key in rejected:
            with pytest.raises(CatalogError) as error:
                reopened.put_profiles(target, batch)
            assert message_key(error) == key
        with pytest.raises(CatalogError):
            reopened.create_catalog(name="mi catálogo", source="S", license="L", edition="E")
    assert path.read_bytes() == before


def test_unchanged_reimport_does_not_modify_the_file(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    store, catalog = build(path)
    store.close()
    before = path.read_bytes()
    with CatalogStore.open(path) as reopened:
        results = reopened.put_profiles(catalog, [make_data("A"), make_data("B")])
    assert [r.status for r in results] == [PutStatus.UNCHANGED, PutStatus.UNCHANGED]
    assert path.read_bytes() == before


def test_a_successful_batch_commits_everything_once(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    store, catalog = build(path)
    results = store.put_profiles(
        catalog,
        [make_data("A", area=2e-3), make_data("C"), make_data("B")],
    )
    assert [r.status for r in results] == [
        PutStatus.REVISED,
        PutStatus.CREATED,
        PutStatus.UNCHANGED,
    ]
    store.close()
    with CatalogStore.open(path) as reopened:
        assert len(reopened.list_profiles(catalog, all_revisions=True)) == 4
    assert files(path) == ["c.sqlite"]
