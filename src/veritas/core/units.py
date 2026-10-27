"""Magnitudes físicas con valor interno SI y dimensión explícita.

Convenciones (plan T-002, ADR-001):

- Dimensiones base: longitud L, masa M, tiempo T y ángulo A. El ángulo es una dimensión
  propia (D1): un giro en rad no es adimensional y la rigidez rotacional N·m/rad no se
  confunde con un momento N·m.
- Registro cerrado de unidades lineales (sin desplazamiento): ``valor_SI = valor × factor``.
- Los valores se almacenan como ``float`` de doble precisión (NFR-005). Se rechazan
  ``bool``, tipos no reales, NaN/±∞ y resultados no representables de productos,
  cocientes, potencias y conversiones: desbordamiento, o resultado de un valor no nulo
  que cae en el rango subnormal o en cero (pérdida de precisión). La división por una
  magnitud nula usa el código ``UNITS_UNREPRESENTABLE`` con la clave
  ``units.division_by_zero``.
- Convención del radián: las fórmulas mecánicas no producen la dimensión ángulo por sí
  solas (4EI/L da N·m; θ = ML/EI da adimensional; s = r·θ da L·A). La conversión es
  explícita con ``RADIAN``: ``4*E*I/L / RADIAN`` es rigidez rotacional (N·m/rad),
  ``M*L/(E*I) * RADIAN`` es un giro (rad) y ``r*theta / RADIAN`` es una longitud (m).
- La igualdad y el hash comparan el ``float`` exacto; las comparaciones con tolerancia
  corresponden a cada cálculo, no a ``==``.
"""

import math
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Self

from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    InputTypeError,
    InputValueError,
    Location,
)

type Real = int | float


# --- Validación ------------------------------------------------------------------------


def _type_error(field: str, expected: str, received: object) -> InputTypeError:
    return InputTypeError(
        Diagnostic.create(
            DiagnosticCode.INPUT_INVALID_TYPE,
            "input.invalid_type",
            {"field": field, "expected": expected, "received": type(received).__name__},
            location=Location(field=field),
        )
    )


def _unrepresentable(operation: str) -> InputValueError:
    return InputValueError(
        Diagnostic.create(
            DiagnosticCode.UNITS_UNREPRESENTABLE,
            "units.unrepresentable",
            {"operation": operation},
        )
    )


def _division_by_zero(operation: str) -> InputValueError:
    return InputValueError(
        Diagnostic.create(
            DiagnosticCode.UNITS_UNREPRESENTABLE,
            "units.division_by_zero",
            {"operation": operation},
        )
    )


def _to_real(value: object, field: str) -> float:
    """Convierte un real admitido a ``float`` finito o lanza un diagnóstico."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _type_error(field, "int o float", value)
    try:
        result = float(value)
    except OverflowError:
        raise _unrepresentable(f"conversión de {field} a float") from None
    if not math.isfinite(result):
        raise InputValueError(
            Diagnostic.create(
                DiagnosticCode.INPUT_NONFINITE,
                "input.nonfinite",
                {"field": field, "value": repr(result)},
                location=Location(field=field),
            )
        )
    return result


def _checked(result: float, *, nonzero_expected: bool, operation: str) -> float:
    """Comprueba que el resultado de una operación entre finitos sigue siendo representable."""
    if not math.isfinite(result):
        raise _unrepresentable(operation)
    if nonzero_expected and abs(result) < sys.float_info.min:
        raise _unrepresentable(operation)  # Cero o subnormal: pérdida de precisión.
    return result


# --- Dimensiones -----------------------------------------------------------------------

_BASE_SYMBOLS: Final = ("M", "L", "T", "A")


@dataclass(frozen=True, slots=True)
class Dimension:
    """Exponentes enteros sobre las dimensiones base L, M, T y A."""

    length: int = 0
    mass: int = 0
    time: int = 0
    angle: int = 0

    def __post_init__(self) -> None:
        for name in ("length", "mass", "time", "angle"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise _type_error(f"Dimension.{name}", "int", value)

    def _combine(self, other: "Dimension", op: Callable[[int, int], int]) -> "Dimension":
        return Dimension(
            op(self.length, other.length),
            op(self.mass, other.mass),
            op(self.time, other.time),
            op(self.angle, other.angle),
        )

    def __mul__(self, other: "Dimension") -> "Dimension":
        return self._combine(other, lambda a, b: a + b)

    def __truediv__(self, other: "Dimension") -> "Dimension":
        return self._combine(other, lambda a, b: a - b)

    def __pow__(self, exponent: int) -> "Dimension":
        if isinstance(exponent, bool) or not isinstance(exponent, int):
            raise _type_error("exponente", "int", exponent)
        return Dimension(
            self.length * exponent,
            self.mass * exponent,
            self.time * exponent,
            self.angle * exponent,
        )

    @property
    def is_dimensionless(self) -> bool:
        return self == DIMENSIONLESS

    def __str__(self) -> str:
        exponents = dict(
            zip(_BASE_SYMBOLS, (self.mass, self.length, self.time, self.angle), strict=True)
        )
        parts = [
            symbol if exp == 1 else f"{symbol}^{exp}"
            for symbol, exp in exponents.items()
            if exp != 0
        ]
        return "·".join(parts) if parts else "1"


DIMENSIONLESS: Final = Dimension()
LENGTH: Final = Dimension(length=1)
AREA: Final = Dimension(length=2)
SECTION_MODULUS: Final = Dimension(length=3)
SECOND_MOMENT: Final = Dimension(length=4)
WARPING_CONSTANT: Final = Dimension(length=6)
MASS: Final = Dimension(mass=1)
TIME: Final = Dimension(time=1)
ANGLE: Final = Dimension(angle=1)
DENSITY: Final = MASS / LENGTH**3
LINEAR_MASS: Final = MASS / LENGTH
ACCELERATION: Final = LENGTH / TIME**2
FORCE: Final = MASS * ACCELERATION
FORCE_PER_LENGTH: Final = FORCE / LENGTH
STRESS: Final = FORCE / AREA
MOMENT: Final = FORCE * LENGTH
ROTATIONAL_STIFFNESS: Final = MOMENT / ANGLE


# --- Unidades --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Unit:
    """Unidad lineal del registro: ``valor_SI = valor × factor``."""

    symbol: str
    dimension: Dimension
    factor: float

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol:
            raise _type_error("Unit.symbol", "str no vacío", self.symbol)
        if not isinstance(self.dimension, Dimension):
            raise _type_error("Unit.dimension", "Dimension", self.dimension)
        factor = _to_real(self.factor, "Unit.factor")
        if factor <= 0.0:
            raise _unrepresentable(f"factor de la unidad {self.symbol}")
        object.__setattr__(self, "factor", factor)

    def __str__(self) -> str:
        return self.symbol


# Factores exactos en notación decimal; los tests los contrastan con fracciones exactas.
UNITS: Final[tuple[Unit, ...]] = (
    Unit("1", DIMENSIONLESS, 1.0),
    Unit("m", LENGTH, 1.0),
    Unit("cm", LENGTH, 1e-2),
    Unit("mm", LENGTH, 1e-3),
    Unit("m²", AREA, 1.0),
    Unit("cm²", AREA, 1e-4),
    Unit("mm²", AREA, 1e-6),
    Unit("m³", SECTION_MODULUS, 1.0),
    Unit("cm³", SECTION_MODULUS, 1e-6),
    Unit("mm³", SECTION_MODULUS, 1e-9),
    Unit("m⁴", SECOND_MOMENT, 1.0),
    Unit("cm⁴", SECOND_MOMENT, 1e-8),
    Unit("mm⁴", SECOND_MOMENT, 1e-12),
    Unit("m⁶", WARPING_CONSTANT, 1.0),
    Unit("cm⁶", WARPING_CONSTANT, 1e-12),
    Unit("mm⁶", WARPING_CONSTANT, 1e-18),
    Unit("kg", MASS, 1.0),
    Unit("t", MASS, 1e3),
    Unit("kg/m³", DENSITY, 1.0),
    Unit("kg/m", LINEAR_MASS, 1.0),
    Unit("m/s²", ACCELERATION, 1.0),
    Unit("N", FORCE, 1.0),
    Unit("kN", FORCE, 1e3),
    Unit("MN", FORCE, 1e6),
    Unit("N/m", FORCE_PER_LENGTH, 1.0),
    Unit("kN/m", FORCE_PER_LENGTH, 1e3),
    Unit("N/mm", FORCE_PER_LENGTH, 1e3),
    Unit("kN/mm", FORCE_PER_LENGTH, 1e6),
    Unit("Pa", STRESS, 1.0),
    Unit("kPa", STRESS, 1e3),
    Unit("MPa", STRESS, 1e6),
    Unit("GPa", STRESS, 1e9),
    Unit("N/mm²", STRESS, 1e6),
    Unit("kN/m²", STRESS, 1e3),
    Unit("N·m", MOMENT, 1.0),
    Unit("N·mm", MOMENT, 1e-3),
    Unit("kN·m", MOMENT, 1e3),
    Unit("kN·mm", MOMENT, 1.0),
    Unit("rad", ANGLE, 1.0),
    Unit("deg", ANGLE, math.pi / 180.0),
    Unit("N·m/rad", ROTATIONAL_STIFFNESS, 1.0),
    Unit("N·mm/rad", ROTATIONAL_STIFFNESS, 1e-3),
    Unit("kN·m/rad", ROTATIONAL_STIFFNESS, 1e3),
)

_ASCII_ALIASES: Final = str.maketrans({"²": "2", "³": "3", "⁴": "4", "⁶": "6", "·": "*"})


def _build_lookup() -> dict[str, Unit]:
    lookup: dict[str, Unit] = {}
    for unit in UNITS:
        aliases = {unit.symbol, unit.symbol.translate(_ASCII_ALIASES)}
        if unit.symbol == "deg":
            aliases.add("°")
        for alias in aliases:
            if alias in lookup:
                raise RuntimeError(f"Alias de unidad duplicado: {alias}")
            lookup[alias] = unit
    return lookup


_LOOKUP: Final = _build_lookup()


def get_unit(unit: "str | Unit") -> Unit:
    """Resuelve una unidad por símbolo canónico o alias ASCII (p. ej. ``kN*m``, ``mm4``)."""
    if isinstance(unit, Unit):
        return unit
    if not isinstance(unit, str):
        raise _type_error("unidad", "str o Unit", unit)
    try:
        return _LOOKUP[unit.strip()]
    except KeyError:
        raise InputValueError(
            Diagnostic.create(DiagnosticCode.UNITS_UNKNOWN, "units.unknown", {"symbol": unit})
        ) from None


def units_with_dimension(dimension: Dimension) -> tuple[Unit, ...]:
    """Unidades del registro con la dimensión dada, en orden de registro."""
    return tuple(unit for unit in UNITS if unit.dimension == dimension)


def _incompatible(left: Dimension, right: Dimension) -> InputValueError:
    return InputValueError(
        Diagnostic.create(
            DiagnosticCode.UNITS_INCOMPATIBLE,
            "units.incompatible",
            {"left": str(left), "right": str(right)},
        )
    )


# --- Magnitudes ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Quantity:
    """Magnitud inmutable: valor en SI y dimensión.

    No se convierte implícitamente a ``float``: usar ``to(unidad)`` para obtener un número.
    """

    si_value: float
    dimension: Dimension

    def __post_init__(self) -> None:
        object.__setattr__(self, "si_value", _to_real(self.si_value, "Quantity.si_value"))
        if not isinstance(self.dimension, Dimension):
            raise _type_error("Quantity.dimension", "Dimension", self.dimension)

    @classmethod
    def of(cls, value: Real, unit: "str | Unit") -> Self:
        """Crea una magnitud a partir de un valor expresado en ``unit``."""
        number = _to_real(value, "valor")
        resolved = get_unit(unit)
        si_value = _checked(
            number * resolved.factor,
            nonzero_expected=number != 0.0,
            operation=f"{number!r} {resolved.symbol} a SI",
        )
        return cls(si_value, resolved.dimension)

    def to(self, unit: "str | Unit") -> float:
        """Valor numérico expresado en ``unit`` (misma dimensión obligatoria)."""
        resolved = get_unit(unit)
        if resolved.dimension != self.dimension:
            raise _incompatible(self.dimension, resolved.dimension)
        return _checked(
            self.si_value / resolved.factor,
            nonzero_expected=self.si_value != 0.0,
            operation=f"conversión a {resolved.symbol}",
        )

    def require(self, dimension: Dimension) -> Self:
        """Devuelve ``self`` si tiene la dimensión exigida; si no, lanza un diagnóstico."""
        if self.dimension != dimension:
            raise _incompatible(self.dimension, dimension)
        return self

    def _same_dimension(self, other: object) -> "Quantity":
        if not isinstance(other, Quantity):
            raise _type_error("operando", "Quantity", other)
        if other.dimension != self.dimension:
            raise _incompatible(self.dimension, other.dimension)
        return other

    def __add__(self, other: "Quantity") -> "Quantity":
        rhs = self._same_dimension(other)
        value = _checked(self.si_value + rhs.si_value, nonzero_expected=False, operation="suma")
        return Quantity(value, self.dimension)

    def __sub__(self, other: "Quantity") -> "Quantity":
        rhs = self._same_dimension(other)
        value = _checked(self.si_value - rhs.si_value, nonzero_expected=False, operation="resta")
        return Quantity(value, self.dimension)

    def __neg__(self) -> "Quantity":
        return Quantity(-self.si_value, self.dimension)

    def __abs__(self) -> "Quantity":
        return Quantity(abs(self.si_value), self.dimension)

    def __mul__(self, other: "Quantity | Real") -> "Quantity":
        if isinstance(other, Quantity):
            factor, dimension = other.si_value, self.dimension * other.dimension
        else:
            factor, dimension = _to_real(other, "factor"), self.dimension
        value = _checked(
            self.si_value * factor,
            nonzero_expected=self.si_value != 0.0 and factor != 0.0,
            operation="producto",
        )
        return Quantity(value, dimension)

    def __rmul__(self, other: Real) -> "Quantity":
        return self.__mul__(other)

    def __truediv__(self, other: "Quantity | Real") -> "Quantity":
        if isinstance(other, Quantity):
            divisor, dimension = other.si_value, self.dimension / other.dimension
        else:
            divisor, dimension = _to_real(other, "divisor"), self.dimension
        if divisor == 0.0:
            raise _division_by_zero("cociente")
        value = _checked(
            self.si_value / divisor,
            nonzero_expected=self.si_value != 0.0,
            operation="cociente",
        )
        return Quantity(value, dimension)

    def __rtruediv__(self, other: Real) -> "Quantity":
        numerator = _to_real(other, "dividendo")
        if self.si_value == 0.0:
            raise _division_by_zero("cociente")
        value = _checked(
            numerator / self.si_value,
            nonzero_expected=numerator != 0.0,
            operation="cociente",
        )
        return Quantity(value, DIMENSIONLESS / self.dimension)

    def __pow__(self, exponent: int) -> "Quantity":
        if isinstance(exponent, bool) or not isinstance(exponent, int):
            raise InputTypeError(
                Diagnostic.create(
                    DiagnosticCode.INPUT_INVALID_TYPE,
                    "units.invalid_exponent",
                    {"exponent": repr(exponent)},
                )
            )
        if exponent < 0 and self.si_value == 0.0:
            raise _division_by_zero("potencia negativa")
        try:
            value = self.si_value**exponent
        except OverflowError:
            raise _unrepresentable("potencia") from None
        checked = _checked(
            value,
            nonzero_expected=self.si_value != 0.0 or exponent == 0,
            operation="potencia",
        )
        return Quantity(checked, self.dimension**exponent)

    def __lt__(self, other: "Quantity") -> bool:
        return self.si_value < self._same_dimension(other).si_value

    def __le__(self, other: "Quantity") -> bool:
        return self.si_value <= self._same_dimension(other).si_value

    def __gt__(self, other: "Quantity") -> bool:
        return self.si_value > self._same_dimension(other).si_value

    def __ge__(self, other: "Quantity") -> bool:
        return self.si_value >= self._same_dimension(other).si_value

    def __str__(self) -> str:
        return f"{self.si_value!r} [{self.dimension}] (SI)"


RADIAN: Final = Quantity(1.0, ANGLE)
"""1 rad. Multiplicar o dividir por ``RADIAN`` introduce o elimina la dimensión ángulo."""
