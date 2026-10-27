"""Base local de barra (ADR-001 §1, revisión A1-a; plan T-003).

Referencias independientes: filas literales de la tabla de ADR-001 §2 y álgebra vectorial
hecha a mano. Tolerancias: 1e-14 absoluta para componentes de orden 1 (unas decenas de
ulp; cos(π/2) = 6.1e-17 no es 0) y ‖Δv‖ ≤ 1e-14·‖v‖ en la ida y vuelta.
"""

import math
import random

import pytest

from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError
from veritas.core.geometry.local_axes import (
    MIN_MEMBER_LENGTH,
    ORIENTATION_TOLERANCE,
    LocalAxes,
    Orientation,
    ReferenceSource,
    compute_local_axes,
    recompute_local_axes,
)
from veritas.core.geometry.vector import Vec3

TOL = 1e-14
ORIGIN = Vec3(0.0, 0.0, 0.0)
X, Y, Z = Vec3(1.0, 0.0, 0.0), Vec3(0.0, 1.0, 0.0), Vec3(0.0, 0.0, 1.0)


def assert_vec(actual: Vec3, expected: Vec3, tol: float = TOL) -> None:
    assert (actual - expected).norm() <= tol, f"{actual} != {expected}"


def assert_orthonormal_right_handed(axes: LocalAxes) -> None:
    rows = (axes.ex, axes.ey, axes.ez)
    for i, a in enumerate(rows):
        for j, b in enumerate(rows):
            assert abs(a.dot(b) - (1.0 if i == j else 0.0)) <= TOL
    assert abs(axes.ex.dot(axes.ey.cross(axes.ez)) - 1.0) <= TOL


def assert_round_trip(axes: LocalAxes) -> None:
    for v in (Vec3(1.0, -2.0, 3.0), Vec3(-1e3, 5e2, 7.0), Vec3(0.3, 0.0, -0.1)):
        back = axes.to_global(axes.to_local(v))
        assert (back - v).norm() <= TOL * v.norm()
        again = axes.to_local(axes.to_global(v))
        assert (again - v).norm() <= TOL * v.norm()


def check(axes: LocalAxes) -> LocalAxes:
    assert_orthonormal_right_handed(axes)
    assert_round_trip(axes)
    return axes


# --- Filas de la tabla de ADR-001 §2 ----------------------------------------------------


@pytest.mark.parametrize(
    ("j", "ex", "ey", "ez", "source"),
    [
        (Vec3(2.0, 0.0, 0.0), X, Y, Z, ReferenceSource.DEFAULT_Z),
        (Vec3(0.0, 2.0, 0.0), Y, -X, Z, ReferenceSource.DEFAULT_Z),
        (Vec3(-2.0, 0.0, 0.0), -X, -Y, Z, ReferenceSource.DEFAULT_Z),
        (Vec3(0.0, 0.0, 2.0), Z, Y, -X, ReferenceSource.FALLBACK_NEG_X),
        (Vec3(0.0, 0.0, -2.0), -Z, -Y, -X, ReferenceSource.FALLBACK_NEG_X),
    ],
)
def test_adr_reference_rows(j: Vec3, ex: Vec3, ey: Vec3, ez: Vec3, source: ReferenceSource) -> None:
    axes = check(compute_local_axes(ORIGIN, j))
    assert_vec(axes.ex, ex)
    assert_vec(axes.ey, ey)
    assert_vec(axes.ez, ez)
    assert axes.orientation.source is source
    assert axes.length == 2.0


def test_alpha_quarter_turn_on_x() -> None:
    axes = check(compute_local_axes(ORIGIN, Vec3(2.0, 0.0, 0.0), alpha=math.pi / 2))
    assert_vec(axes.ex, X)
    assert_vec(axes.ey, Z)
    assert_vec(axes.ez, -Y)


def test_non_vertical_members_get_ez_up() -> None:
    for angle in range(0, 360, 15):
        c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
        axes = check(compute_local_axes(ORIGIN, Vec3(3 * c, 3 * s, 1.0)))
        assert axes.ez.z > 0.0
        assert abs(axes.ey.z) <= TOL  # ey horizontal sin giro alfa.


# --- Sentido de Q (no basta QQ^T = I, que también cumple Q^T) ----------------------------


def test_q_direction_with_quarter_turn() -> None:
    axes = compute_local_axes(ORIGIN, Vec3(2.0, 0.0, 0.0), alpha=math.pi / 2)
    assert_vec(axes.to_local(Z), Vec3(0.0, 1.0, 0.0))
    assert_vec(axes.to_global(Vec3(0.0, 0.0, 1.0)), -Y)
    assert axes.matrix() == (axes.ex.components(), axes.ey.components(), axes.ez.components())


def test_q_direction_oblique_by_hand() -> None:
    # Barra (1,1,0): ex = (1,1,0)/√2; ez0 = Z; ey0 = Z × ex = (-1,1,0)/√2.
    axes = check(compute_local_axes(ORIGIN, Vec3(1.0, 1.0, 0.0)))
    r = 1.0 / math.sqrt(2.0)
    assert_vec(axes.ex, Vec3(r, r, 0.0))
    assert_vec(axes.ey, Vec3(-r, r, 0.0))
    assert_vec(axes.ez, Z)
    # Global +X en local: (ex·X, ey·X, ez·X) = (r, -r, 0).
    assert_vec(axes.to_local(X), Vec3(r, -r, 0.0))


def test_oblique_members_with_alpha() -> None:
    for alpha in (0.0, 0.3, -1.2, 2.5):
        axes = check(compute_local_axes(Vec3(0.5, -1.0, 2.0), Vec3(1.5, 1.0, 5.0), alpha=alpha))
        assert axes.length == pytest.approx(math.sqrt(14.0), rel=1e-15)
        assert_vec(axes.ex, Vec3(1.0, 2.0, 3.0).scaled(1.0 / math.sqrt(14.0)))


# --- Umbral 1e-8 (frontera exacta en float) ---------------------------------------------

BELOW = math.nextafter(ORIENTATION_TOLERANCE, 0.0)
ABOVE = math.nextafter(ORIENTATION_TOLERANCE, 1.0)


@pytest.mark.parametrize(
    ("offset", "source"),
    [
        (BELOW, ReferenceSource.FALLBACK_NEG_X),
        (ORIENTATION_TOLERANCE, ReferenceSource.FALLBACK_NEG_X),
        (ABOVE, ReferenceSource.DEFAULT_Z),
    ],
)
def test_default_reference_threshold(offset: float, source: ReferenceSource) -> None:
    # J = (y, 0, 1): L = hypot(y, 1) = 1 exacto y ‖Z - (Z·ex)ex‖ = y exacto.
    axes = check(compute_local_axes(ORIGIN, Vec3(offset, 0.0, 1.0)))
    assert axes.length == 1.0
    assert axes.orientation.source is source


@pytest.mark.parametrize("offset", [BELOW, ORIENTATION_TOLERANCE])
def test_explicit_reference_at_or_below_threshold_is_rejected(offset: float) -> None:
    with pytest.raises(InputValueError) as info:
        compute_local_axes(ORIGIN, Vec3(2.0, 0.0, 0.0), reference=Vec3(1.0, 0.0, offset))
    assert info.value.code is DiagnosticCode.GEOMETRY_INVALID_ORIENTATION
    assert info.value.diagnostic.message_key == "geometry.reference_parallel"


def test_explicit_reference_above_threshold_is_accepted() -> None:
    axes = check(compute_local_axes(ORIGIN, Vec3(2.0, 0.0, 0.0), reference=Vec3(1.0, 0.0, ABOVE)))
    assert_vec(axes.ez, Z)
    assert axes.orientation.source is ReferenceSource.EXPLICIT


def test_reorthogonalization_near_threshold() -> None:
    # Sin segunda pasada de Gram-Schmidt |ey·ex| llega a ~2e-8 en este barrido.
    rng = random.Random(20261027)
    for _ in range(2000):
        t, phi = rng.uniform(1.5e-8, 3e-8), rng.uniform(0.0, 2 * math.pi)
        axes = compute_local_axes(ORIGIN, Vec3(t * math.cos(phi), t * math.sin(phi), 1.0))
        assert axes.orientation.source is ReferenceSource.DEFAULT_Z
        assert_orthonormal_right_handed(axes)


# --- Referencias explícitas ------------------------------------------------------------


def test_explicit_reference_is_projected_and_stored_normalized() -> None:
    axes = check(compute_local_axes(ORIGIN, Vec3(2.0, 0.0, 0.0), reference=Vec3(0.0, 1.0, 1.0)))
    r = 1.0 / math.sqrt(2.0)
    assert_vec(axes.ez, Vec3(0.0, r, r))
    assert_vec(axes.ey, Vec3(0.0, r, -r))
    assert_vec(axes.orientation.reference, Vec3(0.0, r, r))
    # No perpendicular: se proyecta.
    axes = check(compute_local_axes(ORIGIN, Vec3(2.0, 0.0, 0.0), reference=Vec3(5.0, 0.0, 2.0)))
    assert_vec(axes.ez, Z)
    assert axes.orientation.reference.norm() == pytest.approx(1.0, abs=TOL)


def test_negative_reference_flips_section() -> None:
    axes = check(compute_local_axes(ORIGIN, Vec3(2.0, 0.0, 0.0), reference=-Z))
    assert_vec(axes.ez, -Z)
    assert_vec(axes.ey, -Y)


def test_explicit_reference_never_falls_back() -> None:
    with pytest.raises(InputValueError) as info:
        compute_local_axes(ORIGIN, Vec3(0.0, 0.0, 3.0), reference=Z)
    assert info.value.code is DiagnosticCode.GEOMETRY_INVALID_ORIENTATION


def test_zero_reference_is_rejected() -> None:
    with pytest.raises(InputValueError) as info:
        compute_local_axes(ORIGIN, Vec3(2.0, 0.0, 0.0), reference=Vec3(0.0, 0.0, 0.0))
    assert info.value.diagnostic.message_key == "geometry.reference_invalid"


def test_huge_reference_is_normalized_without_overflow() -> None:
    axes = compute_local_axes(ORIGIN, Vec3(2.0, 0.0, 0.0), reference=Vec3(0.0, 0.0, 1e300))
    assert_vec(axes.ez, Z)


# --- Inversión I/J (función de base; no hay operación de modelo, D7) ---------------------


@pytest.mark.parametrize("j", [Vec3(2.0, 0.0, 0.0), Vec3(1.0, 2.0, 0.5), Vec3(0.0, 0.0, 2.0)])
def test_reversal_keeps_ez_and_flips_ex_ey(j: Vec3) -> None:
    forward = compute_local_axes(ORIGIN, j)
    backward = compute_local_axes(j, ORIGIN)
    assert_vec(backward.ex, -forward.ex)
    assert_vec(backward.ey, -forward.ey)
    assert_vec(backward.ez, forward.ez)


def test_reversal_with_alpha_requires_negated_alpha() -> None:
    j = Vec3(1.0, 2.0, 0.5)
    forward = compute_local_axes(ORIGIN, j, alpha=0.7)
    backward = compute_local_axes(j, ORIGIN, alpha=-0.7)
    assert_vec(backward.ez, forward.ez)
    assert_vec(backward.ey, -forward.ey)


# --- Giro alfa -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("alpha", "ey", "ez"),
    [
        (0.0, Y, Z),
        (math.pi / 2, Z, -Y),
        (math.pi, -Y, -Z),
        (-math.pi / 2, -Z, Y),
    ],
)
def test_alpha_values(alpha: float, ey: Vec3, ez: Vec3) -> None:
    axes = check(compute_local_axes(ORIGIN, Vec3(2.0, 0.0, 0.0), alpha=alpha))
    assert_vec(axes.ey, ey)
    assert_vec(axes.ez, ez)
    assert axes.orientation.alpha == alpha  # Se guarda sin envolver.


def test_full_turn_equals_zero_but_is_stored_as_given() -> None:
    zero = compute_local_axes(ORIGIN, Vec3(2.0, 0.0, 0.0))
    full = compute_local_axes(ORIGIN, Vec3(2.0, 0.0, 0.0), alpha=2 * math.pi)
    assert_vec(full.ey, zero.ey, 1e-15)  # sin(2π) = -2.45e-16 en float.
    assert_vec(full.ez, zero.ez, 1e-15)
    assert full.orientation != zero.orientation


@pytest.mark.parametrize(
    ("alpha", "code"),
    [
        (math.nextafter(2 * math.pi, 7.0), DiagnosticCode.GEOMETRY_INVALID_ORIENTATION),
        (-7.0, DiagnosticCode.GEOMETRY_INVALID_ORIENTATION),
        (math.nan, DiagnosticCode.INPUT_NONFINITE),
        (math.inf, DiagnosticCode.INPUT_NONFINITE),
    ],
)
def test_invalid_alpha(alpha: float, code: DiagnosticCode) -> None:
    with pytest.raises(InputValueError) as info:
        compute_local_axes(ORIGIN, Vec3(2.0, 0.0, 0.0), alpha=alpha)
    assert info.value.code is code


def test_bool_alpha_is_rejected() -> None:
    with pytest.raises(InputTypeError):
        compute_local_axes(ORIGIN, Vec3(2.0, 0.0, 0.0), alpha=True)


# --- Longitud --------------------------------------------------------------------------


def test_length_threshold() -> None:
    check(compute_local_axes(ORIGIN, Vec3(MIN_MEMBER_LENGTH, 0.0, 0.0)))
    for length in (0.0, math.nextafter(MIN_MEMBER_LENGTH, 0.0)):
        with pytest.raises(InputValueError) as info:
            compute_local_axes(ORIGIN, Vec3(length, 0.0, 0.0))
        assert info.value.code is DiagnosticCode.GEOMETRY_ZERO_LENGTH
        assert info.value.diagnostic.message_key == "geometry.zero_length"


def test_unrepresentable_length_is_rejected() -> None:
    with pytest.raises(InputValueError) as info:
        compute_local_axes(Vec3(-1e308, 0.0, 0.0), Vec3(1e308, 0.0, 0.0))
    assert info.value.code is DiagnosticCode.GEOMETRY_ZERO_LENGTH
    assert info.value.diagnostic.message_key == "geometry.length_unrepresentable"


def test_large_but_finite_coordinates() -> None:
    axes = check(compute_local_axes(Vec3(1e200, 0.0, 0.0), Vec3(1e200, 3e200, 0.0)))
    assert_vec(axes.ex, Y)


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_nonfinite_coordinates_are_rejected(bad: float) -> None:
    with pytest.raises(InputValueError) as info:
        Vec3(0.0, bad, 0.0)
    assert info.value.code is DiagnosticCode.INPUT_NONFINITE


# --- Orientación guardada --------------------------------------------------------------


def test_recompute_uses_stored_reference_without_fallback() -> None:
    stored = Orientation(Z, ReferenceSource.DEFAULT_Z)
    axes = recompute_local_axes(ORIGIN, Vec3(1.0, 1.0, 0.0), stored)
    assert_vec(axes.ez, Z)
    with pytest.raises(InputValueError) as info:
        recompute_local_axes(ORIGIN, Vec3(0.0, 0.0, 4.0), stored)
    assert info.value.code is DiagnosticCode.GEOMETRY_INVALID_ORIENTATION
    assert info.value.diagnostic.message_key == "geometry.stored_reference_degenerate"


def test_orientation_requires_unit_reference() -> None:
    with pytest.raises(ValueError, match="normalizada"):
        Orientation(Vec3(0.0, 0.0, 2.0), ReferenceSource.EXPLICIT)


def test_subnormal_reference_is_rejected_as_invalid() -> None:
    with pytest.raises(InputValueError) as info:
        compute_local_axes(ORIGIN, Vec3(2.0, 0.0, 0.0), reference=Vec3(0.0, 0.0, 1e-310))
    assert info.value.code is DiagnosticCode.GEOMETRY_INVALID_ORIENTATION
    assert info.value.diagnostic.message_key == "geometry.reference_invalid"


def test_tiny_normal_reference_is_accepted() -> None:
    axes = compute_local_axes(ORIGIN, Vec3(2.0, 0.0, 0.0), reference=Vec3(0.0, 0.0, 1e-300))
    assert_vec(axes.ez, Z)


def test_huge_integers_are_diagnosed() -> None:
    with pytest.raises(InputValueError) as info:
        Vec3(10**400, 0, 0)
    assert info.value.code is DiagnosticCode.UNITS_UNREPRESENTABLE
    with pytest.raises(InputValueError) as info:
        compute_local_axes(ORIGIN, Vec3(2.0, 0.0, 0.0), alpha=10**400)
    assert info.value.code is DiagnosticCode.INPUT_NONFINITE


def test_to_local_overflow_is_diagnosed() -> None:
    axes = compute_local_axes(ORIGIN, Vec3(1.0, 1.0, 0.0))
    with pytest.raises(InputValueError) as info:
        axes.to_local(Vec3(1.7e308, 1.7e308, 0.0))
    assert info.value.code is DiagnosticCode.UNITS_UNREPRESENTABLE


def test_orientation_source_must_match_reference() -> None:
    with pytest.raises(ValueError, match="DEFAULT_Z"):
        Orientation(Y, ReferenceSource.DEFAULT_Z)
    with pytest.raises(ValueError, match="FALLBACK_NEG_X"):
        Orientation(X, ReferenceSource.FALLBACK_NEG_X)
