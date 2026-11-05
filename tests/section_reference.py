"""Referencia independiente de propiedades de sección por cuadratura de acordes (T-025 E1, D13).

Python puro, sin NumPy/SciPy y SIN llamar al código de fórmulas de ``veritas.core.sections``.
La geometría se describe por su CONTORNO como acordes de material a cada altura ``z``
(``[(y1, y2), …]``); las propiedades salen de integrar sobre ``z``, partiendo en los saltos y
cambios de régimen del contorno (en los acuerdos y arcos, con la sustitución ``z = m − h·cos φ``,
que regulariza el comportamiento ``√`` de los extremos y da convergencia exponencial con
Gauss–Legendre). La descomposición es «caja menos huecos» (cuerdas exteriores e interiores), no
la suma de partes de las fórmulas cerradas.

    A = ∫ w dz,  Iy = ∫ z² w dz,  Iz = ∫ Σ(y2³ − y1³)/3 dz,  Iyz = ∫ z Σ(y2² − y1²)/2 dz,
    Q(z) = ∫_{z'>z} z' w(z') dz',   E = ∫ Q²/w dz,   Avz = Iy²/E   (corte recto z = cte).
"""

import itertools
import math
from collections.abc import Callable
from dataclasses import dataclass

type Chords = list[tuple[float, float]]


def gauss_legendre(n: int) -> list[tuple[float, float]]:
    """Nodos y pesos de Gauss–Legendre de orden ``n`` en [−1, 1] (Newton sobre P_n)."""
    rule = []
    for i in range(1, n + 1):
        x = math.cos(math.pi * (i - 0.25) / (n + 0.5))
        for _ in range(100):
            p0, p1 = 1.0, x
            for k in range(2, n + 1):
                p0, p1 = p1, ((2 * k - 1) * x * p1 - (k - 1) * p0) / k
            dp = n * (x * p1 - p0) / (x * x - 1.0)
            step = p1 / dp
            x -= step
            if abs(step) < 1e-16:
                break
        p0, p1 = 1.0, x
        for k in range(2, n + 1):
            p0, p1 = p1, ((2 * k - 1) * x * p1 - (k - 1) * p0) / k
        dp = n * (x * p1 - p0) / (x * x - 1.0)
        rule.append((x, 2.0 / ((1.0 - x * x) * dp * dp)))
    return rule


_RULE = gauss_legendre(48)
# Regla en φ ∈ [0, π]: φ = π(1 + x)/2.
_PHI = [(math.pi * (1.0 + x) / 2.0, w * math.pi / 2.0) for x, w in _RULE]


def integrate(f: Callable[[float], float], a: float, b: float) -> float:
    """∫_a^b f dz con z = m − h·cos φ (extremos regularizados); 0 si el tramo es degenerado."""
    if b <= a:
        return 0.0
    m, h = (a + b) / 2.0, (b - a) / 2.0
    return sum(w * f(m - h * math.cos(phi)) * h * math.sin(phi) for phi, w in _PHI)


@dataclass(frozen=True)
class Geometry:
    """Contorno: ``chords(z)`` y los puntos de salto ordenados (de abajo arriba)."""

    chords: Callable[[float], Chords]
    breaks: tuple[float, ...]

    def segments(self) -> list[tuple[float, float]]:
        pts = sorted(set(self.breaks))
        return [(a, b) for a, b in itertools.pairwise(pts) if b > a]

    def width(self, z: float) -> float:
        return sum(y2 - y1 for y1, y2 in self.chords(z))

    def total(self, f: Callable[[float], float]) -> float:
        return sum(integrate(f, a, b) for a, b in self.segments())

    def area(self) -> float:
        return self.total(self.width)

    def iy(self) -> float:
        return self.total(lambda z: z * z * self.width(z))

    def iz(self) -> float:
        return self.total(lambda z: sum((y2**3 - y1**3) / 3.0 for y1, y2 in self.chords(z)))

    def iyz(self) -> float:
        return self.total(lambda z: z * sum((y2 * y2 - y1 * y1) / 2.0 for y1, y2 in self.chords(z)))

    def first_moment_above(self, z: float) -> float:
        """Q(z) = ∫_{z' > z} z'·w(z') dz'."""
        total = 0.0
        for a, b in self.segments():
            if b <= z:
                continue
            total += integrate(lambda s: s * self.width(s), max(a, z), b)
        return total

    def shear_energy(self) -> float:
        """∫ Q(z)²/w(z) dz (corte recto z = cte); los acordes de ancho 0 no aportan."""

        def integrand(z: float) -> float:
            w = self.width(z)
            return 0.0 if w <= 0.0 else self.first_moment_above(z) ** 2 / w

        return self.total(integrand)

    def shear_area_z(self) -> float:
        return self.iy() ** 2 / self.shear_energy()


def _sqrt0(x: float) -> float:
    return math.sqrt(max(x, 0.0))


def rounded_half_width(z: float, half_w: float, half_h: float, radius: float) -> float:
    """Semianchura a la altura ``z`` de un rectángulo de esquinas redondeadas."""
    az = abs(z)
    if radius <= 0.0 or az <= half_h - radius:
        return half_w
    return half_w - radius + _sqrt0(radius * radius - (az - (half_h - radius)) ** 2)


def rectangle(b: float, h: float) -> Geometry:
    return Geometry(lambda z: [(-b / 2.0, b / 2.0)], (-h / 2.0, h / 2.0))


def i_section(h: float, b: float, tw: float, tf: float, r: float = 0.0) -> Geometry:
    a, top = h / 2.0 - tf, h / 2.0

    def chords(z: float) -> Chords:
        az = abs(z)
        if az > a:
            return [(-b / 2.0, b / 2.0)]
        x = tw / 2.0
        if r > 0.0 and az > a - r:
            x += r - _sqrt0(r * r - (az - (a - r)) ** 2)
        return [(-x, x)]

    return Geometry(chords, (-top, -a, -(a - r), 0.0, a - r, a, top))


def rect_tube(h: float, b: float, t: float, ro: float = 0.0) -> Geometry:
    """Tubo rectangular: contorno exterior menos contorno interior (radio ``max(ro − t, 0)``)."""
    hi, bi = h / 2.0 - t, b / 2.0 - t
    ri = max(ro - t, 0.0)

    def chords(z: float) -> Chords:
        xo = rounded_half_width(z, b / 2.0, h / 2.0, ro)
        if abs(z) >= hi:
            return [(-xo, xo)]
        xi = rounded_half_width(z, bi, hi, ri)
        return [(-xo, -xi), (xi, xo)]

    pts = (h / 2.0, h / 2.0 - ro, hi, hi - ri)
    return Geometry(chords, (*pts, *(-p for p in pts), 0.0))


def circular_tube(d: float, t: float) -> Geometry:
    big, small = d / 2.0, d / 2.0 - t

    def chords(z: float) -> Chords:
        xo = _sqrt0(big * big - z * z)
        if abs(z) >= small:
            return [(-xo, xo)]
        xi = _sqrt0(small * small - z * z)
        return [(-xo, -xi), (xi, xo)]

    return Geometry(chords, (-big, -small, 0.0, small, big))
