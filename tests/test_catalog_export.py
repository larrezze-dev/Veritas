"""Exportación de perfiles (T-023 E2): CSV nativo exacto, JSON completo, procedencia y escritura."""

import csv
import io
import json
from pathlib import Path

import pytest

from catalog_support import (
    PROVENANCE,
    assert_renders,
    make_properties,
    uid,
)
from veritas.catalogs.errors import CatalogFileError
from veritas.catalogs.export import (
    EXPORT_FORMAT,
    PROVENANCE_FORMAT,
    csv_text,
    json_text,
    provenance_text,
    redistribution_warning,
)
from veritas.catalogs.fileio import write_files
from veritas.catalogs.model import (
    CatalogInfo,
    CatalogProfile,
    ProfileData,
    ProfileDimension,
    ProfileFamily,
    profile_sha256,
    sha256_of,
)

INFO = CatalogInfo(
    id=uid(900),
    name="Cat",
    description="",
    source="Fuente",
    license="CC BY 4.0",
    edition="2026",
    protected=True,
)
STAMP = "2026-11-03T12:00:00Z"


def profile(  # noqa: PLR0913 (campos opcionales por nombre, para variar un caso)
    designation: str,
    *,
    n: int = 1,
    area: float | None = 1.0e-3,
    iy: float | None = 2.0e-6,
    torsion: float | None = None,
    mass: float | None = 7.85,
    dims: tuple[tuple[str, float], ...] = (("h", 0.1), ("b", 0.05)),
    family: ProfileFamily = ProfileFamily.I,
) -> CatalogProfile:
    data = ProfileData(
        series="S1",
        designation=designation,
        family=family,
        dimensions=tuple(ProfileDimension(name, value) for name, value in dims),
        properties=make_properties(area=area, iy=iy, iz=3.0e-7, torsion=torsion, mass=mass),
        provenance=PROVENANCE,
    )
    return CatalogProfile(uid(100 + n), 1, INFO.id, data)


def rows_of(text: str) -> list[list[str]]:
    return list(csv.reader(io.StringIO(text)))


# --- CSV -----------------------------------------------------------------------------------


def test_csv_header_is_the_native_field_names_with_the_dimension_union_sorted() -> None:
    profiles = [
        profile("A", dims=(("h", 0.1),)),
        profile("B", n=2, dims=(("tw", 0.01), ("b", 0.05))),
    ]
    header = rows_of(csv_text(profiles))[0]
    assert header == [
        "series",
        "designation",
        "family",
        "area",
        "iy",
        "iz",
        "torsion_constant",
        "linear_mass",
        "dim_b",
        "dim_h",
        "dim_tw",
    ]


def test_csv_numbers_are_exact_and_absent_values_are_empty_not_zero() -> None:
    awkward = 0.1 + 0.2  # 0.30000000000000004: la ida y vuelta debe ser exacta
    text = csv_text([profile("A", area=awkward, torsion=None, mass=None, dims=(("h", 1e-9),))])
    (_, row) = rows_of(text)
    header = rows_of(text)[0]
    cell = dict(zip(header, row, strict=True))
    assert float(cell["area"]) == awkward
    assert cell["torsion_constant"] == ""
    assert cell["linear_mass"] == ""
    assert float(cell["dim_h"]) == 1e-9
    assert "inf" not in text.lower()
    assert "nan" not in text.lower()


def test_csv_uses_comma_dot_and_unix_line_endings_with_a_header_only_when_empty() -> None:
    text = csv_text([profile("A")])
    assert text.count("\n") == 2
    assert "\r" not in text
    assert csv_text([]).splitlines() == [
        "series,designation,family,area,iy,iz,torsion_constant,linear_mass"
    ]


def test_csv_quotes_texts_that_need_it_and_keeps_unicode() -> None:
    text = csv_text([profile('L 50,5 "x" Ñ')])
    assert rows_of(text)[1][1] == 'L 50,5 "x" Ñ'


# --- JSON ----------------------------------------------------------------------------------


def test_json_export_has_the_catalog_the_warning_and_the_full_profile_documents() -> None:
    profiles = [profile("A"), profile("B", n=2)]
    document = json.loads(json_text(INFO, profiles, exported_at=STAMP))
    assert document["format"] == EXPORT_FORMAT
    assert document["exported_at"] == STAMP
    assert "CC BY 4.0" in document["warning"]
    assert document["catalog"] == {
        "id": str(INFO.id),
        "name": "Cat",
        "description": "",
        "source": "Fuente",
        "license": "CC BY 4.0",
        "edition": "2026",
        "protected": True,
        "cloned_from": None,
    }
    assert [p["data"]["designation"] for p in document["profiles"]] == ["A", "B"]
    first = document["profiles"][0]
    assert first["sha256"] == profile_sha256(profiles[0])
    assert first["revision"] == 1
    area = first["data"]["properties"]["area"]
    assert (area["value"], area["unit"], area["status"]) == (
        1.0e-3,
        "m²",
        "IMPORTADO_SIN_VERIFICAR",
    )
    assert first["data"]["properties"]["torsion_constant"]["value"] is None
    assert first["data"]["provenance"]["license"] == PROVENANCE.license


def test_json_export_names_the_clone_origin_and_is_deterministic() -> None:
    origin = CatalogInfo(
        id=uid(901),
        name="Madre",
        description="",
        source="s",
        license="l",
        edition="e",
        protected=True,
    )
    text = json_text(INFO, [profile("A")], exported_at=STAMP, cloned_from=origin)
    assert json.loads(text)["catalog"]["cloned_from"] == "Madre"
    assert text == json_text(INFO, [profile("A")], exported_at=STAMP, cloned_from=origin)
    assert text.endswith("\n")


# --- Procedencia ---------------------------------------------------------------------------


def test_provenance_describes_the_csv_with_its_hash_and_the_warning() -> None:
    content = csv_text([profile("A"), profile("B", n=2)])
    note = json.loads(
        provenance_text(INFO, file_name="e.csv", content=content, rows=2, exported_at=STAMP)
    )
    assert note["format"] == PROVENANCE_FORMAT
    assert note["file"] == {"name": "e.csv", "sha256": sha256_of(content), "rows": 2}
    assert note["warning"] == redistribution_warning("CC BY 4.0")
    assert note["catalog"]["license"] == "CC BY 4.0"
    assert note["catalog"]["source"] == "Fuente"


def test_the_warning_names_the_license_and_does_not_claim_the_data_is_verified() -> None:
    text = redistribution_warning("MIT")
    assert "«MIT»" in text
    assert "IMPORTADO_SIN_VERIFICAR" in text
    assert "redistribuirlos" in text


# --- Escritura -----------------------------------------------------------------------------


def test_files_are_written_together_and_overwrite_needs_permission(tmp_path: Path) -> None:
    a, b = tmp_path / "a.csv", tmp_path / "a.csv.procedencia.json"
    write_files([(a, b"uno"), (b, b"dos")], overwrite=False)
    assert (a.read_bytes(), b.read_bytes()) == (b"uno", b"dos")
    with pytest.raises(CatalogFileError) as error:
        write_files([(a, b"nuevo"), (b, b"nuevo")], overwrite=False)
    assert error.value.diagnostic.message_key == "catalog.output_exists"
    assert_renders(error.value.diagnostic)
    assert (a.read_bytes(), b.read_bytes()) == (b"uno", b"dos")
    write_files([(a, b"nuevo"), (b, b"nuevo")], overwrite=True)
    assert (a.read_bytes(), b.read_bytes()) == (b"nuevo", b"nuevo")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.csv", "a.csv.procedencia.json"]


def test_if_only_the_second_target_exists_nothing_is_written(tmp_path: Path) -> None:
    a, b = tmp_path / "a.csv", tmp_path / "a.csv.procedencia.json"
    b.write_bytes(b"previo")
    with pytest.raises(CatalogFileError):
        write_files([(a, b"uno"), (b, b"dos")], overwrite=False)
    assert not a.exists()
    assert b.read_bytes() == b"previo"


def test_a_missing_directory_is_a_file_error_and_leaves_no_temporary(tmp_path: Path) -> None:
    target = tmp_path / "no_existe" / "a.csv"
    with pytest.raises(CatalogFileError) as error:
        write_files([(target, b"x")], overwrite=False)
    assert error.value.diagnostic.message_key == "catalog.output_error"
    assert_renders(error.value.diagnostic)
    assert list(tmp_path.iterdir()) == []
