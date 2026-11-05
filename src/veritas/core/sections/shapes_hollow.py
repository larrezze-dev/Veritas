"""Tubos RHS/SHS y CHS: fórmulas cerradas (T-025 E1, D6, I3).

**RHS/SHS** (``h × b``, espesor uniforme ``t``, radio exterior ``ro``, interior
``ri = max(ro − t, 0)``). Con ``hi = h − 2t`` y ``bi = b − 2t``, la caja de aristas vivas se
factoriza sin cancelación (``b·h³ − bi·hi³ = 2t·[b(h² + h·hi + hi²) + hi³]``):

    A_vivo  = 2t·(b + h − 2t),   Iy_vivo = (t/6)·[b(h² + h·hi + hi²) + hi³],
    Iz_vivo = (t/6)·[h(b² + b·bi + bi²) + bi³]

y las esquinas se corrigen sumando las regiones de acuerdo interiores y restando las exteriores
(``core.sections.fillet``), cada una con su vértice en la esquina de su contorno:
``A = A_vivo − 4·(a·ro² − a·ri²)``, ``Iy = Iy_vivo − 4·M(h/2, ro) + 4·M(hi/2, ri)`` (y ``Iz`` con
``b``).

**CHS** (diámetro ``d``, espesor ``t``, ``R = d/2``, ``r = R − t``):

    A = π·t·(d − t),   I = Iy = Iz = π·t·(d − t)·(d² + (d − 2t)²)/16,   J = 2·I,

con ``R⁴ − r⁴ = (R − r)(R + r)(R² + r²)`` factorizado. La torsión de la sección anular es exacta
(``J = Ip``, analogía de torsión de la sección circular). Los ejes principales de SHS y CHS
(``Iy = Iz``) son indeterminados: cualquier eje centroidal es principal.
"""

import math
from typing import Final

from veritas.core.sections import fillet
from veritas.core.sections.parametric import inner_radius

METHOD_RHS: Final = "rhs-closed-form/1"
METHOD_SHS: Final = "shs-closed-form/1"
METHOD_CHS: Final = "chs-closed-form/1"
METHOD_CHS_TORSION: Final = "annulus-torsion-exact/1"


def rhs_area_inertias(h: float, b: float, t: float, ro: float) -> tuple[float, float, float]:
    """(A, Iy, Iz) del tubo rectangular (o cuadrado con ``h = b``)."""
    hi, bi = h - 2.0 * t, b - 2.0 * t
    ri = inner_radius(ro, t)
    area = 2.0 * t * (b + h - 2.0 * t)
    iy = t / 6.0 * (b * (h * h + h * hi + hi * hi) + hi * hi * hi)
    iz = t / 6.0 * (h * (b * b + b * bi + bi * bi) + bi * bi * bi)
    if ro > 0.0:
        # a·(ro² − ri²) con la diferencia factorizada: ro − ri = min(t, ro).
        area -= 4.0 * fillet.AREA_FACTOR * (ro - ri) * (ro + ri)
        iy -= 4.0 * fillet.moment_toward(h / 2.0, ro)
        iz -= 4.0 * fillet.moment_toward(b / 2.0, ro)
        if ri > 0.0:
            iy += 4.0 * fillet.moment_toward(hi / 2.0, ri)
            iz += 4.0 * fillet.moment_toward(bi / 2.0, ri)
    return area, iy, iz


def chs_area_inertia(d: float, t: float) -> tuple[float, float]:
    """(A, I) del tubo circular con ``I = Iy = Iz``."""
    area = math.pi * t * (d - t)
    inertia = area * (d * d + (d - 2.0 * t) * (d - 2.0 * t)) / 16.0
    return area, inertia
