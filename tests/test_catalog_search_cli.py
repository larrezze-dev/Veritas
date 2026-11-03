"""``veritas catalog search|ficha|comparar`` (T-023 E1) sobre las 100 filas sintéticas.

Los resultados esperados se calculan aquí con ``Decimal`` a partir de las celdas del CSV de
origen (no con el código de búsqueda): los límites de cada rango se toman de valores reales del
archivo para probar la frontera inclusiva con los datos importados.
"""

import csv
import re
import shutil
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path

import pytest

from catalog_cli_golden import DATA_DIR, MAPPING, SYNTHETIC_ARGS
from veritas.catalogs.store import CatalogStore
from veritas.cli import main

DB = "c.sqlite"
DIAGNOSTIC = re.compile(r"^[A-Z_]+: .+")
Row = dict[str, str]
ROW_NAME = re.compile(r"^Sint\s+\S+\s+(\S+)", flags=re.MULTILINE)


@pytest.fixture
def imported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    for name in ("catalog_synthetic_100.csv", "catalog_synthetic_bad.csv", MAPPING):
        shutil.copy(DATA_DIR / name, tmp_path / name)
    good = (tmp_path / "catalog_synthetic_100.csv").read_text(encoding="utf-8-sig").splitlines()
    bad = (tmp_path / "catalog_synthetic_bad.csv").read_text(encoding="utf-8-sig").splitlines()
    (tmp_path / "lote105.csv").write_text("\n".join([*good, *bad[1:]]) + "\n", encoding="utf-8-sig")
    assert main(["catalog", "create", "--db", DB, "--nombre", "Sint", "--fuente", "SINTÉTICO",
                 "--licencia", "Sin valor comercial", "--edicion", "2026"]) == 0  # fmt: skip
    assert main(["catalog", "import", "--db", DB, "--catalogo", "Sint", "lote105.csv",
                 *SYNTHETIC_ARGS]) == 0  # fmt: skip
    return tmp_path


def rows() -> list[Row]:
    with (DATA_DIR / "catalog_synthetic_100.csv").open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle, delimiter=";"))


def dec(row: Row, column: str) -> Decimal:
    return Decimal(row[column].replace(",", "."))


def search(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(["catalog", "search", "--db", DB, *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def found(out: str) -> list[str]:
    return ROW_NAME.findall(out)


def expected_names(
    predicate: Callable[[Row], bool],
    key: Callable[[Row], Decimal] | None = None,
    *,
    reverse: bool = False,
) -> list[str]:
    """Designaciones esperadas: orden natural y, si hay ``key``, estable por ese valor."""
    chosen = sorted((r for r in rows() if predicate(r)), key=lambda r: r["Designación"])
    if key is not None:
        chosen.sort(key=key, reverse=reverse)  # estable: los empates conservan el orden natural
    return [r["Designación"] for r in chosen]


def count_line(out: str) -> tuple[int, int]:
    match = re.search(r"(\d+) coincidencia\(s\) de (\d+) perfil", out)
    assert match is not None
    return int(match[1]), int(match[2])


def test_search_without_filters_lists_the_100_stored_profiles_and_not_the_rejected(
    imported: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = search(capsys)
    assert (code, err) == (0, "")
    assert count_line(out) == (100, 100)
    assert found(out) == expected_names(lambda r: True)
    assert "MALO" not in out
    assert out.rstrip().endswith("no son datos verificados.")


@pytest.mark.parametrize("unit_scale", [("cm2", 1), ("mm2", 100)])
def test_area_boundaries_are_inclusive_in_any_unit(
    imported: Path, capsys: pytest.CaptureFixture[str], unit_scale: tuple[str, int]
) -> None:
    unit, scale = unit_scale
    areas = sorted({dec(r, "A (cm²)") for r in rows()})
    low, high = areas[10], areas[30]  # valores reales del archivo: la frontera cuenta
    code, out, _ = search(capsys, "--area", f"{low * scale}..{high * scale} {unit}")
    assert code == 0
    want = expected_names(lambda r: low <= dec(r, "A (cm²)") <= high)
    assert found(out) == want
    assert count_line(out)[0] == len(want) > 20
    # un perfil justo en cada frontera está dentro y uno justo fuera no
    just_above = areas[31]
    _, out_edge, _ = search(
        capsys, "--area", f"{high * scale}..{just_above * scale - Decimal('0.0001') * scale} {unit}"
    )
    assert {r["Designación"] for r in rows() if dec(r, "A (cm²)") == high} <= set(found(out_edge))
    assert not {r["Designación"] for r in rows() if dec(r, "A (cm²)") == just_above} & set(
        found(out_edge)
    )


def test_open_ranges_and_combined_filters(
    imported: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = search(
        capsys,
        "--iy", ">=500 cm4",
        "--masa", "..12 kg/m",
        "--dim", "h", "100..200 mm",
        "--serie", "sintet",
    )  # fmt: skip
    assert code == 0
    want = expected_names(
        lambda r: (
            dec(r, "Iy (cm⁴)") >= 500
            and dec(r, "m (kg/m)") <= 12
            and 100 <= dec(r, "h (mm)") <= 200
        )
    )
    assert want  # el caso no es vacío
    assert found(out) == want


def test_designation_and_family_filters(imported: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _, out, _ = search(capsys, "--designacion=-100x", "--familia", "I")
    assert found(out) == expected_names(lambda r: "-100x" in r["Designación"].casefold())
    _, none, _ = search(capsys, "--familia", "RHS")
    assert count_line(none) == (0, 100)
    assert found(none) == []


@pytest.mark.parametrize("descending", [False, True])
def test_order_by_a_property_is_exact_and_stable(
    imported: Path, capsys: pytest.CaptureFixture[str], descending: bool
) -> None:
    order = "area:desc" if descending else "area"
    _, out, _ = search(capsys, "--orden", order)
    want = expected_names(lambda r: True, key=lambda r: dec(r, "A (cm²)"), reverse=descending)
    assert found(out) == want


def test_order_by_a_dimension_shows_that_dimension(
    imported: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, out, _ = search(capsys, "--orden", "dim_h:desc", "--limite", "5")
    assert "h [m]" in out
    heights = [dec(r, "h (mm)") for r in rows()]
    first = re.search(r"^Sint\s+\S+\s+\S+\s+I\s+1\s+(.*)$", out, flags=re.MULTILINE)
    assert first is not None
    assert Decimal(first[1].split()[-1]) * 1000 == max(heights)


def test_limit_cuts_the_listing_and_says_so(
    imported: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, out, _ = search(capsys, "--limite", "7")
    assert len(found(out)) == 7
    assert "Se muestran los 7 primeros de 100 (--limite)." in out
    assert count_line(out) == (100, 100)


def test_j_is_absent_in_every_row_so_no_profile_matches_and_the_exclusion_is_reported(
    imported: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for text in (">=0 cm4", "..1000 cm4", "0..0 cm4"):
        code, out, _ = search(capsys, "--j", text)
        assert code == 0
        assert count_line(out) == (0, 100)
        assert found(out) == []
        assert "Excluidos por falta de dato: 100 perfil(es)" in out
        assert "(J: 100)" in out
        assert "ausente no es 0" in out


def test_a_missing_dimension_name_excludes_every_profile_and_is_reported(
    imported: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, out, _ = search(capsys, "--dim", "zz", "0..1 m")
    assert count_line(out) == (0, 100)
    assert "(zz: 100)" in out


def test_search_can_be_limited_to_one_catalog_and_spans_all_by_default(
    imported: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["catalog", "create", "--db", DB, "--nombre", "Otro", "--fuente", "S",
                 "--licencia", "L", "--edicion", "1"]) == 0  # fmt: skip
    capsys.readouterr()
    _, all_out, _ = search(capsys)
    assert count_line(all_out) == (100, 100)
    _, one, _ = search(capsys, "--catalogo", "otro")
    assert count_line(one) == (0, 0)
    code, _, err = search(capsys, "--catalogo", "Inexistente")
    assert code == 2
    assert DIAGNOSTIC.match(err)


@pytest.mark.parametrize(
    ("argv", "fragment"),
    [
        (["--area", "20 cm2"], "Rango no válido en --area"),
        (["--iy", "1..2 mm"], "dimensión incompatible"),
        (["--masa", "1..2 parsec"], "unidad desconocida"),
        (["--dim", "H", "1..2 mm"], "Rango no válido en --dim"),
        (["--dim", "h", "5..1 mm"], "mayor que el máximo"),
        (["--orden", "peso"], "Orden no válido"),
        (["--limite", "0"], "Opción --limite no válida"),
    ],
)
def test_invalid_options_exit_2_with_a_plain_diagnostic_and_leave_the_file_intact(
    imported: Path, capsys: pytest.CaptureFixture[str], argv: list[str], fragment: str
) -> None:
    before = (imported / DB).read_bytes()
    code, out, err = search(capsys, *argv)
    assert (code, out) == (2, "")
    assert DIAGNOSTIC.match(err), err
    assert fragment in err
    assert "{" not in err
    assert (imported / DB).read_bytes() == before


def test_search_is_read_only(imported: Path, capsys: pytest.CaptureFixture[str]) -> None:
    before = (imported / DB).read_bytes()
    search(capsys, "--area", "1..100 cm2", "--orden", "iy:desc")
    main(["catalog", "ficha", "--db", DB, "Sint/SINTETICO-I/SINT-100x50-4-6"])
    assert (imported / DB).read_bytes() == before


# --- ficha -------------------------------------------------------------------------------------


def test_ficha_shows_the_profile_provenance_batch_and_revision_history(
    imported: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    capsys.readouterr()
    code = main(["catalog", "ficha", "--db", DB, "Sint/SINTETICO-I/SINT-100x50-4-6"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Perfil SINTETICO-I / SINT-100x50-4-6 — revisión 1 de 1" in out
    assert "A = 0.000952 m²" in out
    assert "J = no disponible" in out
    assert "IMPORTADO_SIN_VERIFICAR" in out
    assert "procedencia: fuente SINTÉTICO" in out
    assert "lote que creó la revisión:" in out
    assert re.search(r"historial de revisiones:\n\s+rev 1 · [0-9a-f]{12} · \d{4}-", out)
    assert out.rstrip().endswith("no son datos verificados.")


def test_ficha_lists_every_revision_and_selects_one(
    imported: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    changed = (imported / "catalog_synthetic_100.csv").read_text(encoding="utf-8-sig")
    changed = changed.replace(
        "SINT-100x50-4-6;I;100;50;4;6;9,52;", "SINT-100x50-4-6;I;100;50;4;6;9,60;"
    )
    (imported / "v2.csv").write_text(changed, encoding="utf-8-sig")
    assert main(["catalog", "import", "--db", DB, "--catalogo", "Sint", "v2.csv",
                 *SYNTHETIC_ARGS]) == 0  # fmt: skip
    capsys.readouterr()
    main(["catalog", "ficha", "--db", DB, "Sint/SINTETICO-I/SINT-100x50-4-6"])
    latest = capsys.readouterr().out
    assert "revisión 2 de 2" in latest
    assert len(re.findall(r"^\s+rev \d · ", latest, flags=re.MULTILINE)) == 2
    main(["catalog", "ficha", "--db", DB, "Sint/SINTETICO-I/SINT-100x50-4-6@1"])
    first = capsys.readouterr().out
    assert "revisión 1 de 2" in first
    assert "A = 0.000952 m²" in first
    assert "A = 0.00096 m²" in latest


@pytest.mark.parametrize(
    ("reference", "fragment"),
    [
        ("Sint", "Referencia de perfil no válida"),
        ("Sint/SINTETICO-I/NO-EXISTE", "No existe el perfil"),
        ("NoCat/SINTETICO-I/SINT-100x50-4-6", "No existe"),
        ("Sint/SINTETICO-I/SINT-100x50-4-6@9", "No existe el perfil"),
    ],
)
def test_ficha_errors_exit_2(
    imported: Path, capsys: pytest.CaptureFixture[str], reference: str, fragment: str
) -> None:
    capsys.readouterr()
    code = main(["catalog", "ficha", "--db", DB, reference])
    captured = capsys.readouterr()
    assert (code, captured.out) == (2, "")
    assert DIAGNOSTIC.match(captured.err)
    assert fragment in captured.err


# --- comparar ----------------------------------------------------------------------------------


def test_comparar_values_and_ratios_match_the_source_cells(
    imported: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    first, second = rows()[0], rows()[50]
    capsys.readouterr()
    code = main(["catalog", "comparar", "--db", DB,
                 f"Sint/SINTETICO-I/{first['Designación']}",
                 f"Sint/SINTETICO-I/{second['Designación']}@1"])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0
    assert "Comparación de 2 perfiles (valores en SI)" in out
    line = next(line for line in out.splitlines() if line.startswith("A "))
    cells = line.split()
    area_a = float(dec(first, "A (cm²)")) * 1e-4
    area_b = float(dec(second, "A (cm²)")) * 1e-4
    assert float(cells[2]) == pytest.approx(area_a, rel=1e-5)
    assert float(cells[3]) == pytest.approx(area_b, rel=1e-5)
    assert float(cells[4]) == pytest.approx(area_b / area_a, rel=1e-3)
    j_line = next(line for line in out.splitlines() if line.startswith("J "))
    assert j_line.split()[2:] == ["-", "-", "-"]  # J ausente: sin valor ni cociente
    assert "«-»: dato no disponible" in out
    assert out.rstrip().endswith("no son datos verificados.")


def test_comparar_across_catalogs_and_up_to_eight(
    imported: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["catalog", "create", "--db", DB, "--nombre", "Otro", "--fuente", "S",
                 "--licencia", "L", "--edicion", "1"]) == 0  # fmt: skip
    assert main(["catalog", "import", "--db", DB, "--catalogo", "Otro",
                 "catalog_synthetic_100.csv", *SYNTHETIC_ARGS]) == 0  # fmt: skip
    capsys.readouterr()
    name = rows()[3]["Designación"]
    code = main(["catalog", "comparar", "--db", DB, f"Sint/SINTETICO-I/{name}",
                 f"Otro/SINTETICO-I/{name}"])  # fmt: skip
    out = capsys.readouterr().out
    assert code == 0
    assert "P2/P1" in out
    ratio = next(line for line in out.splitlines() if line.startswith("A ")).split()[-1]
    assert float(ratio) == 1.0
    eight = [f"Sint/SINTETICO-I/{r['Designación']}" for r in rows()[:8]]
    assert main(["catalog", "comparar", "--db", DB, *eight]) == 0
    assert capsys.readouterr().out.count("P8") >= 2


@pytest.mark.parametrize("count", [1, 9])
def test_comparar_rejects_a_wrong_number_of_profiles_before_reading_anything(
    imported: Path, capsys: pytest.CaptureFixture[str], count: int
) -> None:
    refs = [f"Sint/SINTETICO-I/{r['Designación']}" for r in rows()[:count]]
    capsys.readouterr()
    code = main(["catalog", "comparar", "--db", DB, *refs])
    captured = capsys.readouterr()
    assert (code, captured.out) == (2, "")
    assert "Se comparan de 2 a 8 perfiles" in captured.err


def test_comparar_rejects_a_repeated_profile(
    imported: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ref = f"Sint/SINTETICO-I/{rows()[0]['Designación']}"
    capsys.readouterr()
    code = main(["catalog", "comparar", "--db", DB, ref, ref])
    captured = capsys.readouterr()
    assert (code, captured.out) == (2, "")
    assert "más de una vez" in captured.err


def test_the_existing_commands_are_unchanged_by_the_refactor(
    imported: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``show`` y ``list`` (salidas aceptadas) siguen dando lo mismo tras compartir código."""
    capsys.readouterr()
    assert main(["catalog", "show", "--db", DB, "--catalogo", "Sint",
                 "SINTETICO-I", "SINT-100x50-4-6", "--revision", "1"]) == 0  # fmt: skip
    shown = capsys.readouterr().out
    assert "historial" not in shown
    assert shown.splitlines()[0] == "Perfil SINTETICO-I / SINT-100x50-4-6 — revisión 1 de 1"
    code = main(
        [
            "catalog",
            "show",
            "--db",
            DB,
            "--catalogo",
            "Sint",
            "SINTETICO-I",
            "SINT-100x50-4-6",
            "--revision",
            "7",
        ]
    )
    assert code == 2
    assert "No existe el perfil «SINTETICO-I / SINT-100x50-4-6 rev 7»" in capsys.readouterr().err
    with CatalogStore.open(DB) as store:
        assert len(store.list_profiles(store.catalog_by_name("Sint").id)) == 100
