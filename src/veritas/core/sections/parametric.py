"""Perfiles paramétricos: I, RHS, SHS y CHS (T-025 E1, D4/D5/D6; ADR-011).

``ParametricParameters`` es la geometría de una revisión de sección de la familia
``PARAMETRICA``. El rectángulo macizo NO está aquí: sigue siendo ``RECTANGULAR``.

Convención (ADR-001 §1): ``b`` según y local y ``h`` según z local (``d`` en el tubo circular);
el origen es el centroide, que coincide con el centro de la forma (todas son doblemente
simétricas). Todas las dimensiones están en m.

======  ===============================  ==============================================
Forma   Dimensiones (orden canónico)     Significado
======  ===============================  ==============================================
I       ``h, b, tw, tf, r``              canto total, ancho de ala, alma, ala, acuerdo
RHS     ``h, b, t, ro``                  canto, ancho, espesor uniforme, radio exterior
SHS     ``b, t, ro``                     lado (h = b), espesor uniforme, radio exterior
CHS     ``d, t``                         diámetro exterior y espesor
======  ===============================  ==============================================

``r`` (I) y ``ro`` (RHS/SHS) son opcionales y valen 0 (arista viva) si no se indican, pero se
guardan SIEMPRE: la representación es única, de modo que la misma geometría da el mismo contenido
y el mismo hash. El radio interior del tubo no es independiente (``ri = max(ro − t, 0)``: esquinas
concéntricas, espesor uniforme; plan T-025, G1) y no se guarda. La SHS tiene una sola dimensión
de lado, ``b``: no se compara ``h = b`` en coma flotante.

Las reglas de validez (EDT-004) viven aquí, en ``core``, para que las apliquen igual el borrador
del editor (que acumula todos los diagnósticos) y la propia revisión (que rechaza con el primero,
también al abrir un archivo manipulado).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from veritas.core.diagnostics import Diagnostic, DiagnosticCode, InputValueError, Location
from veritas.core.validation import invalid_type, out_of_range, require_real


class ParametricShape(StrEnum):
    """Forma paramétrica; las etiquetas coinciden con ``PROFILE_SHAPES`` del catálogo."""

    I = "I"  # noqa: E741 (etiqueta de forma del catálogo)
    RHS = "RHS"
    SHS = "SHS"
    CHS = "CHS"


SHAPE_DIMENSIONS: Final = {
    ParametricShape.I: ("h", "b", "tw", "tf", "r"),
    ParametricShape.RHS: ("h", "b", "t", "ro"),
    ParametricShape.SHS: ("b", "t", "ro"),
    ParametricShape.CHS: ("d", "t"),
}
"""Dimensiones de cada forma en orden canónico (el de la representación guardada)."""

OPTIONAL_DIMENSIONS: Final = frozenset({"r", "ro"})
"""Radios opcionales: valen 0 si no se indican."""


def inner_radius(ro: float, t: float) -> float:
    """Radio interior del tubo rectangular: esquinas concéntricas, ``max(ro − t, 0)``."""
    return max(ro - t, 0.0)


@dataclass(frozen=True, slots=True)
class ParametricDimension:
    """Dimensión nominal: nombre y valor SI finito (m). El signo lo juzgan las reglas de forma."""

    name: str
    value: float

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name:
            raise invalid_type("dimension_name", "texto no vacío", self.name)
        number = require_real(self.value, f"dimension.{self.name}")
        object.__setattr__(self, "value", number + 0.0)  # −0,0 → +0,0: representación única


def _dimension_error(
    shape: str, expected: tuple[str, ...], found: tuple[str, ...]
) -> InputValueError:
    return InputValueError(
        Diagnostic.create(
            DiagnosticCode.INPUT_OUT_OF_RANGE,
            "section.geometry_dimensions",
            {"shape": shape, "expected": ", ".join(expected), "found": ", ".join(found) or "-"},
            location=Location(field="dimensions"),
        )
    )


def _rule(code_key: str, field: str, params: Mapping[str, str | float]) -> Diagnostic:
    return Diagnostic.create(
        DiagnosticCode.INPUT_OUT_OF_RANGE,
        code_key,
        {"field": field, **params},
        location=Location(field=field),
    )


def _positive(values: Mapping[str, float], names: tuple[str, ...]) -> list[Diagnostic]:
    return [
        out_of_range(name, values[name], "> 0", Location(field=name)).diagnostic
        for name in names
        if values[name] <= 0.0
    ]


def _radius(name: str, values: Mapping[str, float], limit: float) -> list[Diagnostic]:
    """Radio opcional: 0 ≤ valor ≤ ``limit``. ``limit`` solo se evalúa si es finito y positivo."""
    value = values[name]
    if value < 0.0:
        return [out_of_range(name, value, "≥ 0", Location(field=name)).diagnostic]
    if limit > 0.0 and value > limit:
        return [_rule("section.geometry_radius_too_large", name, {"value": value, "max": limit})]
    return []


def geometry_diagnostics(
    shape: ParametricShape, values: Mapping[str, float]
) -> tuple[Diagnostic, ...]:
    """Todas las violaciones EDT-004 de una geometría (vacía si es válida).

    ``values`` trae TODAS las dimensiones de la forma (en SI, finitas). Reglas, necesarias y
    suficientes para que el contorno sea simple y sin autointersección:

    - I: ``h, b, tw, tf > 0``; ``2·tf < h``; ``tw < b``; ``0 ≤ r ≤ min((b − tw)/2, (h − 2tf)/2)``.
    - RHS: ``h, b, t > 0``; ``2t < min(h, b)``; ``0 ≤ ro ≤ min(h, b)/2`` (``ri`` derivado).
    - SHS: igual con ``h = b``. CHS: ``d, t > 0`` y ``t < d/2``.
    """
    found: list[Diagnostic] = []
    if shape is ParametricShape.I:
        h, b, tw, tf = values["h"], values["b"], values["tw"], values["tf"]
        found += _positive(values, ("h", "b", "tw", "tf"))
        if tf > 0.0 and h > 0.0 and not 2.0 * tf < h:
            found.append(_rule("section.geometry_flange_too_thick", "tf", {"tf": tf, "h": h}))
        if tw > 0.0 and b > 0.0 and not tw < b:
            found.append(_rule("section.geometry_web_too_thick", "tw", {"tw": tw, "b": b}))
        limit = min((b - tw) / 2.0, (h - 2.0 * tf) / 2.0)
        found += _radius("r", values, limit)
    elif shape in (ParametricShape.RHS, ParametricShape.SHS):
        b, t = values["b"], values["t"]
        h = b if shape is ParametricShape.SHS else values["h"]
        found += _positive(values, SHAPE_DIMENSIONS[shape][:-1])
        side = min(h, b)
        if t > 0.0 and side > 0.0 and not 2.0 * t < side:
            found.append(_rule("section.geometry_wall_too_thick", "t", {"t": t, "side": side}))
        found += _radius("ro", values, side / 2.0)
    else:
        d, t = values["d"], values["t"]
        found += _positive(values, ("d", "t"))
        if t > 0.0 and d > 0.0 and not t < d / 2.0:
            found.append(_rule("section.geometry_wall_too_thick_circle", "t", {"t": t, "d": d}))
    return tuple(found)


@dataclass(frozen=True, slots=True)
class ParametricParameters:
    """Forma y dimensiones (todas, en orden canónico) de un perfil paramétrico."""

    shape: ParametricShape
    dimensions: tuple[ParametricDimension, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.shape, ParametricShape):
            raise invalid_type("shape", "ParametricShape", self.shape)
        dims = tuple(self.dimensions)
        if not all(isinstance(d, ParametricDimension) for d in dims):
            raise invalid_type("dimensions", "ParametricDimension", dims)
        expected = SHAPE_DIMENSIONS[self.shape]
        names = tuple(d.name for d in dims)
        if names != expected:
            raise _dimension_error(str(self.shape), expected, names)
        object.__setattr__(self, "dimensions", dims)
        problems = geometry_diagnostics(self.shape, self.as_dict())
        if problems:
            raise InputValueError(problems[0])

    @classmethod
    def from_values(
        cls, shape: ParametricShape, values: Mapping[str, float]
    ) -> "ParametricParameters":
        """Construye desde un diccionario SI; los radios omitidos valen 0; el resto, obligatorio."""
        expected = SHAPE_DIMENSIONS[shape]
        names = tuple(values)
        unknown = [n for n in names if n not in expected]
        missing = [n for n in expected if n not in values and n not in OPTIONAL_DIMENSIONS]
        if unknown or missing:
            raise _dimension_error(str(shape), expected, names)
        return cls(shape, tuple(ParametricDimension(n, values.get(n, 0.0)) for n in expected))

    def as_dict(self) -> dict[str, float]:
        """Dimensiones por nombre (copia, en orden canónico)."""
        return {d.name: d.value for d in self.dimensions}

    def get(self, name: str) -> float:
        """Valor de una dimensión de la forma; ``KeyError`` si no existe."""
        return self.as_dict()[name]
