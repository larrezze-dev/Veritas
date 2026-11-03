"""Consulta del catálogo (T-023 E1): rangos, filtros, orden, referencias y comparación (puro)."""

from collections.abc import Callable
from functools import partial

import pytest

from catalog_support import (
    PROVENANCE,
    assert_renders,
    make_properties,
    message_key,
    uid,
)
from veritas.catalogs.errors import CatalogError
from veritas.catalogs.model import (
    CatalogInfo,
    CatalogProfile,
    ProfileData,
    ProfileDimension,
    ProfileFamily,
)
from veritas.catalogs.query import (
    Criteria,
    Entry,
    Order,
    ProfileRef,
    Range,
    compare,
    dimension_field,
    parse_order,
    parse_range,
    parse_reference,
    search,
    sort_entries,
    value_of,
)

_counter = iter(range(1, 10_000))


def info(name: str = "Cat") -> CatalogInfo:
    return CatalogInfo(
        id=uid(900),
        name=name,
        description="",
        source="s",
        license="l",
        edition="e",
        protected=False,
    )


def entry(  # noqa: PLR0913 (campos opcionales por nombre, para variar un caso)
    designation: str,
    *,
    series: str = "S1",
    area: float | None = 1.0e-3,
    iy: float | None = 2.0e-6,
    iz: float | None = 3.0e-7,
    torsion: float | None = None,
    mass: float | None = 7.85,
    family: ProfileFamily = ProfileFamily.I,
    h: float | None = 0.1,
    catalog: str = "Cat",
    revision: int = 1,
) -> Entry:
    dims = () if h is None else (ProfileDimension("h", h),)
    data = ProfileData(
        series=series,
        designation=designation,
        family=family,
        dimensions=dims,
        properties=make_properties(area=area, iy=iy, iz=iz, torsion=torsion, mass=mass),
        provenance=PROVENANCE,
    )
    catalog_info = info(catalog)
    profile = CatalogProfile(
        id=uid(next(_counter)), revision=revision, catalog_id=catalog_info.id, data=data
    )
    return Entry(catalog_info, profile)


def fails(call: Callable[[], object], key: str) -> CatalogError:
    with pytest.raises(CatalogError) as error:
        call()
    assert message_key(error) == key
    assert_renders(error.value.diagnostic)
    return error.value


# --- parse_range -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "field", "expected"),
    [
        ("20..40 cm2", "area", Range(20e-4, 40e-4)),
        ("20..40 cm²", "area", Range(20e-4, 40e-4)),
        ("20.. cm2", "area", Range(20e-4, None)),
        ("..40 cm2", "area", Range(None, 40e-4)),
        (">=20 cm2", "area", Range(20e-4, None)),
        ("<=40 cm2", "area", Range(None, 40e-4)),
        ("  20 .. 40   cm2 ", "area", Range(20e-4, 40e-4)),
        ("1000..2000 cm4", "iy", Range(1000e-8, 2000e-8)),
        ("0.5..1.5e3 mm4", "torsion_constant", Range(0.5e-12, 1.5e3 * 1e-12)),
        ("..30 kg/m", "linear_mass", Range(None, 30.0)),
        ("200..300 mm", "dim_h", Range(0.2, 0.3)),
        ("0..5 cm2", "area", Range(0.0, 5e-4)),
        (".5..2 m", "dim_b", Range(0.5, 2.0)),
    ],
)
def test_ranges_are_parsed_in_si(text: str, field: str, expected: Range) -> None:
    got = parse_range(text, field, "--x")
    for actual, wanted in ((got.minimum, expected.minimum), (got.maximum, expected.maximum)):
        if wanted is None:
            assert actual is None
        else:
            assert actual == pytest.approx(wanted, rel=1e-12)


@pytest.mark.parametrize(
    ("text", "field", "reason"),
    [
        ("", "area", "use A..B"),
        ("..", "area", "use A..B"),
        ("20 cm2", "area", "use A..B"),
        ("20..40", "area", "use A..B"),
        ("20..40cm2", "area", "use A..B"),
        ("-5..40 cm2", "area", "use A..B"),
        ("nan..40 cm2", "area", "use A..B"),
        ("1_0..40 cm2", "area", "use A..B"),
        ("40..20 cm2", "area", "mayor que el máximo"),
        ("20..40 parsec", "area", "unidad desconocida"),
        ("20..40 cm4", "area", "dimensión incompatible"),
        ("20..40 mm", "iy", "dimensión incompatible"),
        ("20..40 kg", "linear_mass", "dimensión incompatible"),
        ("1e999..2e999 cm2", "area", "fuera de rango"),
        ("1e400.. cm2", "area", "fuera de rango"),
    ],
)
def test_malformed_ranges_are_diagnosed_with_the_option_and_value(
    text: str, field: str, reason: str
) -> None:
    error = fails(lambda: parse_range(text, field, "--opcion"), "catalog.search_range")
    params = error.diagnostic.params_dict()
    assert params["option"] == "--opcion"
    assert params["value"] == text
    assert reason in str(params["reason"])


def test_a_text_field_has_no_range() -> None:
    fails(lambda: parse_range("1..2 m", "series", "--x"), "catalog.search_range")


def test_dimension_names_follow_the_importer_rule() -> None:
    assert dimension_field("h") == "dim_h"
    assert dimension_field("tf_2") == "dim_tf_2"
    for bad in ("", "H", "2h", "a b", "a-b"):
        fails(partial(dimension_field, bad), "catalog.search_range")


# --- Límites inclusivos ------------------------------------------------------------------------


def test_range_bounds_are_inclusive_and_exact_at_the_boundary() -> None:
    interval = parse_range("20..40 cm2", "area", "--area")
    assert interval.contains(20e-4)
    assert interval.contains(40e-4)
    assert interval.contains(30e-4)
    assert not interval.contains(20e-4 * (1 - 1e-9))  # justo por debajo del mínimo
    assert not interval.contains(40e-4 * (1 + 1e-9))  # justo por encima del máximo


def test_the_same_boundary_in_another_unit_is_still_inside() -> None:
    """2000 mm² y 20 cm² son el mismo límite: la conversión no lo saca del rango."""
    assert parse_range("20..40 cm2", "area", "--area").contains(2000e-6)
    assert parse_range("2000..4000 mm2", "area", "--area").contains(20e-4)
    assert parse_range("2000..4000 mm2", "area", "--area").contains(40e-4)


def test_open_ranges() -> None:
    assert Range(None, None).contains(1e30)
    assert Range(5.0, None).contains(5.0)
    assert not Range(5.0, None).contains(4.999)
    assert Range(None, 5.0).contains(5.0)
    assert not Range(None, 5.0).contains(5.001)


# --- Búsqueda ----------------------------------------------------------------------------------


def names(result_entries: tuple[Entry, ...]) -> list[str]:
    return [e.profile.designation for e in result_entries]


def test_search_without_criteria_returns_everything_in_input_order() -> None:
    entries = [entry("B"), entry("A")]
    result = search(entries, Criteria())
    assert (result.examined, names(result.matches)) == (2, ["B", "A"])
    assert (result.excluded_missing, result.missing) == (0, ())


def test_text_filters_are_case_insensitive_substrings() -> None:
    entries = [
        entry("IPE 200", series="Europeas"),
        entry("HEA 200", series="Europeas"),
        entry("W8x10", series="Americanas"),
    ]
    assert names(search(entries, Criteria(series="europ")).matches) == ["IPE 200", "HEA 200"]
    assert names(search(entries, Criteria(designation="ipe")).matches) == ["IPE 200"]
    assert names(search(entries, Criteria(series="EUROP", designation="200")).matches) == [
        "IPE 200",
        "HEA 200",
    ]
    assert names(search(entries, Criteria(designation="zzz")).matches) == []


def test_family_filter() -> None:
    entries = [entry("A"), entry("B", family=ProfileFamily.RHS)]
    assert names(search(entries, Criteria(family=ProfileFamily.RHS)).matches) == ["B"]


def test_numeric_filters_include_the_boundary_and_exclude_just_outside() -> None:
    interval = parse_range("20..40 cm2", "area", "--area")
    entries = [
        entry("below", area=19.999e-4),
        entry("low", area=20e-4),
        entry("mid", area=30e-4),
        entry("high", area=40e-4),
        entry("above", area=40.001e-4),
    ]
    result = search(entries, Criteria(ranges=(("area", interval),)))
    assert names(result.matches) == ["low", "mid", "high"]


def test_all_numeric_criteria_must_hold() -> None:
    entries = [
        entry("both", area=30e-4, iy=1500e-8),
        entry("only_area", area=30e-4, iy=500e-8),
        entry("only_iy", area=50e-4, iy=1500e-8),
    ]
    criteria = Criteria(
        ranges=(
            ("area", parse_range("20..40 cm2", "area", "--area")),
            ("iy", parse_range("1000..2000 cm4", "iy", "--iy")),
        )
    )
    assert names(search(entries, criteria).matches) == ["both"]


def test_an_absent_property_is_never_a_match_and_is_counted_apart() -> None:
    """Ausente no es 0: ni cumple «..30 kg/m» ni «0..5 cm4», y se cuenta por propiedad."""
    entries = [entry("with", torsion=2e-8), entry("without", torsion=None)]  # J = 2 cm4
    for text, expected in (("..1 cm4", []), ("0..1 cm4", []), (">=0 cm4", ["with"])):
        interval = parse_range(text, "torsion_constant", "--j")
        result = search(entries, Criteria(ranges=(("torsion_constant", interval),)))
        assert names(result.matches) == expected, text
        assert (result.excluded_missing, result.missing) == (1, (("torsion_constant", 1),)), text


def test_a_profile_outside_another_range_is_not_reported_as_missing() -> None:
    """Solo se cuentan los que cumplirían todo lo comprobable: el resto se descarta sin más."""
    entries = [
        entry("out_and_absent", area=50e-4, torsion=None),
        entry("in_and_absent", area=30e-4, torsion=None),
    ]
    criteria = Criteria(
        ranges=(
            ("area", parse_range("20..40 cm2", "area", "--area")),
            ("torsion_constant", Range(0.0, None)),
        )
    )
    result = search(entries, criteria)
    assert result.matches == ()
    assert (result.excluded_missing, result.missing) == (1, (("torsion_constant", 1),))


def test_missing_counts_are_per_field() -> None:
    entries = [entry("a", torsion=None, mass=None), entry("b", torsion=None)]
    criteria = Criteria(
        ranges=(("torsion_constant", Range(0.0, None)), ("linear_mass", Range(0.0, None)))
    )
    result = search(entries, criteria)
    assert result.excluded_missing == 2
    assert result.missing == (("linear_mass", 1), ("torsion_constant", 2))


def test_dimension_filters_and_profiles_without_that_dimension() -> None:
    entries = [entry("a", h=0.2), entry("b", h=0.5), entry("c", h=None)]
    criteria = Criteria(ranges=(("dim_h", parse_range("150..300 mm", "dim_h", "--dim h")),))
    result = search(entries, criteria)
    assert names(result.matches) == ["a"]
    assert (result.excluded_missing, result.missing) == (1, (("dim_h", 1),))


def test_value_of_reads_properties_and_dimensions() -> None:
    e = entry("a", area=2e-3, h=0.3)
    assert value_of(e.profile, "area") == 2e-3
    assert value_of(e.profile, "torsion_constant") is None
    assert value_of(e.profile, "dim_h") == 0.3
    assert value_of(e.profile, "dim_zz") is None


# --- Orden -------------------------------------------------------------------------------------


def test_default_order_is_catalog_series_designation() -> None:
    entries = [
        entry("B", catalog="Zeta"),
        entry("B", catalog="alfa"),
        entry("A", catalog="alfa", series="S2"),
        entry("C", catalog="alfa", series="S1"),
    ]
    got = [(e.catalog.name, e.profile.series, e.profile.designation) for e in sort_entries(entries)]
    assert got == [
        ("alfa", "S1", "B"),
        ("alfa", "S1", "C"),
        ("alfa", "S2", "A"),
        ("Zeta", "S1", "B"),
    ]


def test_numeric_order_ascending_and_descending_with_absent_last() -> None:
    entries = [
        entry("c", area=3e-3),
        entry("none1", area=None),
        entry("a", area=1e-3),
        entry("b", area=2e-3),
        entry("none0", area=None),
    ]
    assert names(sort_entries(entries, Order("area"))) == ["a", "b", "c", "none0", "none1"]
    assert names(sort_entries(entries, Order("area", descending=True))) == [
        "c",
        "b",
        "a",
        "none0",
        "none1",
    ]


def test_ties_keep_the_natural_order_in_both_directions() -> None:
    entries = [entry("z", area=1e-3), entry("a", area=1e-3), entry("m", area=1e-3)]
    assert names(sort_entries(entries, Order("area"))) == ["a", "m", "z"]
    assert names(sort_entries(entries, Order("area", descending=True))) == ["a", "m", "z"]


def test_text_and_dimension_order() -> None:
    entries = [entry("b", series="Y", h=0.3), entry("a", series="x", h=0.1), entry("c", h=None)]
    assert names(sort_entries(entries, Order("series"))) == ["c", "a", "b"]
    assert [e.profile.series for e in sort_entries(entries, Order("series"))] == ["S1", "x", "Y"]
    assert [e.profile.series for e in sort_entries(entries, Order("series", True))] == [
        "Y",
        "x",
        "S1",
    ]
    assert names(sort_entries(entries, Order("dim_h", True))) == ["b", "a", "c"]


def test_sorting_does_not_change_the_input() -> None:
    entries = [entry("b"), entry("a")]
    sort_entries(entries, Order("series"))
    assert names(tuple(entries)) == ["b", "a"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("area", Order("area")),
        ("AREA:desc", Order("area", True)),
        (" masa : asc ", Order("linear_mass")),
        ("j:desc", Order("torsion_constant", True)),
        ("serie", Order("series")),
        ("designacion:desc", Order("designation", True)),
        ("dim_h", Order("dim_h")),
        ("DIM_H:desc", Order("dim_h", True)),
    ],
)
def test_parse_order(text: str, expected: Order) -> None:
    assert parse_order(text) == expected


@pytest.mark.parametrize("text", ["", "peso", "area:up", "area:", "dim_", "area:desc:x"])
def test_invalid_order_is_diagnosed(text: str) -> None:
    error = fails(lambda: parse_order(text), "catalog.search_order")
    assert error.diagnostic.params_dict()["value"] == text


# --- Referencias -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Cat/S1/IPE 200", ProfileRef("Cat", "S1", "IPE 200")),
        ("Cat/S1/IPE 200@3", ProfileRef("Cat", "S1", "IPE 200", 3)),
        (" Cat / S1 / IPE 200@2 ", ProfileRef("Cat", "S1", "IPE 200", 2)),
        ("Cat/S1/L 50/5", ProfileRef("Cat", "S1", "L 50/5")),
        ("Cat/S1/a@b", ProfileRef("Cat", "S1", "a@b")),
    ],
)
def test_parse_reference(text: str, expected: ProfileRef) -> None:
    assert parse_reference(text) == expected


@pytest.mark.parametrize(
    "text", ["", "Cat", "Cat/S1", "/S1/D", "Cat//D", "Cat/S1/", "Cat/S1/@2", "Cat/S1/D@0"]
)
def test_invalid_reference_is_diagnosed(text: str) -> None:
    error = fails(lambda: parse_reference(text), "catalog.reference_invalid")
    assert error.diagnostic.params_dict()["value"] == text


def test_reference_text_round_trips() -> None:
    ref = ProfileRef("Cat", "S1", "IPE 200", 4)
    assert parse_reference(str(ref)) == ref
    assert str(ProfileRef("Cat", "S1", "IPE 200")) == "Cat/S1/IPE 200"


# --- Comparación -------------------------------------------------------------------------------


def test_comparison_values_and_exact_ratios_against_the_first() -> None:
    a = entry("A", area=1e-3, iy=2e-6, iz=4e-7, mass=8.0, h=0.1)
    b = entry("B", area=3e-3, iy=1e-5, iz=2e-7, mass=24.0, h=0.2)
    comparison = compare([a, b])
    rows = {row.label: row for row in comparison.rows}
    assert rows["A"].values == (1e-3, 3e-3)
    assert rows["A"].ratios == (None, 3e-3 / 1e-3)
    assert rows["Iy"].ratios[1] == pytest.approx(5.0)
    assert rows["Iz"].ratios[1] == pytest.approx(0.5)
    assert rows["m"].ratios[1] == pytest.approx(3.0)
    assert rows["h"].unit == "m"
    assert rows["h"].ratios[1] == pytest.approx(2.0)


def test_comparison_never_computes_a_ratio_with_an_absent_value() -> None:
    a = entry("A", torsion=None)
    b = entry("B", torsion=2e-8)
    c = entry("C", torsion=None)
    row = {r.label: r for r in compare([a, b, c]).rows}["J"]
    assert row.values == (None, 2e-8, None)
    assert row.ratios == (None, None, None)  # el primero no tiene J: no hay referencia
    row2 = {r.label: r for r in compare([b, a]).rows}["J"]
    assert row2.values == (2e-8, None)
    assert row2.ratios == (None, None)


def test_comparison_uses_the_union_of_dimensions_sorted() -> None:
    a = entry("A", h=0.1)
    data = a.profile.data
    b_data = ProfileData(
        series="S1",
        designation="B",
        family=ProfileFamily.I,
        dimensions=(ProfileDimension("b", 0.05), ProfileDimension("h", 0.2)),
        properties=data.properties,
        provenance=PROVENANCE,
    )
    b = Entry(
        a.catalog,
        CatalogProfile(id=uid(7000), revision=1, catalog_id=a.catalog.id, data=b_data),
    )
    labels = [row.label for row in compare([a, b]).rows]
    assert labels == ["A", "Iy", "Iz", "J", "m", "b", "h"]
    b_row = {r.label: r for r in compare([a, b]).rows}["b"]
    assert b_row.values == (None, 0.05)
    assert b_row.ratios == (None, None)


@pytest.mark.parametrize("count", [0, 1, 9])
def test_the_number_of_compared_profiles_is_limited(count: int) -> None:
    error = fails(lambda: compare([entry(f"P{i}") for i in range(count)]), "catalog.compare_count")
    assert error.diagnostic.params_dict()["count"] == count


def test_eight_profiles_are_accepted() -> None:
    assert len(compare([entry(f"P{i}") for i in range(8)]).entries) == 8


def test_the_same_profile_revision_twice_is_rejected() -> None:
    a = entry("A")
    fails(lambda: compare([a, a]), "catalog.compare_repeated")
    other_revision = Entry(
        a.catalog,
        CatalogProfile(id=a.profile.id, revision=2, catalog_id=a.catalog.id, data=a.profile.data),
    )
    assert len(compare([a, other_revision]).entries) == 2  # dos revisiones del mismo perfil
