"""Fuerzas de empotramiento de las cargas de barra (plan T-015, E2.0; ADR-005).

Fuente única de p0 para las cargas de barra de Veritas: vector local de 12 GDL en el orden
[N, Vy, Vz, T, My, Mz] de I y luego de J, en la base local de ADR-001 (ex de I a J), con el
convenio de ``fixed_end_forces``: localForce = k·T·u + p0 y carga nodal equivalente −p0.

Formulación Euler–Bernoulli prismática, exacta: p0 = −∫Nᵀ·q dx con las funciones de forma
de Hermite en flexión (θz = +dv/dx en el plano x–y, θy = −dw/dx en el x–z) y lineales en axil
y torsión; un par concentrado trabaja con N′. Las distribuidas lineales se integran con
Gauss–Legendre de 3 puntos sobre [ξa, ξb] (exacto hasta grado 5; el integrando es de grado
≤ 4). Puro: sin OpenSeesPy y sin importar ``veritas.fem.spatial`` (``stiffness`` lo importa).
"""

import math
from collections.abc import Callable, Sequence
from typing import Final

from veritas.core.loads.loads import MIN_SEGMENT

type Vec3 = tuple[float, float, float]
# Suma en F la contribución de una carga en ξ con un peso: (F, ξ, carga, peso, L).
type _Accumulator = Callable[[list[float], float, Vec3, float, float], None]

LOCAL_DOFS: Final = 12
# Índices de p0 (N, Vy, Mz de I y J) que forman la vista del elemento plano.
PLANAR_INDICES: Final = (0, 1, 5, 6, 7, 11)
# Gauss–Legendre de 3 puntos en [−1, 1]: (t, peso).
_GAUSS_3: Final = (
    (-math.sqrt(3.0 / 5.0), 5.0 / 9.0),
    (0.0, 8.0 / 9.0),
    (math.sqrt(3.0 / 5.0), 5.0 / 9.0),
)


def uniform_fixed_end_forces(wx: float, wy: float, wz: float, length: float) -> list[float]:
    """p0 de ``-beamUniform`` (Wy, Wz, Wx) en toda la luz (hecho 9 de T-018).

    Mismas operaciones, en el mismo orden, que la fórmula original de ``fixed_end_forces``:
    las corridas existentes no cambian ni un bit.
    """
    ell = length
    p0 = [0.0] * 12
    p0[0] = p0[6] = -wx * ell / 2
    p0[1] = p0[7] = -wy * ell / 2
    p0[2] = p0[8] = -wz * ell / 2
    p0[5], p0[11] = -wy * ell**2 / 12, wy * ell**2 / 12
    p0[4], p0[10] = wz * ell**2 / 12, -wz * ell**2 / 12
    return p0


def _hermite(xi: float, length: float) -> tuple[float, float, float, float]:
    """N1…N4 en ξ (N2 y N4 en m)."""
    return (
        1 - 3 * xi**2 + 2 * xi**3,
        length * (xi - 2 * xi**2 + xi**3),
        3 * xi**2 - 2 * xi**3,
        length * (xi**3 - xi**2),
    )


def _hermite_slope(xi: float, length: float) -> tuple[float, float, float, float]:
    """dN1/dx…dN4/dx en ξ (N1′ y N3′ en 1/m, N2′ y N4′ adimensionales)."""
    return (
        (-6 * xi + 6 * xi**2) / length,
        1 - 4 * xi + 3 * xi**2,
        (6 * xi - 6 * xi**2) / length,
        3 * xi**2 - 2 * xi,
    )


def _add_force(f: list[float], xi: float, q: Vec3, weight: float, length: float) -> None:
    """F += peso·Nᵀ·q de una fuerza (qx, qy, qz) en ξ."""
    qx, qy, qz = (weight * c for c in q)
    n1, n2, n3, n4 = _hermite(xi, length)
    f[0] += (1 - xi) * qx
    f[6] += xi * qx
    f[1] += n1 * qy
    f[5] += n2 * qy
    f[7] += n3 * qy
    f[11] += n4 * qy
    f[2] += n1 * qz
    f[4] -= n2 * qz
    f[8] += n3 * qz
    f[10] -= n4 * qz


def _add_moment(f: list[float], xi: float, m: Vec3, weight: float, length: float) -> None:
    """F += peso·(trabajo de un par (mx, my, mz) en ξ): θx lineal, θz = v′, θy = −w′."""
    mx, my, mz = (weight * c for c in m)
    d1, d2, d3, d4 = _hermite_slope(xi, length)
    f[3] += (1 - xi) * mx
    f[9] += xi * mx
    f[1] += d1 * mz
    f[5] += d2 * mz
    f[7] += d3 * mz
    f[11] += d4 * mz
    f[2] -= d1 * my
    f[4] += d2 * my
    f[8] -= d3 * my
    f[10] += d4 * my


def _check_length(length: float) -> None:
    if not (math.isfinite(length) and length > 0.0):
        raise ValueError(f"longitud de barra no válida: {length!r}")


def _check_finite(*vectors: Vec3) -> None:
    if not all(math.isfinite(c) for vector in vectors for c in vector):
        raise ValueError("componente de carga no finita")


def _check_segment(start: float, end: float) -> None:
    if not (0.0 <= start < end <= 1.0) or end - start <= MIN_SEGMENT:
        raise ValueError(f"tramo de carga no válido: [{start!r}, {end!r}]")


def point_fixed_end_forces(
    position: float, force: Vec3, moment: Vec3, length: float
) -> list[float]:
    """p0 de una fuerza (N) y un par (N·m) locales en ξ = ``position``, 0 < ξ < 1."""
    _check_length(length)
    _check_finite(force, moment)
    if not 0.0 < position < 1.0:
        raise ValueError(f"posición de carga puntual no válida: {position!r}")
    f = [0.0] * LOCAL_DOFS
    _add_force(f, position, force, 1.0, length)
    _add_moment(f, position, moment, 1.0, length)
    return [-value for value in f]


def _linear(
    add: _Accumulator, segment: tuple[float, float], intensities: tuple[Vec3, Vec3], length: float
) -> list[float]:
    start, end = segment
    intensity_start, intensity_end = intensities
    _check_length(length)
    _check_finite(intensity_start, intensity_end)
    _check_segment(start, end)
    half = (end - start) / 2
    f = [0.0] * LOCAL_DOFS
    for t, weight in _GAUSS_3:
        s = (1 + t) / 2  # Coordenada del tramo, 0 en ξa y 1 en ξb.
        q = tuple(a + (b - a) * s for a, b in zip(intensity_start, intensity_end, strict=True))
        add(f, start + half * (1 + t), (q[0], q[1], q[2]), length * half * weight, length)
    return [-value for value in f]


def distributed_fixed_end_forces(
    start: float, end: float, intensity_start: Vec3, intensity_end: Vec3, length: float
) -> list[float]:
    """p0 de una fuerza local (N/m) lineal entre ξa = ``start`` y ξb = ``end``."""
    return _linear(_add_force, (start, end), (intensity_start, intensity_end), length)


def distributed_moment_fixed_end_forces(
    start: float, end: float, intensity_start: Vec3, intensity_end: Vec3, length: float
) -> list[float]:
    """p0 de un momento local (N·m/m) lineal entre ξa = ``start`` y ξb = ``end``."""
    return _linear(_add_moment, (start, end), (intensity_start, intensity_end), length)


def planar_view(p0: Sequence[float]) -> list[float]:
    """(N, Vy, Mz) de I y J: p0 del elemento plano (convención de ``local_equivalent_loads``).

    La carga debe haberse expresado antes en la base 2D del elemento plano (la de
    ``planar_xz._beam_load``): «x» = eje de la barra en (x₁ = X, x₂ = Z), «y» = ese eje girado
    +90° en (x₁, x₂) y el par como componente «z», con θ = −Ry. No admite componentes
    locales 3D del dominio.
    """
    if len(p0) != LOCAL_DOFS:
        raise ValueError(f"p0 debe tener {LOCAL_DOFS} componentes")
    return [p0[i] for i in PLANAR_INDICES]
