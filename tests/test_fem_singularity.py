"""Salvaguarda de singularidad D3 (plan T-007): matrices sintéticas, batería de mecanismos y
estructuras legítimas esbeltas para calibrar ``tol_pivot`` (revisión B1).
"""

import math
from collections.abc import Callable
from itertools import pairwise

import pytest

from fem_support import Frame, inclined
from veritas.core.geometry.entities import Node
from veritas.core.supports.support import (
    FIXED,
    FREE,
    DofRestraint,
    SupportBasis,
    SupportDof,
    fixed_restraints,
    pinned_restraints,
    roller_restraints,
)
from veritas.core.units import Quantity
from veritas.fem import RunStatus, analyze_planar_xz
from veritas.fem.adapters.planar_xz import translate_planar_xz
from veritas.fem.adapters.singularity import (
    InvalidStiffnessError,
    PivotReport,
    check_singularity,
    pivot_check,
)

TOL = 1e-12
# Margen exigido a las estructuras legítimas: su pivote escalado debe superar este valor
# (al menos dos órdenes por encima de ``tol_pivot``). El pivote baja con la esbeltez total:
# un voladizo girado de L/h = 5000 da unos 8,7e-10.
LEGIT_MIN = 1e-10


def report(frame: Frame) -> PivotReport:
    request = translate_planar_xz(frame.model, frame.config()).request
    return check_singularity(request, TOL)


def beam_line(frame: Frame, length: float, segments: int, angle_deg: float = 0.0) -> list[Node]:
    c, s = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    step = length / segments
    nodes = [frame.node(step * k * c, step * k * s) for k in range(segments + 1)]
    for a, b in pairwise(nodes):
        frame.member(a, b)
    frame.nodal(nodes[-1], fz=-1000.0)
    return nodes


# --- Matrices sintéticas -------------------------------------------------------------


def test_empty_matrix_is_not_singular() -> None:
    assert pivot_check([], TOL) == (None, None, False)


@pytest.mark.parametrize("matrix", [[[-1.0]], [[math.nan]], [[1.0, 0.0], [0.0, math.inf]]])
def test_negative_or_nonfinite_diagonal_is_internal_error(matrix: list[list[float]]) -> None:
    with pytest.raises(InvalidStiffnessError):
        pivot_check(matrix, TOL)


def test_zero_or_subnormal_diagonal_is_singular() -> None:
    # Giro libre sin rigidez: la fila y la columna del GDL son nulas.
    assert pivot_check([[4.0, 0.0], [0.0, 0.0]], TOL) == (0.0, 1, True)
    assert pivot_check([[1.0, 0.0], [0.0, 1e-320]], TOL)[2]


def test_small_but_normal_decoupled_diagonal_is_not_singular() -> None:
    # La escala absoluta de la diagonal no decide (unidades mixtas): lo decide la matriz escalada.
    assert pivot_check([[1e20, 0.0], [0.0, 1e-13]], TOL) == (1.0, 0, False)


def test_rank_deficient_matrix_is_singular() -> None:
    pivot, index, singular = pivot_check([[2.0, -2.0], [-2.0, 2.0]], TOL)
    assert singular
    assert pivot is not None
    assert abs(pivot) <= TOL
    assert index in (0, 1)


def test_well_conditioned_matrix_reports_min_pivot() -> None:
    pivot, _, singular = pivot_check([[4.0, 2.0], [2.0, 3.0]], TOL)
    assert not singular
    # Escalada: [[1, a], [a, 1]] con a = 2/√12; pivote mínimo 1 − a².
    assert pivot == pytest.approx(1 - 4 / 12, rel=1e-12)


# --- Mecanismos: nunca SUCCEEDED -----------------------------------------------------


def _two_vertical_rollers(frame: Frame) -> None:
    a, _, b = beam_line(frame, 4.0, 2)
    frame.support(a, roller_restraints())
    frame.support(b, roller_restraints())


def _rotated_rollers(frame: Frame, segments: int) -> None:
    nodes = beam_line(frame, 4.0, segments, 37.0)
    basis = inclined(37.0)  # x′ a lo largo de la barra; el rodillo fija z′ (normal)
    frame.support(nodes[0], roller_restraints(), basis)
    frame.support(nodes[-1], roller_restraints(), basis)


def _hinge_and_roller_through_hinge(frame: Frame) -> None:
    nodes = beam_line(frame, 4.0, 3, 37.0)
    frame.support(nodes[0], pinned_restraints())
    # El rodillo fija x′ = eje de la barra: su recta de acción pasa por la articulación.
    frame.support(nodes[-1], roller_restraints(SupportDof.UX), inclined(37.0))


def _no_supports(frame: Frame) -> None:
    beam_line(frame, 4.0, 2)


def _disconnected_unsupported(frame: Frame) -> None:
    a, b = frame.node(0, 0), frame.node(2, 0)
    frame.member(a, b)
    frame.support(a, fixed_restraints())
    c, d = frame.node(5, 1), frame.node(7, 2)
    frame.member(c, d)
    frame.nodal(d, fz=-1.0)


def _out_of_plane_only(frame: Frame) -> None:
    a, _, b = beam_line(frame, 4.0, 2)
    only = (FREE, FIXED, FREE, FIXED, FREE, FIXED)
    frame.support(a, only)
    frame.support(b, only)


MECHANISMS: dict[str, Callable[[Frame], None]] = {
    "dos rodillos verticales": _two_vertical_rollers,
    "rodillos girados 37° (1 barra)": lambda f: _rotated_rollers(f, 1),
    "rodillos girados 37° (7 barras)": lambda f: _rotated_rollers(f, 7),
    "articulación + rodillo por la articulación": _hinge_and_roller_through_hinge,
    "sin apoyos": _no_supports,
    "subestructura desconectada sin apoyo": _disconnected_unsupported,
    "apoyos solo fuera del plano": _out_of_plane_only,
}


@pytest.mark.parametrize("name", list(MECHANISMS))
def test_mechanisms_are_singular(name: str) -> None:
    frame = Frame()
    MECHANISMS[name](frame)
    result = report(frame)
    assert result.singular, (name, result)
    assert result.dof is not None


# --- Estructuras legítimas: margen frente a tol_pivot --------------------------------


def _slender(length: float, segments: int, angle: float = 0.0) -> Callable[[Frame], None]:
    def build(frame: Frame) -> None:
        nodes = beam_line(frame, length, segments, angle)
        frame.support(nodes[0], pinned_restraints())
        basis = inclined(angle) if angle else SupportBasis.GLOBAL
        frame.support(nodes[-1], roller_restraints(), basis)

    return build


def _inclined_roller(frame: Frame) -> None:
    nodes = beam_line(frame, 4.0, 2)
    frame.support(nodes[0], pinned_restraints())
    frame.support(nodes[-1], roller_restraints(), inclined(30.0))


def _beam_on_spring(frame: Frame) -> None:
    nodes = beam_line(frame, 4.0, 2)
    frame.support(nodes[0], pinned_restraints())
    spring = DofRestraint.spring(SupportDof.UZ, Quantity.of(1, "kN/m"))
    frame.support(nodes[-1], (FREE, FREE, spring, FREE, FREE, FREE))


def _cantilever_frame(frame: Frame) -> None:
    a, b, c = frame.node(0, 0), frame.node(0, 3), frame.node(4, 3)
    frame.member(a, b)
    frame.member(b, c)
    frame.support(a, fixed_restraints())
    frame.nodal(c, fz=-1000.0)


def _stiff_spring(stiffness: float) -> Callable[[Frame], None]:
    def build(frame: Frame) -> None:
        a, b = frame.node(0, 0), frame.node(2, 0)
        frame.member(a, b)
        frame.support(a, fixed_restraints())
        spring = DofRestraint.spring(SupportDof.UZ, Quantity.of(stiffness, "N/m"))
        frame.support(b, (FREE, FREE, spring, FREE, FREE, FREE))
        frame.nodal(b, fz=-1.0)

    return build


def _rotated(
    length: float, segments: int, angle: float, *, cantilever: bool
) -> Callable[[Frame], None]:
    """Barras esbeltas giradas y subdivididas: antes de R2 daban un FAILED falso por equilibrio."""

    def build(frame: Frame) -> None:
        nodes = beam_line(frame, length, segments, angle)
        if cantilever:
            frame.support(nodes[0], fixed_restraints())
        else:
            frame.support(nodes[0], pinned_restraints())
            frame.support(nodes[-1], pinned_restraints())

    return build


LEGITIMATE: dict[str, Callable[[Frame], None]] = {
    "voladizo girado 53°, L/h = 5000, 50 barras": _rotated(2000.0, 50, 53.0, cantilever=True),
    "voladizo girado 37°, L = 400 m, 100 barras": _rotated(400.0, 99, 37.0, cantilever=True),
    "biarticulada girada 37°, L = 400 m, 100 barras": _rotated(400.0, 100, 37.0, cantilever=False),
    "resorte casi rígido 1e21 N/m en la punta": _stiff_spring(1e21),
    "resorte casi rígido 1e30 N/m en la punta": _stiff_spring(1e30),
    "L/h = 200 (1 barra)": _slender(80.0, 1),
    "L/h = 200 (10 barras)": _slender(80.0, 10),
    "L/h = 1000 (1 barra)": _slender(400.0, 1),
    "L/h = 1000 (20 barras)": _slender(400.0, 20),
    "L/h = 1000 girada 37° (20 barras)": _slender(400.0, 20, 37.0),
    "rodillo inclinado 30°": _inclined_roller,
    "viga sobre resorte": _beam_on_spring,
    "pórtico en L empotrado": _cantilever_frame,
}


@pytest.mark.parametrize("name", list(LEGITIMATE))
def test_legitimate_structures_pass_with_margin(name: str) -> None:
    frame = Frame()
    LEGITIMATE[name](frame)
    result = report(frame)
    assert not result.singular, (name, result)
    assert result.min_pivot is not None
    assert result.min_pivot >= LEGIT_MIN, (name, result.min_pivot)


@pytest.mark.solver
@pytest.mark.parametrize("name", list(LEGITIMATE))
def test_legitimate_structures_succeed_end_to_end(name: str) -> None:
    """R2: ninguna estructura legítima da un FAILED falso (equilibrio incluido)."""
    frame = Frame()
    LEGITIMATE[name](frame)
    run = analyze_planar_xz(frame.model, frame.config())
    assert run.status is RunStatus.SUCCEEDED, (name, run.diagnostics)
