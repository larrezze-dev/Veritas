"""Sección rectangular maciza: fórmulas cerradas y torsión de Saint-Venant (T-004, D3/D4).

Convención (ADR-001 §1): b según y local, h según z local; el rectángulo se define con
origen en su centro, que es el centroide, y el eje de la barra pasa por él.

    A = b·h,  Iy = ∫z² dA = b·h³/12,  Iz = ∫y² dA = h·b³/12,  Iyz = ∫yz dA = 0.

Constante de torsión (Timoshenko y Goodier, *Theory of Elasticity*, torsión de la barra
rectangular; edición a confirmar). Con lado largo a y corto c (lados completos):

    J = (a·c³/3)·[1 - (192/π⁵)(c/a)·S],  S = Σ_{n impar} tanh(nπa/(2c))/n⁵.

S se evalúa en forma de convergencia exponencial, usando tanh(x) = 1 - 2/(e^{2x}+1) y
Σ_{n impar} n⁻⁵ = (31/32)·ζ(5):

    S = (31/32)·ζ(5) - Σ_{n impar} 2/(n⁵·(e^{2x_n}+1)),  x_n = nπa/(2c) ≥ nπ/2.
"""

import math
from typing import Final

from veritas.core.diagnostics import Location
from veritas.core.sections.properties import PropertyStatus, SectionProperties, SectionProperty
from veritas.core.validation import checked_positive

ZETA_5: Final = 1.0369277551433699263
"""ζ(5), función zeta de Riemann en 5 (OEIS A013663)."""

ODD_ZETA_5: Final = 31.0 / 32.0 * ZETA_5
"""Σ_{n impar} n⁻⁵ = (1 - 2⁻⁵)·ζ(5)."""

SERIES_RELATIVE_STOP: Final = 1e-17
"""Fin de la serie: término menor que esta fracción de S (por debajo del redondeo)."""

_MAX_EXP_ARGUMENT: Final = 700.0  # e^700 ≈ 1e304: el término restante es despreciable.

METHOD_CLOSED_FORM: Final = "rect-closed-form/1"
METHOD_SAINT_VENANT: Final = "saint-venant-series/1"
ORIGIN: Final = "calculado a partir de los parámetros b, h de esta revisión"


def saint_venant_sum(aspect: float) -> float:
    """S(a/c) = Σ_{n impar} tanh(nπ·aspect/2)/n⁵ para ``aspect`` = a/c ≥ 1."""
    if not aspect >= 1.0:
        raise ValueError("aspect debe ser >= 1 (lado largo / lado corto)")
    total = ODD_ZETA_5
    n = 1
    while True:
        two_x = n * math.pi * aspect
        if two_x > _MAX_EXP_ARGUMENT:
            return total
        term = 2.0 / (n**5 * (math.exp(two_x) + 1.0))
        total -= term
        if term < SERIES_RELATIVE_STOP * total:
            return total
        n += 2


def torsion_constant(b: float, h: float) -> float:
    """J de Saint-Venant de un rectángulo b×h macizo (m⁴), simétrica en b y h."""
    long_side, short_side = max(b, h), min(b, h)
    aspect = long_side / short_side
    factor = 1.0 - 192.0 / math.pi**5 / aspect * saint_venant_sum(aspect)
    return long_side * short_side * short_side * short_side / 3.0 * factor


def rectangular_properties(
    b: float, h: float, location: Location | None = None
) -> SectionProperties:
    """Propiedades CALCULADO de un rectángulo con b, h > 0 en m."""

    def calc(value: float, unit: str, method: str = METHOD_CLOSED_FORM) -> SectionProperty:
        return SectionProperty(value, unit, ORIGIN, method, PropertyStatus.CALCULADO)

    # Productos factor a factor (b·h·h·h): evitan el desbordamiento de h³ aislado cuando b
    # es pequeño. Si un producto intermedio desborda aunque el resultado /12 cupiera, se
    # rechaza igualmente (∞): criterio conservador con el código correcto. La comprobación
    # de J es defensiva: J ≥ 0,1406·a·c³ > min(Iy, Iz), que ya se ha comprobado.
    area = checked_positive(b * h, "A", location)
    iy = checked_positive(b * h * h * h / 12.0, "Iy", location)
    iz = checked_positive(h * b * b * b / 12.0, "Iz", location)
    j = checked_positive(torsion_constant(b, h), "J", location)
    return SectionProperties(
        area=calc(area, "m²"),
        centroid_y=calc(0.0, "m"),
        centroid_z=calc(0.0, "m"),
        iy=calc(iy, "m⁴"),
        iz=calc(iz, "m⁴"),
        iyz=calc(0.0, "m⁴"),
        torsion_constant=calc(j, "m⁴", METHOD_SAINT_VENANT),
    )
