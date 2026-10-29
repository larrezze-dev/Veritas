"""Referencias espaciales T-FEM3D-01, 02, 03 y 05 con OpenSeesPy real (plan T-018, D8).

Independiente de ``fem.benchmarks``: cada valor esperado es una fórmula de este archivo con
E y G literales y A, Iy, Iz y J de ``rectangular_properties`` (la función que alimenta el
modelo; J de Saint-Venant no tiene forma cerrada). La Q de la barra es la del dominio
(aceptada en T-003). Criterio: |calc − ref| ≤ 1e-9·escala, con escala la mayor |ref| del
mismo tipo en el caso (T-FEM3D-03: H para fuerzas y H·L para momentos, plan T-018).

El último test comprueba que las referencias de ``fem.benchmarks`` (las que mostrará la CLI)
coinciden con las de este archivo.
"""

import math
from collections.abc import Sequence

import pytest

import benchmark_cache
from fem_support import Frame
from veritas.core.geometry.entities import Member, Node
from veritas.core.loads.loads import LoadBasis
from veritas.core.messages_es import MESSAGES_ES, render_text
from veritas.core.sections.rectangular import rectangular_properties
from veritas.core.supports.support import fixed_restraints
from veritas.core.units import Quantity
from veritas.fem import benchmarks
from veritas.fem.spatial import SpatialResults, analyze_spatial, normalize_spatial
from veritas.fem.spatial.config import SpatialAnalysisConfiguration

pytestmark = pytest.mark.solver

TOL = 1e-9
E, G = 210e9, 81e9
_PROPS = rectangular_properties(0.2, 0.4)
A, IY, IZ, J = (
    float(p.value or 0.0) for p in (_PROPS.area, _PROPS.iy, _PROPS.iz, _PROPS.torsion_constant)
)
P = 10_000.0


def solve(frame: Frame) -> SpatialResults:
    run = analyze_spatial(frame.model, SpatialAnalysisConfiguration(frame.case.id))
    assert run.raw is not None, [d.message_key for d in run.diagnostics]
    return normalize_spatial(run, frame.model)


def q_of(frame: Frame, member: Member) -> tuple[tuple[float, float, float], ...]:
    return frame.model.axes(member.id).matrix()


def blocks(q: Sequence[Sequence[float]], v: Sequence[float], *, transpose: bool) -> list[float]:
    """Q·v (o Qᵀ·v) por bloques de tres."""
    out: list[float] = []
    for s in range(0, len(v), 3):
        b = v[s : s + 3]
        if transpose:
            out += [sum(q[r][k] * b[r] for r in range(3)) for k in range(3)]
        else:
            out += [sum(q[r][k] * b[k] for k in range(3)) for r in range(3)]
    return out


def present(values: Sequence[float | None]) -> list[float]:
    """Las seis componentes publicadas; una ausente falla (nunca se toma como 0)."""
    assert all(v is not None for v in values), values
    return [float(v) for v in values if v is not None]


def disp(res: SpatialResults, node: Node) -> list[float]:
    return present(next(n for n in res.nodes if n.node_id == node.id).displacement.values())


def reaction(res: SpatialResults, node: Node) -> list[float]:
    return present(next(r for r in res.reactions if r.node_id == node.id).global_.values())


def p_i(res: SpatialResults, member: Member) -> list[float]:
    return present(next(m for m in res.members if m.member_id == member.id).p_i.values())


def close(computed: Sequence[float], expected: Sequence[float], scale: float) -> None:
    assert scale > 0.0
    for index, (c, e) in enumerate(zip(computed, expected, strict=True)):
        assert abs(c - e) <= TOL * scale, (index, c, e)


def scale_of(*values: float) -> float:
    return max(abs(v) for v in values)


def close_fm(
    computed: Sequence[float], expected: Sequence[float], force: float = 0.0, moment: float = 0.0
) -> None:
    """Fuerzas (0-2) y momentos (3-5) con escala propia: la mayor |ref| del bloque o el mínimo."""
    close(computed[:3], expected[:3], max(force, scale_of(*expected[:3])))
    close(computed[3:], expected[3:], max(moment, scale_of(*expected[3:])))


# --- T-FEM3D-01 -----------------------------------------------------------------------------

LOAD_01 = (20_000.0, 5_000.0, -10_000.0, 3_000.0)  # N, Py, Pz y T, en base local


@pytest.mark.parametrize(
    ("tip", "alpha"),
    [((0.0, 3.5, 0.0), None), ((1.0, -1.5, 3.0), 0.37)],
    ids=["+Y", "oblicuo"],
)
def test_3d01_cantilever_local_tip_load(
    tip: tuple[float, float, float], alpha: float | None
) -> None:
    frame = Frame()
    o, j = frame.node(0.0, 0.0), frame.node(tip[0], tip[2], y=tip[1])
    bar = frame.member(o, j, alpha=alpha)
    frame.support(o, fixed_restraints())
    q, length = q_of(frame, bar), math.dist((0.0, 0.0, 0.0), tip)
    n, py, pz, t = LOAD_01
    f = blocks(q, (n, py, pz, t, 0.0, 0.0), transpose=True)
    frame.nodal(j, fx=f[0], fy=f[1], fz=f[2], mx=f[3], my=f[4], mz=f[5])
    res = solve(frame)
    local = blocks(q, disp(res, j), transpose=False)
    u = (n * length / (E * A), py * length**3 / (3 * E * IZ), pz * length**3 / (3 * E * IY))
    r = (t * length / (G * J), -pz * length**2 / (2 * E * IY), py * length**2 / (2 * E * IZ))
    close(local[:3], u, scale_of(*u))
    close(local[3:], r, scale_of(*r))
    expected_pi = (-n, -py, -pz, -t, pz * length, -py * length)
    close_fm(p_i(res, bar), expected_pi)
    close_fm(reaction(res, o), blocks(q, expected_pi, transpose=True))


# --- T-FEM3D-02 -----------------------------------------------------------------------------

L1, L2 = 3.0, 2.0


def bracket() -> tuple[Frame, Node, Node, Member, Member]:
    frame = Frame()
    o, a, c = frame.node(0.0, 0.0), frame.node(L1, 0.0), frame.node(L1, 0.0, y=L2)
    b1, b2 = frame.member(o, a), frame.member(a, c)
    frame.support(o, fixed_restraints())
    return frame, o, c, b1, b2


def test_3d02a_bent_bracket_vertical_load() -> None:
    frame, o, c, b1, b2 = bracket()
    assert q_of(frame, b2) == ((0.0, 1.0, 0.0), (-1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
    frame.nodal(c, fz=-P)
    res = solve(frame)
    delta = P * L1**3 / (3 * E * IY) + P * L2**3 / (3 * E * IY) + P * L1 * L2**2 / (G * J)
    close(disp(res, c)[:3], (0.0, 0.0, -delta), delta)
    close_fm(reaction(res, o), (0, 0, P, P * L2, -P * L1, 0))
    section = next(m for m in res.members if m.member_id == b1.id).section_start.values()
    close_fm(present(section), (0, 0, -P, -P * L2, P * L1, 0))
    close_fm(p_i(res, b2), (0, 0, P, 0, -P * L2, 0))


def test_3d02b_bent_bracket_horizontal_load() -> None:
    frame, o, c, _b1, _b2 = bracket()
    frame.nodal(c, fx=P)
    res = solve(frame)
    ux = P * L2**3 / (3 * E * IZ) + P * L1 / (E * A) + P * L1 * L2**2 / (E * IZ)
    close((disp(res, c)[0], disp(res, c)[2]), (ux, 0.0), ux)
    close_fm(reaction(res, o), (-P, 0, 0, 0, 0, P * L2))


# --- T-FEM3D-03 -----------------------------------------------------------------------------

SPAN, HEIGHT = 4.0, 3.0


def portal() -> tuple[Frame, dict[str, Node], dict[str, Member]]:
    frame = Frame()
    nodes = {
        "O1": frame.node(0.0, 0.0),
        "O2": frame.node(SPAN, 0.0),
        "A1": frame.node(0.0, HEIGHT),
        "A2": frame.node(SPAN, HEIGHT),
    }
    members = {
        "C1": frame.member(nodes["O1"], nodes["A1"]),
        "C2": frame.member(nodes["O2"], nodes["A2"]),
        "D": frame.member(nodes["A1"], nodes["A2"]),
    }
    frame.support(nodes["O1"], fixed_restraints())
    frame.support(nodes["O2"], fixed_restraints())
    return frame, nodes, members


def test_3d03a_portal_in_plane() -> None:
    frame, n, m = portal()
    frame.nodal(n["A1"], fx=P / 2)
    frame.nodal(n["A2"], fx=P / 2)
    res = solve(frame)
    c, b, a = E * IY / HEIGHT, E * IY / SPAN, E * A / HEIGHT
    det = c * a + 6 * a * b + 24 * c * b / SPAN**2
    sway = P * HEIGHT**2 * (4 * c * a + 6 * a * b + 96 * c * b / SPAN**2) / (24 * c * det)
    theta = P * HEIGHT * (a + 24 * b / SPAN**2) / (4 * det)
    v = 3 * P * HEIGHT * b / (SPAN * det)
    base = -6 * c * sway / HEIGHT + 2 * c * theta
    close(disp(res, n["A1"])[:3], (sway, 0, v), sway)
    close(disp(res, n["A2"])[:3], (sway, 0, -v), sway)
    close(disp(res, n["A1"])[3:], (0, theta, 0), theta)
    close(disp(res, n["A2"])[3:], (0, theta, 0), theta)
    close_fm(reaction(res, n["O1"]), (-P / 2, 0, -a * v, 0, base, 0), P, P * SPAN)
    close_fm(reaction(res, n["O2"]), (-P / 2, 0, a * v, 0, base, 0), P, P * SPAN)
    close_fm(p_i(res, m["C1"]), (-a * v, 0, P / 2, 0, base, 0), P, P * SPAN)
    beam_moment = -12 * b * v / SPAN + 6 * b * theta
    close_fm(p_i(res, m["D"]), (0, 0, -a * v, 0, beam_moment, 0), P, P * SPAN)


def test_3d03b_portal_out_of_plane() -> None:
    frame, n, m = portal()
    frame.nodal(n["A1"], fy=P)
    res = solve(frame)
    iw, ibz, tb, tc = E * IZ / HEIGHT, E * IZ / SPAN, G * J / SPAN, G * J / HEIGHT
    ds, ts = P * HEIGHT**3 / (6 * E * IZ), -P * HEIGHT**2 / (4 * E * IZ)
    k = 6 * iw * (iw + 2 * tb) / (HEIGHT**2 * (2 * iw + tb))
    k += 24 * ibz * tc / (SPAN**2 * (6 * ibz + tc))
    da = P / 2 / k
    ta = -3 * iw * da / (HEIGHT * (2 * iw + tb))
    za = -12 * ibz * da / (SPAN * (6 * ibz + tc))
    u1, u2 = disp(res, n["A1"]), disp(res, n["A2"])
    close((u1[0], u1[1], u1[2], u2[1]), (0, ds + da, 0, ds - da), ds + da)
    close((u1[3], u1[4], u1[5], u2[3], u2[5]), (ts + ta, 0, za, ts - ta, za), abs(ts) + abs(ta))
    s = 12 * iw / HEIGHT**2 * da + 6 * iw / HEIGHT * ta
    mx1 = 6 * iw / HEIGHT * (ds + da) + 2 * iw * (ts + ta)
    mx2 = 6 * iw / HEIGHT * (ds - da) + 2 * iw * (ts - ta)
    close_fm(reaction(res, n["O1"]), (0, -P / 2 - s, 0, mx1, 0, -tc * za), P, P * SPAN)
    close_fm(reaction(res, n["O2"]), (0, -P / 2 + s, 0, mx2, 0, -tc * za), P, P * SPAN)
    close(
        (p_i(res, m["D"])[3], p_i(res, m["C1"])[3], p_i(res, m["C2"])[3]),
        (2 * tb * ta, -tc * za, -tc * za),
        P * SPAN,
    )


# --- T-FEM3D-05 -----------------------------------------------------------------------------

TIP_05 = (-3.0, 1.0, 1.5)


def oblique() -> tuple[Frame, Node, Node, Member]:
    frame = Frame()  # caso PERMANENT: admite peso propio
    o, j = frame.node(0.0, 0.0), frame.node(TIP_05[0], TIP_05[2], y=TIP_05[1])
    bar = frame.member(o, j, alpha=-0.6)
    frame.support(o, fixed_restraints())
    return frame, o, j, bar


def check_oblique(frame: Frame, o: Node, j: Node, bar: Member, q_global: Sequence[float]) -> None:
    res = solve(frame)
    q, length = q_of(frame, bar), math.dist((0.0, 0.0, 0.0), TIP_05)
    wx, wy, wz = blocks(q, q_global, transpose=False)
    u = (wx * length**2 / (2 * E * A), wy * length**4 / (8 * E * IZ), wz * length**4 / (8 * E * IY))
    r = (0.0, -wz * length**3 / (6 * E * IY), wy * length**3 / (6 * E * IZ))
    local = blocks(q, disp(res, j), transpose=False)
    close(local[:3], u, scale_of(*u))
    close(local[3:], r, scale_of(*r))
    total = [w * length for w in q_global]
    mid = [x / 2 for x in TIP_05]
    moment = (
        mid[1] * total[2] - mid[2] * total[1],
        mid[2] * total[0] - mid[0] * total[2],
        mid[0] * total[1] - mid[1] * total[0],
    )
    expected = [-x for x in (*total, *moment)]
    close_fm(reaction(res, o), expected)
    close_fm(p_i(res, bar), blocks(q, expected, transpose=False))


def test_3d05a_oblique_self_weight() -> None:
    frame, o, j, bar = oblique()
    frame.model.set_self_weight(frame.case.id)
    rho_a_g = 7850 * A * 9.80665  # ρ·A·g a mano, distinto del camino del código
    check_oblique(frame, o, j, bar, (0.0, 0.0, -rho_a_g))


def test_3d05b_oblique_local_and_global_loads() -> None:
    frame, o, j, bar = oblique()
    local, glob = (4_000.0, -3_000.0, 2_000.0), (1_000.0, 2_000.0, -5_000.0)
    for basis, values in ((LoadBasis.LOCAL, local), (LoadBasis.GLOBAL, glob)):
        qx, qy, qz = (Quantity.of(v, "N/m") for v in values)
        frame.model.add_member_uniform_load(frame.case.id, bar.id, basis=basis, qx=qx, qy=qy, qz=qz)
    q = q_of(frame, bar)
    total = [a + b for a, b in zip(blocks(q, local, transpose=True), glob, strict=True)]
    check_oblique(frame, o, j, bar, total)


# --- Coherencia con fem.benchmarks ----------------------------------------------------------


def test_benchmark_properties_are_the_section_properties() -> None:
    assert (benchmarks.AREA_3D, benchmarks.IY_3D, benchmarks.IZ_3D, benchmarks.J_3D) == (
        A,
        IY,
        IZ,
        J,
    )
    assert pytest.approx(7.3178e-4, rel=1e-4) == J  # valor del plan (T-FEM3D-03)


def test_benchmark_spatial_references_match() -> None:
    """Muestras de las referencias que mostrará la CLI frente a las fórmulas de este archivo."""
    delta = P * L1**3 / (3 * E * IY) + P * L2**3 / (3 * E * IY) + P * L1 * L2**2 / (G * J)
    expected = {
        ("T-FEM3D-01 a", "θx J (local)"): LOAD_01[3] * 3.5 / (G * J),
        ("T-FEM3D-01 b", "uy J (local)"): LOAD_01[1] * 3.5**3 / (3 * E * IZ),
        ("T-FEM3D-02 a", "Uz C"): -delta,
        ("T-FEM3D-02 b", "R_MZ O"): P * L2,
        ("T-FEM3D-03 a", "R_X O1"): -P / 2,
        ("T-FEM3D-03 b", "Uy A1"): None,  # solo presencia; el valor lo fija el test 03b
        ("T-FEM3D-05 a", "R_Z O"): 7850 * A * 9.80665 * 3.5,
    }
    references = {
        (o.case_id, c.quantity): (o, c)
        for case in benchmarks.SPATIAL_CASES
        for o in benchmark_cache.run_spatial_reference(case)
        for c in o.checks
    }
    for key, value in expected.items():
        outcome, check = references[key]
        assert outcome.matches, key
        if value is not None:
            assert check.reference == pytest.approx(value, rel=1e-12), key


def test_unknown_spatial_case_is_rejected() -> None:
    with pytest.raises(ValueError, match="desconocido"):
        benchmarks.run_spatial_reference("T-FEM3D-99")


def test_spatial_benchmark_texts_render_from_catalog() -> None:
    texts = [*benchmarks.SPATIAL_HYPOTHESES]
    for specs in benchmarks.SPATIAL_SPECS.values():
        texts += [text for spec in specs for text in (spec.title, spec.description)]
    texts.append(benchmarks.Text.of("benchmark.note_cross_planar", tolerance=1e-6))
    for text in texts:
        assert text.key in MESSAGES_ES, text.key
        assert "{" not in render_text(text.key, dict(text.params)), text.key


def test_all_spatial_benchmark_cases_match() -> None:
    """Cada variante de ``SPATIAL_CASES`` coincide (lo que mostrará la CLI, pieza 7)."""
    outcomes = [
        o for case in benchmarks.SPATIAL_CASES for o in benchmark_cache.run_spatial_reference(case)
    ]
    assert len(outcomes) == 8
    for outcome in outcomes:
        failing = [c.quantity for c in outcome.checks if not c.matches]
        assert outcome.matches, (outcome.case_id, failing)
