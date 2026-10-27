"""Pruebas de perfiles de presentación (plan T-002, D4-B)."""

import pytest

from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError
from veritas.core.presentation import (
    BUILTIN_PROFILE_NAMES,
    BUILTIN_PROFILES,
    PROFILE_KN_M,
    PROFILE_N_MM,
    PROFILE_SI,
    PresentationProfile,
    QuantityKind,
    format_quantity,
)


@pytest.mark.parametrize("profile", BUILTIN_PROFILES, ids=lambda p: p.name)
def test_builtin_profiles_are_complete_and_coherent(profile: PresentationProfile) -> None:
    assert profile.builtin
    assert [kind for kind, _ in profile.units] == list(QuantityKind)
    for kind, unit in profile.units:
        assert unit.dimension == kind.dimension


def test_builtin_names() -> None:
    assert {"SI", "kN-m", "N-mm"} == BUILTIN_PROFILE_NAMES


def test_kinds_with_shared_dimension_are_distinct() -> None:
    load, stiffness = QuantityKind.FORCE_PER_LENGTH, QuantityKind.TRANSLATIONAL_STIFFNESS
    assert load.dimension == stiffness.dimension
    assert len({load, stiffness}) == 2
    assert QuantityKind.ROTATIONAL_STIFFNESS.dimension != QuantityKind.MOMENT.dimension


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        (PROFILE_SI, "12500.000 N·m"),
        (PROFILE_KN_M, "12.500 kN·m"),
        (PROFILE_N_MM, "12500000.000 N·mm"),
    ],
)
def test_format_moment(profile: PresentationProfile, expected: str) -> None:
    moment = u.Quantity.of(12.5, "kN·m")
    assert format_quantity(moment, QuantityKind.MOMENT, profile) == expected


def test_format_does_not_change_internal_value() -> None:
    inertia = u.Quantity.of(8356, "cm⁴")
    assert format_quantity(inertia, QuantityKind.SECOND_MOMENT, PROFILE_N_MM, 0) == "83560000 mm⁴"
    assert inertia.si_value == pytest.approx(8.356e-5, rel=1e-15, abs=0.0)


@pytest.mark.parametrize(
    ("quantity", "kind", "profile", "expected"),
    [
        (u.Quantity.of(8356, "cm⁴"), QuantityKind.SECOND_MOMENT, PROFILE_SI, "8.356e-05 m⁴"),
        (u.Quantity.of(4e-4, "rad"), QuantityKind.ANGLE, PROFILE_KN_M, "4.000e-04 rad"),
        (u.Quantity.of(0.4, "mm"), QuantityKind.LENGTH, PROFILE_KN_M, "4.000e-04 m"),
        (u.Quantity.of(-0.4, "mm"), QuantityKind.LENGTH, PROFILE_KN_M, "-4.000e-04 m"),
        (u.Quantity.of(-1e-9, "m"), QuantityKind.LENGTH, PROFILE_SI, "-1.000e-09 m"),
    ],
)
def test_format_never_shows_a_nonzero_value_as_zero(
    quantity: u.Quantity, kind: QuantityKind, profile: PresentationProfile, expected: str
) -> None:
    assert format_quantity(quantity, kind, profile) == expected


def test_format_zero_and_negative_zero() -> None:
    assert format_quantity(u.Quantity.of(0, "m"), QuantityKind.LENGTH) == "0.000 m"
    assert format_quantity(u.Quantity.of(-0.0, "m"), QuantityKind.LENGTH) == "0.000 m"
    assert format_quantity(u.Quantity.of(-2, "m"), QuantityKind.LENGTH, decimals=1) == "-2.0 m"
    assert format_quantity(u.Quantity.of(0.4, "m"), QuantityKind.LENGTH, decimals=0) == "4e-01 m"


def test_format_same_dimension_different_kinds() -> None:
    value = u.Quantity.of(5, "kN/m")
    assert format_quantity(value, QuantityKind.FORCE_PER_LENGTH, PROFILE_KN_M) == "5.000 kN/m"
    stiffness = format_quantity(value, QuantityKind.TRANSLATIONAL_STIFFNESS, PROFILE_N_MM)
    assert stiffness == "5.000 N/mm"


def test_format_rejects_wrong_kind() -> None:
    with pytest.raises(InputValueError) as exc_info:
        format_quantity(u.Quantity.of(1, "kN"), QuantityKind.LENGTH)
    assert exc_info.value.code is DiagnosticCode.UNITS_INCOMPATIBLE


@pytest.mark.parametrize("decimals", [-1, 16])
def test_format_rejects_decimals_out_of_range(decimals: int) -> None:
    with pytest.raises(InputValueError) as exc_info:
        format_quantity(u.Quantity.of(1, "m"), QuantityKind.LENGTH, decimals=decimals)
    assert exc_info.value.code is DiagnosticCode.UNSUPPORTED_CONFIGURATION


@pytest.mark.parametrize(
    "kwargs",
    [
        {"quantity": 1.0, "kind": QuantityKind.LENGTH},
        {"quantity": u.Quantity.of(1, "m"), "kind": "LENGTH"},
        {"quantity": u.Quantity.of(1, "m"), "kind": QuantityKind.LENGTH, "decimals": True},
        {"quantity": u.Quantity.of(1, "m"), "kind": QuantityKind.LENGTH, "profile": "SI"},
    ],
)
def test_format_rejects_invalid_types(kwargs: dict[str, object]) -> None:
    with pytest.raises(InputTypeError):
        format_quantity(**kwargs)  # type: ignore[arg-type]


def test_derive_creates_user_profile_without_touching_base() -> None:
    custom = PROFILE_KN_M.derive("Mi perfil", {QuantityKind.LENGTH: "mm"})
    assert custom.name == "Mi perfil"
    assert not custom.builtin
    assert custom.unit_for(QuantityKind.LENGTH).symbol == "mm"
    assert custom.unit_for(QuantityKind.FORCE).symbol == "kN"
    assert PROFILE_KN_M.unit_for(QuantityKind.LENGTH).symbol == "m"


@pytest.mark.parametrize("name", ["SI", "kN-m", "N-mm"])
def test_derive_rejects_reserved_names(name: str) -> None:
    with pytest.raises(InputValueError) as exc_info:
        PROFILE_SI.derive(name, {})
    assert exc_info.value.code is DiagnosticCode.UNSUPPORTED_CONFIGURATION


@pytest.mark.parametrize("name", ["", "  ", " espacio"])
def test_rejects_invalid_names(name: str) -> None:
    with pytest.raises(InputValueError):
        PROFILE_SI.derive(name, {})


def test_derive_rejects_unit_of_wrong_dimension() -> None:
    with pytest.raises(InputValueError) as exc_info:
        PROFILE_SI.derive("x", {QuantityKind.LENGTH: "kN"})
    assert exc_info.value.code is DiagnosticCode.UNITS_INCOMPATIBLE


def test_derive_rejects_unknown_unit_and_bad_kind() -> None:
    with pytest.raises(InputValueError) as exc_info:
        PROFILE_SI.derive("x", {QuantityKind.LENGTH: "pulgada"})
    assert exc_info.value.code is DiagnosticCode.UNITS_UNKNOWN
    with pytest.raises(InputTypeError):
        PROFILE_SI.derive("x", {"LENGTH": "mm"})  # type: ignore[dict-item]


def test_incomplete_or_duplicated_profile_is_rejected() -> None:
    partial = PROFILE_SI.units[:-1]
    with pytest.raises(InputValueError) as exc_info:
        PresentationProfile("parcial", partial)
    assert exc_info.value.code is DiagnosticCode.UNSUPPORTED_CONFIGURATION
    duplicated = (*PROFILE_SI.units, PROFILE_SI.units[0])
    with pytest.raises(InputValueError):
        PresentationProfile("doble", duplicated)


@pytest.mark.parametrize("builtin", [True, False])
@pytest.mark.parametrize("name", ["SI", "kN-m", "N-mm"])
def test_cannot_forge_builtin_profiles(name: str, builtin: bool) -> None:
    with pytest.raises(InputValueError) as exc_info:
        PresentationProfile(name, PROFILE_N_MM.units, builtin=builtin)
    assert exc_info.value.code is DiagnosticCode.UNSUPPORTED_CONFIGURATION


def test_cannot_mark_user_profile_as_builtin() -> None:
    with pytest.raises(InputValueError):
        PresentationProfile("Mío", PROFILE_N_MM.units, builtin=True)


def test_profile_is_immutable() -> None:
    with pytest.raises(AttributeError):
        PROFILE_SI.name = "otro"  # type: ignore[misc]
