"""Perfil I de alas paralelas, bisimétrico: fórmulas cerradas (T-025 E1, D6).

Dimensiones (m): canto total ``h`` (z), ancho de ala ``b`` (y), alma ``tw``, ala ``tf`` y acuerdo
alma–ala ``r`` (cuatro acuerdos iguales, 0 = arista viva). Sea ``hw = h − 2·tf`` el alma libre.
Por suma de partes (sin restar cajas, para no perder cifras con almas y alas finas):

    A  = 2·b·tf + hw·tw + 4·a·r²
    Iy = tw·hw³/12 + 2·(b·tf³/12 + b·tf·((hw + tf)/2)²) + 4·∫z² dA_acuerdo
    Iz = 2·tf·b³/12 + hw·tw³/12 + 4·∫y² dA_acuerdo

con los acuerdos de ``core.sections.fillet`` en el vértice ``(tw/2, hw/2)``. Centroide e ``Iyz``
son exactamente 0 por la doble simetría. La constante de torsión de Saint-Venant de la I no
tiene solución cerrada exacta validada (DEC-005, D7): ``NO_DISPONIBLE``.
"""

from typing import Final

from veritas.core.sections import fillet

METHOD_I: Final = "i-closed-form/1"


def i_area_inertias(
    h: float, b: float, tw: float, tf: float, r: float
) -> tuple[float, float, float]:
    """(A, Iy, Iz) de la I; ``r`` = 0 es la forma de aristas vivas."""
    hw = h - 2.0 * tf
    area = 2.0 * b * tf + hw * tw
    iy = tw * hw * hw * hw / 12.0 + 2.0 * (
        b * tf * tf * tf / 12.0 + b * tf * (hw + tf) * (hw + tf) / 4.0
    )
    iz = 2.0 * tf * b * b * b / 12.0 + hw * tw * tw * tw / 12.0
    if r > 0.0:
        area += 4.0 * fillet.area(r)
        iy += 4.0 * fillet.moment_toward(hw / 2.0, r)
        iz += 4.0 * fillet.moment_away(tw / 2.0, r)
    return area, iy, iz
