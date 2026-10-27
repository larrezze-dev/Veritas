"""Sección rectangular maciza: A, centroide, Iy, Iz, Iyz y J (plan T-004, T-SEC-001).

Referencias independientes del código: aritmética exacta con ``Fraction`` sobre los
floats ya convertidos a SI, integración de Green sobre los vértices, la tabla de β de
Timoshenko y Goodier y una evaluación directa de la serie de Saint-Venant en tanh.
"""

import dataclasses
import math
import sys
from fractions import Fraction
from uuid import uuid4

import pytest

from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError
from veritas.core.presentation import PROFILE_KN_M, QuantityKind, format_quantity
from veritas.core.sections.properties import PropertyStatus, SectionProperty
from veritas.core.sections.rectangular import (
    METHOD_CLOSED_FORM,
    METHOD_SAINT_VENANT,
    ODD_ZETA_5,
    rectangular_properties,
    saint_venant_sum,
    torsion_constant,
)
from veritas.core.sections.section import (
    RectangularParameters,
    SectionFamily,
    build_rectangular_section,
)
from veritas.core.units import LENGTH, SECOND_MOMENT, Quantity

ULP = sys.float_info.epsilon


def rel_close(actual: float, expected: Fraction | float, ulps: int) -> bool:
    """|actual - expected| <= ulps·eps·|expected| (comparación en aritmética exacta)."""
    exact = Fraction(expected)
    return abs(Fraction(actual) - exact) <= ulps * Fraction(ULP) * abs(exact)


def green_moments(vertices: list[tuple[float, float]]) -> tuple[Fraction, ...]:
    """A, ∫y dA, ∫z dA, ∫y² dA, ∫z² dA, ∫yz dA de un polígono (y, z) antihorario.

    Fórmulas de Green para polígonos, en aritmética exacta e independientes del código.
    """
    a = sy = sz = syy = szz = syz = Fraction(0)
    points = [(Fraction(y), Fraction(z)) for y, z in vertices]
    for (y0, z0), (y1, z1) in zip(points, points[1:] + points[:1], strict=True):
        cross = y0 * z1 - y1 * z0
        a += cross / 2
        sy += (y0 + y1) * cross / 6
        sz += (z0 + z1) * cross / 6
        syy += (y0 * y0 + y0 * y1 + y1 * y1) * cross / 12
        szz += (z0 * z0 + z0 * z1 + z1 * z1) * cross / 12
        syz += (y0 * z1 + 2 * y0 * z0 + 2 * y1 * z1 + y1 * z0) * cross / 24
    return a, sy, sz, syy, szz, syz


def rectangle(b: float, h: float, yc: float = 0.0, zc: float = 0.0) -> list[tuple[float, float]]:
    return [
        (yc - b / 2, zc - h / 2),
        (yc + b / 2, zc - h / 2),
        (yc + b / 2, zc + h / 2),
        (yc - b / 2, zc + h / 2),
    ]


def beta_direct(aspect: float, terms: int = 20_001) -> float:
    """β = J/(a·c³) con la serie directa en tanh, sumada de menor a mayor."""
    total = math.fsum(
        math.tanh(n * math.pi * aspect / 2) / n**5 for n in range(terms, 0, -2) if n % 2
    )
    return (1 - 192 / math.pi**5 / aspect * total) / 3


# --- Integrador de Green del propio test ------------------------------------------------


def test_green_integrator_sign_with_offset_rectangle() -> None:
    # Steiner: Iyz respecto al origen = A·yc·zc (con signo); Iy0 = Iy + A·zc².
    b, h, yc, zc = 2.0, 3.0, 5.0, -7.0
    a, sy, sz, syy, szz, syz = green_moments(rectangle(b, h, yc, zc))
    area = Fraction(b) * Fraction(h)
    assert a == area
    assert (sy, sz) == (area * 5, area * -7)
    assert syz == area * 5 * -7
    assert szz == Fraction(b) * Fraction(h) ** 3 / 12 + area * 49
    assert syy == Fraction(h) * Fraction(b) ** 3 / 12 + area * 25


# --- T-SEC-001 ---------------------------------------------------------------------------


def test_rectangle_closed_form_against_exact_and_green() -> None:  # T-SEC-001
    b, h = 0.2, 0.4
    props = rectangular_properties(b, h)
    fb, fh = Fraction(b), Fraction(h)
    assert rel_close(props.area.value or 0.0, fb * fh, 1)
    assert rel_close(props.iy.value or 0.0, fb * fh**3 / 12, 4)
    assert rel_close(props.iz.value or 0.0, fh * fb**3 / 12, 4)
    a, sy, sz, syy, szz, syz = green_moments(rectangle(b, h))
    assert rel_close(props.area.value or 0.0, a, 1)
    assert rel_close(props.iy.value or 0.0, szz, 4)  # Iy = ∫z² dA
    assert rel_close(props.iz.value or 0.0, syy, 4)  # Iz = ∫y² dA
    assert sy == sz == syz == 0
    for zero in (props.centroid_y, props.centroid_z, props.iyz):
        assert zero.value == 0.0
        assert math.copysign(1.0, zero.value) == 1.0  # +0.0, nunca -0.0
    assert math.isclose(props.area.value or 0.0, 0.08, rel_tol=1e-15)
    assert math.isclose(props.iy.value or 0.0, 1.0666666666666667e-3, rel_tol=1e-15)
    assert math.isclose(props.iz.value or 0.0, 2.6666666666666667e-4, rel_tol=1e-15)


def test_axis_swap_detector() -> None:
    props = rectangular_properties(0.1, 0.3)
    assert props.iy.value is not None
    assert props.iz.value is not None
    assert props.iy.value > props.iz.value  # h en z: el eje fuerte es y.
    assert math.isclose(props.iy.value / props.iz.value, 9.0, rel_tol=1e-14)


def test_property_traceability() -> None:
    props = rectangular_properties(0.2, 0.4)
    assert props.area.unit == "m²"
    assert props.centroid_y.unit == "m"
    assert {p.unit for p in (props.iy, props.iz, props.iyz, props.torsion_constant)} == {"m⁴"}
    for prop in (props.area, props.iy, props.iz, props.iyz, props.centroid_y):
        assert prop.status is PropertyStatus.CALCULADO
        assert prop.method == METHOD_CLOSED_FORM
        assert "b, h" in prop.origin
    assert props.torsion_constant.method == METHOD_SAINT_VENANT
    assert props.iy.quantity == Quantity(props.iy.value or 0.0, SECOND_MOMENT)


def test_millimetre_input_matches_metres_and_presents_in_cm4() -> None:
    in_m = build_rectangular_section(uuid4(), 1, b=Quantity.of(0.2, "m"), h=Quantity.of(0.4, "m"))
    in_mm = build_rectangular_section(
        uuid4(), 1, b=Quantity.of(200, "mm"), h=Quantity.of(400, "mm")
    )
    for name in ("area", "iy", "iz", "torsion_constant"):
        assert rel_close(
            getattr(in_mm.properties, name).value, getattr(in_m.properties, name).value, 8
        )
    iy = in_mm.properties.iy.quantity
    assert iy is not None
    assert format_quantity(iy, QuantityKind.SECOND_MOMENT, PROFILE_KN_M) == "106666.667 cm⁴"


# --- Torsión de Saint-Venant ------------------------------------------------------------


@pytest.mark.parametrize(
    ("aspect", "beta_table"),
    [(1.0, 0.141), (1.5, 0.196), (2.0, 0.229), (3.0, 0.263), (5.0, 0.291), (10.0, 0.312)],
)
def test_beta_against_timoshenko_goodier_table(aspect: float, beta_table: float) -> None:
    # Tabla con 3 cifras: tolerancia 5e-4.
    c = 0.1
    beta = torsion_constant(c, aspect * c) / (aspect * c * c**3)
    assert abs(beta - beta_table) <= 5e-4


@pytest.mark.parametrize("aspect", [1.0, 1.2, 1.5, 2.0, 3.0, 7.5, 10.0, 50.0])
def test_beta_against_direct_series(aspect: float) -> None:
    c = 1.0
    beta = torsion_constant(c, aspect * c) / (aspect * c**4)
    assert math.isclose(beta, beta_direct(aspect), rel_tol=1e-13)


def test_reference_values_square_and_two_to_one() -> None:
    assert math.isclose(torsion_constant(1.0, 1.0), 0.140577, abs_tol=5e-7)
    assert math.isclose(torsion_constant(1.0, 2.0) / 2.0, 0.228682, abs_tol=5e-7)


def test_thin_limit() -> None:
    # β ≈ 1/3 - 0.21·c/a: con a/c = 1e6, |β - 1/3| ≤ 3e-7.
    beta = torsion_constant(1e-6, 1.0) / (1.0 * 1e-6**3)
    assert abs(beta - 1 / 3) <= 3e-7
    assert beta < 1 / 3


def test_torsion_symmetry_and_not_polar_moment() -> None:
    assert torsion_constant(0.2, 0.4) == torsion_constant(0.4, 0.2)
    props = rectangular_properties(0.3, 0.3)
    j, iy, iz = props.torsion_constant.value, props.iy.value, props.iz.value
    assert j is not None
    assert iy is not None
    assert iz is not None
    assert not math.isclose(j, iy + iz, rel_tol=1e-2)  # J ≠ Iy + Iz (0,1406 frente a 0,1667)
    assert math.isclose(j / 0.3**4, 0.140577, abs_tol=5e-7)


def test_series_rejects_aspect_below_one() -> None:
    with pytest.raises(ValueError, match="aspect"):
        saint_venant_sum(0.5)
    assert saint_venant_sum(1e300) == ODD_ZETA_5


def test_series_stops_before_exp_overflow() -> None:
    # Por encima de e^700 no se evalúa ningún término más; el resultado sigue finito.
    assert math.isfinite(torsion_constant(1.0, 1e5))


def test_series_converges_in_few_terms(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[float] = []
    real_exp = math.exp

    def counting_exp(x: float) -> float:
        calls.append(x)
        return real_exp(x)

    monkeypatch.setattr(math, "exp", counting_exp)
    saint_venant_sum(1.0)
    assert len(calls) <= 8


# --- Casos inválidos ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("b", "h", "error", "code"),
    [
        (Quantity.of(0, "m"), Quantity.of(0.4, "m"), InputValueError, "INPUT_OUT_OF_RANGE"),
        (Quantity.of(-0.2, "m"), Quantity.of(0.4, "m"), InputValueError, "INPUT_OUT_OF_RANGE"),
        (Quantity.of(0.2, "m"), Quantity.of(0, "mm"), InputValueError, "INPUT_OUT_OF_RANGE"),
        (Quantity.of(0.2, "m²"), Quantity.of(0.4, "m"), InputValueError, "UNITS_INCOMPATIBLE"),
        (0.2, Quantity.of(0.4, "m"), InputTypeError, "INPUT_INVALID_TYPE"),
        (True, Quantity.of(0.4, "m"), InputTypeError, "INPUT_INVALID_TYPE"),
    ],
)
def test_invalid_dimensions_rejected(
    b: object, h: object, error: type[Exception], code: str
) -> None:
    with pytest.raises(error) as info:
        build_rectangular_section(uuid4(), 1, b=b, h=h)  # type: ignore[arg-type]
    assert info.value.code == DiagnosticCode(code)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("b", "h", "prop"),
    [
        (1e-160, 1e-160, "A"),
        (1e-80, 1e-80, "Iy"),
        (1e200, 1e200, "A"),
        (1.0, 1e110, "Iy"),
        # h³ = 1e330 desbordaría, pero b·h·h·h = 1e130 es representable: falla Iz (1e-490).
        (1e-200, 1e110, "Iz"),
    ],
)
def test_unrepresentable_properties(b: float, h: float, prop: str) -> None:
    with pytest.raises(InputValueError) as info:
        rectangular_properties(b, h)
    assert info.value.code is DiagnosticCode.UNITS_UNREPRESENTABLE
    assert info.value.diagnostic.params_dict()["property"] == prop


def test_parameters_reject_non_finite_and_bool() -> None:
    with pytest.raises(InputValueError) as info:
        RectangularParameters(math.inf, 1.0)
    assert info.value.code is DiagnosticCode.INPUT_NONFINITE
    with pytest.raises(InputTypeError):
        RectangularParameters(True, 1.0)


def test_section_property_invariants() -> None:
    unavailable = SectionProperty(None, "m⁴", "o", "m", PropertyStatus.NO_DISPONIBLE)
    assert unavailable.quantity is None
    with pytest.raises(ValueError, match="NO_DISPONIBLE"):
        SectionProperty(0.0, "m⁴", "o", "m", PropertyStatus.NO_DISPONIBLE)
    with pytest.raises(InputTypeError):
        SectionProperty(None, "m⁴", "o", "m", PropertyStatus.CALCULADO)
    with pytest.raises(ValueError, match="SI"):
        SectionProperty(1.0, "cm⁴", "o", "m", PropertyStatus.CALCULADO)
    with pytest.raises(ValueError, match="finito"):
        SectionProperty(math.nan, "m⁴", "o", "m", PropertyStatus.CALCULADO)


def test_section_revision_family_and_ref() -> None:
    section = build_rectangular_section(
        uuid4(), 3, b=Quantity.of(1, "m"), h=Quantity.of(2, "m"), name="R"
    )
    assert section.family is SectionFamily.RECTANGULAR
    assert section.ref.revision == 3
    assert section.parameters == RectangularParameters(1.0, 2.0)
    assert Quantity(section.parameters.b, LENGTH) == Quantity.of(1, "m")


def _calc(value: float, unit: str) -> SectionProperty:
    return SectionProperty(value, unit, "o", "x", PropertyStatus.CALCULADO)


@pytest.mark.parametrize(
    ("field", "prop", "code"),
    [
        ("area", _calc(0.0, "m²"), "UNITS_UNREPRESENTABLE"),
        ("iy", _calc(1e-310, "m⁴"), "UNITS_UNREPRESENTABLE"),
        ("torsion_constant", _calc(-1.0, "m⁴"), "UNITS_UNREPRESENTABLE"),
        ("iyz", _calc(-0.0, "m⁴"), "INPUT_OUT_OF_RANGE"),
        ("centroid_y", _calc(1e-3, "m"), "INPUT_OUT_OF_RANGE"),
        ("area", _calc(0.08, "m⁴"), "UNITS_INCOMPATIBLE"),
        (
            "iy",
            dataclasses.replace(rectangular_properties(0.2, 0.4).iy, value=1e-3),
            "INPUT_OUT_OF_RANGE",
        ),
    ],
)
def test_section_revision_rejects_inconsistent_properties(
    field: str, prop: SectionProperty, code: str
) -> None:  # Revisión de la implementación, B1.
    section = build_rectangular_section(
        uuid4(), 1, b=Quantity.of(0.2, "m"), h=Quantity.of(0.4, "m")
    )
    properties = dataclasses.replace(section.properties, **{field: prop})
    with pytest.raises(InputValueError) as info:
        dataclasses.replace(section, properties=properties)
    assert info.value.code == DiagnosticCode(code)


def test_properties_must_match_parameters() -> None:
    section = build_rectangular_section(
        uuid4(), 1, b=Quantity.of(0.2, "m"), h=Quantity.of(0.4, "m")
    )
    with pytest.raises(InputValueError) as info:
        dataclasses.replace(section, parameters=RectangularParameters(0.2, 0.5))
    assert info.value.diagnostic.message_key == "section.properties_mismatch"


def test_properties_match_with_tolerance_and_old_versions_are_kept() -> None:  # R1 (ii)
    section = build_rectangular_section(
        uuid4(), 1, b=Quantity.of(0.2, "m"), h=Quantity.of(0.4, "m")
    )
    j = section.properties.torsion_constant
    assert j.value is not None
    nudged = dataclasses.replace(j, value=j.value * (1 + 1e-14))
    accepted = dataclasses.replace(
        section, properties=dataclasses.replace(section.properties, torsion_constant=nudged)
    )
    assert accepted.properties.torsion_constant.value == nudged.value
    # Un valor guardado con otra versión del algoritmo no se recalcula al cargar (O3).
    old = dataclasses.replace(j, value=j.value * 1.01, method="saint-venant-series/0")
    kept = dataclasses.replace(
        section, properties=dataclasses.replace(section.properties, torsion_constant=old)
    )
    assert kept.properties.torsion_constant == old
