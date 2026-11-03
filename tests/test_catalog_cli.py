"""``veritas catalog`` (T-022 E2): flujo, códigos de salida y archivo intacto en los fallos."""

import json
import re
import shutil
import sqlite3
from pathlib import Path

import pytest

from catalog_cli_golden import DATA_DIR, MAPPING, SYNTHETIC_ARGS
from test_catalog_batches import v1_file
from veritas.catalogs.store import CatalogStore
from veritas.cli import main

DB = "c.sqlite"
DIAGNOSTIC = re.compile(r"^[A-Z_]+: .+")


@pytest.fixture
def work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    for name in ("catalog_synthetic_100.csv", "catalog_synthetic_bad.csv", MAPPING):
        shutil.copy(DATA_DIR / name, tmp_path / name)
    return tmp_path


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(["catalog", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def create(capsys: pytest.CaptureFixture[str], name: str = "Sint", db: str = DB) -> None:
    code, _, err = run(
        capsys,
        "create", "--db", db, "--nombre", name, "--fuente", "SINTÉTICO",
        "--licencia", "Sin valor comercial", "--edicion", "2026",
    )  # fmt: skip
    assert (code, err) == (0, "")


def import_args(file: str = "catalog_synthetic_100.csv", name: str = "Sint") -> list[str]:
    return ["import", "--db", DB, "--catalogo", name, file, *SYNTHETIC_ARGS]


def listing(directory: Path) -> list[str]:
    return sorted(p.name for p in directory.iterdir())


def assert_untouched(work: Path, before: bytes, names: list[str]) -> None:
    assert (work / DB).read_bytes() == before
    assert listing(work) == names  # sin archivos auxiliares ni diarios


def snapshot(work: Path) -> tuple[bytes, list[str]]:
    return (work / DB).read_bytes(), listing(work)


def assert_diagnostic(err: str) -> None:
    assert DIAGNOSTIC.match(err.strip()), err
    assert "{" not in err  # ninguna plantilla sin rellenar


# --- create / lock -----------------------------------------------------------------------------


def test_create_makes_the_file_and_a_second_catalog_can_join_it(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create(capsys, "Uno")
    assert (work / DB).is_file()
    create(capsys, "Dos")
    code, out, _ = run(capsys, "list", "--db", DB)
    assert code == 0
    assert "Uno" in out
    assert "Dos" in out


def test_create_rejects_a_taken_name_without_changing_the_file(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create(capsys, "Uno")
    before = snapshot(work)
    code, out, err = run(
        capsys,
        "create",
        "--db",
        DB,
        "--nombre",
        "UNO",
        "--fuente",
        "F",
        "--licencia",
        "L",
        "--edicion",
        "E",
    )
    assert (code, out) == (2, "")
    assert_diagnostic(err)
    assert_untouched(work, *before)


def test_a_failed_create_does_not_leave_a_new_file(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run(
        capsys,
        "create",
        "--db",
        "nuevo.sqlite",
        "--nombre",
        " ",
        "--fuente",
        "F",
        "--licencia",
        "L",
        "--edicion",
        "E",
    )
    assert (code, out) == (2, "")
    assert_diagnostic(err)
    assert listing(work) == sorted(p.name for p in DATA_DIR.glob("*") if p.name in {
        "catalog_synthetic_100.csv", "catalog_synthetic_bad.csv", MAPPING})  # fmt: skip


def test_create_refuses_a_foreign_sqlite_file_without_writing(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    connection = sqlite3.connect(work / "otro.sqlite")
    connection.execute("CREATE TABLE x (a)")
    connection.close()
    before = (work / "otro.sqlite").read_bytes()
    code, out, err = run(
        capsys,
        "create",
        "--db",
        "otro.sqlite",
        "--nombre",
        "N",
        "--fuente",
        "F",
        "--licencia",
        "L",
        "--edicion",
        "E",
    )
    assert (code, out) == (2, "")
    assert_diagnostic(err)
    assert (work / "otro.sqlite").read_bytes() == before


def test_lock_is_idempotent_and_blocks_imports_and_simulations(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create(capsys)
    assert run(capsys, "lock", "--db", DB, "--catalogo", "sint")[0] == 0
    assert run(capsys, "lock", "--db", DB, "--catalogo", "Sint")[0] == 0
    before = snapshot(work)
    for extra in ([], ["--simular"]):
        code, out, err = run(capsys, *import_args(), *extra)
        assert (code, out) == (2, "")
        assert "protegido" in err
        assert_diagnostic(err)
    assert_untouched(work, *before)
    assert "sí" in run(capsys, "list", "--db", DB)[1]


# --- import: flujo -------------------------------------------------------------------------------


def test_simulation_changes_nothing_and_predicts_the_real_import(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create(capsys)
    before = snapshot(work)
    code, simulated, err = run(capsys, *import_args(), "--simular")
    assert (code, err) == (0, "")
    assert "SIMULACIÓN" in simulated
    assert "creados: 100 · revisados: 0 · sin cambios: 0" in simulated
    assert_untouched(work, *before)
    code, written, _ = run(capsys, *import_args())
    assert code == 0
    assert "creados: 100 · revisados: 0 · sin cambios: 0" in written
    before = snapshot(work)
    assert (
        "creados: 0 · revisados: 0 · sin cambios: 100"
        in run(capsys, *import_args(), "--simular")[1]
    )
    assert_untouched(work, *before)


def test_reimport_is_idempotent_and_a_changed_value_revises_only_its_row(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create(capsys)
    assert run(capsys, *import_args())[0] == 0
    path = work / "catalog_synthetic_100.csv"
    text = path.read_text(encoding="utf-8-sig")
    changed = text.replace(
        "SINT-100x50-4-6;I;100;50;4;6;9,52;", "SINT-100x50-4-6;I;100;50;4;6;9,60;"
    )
    assert changed != text
    path.write_text(changed, encoding="utf-8-sig", newline="")
    code, out, _ = run(capsys, *import_args(), "--simular")
    assert "creados: 0 · revisados: 1 · sin cambios: 99" in out
    code, out, _ = run(capsys, *import_args())
    assert (code, "creados: 0 · revisados: 1 · sin cambios: 99" in out) == (0, True)
    shown = run(capsys, "show", "--db", DB, "--catalogo", "Sint", "SINTETICO-I", "SINT-100x50-4-6")[
        1
    ]
    assert "revisión 2 de 2" in shown
    assert "A = 0.00096 m²" in shown
    old = run(
        capsys,
        "show",
        "--db",
        DB,
        "--catalogo",
        "Sint",
        "SINTETICO-I",
        "SINT-100x50-4-6",
        "--revision",
        "1",
    )[1]
    assert "revisión 1 de 2" in old
    assert "A = 0.000952 m²" in old
    other = run(capsys, "show", "--db", DB, "--catalogo", "Sint", "SINTETICO-I", "SINT-100x55-5-6")[
        1
    ]
    assert "revisión 1 de 1" in other  # las demás filas no se revisaron
    assert run(capsys, "verify", "--db", DB)[0] == 0


def test_strict_rejects_the_whole_batch_and_reports_every_row_on_stderr(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create(capsys)
    both = work / "ambos.csv"
    bad = (work / "catalog_synthetic_bad.csv").read_text(encoding="utf-8-sig").splitlines()[1:]
    good = (work / "catalog_synthetic_100.csv").read_text(encoding="utf-8-sig").splitlines()
    both.write_text("\n".join([*good, *bad]) + "\n", encoding="utf-8-sig")
    before = snapshot(work)
    code, out, err = run(capsys, *import_args("ambos.csv"), "--estricto")
    assert (code, out) == (2, "")
    assert "RECHAZADO ENTERO" in err
    assert len(re.findall(r"^  línea \d+", err, flags=re.MULTILINE)) == 5
    assert_untouched(work, *before)
    assert run(capsys, "list", "--db", DB, "--catalogo", "Sint")[1].strip().endswith("sin perfiles")
    # El mismo archivo sin --estricto escribe las 100 válidas.
    code, out, err = run(capsys, *import_args("ambos.csv"))
    assert (code, err) == (0, "")
    assert "aceptadas: 100 · rechazadas: 5" in out


def test_strict_with_a_clean_file_writes_normally(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create(capsys)
    code, out, err = run(capsys, *import_args(), "--estricto")
    assert (code, err) == (0, "")
    assert "creados: 100" in out


def test_a_file_with_no_valid_row_writes_nothing_but_exits_zero(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create(capsys)
    before = snapshot(work)
    code, out, err = run(capsys, *import_args("catalog_synthetic_bad.csv"))
    assert (code, err) == (0, "")
    assert "Ninguna fila aceptada" in out
    assert "aceptadas: 0 · rechazadas: 5" in out
    assert_untouched(work, *before)


def test_native_json_import_and_warnings(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    create(capsys)
    records = [
        {"series": "N", "designation": "A", "family": "I", "area": 3e-3, "iy": 1e-6, "iz": 2e-6},
        {"series": "N", "designation": "B", "family": "RHS", "area": 3e-3, "iy": 1e-6, "iz": 2e-6},
    ]
    (work / "nativo.json").write_text(json.dumps(records), encoding="utf-8")
    code, out, err = run(capsys, "import", "--db", DB, "--catalogo", "Sint", "nativo.json")
    assert (code, err) == (0, "")
    assert "con aviso: 1" in out
    assert "N / A" in out
    assert "formato nativo (SI)" in out
    shown = run(capsys, "show", "--db", DB, "--catalogo", "Sint", "N", "A")[1]
    assert "Iy < Iz" in shown
    assert "J = no disponible · NO_DISPONIBLE" in shown


def test_batches_are_listed_with_their_hashes(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create(capsys)
    run(capsys, *import_args())
    run(capsys, *import_args())
    code, out, _ = run(capsys, "list", "--db", DB, "--catalogo", "Sint", "--lotes")
    assert code == 0
    assert "2 lote(s)" in out
    assert "100/0/0" in out
    assert "0/0/100" in out
    assert run(capsys, "list", "--db", DB, "--catalogo", "Vacio", "--lotes")[0] == 2


# --- import: fallos ---------------------------------------------------------------------------


IMPORT = ["import", "--db", DB, "--catalogo", "Sint", "catalog_synthetic_100.csv"]


@pytest.mark.parametrize(
    "argv",
    [
        [
            "import",
            "--db",
            DB,
            "--catalogo",
            "NoExiste",
            "catalog_synthetic_100.csv",
            *SYNTHETIC_ARGS,
        ],
        [*IMPORT, "--mapeo", "no_existe.json", "--sep", ";", "--decimal", ","],
        [*IMPORT, "--mapeo", MAPPING, "--sep", ";", "--decimal", "x"],
        [*IMPORT, "--mapeo", MAPPING, "--sep", "	", "--decimal", "."],
        [*IMPORT, "--mapeo", MAPPING, "--sep", ",", "--decimal", ","],  # coma decimal con «,»
    ],
)
def test_import_failures_exit_two_and_leave_the_file_identical(
    work: Path, capsys: pytest.CaptureFixture[str], argv: list[str]
) -> None:
    create(capsys)
    before = snapshot(work)
    code, out, err = run(capsys, *argv)
    assert (code, out) == (2, "")
    assert_diagnostic(err)
    assert_untouched(work, *before)


def test_file_level_import_failures(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    create(capsys)
    (work / "roto.json").write_text("{", encoding="utf-8")
    (work / "vacio.csv").write_text("", encoding="utf-8")
    (work / "mapa_roto.json").write_text('{"format": "x"}', encoding="utf-8")
    (work / "datos.txt").write_text("a", encoding="utf-8")
    before = snapshot(work)
    for argv in (
        ["import", "--db", DB, "--catalogo", "Sint", "no_existe.csv"],
        ["import", "--db", DB, "--catalogo", "Sint", "roto.json"],
        ["import", "--db", DB, "--catalogo", "Sint", "vacio.csv"],
        ["import", "--db", DB, "--catalogo", "Sint", "datos.txt"],
        [
            "import",
            "--db",
            DB,
            "--catalogo",
            "Sint",
            "catalog_synthetic_100.csv",
            "--mapeo",
            "mapa_roto.json",
        ],
        [
            "import",
            "--db",
            DB,
            "--catalogo",
            "Sint",
            "catalog_synthetic_100.csv",
        ],  # nativo con columnas ajenas
    ):
        code, out, err = run(capsys, *argv)
        assert (code, out) == (2, ""), argv
        assert_diagnostic(err)
    assert_untouched(work, *before)


def test_a_missing_database_is_an_error_and_is_not_created(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for argv in (["list"], ["verify"], ["lock", "--catalogo", "X"]):
        code, out, err = run(capsys, *argv, "--db", "no_hay.sqlite")
        assert (code, out) == (2, "")
        assert_diagnostic(err)
    assert not (work / "no_hay.sqlite").exists()


# --- list / show / verify ------------------------------------------------------------------------


def test_list_of_an_empty_file_and_option_rules(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    CatalogStore.create(work / DB).close()
    assert "sin catálogos" in run(capsys, "list", "--db", DB)[1]
    code, out, err = run(capsys, "list", "--db", DB, "--lotes")
    assert (code, out) == (2, "")
    assert_diagnostic(err)


def test_show_errors_exit_two(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    create(capsys)
    run(capsys, *import_args())
    for argv in (
        ["show", "--db", DB, "--catalogo", "Sint", "SINTETICO-I", "NO-EXISTE"],
        [
            "show",
            "--db",
            DB,
            "--catalogo",
            "Sint",
            "SINTETICO-I",
            "SINT-100x50-4-6",
            "--revision",
            "9",
        ],
        ["show", "--db", DB, "--catalogo", "Otro", "SINTETICO-I", "SINT-100x50-4-6"],
    ):
        code, out, err = run(capsys, *argv)
        assert (code, out) == (2, "")
        assert_diagnostic(err)


def test_read_only_commands_leave_the_file_identical(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    create(capsys)
    run(capsys, *import_args())
    before = snapshot(work)
    for argv in (
        ["list", "--db", DB],
        ["list", "--db", DB, "--catalogo", "Sint"],
        ["list", "--db", DB, "--catalogo", "Sint", "--lotes"],
        ["show", "--db", DB, "--catalogo", "Sint", "SINTETICO-I", "SINT-100x50-4-6"],
        ["verify", "--db", DB],
        [*import_args(), "--simular"],
    ):
        assert run(capsys, *argv)[0] == 0
    assert_untouched(work, *before)


def test_verify_exit_codes(work: Path, capsys: pytest.CaptureFixture[str]) -> None:
    create(capsys)
    run(capsys, *import_args())
    code, out, err = run(capsys, "verify", "--db", DB)
    assert (code, err) == (0, "")
    assert "100 revisión(es)" in out
    assert "1 lote(s)" in out
    connection = sqlite3.connect(work / DB)
    connection.execute(
        "UPDATE profile_revisions SET content_sha256 = 'x' WHERE designation LIKE 'SINT-100x5%'"
    )
    connection.commit()
    connection.close()
    before = snapshot(work)
    code, out, err = run(capsys, "verify", "--db", DB)
    assert (code, out) == (3, "")
    assert "CON PROBLEMAS" in err
    assert err.count("[perfil ") == 2  # SINT-100x50 y SINT-100x55
    assert_untouched(work, *before)


def test_verify_of_a_foreign_file_is_a_file_error_not_a_verify_failure(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (work / DB).write_bytes(b"no soy sqlite " * 20)
    code, out, err = run(capsys, "verify", "--db", DB)
    assert (code, out) == (2, "")
    assert_diagnostic(err)


def test_opening_a_v1_catalog_migrates_it_and_it_verifies(
    work: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    v1_file(work / "viejo.sqlite")
    code, out, err = run(capsys, "verify", "--db", "viejo.sqlite")
    assert (code, err) == (0, "")
    assert "esquema v2" in out
    assert "0 lote(s)" in out
    shown = run(capsys, "show", "--db", "viejo.sqlite", "--catalogo", "Antiguo", "SERIE-A", "OLD")[
        1
    ]
    assert "anterior al registro de lotes" in shown


def test_usage_errors_are_argparse_exit_two(capsys: pytest.CaptureFixture[str]) -> None:
    for argv in ([], ["import"], ["frobnicate"], ["list"]):
        with pytest.raises(SystemExit) as stop:
            main(["catalog", *argv])
        assert stop.value.code == 2
    capsys.readouterr()
