"""Vector libre 3D de floats finitos (plan T-003, D5).

Las componentes son números en SI sin unidad propia: el campo que contiene el vector
declara qué representa (posición en m, dirección adimensional). Las fronteras públicas
convierten desde ``Quantity`` con ``Vec3.from_lengths``.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Self

from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    InputTypeError,
    InputValueError,
    Location,
)
from veritas.core.units import DIMENSIONLESS, LENGTH, Quantity

_DIMENSIONS = 3


def _component(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise InputTypeError(
            Diagnostic.create(
                DiagnosticCode.INPUT_INVALID_TYPE,
                "input.invalid_type",
                {"field": field, "expected": "int o float", "received": type(value).__name__},
                location=Location(field=field),
            )
        )
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


def _unrepresentable(operation: str) -> InputValueError:
    return InputValueError(
        Diagnostic.create(
            DiagnosticCode.UNITS_UNREPRESENTABLE,
            "units.unrepresentable",
            {"operation": operation},
        )
    )


@dataclass(frozen=True, slots=True)
class Vec3:
    """Vector inmutable (x, y, z) de floats finitos."""

    x: float
    y: float
    z: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _component(self.x, "Vec3.x"))
        object.__setattr__(self, "y", _component(self.y, "Vec3.y"))
        object.__setattr__(self, "z", _component(self.z, "Vec3.z"))

    @classmethod
    def _checked(cls, x: float, y: float, z: float, operation: str) -> Self:
        if not (math.isfinite(x) and math.isfinite(y) and math.isfinite(z)):
            raise _unrepresentable(operation)
        return cls(x, y, z)

    @classmethod
    def from_lengths(cls, x: Quantity, y: Quantity, z: Quantity) -> Self:
        """Posición a partir de tres longitudes; se almacena en m."""
        components = []
        for name, value in (("x", x), ("y", y), ("z", z)):
            if not isinstance(value, Quantity):
                raise InputTypeError(
                    Diagnostic.create(
                        DiagnosticCode.INPUT_INVALID_TYPE,
                        "input.invalid_type",
                        {"field": name, "expected": "Quantity", "received": type(value).__name__},
                        location=Location(field=name),
                    )
                )
            components.append(value.require(LENGTH).si_value)
        return cls(*components)

    @classmethod
    def direction(cls, value: "Vec3 | Sequence[object]", field: str = "referencia") -> Self:
        """Dirección libre adimensional: ``Vec3`` o tres reales o ``Quantity`` adimensionales."""
        if isinstance(value, Vec3):
            return cls(value.x, value.y, value.z)
        if (
            isinstance(value, str | bytes)
            or not isinstance(value, Sequence)
            or len(value) != _DIMENSIONS
        ):
            raise InputTypeError(
                Diagnostic.create(
                    DiagnosticCode.INPUT_INVALID_TYPE,
                    "input.invalid_type",
                    {"field": field, "expected": "3 componentes", "received": repr(value)},
                    location=Location(field=field),
                )
            )
        components = [
            item.require(DIMENSIONLESS).si_value if isinstance(item, Quantity) else item
            for item in value
        ]
        return cls(
            _component(components[0], field),
            _component(components[1], field),
            _component(components[2], field),
        )

    def __add__(self, other: "Vec3") -> "Vec3":
        return Vec3._checked(self.x + other.x, self.y + other.y, self.z + other.z, "suma")

    def __sub__(self, other: "Vec3") -> "Vec3":
        return Vec3._checked(self.x - other.x, self.y - other.y, self.z - other.z, "resta")

    def __neg__(self) -> "Vec3":
        return Vec3(-self.x, -self.y, -self.z)

    def scaled(self, factor: float) -> "Vec3":
        """Producto por un escalar."""
        return Vec3._checked(factor * self.x, factor * self.y, factor * self.z, "escalado")

    def dot(self, other: "Vec3") -> float:
        """Producto escalar."""
        return self.x * other.x + self.y * other.y + self.z * other.z

    def cross(self, other: "Vec3") -> "Vec3":
        """Producto vectorial dextrógiro ``self × other``."""
        return Vec3._checked(
            self.y * other.z - self.z * other.y,
            self.z * other.x - self.x * other.z,
            self.x * other.y - self.y * other.x,
            "producto vectorial",
        )

    def norm(self) -> float:
        """Norma euclídea sin desbordamiento intermedio (``math.hypot``)."""
        return math.hypot(self.x, self.y, self.z)

    def components(self) -> tuple[float, float, float]:
        """Componentes como tupla."""
        return (self.x, self.y, self.z)


UNIT_X = Vec3(1.0, 0.0, 0.0)
UNIT_Y = Vec3(0.0, 1.0, 0.0)
UNIT_Z = Vec3(0.0, 0.0, 1.0)
