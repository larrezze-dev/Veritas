"""Importador CSV/JSON: lectura, validación por fila, duplicados y avisos (T-022 E2)."""

import hashlib
import json
import math
from collections.abc import Callable
from pathlib import Path

import pytest

from catalog_support import assert_renders, message_key, uid
from veritas.catalogs.columns import NATIVE_METHOD, parse_mapping
from veritas.catalogs.errors import CatalogError, CatalogFileError
from veritas.catalogs.importer import (
    AREA_TOLERANCE,
    MASS_TOLERANCE,
    STEEL_DENSITY,
    WARN_AREA,
    WARN_AXES,
    WARN_MASS,
    ParsedBatch,
    RowResult,
    check_options,
    coherence_warnings,
    parse_batch,
    read_source,
)
from veritas.catalogs.model import CatalogInfo, ProfileFamily
from veritas.core.diagnostics import Diagnostic, DiagnosticCode
from veritas.core.messages_es import MESSAGES_ES
from veritas.core.sections.properties import PropertyStatus

INFO = CatalogInfo(
    id=uid(1),
    name="Prueba",
    description="",
    source="SINTÉTICO",
    license="Sin valor comercial",
    edition="2026",
    protected=False,
)
HEADER = "series,designation,family,area,iy,iz,torsion_constant,linear_mass"
GOOD = "S,P-1,I,1e-3,2e-6,3e-7,,7.85"


def write(tmp_path: Path, name: str, content: str | bytes) -> Path:
    path = tmp_path / name
    if isinstance(content, str):
        path.write_text(content, encoding="utf-8", newline="")
    else:
        path.write_bytes(content)
    return path


def parse_text(
    tmp_path: Path, rows: list[str], header: str = HEADER, **kwargs: object
) -> ParsedBatch:
    source = read_source(write(tmp_path, "lote.csv", "\n".join([header, *rows]) + "\n"))
    return parse_batch(source, None, INFO, **kwargs)  # type: ignore[arg-type]


def only(result: RowResult) -> str:
    assert result.data is None
    assert len(result.issues) == 1
    assert_renders(result.issues[0].diagnostic)
    return result.issues[0].diagnostic.message_key


# --- Lectura de CSV ----------------------------------------------------------------------------


def test_bom_crlf_and_blank_lines_keep_physical_line_numbers(tmp_path: Path) -> None:
    text = "﻿" + HEADER + "\r\n\r\n" + GOOD + "\r\n   \r\n" + GOOD.replace("P-1", "P-2") + "\r\n"
    path = write(tmp_path, "lote.csv", text.encode("utf-8"))
    source = read_source(path)
    assert source.header[0] == "series"  # sin BOM
    assert [row.line for row in source.rows] == [3, 5]
    assert source.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert source.file_format == "CSV"
    assert source.name == "lote.csv"


def test_a_quoted_multiline_cell_shifts_the_lines_that_follow(tmp_path: Path) -> None:
    rows = ['S,"P\n-1",I,1e-3,2e-6,3e-7,,7.85', GOOD.replace("P-1", "P-2")]
    batch = parse_text(tmp_path, rows)
    assert [row.line for row in batch.rows] == [2, 4]
    assert batch.rows[0].data is not None
    assert batch.rows[0].data.designation == "P\n-1"


def test_a_row_with_the_wrong_width_is_rejected_alone(tmp_path: Path) -> None:
    batch = parse_text(tmp_path, [GOOD, "S,P-2,I,1e-3", GOOD.replace("P-1", "P-3") + ",extra"])
    assert [r.data is not None for r in batch.rows] == [True, False, False]
    assert [only(r) for r in batch.rejected] == ["catalog.row_width"] * 2
    assert batch.rejected[0].issues[0].diagnostic.params_dict() == {"expected": 8, "found": 4}


@pytest.mark.parametrize("content", ["", "\n\n  \n", HEADER + "\n", HEADER + "\n\n  ,,\n"])
def test_a_file_without_data_rows_is_an_error(tmp_path: Path, content: str) -> None:
    with pytest.raises(CatalogFileError) as error:
        read_source(write(tmp_path, "vacio.csv", content))
    assert message_key(error) == "catalog.import_empty"
    assert_renders(error.value.diagnostic)


def test_file_level_errors(tmp_path: Path) -> None:
    cases: list[tuple[Callable[[], object], str]] = [
        (lambda: read_source(tmp_path / "no_existe.csv"), "catalog.import_missing"),
        (lambda: read_source(write(tmp_path, "a.txt", HEADER)), "catalog.import_format"),
        (
            lambda: read_source(write(tmp_path, "b.csv", HEADER.encode("utf-16"))),
            "catalog.import_encoding",
        ),
        (lambda: read_source(write(tmp_path, "c.csv", 'a,"b"c\n1,2\n')), "catalog.import_csv"),
        (lambda: read_source(tmp_path), "catalog.import_format"),
        (lambda: read_source(tmp_path, requested_format="csv"), "catalog.import_unreadable"),
        (
            lambda: read_source(write(tmp_path, "d.csv", GOOD), requested_format="xml"),
            "catalog.import_options",
        ),
    ]
    for call, key in cases:
        with pytest.raises(CatalogFileError) as error:
            call()
        assert message_key(error) == key
        assert_renders(error.value.diagnostic)


def test_an_explicit_format_overrides_the_extension(tmp_path: Path) -> None:
    path = write(tmp_path, "datos.dat", HEADER + "\n" + GOOD + "\n")
    assert read_source(path, requested_format="csv").file_format == "CSV"


def test_a_repeated_header_name_is_rejected(tmp_path: Path) -> None:
    source = read_source(write(tmp_path, "a.csv", HEADER + ",area\n" + GOOD + ",1\n"))
    with pytest.raises(CatalogError) as error:
        parse_batch(source, None, INFO)
    assert message_key(error) == "catalog.import_duplicate_column"


@pytest.mark.parametrize(
    ("sep", "decimal", "ok"),
    [
        (",", ".", True),
        (";", ".", True),
        (";", ",", True),
        (",", ",", False),
        ("\t", ".", False),
        (";", ";", False),
    ],
)
def test_option_combinations(sep: str, decimal: str, ok: bool) -> None:
    if ok:
        check_options(sep, decimal)
        return
    with pytest.raises(CatalogFileError) as error:
        check_options(sep, decimal)
    assert message_key(error) == "catalog.import_options"
    assert_renders(error.value.diagnostic)


def test_semicolon_and_decimal_comma(tmp_path: Path) -> None:
    source = read_source(
        write(
            tmp_path, "a.csv", "series;designation;family;area;iy;iz\nS;P;I;1,5e-3;2,5e-6;3e-7\n"
        ),
        sep=";",
    )
    batch = parse_batch(source, None, INFO, decimal=",")
    assert batch.rows[0].data is not None
    assert batch.rows[0].data.properties.area.value == 1.5e-3
    bad = parse_batch(source, None, INFO).rows[0]  # sin declarar la coma decimal
    assert [i.diagnostic.message_key for i in bad.issues] == ["catalog.row_not_number"] * 2
    assert [i.field for i in bad.issues] == ["area", "iy"]


# --- Lectura de JSON ---------------------------------------------------------------------------


def records(*items: object) -> str:
    return json.dumps(list(items))


def test_json_rows_with_numbers_strings_and_nulls(tmp_path: Path) -> None:
    record = {
        "series": "S",
        "designation": "P",
        "family": "I",
        "area": 1e-3,
        "iy": "2e-6",
        "iz": 3e-7,
        "torsion_constant": None,
        "linear_mass": 7,
    }
    other = {**record, "designation": "Q"}
    del other["linear_mass"]
    source = read_source(write(tmp_path, "datos.json", records(record, other)))
    assert source.file_format == "JSON"
    assert [row.line for row in source.rows] == [1, 2]
    batch = parse_batch(source, None, INFO)
    first, second = (row.data for row in batch.rows)
    assert first is not None
    assert second is not None
    assert first.properties.linear_mass.value == 7.0
    assert first.properties.torsion_constant.status is PropertyStatus.NO_DISPONIBLE
    assert second.properties.linear_mass.value is None


def test_json_value_types(tmp_path: Path) -> None:
    good = {"series": "S", "designation": "P", "family": "I", "area": 1, "iy": 1, "iz": 1}
    bad = [
        {**good, "area": True},
        {**good, "iy": [1]},
        {**good, "designation": 100},
        {**good, "iz": {"a": 1}},
        {**good, "area": 10**400},
        "no es un objeto",
    ]
    batch = parse_batch(read_source(write(tmp_path, "a.json", records(good, *bad))), None, INFO)
    assert batch.rows[0].data is not None
    assert [only(row) for row in batch.rows[1:]] == ["catalog.row_value_type"] * 6
    assert [row.issues[0].field for row in batch.rows[1:]] == [
        "area", "iy", "designation", "iz", "area", "(registro)",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("content", "key"),
    [
        ("{}", "catalog.import_structure"),
        ('"texto"', "catalog.import_structure"),
        ("[{]", "catalog.import_json"),
        ('[{"a": 1, "a": 2}]', "catalog.import_json"),
        ('[{"area": NaN}]', "catalog.import_json"),
        ('[{"area": Infinity}]', "catalog.import_json"),
        ("[]", "catalog.import_empty"),
    ],
)
def test_json_file_errors(tmp_path: Path, content: str, key: str) -> None:
    with pytest.raises(CatalogFileError) as error:
        read_source(write(tmp_path, "a.json", content))
    assert message_key(error) == key
    assert_renders(error.value.diagnostic)


# --- Una fila válida ----------------------------------------------------------------------------


def test_a_valid_row_is_imported_unverified_with_provenance(tmp_path: Path) -> None:
    batch = parse_text(tmp_path, [GOOD.replace(",,", ",5e-8,", 1)])
    data = batch.rows[0].data
    assert data is not None
    props = data.properties
    for name, value in (("area", 1e-3), ("iy", 2e-6), ("iz", 3e-7), ("torsion_constant", 5e-8)):
        prop = getattr(props, name)
        assert prop.value == value
        assert prop.status is PropertyStatus.IMPORTADO_SIN_VERIFICAR
        assert prop.origin == INFO.source
    assert props.linear_mass.value == 7.85
    assert (data.provenance.source, data.provenance.license, data.provenance.edition) == (
        INFO.source,
        INFO.license,
        INFO.edition,
    )
    assert data.provenance.method == NATIVE_METHOD
    assert data.warnings == ()
    assert batch.rows[0].line == 2


def test_an_empty_cell_is_not_available_and_a_zero_is_an_error(tmp_path: Path) -> None:
    batch = parse_text(
        tmp_path, [GOOD, "S,P-2,I,1e-3,2e-6,3e-7,0,7.85", "S,P-3,I,1e-3,2e-6,3e-7,0.0,"]
    )
    ok, zero_j, zero_j2 = batch.rows
    assert ok.data is not None
    assert ok.data.properties.torsion_constant.value is None
    assert ok.data.properties.torsion_constant.status is PropertyStatus.NO_DISPONIBLE
    assert only(zero_j) == "catalog.row_not_positive"
    assert zero_j.issues[0].field == "torsion_constant"
    assert only(zero_j2) == "catalog.row_not_positive"


def test_a_missing_optional_column_is_not_available_for_every_row(tmp_path: Path) -> None:
    batch = parse_text(
        tmp_path, ["S,P,I,1e-3,2e-6,3e-7"], header="series,designation,family,area,iy,iz"
    )
    data = batch.rows[0].data
    assert data is not None
    assert data.properties.torsion_constant.value is None
    assert data.properties.linear_mass.value is None


def test_values_are_converted_with_the_mapping_units(tmp_path: Path) -> None:
    mapping = parse_mapping(
        json.dumps(
            {
                "format": "veritas-catalog-mapping/1",
                "columns": {
                    "Serie": {"field": "series"},
                    "Perfil": {"field": "designation"},
                    "A": {"field": "area", "unit": "cm²"},
                    "Iy": {"field": "iy", "unit": "cm⁴"},
                    "Iz": {"field": "iz", "unit": "mm⁴"},
                    "h": {"field": "dim_h", "unit": "mm"},
                },
                "default_family": "OTRO",
                "method": "Prueba de unidades",
            }
        )
    )
    path = write(
        tmp_path, "u.csv", "Serie,Perfil,A,Iy,Iz,h,Nota\nS,P,12.5,155.4357,2000000,100,x\n"
    )
    batch = parse_batch(read_source(path), mapping, INFO)
    data = batch.rows[0].data
    assert data is not None
    assert data.properties.area.value == pytest.approx(12.5e-4, rel=1e-15)
    assert data.properties.iy.value == pytest.approx(155.4357e-8, rel=1e-15)
    assert data.properties.iz.value == pytest.approx(2e-6, rel=1e-15)
    assert [d.name for d in data.dimensions] == ["h"]
    assert data.dimensions[0].value == pytest.approx(0.1, rel=1e-15)
    assert data.family is ProfileFamily.OTRO
    assert data.provenance.method == "Prueba de unidades"
    assert "«A» (m²)" not in data.properties.area.method
    assert data.properties.area.method == "Importado de la columna «A» (cm²) y convertido a SI"


def test_a_mapped_column_missing_from_the_file_is_an_error(tmp_path: Path) -> None:
    mapping = parse_mapping(
        json.dumps(
            {
                "format": "veritas-catalog-mapping/1",
                "columns": {
                    "Serie": {"field": "series"},
                    "Perfil": {"field": "designation"},
                    "A": {"field": "area", "unit": "m²"},
                    "Iy": {"field": "iy", "unit": "m⁴"},
                    "Iz": {"field": "iz", "unit": "m⁴"},
                },
                "default_family": "I",
            }
        )
    )
    source = read_source(write(tmp_path, "m.csv", "Serie,Perfil,A,Iy\nS,P,1,1\n"))
    with pytest.raises(CatalogError) as error:
        parse_batch(source, mapping, INFO)
    assert message_key(error) == "catalog.mapping_column_missing"


# --- Errores de fila ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("row", "key", "field"),
    [
        ("S,P,I,abc,2e-6,3e-7,,", "catalog.row_not_number", "area"),
        ("S,P,I,1e-3,nan,3e-7,,", "catalog.row_not_number", "iy"),
        ("S,P,I,1e-3,2e-6,inf,,", "catalog.row_not_number", "iz"),
        ("S,P,I,1e-3,2e-6,3e-7,1_0,", "catalog.row_not_number", "torsion_constant"),
        ("S,P,I,1e-3,2e-6,3e-7,0x1,", "catalog.row_not_number", "torsion_constant"),
        ("S,P,I,1e999,2e-6,3e-7,,", "catalog.row_not_number", "area"),
        ("S,P,I,0,2e-6,3e-7,,", "catalog.row_not_positive", "area"),
        ("S,P,I,-1e-3,2e-6,3e-7,,", "catalog.row_not_positive", "area"),
        ("S,P,I,1e-3,-0,3e-7,,", "catalog.row_not_positive", "iy"),
        ("S,P,I,1e-3,2e-6,3e-7,,-7.85", "catalog.row_not_positive", "linear_mass"),
        ("S,P,I,,2e-6,3e-7,,", "catalog.row_required", "area"),
        ("S,P,I,1e-3,,3e-7,,", "catalog.row_required", "iy"),
        ("S,P,I,1e-3,2e-6, ,,", "catalog.row_required", "iz"),
        ("S,,I,1e-3,2e-6,3e-7,,", "catalog.row_required", "designation"),
        (" ,P,I,1e-3,2e-6,3e-7,,", "catalog.row_required", "series"),
        ("S,P,,1e-3,2e-6,3e-7,,", "catalog.row_required", "family"),
        ("S,P,TUBO,1e-3,2e-6,3e-7,,", "catalog.row_family_unknown", "family"),
        ("S,P,I,1e-320,2e-6,3e-7,,", "catalog.row_unrepresentable", "area"),  # subnormal
    ],
)
def test_row_errors(tmp_path: Path, row: str, key: str, field: str | None) -> None:
    result = parse_text(tmp_path, [row]).rows[0]
    assert only(result) == key
    assert result.issues[0].field == field
    assert result.issues[0].line == result.line == 2
    assert "línea 2" in result.issues[0].text()


def test_every_error_of_a_row_is_reported(tmp_path: Path) -> None:
    result = parse_text(tmp_path, ["S,,TUBO,abc,0,,,"]).rows[0]
    assert result.data is None
    assert sorted(i.diagnostic.message_key for i in result.issues) == sorted(
        [
            "catalog.row_required",  # designation
            "catalog.row_family_unknown",
            "catalog.row_not_number",  # area
            "catalog.row_not_positive",  # iy
            "catalog.row_required",  # iz
        ]
    )
    for issue in result.issues:
        assert_renders(issue.diagnostic)


def test_an_unrepresentable_conversion_is_a_row_error(tmp_path: Path) -> None:
    mapping = parse_mapping(
        json.dumps(
            {
                "format": "veritas-catalog-mapping/1",
                "columns": {
                    "S": {"field": "series"},
                    "D": {"field": "designation"},
                    "A": {"field": "area", "unit": "m²"},
                    "Iy": {"field": "iy", "unit": "mm⁴"},
                    "Iz": {"field": "iz", "unit": "m⁴"},
                },
                "default_family": "I",
            }
        )
    )
    source = read_source(write(tmp_path, "r.csv", "S,D,A,Iy,Iz\nS,P,1,1e-320,1\n"))
    result = parse_batch(source, mapping, INFO).rows[0]
    assert only(result) == "catalog.row_unrepresentable"


def test_long_values_are_shortened_in_the_message(tmp_path: Path) -> None:
    result = parse_text(tmp_path, ["S,P,I," + "9" * 100 + "x,2e-6,3e-7,,"]).rows[0]
    value = str(result.issues[0].diagnostic.params_dict()["value"])
    assert len(value) <= 40
    assert value.endswith("…")


# --- Aliases y familia ---------------------------------------------------------------------------


def test_family_without_case_and_with_a_default(tmp_path: Path) -> None:
    batch = parse_text(tmp_path, ["S,P,i,1e-3,2e-6,3e-7,,", "S,Q,shs,1e-3,2e-6,3e-7,,"])
    assert [r.data.family for r in batch.rows if r.data] == [ProfileFamily.I, ProfileFamily.SHS]


# --- Claves repetidas ----------------------------------------------------------------------------


def line_set(batch: ParsedBatch) -> list[int]:
    return [row.line for row in batch.rejected]


def test_repeated_keys_reject_every_row_with_that_key(tmp_path: Path) -> None:
    rows = [GOOD, GOOD, GOOD.replace("P-1", "P-2"), GOOD, GOOD.replace("P-1", "P-3")]
    batch = parse_text(tmp_path, rows)
    assert line_set(batch) == [2, 3, 5]
    assert [r.data is not None for r in batch.rows] == [False, False, True, False, True]
    for row in batch.rejected:
        (issue,) = row.issues
        assert issue.diagnostic.message_key == "catalog.row_duplicate_key"
        assert issue.diagnostic.params_dict() == {
            "series": "S",
            "designation": "P-1",
            "first_line": 2,
        }
        assert_renders(issue.diagnostic)
    assert [d.designation for d in batch.accepted] == ["P-2", "P-3"]


def test_keys_are_compared_after_trimming_but_case_sensitively(tmp_path: Path) -> None:
    batch = parse_text(
        tmp_path, [GOOD, GOOD.replace("S,P-1", " S , P-1 "), GOOD.replace("P-1", "p-1")]
    )
    assert line_set(batch) == [2, 3]
    assert batch.rows[2].data is not None


def test_a_repeated_key_is_rejected_even_when_the_row_has_other_errors(tmp_path: Path) -> None:
    batch = parse_text(tmp_path, [GOOD, "S,P-1,I,abc,2e-6,3e-7,,"])
    assert line_set(batch) == [2, 3]
    assert [i.diagnostic.message_key for i in batch.rows[1].issues] == [
        "catalog.row_not_number",
        "catalog.row_duplicate_key",
    ]


def test_the_same_designation_in_another_series_is_not_a_repeat(tmp_path: Path) -> None:
    batch = parse_text(tmp_path, [GOOD, GOOD.replace("S,", "T,", 1)])
    assert batch.rejected == ()


def test_rows_without_a_key_do_not_form_a_group(tmp_path: Path) -> None:
    batch = parse_text(tmp_path, ["S,,I,1e-3,2e-6,3e-7,,", "S,,I,1e-3,2e-6,3e-7,,"])
    assert [only(r) for r in batch.rows] == ["catalog.row_required"] * 2


# --- Procedencia del lote ------------------------------------------------------------------------


def test_the_batch_source_describes_file_mapping_and_counts(tmp_path: Path) -> None:
    batch = parse_text(tmp_path, [GOOD, "S,P-2,I,abc,2e-6,3e-7,,"])
    source = batch.import_source
    assert (source.rows_read, source.rows_rejected, source.rows_accepted) == (2, 1, 1)
    assert source.file_sha256 == batch.source.sha256
    assert source.mapping_json == batch.mapping.document
    assert source.mapping_sha256 == batch.mapping.sha256
    assert source.file_name == "lote.csv"
    assert source.file_format == "CSV"


def test_a_changed_value_does_not_change_the_provenance_of_the_other_rows(tmp_path: Path) -> None:
    first = parse_text(tmp_path, [GOOD, GOOD.replace("P-1", "P-2")])
    second = parse_text(tmp_path, [GOOD, GOOD.replace("P-1", "P-2").replace("1e-3", "1.1e-3")])
    assert first.source.sha256 != second.source.sha256
    assert first.accepted[0] == second.accepted[0]  # D8: la fila intacta es idéntica


# --- Avisos de coherencia (D11) -------------------------------------------------------------------

I_DIMS = {"h": 0.2, "b": 0.1, "tw": 0.005, "tf": 0.01}
I_AREA = 2 * 0.1 * 0.01 + (0.2 - 2 * 0.01) * 0.005


def warnings(**changes: object) -> tuple[str, ...]:
    values: dict[str, object] = {
        "family": ProfileFamily.I,
        "dimensions": {},
        "area": 1e-3,
        "iy": 2e-6,
        "iz": 1e-6,
        "mass": 1e-3 * STEEL_DENSITY,
        "steel": True,
    }
    return coherence_warnings(**{**values, **changes})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("factor", "warns"),
    [(1.0, False), (1 + MASS_TOLERANCE - 1e-4, False), (1 + MASS_TOLERANCE + 1e-4, True),
     (1 - MASS_TOLERANCE + 1e-4, False), (1 - MASS_TOLERANCE - 1e-4, True)],
)  # fmt: skip
def test_mass_warning_boundary(factor: float, warns: bool) -> None:
    result = warnings(mass=1e-3 * STEEL_DENSITY * factor)
    assert (WARN_MASS in result) is warns


def test_mass_warning_needs_steel_area_and_mass() -> None:
    far = 1e-3 * STEEL_DENSITY * 3
    assert WARN_MASS not in warnings(mass=far, steel=False)
    assert WARN_MASS not in warnings(mass=None)
    assert WARN_MASS not in warnings(mass=far, area=None)
    assert WARN_MASS in warnings(mass=far)
    assert WARN_MASS in warnings(mass=far, family=ProfileFamily.RHS)  # no depende de la forma


@pytest.mark.parametrize(
    ("factor", "warns"),
    [(1.0, False), (1 + AREA_TOLERANCE - 1e-4, False), (1 + AREA_TOLERANCE + 1e-4, True),
     (1 - AREA_TOLERANCE + 1e-4, False), (1 - AREA_TOLERANCE - 1e-4, True)],
)  # fmt: skip
def test_area_warning_boundary(factor: float, warns: bool) -> None:
    result = warnings(dimensions=I_DIMS, area=I_AREA * factor, mass=None)
    assert (WARN_AREA in result) is warns


def test_area_warning_conditions() -> None:
    far = I_AREA * 2
    assert WARN_AREA in warnings(dimensions=I_DIMS, area=far, mass=None)
    assert WARN_AREA not in warnings(
        dimensions=I_DIMS, area=far, mass=None, family=ProfileFamily.RHS
    )
    assert WARN_AREA not in warnings(dimensions=I_DIMS, area=None, mass=None)
    for name in I_DIMS:
        partial = {k: v for k, v in I_DIMS.items() if k != name}
        assert WARN_AREA not in warnings(dimensions=partial, area=far, mass=None)
    impossible = {**I_DIMS, "h": 0.015}  # h − 2·tf < 0: la I no existe
    assert WARN_AREA in warnings(dimensions=impossible, area=1e-3, mass=None)


def test_axes_warning() -> None:
    assert warnings(iy=1e-6, iz=2e-6) == (WARN_AXES,)
    assert WARN_AXES not in warnings(iy=1e-6, iz=1e-6)  # igual: sin aviso
    assert WARN_AXES not in warnings(iy=2e-6, iz=1e-6)
    assert WARN_AXES not in warnings(iy=1e-6, iz=2e-6, family=ProfileFamily.U)
    assert WARN_AXES not in warnings(iy=None)


def test_warnings_keep_a_fixed_order_and_reach_the_row(tmp_path: Path) -> None:
    assert warnings(
        dimensions=I_DIMS, area=I_AREA * 2, iy=1e-6, iz=2e-6, mass=I_AREA * 2 * STEEL_DENSITY * 3
    ) == (WARN_MASS, WARN_AREA, WARN_AXES)
    header = HEADER + ",dim_h,dim_b,dim_tw,dim_tf"
    row = "S,P,I,2.9e-3,2e-6,3e-6,,22.765,0.2,0.1,0.005,0.01"
    data = parse_text(tmp_path, [row], header=header).rows[0].data
    assert data is not None
    assert data.warnings == (WARN_AXES,)  # sin «steel» no hay aviso de masa; A coincide


def test_warning_messages_state_their_tolerances() -> None:
    assert f"±{MASS_TOLERANCE * 100:.0f} %" in MESSAGES_ES[WARN_MASS]
    assert f"±{AREA_TOLERANCE * 100:.0f} %" in MESSAGES_ES[WARN_AREA]
    assert f"{STEEL_DENSITY:.0f} kg/m³" in MESSAGES_ES[WARN_MASS]


def test_an_unexpected_domain_error_while_building_is_a_row_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from veritas.catalogs import importer  # noqa: PLC0415 (solo esta prueba)

    def broken(*args: object, **kwargs: object) -> object:
        raise CatalogError(
            Diagnostic.create(
                DiagnosticCode.INPUT_OUT_OF_RANGE, "catalog.invalid_warning", {"key": "x"}
            )
        )

    monkeypatch.setattr(importer, "_build", broken)
    result = parse_text(tmp_path, [GOOD, GOOD.replace("P-1", "P-2")])
    assert [only(row) for row in result.rows] == ["catalog.row_invalid"] * 2
    assert "x" in str(result.rows[0].issues[0].diagnostic.params_dict()["detail"])


# --- Serie por defecto y radio de acuerdo -------------------------------------------------------

STEEL_SECTIONS_MAPPING = (
    Path(__file__).parent / "data" / "catalog_mapping_steel_sections_example.json"
)
# Cabecera de ipe/hea/heb/hem en steel-sections 0.2.1 (solo los nombres, sin datos).
STEEL_SECTIONS_HEADER = (
    "section,mass_kg_m,mass_lb_ft,h_mm,b_mm,tw_mm,tf_mm,r_mm,area_cm2,iy_cm4,iz_cm4"
)


def plate_area_mm2(h: float, b: float, tw: float, tf: float, r: float) -> float:
    return 2 * b * tf + (h - 2 * tf) * tw + (4 - math.pi) * r * r


def test_default_series_fills_in_when_there_is_no_series_column(tmp_path: Path) -> None:
    mapping = parse_mapping(STEEL_SECTIONS_MAPPING.read_text(encoding="utf-8"))
    area = plate_area_mm2(200, 100, 5, 8, 10) / 100  # cm²
    mass = area * 0.785  # kg/m con ρ = 7850 kg/m³
    rows = [
        f"SINT-A,{mass:.4f},4.7,200,100,5,8,10,{area:.4f},1500,140",
        f"SINT-B,{mass * 1.05:.4f},4.7,200,100,5,8,10,{area * 1.05:.4f},1500,140",  # A +5 %
    ]
    path = write(tmp_path, "ss.csv", "\n".join([STEEL_SECTIONS_HEADER, *rows]) + "\n")
    batch = parse_batch(read_source(path), mapping, INFO)
    first, second = batch.rows
    assert first.data is not None
    assert first.data.series == "steel-sections (I europeos)"
    assert first.key == ("steel-sections (I europeos)", "SINT-A")
    assert first.data.warnings == ()  # con r, el área de la I cuadra
    assert second.data is not None
    assert second.data.warnings == (WARN_AREA,)  # la masa es coherente con su A


def test_the_radius_is_what_removes_the_area_warning() -> None:
    dims = {"h": 0.2, "b": 0.1, "tw": 0.005, "tf": 0.008}
    plates = 2 * 0.1 * 0.008 + (0.2 - 2 * 0.008) * 0.005
    with_radius = plates + (4 - math.pi) * 0.01**2
    rounded = {**dims, "r": 0.01}
    assert WARN_AREA in warnings(dimensions=dims, area=with_radius, mass=None)
    assert WARN_AREA not in warnings(dimensions=rounded, area=with_radius, mass=None)
    assert WARN_AREA in warnings(dimensions=rounded, area=with_radius * 1.02, mass=None)


def test_a_series_column_wins_over_the_default(tmp_path: Path) -> None:
    mapping = parse_mapping(
        json.dumps(
            {
                "format": "veritas-catalog-mapping/1",
                "columns": {
                    "S": {"field": "series"},
                    "D": {"field": "designation"},
                    "A": {"field": "area", "unit": "m²"},
                    "Iy": {"field": "iy", "unit": "m⁴"},
                    "Iz": {"field": "iz", "unit": "m⁴"},
                },
                "default_series": "OMISIÓN",
                "default_family": "I",
            }
        )
    )
    source = read_source(write(tmp_path, "s.csv", "S,D,A,Iy,Iz\nREAL,P,1,1,1\n,Q,1,1,1\n"))
    first, second = (row.data for row in parse_batch(source, mapping, INFO).rows)
    assert first is not None
    assert second is not None
    assert (first.series, second.series) == ("REAL", "OMISIÓN")
