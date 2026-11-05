"""Área de cortante «energética» (T-025 E1, D8; ADR-011): definición, alcance y fórmulas.

**Nombre y uso.** ``shear_area_energy_y/z`` es una propiedad de la sección (no conoce el material,
SEC-002), independiente de ν. NO es el área resistente a cortante de una norma (DB-SE-A, EC3) ni es
comparable con el ``Av`` de un catálogo: ``design_codes`` no puede usarla como tal. Hoy no la
consume ningún elemento (el solver es Euler–Bernoulli; TK-013).

**Definición (corte recto de Jourawski, energía complementaria).** ``Avz`` va con el cortante Vz
(flexión alrededor de y, inercia Iy) y ``Avy`` con Vy (flexión alrededor de z, inercia Iz), como los
esfuerzos de ADR-001. Con cortes rectos ``z = cte`` y tensión uniforme en cada corte,

    Q(z) = ∫_{z' > z} z'·w(z') dz',   w(z) = suma de las cuerdas de material a la altura z,
    τ = Vz·Q/(Iy·w),   Avz = Iy² / ∫ (Q/w)² dA = Iy² / ∫ Q(z)²/w(z) dz,

(análogo con ``y = cte`` e ``Iz`` para ``Avy``). Se cumple ``∫Q dz = Iy`` y, por Cauchy–Schwarz,
``0 < Av < A``. El rectángulo da exactamente 5/6·A en ambos ejes.

**Alcance aprobado** (la hipótesis de corte recto solo se acepta donde es defendible; en el resto la
propiedad es ``NO_DISPONIBLE`` con motivo, nunca un valor burdo ni 0):

- rectángulo: ``Avy`` y ``Avz``;
- RHS/SHS de aristas vivas: ``Avz`` y ``Avy`` (la misma fórmula con ``b`` y ``h`` intercambiados;
  ampliación aceptada por el usuario el 2026-11-05);
- CHS (todo espesor): ``Avy = Avz`` por forma cerrada del corte recto (ampliación aceptada el
  2026-11-05). Es la propiedad energética del corte recto, NO la de pared delgada con tensión
  tangencial (A/2): tiende a 2/3·A en pared fina y a 0,9·A en el círculo macizo;
- I de aristas vivas: solo ``Avz`` (el cortante según y lo transmiten las alas);
- ``Avy`` de la I y cualquier radio de acuerdo > 0: ``NO_DISPONIBLE`` (TK-029).

Las fórmulas son sumas de términos positivos (sin cancelación). Con ``a`` la semialtura libre,
``H = h/2`` y ``Qf`` el momento estático de un ala respecto del eje y:

    I:   E = 2·a·Qf²/tw + (4/3)·a³·Qf + (4/15)·a⁵·tw + (b/2)·F,
    RHS: E = a·Qf²/t   + (4/3)·a³·Qf + (8/15)·a⁵·t  + (b/2)·F,
    F = (4/3)·H²·tf³ − H·tf⁴ + tf⁵/5   (tf = t en el tubo),   Av = Iy²/E.
"""

import math
from dataclasses import dataclass
from typing import Final

from veritas.core.sections.parametric import ParametricParameters, ParametricShape
from veritas.core.sections.properties import PropertyStatus, SectionProperty
from veritas.core.sections.shapes_hollow import rhs_area_inertias
from veritas.core.sections.shapes_i import i_area_inertias
from veritas.core.validation import checked_positive, invalid_type

METHOD_SHEAR: Final = "shear-energy-straight-cut/1"
ORIGIN_Z: Final = (
    "calculado de los parámetros de esta revisión: energía de cortante de Jourawski con cortes "
    "rectos z = cte (Vz)"
)
ORIGIN_Y: Final = (
    "calculado de los parámetros de esta revisión: energía de cortante de Jourawski con cortes "
    "rectos y = cte (Vy)"
)
_UNAVAILABLE_RADII: Final = (
    "sin forma cerrada validada con radios de acuerdo: el área de cortante energética solo se "
    "define para aristas vivas"
)
_UNAVAILABLE_Y_I: Final = (
    "el corte recto y = cte no es defendible en la I: el cortante según y lo transmiten las alas "
    "con flujo horizontal"
)


@dataclass(frozen=True, slots=True)
class ShearAreas:
    """Grupo opcional de la revisión de sección: ``shear_area_energy_y`` y ``_z`` (m²).

    ``None`` en la revisión significa «nunca calculada» (secciones previas a T-025); un grupo
    presente con una propiedad ``NO_DISPONIBLE`` lleva el motivo en ``origin``.
    """

    shear_y: SectionProperty
    shear_z: SectionProperty

    def __post_init__(self) -> None:
        for name in self.__slots__:
            if not isinstance(getattr(self, name), SectionProperty):
                raise invalid_type(name, "SectionProperty", getattr(self, name))


def _calculated(value: float, origin: str, name: str) -> SectionProperty:
    checked_positive(value, name)
    return SectionProperty(value, "m²", origin, METHOD_SHEAR, PropertyStatus.CALCULADO)


def _unavailable(motive: str) -> SectionProperty:
    return SectionProperty(None, "m²", motive, METHOD_SHEAR, PropertyStatus.NO_DISPONIBLE)


def rectangular_shear(b: float, h: float) -> ShearAreas:
    """5/6·b·h en ambos ejes: ``∫Q²/w dz = b·h⁵/120`` con ``Iy = b·h³/12`` (y análogo en y)."""
    value = 5.0 / 6.0 * (b * h)
    return ShearAreas(
        _calculated(value, ORIGIN_Y, "shear_area_energy_y"),
        _calculated(value, ORIGIN_Z, "shear_area_energy_z"),
    )


def _flange_term(b: float, half_height: float, tf: float) -> float:
    return (
        b
        / 2.0
        * (4.0 / 3.0 * half_height * half_height * tf**3 - half_height * tf**4 + tf**5 / 5.0)
    )


def i_shear_z(h: float, b: float, tw: float, tf: float) -> float:
    """``Avz`` de la I de aristas vivas (m²)."""
    _, iy, _ = i_area_inertias(h, b, tw, tf, 0.0)
    a, half = (h - 2.0 * tf) / 2.0, h / 2.0
    qf = b * tf * (h - tf) / 2.0
    energy = (
        2.0 * a * qf * qf / tw
        + 4.0 / 3.0 * a**3 * qf
        + 4.0 / 15.0 * a**5 * tw
        + _flange_term(b, half, tf)
    )
    return iy * iy / energy


def rhs_shear_z(h: float, b: float, t: float) -> float:
    """``Avz`` del tubo rectangular de aristas vivas (m²)."""
    _, iy, _ = rhs_area_inertias(h, b, t, 0.0)
    a, half = (h - 2.0 * t) / 2.0, h / 2.0
    qf = b * t * (h - t) / 2.0
    energy = (
        a * qf * qf / t + 4.0 / 3.0 * a**3 * qf + 8.0 / 15.0 * a**5 * t + _flange_term(b, half, t)
    )
    return iy * iy / energy


def chs_shear(d: float, t: float) -> float:
    """``Av`` (= ``Avy`` = ``Avz``) de la CHS por corte recto, forma cerrada para todo espesor.

    Con ``R = d/2``, ``r = R − t``, ``D = R² − r²``, ``alfa = atan2(r, √D)`` e
    ``Iy = π/4·(R⁴ − r⁴)``: ``E = (2/9)·D·{(5π/16)(R⁴ + R²r² + r⁴) + Dπr²/2 + 3πr⁴/8
    + alfa(r²R² − R⁴/4) + r³√D + rD^{3/2}/2 − R²r√D/4}`` y ``Av = Iy²/E``.
    Contrastada con la cuadratura de acordes (≤ 1e-12).
    """
    big, small = d / 2.0, d / 2.0 - t
    big2, small2 = big * big, small * small
    diff = big2 - small2
    root = math.sqrt(diff)
    alpha = math.atan2(small, root)
    inertia = math.pi / 4.0 * (big2 * big2 - small2 * small2)
    braces = (
        5.0 * math.pi / 16.0 * (big2 * big2 + big2 * small2 + small2 * small2)
        + diff * math.pi * small2 / 2.0
        + 3.0 * math.pi * small2 * small2 / 8.0
        + alpha * (small2 * big2 - big2 * big2 / 4.0)
        + small**3 * root
        + small * diff * root / 2.0
        - big2 * small * root / 4.0
    )
    return inertia * inertia / (2.0 / 9.0 * diff * braces)


def parametric_shear(parameters: ParametricParameters) -> ShearAreas:
    """Grupo de áreas de cortante de un perfil paramétrico, con el alcance aprobado."""
    shape, v = parameters.shape, parameters.as_dict()
    if shape is ParametricShape.I:
        if v["r"] > 0.0:
            return ShearAreas(_unavailable(_UNAVAILABLE_RADII), _unavailable(_UNAVAILABLE_RADII))
        value = i_shear_z(v["h"], v["b"], v["tw"], v["tf"])
        return ShearAreas(
            _unavailable(_UNAVAILABLE_Y_I), _calculated(value, ORIGIN_Z, "shear_area_energy_z")
        )
    if shape in (ParametricShape.RHS, ParametricShape.SHS):
        if v["ro"] > 0.0:
            return ShearAreas(_unavailable(_UNAVAILABLE_RADII), _unavailable(_UNAVAILABLE_RADII))
        b = v["b"]
        h = b if shape is ParametricShape.SHS else v["h"]
        return ShearAreas(
            _calculated(rhs_shear_z(b, h, v["t"]), ORIGIN_Y, "shear_area_energy_y"),
            _calculated(rhs_shear_z(h, b, v["t"]), ORIGIN_Z, "shear_area_energy_z"),
        )
    value = chs_shear(v["d"], v["t"])
    return ShearAreas(
        _calculated(value, ORIGIN_Y, "shear_area_energy_y"),
        _calculated(value, ORIGIN_Z, "shear_area_energy_z"),
    )
