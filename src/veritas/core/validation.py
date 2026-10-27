"""Validación de valores físicos de frontera (plan T-004, D7).

``INPUT_OUT_OF_RANGE``: valor finito fuera del rango físicamente admisible.
``UNITS_UNREPRESENTABLE``: propiedad estrictamente positiva calculada que sale 0,
subnormal o infinita; una propiedad nunca se presenta como cero.
"""

import math
import sys

from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    InputTypeError,
    InputValueError,
    Location,
)
from veritas.core.units import Dimension, Quantity


def out_of_range(
    field: str, value: float, condition: str, location: Location | None = None
) -> InputValueError:
    """Diagnóstico ``INPUT_OUT_OF_RANGE`` para ``field``."""
    return InputValueError(
        Diagnostic.create(
            DiagnosticCode.INPUT_OUT_OF_RANGE,
            "input.out_of_range",
            {"field": field, "value": repr(value), "condition": condition},
            location=location,
        )
    )


def invalid_type(
    field: str, expected: str, received: object, location: Location | None = None
) -> InputTypeError:
    """Diagnóstico ``INPUT_INVALID_TYPE`` para ``field``."""
    return InputTypeError(
        Diagnostic.create(
            DiagnosticCode.INPUT_INVALID_TYPE,
            "input.invalid_type",
            {"field": field, "expected": expected, "received": type(received).__name__},
            location=location,
        )
    )


def nonfinite(field: str, value: float, location: Location | None = None) -> InputValueError:
    """Diagnóstico ``INPUT_NONFINITE`` para ``field``."""
    return InputValueError(
        Diagnostic.create(
            DiagnosticCode.INPUT_NONFINITE,
            "input.nonfinite",
            {"field": field, "value": repr(value)},
            location=location,
        )
    )


def require_text(value: object, field: str, location: Location | None = None) -> str:
    """Texto no vacío (sin contar espacios)."""
    if not isinstance(value, str) or not value.strip():
        raise invalid_type(field, "texto no vacío", value, location)
    return value


def require_real(value: object, field: str, location: Location | None = None) -> float:
    """Real finito (int o float, no bool) convertido a ``float``."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise invalid_type(field, "número real", value, location)
    try:
        number = float(value)
    except OverflowError:
        raise nonfinite(field, math.inf, location) from None
    if not math.isfinite(number):
        raise nonfinite(field, number, location)
    return number


def positive_float(value: object, field: str, location: Location | None = None) -> float:
    """Real finito estrictamente positivo (valor SI ya almacenado)."""
    number = require_real(value, field, location)
    if number <= 0.0:
        raise out_of_range(field, number, "> 0", location)
    return number


def positive_si(
    value: object, dimension: Dimension, field: str, location: Location | None = None
) -> float:
    """Valor SI de una ``Quantity`` de la dimensión dada y estrictamente positiva."""
    if not isinstance(value, Quantity):
        raise invalid_type(field, "Quantity", value, location)
    return positive_float(value.require(dimension).si_value, field, location)


def checked_positive(value: float, name: str, location: Location | None = None) -> float:
    """Propiedad calculada estrictamente positiva, finita y normal."""
    if not math.isfinite(value) or value < sys.float_info.min:
        raise InputValueError(
            Diagnostic.create(
                DiagnosticCode.UNITS_UNREPRESENTABLE,
                "section.property_unrepresentable",
                {"property": name, "value": repr(value)},
                location=location,
            )
        )
    return value
