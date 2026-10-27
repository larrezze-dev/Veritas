"""Pruebas del sistema de unidades (plan T-002).

Las referencias de factores se construyen con fracciones exactas a partir de prefijos
SI, de forma independiente de los literales de ``veritas.core.units``.
"""

import math
from fractions import Fraction

import pytest

from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError

KILO = Fraction(1000)
MEGA = KILO**2
GIGA = KILO**3
CENTI = Fraction(1, 100)
MILLI = Fraction(1, 1000)

# Factor exacto a SI de cada unidad racional del registro.
EXACT_FACTORS: dict[str, Fraction] = {
    "1": Fraction(1),
    "m": Fraction(1),
    "cm": CENTI,
    "mm": MILLI,
    "m²": Fraction(1),
    "cm²": CENTI**2,
    "mm²": MILLI**2,
    "m³": Fraction(1),
    "cm³": CENTI**3,
    "mm³": MILLI**3,
    "m⁴": Fraction(1),
    "cm⁴": CENTI**4,
    "mm⁴": MILLI**4,
    "m⁶": Fraction(1),
    "cm⁶": CENTI**6,
    "mm⁶": MILLI**6,
    "kg": Fraction(1),
    "t": KILO,
    "kg/m³": Fraction(1),
    "m/s²": Fraction(1),
    "N": Fraction(1),
    "kN": KILO,
    "MN": MEGA,
    "N/m": Fraction(1),
    "kN/m": KILO,
    "N/mm": 1 / MILLI,
    "kN/mm": KILO / MILLI,
    "Pa": Fraction(1),
    "kPa": KILO,
    "MPa": MEGA,
    "GPa": GIGA,
    "N/mm²": 1 / MILLI**2,
    "kN/m²": KILO,
    "N·m": Fraction(1),
    "N·mm": MILLI,
    "kN·m": KILO,
    "kN·mm": KILO * MILLI,
    "rad": Fraction(1),
    "N·m/rad": Fraction(1),
    "N·mm/rad": MILLI,
    "kN·m/rad": KILO,
}

# Tolerancia de ida y vuelta: una multiplicación y una división en doble precisión
# acumulan como mucho unos pocos ulp (≈ 2.2e-16 cada uno).
ROUND_TRIP_REL = 1e-15


def test_reference_table_covers_registry_except_degree() -> None:
    assert set(EXACT_FACTORS) == {unit.symbol for unit in u.UNITS} - {"deg"}


@pytest.mark.parametrize(("symbol", "exact"), sorted(EXACT_FACTORS.items()))
def test_rational_factor_is_nearest_double_of_exact_value(symbol: str, exact: Fraction) -> None:
    assert u.get_unit(symbol).factor == float(exact)


def test_degree_conversion() -> None:
    assert u.get_unit("deg").factor == math.radians(1)
    half_turn = u.Quantity.of(180, "deg").to("rad")
    assert half_turn == pytest.approx(math.pi, rel=ROUND_TRIP_REL, abs=0.0)
    right_angle = u.Quantity.of(math.pi / 2, "rad").to("deg")
    assert right_angle == pytest.approx(90.0, rel=ROUND_TRIP_REL, abs=0.0)


def test_factor_is_stored_as_float() -> None:
    unit = u.Unit("x", u.LENGTH, 1)
    assert type(unit.factor) is float


@pytest.mark.parametrize("unit", u.UNITS, ids=lambda unit: unit.symbol)
@pytest.mark.parametrize("value", [1.0, -123.456, 0.0, 7.25e-5, 3.0e8])
def test_round_trip_through_si(unit: u.Unit, value: float) -> None:
    quantity = u.Quantity.of(value, unit)
    if value == 0.0:
        assert quantity.to(unit) == 0.0
    else:
        assert quantity.to(unit) == pytest.approx(value, rel=ROUND_TRIP_REL, abs=0.0)


@pytest.mark.parametrize(
    ("value", "source", "target", "expected"),
    [
        (12.5, "kN·m", "N·m", Fraction(12500)),
        (210000, "N/mm²", "GPa", Fraction(210)),
        (8356, "cm⁴", "mm⁴", Fraction(83_560_000)),
        (1, "mm⁴", "m⁴", Fraction(1, 10**12)),
        (25, "kN/m", "N/mm", Fraction(25)),
        (3, "t", "kg", Fraction(3000)),
        (1.5, "kN·m/rad", "N·mm/rad", Fraction(1_500_000)),
    ],
)
def test_known_conversions(value: float, source: str, target: str, expected: Fraction) -> None:
    result = u.Quantity.of(value, source).to(target)
    assert result == pytest.approx(float(expected), rel=ROUND_TRIP_REL, abs=0.0)


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [("kN*m", "kN·m"), ("mm4", "mm⁴"), ("N/mm2", "N/mm²"), ("kg/m3", "kg/m³"), ("°", "deg")],
)
def test_ascii_aliases(alias: str, canonical: str) -> None:
    assert u.get_unit(alias) is u.get_unit(canonical)


# --- Álgebra dimensional ---------------------------------------------------------------


def test_named_dimensions() -> None:
    assert u.Dimension(length=1, mass=1, time=-2) == u.FORCE
    assert u.Dimension(length=-1, mass=1, time=-2) == u.STRESS
    assert u.Dimension(length=2, mass=1, time=-2) == u.MOMENT
    assert u.Dimension(length=2, mass=1, time=-2, angle=-1) == u.ROTATIONAL_STIFFNESS
    assert str(u.MOMENT) == "M·L^2·T^-2"
    assert str(u.DIMENSIONLESS) == "1"


def test_pressure_from_force_over_area() -> None:
    pressure = u.Quantity.of(1, "kN") / u.Quantity.of(1, "m²")
    assert pressure.dimension == u.STRESS
    assert pressure.to("kPa") == 1.0


def test_line_weight_from_density_area_and_gravity() -> None:
    # w = ρ·A·g = 7850 kg/m³ · 100 cm² · 9.81 m/s² = 770.085 N/m (referencia exacta)
    weight = u.Quantity.of(7850, "kg/m³") * u.Quantity.of(100, "cm²") * u.Quantity.of(9.81, "m/s²")
    expected = Fraction(7850) * Fraction(100) * CENTI**2 * Fraction(981, 100)
    assert weight.dimension == u.FORCE_PER_LENGTH
    assert weight.to("N/m") == pytest.approx(float(expected), rel=ROUND_TRIP_REL, abs=0.0)


def test_cantilever_tip_stiffness_dimension_and_value() -> None:
    # k = 3·E·I/L³ con E = 210000 N/mm², I = 8356 cm⁴, L = 6 m
    e_modulus = u.Quantity.of(210000, "N/mm²")
    inertia = u.Quantity.of(8356, "cm⁴")
    length = u.Quantity.of(6, "m")
    stiffness = 3 * e_modulus * inertia / length**3
    expected = 3 * (Fraction(210000) / MILLI**2) * (Fraction(8356) * CENTI**4) / 6**3
    assert stiffness.dimension == u.FORCE_PER_LENGTH
    assert stiffness.to("N/m") == pytest.approx(float(expected), rel=1e-14, abs=0.0)


def test_angle_is_not_dimensionless() -> None:
    moment = u.Quantity.of(10, "kN·m")
    rotation = u.Quantity.of(0.01, "rad")
    assert (moment / rotation).dimension == u.ROTATIONAL_STIFFNESS
    assert u.ANGLE != u.DIMENSIONLESS
    with pytest.raises(InputValueError) as exc_info:
        moment.to("kN·m/rad")
    assert exc_info.value.code is DiagnosticCode.UNITS_INCOMPATIBLE
    with pytest.raises(InputValueError):
        _ = rotation + u.Quantity.of(0.01, "1")


def test_arithmetic() -> None:
    a = u.Quantity.of(2, "m")
    b = u.Quantity.of(500, "mm")
    assert (a + b).to("m") == 2.5
    assert (a - b).to("mm") == 1500.0
    assert (-a).to("m") == -2.0
    assert abs(-a) == a
    assert (a * 2).to("m") == 4.0
    assert (2 * a).to("m") == 4.0
    assert (a / 4).to("m") == 0.5
    assert (1 / a).dimension == u.Dimension(length=-1)
    assert (a**2).dimension == u.AREA
    assert (a**0).dimension == u.DIMENSIONLESS
    assert b < a
    assert a >= b
    assert a > b
    assert b <= a


def test_equality_and_hash() -> None:
    assert u.Quantity.of(1, "kN") == u.Quantity.of(1000, "N")
    assert hash(u.Quantity.of(1, "kN")) == hash(u.Quantity.of(1000, "N"))
    assert u.Quantity.of(0.0, "m") == u.Quantity.of(-0.0, "m")
    assert u.Quantity.of(1, "N/m") == u.Quantity.of(1, "N/m")
    assert u.Quantity.of(1, "m") != u.Quantity.of(1, "rad")


def test_immutability() -> None:
    quantity = u.Quantity.of(1, "m")
    with pytest.raises(AttributeError):
        quantity.si_value = 2.0  # type: ignore[misc]
    with pytest.raises(AttributeError):
        u.LENGTH.length = 2  # type: ignore[misc]


def test_require() -> None:
    length = u.Quantity.of(3, "m")
    assert length.require(u.LENGTH) is length
    with pytest.raises(InputValueError) as exc_info:
        length.require(u.FORCE)
    assert exc_info.value.code is DiagnosticCode.UNITS_INCOMPATIBLE


# --- Casos inválidos -------------------------------------------------------------------


@pytest.mark.parametrize("value", [True, "1.0", None, [1.0]])
def test_rejects_non_real_values(value: object) -> None:
    with pytest.raises(InputTypeError) as exc_info:
        u.Quantity.of(value, "m")  # type: ignore[arg-type]
    assert exc_info.value.code is DiagnosticCode.INPUT_INVALID_TYPE
    assert isinstance(exc_info.value, TypeError)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_rejects_non_finite_values(value: float) -> None:
    with pytest.raises(InputValueError) as exc_info:
        u.Quantity.of(value, "m")
    assert exc_info.value.code is DiagnosticCode.INPUT_NONFINITE
    assert isinstance(exc_info.value, ValueError)


def test_rejects_non_finite_si_value_in_constructor() -> None:
    with pytest.raises(InputValueError):
        u.Quantity(math.nan, u.LENGTH)


@pytest.mark.parametrize("symbol", ["furlong", "KN", "", "kN m"])
def test_unknown_unit(symbol: str) -> None:
    with pytest.raises(InputValueError) as exc_info:
        u.get_unit(symbol)
    assert exc_info.value.code is DiagnosticCode.UNITS_UNKNOWN


def test_unit_type_error() -> None:
    with pytest.raises(InputTypeError):
        u.get_unit(3)  # type: ignore[arg-type]


def test_incompatible_addition_and_comparison() -> None:
    with pytest.raises(InputValueError) as exc_info:
        _ = u.Quantity.of(1, "kN") + u.Quantity.of(1, "m")
    assert exc_info.value.code is DiagnosticCode.UNITS_INCOMPATIBLE
    with pytest.raises(InputValueError):
        _ = u.Quantity.of(1, "kN") < u.Quantity.of(1, "m")
    with pytest.raises(InputTypeError):
        _ = u.Quantity.of(1, "kN") + 1  # type: ignore[operator]


@pytest.mark.parametrize(
    "operation",
    [
        lambda: u.Quantity.of(1e308, "kN"),
        lambda: u.Quantity.of(10**400, "m"),
        lambda: u.Quantity.of(5e-324, "mm"),
        lambda: u.Quantity.of(1e200, "m") * u.Quantity.of(1e200, "m"),
        lambda: u.Quantity.of(1e-200, "m") * u.Quantity.of(1e-200, "m"),
        lambda: u.Quantity.of(3e-162, "m") * u.Quantity.of(3e-162, "m"),
        lambda: u.Quantity.of(1e-300, "mm⁶"),
        lambda: u.Quantity.of(1e200, "m") ** 2,
        lambda: u.Quantity.of(1.7e308, "m") + u.Quantity.of(1.7e308, "m"),
        lambda: u.Quantity(1e308, u.LENGTH).to("mm"),
        lambda: u.Quantity(1e-320, u.FORCE).to("MN"),
    ],
    ids=[
        "construccion-desborda",
        "entero-enorme",
        "construccion-subdesborda",
        "producto-desborda",
        "producto-subdesborda",
        "producto-subnormal",
        "construccion-subnormal",
        "potencia-desborda",
        "suma-desborda",
        "conversion-desborda",
        "conversion-subdesborda",
    ],
)
def test_unrepresentable_results(operation: object) -> None:
    assert callable(operation)
    with pytest.raises(InputValueError) as exc_info:
        operation()
    assert exc_info.value.code is DiagnosticCode.UNITS_UNREPRESENTABLE


def test_smallest_normal_result_is_kept() -> None:
    quantity = u.Quantity.of(2.3e-302, "mm")  # 2.3e-305 m, por encima del mínimo normal
    assert quantity.si_value == pytest.approx(2.3e-305, rel=ROUND_TRIP_REL, abs=0.0)


def test_sum_is_exact_even_when_subnormal() -> None:
    # La resta de valores normales próximos es exacta (Sterbenz); no se rechaza.
    a = u.Quantity(3e-308, u.LENGTH)
    b = u.Quantity(2.9e-308, u.LENGTH)
    assert 0.0 < (a - b).si_value < 2.3e-308


# --- Convención del radián -------------------------------------------------------------


def test_rotational_stiffness_from_beam_formula() -> None:
    # k = 4EI/L con E = 210000 N/mm², I = 8356 cm⁴, L = 6 m: 11698.4 kN·m/rad (exacto)
    e_modulus = u.Quantity.of(210000, "N/mm²")
    inertia = u.Quantity.of(8356, "cm⁴")
    length = u.Quantity.of(6, "m")
    raw = 4 * e_modulus * inertia / length
    assert raw.dimension == u.MOMENT
    stiffness = raw / u.RADIAN
    expected = 4 * (Fraction(210000) / MILLI**2) * (Fraction(8356) * CENTI**4) / 6 / KILO
    assert stiffness.to("kN·m/rad") == pytest.approx(float(expected), rel=1e-15, abs=0.0)


def test_rotation_and_arc_length() -> None:
    # θ = M·L/(E·I) con M = 10 kN·m, L = 2 m, E = 210000 N/mm², I = 1e8 mm⁴
    moment = u.Quantity.of(10, "kN·m")
    length = u.Quantity.of(2, "m")
    flexural = u.Quantity.of(210000, "N/mm²") * u.Quantity.of(1e8, "mm⁴")
    theta = moment * length / flexural * u.RADIAN
    assert theta.dimension == u.ANGLE
    expected_theta = Fraction(10_000 * 2) / (
        Fraction(210000) / MILLI**2 * Fraction(10**8) * MILLI**4
    )
    assert theta.to("rad") == pytest.approx(float(expected_theta), rel=1e-15, abs=0.0)
    arc = u.Quantity.of(3, "m") * theta / u.RADIAN
    assert arc.to("m") == pytest.approx(float(3 * expected_theta), rel=1e-15, abs=0.0)


def test_radian_constant() -> None:
    assert u.Quantity.of(1, "rad") == u.RADIAN
    assert (u.RADIAN / u.RADIAN).dimension == u.DIMENSIONLESS


@pytest.mark.parametrize(
    "operation",
    [
        lambda: u.Quantity.of(1, "m") / u.Quantity.of(0, "m"),
        lambda: u.Quantity.of(1, "m") / 0,
        lambda: 1 / u.Quantity.of(0, "m"),
        lambda: u.Quantity.of(0, "m") ** -1,
    ],
)
def test_division_by_zero(operation: object) -> None:
    assert callable(operation)
    with pytest.raises(InputValueError) as exc_info:
        operation()
    assert exc_info.value.code is DiagnosticCode.UNITS_UNREPRESENTABLE
    assert exc_info.value.diagnostic.message_key == "units.division_by_zero"


@pytest.mark.parametrize("exponent", [0.5, True, "2"])
def test_power_requires_integer(exponent: object) -> None:
    with pytest.raises(InputTypeError):
        _ = u.Quantity.of(2, "m") ** exponent  # type: ignore[operator]


def test_dimension_rejects_non_integer_exponents() -> None:
    with pytest.raises(InputTypeError):
        u.Dimension(length=1.5)  # type: ignore[arg-type]
    with pytest.raises(InputTypeError):
        u.Dimension(mass=True)


def test_registry_has_unique_aliases() -> None:
    symbols = [unit.symbol for unit in u.UNITS]
    assert len(symbols) == len(set(symbols))
