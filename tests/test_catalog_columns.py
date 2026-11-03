"""Mapeo de columnas, números estrictos y unidades del importador (T-022 E2)."""

import json
from pathlib import Path

import pytest

from catalog_support import assert_renders, message_key
from veritas.catalogs.columns import (
    DEFAULT_METHOD,
    MAPPING_FORMAT,
    NATIVE_METHOD,
    REQUIRED_FIELDS,
    check_header,
    field_dimension,
    is_field,
    native_mapping,
    parse_mapping,
    parse_number,
    resolve_family,
    to_si,
)
from veritas.catalogs.errors import CatalogError
from veritas.catalogs.model import ProfileFamily, sha256_of
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticError


def mapping_doc(**changes: object) -> dict[str, object]:
    document: dict[str, object] = {
        "format": MAPPING_FORMAT,
        "columns": {
            "Serie": {"field": "series"},
            "Nombre": {"field": "designation"},
            "Tipo": {"field": "family"},
            "A": {"field": "area", "unit": "cm²"},
            "Iy": {"field": "iy", "unit": "cm4"},
            "Iz": {"field": "iz", "unit": "mm⁴"},
            "h": {"field": "dim_h", "unit": "mm"},
        },
    }
    return {**document, **changes}


def parse(document: object) -> object:
    return parse_mapping(json.dumps(document))


def fails(document: object, key: str) -> CatalogError:
    with pytest.raises(CatalogError) as error:
        parse_mapping(document if isinstance(document, str) else json.dumps(document))
    assert message_key(error) == key
    assert_renders(error.value.diagnostic)
    return error.value


# --- Mapeo válido -------------------------------------------------------------------------------


def test_a_valid_mapping_resolves_units_to_canonical_symbols() -> None:
    mapping = parse_mapping(json.dumps(mapping_doc(steel=True, default_family="I")))
    assert {spec.column: spec.unit for spec in mapping.columns}["Iy"] == "cm⁴"  # alias cm4
    assert mapping.steel
    assert mapping.default_family is ProfileFamily.I
    assert mapping.method == DEFAULT_METHOD
    assert mapping.spec_for("dim_h") is not None
    assert mapping.spec_for("torsion_constant") is None


def test_the_canonical_document_ignores_layout_and_order() -> None:
    document = mapping_doc(family_aliases={"B": "I", "a": "U"})
    spaced = parse_mapping(json.dumps(document, indent=4))
    columns = dict(reversed(list(document["columns"].items())))  # type: ignore[attr-defined]
    reordered = parse_mapping(json.dumps({**document, "columns": columns}))
    assert spaced.document == reordered.document
    assert spaced.sha256 == reordered.sha256 == sha256_of(spaced.document)
    assert json.loads(spaced.document)["family_aliases"] == {"a": "U", "b": "I"}


def test_a_different_unit_changes_the_hash() -> None:
    other = mapping_doc()
    other["columns"]["A"]["unit"] = "mm²"  # type: ignore[index]
    assert (
        parse_mapping(json.dumps(other)).sha256 != parse_mapping(json.dumps(mapping_doc())).sha256
    )


def test_aliases_are_matched_without_case_and_enum_values_win() -> None:
    mapping = parse_mapping(json.dumps(mapping_doc(family_aliases={"doble t": "I", "u": "T"})))
    assert resolve_family(" DOBLE T ", mapping) is ProfileFamily.I
    assert resolve_family("u", mapping) is ProfileFamily.U  # el valor del enumerado manda
    assert resolve_family("shs", mapping) is ProfileFamily.SHS
    assert resolve_family("tubo", mapping) is None


# --- Mapeo inválido ------------------------------------------------------------------------------


def test_invalid_json_and_strictness() -> None:
    fails("{no es json", "catalog.mapping_json")
    fails('{"format": "x", "format": "y"}', "catalog.mapping_json")  # clave repetida
    fails('{"format": NaN}', "catalog.mapping_json")
    fails("[1]", "catalog.mapping_structure")


@pytest.mark.parametrize(
    "document",
    [
        {"columns": {}},
        {**mapping_doc(), "extra": 1},
        mapping_doc(columns={}),
        mapping_doc(columns={"A": "area"}),
        mapping_doc(columns={"A": {"field": "area", "unit": "cm²", "extra": 1}}),
        mapping_doc(columns={" ": {"field": "series"}}),
        mapping_doc(steel="si"),
        mapping_doc(method=""),
        mapping_doc(family_aliases=[]),
        mapping_doc(family_aliases={" ": "I"}),
    ],
)
def test_structural_defects(document: object) -> None:
    fails(document, "catalog.mapping_structure")


def test_unknown_format_version() -> None:
    error = fails(mapping_doc(format="veritas-catalog-mapping/9"), "catalog.mapping_format")
    assert error.diagnostic.params_dict()["found"] == "veritas-catalog-mapping/9"


def test_unknown_field_and_repeated_field() -> None:
    columns = {**mapping_doc()["columns"], "X": {"field": "peso"}}  # type: ignore[dict-item]
    fails(mapping_doc(columns=columns), "catalog.mapping_field_unknown")
    columns = {**mapping_doc()["columns"], "A2": {"field": "area", "unit": "m²"}}  # type: ignore[dict-item]
    error = fails(mapping_doc(columns=columns), "catalog.mapping_field_repeated")
    assert error.diagnostic.params_dict()["field"] == "area"
    fails(mapping_doc(family_aliases={"a": "I", " A ": "U"}), "catalog.mapping_field_repeated")


@pytest.mark.parametrize(
    "entry",
    [
        {"field": "area"},  # sin unidad
        {"field": "area", "unit": 3},
        {"field": "area", "unit": "furlong"},  # desconocida
        {"field": "area", "unit": "mm"},  # longitud en lugar de área
        {"field": "iy", "unit": "cm²"},
        {"field": "linear_mass", "unit": "kg"},
        {"field": "dim_h", "unit": "cm²"},
        {"field": "series", "unit": "mm"},  # un texto no lleva unidad
    ],
)
def test_unit_defects(entry: dict[str, object]) -> None:
    base: dict[str, dict[str, object]] = mapping_doc()["columns"]  # type: ignore[assignment]
    columns = {name: spec for name, spec in base.items() if spec["field"] != entry["field"]}
    columns["Z"] = entry
    fails(mapping_doc(columns=columns), "catalog.mapping_unit")


@pytest.mark.parametrize("missing", REQUIRED_FIELDS)
def test_required_fields_must_be_mapped(missing: str) -> None:
    columns = {
        name: spec
        for name, spec in mapping_doc()["columns"].items()  # type: ignore[attr-defined]
        if spec["field"] != missing
    }
    error = fails(mapping_doc(columns=columns), "catalog.mapping_required")
    assert error.diagnostic.params_dict() == {"field": missing}


def test_family_needs_a_column_or_a_default() -> None:
    columns = {k: v for k, v in mapping_doc()["columns"].items() if v["field"] != "family"}  # type: ignore[attr-defined]
    error = fails(mapping_doc(columns=columns), "catalog.mapping_required")
    assert error.diagnostic.params_dict() == {"field": "family"}
    assert parse_mapping(json.dumps(mapping_doc(columns=columns, default_family="OTRO")))


@pytest.mark.parametrize("value", ["Z", 3, ""])
def test_invalid_families(value: object) -> None:
    fails(mapping_doc(default_family=value), "catalog.mapping_family")
    fails(mapping_doc(family_aliases={"a": value}), "catalog.mapping_family")


# --- Formato nativo y cabecera -----------------------------------------------------------------


def test_native_mapping_uses_si_and_requires_the_mandatory_columns() -> None:
    header = ["series", "designation", "family", "area", "iy", "iz", "linear_mass", "dim_h"]
    mapping = native_mapping(header)
    units = {spec.field: spec.unit for spec in mapping.columns}
    assert units == {
        "series": None,
        "designation": None,
        "family": None,
        "area": "m²",
        "iy": "m⁴",
        "iz": "m⁴",
        "linear_mass": "kg/m",
        "dim_h": "m",
    }
    assert mapping.method == NATIVE_METHOD


def test_native_mapping_rejects_unknown_columns_and_missing_ones() -> None:
    with pytest.raises(CatalogError) as error:
        native_mapping(["series", "designation", "area", "iy", "Iz"])  # «Iz» mal escrito
    assert message_key(error) == "catalog.mapping_column_unknown"
    assert_renders(error.value.diagnostic)
    with pytest.raises(CatalogError) as error:
        native_mapping(["series", "designation", "family", "area", "iy"])
    assert message_key(error) == "catalog.mapping_required"


def test_header_checks() -> None:
    mapping = parse_mapping(json.dumps(mapping_doc()))
    header = [spec.column for spec in mapping.columns]
    check_header(mapping, [*header, "extra"])  # las columnas no mapeadas se ignoran
    with pytest.raises(CatalogError) as error:
        check_header(mapping, header[:-1])
    assert message_key(error) == "catalog.mapping_column_missing"
    assert_renders(error.value.diagnostic)
    with pytest.raises(CatalogError) as error:
        check_header(mapping, [*header, "A"])
    assert message_key(error) == "catalog.import_duplicate_column"
    assert_renders(error.value.diagnostic)


# --- Números ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "decimal", "expected"),
    [
        ("123", ".", 123.0),
        ("1.5", ".", 1.5),
        (" -2e-3 ", ".", -2e-3),
        ("+.5", ".", 0.5),
        ("5.", ".", 5.0),
        ("1E3", ".", 1000.0),
        ("1,5", ",", 1.5),
        (",5", ",", 0.5),
        ("1,5e2", ",", 150.0),
        ("0", ".", 0.0),
    ],
)
def test_strict_numbers(text: str, decimal: str, expected: float) -> None:
    assert parse_number(text, decimal) == expected


@pytest.mark.parametrize(
    ("text", "decimal"),
    [
        ("", "."),
        (" ", "."),
        ("abc", "."),
        ("nan", "."),
        ("NaN", "."),
        ("inf", "."),
        ("-inf", "."),
        ("infinity", "."),
        ("1e999", "."),  # desborda a infinito
        ("1_000", "."),
        ("0x10", "."),
        ("1 000", "."),
        ("1,000", "."),  # separador de miles o coma sin declarar
        ("1.5", ","),  # punto con decimal «,»
        ("1.000,5", ","),
        ("1..5", "."),
        ("--1", "."),
        ("1e", "."),
        ("٣", "."),  # dígito no ASCII
        ("1,5,2", ","),
    ],
)
def test_rejected_numbers(text: str, decimal: str) -> None:
    with pytest.raises(ValueError):  # noqa: PT011 (el mensaje es la propia celda, a veces vacía)
        parse_number(text, decimal)


# --- Unidades ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("number", "unit", "expected"),
    [
        (100.0, "mm", 0.1),
        (12.5, "cm²", 12.5e-4),
        (155.4357, "cm⁴", 155.4357e-8),
        (2.0, "mm⁴", 2e-12),
        (7.85, "kg/m", 7.85),
        (3.0, "m⁴", 3.0),
    ],
)
def test_conversion_to_si(number: float, unit: str, expected: float) -> None:
    assert to_si(number, unit) == pytest.approx(expected, rel=1e-15)


def test_unrepresentable_values_are_diagnostics() -> None:
    with pytest.raises(DiagnosticError):
        to_si(1e-320, "mm⁴")  # el producto llega a 0 o a subnormal
    with pytest.raises(DiagnosticError):
        to_si(1e308, "kN")  # desborda a infinito


def test_field_helpers() -> None:
    assert is_field("dim_tw")
    assert not is_field("dim_")
    assert not is_field("dim_H")
    assert not is_field("peso")
    assert field_dimension("area") == u.AREA
    assert field_dimension("dim_h") == u.LENGTH
    assert field_dimension("series") is None


def test_default_series_replaces_the_series_column_requirement() -> None:
    columns = {k: v for k, v in mapping_doc()["columns"].items() if v["field"] != "series"}  # type: ignore[attr-defined]
    error = fails(mapping_doc(columns=columns), "catalog.mapping_required")
    assert error.diagnostic.params_dict() == {"field": "series"}
    mapping = parse_mapping(json.dumps(mapping_doc(columns=columns, default_series=" Serie X ")))
    assert mapping.default_series == "Serie X"
    assert json.loads(mapping.document)["default_series"] == "Serie X"


@pytest.mark.parametrize("value", ["", "  ", 3, ["a"]])
def test_invalid_default_series(value: object) -> None:
    fails(mapping_doc(default_series=value), "catalog.mapping_structure")


def test_the_steel_sections_example_mapping_is_valid_and_has_no_data() -> None:
    path = Path(__file__).parent / "data" / "catalog_mapping_steel_sections_example.json"
    mapping = parse_mapping(path.read_text(encoding="utf-8"))
    assert mapping.default_series == "steel-sections (I europeos)"
    assert mapping.steel
    assert {spec.field for spec in mapping.columns} >= {"designation", "area", "iy", "iz", "dim_r"}
    assert path.stat().st_size < 2000  # solo nombres de columna: ningún dato de perfiles
