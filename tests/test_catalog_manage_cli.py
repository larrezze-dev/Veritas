"""``veritas catalog clone|favorito|exportar`` (T-023 E2) sobre las 100 filas sintéticas."""

import csv
import json
import re
import shutil
from pathlib import Path

import pytest

from catalog_cli_golden import DATA_DIR, MAPPING, SYNTHETIC_ARGS
from veritas.catalogs.model import profile_sha256, sha256_of
from veritas.catalogs.store import CatalogStore
from veritas.cli import main

DB = "c.sqlite"
USER = "u.json"
DIAGNOSTIC = re.compile(r"^[A-Z_]+: .+")
FIRST = "SINTETICO-I/SINT-100x50-4-6"
SECOND = "SINTETICO-I/SINT-100x55-5-6"
THIRD = "SINTETICO-I/SINT-100x60-6-6"


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Directorio con el catálogo «Sint» (100 filas) ya importado y protegido."""
    monkeypatch.chdir(tmp_path)
    for name in ("catalog_synthetic_100.csv", MAPPING):
        shutil.copy(DATA_DIR / name, tmp_path / name)
    assert main(["catalog", "create", "--db", DB, "--nombre", "Sint", "--fuente", "SINTÉTICO",
                 "--licencia", "CC BY 4.0", "--edicion", "2026"]) == 0  # fmt: skip
    assert main(["catalog", "import", "--db", DB, "--catalogo", "Sint",
                 "catalog_synthetic_100.csv", *SYNTHETIC_ARGS]) == 0  # fmt: skip
    assert main(["catalog", "lock", "--db", DB, "--catalogo", "Sint"]) == 0
    return tmp_path


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    capsys.readouterr()
    code = main(["catalog", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def profiles_of(name: str, db: str = DB) -> list[tuple[object, ...]]:
    with CatalogStore.open(db) as store:
        info = store.catalog_by_name(name)
        return [
            (
                p.series,
                p.designation,
                p.family,
                p.properties.area.value,
                p.properties.iy.value,
                p.properties.iz.value,
                p.properties.torsion_constant.value,
                p.properties.linear_mass.value,
                tuple((d.name, d.value) for d in p.data.dimensions),
            )
            for p in store.list_profiles(info.id)
        ]


# --- clone ---------------------------------------------------------------------------------


def test_clone_of_a_protected_catalog_is_editable_traceable_and_leaves_the_origin_intact(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    origin_before = profiles_of("Sint")
    code, out, err = run(capsys, "clone", "--db", DB, "--catalogo", "Sint", "--nombre", "Copia")
    assert (code, err) == (0, "")
    assert "clonado como «Copia» (editable): 100 perfil(es) copiados como revisión 1" in out
    assert "origen: «Sint» (protegido)" in out
    assert profiles_of("Sint") == origin_before
    assert profiles_of("Copia") == origin_before  # mismos valores, propiedad a propiedad
    listing = run(capsys, "list", "--db", DB)[1]
    assert re.search(r"^Copia\s+100\s+no\s", listing, flags=re.MULTILINE)
    assert re.search(r"^Sint\s+100\s+sí\s", listing, flags=re.MULTILINE)
    assert "esquema v4" in run(capsys, "verify", "--db", DB)[1]


def test_show_and_ficha_state_that_a_revision_was_copied_by_a_clone(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "clone", "--db", DB, "--catalogo", "Sint", "--nombre", "Copia")
    shown = run(capsys, "show", "--db", DB, "--catalogo", "Copia", *FIRST.split("/"))[1]
    sheet = run(capsys, "ficha", "--db", DB, f"Copia/{FIRST}")[1]
    for text in (shown, sheet):
        assert "origen de la revisión: clon de «Sint» (SINTETICO-I / SINT-100x50-4-6 rev 1)" in text
        assert "anterior al registro de lotes" not in text
        assert text.count("copiada por clon") == (1 if text is sheet else 0)
        assert "lote que creó la revisión" not in text
    original = run(capsys, "ficha", "--db", DB, f"Sint/{FIRST}")[1]
    assert "lote que creó la revisión:" in original  # el origen sigue citando su lote
    assert "origen de la revisión" not in original


def test_the_clone_accepts_the_same_file_again_as_unchanged_and_the_origin_does_not(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "clone", "--db", DB, "--catalogo", "Sint", "--nombre", "Copia")
    args = ["import", "--db", DB, "--catalogo", "{}", "catalog_synthetic_100.csv", *SYNTHETIC_ARGS]
    code, out, _ = run(capsys, *[a.format("Copia") for a in args])
    assert code == 0
    assert "creados: 0 · revisados: 0 · sin cambios: 100" in out
    code, _, err = run(capsys, *[a.format("Sint") for a in args])
    assert code == 2  # el origen sigue protegido
    assert "protegido" in err
    assert run(capsys, "verify", "--db", DB)[0] == 0


def test_clone_errors_exit_2_and_leave_the_file_byte_identical(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (work / DB).read_bytes()
    for argv, fragment in (
        (["--catalogo", "Sint", "--nombre", "sint"], "ya existe un catálogo"),
        (["--catalogo", "Inexistente", "--nombre", "X"], "No existe el catálogo"),
    ):
        code, out, err = run(capsys, "clone", "--db", DB, *argv)
        assert (code, out) == (2, "")
        assert DIAGNOSTIC.match(err), err
        assert "{" not in err
        assert fragment.casefold() in err.casefold(), err
    assert (work / DB).read_bytes() == before


# --- favoritos y recientes -----------------------------------------------------------------


def test_favorites_work_on_a_protected_catalog_without_touching_its_bytes(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (work / DB).read_bytes()
    code, out, err = run(
        capsys, "favorito", "agregar", "--db", DB, "--usuario", USER, f"Sint/{FIRST}"
    )
    assert (code, err) == (0, "")
    assert "añadido a los favoritos" in out
    run(capsys, "favorito", "agregar", "--db", DB, "--usuario", USER, f"Sint/{SECOND}@1")
    code, out, _ = run(capsys, "favorito", "listar", "--db", DB, "--usuario", USER)
    assert code == 0
    assert out.index("SINT-100x50-4-6") < out.index("SINT-100x55-5-6")  # en orden de alta
    assert "2 de este archivo" in out
    code, out, _ = run(capsys, "favorito", "quitar", "--db", DB, "--usuario", USER, f"Sint/{FIRST}")
    assert "quitado de los favoritos" in out
    assert (
        "SINT-100x50-4-6" not in run(capsys, "favorito", "listar", "--db", DB, "--usuario", USER)[1]
    )
    assert (work / DB).read_bytes() == before


def test_adding_twice_or_removing_an_absent_favorite_changes_nothing(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    args = ("favorito", "agregar", "--db", DB, "--usuario", USER, f"Sint/{FIRST}")
    run(capsys, *args)
    saved = (work / USER).read_bytes()
    out = run(capsys, *args)[1]
    assert "ya estaba en los favoritos" in out
    out = run(capsys, "favorito", "quitar", "--db", DB, "--usuario", USER, f"Sint/{SECOND}")[1]
    assert "no estaba en los favoritos" in out
    assert (work / USER).read_bytes() == saved


def test_ficha_and_comparar_record_recents_only_when_a_user_file_is_given(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "ficha", "--db", DB, f"Sint/{FIRST}")
    assert not (work / USER).exists()  # sin --usuario no hay estado oculto
    run(capsys, "ficha", "--db", DB, f"Sint/{FIRST}", "--usuario", USER)
    run(capsys, "ficha", "--db", DB, f"Sint/{SECOND}", "--usuario", USER)
    run(capsys, "ficha", "--db", DB, f"Sint/{FIRST}", "--usuario", USER)  # vuelve a ser el último
    out = run(capsys, "favorito", "recientes", "--db", DB, "--usuario", USER)[1]
    assert out.index("SINT-100x50-4-6") < out.index("SINT-100x55-5-6")
    assert out.count("SINT-") == 2  # sin repetidos
    run(capsys, "comparar", "--db", DB, f"Sint/{SECOND}", f"Sint/{THIRD}", "--usuario", USER)
    out = run(capsys, "favorito", "recientes", "--db", DB, "--usuario", USER)[1]
    order = [out.index(name) for name in ("SINT-100x55-5-6", "SINT-100x60-6-6", "SINT-100x50-4-6")]
    assert order == sorted(order)  # P1 del último comparar primero; después P2; después el anterior
    assert json.loads((work / USER).read_text(encoding="utf-8"))["favorites"] == []


def test_a_failed_command_does_not_record_recents(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, _, _ = run(capsys, "ficha", "--db", DB, "Sint/SINTETICO-I/NO-EXISTE", "--usuario", USER)
    assert code == 2
    assert not (work / USER).exists()
    code, _, _ = run(
        capsys, "comparar", "--db", DB, f"Sint/{FIRST}", f"Sint/{FIRST}", "--usuario", USER
    )
    assert code == 2
    assert not (work / USER).exists()


def test_a_corrupt_user_file_stops_the_command_before_doing_anything_and_is_not_modified(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = b'{"format": "veritas-catalog-user/1", "favorites": 3}'
    (work / USER).write_bytes(bad)
    before = (work / DB).read_bytes()
    for argv in (
        ("ficha", "--db", DB, f"Sint/{FIRST}", "--usuario", USER),
        ("comparar", "--db", DB, f"Sint/{FIRST}", f"Sint/{SECOND}", "--usuario", USER),
        ("favorito", "agregar", "--db", DB, "--usuario", USER, f"Sint/{FIRST}"),
        ("favorito", "listar", "--db", DB, "--usuario", USER),
    ):
        code, out, err = run(capsys, *argv)
        assert (code, out) == (2, ""), argv
        assert DIAGNOSTIC.match(err), err
        assert "no se ha modificado" in err
        assert (work / USER).read_bytes() == bad
    assert (work / DB).read_bytes() == before
    code, out, _ = run(
        capsys, "ficha", "--db", DB, f"Sint/{FIRST}"
    )  # sin --usuario sigue sirviendo
    assert code == 0
    assert "Perfil SINTETICO-I / SINT-100x50-4-6" in out


def test_favorites_of_another_catalog_file_are_counted_but_not_shown(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "favorito", "agregar", "--db", DB, "--usuario", USER, f"Sint/{FIRST}")
    assert main(["catalog", "create", "--db", "otro.sqlite", "--nombre", "Sint", "--fuente", "S",
                 "--licencia", "L", "--edicion", "1"]) == 0  # fmt: skip
    capsys.readouterr()
    out = run(capsys, "favorito", "listar", "--db", "otro.sqlite", "--usuario", USER)[1]
    assert "0 de este archivo" in out
    assert "Otros: 1 perfil(es) de catálogos de otros archivos" in out  # mismo nombre, otro id
    assert "SINT-100x50-4-6" not in out


# --- exportar ------------------------------------------------------------------------------


def test_csv_export_reimports_with_identical_values_and_ships_license_and_hash(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (work / DB).read_bytes()
    code, out, err = run(capsys, "exportar", "--db", DB, "--catalogo", "Sint",
                         "--formato", "csv", "--salida", "e.csv")  # fmt: skip
    assert (code, err) == (0, "")
    assert "CSV, 100 perfil(es)" in out
    assert "CC BY 4.0" in out
    assert "Aviso: Los datos de este catálogo se rigen por su licencia" in out
    assert (work / DB).read_bytes() == before  # exportar un catálogo protegido no lo toca
    note = json.loads((work / "e.csv.procedencia.json").read_text(encoding="utf-8"))
    assert note["file"] == {
        "name": "e.csv",
        "sha256": sha256_of((work / "e.csv").read_text(encoding="utf-8")),
        "rows": 100,
    }
    assert note["catalog"]["license"] == "CC BY 4.0"
    assert "redistribuirlos" in note["warning"]

    assert main(["catalog", "create", "--db", DB, "--nombre", "Re", "--fuente", "S",
                 "--licencia", "L", "--edicion", "1"]) == 0  # fmt: skip
    code, out, _ = run(capsys, "import", "--db", DB, "--catalogo", "Re", "e.csv")  # sin mapeo
    assert code == 0
    assert "aceptadas: 100 · rechazadas: 0" in out
    assert profiles_of("Re") == profiles_of("Sint")  # valores SI idénticos, incluidos J ausente


def test_csv_export_is_deterministic_apart_from_the_sidecar_date(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(
        capsys,
        "exportar",
        "--db",
        DB,
        "--catalogo",
        "Sint",
        "--formato",
        "csv",
        "--salida",
        "a.csv",
    )
    run(
        capsys,
        "exportar",
        "--db",
        DB,
        "--catalogo",
        "Sint",
        "--formato",
        "csv",
        "--salida",
        "b.csv",
    )
    assert (work / "a.csv").read_bytes() == (work / "b.csv").read_bytes()
    with (work / "a.csv").open(encoding="utf-8", newline="") as handle:
        assert len(list(csv.reader(handle))) == 101


def test_json_export_keeps_license_status_method_and_the_revision_hash(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "exportar", "--db", DB, "--catalogo", "Sint",
                       "--formato", "json", "--salida", "e.json")  # fmt: skip
    assert code == 0
    assert "procedencia y licencia" not in out  # el JSON ya lo lleva dentro
    assert not (work / "e.json.procedencia.json").exists()
    document = json.loads((work / "e.json").read_text(encoding="utf-8"))
    assert document["format"] == "veritas-catalog-export/1"
    assert document["catalog"]["license"] == "CC BY 4.0"
    assert document["catalog"]["protected"] is True
    assert len(document["profiles"]) == 100
    with CatalogStore.open(DB) as store:
        by_name = {p.designation: p for p in store.list_profiles(store.catalog_by_name("Sint").id)}
    for exported in document["profiles"]:
        stored = by_name[exported["data"]["designation"]]
        assert exported["sha256"] == profile_sha256(stored)
        assert exported["id"] == str(stored.id)
        props = exported["data"]["properties"]
        assert props["area"]["status"] == "IMPORTADO_SIN_VERIFICAR"
        assert props["area"]["method"].startswith("Importado de la columna")
        assert props["torsion_constant"]["value"] is None
        assert props["torsion_constant"]["status"] == "NO_DISPONIBLE"


def test_a_clone_export_names_its_origin(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    run(capsys, "clone", "--db", DB, "--catalogo", "Sint", "--nombre", "Copia")
    run(
        capsys,
        "exportar",
        "--db",
        DB,
        "--catalogo",
        "Copia",
        "--formato",
        "json",
        "--salida",
        "c.json",
    )
    document = json.loads((work / "c.json").read_text(encoding="utf-8"))
    assert document["catalog"]["cloned_from"] == "Sint"
    assert document["catalog"]["protected"] is False


def test_export_does_not_overwrite_without_force_and_writes_nothing_then(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ("exportar", "--db", DB, "--catalogo", "Sint", "--formato", "csv", "--salida", "e.csv")
    assert run(capsys, *argv)[0] == 0
    first = ((work / "e.csv").read_bytes(), (work / "e.csv.procedencia.json").read_bytes())
    code, out, err = run(capsys, *argv)
    assert (code, out) == (2, "")
    assert DIAGNOSTIC.match(err)
    assert "--forzar" in err
    assert ((work / "e.csv").read_bytes(), (work / "e.csv.procedencia.json").read_bytes()) == first
    (work / "e.csv").unlink()  # solo existe el acompañante: tampoco se escribe el CSV
    assert run(capsys, *argv)[0] == 2
    assert not (work / "e.csv").exists()
    assert run(capsys, *argv, "--forzar")[0] == 0
    assert (work / "e.csv").read_bytes() == first[0]


def test_export_to_a_missing_directory_fails_cleanly(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = sorted(p.name for p in work.iterdir())
    code, out, err = run(capsys, "exportar", "--db", DB, "--catalogo", "Sint", "--formato", "json",
                         "--salida", "no_existe/e.json")  # fmt: skip
    assert (code, out) == (2, "")
    assert DIAGNOSTIC.match(err)
    assert sorted(p.name for p in work.iterdir()) == before


def test_exporting_an_empty_catalog_gives_a_header_only_csv(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "create", "--db", DB, "--nombre", "Vacio", "--fuente", "S", "--licencia", "L",
        "--edicion", "1")  # fmt: skip
    code, out, _ = run(capsys, "exportar", "--db", DB, "--catalogo", "Vacio", "--formato", "csv",
                       "--salida", "v.csv")  # fmt: skip
    assert code == 0
    assert "0 perfil(es)" in out
    assert len((work / "v.csv").read_text(encoding="utf-8").splitlines()) == 1
