"""Región de acuerdo (T-025 E1, D3): cuadrado r×r menos el cuarto de círculo concéntrico.

Es la región entre el vértice de una esquina y el arco de radio ``r`` cuyo centro está a distancia
``r`` de las dos aristas. Con ``w`` la distancia al vértice medida sobre un eje (paralelo a una de
las aristas), las integrales sobre la región son ``∫dA = a·r²``, ``∫w dA = a·c·r³`` y
``∫w² dA = k·r⁴``, con

    a = 1 − π/4,  c = (5/6 − π/4)/(1 − π/4) = (10 − 3π)/(3(4 − π)),  k = 1 − 5π/16.

Sirven igual para el acuerdo alma–ala de la I (material añadido) y para la esquina de un tubo
(material retirado de la caja). Con coordenada ``z = zv − w`` (la región queda del lado ``−z`` del
vértice) y ``y = yv + w``:

    ∫z² dA = A·zv² − 2·zv·A·c·r + k·r⁴,   ∫y² dA = A·yv² + 2·yv·A·c·r + k·r⁴.
"""

import math
from typing import Final

AREA_FACTOR: Final = (4.0 - math.pi) / 4.0
"""a: el área de la región es ``a·r²``."""

CENTROID_FACTOR: Final = (10.0 - 3.0 * math.pi) / (3.0 * (4.0 - math.pi))
"""c: distancia del centroide al vértice, ``c·r``, sobre cada eje."""

SECOND_FACTOR: Final = (16.0 - 5.0 * math.pi) / 16.0
"""k: ``∫w² dA = k·r⁴`` respecto del eje que pasa por el vértice."""


def area(r: float) -> float:
    """Área de una región de acuerdo de radio ``r``."""
    return AREA_FACTOR * r * r


def moment_toward(vertex: float, r: float) -> float:
    """∫z² dA de la región cuya coordenada es ``vertex − w`` (vértice en ``vertex`` > 0)."""
    a = area(r)
    return a * vertex * (vertex - 2.0 * CENTROID_FACTOR * r) + SECOND_FACTOR * r**4


def moment_away(vertex: float, r: float) -> float:
    """∫y² dA de la región cuya coordenada es ``vertex + w`` (vértice en ``vertex`` ≥ 0)."""
    a = area(r)
    return a * vertex * (vertex + 2.0 * CENTROID_FACTOR * r) + SECOND_FACTOR * r**4
