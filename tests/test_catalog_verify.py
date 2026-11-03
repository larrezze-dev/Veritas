"""``CatalogStore.verify`` (T-022 E1): detecta alteraciones y no modifica el archivo."""

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from uuid import UUID

import pytest

from catalog_support import id_factory, make_data
from veritas.catalogs.model import sha256_of
from veritas.catalogs.store import CatalogStore

Tamper = Callable[[sqlite3.Connection], object]


def build(path: Path) -> UUID:
    with CatalogStore.create(path, id_factory=id_factory()) as store:
        catalog = store.create_catalog(name="C", source="S", license="L", edition="E").id
        store.put_profiles(catalog, [make_data("A"), make_data("B")])
        store.put_profile(catalog, make_data("A", area=2e-3))  # A tiene dos revisiones
    return catalog


def keys(path: Path) -> list[str]:
    with CatalogStore.open(path) as store:
        return sorted({issue.message_key for issue in store.verify().issues})


def tamper(path: Path, action: Tamper) -> None:
    connection = sqlite3.connect(path, isolation_level=None)
    try:
        action(connection)
    finally:
        connection.close()


def test_clean_catalog_verifies_and_verify_does_not_write(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    build(path)
    before = path.read_bytes()
    with CatalogStore.open(path) as store:
        report = store.verify()
    assert report.ok
    assert report.issues == ()
    assert report.revisions_checked == 3
    assert path.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["c.sqlite"]


def test_empty_catalog_verifies(tmp_path: Path) -> None:
    with CatalogStore.create(tmp_path / "c.sqlite") as store:
        report = store.verify()
    assert report.ok
    assert report.revisions_checked == 0


def edit_document(
    connection: sqlite3.Connection, mutate: Callable[[dict[str, object]], None]
) -> None:
    """Cambia el documento de la revisión 1 de A y recalcula su hash (manipulación coherente)."""
    row = connection.execute(
        "SELECT profile_id, content_json FROM profile_revisions"
        " WHERE designation = 'A' AND revision = 1"
    ).fetchone()
    document = json.loads(row[1])
    mutate(document)
    text = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    connection.execute(
        "UPDATE profile_revisions SET content_json = ?, content_sha256 = ?"
        " WHERE profile_id = ? AND revision = 1",
        (text, sha256_of(text), row[0]),
    )


def test_altered_document_without_rehash_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    build(path)
    tamper(
        path,
        lambda c: c.execute(
            "UPDATE profile_revisions SET content_json = replace(content_json, '0.001', '0.002')"
            " WHERE designation = 'B'"
        ),
    )
    assert keys(path) == ["catalog.verify_hash"]


def test_altered_hash_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    build(path)
    tamper(path, lambda c: c.execute("UPDATE profile_revisions SET content_sha256 = 'x'"))
    assert keys(path) == ["catalog.verify_hash"]


def test_consistent_but_non_canonical_document_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    build(path)

    def spaced(connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT profile_id, content_json FROM profile_revisions WHERE designation = 'B'"
        ).fetchone()
        text = json.dumps(json.loads(row[1]), indent=1, ensure_ascii=False)
        connection.execute(
            "UPDATE profile_revisions SET content_json = ?, content_sha256 = ?"
            " WHERE profile_id = ?",
            (text, sha256_of(text), row[0]),
        )

    tamper(path, spaced)
    assert keys(path) == ["catalog.verify_document"]


def test_document_claiming_a_verified_status_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    build(path)

    def claim(document: dict[str, object]) -> None:
        data = document["data"]
        assert isinstance(data, dict)
        data["properties"]["area"]["status"] = "VERIFICADO"

    tamper(path, lambda c: edit_document(c, claim))
    assert keys(path) == ["catalog.verify_document"]


def test_query_columns_must_match_the_document(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    build(path)
    tamper(path, lambda c: c.execute("UPDATE profile_revisions SET area = area * 2"))
    assert keys(path) == ["catalog.verify_columns"]
    tamper(path, lambda c: c.execute("UPDATE profile_revisions SET area = NULL"))
    assert keys(path) == ["catalog.verify_columns"]


def test_identity_columns_must_match_the_document(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    build(path)
    tamper(path, lambda c: c.execute("UPDATE profile_revisions SET series = 'OTRA'"))
    assert "catalog.verify_columns" in keys(path)


def test_revision_gap_is_detected(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    build(path)
    tamper(
        path,
        lambda c: c.execute(
            "DELETE FROM profile_revisions WHERE revision = 1 AND designation = 'A'"
        ),
    )
    assert keys(path) == ["catalog.verify_revision_sequence"]


def test_orphan_rows_are_detected(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    build(path)

    def orphan(connection: sqlite3.Connection) -> None:
        connection.execute("PRAGMA foreign_keys = OFF")
        connection.execute("DELETE FROM catalogs")

    tamper(path, orphan)
    assert "catalog.verify_orphan" in keys(path)


def test_two_profiles_sharing_a_natural_key_are_detected(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    build(path)

    def duplicate(connection: sqlite3.Connection) -> None:
        # Mismo (catálogo, serie, designación, revisión) en otro profile_id: la UNIQUE lo
        # impide, así que se cambia la revisión para sortearla y se detecta la identidad.
        connection.execute("UPDATE profile_revisions SET profile_id = 'otro' WHERE revision = 2")

    tamper(path, duplicate)
    found = keys(path)
    assert "catalog.verify_identity" in found or "catalog.verify_columns" in found


def test_verify_reports_every_problem_not_just_the_first(tmp_path: Path) -> None:
    path = tmp_path / "c.sqlite"
    build(path)
    tamper(path, lambda c: c.execute("UPDATE profile_revisions SET content_sha256 = 'x'"))
    with CatalogStore.open(path) as store:
        report = store.verify()
    assert not report.ok
    assert len(report.issues) == 3
    assert report.revisions_checked == 3


@pytest.mark.parametrize("field", ["application_id", "user_version"])
def test_header_changes_after_opening_are_reported(tmp_path: Path, field: str) -> None:
    path = tmp_path / "c.sqlite"
    build(path)
    with CatalogStore.open(path) as store:
        tamper(path, lambda c: c.execute(f"PRAGMA {field} = 7"))
        report = store.verify()
    assert [i.message_key for i in report.issues] == ["catalog.verify_header"]
