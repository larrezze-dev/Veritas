"""Biblioteca de fuerzas de empotramiento (plan T-015, E2.0; ADR-005).

Referencias independientes del Gauss del código: fórmulas cerradas escritas a mano y
evaluadas con ``Fraction`` (R-FIX, R-PROP), equilibrio de p0 con la carga, coherencia con la
uniforme de T-006 (R-UNI), identidad bit a bit de ``fixed_end_forces`` y del código 3 de
``-beamUniform``, y contraste con ``eleLoad`` de OpenSeesPy en barras biempotradas (R-OPS,
hecho 1). Convención p0 del plan (tabla de la revisión de structural-reviewer): a desde I,
b = L − a, localForce = k·T·u + p0.
"""

import json
import random
import subprocess
import sys
from collections.abc import Callable, Sequence
from fractions import Fraction

import pytest

from veritas.fem.member_loads import (
    PLANAR_INDICES,
    distributed_fixed_end_forces,
    distributed_moment_fixed_end_forces,
    planar_view,
    point_fixed_end_forces,
    uniform_fixed_end_forces,
)
from veritas.fem.spatial.protocol import SpatialBeam, SpatialBeamLoad
from veritas.fem.spatial.stiffness import (
    condense,
    condense_beam,
    fixed_end_forces,
    local_beam_stiffness,
    released_indices,
)

type Ref = Sequence[Fraction | float]

L = 4  # N1: L ≠ 1 para detectar el 1/L de N1′ y N3′.
ZERO = (0.0, 0.0, 0.0)
FORCES = (0, 1, 2, 6, 7, 8)


def _assert_p0(actual: Sequence[float], expected: Ref, length: float = L) -> None:
    """Cota 1e-12 relativa al vector, con escalas separadas de fuerzas y de momentos / L (N4)."""
    forces = [float(expected[i]) for i in FORCES]
    moments = [float(expected[i]) / length for i in range(12) if i not in FORCES]
    scale = max(*map(abs, forces), *map(abs, moments), 1e-300)
    for i in range(12):
        ref = float(expected[i])
        diff = abs(actual[i] - ref) / (1.0 if i in FORCES else length)
        assert diff <= 1e-12 * scale, (i, actual[i], ref)


def _vec(entries: dict[int, Fraction]) -> list[Fraction]:
    out = [Fraction(0)] * 12
    for i, value in entries.items():
        out[i] = value
    return out


# --- R-FIX: barra biempotrada ---------------------------------------------------------


@pytest.mark.parametrize("a", [Fraction(1), Fraction(1, 2), Fraction(13, 5)])
def test_point_force_matches_closed_forms(a: Fraction) -> None:
    p, b = Fraction(-10), L - a
    xi = a / L
    y = point_fixed_end_forces(float(xi), (0.0, float(p), 0.0), ZERO, L)
    _assert_p0(
        y,
        _vec(
            {
                1: -p * b**2 * (L + 2 * a) / L**3,
                5: -p * a * b**2 / L**2,
                7: -p * a**2 * (L + 2 * b) / L**3,
                11: p * a**2 * b / L**2,
            }
        ),
    )
    z = point_fixed_end_forces(float(xi), (0.0, 0.0, float(p)), ZERO, L)
    _assert_p0(
        z,
        _vec(
            {
                2: -p * b**2 * (L + 2 * a) / L**3,
                4: p * a * b**2 / L**2,
                8: -p * a**2 * (L + 2 * b) / L**3,
                10: -p * a**2 * b / L**2,
            }
        ),
    )
    x = point_fixed_end_forces(float(xi), (float(p), 0.0, 0.0), ZERO, L)
    _assert_p0(x, _vec({0: -p * b / L, 6: -p * a / L}))


def test_point_force_reproduces_fact_1() -> None:
    """Hecho 1 (OpenSees, L = 4, P = −10 en ξ = 0,25): 8,4375, 5,625, 1,875 y N_I = 7,5."""
    p0 = point_fixed_end_forces(0.25, (-10.0, -10.0, 0.0), ZERO, L)
    assert p0[1] == pytest.approx(8.4375, rel=1e-14)
    assert p0[5] == pytest.approx(5.625, rel=1e-14)
    assert p0[11] == pytest.approx(-1.875, rel=1e-14)
    assert p0[0] == pytest.approx(7.5, rel=1e-14)


@pytest.mark.parametrize("a", [Fraction(1), Fraction(5, 2)])
def test_point_couples_match_closed_forms(a: Fraction) -> None:
    c, b = Fraction(7), L - a
    xi = float(a / L)
    _assert_p0(
        point_fixed_end_forces(xi, ZERO, (0.0, 0.0, float(c)), L),
        _vec(
            {
                1: 6 * c * a * b / L**3,
                5: c * b * (2 * a - b) / L**2,
                7: -6 * c * a * b / L**3,
                11: c * a * (2 * b - a) / L**2,
            }
        ),
    )
    _assert_p0(
        point_fixed_end_forces(xi, ZERO, (0.0, float(c), 0.0), L),
        _vec(
            {
                2: -6 * c * a * b / L**3,
                4: c * b * (2 * a - b) / L**2,
                8: 6 * c * a * b / L**3,
                10: c * a * (2 * b - a) / L**2,
            }
        ),
    )
    _assert_p0(
        point_fixed_end_forces(xi, ZERO, (float(c), 0.0, 0.0), L),
        _vec({3: -c * b / L, 9: -c * a / L}),
    )


def test_triangular_load_matches_closed_forms() -> None:
    """0 en I y w en J: V = 3wL/20 y 7wL/20, M = wL²/30 y wL²/20 (magnitudes)."""
    w = Fraction(-3)
    y = distributed_fixed_end_forces(0.0, 1.0, ZERO, (0.0, float(w), 0.0), L)
    _assert_p0(
        y,
        _vec(
            {
                1: -3 * w * L / 20,
                5: -w * L**2 / 30,
                7: -7 * w * L / 20,
                11: w * L**2 / 20,
            }
        ),
    )
    x = distributed_fixed_end_forces(0.0, 1.0, ZERO, (float(w), 0.0, 0.0), L)
    _assert_p0(x, _vec({0: -w * L / 6, 6: -w * L / 3}))


def _hermite_integrals(x1: Fraction, x2: Fraction) -> list[Fraction]:
    """∫N1…N4 dx en [x1, x2] por las primitivas de Hermite, escritas a mano."""
    ell = Fraction(L)
    primitives: tuple[Callable[[Fraction], Fraction], ...] = (
        lambda x: x - x**3 / ell**2 + x**4 / (2 * ell**3),
        lambda x: x**2 / 2 - 2 * x**3 / (3 * ell) + x**4 / (4 * ell**2),
        lambda x: x**3 / ell**2 - x**4 / (2 * ell**3),
        lambda x: -(x**3) / (3 * ell) + x**4 / (4 * ell**2),
    )
    return [f(x2) - f(x1) for f in primitives]


@pytest.mark.parametrize(("x1", "x2"), [(Fraction(1), Fraction(3)), (Fraction(0), Fraction(3, 2))])
def test_partial_uniform_matches_hermite_primitives(x1: Fraction, x2: Fraction) -> None:
    w = Fraction(-2)
    n1, n2, n3, n4 = (w * value for value in _hermite_integrals(x1, x2))
    segment = (float(x1 / L), float(x2 / L))
    y = distributed_fixed_end_forces(*segment, (0.0, float(w), 0.0), (0.0, float(w), 0.0), L)
    _assert_p0(y, _vec({1: -n1, 5: -n2, 7: -n3, 11: -n4}))
    z = distributed_fixed_end_forces(*segment, (0.0, 0.0, float(w)), (0.0, 0.0, float(w)), L)
    _assert_p0(z, _vec({2: -n1, 4: n2, 8: -n3, 10: n4}))


def test_partial_and_trapezoidal_reproduce_fact_1() -> None:
    """Hecho 1: parcial w = −2 en [1, 3] → 2,0 y 1,8333; trapezoidal −1 → −3 → 3,2, 4,8,
    2,4 y 2,9333."""
    partial = distributed_fixed_end_forces(0.25, 0.75, (0.0, -2.0, 0.0), (0.0, -2.0, 0.0), L)
    _assert_p0(
        partial, _vec({1: Fraction(2), 5: Fraction(11, 6), 7: Fraction(2), 11: -Fraction(11, 6)})
    )
    trapezoid = distributed_fixed_end_forces(0.0, 1.0, (0.0, -1.0, 0.0), (0.0, -3.0, 0.0), L)
    _assert_p0(
        trapezoid,
        _vec({1: Fraction(16, 5), 5: Fraction(12, 5), 7: Fraction(24, 5), 11: -Fraction(44, 15)}),
    )


def test_uniform_distributed_moment_gives_shear_pair() -> None:
    m = 5.0
    _assert_p0(
        distributed_moment_fixed_end_forces(0.0, 1.0, (0.0, 0.0, m), (0.0, 0.0, m), L),
        _vec({1: Fraction(5), 7: Fraction(-5)}),
    )
    _assert_p0(
        distributed_moment_fixed_end_forces(0.0, 1.0, (0.0, m, 0.0), (0.0, m, 0.0), L),
        _vec({2: Fraction(-5), 8: Fraction(5)}),
    )


def test_linear_distributed_moment_by_parts() -> None:
    """∫N′·m dx = N(L)·m1 − N(0)·m0 − (m1 − m0)/L·∫N dx (integración por partes)."""
    m0, m1 = Fraction(2), Fraction(-6)
    full = [Fraction(L, 2), Fraction(L**2, 12), Fraction(L, 2), -Fraction(L**2, 12)]
    at_0, at_l = (1, 0, 0, 0), (0, 0, 1, 0)
    g = [at_l[i] * m1 - at_0[i] * m0 - (m1 - m0) / L * full[i] for i in range(4)]
    mz = distributed_moment_fixed_end_forces(
        0.0, 1.0, (0.0, 0.0, float(m0)), (0.0, 0.0, float(m1)), L
    )
    _assert_p0(mz, _vec({1: -g[0], 5: -g[1], 7: -g[2], 11: -g[3]}))
    my = distributed_moment_fixed_end_forces(
        0.0, 1.0, (0.0, float(m0), 0.0), (0.0, float(m1), 0.0), L
    )
    _assert_p0(my, _vec({2: g[0], 4: -g[1], 8: g[2], 10: -g[3]}))


def test_partial_torsion_and_axial_are_linear() -> None:
    """∫(1 − x/L)·m dx y ∫(x/L)·m dx de un torsor uniforme en [x1, x2]."""
    x1, x2, m = Fraction(1), Fraction(3), Fraction(4)
    near_i = m * ((x2 - x1) - (x2**2 - x1**2) / (2 * L))
    near_j = m * (x2**2 - x1**2) / (2 * L)
    torsion = distributed_moment_fixed_end_forces(0.25, 0.75, (4.0, 0.0, 0.0), (4.0, 0.0, 0.0), L)
    _assert_p0(torsion, _vec({3: -near_i, 9: -near_j}))
    axial = distributed_fixed_end_forces(0.25, 0.75, (4.0, 0.0, 0.0), (4.0, 0.0, 0.0), L)
    _assert_p0(axial, _vec({0: -near_i, 6: -near_j}))


# --- Equilibrio de p0 con la carga (signos de los dos planos) -------------------------


def _equilibrium_residual(
    p0: Sequence[float], force: Sequence[float], moment_about_i: Sequence[float]
) -> tuple[list[float], list[float]]:
    """p0 + carga = 0: fuerzas, y momentos respecto a I con r × F = (0, −x·Fz, x·Fy) (N2)."""
    forces = [p0[k] + p0[6 + k] + force[k] for k in range(3)]
    j_arm = (0.0, -L * p0[8], L * p0[7])
    moments = [p0[3 + k] + p0[9 + k] + j_arm[k] + moment_about_i[k] for k in range(3)]
    return forces, moments


def _rand3(rng: random.Random) -> tuple[float, float, float]:
    return (rng.uniform(-9, 9), rng.uniform(-9, 9), rng.uniform(-9, 9))


@pytest.mark.parametrize("seed", range(20))
def test_fixed_end_forces_equilibrate_the_load(seed: int) -> None:
    rng = random.Random(seed)
    xi = rng.uniform(0.01, 0.99)
    f, c = _rand3(rng), _rand3(rng)
    x = xi * L
    cases: list[tuple[list[float], Sequence[float], Sequence[float]]] = [
        (point_fixed_end_forces(xi, f, c, L), f, [c[0], c[1] - x * f[2], c[2] + x * f[1]]),
    ]
    xa, xb = sorted((rng.uniform(0, 1), rng.uniform(0, 1)))
    qa, qb = _rand3(rng), _rand3(rng)
    da, db, span = xa * L, xb * L, (xb - xa) * L
    total = [(qa[k] + qb[k]) / 2 * span for k in range(3)]
    # ∫x·q dx de una carga lineal en [xa, xb], en forma cerrada.
    first = [span / 6 * (qa[k] * (2 * da + db) + qb[k] * (da + 2 * db)) for k in range(3)]
    cases.append(
        (distributed_fixed_end_forces(xa, xb, qa, qb, L), total, [0.0, -first[2], first[1]])
    )
    cases.append((distributed_moment_fixed_end_forces(xa, xb, qa, qb, L), ZERO, total))
    for p0, force, moment in cases:
        forces, moments = _equilibrium_residual(p0, force, moment)
        assert max(map(abs, forces)) <= 1e-12 * 100
        assert max(map(abs, moments)) <= 1e-12 * 100 * L


# --- R-UNI e identidad con T-006 ------------------------------------------------------


def _old_fixed_end_forces(load: SpatialBeamLoad, length: float) -> list[float]:
    """Copia literal de ``fixed_end_forces`` antes de E2.0."""
    ell = length
    p0 = [0.0] * 12
    p0[0] = p0[6] = -load.wx * ell / 2
    p0[1] = p0[7] = -load.wy * ell / 2
    p0[2] = p0[8] = -load.wz * ell / 2
    p0[5], p0[11] = -load.wy * ell**2 / 12, load.wy * ell**2 / 12
    p0[4], p0[10] = load.wz * ell**2 / 12, -load.wz * ell**2 / 12
    return p0


@pytest.mark.parametrize("seed", range(50))
def test_fixed_end_forces_delegation_is_bit_identical(seed: int) -> None:
    rng = random.Random(seed)
    load = SpatialBeamLoad(
        1,
        rng.uniform(-1e5, 1e5),
        rng.choice([0.0, -0.0, rng.uniform(-9, 9)]),
        rng.uniform(-1e3, 1e3),
    )
    length = rng.uniform(0.1, 50)
    assert fixed_end_forces(load, length) == _old_fixed_end_forces(load, length)
    old, new = _old_fixed_end_forces(load, length), fixed_end_forces(load, length)
    assert [repr(v) for v in new] == [repr(v) for v in old]  # También el signo de los ceros.


@pytest.mark.parametrize("seed", range(10))
def test_full_span_distributed_equals_uniform(seed: int) -> None:
    rng = random.Random(seed)
    q, length = _rand3(rng), rng.uniform(0.5, 20)
    uniform = uniform_fixed_end_forces(q[0], q[1], q[2], length)
    _assert_p0(distributed_fixed_end_forces(0.0, 1.0, q, q, length), uniform, length)


def test_planar_view_matches_planar_equivalent_loads() -> None:
    """−F_eq de ``local_equivalent_loads``:
    [wx·L/2, wy·L/2, wy·L²/12, wx·L/2, wy·L/2, −wy·L²/12]."""
    wx, wy = 3.0, -7.0
    f_eq = [wx * L / 2, wy * L / 2, wy * L**2 / 12, wx * L / 2, wy * L / 2, -wy * L**2 / 12]
    assert planar_view(uniform_fixed_end_forces(wx, wy, 0.0, L)) == [-v for v in f_eq]
    assert PLANAR_INDICES == (0, 1, 5, 6, 7, 11)


# --- R-PROP y código 3 generalizado ---------------------------------------------------


def _beam(release_y: int = 0, release_z: int = 0) -> SpatialBeam:
    return SpatialBeam(
        1,
        "b",
        1,
        2,
        0.01,
        2e11,
        8e10,
        2e-5,
        3e-5,
        1e-4,
        (0.0, 0.0, 1.0),
        release_y=release_y,
        release_z=release_z,
    )


def _condensed(beam: SpatialBeam, p0: Sequence[float]) -> list[float]:
    _, out = condense_beam(beam, local_beam_stiffness(beam, L), p0, length=L)
    assert out is not None
    return out


def test_propped_cantilever_point_load() -> None:
    """Extremo articulado en I (y en J, intercambiando a y b) en los dos planos (R-PROP)."""
    p, a = Fraction(-10), Fraction(1)
    b, xi = L - a, float(a / L)
    r_i = p * b**2 * (a + 2 * L) / (2 * L**3)
    m_j = p * a * b * (L + a) / (2 * L**2)
    py = point_fixed_end_forces(xi, (0.0, float(p), 0.0), ZERO, L)
    _assert_p0(_condensed(_beam(release_z=1), py), _vec({1: -r_i, 7: -(p - r_i), 11: m_j}))
    pz = point_fixed_end_forces(xi, (0.0, 0.0, float(p)), ZERO, L)
    _assert_p0(_condensed(_beam(release_y=1), pz), _vec({2: -r_i, 8: -(p - r_i), 10: -m_j}))
    r_j = p * a**2 * (b + 2 * L) / (2 * L**3)
    m_i = p * a * b * (L + b) / (2 * L**2)
    _assert_p0(_condensed(_beam(release_z=2), py), _vec({1: -(p - r_j), 5: -m_i, 7: -r_j}))
    # Hecho 2 (sonda 2D): R_I = 6,328 y M_J = 4,6875 en magnitud.
    assert float(r_i) == pytest.approx(-6.328125)
    assert float(m_j) == pytest.approx(-4.6875)


def test_code_3_gives_simply_supported_reactions() -> None:
    p, c, a = Fraction(-10), Fraction(7), Fraction(1)
    b, xi = L - a, float(a / L)
    both = _beam(release_y=3, release_z=3)
    _assert_p0(
        _condensed(both, point_fixed_end_forces(xi, (0.0, float(p), float(p)), ZERO, L)),
        _vec({1: -p * b / L, 7: -p * a / L, 2: -p * b / L, 8: -p * a / L}),
    )
    _assert_p0(
        _condensed(both, point_fixed_end_forces(xi, ZERO, (0.0, float(c), float(c)), L)),
        _vec({1: c / L, 7: -c / L, 2: -c / L, 8: c / L}),
    )
    # N7: momento distribuido parcial, M = m·Δ en total.
    m, span = Fraction(3), Fraction(2)
    moment = distributed_moment_fixed_end_forces(0.25, 0.75, (0.0, 3.0, 3.0), (0.0, 3.0, 3.0), L)
    _assert_p0(
        _condensed(both, moment),
        _vec({1: m * span / L, 7: -m * span / L, 2: -m * span / L, 8: m * span / L}),
    )


@pytest.mark.parametrize(("release_y", "release_z"), [(3, 0), (0, 3), (3, 1), (2, 3), (3, 3)])
def test_code_3_closed_form_matches_elimination(release_y: int, release_z: int) -> None:
    """N3 y N5: la forma cerrada = la eliminación de ``condense`` antes de sobrescribirla; el
    axil y el torsor no cambian."""
    rng = random.Random(release_y * 10 + release_z)
    beam = _beam(release_y, release_z)
    k = local_beam_stiffness(beam, L)
    p0 = [
        a + b
        for a, b in zip(
            point_fixed_end_forces(0.3, _rand3(rng), _rand3(rng), L),
            distributed_moment_fixed_end_forces(0.1, 0.6, _rand3(rng), _rand3(rng), L),
            strict=True,
        )
    ]
    _, eliminated = condense(k, released_indices(beam), p0)
    assert eliminated is not None
    closed = _condensed(beam, p0)
    scale = max(map(abs, p0))
    for i in (1, 2, 7, 8):
        assert closed[i] == pytest.approx(eliminated[i], abs=1e-9 * scale)
    for i in (0, 3, 6, 9):
        assert closed[i] == p0[i]


@pytest.mark.parametrize("seed", range(20))
def test_code_3_uniform_load_keeps_current_path(seed: int) -> None:
    """Con M_I + M_J == 0 exacto, el camino generalizado es el actual (``==`` y ceros)."""
    rng = random.Random(seed)
    load = SpatialBeamLoad(
        1, rng.uniform(-9, 9), rng.choice([0.0, rng.uniform(-9, 9)]), rng.uniform(-9, 9)
    )
    length = rng.uniform(0.5, 20)
    p0 = fixed_end_forces(load, length)
    for beam in (_beam(3, 0), _beam(0, 3), _beam(3, 3), _beam(3, 1)):
        k = local_beam_stiffness(beam, length)
        _, current = condense_beam(beam, k, p0)
        _, general = condense_beam(beam, k, p0, length=length)
        assert current is not None
        assert general is not None
        assert [repr(v) for v in general] == [repr(v) for v in current]


def test_code_3_superposition() -> None:
    """N8: el código 3 sobre el p0 total = la suma de los p0 condensados de cada carga."""
    both = _beam(3, 3)
    first = point_fixed_end_forces(0.2, (1.0, -4.0, 2.0), (0.5, 3.0, -1.0), L)
    second = distributed_fixed_end_forces(0.3, 0.9, (0.0, 2.0, -5.0), (1.0, -3.0, 4.0), L)
    total = _condensed(both, [a + b for a, b in zip(first, second, strict=True)])
    parts = [a + b for a, b in zip(_condensed(both, first), _condensed(both, second), strict=True)]
    _assert_p0(total, parts)


# --- Casos límite y entradas no válidas (N6) ------------------------------------------


def test_short_segment_tends_to_point_load() -> None:
    q, span = 1e6, 1e-6
    short = distributed_fixed_end_forces(0.4, 0.4 + span, (0.0, q, q), (0.0, q, q), L)
    point = point_fixed_end_forces(0.4 + span / 2, (0.0, q * span * L, q * span * L), ZERO, L)
    for got, ref in zip(short, point, strict=True):
        assert got == pytest.approx(ref, rel=1e-9, abs=1e-9)


def test_point_load_near_end_goes_to_node() -> None:
    p0 = point_fixed_end_forces(1e-6, (2.0, -3.0, 4.0), ZERO, L)
    assert p0[0] == pytest.approx(-2.0, rel=1e-5)
    assert p0[1] == pytest.approx(3.0, rel=1e-5)
    assert p0[2] == pytest.approx(-4.0, rel=1e-5)


@pytest.mark.parametrize(
    "call",
    [
        lambda: point_fixed_end_forces(0.0, (1.0, 0.0, 0.0), ZERO, L),
        lambda: point_fixed_end_forces(1.0, (1.0, 0.0, 0.0), ZERO, L),
        lambda: point_fixed_end_forces(0.5, (float("nan"), 0.0, 0.0), ZERO, L),
        lambda: point_fixed_end_forces(0.5, ZERO, ZERO, 0.0),
        lambda: point_fixed_end_forces(0.5, ZERO, ZERO, float("inf")),
        lambda: distributed_fixed_end_forces(0.6, 0.4, ZERO, ZERO, L),
        lambda: distributed_fixed_end_forces(-0.1, 0.4, ZERO, ZERO, L),
        lambda: distributed_fixed_end_forces(0.4, 1.1, ZERO, ZERO, L),
        lambda: distributed_moment_fixed_end_forces(0.4, 0.4 + 1e-10, ZERO, ZERO, L),
        lambda: distributed_moment_fixed_end_forces(0.0, 1.0, (float("inf"), 0, 0), ZERO, L),
        lambda: planar_view([0.0] * 6),
    ],
)
def test_invalid_input_raises(call: Callable[[], object]) -> None:
    with pytest.raises(ValueError, match=r"no válid|no finita|componentes"):
        call()


# --- R-OPS: contraste con OpenSeesPy (hecho 1) ----------------------------------------

_OPS_PROBE = r"""
import json, sys
import openseespy.opensees as ops

def run(ndm, load):
    ops.wipe()
    ops.model("basic", "-ndm", ndm, "-ndf", 3 if ndm == 2 else 6)
    if ndm == 2:
        for tag, xy in ((1, (0, 0)), (2, (4, 0)), (3, (4, 1))):
            ops.node(tag, *xy)
        ops.fix(1, 1, 1, 1); ops.fix(2, 1, 1, 1)
        ops.geomTransf("Linear", 1)
        for tag, (i, j) in ((1, (1, 2)), (2, (2, 3))):
            ops.element("elasticBeamColumn", tag, i, j, 0.01, 2e11, 1e-4, 1)
    else:
        for tag, xyz in ((1, (0, 0, 0)), (2, (4, 0, 0)), (3, (4, 1, 0))):
            ops.node(tag, *xyz)
        ops.fix(1, 1, 1, 1, 1, 1, 1); ops.fix(2, 1, 1, 1, 1, 1, 1)
        ops.geomTransf("Linear", 1, 0.0, 0.0, 1.0)
        for tag, (i, j) in ((1, (1, 2)), (2, (2, 3))):
            ops.element("elasticBeamColumn", tag, i, j, 0.01, 2e11, 8e10, 2e-5, 3e-5, 1e-4, 1)
    ops.timeSeries("Linear", 1)
    ops.pattern("Plain", 1, 1)
    ops.eleLoad("-ele", 1, "-type", *load)
    ops.load(3, *([1.0] + [0.0] * (2 if ndm == 2 else 5)))  # La barra auxiliar trabaja.
    ops.constraints("Plain"); ops.numberer("RCM"); ops.system("BandGeneral")
    ops.test("NormDispIncr", 1e-12, 10); ops.algorithm("Linear")
    ops.integrator("LoadControl", 1.0); ops.analysis("Static")
    assert ops.analyze(1) == 0
    out = list(ops.eleResponse(1, "localForce"))
    ops.wipe()
    return out

cases = json.loads(sys.argv[1])
print(json.dumps([run(ndm, load) for ndm, load in cases]))
"""


@pytest.mark.solver
def test_fixed_beam_matches_opensees_ele_load() -> None:
    cases: list[tuple[int, list[object], list[float]]] = [
        # 2D: localForce (N, V, M) de I y J = planar_view(p0).
        (
            2,
            ["-beamPoint", -10.0, 0.25, -6.0],
            planar_view(point_fixed_end_forces(0.25, (-6.0, -10.0, 0.0), ZERO, L)),
        ),
        (
            2,
            ["-beamUniform", -2.0, 1.5, 0.25, 0.75],
            planar_view(
                distributed_fixed_end_forces(0.25, 0.75, (1.5, -2.0, 0.0), (1.5, -2.0, 0.0), L)
            ),
        ),
        (
            2,
            ["-beamUniform", -1.0, 0.0, 0.0, 1.0, -3.0, 0.0],
            planar_view(
                distributed_fixed_end_forces(0.0, 1.0, (0.0, -1.0, 0.0), (0.0, -3.0, 0.0), L)
            ),
        ),
        # 3D (vecxz = Z: ey = Y, ez = Z): localForce de 12 = p0.
        (
            3,
            ["-beamPoint", -10.0, 4.0, 0.25, -6.0],
            point_fixed_end_forces(0.25, (-6.0, -10.0, 4.0), ZERO, L),
        ),
        (
            3,
            ["-beamUniform", -2.0, 3.0, 1.5, 0.25, 0.75],
            distributed_fixed_end_forces(0.25, 0.75, (1.5, -2.0, 3.0), (1.5, -2.0, 3.0), L),
        ),
    ]
    payload = json.dumps([[ndm, load] for ndm, load, _ in cases])
    completed = subprocess.run(
        [sys.executable, "-c", _OPS_PROBE, payload],
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )
    results = json.loads(completed.stdout.strip().splitlines()[-1])
    for (ndm, load, expected), actual in zip(cases, results, strict=True):
        scale = max(map(abs, expected))
        assert len(actual) == len(expected), (ndm, load)
        for got, ref in zip(actual, expected, strict=True):
            assert got == pytest.approx(ref, abs=1e-9 * scale), (ndm, load, actual, expected)
