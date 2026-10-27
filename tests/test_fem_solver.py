"""Gate G1-G7 de extremo a extremo con OpenSeesPy real (plan T-007).

Cada caso parte de un modelo de dominio, atraviesa traductor, worker aislado y runner, y se
compara con una fórmula a mano en la base BRUTA del solver (x₁ = X, x₂ = Z, θ = −Ry).
Tolerancia relativa 1e-9 (solución directa en doble precisión); 1e-6 con penalización (G7).
Verifica el mapeo de entrada y el pipeline; no normaliza ni valida esfuerzos (T-008/T-009).
"""

import math

import pytest

from fem_support import AREA, E_PA, IY, Frame, inclined
from veritas.core.geometry.entities import Member, Node
from veritas.core.loads.loads import LoadBasis
from veritas.core.supports.support import (
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
from veritas.fem.adapters.singularity import reduced_coordinates, reduced_stiffness
from veritas.fem.results.run import AnalysisRun, RawNodeResult

pytestmark = pytest.mark.solver

REL = 1e-9
L = 2.0
P = 1000.0
Q = 1000.0
EI = E_PA * IY
EA = E_PA * AREA


def solve(frame: Frame) -> AnalysisRun:
    run = analyze_planar_xz(frame.model, frame.config())
    assert run.status is RunStatus.SUCCEEDED, run.diagnostics
    assert run.raw is not None
    return run


def node_result(run: AnalysisRun, node_id: object) -> RawNodeResult:
    assert run.raw is not None
    return next(n for n in run.raw.nodes if n.node_id == node_id)


def cantilever(
    end: tuple[float, float] = (L, 0.0), start: tuple[float, float] = (0.0, 0.0)
) -> tuple[Frame, Node, Node, Member]:
    frame = Frame()
    a, b = frame.node(*start), frame.node(*end)
    member = frame.member(a, b)
    frame.support(a, fixed_restraints())
    return frame, a, b, member


def test_g0_versions_observed() -> None:
    frame, _, b, _ = cantilever()
    frame.nodal(b, fz=-P)
    run = solve(frame)
    assert run.versions["openseespy"] == "3.8.0.0"
    assert run.versions["python"] is not None
    assert run.versions["python"].startswith("3.12")


def test_g1_bending_and_axial() -> None:
    frame, _, b, _ = cantilever()
    frame.nodal(b, fz=-P)
    tip = node_result(solve(frame), b.id)
    assert tip.displacement[1] == pytest.approx(-P * L**3 / (3 * EI), rel=REL)
    assert tip.displacement[2] == pytest.approx(-P * L**2 / (2 * EI), rel=REL)
    frame, _, b, _ = cantilever()
    frame.nodal(b, fx=P)
    assert node_result(solve(frame), b.id).displacement[0] == pytest.approx(P * L / EA, rel=REL)


@pytest.mark.parametrize(
    ("start", "end", "q"),
    [
        ((0.0, 0.0), (L, 0.0), (0.0, -Q)),  # horizontal, transversal
        ((0.0, 0.0), (L, 0.0), (Q, 0.0)),  # horizontal, axial (Wx ≠ 0)
        ((0.0, 0.0), (0.0, L), (Q, 0.0)),  # vertical
        ((L, 0.0), (0.0, 0.0), (0.0, -Q)),  # I → J hacia −X
        ((0.0, 0.0), (3.0, 4.0), (0.0, -Q)),  # inclinada 3-4-5
        ((0.0, 0.0), (3.0, 4.0), (Q, 0.0)),
    ],
)
def test_g2_uniform_member_load(
    start: tuple[float, float], end: tuple[float, float], q: tuple[float, float]
) -> None:
    frame, _, b, member = cantilever(end, start)
    frame.model.add_member_uniform_load(
        frame.case_id, member.id, qx=Quantity.of(q[0], "N/m"), qz=Quantity.of(q[1], "N/m")
    )
    length = math.hypot(end[0] - start[0], end[1] - start[1])
    ex = ((end[0] - start[0]) / length, (end[1] - start[1]) / length)
    ey = (-ex[1], ex[0])
    q_axial = q[0] * ex[0] + q[1] * ex[1]
    q_trans = q[0] * ey[0] + q[1] * ey[1]
    d_axial = q_axial * length**2 / (2 * EA)
    d_trans = q_trans * length**4 / (8 * EI)
    expected = (ex[0] * d_axial + ey[0] * d_trans, ex[1] * d_axial + ey[1] * d_trans)
    tip = node_result(solve(frame), b.id).displacement
    for k in (0, 1):
        assert tip[k] == pytest.approx(expected[k], rel=REL, abs=1e-18)


def test_g2_local_load_and_self_weight() -> None:
    frame, _, b, member = cantilever()
    frame.model.add_member_uniform_load(
        frame.case_id, member.id, basis=LoadBasis.LOCAL, qz=Quantity.of(-Q, "N/m")
    )
    frame.model.set_self_weight(frame.case_id, gravity=Quantity.of(10, "m/s²"))
    q = Q + 7850 * AREA * 10  # ez = +Z en una barra horizontal con alfa = 0
    tip = node_result(solve(frame), b.id).displacement
    assert tip[1] == pytest.approx(-q * L**4 / (8 * EI), rel=REL)


def test_g3_nodal_moment_maps_to_minus_my() -> None:
    # u = ω × r = (0, 0, −Ry·x): con My = +M en la punta, Uz = −ML²/(2EI) y θ = −ML/EI.
    frame, _, b, _ = cantilever()
    frame.nodal(b, my=P)
    tip = node_result(solve(frame), b.id).displacement
    assert tip[1] == pytest.approx(-P * L**2 / (2 * EI), rel=REL)
    assert tip[2] == pytest.approx(-P * L / EI, rel=REL)


def test_g4_reactions_include_load_on_supported_node_and_member_load() -> None:
    frame, a, b, member = cantilever()
    frame.nodal(b, fz=-P)
    frame.nodal(a, fx=500.0, fz=-300.0)
    frame.model.add_member_uniform_load(frame.case_id, member.id, qz=Quantity.of(-Q, "N/m"))
    base = node_result(solve(frame), a.id)
    assert base.reaction[0] == pytest.approx(-500.0, rel=REL)
    assert base.reaction[1] == pytest.approx(P + 300.0 + Q * L, rel=REL)
    # Momento bruto positivo antihorario en (x₁, x₂).
    assert base.reaction[2] == pytest.approx(P * L + Q * L**2 / 2, rel=REL)


@pytest.mark.parametrize("flip", [False, True])
def test_g6_inclined_springs(flip: bool) -> None:
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    k1, k2, k3 = 1e6, 2e6, 3e5
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(L, 0)
    frame.member(a, b)
    basis = inclined(30.0)
    if flip:  # ey′ = −Y: z′ en sentido opuesto.
        basis = SupportBasis.inclined((c, 0.0, s), (s, 0.0, -c))
    springs = (
        DofRestraint.spring(SupportDof.UX, Quantity.of(k1, "N/m")),
        FREE,
        DofRestraint.spring(SupportDof.UZ, Quantity.of(k2, "N/m")),
        FREE,
        DofRestraint.spring(SupportDof.RY, Quantity.of(k3, "N*m/rad")),
        FREE,
    )
    frame.support(a, springs, basis)
    zp = (s, -c) if flip else (-s, c)
    frame.nodal(a, fx=P * c + 2 * P * zp[0], fz=P * s + 2 * P * zp[1], my=50.0)
    run = solve(frame)
    u = node_result(run, a.id).displacement
    assert u[0] * c + u[1] * s == pytest.approx(P / k1, rel=REL)
    assert u[0] * zp[0] + u[1] * zp[1] == pytest.approx(2 * P / k2, rel=REL)
    assert u[2] == pytest.approx(-50.0 / k3, rel=REL)
    assert run.raw is not None
    assert len(run.raw.springs) == 3
    assert node_result(run, a.id).reaction == (None, None, None)


def test_g7_inclined_rigid_roller_by_penalty() -> None:
    c, s, t = math.cos(math.radians(30)), math.sin(math.radians(30)), math.tan(math.radians(30))
    length = 4.0
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(length, 0)
    frame.member(a, b)
    frame.support(a, pinned_restraints())
    frame.support(b, roller_restraints(), inclined(30.0))
    frame.nodal(b, fz=-P)
    run = solve(frame)
    assert run.raw is not None
    u = node_result(run, b.id).displacement
    ux = -P * t * length / EA
    assert u[0] == pytest.approx(ux, rel=1e-6)
    assert u[1] == pytest.approx(ux * t, rel=1e-6)
    (spring,) = run.raw.springs
    assert spring.penalty
    # Reacción normal al plano de rodadura: R = P/cos30 a lo largo de n = (−sin30, cos30).
    assert spring.reaction[0] == pytest.approx(-s * P / c, rel=1e-6)
    assert spring.reaction[1] == pytest.approx(P, rel=1e-6)
    assert "fem.hypothesis_penalty" in run.hypotheses


def test_isolated_runs_leave_no_state() -> None:
    frame, _, b, _ = cantilever()
    frame.nodal(b, fz=-P)
    first = node_result(solve(frame), b.id).displacement
    second = node_result(solve(frame), b.id).displacement
    assert first == second


def test_two_nodal_loads_on_same_node_add_up() -> None:
    frame, _, b, _ = cantilever()
    frame.nodal(b, fz=-0.6 * P)
    frame.nodal(b, fz=-0.4 * P)
    tip = node_result(solve(frame), b.id)
    assert tip.displacement[1] == pytest.approx(-P * L**3 / (3 * EI), rel=REL)


def _exact_reduced_solution(frame: Frame) -> dict[int, tuple[float, float]]:
    """Referencia independiente de OpenSees y de la penalización: K_red·u = Tᵀ·P resuelto en
    Python puro con la restricción oblicua exacta (solo cargas nodales). Devuelve (u₁, u₂)
    por tag de nudo."""
    request = translate_planar_xz(frame.model, frame.config()).request
    matrix, _ = reduced_stiffness(request)
    coordinates = reduced_coordinates(request)
    index = {node.tag: i for i, node in enumerate(request.nodes)}
    loads = [0.0] * (3 * len(request.nodes))
    for load in request.nodal_loads:
        base = 3 * index[load.node]
        loads[base], loads[base + 1], loads[base + 2] = load.p1, load.p2, load.m
    size = len(coordinates)
    aug = [
        [*matrix[a], sum(t * loads[i] for i, t in col.items())]
        for a, (_, col) in enumerate(coordinates)
    ]
    for k in range(size):  # Gauss con pivotaje parcial
        p = max(range(k, size), key=lambda i: abs(aug[i][k]))
        aug[k], aug[p] = aug[p], aug[k]
        for i in range(k + 1, size):
            factor = aug[i][k] / aug[k][k]
            aug[i] = [a - factor * b for a, b in zip(aug[i], aug[k], strict=True)]
    solution = [0.0] * size
    for k in reversed(range(size)):
        known = sum(aug[k][j] * solution[j] for j in range(k + 1, size))
        solution[k] = (aug[k][size] - known) / aug[k][k]
    full = [0.0] * (3 * len(request.nodes))
    for value, (_, col) in zip(solution, coordinates, strict=True):
        for i, t in col.items():
            full[i] += t * value
    return {tag: (full[3 * i], full[3 * i + 1]) for tag, i in index.items()}


@pytest.mark.parametrize("stiffness", [1e15, 1e18, 1e21])
def test_r1_penalty_is_local_despite_very_stiff_spring_elsewhere(stiffness: float) -> None:
    # Antes de R1, k = 1e18 y 1e21 daban SUCCEEDED con u₁ erróneo (incluso de signo).
    frame = Frame()
    a, b, c = frame.node(0, 0), frame.node(4, 0), frame.node(6, 0)
    frame.member(a, b)
    frame.member(b, c)
    frame.support(a, pinned_restraints())
    frame.support(b, roller_restraints(), inclined(30.0))
    spring = DofRestraint.spring(SupportDof.UZ, Quantity.of(stiffness, "N/m"))
    frame.support(c, (FREE, FREE, spring, FREE, FREE, FREE))
    frame.nodal(b, fz=-P)
    run = solve(frame)
    request = translate_planar_xz(frame.model, frame.config()).request
    tag_b = next(n.tag for n in request.nodes if n.entity_id == str(b.id))
    expected = _exact_reduced_solution(frame)[tag_b]
    got = node_result(run, b.id).displacement
    assert got[0] == pytest.approx(expected[0], rel=1e-6)
    assert got[1] == pytest.approx(expected[1], rel=1e-6)


def test_r3_large_coordinates_do_not_break_equilibrium_check() -> None:
    offset = 1e6  # m (p. ej. coordenadas georreferenciadas)
    frame = Frame()
    a, b = frame.node(offset, 0), frame.node(offset + L, 0)
    member = frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.nodal(b, fz=-P, my=P)
    frame.model.add_member_uniform_load(frame.case_id, member.id, qz=Quantity.of(-Q, "N/m"))
    tip = node_result(solve(frame), b.id).displacement
    expected = -P * L**3 / (3 * EI) - P * L**2 / (2 * EI) - Q * L**4 / (8 * EI)
    assert tip[1] == pytest.approx(expected, rel=REL)
