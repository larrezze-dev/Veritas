"""Precisión por magnitud de los perfiles de presentación (plan T-020, E1: D7).

Los perfiles integrados conservan 3 decimales fijos en todas las magnitudes: su salida no
cambia. Un perfil derivado puede fijar cifras significativas por magnitud.
"""

from collections.abc import Callable

import pytest

from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError
from veritas.core.presentation import (
    BUILTIN_PROFILES,
    DEFAULT_PRECISION,
    PROFILE_SI,
    Precision,
    PrecisionMode,
    PresentationProfile,
    QuantityKind,
    format_quantity,
)

SIG = PrecisionMode.SIGNIFICANT
DEC = PrecisionMode.DECIMALS


def sig(digits: int) -> Precision:
    return Precision(SIG, digits)


def shown(value: float, digits: int, unit: str = "m") -> str:
    profile = PROFILE_SI.derive("t", {}, {QuantityKind.LENGTH: sig(digits)})
    return format_quantity(u.Quantity.of(value, unit), QuantityKind.LENGTH, profile)


@pytest.mark.parametrize("profile", BUILTIN_PROFILES, ids=lambda p: p.name)
def test_builtin_profiles_keep_three_decimals(profile: PresentationProfile) -> None:
    assert [kind for kind, _ in profile.precision] == list(QuantityKind)
    for kind in QuantityKind:
        assert profile.precision_for(kind) == DEFAULT_PRECISION == Precision(DEC, 3)
        quantity = u.Quantity.of(1.23456789, profile.unit_for(kind))
        assert format_quantity(quantity, kind, profile) == format_quantity(
            quantity, kind, profile, decimals=3
        )


def test_explicit_decimals_override_the_profile_precision() -> None:
    profile = PROFILE_SI.derive("t", {}, {QuantityKind.LENGTH: sig(2)})
    length = u.Quantity.of(1.23456, "m")
    assert format_quantity(length, QuantityKind.LENGTH, profile) == "1.2 m"
    assert format_quantity(length, QuantityKind.LENGTH, profile, decimals=4) == "1.2346 m"


@pytest.mark.parametrize(
    ("value", "digits", "expected"),
    [
        (0.0123456, 3, "0.0123 m"),
        (0.00123456, 3, "0.00123 m"),
        (1.23456e-5, 3, "1.23e-05 m"),  # bajo 1e-3: científica, nunca un cero
        (-1.23456e-5, 3, "-1.23e-05 m"),
        (9.996, 3, "10.0 m"),  # el redondeo cambia de decena
        (123456.0, 3, "123000 m"),
        (1234567.0, 3, "1230000 m"),
        (2.0, 1, "2 m"),
        (0.0, 3, "0.00 m"),
        (-0.0, 3, "0.00 m"),
        (0.0, 1, "0 m"),
        (1.5, 15, "1.50000000000000 m"),
    ],
)
def test_significant_figures(value: float, digits: int, expected: str) -> None:
    assert shown(value, digits) == expected


def test_significant_never_shows_a_nonzero_value_as_zero() -> None:
    for value in (1e-12, -1e-12, 4e-7):
        text = shown(value, 2)
        assert any(char in "123456789" for char in text.split(" ")[0]), text


def test_significant_converts_to_the_profile_unit_first() -> None:
    profile = PROFILE_SI.derive("t", {QuantityKind.LENGTH: "mm"}, {QuantityKind.LENGTH: sig(3)})
    assert format_quantity(u.Quantity.of(0.119e-3, "m"), QuantityKind.LENGTH, profile) == "0.119 mm"


def test_precision_is_per_magnitude() -> None:
    profile = PROFILE_SI.derive(
        "t", {}, {QuantityKind.DISPLACEMENT: sig(5), QuantityKind.ANGLE: Precision(DEC, 6)}
    )
    value = 0.0123456789
    shown_displacement = format_quantity(
        u.Quantity.of(value, "m"), QuantityKind.DISPLACEMENT, profile
    )
    assert shown_displacement == "0.012346 m"
    assert format_quantity(u.Quantity.of(value, "m"), QuantityKind.LENGTH, profile) == "0.012 m"
    assert format_quantity(u.Quantity.of(value, "rad"), QuantityKind.ANGLE, profile) == (
        "0.012346 rad"
    )


def test_derive_without_precision_keeps_the_base_precision_and_does_not_mutate_it() -> None:
    first = PROFILE_SI.derive("a", {}, {QuantityKind.FORCE: sig(4)})
    second = first.derive("b", {QuantityKind.LENGTH: "mm"})
    assert second.precision_for(QuantityKind.FORCE) == sig(4)
    assert PROFILE_SI.precision_for(QuantityKind.FORCE) == DEFAULT_PRECISION
    assert not first.builtin


def test_profiles_with_same_content_are_equal() -> None:
    explicit = {kind: DEFAULT_PRECISION for kind in QuantityKind}
    assert PROFILE_SI.derive("a", {}, explicit) == PROFILE_SI.derive("a", {})


@pytest.mark.parametrize(("mode", "digits"), [(SIG, 0), (SIG, 16), (SIG, -1), (DEC, -1), (DEC, 16)])
def test_precision_out_of_range_is_rejected(mode: PrecisionMode, digits: int) -> None:
    with pytest.raises(InputValueError) as info:
        Precision(mode, digits)
    assert info.value.code is DiagnosticCode.UNSUPPORTED_CONFIGURATION
    assert info.value.diagnostic.message_key == "presentation.invalid_precision"


def test_precision_accepts_the_range_limits() -> None:
    assert Precision(DEC, 0).digits == 0
    assert Precision(DEC, 15).digits == 15
    assert Precision(SIG, 1).digits == 1
    assert Precision(SIG, 15).digits == 15


def _bad_mode() -> object:
    return Precision("DECIMALS", 3)  # type: ignore[arg-type]


def _bad_bool() -> object:
    return Precision(DEC, True)


def _bad_float() -> object:
    return Precision(DEC, 2.5)  # type: ignore[arg-type]


def _bad_kind() -> object:
    return PROFILE_SI.derive("t", {}, {"LENGTH": sig(3)})  # type: ignore[dict-item]


def _bad_value() -> object:
    return PROFILE_SI.derive("t", {}, {QuantityKind.LENGTH: 3})  # type: ignore[dict-item]


@pytest.mark.parametrize("build", [_bad_mode, _bad_bool, _bad_float, _bad_kind, _bad_value])
def test_invalid_precision_types_are_rejected(build: Callable[[], object]) -> None:
    with pytest.raises(InputTypeError):
        build()


def test_profile_rejects_duplicate_precision_kinds() -> None:
    duplicated = ((QuantityKind.LENGTH, sig(3)), (QuantityKind.LENGTH, sig(4)))
    with pytest.raises(InputValueError) as info:
        PresentationProfile("t", PROFILE_SI.units, precision=duplicated)
    assert info.value.diagnostic.message_key == "presentation.duplicate_kind"


def test_builtin_names_stay_protected_with_precision() -> None:
    with pytest.raises(InputValueError):
        PROFILE_SI.derive("SI", {}, {QuantityKind.LENGTH: sig(3)})
