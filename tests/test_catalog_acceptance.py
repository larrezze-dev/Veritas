"""Batería de aceptación SEC del catálogo (T-022 E2): 100 filas válidas + 5 defectuosas.

SRS §5.2, «Aceptación SEC»: importar 100 filas de prueba, identificar registros incorrectos sin
corromper el catálogo, cerrar y reabrir sin alterar valores ni fuentes. (Filtrar, comparar,
seleccionar y asignar a barras son de T-023 y T-024.)

Los valores esperados se recalculan aquí con fórmulas cerradas en ``fractions`` a partir de las
dimensiones del archivo, no con el importador contra sí mismo.
"""

import csv
import hashlib
import re
import sqlite3
from fractions import Fraction
from pathlib import Path

import pytest

from catalog_cli_golden import DATA_DIR, MAPPING, SYNTHETIC_ARGS
from veritas.catalogs.model import profile_json, profile_sha256, sha256_of
from veritas.catalogs.store import CatalogStore
from veritas.cli import main
from veritas.core.sections.properties import PropertyStatus

DB = "c.sqlite"
STEEL = Fraction(7850)


@pytest.fixture
def imported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Directorio con el catálogo ya importado desde 100 + 5 filas, en un único archivo."""
    monkeypatch.chdir(tmp_path)
    good = (DATA_DIR / "catalog_synthetic_100.csv").read_text(encoding="utf-8-sig").splitlines()
    bad = (DATA_DIR / "catalog_synthetic_bad.csv").read_text(encoding="utf-8-sig").splitlines()
    (tmp_path / "lote105.csv").write_text("\n".join([*good, *bad[1:]]) + "\n", encoding="utf-8-sig")
    (tmp_path / MAPPING).write_bytes((DATA_DIR / MAPPING).read_bytes())
    assert main(["catalog", "create", "--db", DB, "--nombre", "Sint", "--fuente", "SINTÉTICO",
                 "--licencia", "Sin valor comercial", "--edicion", "2026"]) == 0  # fmt: skip
    code = main(
        ["catalog", "import", "--db", DB, "--catalogo", "Sint", "lote105.csv", *SYNTHETIC_ARGS]
    )
    assert code == 0
    return tmp_path


def file_rows() -> list[dict[str, str]]:
    with (DATA_DIR / "catalog_synthetic_100.csv").open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def expected(row: dict[str, str]) -> dict[str, Fraction]:
    """A, Iy, Iz y masa SI de una I soldada de chapas (mm → m), por fórmula cerrada."""
    h, b, tw, tf = (Fraction(int(row[name])) for name in ("h (mm)", "b (mm)", "tw (mm)", "tf (mm)"))
    web = h - 2 * tf
    area = 2 * b * tf + web * tw
    iy = (b * h**3 - (b - tw) * web**3) / 12
    iz = (2 * tf * b**3 + web * tw**3) / 12
    return {
        "area": area / 10**6,
        "iy": iy / 10**12,
        "iz": iz / 10**12,
        "linear_mass": area / 10**6 * STEEL,
    }


def open_store() -> CatalogStore:
    return CatalogStore.open(DB)


def test_100_valid_rows_are_stored_and_the_5_defective_ones_are_identified(imported: Path) -> None:
    with open_store() as store:
        info = store.catalog_by_name("Sint")
        profiles = store.list_profiles(info.id)
        (batch,) = store.list_batches(info.id)
    assert len(profiles) == 100
    assert (batch.source.rows_read, batch.source.rows_accepted, batch.source.rows_rejected) == (
        105,
        100,
        5,
    )
    assert (batch.created, batch.revised, batch.unchanged) == (100, 0, 0)
    assert not any("MALO" in p.designation or not p.designation for p in profiles)


def test_the_rejected_rows_are_reported_with_their_lines(
    imported: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    code = main(["catalog", "import", "--db", DB, "--catalogo", "Sint", "lote105.csv",
                 *SYNTHETIC_ARGS, "--simular"])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0
    reported = {int(n): field for n, field in re.findall(r"línea (\d+) \[(\w+)\]", out)}
    assert reported == {102: "area", 103: "iy", 104: "designation", 105: "iz", 106: "family"}


def test_values_match_independent_closed_formulas(imported: Path) -> None:
    rows = {row["Designación"]: row for row in file_rows()}
    with open_store() as store:
        profiles = store.list_profiles(store.catalog_by_name("Sint").id)
    assert {p.designation for p in profiles} == set(rows)
    for profile in profiles:
        want = expected(rows[profile.designation])
        props = profile.properties
        assert props.area.value == pytest.approx(float(want["area"]), rel=1e-12)
        assert props.iy.value == pytest.approx(float(want["iy"]), rel=1e-6)  # CSV a 4 decimales
        assert props.iz.value == pytest.approx(float(want["iz"]), rel=1e-6)
        assert props.linear_mass.value == pytest.approx(float(want["linear_mass"]), rel=1e-5)
        dims = {d.name: d.value for d in profile.data.dimensions}
        assert dims == pytest.approx(
            {k: int(rows[profile.designation][f"{k} (mm)"]) / 1000 for k in ("h", "b", "tw", "tf")}
        )


def test_every_property_is_unverified_or_unavailable_and_j_is_never_deduced(imported: Path) -> None:
    with open_store() as store:
        profiles = store.list_profiles(store.catalog_by_name("Sint").id)
    for profile in profiles:
        props = profile.properties
        for name in ("area", "iy", "iz", "linear_mass"):
            assert getattr(props, name).status is PropertyStatus.IMPORTADO_SIN_VERIFICAR
        assert props.torsion_constant.status is PropertyStatus.NO_DISPONIBLE
        assert props.torsion_constant.value is None
        assert profile.data.warnings == ()  # masa = A·ρ, A = chapas, Iy > Iz


def test_every_profile_carries_its_provenance(imported: Path) -> None:
    with open_store() as store:
        info = store.catalog_by_name("Sint")
        profiles = store.list_profiles(info.id)
    for profile in profiles:
        prov = profile.provenance
        assert (prov.source, prov.license, prov.edition) == (
            info.source,
            info.license,
            info.edition,
        )
        assert prov.method.startswith("Fixture sintético")


def test_close_and_reopen_changes_nothing(imported: Path) -> None:
    path = imported / DB
    with open_store() as store:
        info = store.catalog_by_name("Sint")
        first = store.list_profiles(info.id)
        first_batches = store.list_batches(info.id)
    before = path.read_bytes()
    with open_store() as store:
        second = store.list_profiles(info.id)
        assert store.list_batches(info.id) == first_batches
        assert store.verify().ok
    assert second == first  # campo a campo
    assert path.read_bytes() == before  # abrir no escribe
    connection = sqlite3.connect(path)
    stored = connection.execute(
        "SELECT profile_id, revision, content_json, content_sha256 FROM profile_revisions"
    ).fetchall()
    connection.close()
    assert len(stored) == 100
    by_id = {str(p.id): p for p in second}
    for profile_id, revision, text, digest in stored:
        profile = by_id[profile_id]
        assert revision == profile.revision == 1
        assert text == profile_json(profile)
        assert digest == profile_sha256(profile) == sha256_of(text)


def test_the_batch_records_the_source_file_and_mapping_exactly(imported: Path) -> None:
    with open_store() as store:
        (batch,) = store.list_batches()
    assert (
        batch.source.file_sha256
        == hashlib.sha256((imported / "lote105.csv").read_bytes()).hexdigest()
    )
    assert batch.source.file_name == "lote105.csv"
    assert batch.source.file_format == "CSV"
    assert "veritas-catalog-mapping/1" in batch.source.mapping_json
    assert batch.source.mapping_sha256 == sha256_of(batch.source.mapping_json)


def test_an_unexpected_failure_in_the_middle_of_the_import_leaves_the_file_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lote.csv").write_bytes((DATA_DIR / "catalog_synthetic_100.csv").read_bytes())
    (tmp_path / MAPPING).write_bytes((DATA_DIR / MAPPING).read_bytes())
    main(["catalog", "create", "--db", DB, "--nombre", "Sint", "--fuente", "S", "--licencia", "L",
          "--edicion", "E"])  # fmt: skip
    before = (tmp_path / DB).read_bytes()
    calls = 0
    original = CatalogStore._insert_revision

    def flaky(self: CatalogStore, con: sqlite3.Connection, profile: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 60:
            raise RuntimeError("fallo inyectado a mitad del lote")
        original(self, con, profile)  # type: ignore[arg-type]

    monkeypatch.setattr(CatalogStore, "_insert_revision", flaky)
    with pytest.raises(RuntimeError):
        main(["catalog", "import", "--db", DB, "--catalogo", "Sint", "lote.csv", *SYNTHETIC_ARGS])
    assert calls == 60
    assert (tmp_path / DB).read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted([DB, "lote.csv", MAPPING])
