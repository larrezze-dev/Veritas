"""Normalización espacial ``normalize_spatial`` con OpenSeesPy real (plan T-018, pieza 5b).

Gates G-N1…G-N7 de T-008 en 3D y voladizos de T-FEM3D-01 con Uy, Rx y Rz no nulos. Los
valores esperados son fórmulas a mano con ADR-001 §2; la Q de la barra se toma del dominio
(aceptado), no del código que se prueba. Tolerancia relativa 1e-9; 1e-6 con penalización.
"""

import dataclasses
import math
from collections.abc import Sequence
from typing import Any
from uuid import UUID

import pytest

import test_fem3d_singularity as cases
from fem_support import AREA, E_PA, IY, IZ, Frame
from veritas.core.diagnostics import DiagnosticCode, InputValueError
from veritas.core.geometry.entities import Node
from veritas.core.loads.loads import LoadBasis
from veritas.core.messages_es import render_message
from veritas.core.supports.support import (
    SupportBasis,
    SupportDof,
    build_restraints,
    fixed_restraints,
)
from veritas.core.units import Quantity
from veritas.fem import ComponentStatus, MemberEndForces, ReactionSource, SupportReaction
from veritas.fem.results.normalized import ResultsNormalizationError
from veritas.fem.spatial import (
    SpatialResults,
    analyze_spatial,
    normalize_spatial,
    provenance_constants,
)
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.results import (
    HYPOTHESIS_SPATIAL_NORMALIZED,
    PROVENANCE_KEYS,
    SPATIAL_INERTIA_AXIS,
    SpatialAnalysisRun,
)
from veritas.fem.spatial.translator import translate_spatial

pytestmark = pytest.mark.solver

REL = 1e-9
REL_PENALTY = 1e-6
L = 2.0
P = 1000.0
Q = 1000.0
CALC = ComponentStatus.CALCULATED
NA = ComponentStatus.NOT_APPLICABLE
UX, UY, UZ = SupportDof.UX, SupportDof.UY, SupportDof.UZ
RX, RY, RZ = SupportDof.RX, SupportDof.RY, SupportDof.RZ


def run_of(frame: Frame, **config: float) -> SpatialAnalysisRun:
    run = analyze_spatial(
        frame.model,
        SpatialAnalysisConfiguration(frame.case_id, **config),  # type: ignore[arg-type]
    )
    assert run.raw is not None, [d.message_key for d in run.diagnostics]
    return run


def results(frame: Frame) -> SpatialResults:
    return normalize_spatial(run_of(frame), frame.model)


def check(
    values: Sequence[float | None],
    expected: Sequence[float],
    scale: float = P,
    rel: float = REL,
) -> None:
    """Componentes desde la primera; las de índice ≥ 3 (momentos) con escala ``scale``·L."""
    for index, (value, wanted) in enumerate(zip(values, expected, strict=True)):
        assert value is not None, index
        limit = rel * scale * (L if index >= 3 else 1.0)
        assert abs(value - wanted) <= limit, (index, value, wanted)


def member(res: SpatialResults, index: int = 0) -> MemberEndForces:
    return res.members[index]


def reaction(res: SpatialResults, node: Node) -> SupportReaction:
    return next(r for r in res.reactions if r.node_id == node.id)


def displacement(res: SpatialResults, node: Node) -> tuple[float | None, ...]:
    return next(n for n in res.nodes if n.node_id == node.id).displacement.values()


def cantilever(end: tuple[float, float, float], alpha: float | None = None) -> tuple[Frame, Node]:
    """Empotrada en el origen; ``end`` = (x, y, z) del extremo J."""
    frame = Frame()
    a, b = frame.node(0.0, 0.0), frame.node(end[0], end[2], y=end[1])
    frame.member(a, b, alpha=alpha)
    frame.support(a, fixed_restraints())
    return frame, b


def to_local(q: Sequence[Sequence[float]], values: Sequence[float]) -> list[float]:
    """Q·v por bloques de tres."""
    result: list[float] = []
    for block in range(0, len(values), 3):
        v = values[block : block + 3]
        result += [sum(q[r][c] * v[c] for c in range(3)) for r in range(3)]
    return result


def to_global(q: Sequence[Sequence[float]], local: Sequence[float]) -> list[float]:
    """Qᵀ·v por bloques de tres."""
    result: list[float] = []
    for block in range(0, len(local), 3):
        v = local[block : block + 3]
        result += [sum(q[r][c] * v[r] for r in range(3)) for c in range(3)]
    return result


# --- G-N1…G-N7 de T-008 en 3D ----------------------------------------------------------------


def test_gn1_tension() -> None:
    frame, b = cantilever((L, 0.0, 0.0))
    frame.nodal(b, fx=P)
    res = results(frame)
    check(member(res).p_i.values(), (-P, 0, 0, 0, 0, 0))
    check(member(res).p_j.values(), (P, 0, 0, 0, 0, 0))
    check(member(res).section_start.values(), (P, 0, 0, 0, 0, 0))
    check(res.reactions[0].global_.values(), (-P, 0, 0, 0, 0, 0))


def test_gn2_cantilever_tip_load_all_components_calculated() -> None:
    frame, b = cantilever((L, 0.0, 0.0))
    frame.nodal(b, fz=-P)
    res = results(frame)
    m = member(res)
    check(res.reactions[0].global_.values(), (0, 0, P, 0, -P * L, 0))
    check(m.p_i.values(), (0, 0, P, 0, -P * L, 0))
    check(m.p_j.values(), (0, 0, -P, 0, 0, 0))
    check(m.section_start.values(), (0, 0, -P, 0, P * L, 0))
    check(m.section_end.values(), (0, 0, -P, 0, 0, 0))
    assert m.inertia_axis == SPATIAL_INERTIA_AXIS
    for vector in (
        m.p_i,
        m.p_j,
        m.section_start,
        m.section_end,
        *(n.displacement for n in res.nodes),
    ):
        assert all(c.status is CALC for c in vector.components)


def test_gn3a_cantilever_uniform_load_includes_fixed_end_forces() -> None:
    frame, _b = cantilever((L, 0.0, 0.0))
    (member_id,) = frame.model.members
    frame.model.add_member_uniform_load(
        frame.case_id, member_id, basis=LoadBasis.GLOBAL, qz=Quantity.of(-Q, "N/m")
    )
    m = member(results(frame))
    check(m.p_i.values(), (0, 0, Q * L, 0, -Q * L * L / 2, 0))
    check(m.p_j.values(), (0, 0, 0, 0, 0, 0))


def test_gn3b_fixed_fixed_uniform_load_in_y() -> None:
    """Carga según −Y: flexión alrededor de z local (Iz), que el plano XZ no ve."""
    frame = Frame()
    a, c, b = frame.node(0, 0), frame.node(L / 2, 0), frame.node(L, 0)
    first, second = frame.member(a, c), frame.member(c, b)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    for item in (first, second):
        frame.model.add_member_uniform_load(
            frame.case_id, item.id, basis=LoadBasis.GLOBAL, qy=Quantity.of(-Q, "N/m")
        )
    res = results(frame)
    by_id = {m.member_id: m for m in res.members}
    end_moment = Q * L**2 / 12
    # Esperados en global (acción del nudo sobre la barra) y proyectados con la Q del dominio.
    first_q, second_q = frame.model.axes(first.id).matrix(), frame.model.axes(second.id).matrix()
    check(by_id[first.id].p_i.values(), to_local(first_q, (0, Q * L / 2, 0, 0, 0, end_moment)))
    check(by_id[second.id].p_j.values(), to_local(second_q, (0, Q * L / 2, 0, 0, 0, -end_moment)))
    # Centro del vano: qL²/24 con signo opuesto al de los extremos.
    check(by_id[first.id].section_end.values(), to_local(first_q, (0, 0, 0, 0, 0, Q * L**2 / 24)))


def test_gn3c_inclined_member_axial_fixed_end_force() -> None:
    angle = math.radians(30)
    frame, _b = cantilever((L * math.cos(angle), 0.0, L * math.sin(angle)))
    (member_id,) = frame.model.members
    frame.model.add_member_uniform_load(
        frame.case_id, member_id, basis=LoadBasis.GLOBAL, qz=Quantity.of(-Q, "N/m")
    )
    m = member(results(frame))
    s, c = math.sin(angle), math.cos(angle)
    check(m.p_i.values(), (Q * L * s, 0, Q * L * c, 0, -Q * L * L / 2 * c, 0))
    check(m.p_j.values(), (0, 0, 0, 0, 0, 0))


def test_gn4_load_on_supported_node() -> None:
    frame, b = cantilever((L, 0.0, 0.0))
    a = next(n for n in frame.model.nodes.values() if n.id != b.id)
    frame.nodal(a, fz=-300.0, mx=50.0)
    frame.nodal(b, fz=-P)
    check(results(frame).reactions[0].global_.values(), (0, 0, P + 300.0, -50.0, -P * L, 0))


ORIENTATIONS: dict[str, tuple[tuple[float, float, float], float | None]] = {
    "gn5a_minus_x": ((-L, 0.0, 0.0), None),
    "gn5b_vertical_up": ((0.0, 0.0, L), None),
    "gn5c_vertical_down": ((0.0, 0.0, -L), None),
    "gn5d_alpha_pi_2": ((L, 0.0, 0.0), math.pi / 2),
    "gn5e_alpha_minus_pi_2": ((L, 0.0, 0.0), -math.pi / 2),
    "gn5f_alpha_pi": ((L, 0.0, 0.0), math.pi),
    "plus_y": ((0.0, L, 0.0), None),
}


@pytest.mark.parametrize("name", list(ORIENTATIONS))
def test_gn5_orientations(name: str) -> None:
    """Carga global F en J: R = (−F, −r_J × F) y pI = Q·R por bloques (ADR-001 §2)."""
    end, alpha = ORIENTATIONS[name]
    frame, b = cantilever(end, alpha=alpha)
    force = (300.0, -700.0, -P)
    frame.nodal(b, fx=force[0], fy=force[1], fz=force[2])
    res = results(frame)
    r, f = end, force
    moment = (r[1] * f[2] - r[2] * f[1], r[2] * f[0] - r[0] * f[2], r[0] * f[1] - r[1] * f[0])
    expected = (-f[0], -f[1], -f[2], -moment[0], -moment[1], -moment[2])
    check(res.reactions[0].global_.values(), expected)
    q = frame.model.axes(member(res).member_id).matrix()
    check(member(res).p_i.values(), to_local(q, expected))
    axes = frame.model.axes(member(res).member_id)
    assert member(res).axes == (axes.ex, axes.ey, axes.ez)


def test_gn6_vertical_spring() -> None:
    k = 1e6
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(L, 0)
    frame.member(a, b)
    frame.support(a, build_restraints((UX, UY, UZ, RX)))
    frame.support(b, build_restraints((UY,), {UZ: Quantity.of(k, "N/m")}))
    frame.nodal(b, fz=-P)
    res = results(frame)
    spring = reaction(res, b)
    check(spring.global_.values()[1:3], (0.0, P))
    assert [c.status for c in spring.global_.components] == [NA, CALC, CALC, NA, NA, NA]
    assert spring.sources == ((UY, ReactionSource.FIX), (UZ, ReactionSource.SPRING))
    uz = displacement(res, b)[2]
    assert uz == pytest.approx(-P / k, rel=REL)


def _chain_roller() -> tuple[Frame, Node, Node]:
    """G-N7 sin penalización: A articulada (con Rx), B con Uy y Uz′ fijos; z′ a 30° en XZ.

    y′ = Y (``fix``) y z′ = (−sin30, 0, cos30) oblicua (cadena exacta).
    """
    s, c = math.sin(math.radians(30)), math.cos(math.radians(30))
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(L, 0)
    beam = frame.member(a, b)
    frame.support(a, build_restraints((UX, UY, UZ, RX)))
    frame.support(b, build_restraints((UY, UZ)), SupportBasis.inclined((c, 0.0, s), (-s, 0.0, c)))
    frame.model.add_member_uniform_load(
        frame.case_id, beam.id, basis=LoadBasis.GLOBAL, qz=Quantity.of(-Q, "N/m")
    )
    return frame, a, b


def test_gn7_inclined_roller_by_chain_is_exact() -> None:
    frame, a, b = _chain_roller()
    res = results(frame)
    s, c = math.sin(math.radians(30)), math.cos(math.radians(30))
    normal = Q * L / (2 * c)
    roller = reaction(res, b)
    assert roller.sources == ((UY, ReactionSource.FIX), (UZ, ReactionSource.FIX))
    assert roller.support_basis is not None
    assert roller.axes is not None
    assert [x.status for x in roller.support_basis.components] == [NA, CALC, CALC, NA, NA, NA]
    check(roller.support_basis.values()[1:3], (0.0, normal), Q * L)
    # Global: traslaciones calculadas (Q_apoyoᵀ mezcla la terna); giros no aplicables.
    assert [x.status for x in roller.global_.components] == [CALC, CALC, CALC, NA, NA, NA]
    check(roller.global_.values()[:3], (-normal * s, 0.0, Q * L / 2), Q * L)
    check(reaction(res, a).global_.values()[:4], (normal * s, 0.0, Q * L / 2, 0.0), Q * L)
    assert "fem3d.hypothesis_inclined_chain" in res.hypotheses


# --- T-FEM3D-01: voladizo espacial con Uy, Rx y Rz no nulos ------------------------------


@pytest.mark.parametrize("direction", [(0.0, 1.0, 0.0), cases.OBLIQUE], ids=["+Y", "oblicua"])
def test_tfem3d01_cantilever_local_loads(direction: tuple[float, float, float]) -> None:
    n, py, pz, t = 2000.0, -700.0, 1300.0, 450.0
    frame = cases.oblique_cantilever(L, direction)
    b = cases.tip(frame)
    member_id = next(iter(frame.model.members))
    q = frame.model.axes(member_id).matrix()
    f = to_global(q, (n, py, pz))
    mom = to_global(q, (t, 0.0, 0.0))
    frame.nodal(b, fx=f[0], fy=f[1], fz=f[2], mx=mom[0], my=mom[1], mz=mom[2])
    res = results(frame)
    gj = cases.G_PA * cases.J
    local_u = (
        n * L / (E_PA * AREA),
        py * L**3 / (3 * E_PA * IZ),
        pz * L**3 / (3 * E_PA * IY),
        t * L / gj,
        -pz * L**2 / (2 * E_PA * IY),
        py * L**2 / (2 * E_PA * IZ),
    )
    u = displacement(res, b)
    expected_u = to_global(q, local_u)
    scale = max(abs(v) for v in expected_u)
    for got, wanted in zip(u, expected_u, strict=True):
        assert got == pytest.approx(wanted, rel=REL, abs=REL * scale)
    assert min(abs(v) for v in local_u) > 0  # Uy, Rx y Rz locales no nulos.
    p_i = (-n, -py, -pz, -t, pz * L, -py * L)
    check(member(res).p_i.values(), p_i)
    check(member(res).p_j.values(), (n, py, pz, t, 0, 0))
    check(res.reactions[0].global_.values(), to_global(q, p_i))


# --- Reacciones en base del apoyo: cadena, PROP-1, penalización y resortes ------------------


def _free_components_vanish(rea: SupportReaction, restrained: set[int], rel: float) -> None:
    assert rea.axes is not None
    global_ = rea.global_.values()
    for triple in (0, 3):
        vector = [global_[triple + k] or 0.0 for k in range(3)]
        norm = math.sqrt(sum(v * v for v in vector))
        for row, axis in enumerate(rea.axes):
            value = sum(a * v for a, v in zip(axis.components(), vector, strict=True))
            if triple + row not in restrained:
                assert abs(value) <= rel * max(norm, 1.0), (triple + row, value)


PROP1_BASIS = SupportBasis.inclined((0.6, 0.0, 0.8), (-0.8, 0.0, 0.6))
INCLINED: list[tuple[str, object, Sequence[tuple[SupportDof, ReactionSource]], float]] = [
    ("cadena", lambda: cases._roller_frame(2.0), ((UX, ReactionSource.FIX),), REL),
    (
        "fix y cadena",
        lambda: cases._tip_constraint(cases.FIX_AND_CHAIN, (UX, UZ)),
        ((UX, ReactionSource.FIX), (UZ, ReactionSource.FIX)),
        REL,
    ),
    (
        "PROP-1",  # Fila libre y′ = Y: ``fix`` exacto en X y Z.
        lambda: cases._tip_constraint(PROP1_BASIS, (UX, UZ)),
        ((UX, ReactionSource.FIX), (UZ, ReactionSource.FIX)),
        REL,
    ),
    (
        "deslizador con dos cadenas",
        lambda: cases._tip_constraint(cases.SLIDER, (UX, UY)),
        ((UX, ReactionSource.FIX), (UY, ReactionSource.FIX)),
        REL,
    ),
    (
        "penalización",
        lambda: cases._penalty_tip(2.0, (UX, RX, RY)),
        (
            (UX, ReactionSource.FIX),
            (RX, ReactionSource.PENALTY),
            (RY, ReactionSource.PENALTY),
        ),
        REL_PENALTY,
    ),
    (
        "cadena y resortes orientados",
        cases._spring_tip,
        ((UX, ReactionSource.FIX), (UY, ReactionSource.SPRING), (RZ, ReactionSource.SPRING)),
        REL,
    ),
]


@pytest.mark.parametrize(
    ("name", "build", "sources", "rel"), INCLINED, ids=[i[0] for i in INCLINED]
)
def test_inclined_support_reaction_in_support_basis(
    name: str, build: object, sources: Sequence[tuple[SupportDof, ReactionSource]], rel: float
) -> None:
    frame: Frame = build()  # type: ignore[operator]
    res = results(frame)
    rea = reaction(res, cases.tip(frame))
    assert rea.sources == tuple(sources), name
    restrained = {dof.value for dof, _ in sources}
    assert rea.support_basis is not None
    for index, component in enumerate(rea.support_basis.components):
        assert (component.status is CALC) == (index in restrained), (name, index)
    _free_components_vanish(rea, restrained, rel)


def _support_rows(rea: SupportReaction) -> tuple[tuple[float, float, float], ...]:
    assert rea.axes is not None
    return tuple(axis.components() for axis in rea.axes)


def _dot(a: Sequence[float], b: Sequence[float | None]) -> float:
    return sum(x * (y or 0.0) for x, y in zip(a, b, strict=True))


def test_oriented_spring_reactions_by_hand() -> None:
    """Revisión 5b, m4: R′_UY = −k_y·(ey′·u) y M′_RZ = −k_rz·(ez′·θ) con los u publicados."""
    frame = cases._spring_tip()
    res = results(frame)
    tip = cases.tip(frame)
    rea = reaction(res, tip)
    rows = _support_rows(rea)
    u = displacement(res, tip)
    assert rea.support_basis is not None
    values = rea.support_basis.values()
    force = -2e6 * _dot(rows[1], u[:3])
    moment = -5e5 * _dot(rows[2], u[3:])
    assert values[1] == pytest.approx(force, rel=REL)
    assert values[5] == pytest.approx(moment, rel=REL)
    assert min(abs(force), abs(moment)) > 0


def test_penalty_reaction_by_hand() -> None:
    """Revisión 5b, m4: M′ = −k_pen·(b·θ), con k_pen de la petición."""
    frame = cases._penalty_tip(2.0, (UX, RX, RY))
    res = results(frame)
    request = translate_spatial(frame.model, SpatialAnalysisConfiguration(frame.case_id)).request
    stiffness = {spring.dof: spring.stiffness for spring in request.springs if spring.penalty}
    tip = cases.tip(frame)
    rea = reaction(res, tip)
    rows = _support_rows(rea)
    theta = displacement(res, tip)[3:]
    assert rea.support_basis is not None
    values = rea.support_basis.values()
    scale = max(abs(v or 0.0) for v in values[3:])
    for dof, row in ((4, 0), (5, 1)):  # RX′ y RY′ (dof 1-6 de la petición).
        expected = -stiffness[dof] * _dot(rows[row], theta)
        assert values[3 + row] == pytest.approx(expected, rel=REL_PENALTY, abs=REL_PENALTY * scale)


# --- Equilibrio: la batería normaliza; manipular el bruto da fallo ---------------------------


def _dev1_roller() -> Frame:
    """DEV-1: L/h = 5000 girada, con rodillo inclinado y carga de barra."""
    frame = cases._roller_frame(0.4 * 5000)
    member_id = next(iter(frame.model.members))
    frame.model.add_member_uniform_load(frame.case_id, member_id, qz=Quantity.of(-50.0, "N/m"))
    return frame


BATTERY = [
    *cases.CONTRAST,
    *cases.PENALTY,
    *cases.LEGITIMATE,
    ("DEV-1 rodillo L/h=5000", _dev1_roller),
    *(
        (f"Obs. 8 a={a:g} {name}", lambda a=a, fixed=fixed: cases._long_chain(a, fixed))
        for a in (20.0, 400.0, 2000.0)
        for name, fixed in (("rodillo", (UX,)), ("deslizador", (UX, UY)))
    ),
]


@pytest.mark.parametrize(("name", "build"), BATTERY, ids=[n for n, _ in BATTERY])
def test_battery_normalizes(name: str, build: object) -> None:
    frame: Frame = build()  # type: ignore[operator]
    res = results(frame)
    assert res.hypotheses[-1] == HYPOTHESIS_SPATIAL_NORMALIZED, name
    assert len(res.nodes) == len(frame.model.nodes)
    assert len(res.members) == len(frame.model.members)
    assert len(res.reactions) == len(frame.model.supports)


def _tampered(run: SpatialAnalysisRun, **raw: Any) -> SpatialAnalysisRun:
    assert run.raw is not None
    return dataclasses.replace(run, raw=dataclasses.replace(run.raw, **raw))


def _assert_equilibrium_failure(run: SpatialAnalysisRun, frame: Frame) -> None:
    with pytest.raises(ResultsNormalizationError) as error:
        normalize_spatial(run, frame.model)
    assert error.value.diagnostic.message_key == "fem.results_equilibrium"
    assert error.value.diagnostic.code is DiagnosticCode.ANALYSIS_FAILED
    render_message(error.value.diagnostic)


def _bump(values: Sequence[float], index: int, delta: float) -> Any:
    """``values`` con ``delta`` sumado en ``index`` (misma longitud: 6 o 12)."""
    return tuple(v + delta if i == index else v for i, v in enumerate(values))


@pytest.mark.parametrize("index", range(12))
def test_tampered_member_force_fails(index: int) -> None:
    frame = cases._spring_tip()
    run = run_of(frame)
    assert run.raw is not None
    first = run.raw.members[0]
    changed = dataclasses.replace(first, local_force=_bump(first.local_force, index, 1.0))
    _assert_equilibrium_failure(_tampered(run, members=(changed, *run.raw.members[1:])), frame)


def _shift(values: Sequence[float], force: Sequence[float], moment: Sequence[float]) -> Any:
    return tuple(v + d for v, d in zip(values, (*force, *moment), strict=True))


def _neutral(
    run: SpatialAnalysisRun, frame: Frame, delta: Sequence[float], *, on_ground: bool
) -> SpatialAnalysisRun:
    """+δ en la punta (tierra o reacción exacta) y −δ con su momento −r×δ en el
    empotramiento del origen: ΣF y ΣM no cambian, solo el reparto entre nudos (revisión 5b,
    m2). Solo el equilibrio por nudo puede detectarlo."""
    assert run.raw is not None
    tip = cases.tip(frame)
    r = tip.position.components()
    torque = (
        r[1] * delta[2] - r[2] * delta[1],
        r[2] * delta[0] - r[0] * delta[2],
        r[0] * delta[1] - r[1] * delta[0],
    )
    zero = (0.0, 0.0, 0.0)
    nodes = []
    for node in run.raw.nodes:
        assert node.reaction is not None  # Empotramiento en el origen y punta con fix/cadena.
        if node.node_id != tip.id:
            shifted = _shift(node.reaction, [-d for d in delta], [-t for t in torque])
        elif not on_ground:
            shifted = _shift(node.reaction, delta, zero)
        else:
            shifted = node.reaction
        nodes.append(dataclasses.replace(node, reaction=shifted))
    grounds = run.raw.grounds
    if on_ground:
        (ground,) = grounds
        grounds = (dataclasses.replace(ground, reaction=_shift(ground.reaction, delta, zero)),)
    return _tampered(run, nodes=tuple(nodes), grounds=grounds)


def _assert_node_failure(run: SpatialAnalysisRun, frame: Frame) -> None:
    with pytest.raises(ResultsNormalizationError) as error:
        normalize_spatial(run, frame.model)
    diagnostic = error.value.diagnostic
    assert diagnostic.message_key == "fem.results_equilibrium"
    assert dict(diagnostic.params)["where"] == "node"
    assert diagnostic.location is not None
    assert diagnostic.location.entity_id in set(frame.model.nodes)


def test_tampered_ground_reaction_neutral_globally_fails_at_node() -> None:
    """Revisión 5a, O1: la reacción de la tierra entra en el equilibrio de su nudo."""
    frame = cases._spring_tip()
    _assert_node_failure(_neutral(run_of(frame), frame, (0.0, 1.0, 0.0), on_ground=True), frame)


def test_tampered_chain_reaction_neutral_globally_fails_at_node() -> None:
    """Revisión 5a, O1: la reacción de la cadena (en su nudo) entra en su equilibrio."""
    frame = cases._roller_frame(2.0)
    norm = math.sqrt(sum(c * c for c in cases.NORMAL))
    delta = [c / norm for c in cases.NORMAL]  # δ = 1 N según la normal de la cadena.
    _assert_node_failure(_neutral(run_of(frame), frame, delta, on_ground=False), frame)


def test_nonfinite_raw_value_fails() -> None:
    frame = cases._spring_tip()
    run = run_of(frame)
    assert run.raw is not None
    node = run.raw.nodes[1]
    changed = dataclasses.replace(node, displacement=_bump(node.displacement, 0, math.nan))
    with pytest.raises(ResultsNormalizationError) as error:
        normalize_spatial(_tampered(run, nodes=(run.raw.nodes[0], changed)), frame.model)
    assert error.value.diagnostic.message_key == "fem.nonfinite_result"


# --- Correspondencia modelo ↔ corrida y argumentos -------------------------------------------


def _mismatch(run: SpatialAnalysisRun, frame: Frame, reason: str) -> None:
    with pytest.raises(InputValueError) as error:
        normalize_spatial(run, frame.model)
    assert error.value.diagnostic.code is DiagnosticCode.REVISION_CONFLICT
    assert error.value.diagnostic.message_key == "fem.results_model_mismatch"
    assert dict(error.value.diagnostic.params)["reason"] == reason


def test_failed_run_has_no_results() -> None:
    frame = cases._simply_supported()
    run = analyze_spatial(frame.model, SpatialAnalysisConfiguration(frame.case_id))
    assert run.raw is None
    with pytest.raises(InputValueError) as error:
        normalize_spatial(run, frame.model)
    assert error.value.diagnostic.message_key == "fem.results_unavailable"


def test_model_changed_after_run_is_rejected() -> None:
    frame = cases._spring_tip()
    run = run_of(frame)
    frame.nodal(cases.tip(frame), fz=-1.0)
    _mismatch(run, frame, "model_revision")


def test_other_model_with_same_revision_is_rejected() -> None:
    frame, other = cases._spring_tip(), cases._spring_tip()
    run = run_of(frame)
    assert other.model.model_revision == run.model_revision
    # Mismo contador, otros UUID: el caso de la corrida no existe en el otro modelo.
    _mismatch(run, other, "REFERENCE_NOT_FOUND")


def test_request_that_differs_from_the_translation_is_rejected() -> None:
    frame = cases._spring_tip()
    run = run_of(frame)
    assert run.request_json is not None
    tampered = run.request_json[:-1] + " " + run.request_json[-1]  # Otro texto.
    _mismatch(dataclasses.replace(run, request_json=tampered), frame, "request")


def test_configuration_change_that_alters_the_request_is_rejected() -> None:
    frame = cases._penalty_tip(2.0, (RX, RY))
    run = run_of(frame)
    configuration = {**run.configuration, "penalty_factor": 1e7}
    _mismatch(dataclasses.replace(run, configuration=configuration), frame, "request")


@pytest.mark.parametrize(
    "configuration",
    [{"unknown": 1.0}, {"tol_axis": -1.0}],
    ids=["clave desconocida", "valor inválido"],
)
def test_unreadable_configuration_is_a_mismatch(configuration: dict[str, float]) -> None:
    frame = cases._spring_tip()
    run = run_of(frame)
    changed = dataclasses.replace(run, configuration={**run.configuration, **configuration})
    _mismatch(changed, frame, "configuration")


def test_missing_request_is_a_mismatch() -> None:
    frame = cases._spring_tip()
    run = run_of(frame)
    _mismatch(dataclasses.replace(run, request_json=None), frame, "request")


def test_provenance_keys_match_the_run_snapshot() -> None:
    assert frozenset(provenance_constants()) == PROVENANCE_KEYS


END_PENALTY_KEYS = ("end_penalty_factor", "end_penalty_tolerance", "end_penalty_max_iterations")


def test_end_penalty_fields_are_in_the_snapshot_only() -> None:
    """ADR-004 §7: los campos nuevos van en ``snapshot()`` y no en ``provenance_constants``;
    la normalización reconstruye la configuración con ellos."""
    frame = cases._spring_tip()
    run = run_of(frame)
    assert {key: run.configuration[key] for key in END_PENALTY_KEYS} == {
        "end_penalty_factor": 1e1,
        "end_penalty_tolerance": 1e-10,
        "end_penalty_max_iterations": 20,
    }
    assert PROVENANCE_KEYS.isdisjoint(END_PENALTY_KEYS)
    normalize_spatial(run, frame.model)


@pytest.mark.parametrize(
    ("change", "kind"),
    [
        ({"end_penalty_tolerance": 1e-11}, "request"),  # Va en la petición v3.
        ({"end_penalty_max_iterations": 0}, "configuration"),
        ({"end_penalty_max_iterations": 2.5}, "configuration"),
    ],
)
def test_end_penalty_configuration_change_is_a_mismatch(
    change: dict[str, float], kind: str
) -> None:
    frame = cases._spring_tip()
    run = run_of(frame)
    _mismatch(dataclasses.replace(run, configuration={**run.configuration, **change}), frame, kind)


def test_argument_types() -> None:
    frame = cases._spring_tip()
    run = run_of(frame)
    with pytest.raises(TypeError):
        normalize_spatial(object(), frame.model)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        normalize_spatial(run, object())  # type: ignore[arg-type]


def test_results_carry_run_identity() -> None:
    frame = cases._spring_tip()
    run = run_of(frame)
    res = normalize_spatial(run, frame.model)
    assert (res.run_id, res.model_revision, res.case_id) == (
        run.run_id,
        run.model_revision,
        run.case_id,
    )
    assert res.hypotheses == (*run.hypotheses, HYPOTHESIS_SPATIAL_NORMALIZED)
    assert isinstance(res.case_id, UUID)
