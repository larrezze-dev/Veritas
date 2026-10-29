"""Puertas del worker 3D con OpenSeesPy real (plan T-018, hechos 1, 2, 6, 8-11).

Cada caso construye una petición del protocolo espacial a mano, la ejecuta con el runner
aislado y la compara con una fórmula cerrada: flexibilidad de voladizo en base local de
ADR-001 y, con apoyos, multiplicadores en forma cerrada. Criterio |e| ≤ 1e-9·escala.
"""

import math
from collections.abc import Sequence
from pathlib import Path

import pytest

from veritas.core.geometry.local_axes import compute_local_axes
from veritas.core.geometry.vector import Vec3
from veritas.fem.adapters import opensees_worker_3d
from veritas.fem.adapters.protocol import WorkerStatus
from veritas.fem.runners.subprocess_runner import run_worker
from veritas.fem.spatial.protocol import (
    Six,
    SpatialBeam,
    SpatialBeamLoad,
    SpatialChain,
    SpatialFix,
    SpatialNodalLoad,
    SpatialNode,
    SpatialRequest,
    SpatialSpring,
    SpatialWorkerResponse,
)
from veritas.fem.spatial.protocol import Vec3 as Triple

pytestmark = pytest.mark.solver

WORKER_3D = Path(opensees_worker_3d.__file__).resolve()
REL = 1e-9
E, G = 210e9, 81e9
B, H = 0.2, 0.4
A, IY, IZ = B * H, B * H**3 / 12, H * B**3 / 12
J = 7.3e-4  # Mismo valor en la petición y en la fórmula: solo se prueba el mapeo.
LOAD: Six = (3000.0, -2000.0, -5000.0, 400.0, -700.0, 900.0)
OBLIQUE = (1.2, -0.7, 0.9)

type Matrix = list[list[float]]


def _matmul(a: Matrix, b: Matrix) -> Matrix:
    return [
        [sum(a[i][k] * b[k][j] for k in range(len(b))) for j in range(len(b[0]))]
        for i in range(len(a))
    ]


def _matvec(a: Matrix, v: Sequence[float]) -> list[float]:
    return [sum(row[k] * v[k] for k in range(len(v))) for row in a]


def _transpose(a: Matrix) -> Matrix:
    return [list(col) for col in zip(*a, strict=True)]


def _solve(a: Matrix, b: Sequence[float]) -> list[float]:
    """Gauss con pivote parcial (sistemas de 6×6 o menores)."""
    n = len(b)
    m = [[*list(row), b[i]] for i, row in enumerate(a)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(m[r][c]))
        m[c], m[p] = m[p], m[c]
        for r in range(c + 1, n):
            f = m[r][c] / m[c][c]
            for k in range(c, n + 1):
                m[r][k] -= f * m[c][k]
    x = [0.0] * n
    for r in reversed(range(n)):
        x[r] = (m[r][n] - sum(m[r][k] * x[k] for k in range(r + 1, n))) / m[r][r]
    return x


def _unit(v: Sequence[float]) -> Triple:
    n = math.sqrt(sum(c * c for c in v))
    return v[0] / n, v[1] / n, v[2] / n


def _cross(a: Sequence[float], b: Sequence[float]) -> Triple:
    return a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]


class Cantilever:
    """Voladizo empotrado en el origen con el extremo libre en ``tip`` (nudo 2)."""

    def __init__(self, tip: Sequence[float], alpha: float = 0.0) -> None:
        axes = compute_local_axes(Vec3(0.0, 0.0, 0.0), Vec3(*tip), alpha=alpha)
        self.tip = tuple(float(c) for c in tip)
        self.length = axes.length
        self.q: Matrix = [list(row) for row in axes.matrix()]
        self.vecxz: Triple = axes.ez.components()
        length = self.length
        # Flexibilidad local del extremo libre (Euler–Bernoulli, ADR-001: θy = −dw/dx).
        f: Matrix = [[0.0] * 6 for _ in range(6)]
        f[0][0] = length / (E * A)
        f[1][1], f[1][5] = length**3 / (3 * E * IZ), length**2 / (2 * E * IZ)
        f[5][1], f[5][5] = f[1][5], length / (E * IZ)
        f[2][2], f[2][4] = length**3 / (3 * E * IY), -(length**2) / (2 * E * IY)
        f[4][2], f[4][4] = f[2][4], length / (E * IY)
        f[3][3] = length / (G * J)
        t = self.t6()
        self.flex = _matmul(_matmul(_transpose(t), f), t)  # global
        self.max_diag = max(E * A / length, 12 * E * IY / length**3, 4 * E * IY / length)

    def t6(self) -> Matrix:
        t: Matrix = [[0.0] * 6 for _ in range(6)]
        for b in range(2):
            for r in range(3):
                for c in range(3):
                    t[3 * b + r][3 * b + c] = self.q[r][c]
        return t

    def request(self, **extra: object) -> SpatialRequest:
        fields: dict[str, object] = {
            "model_revision": 1,
            "case_id": "c",
            "case_revision": 1,
            "alpha_sp": self.max_diag,
            "alpha_mp": self.max_diag,
            "nodes": (SpatialNode(1, "n1", 0.0, 0.0, 0.0), SpatialNode(2, "n2", *self.tip)),
            "beams": (SpatialBeam(1, "b1", 1, 2, A, E, G, J, IY, IZ, self.vecxz),),
            "fixes": (SpatialFix(1, (1, 1, 1, 1, 1, 1)),),
            "springs": (),
            "chains": (),
            "nodal_loads": (SpatialNodalLoad(2, LOAD),),
            "beam_loads": (),
        }
        fields.update(extra)
        return SpatialRequest(**fields)  # type: ignore[arg-type]


def run(request: SpatialRequest) -> SpatialWorkerResponse:
    outcome = run_worker(request.to_json(), 60.0, worker=WORKER_3D)
    assert outcome.response_text is not None, outcome.stderr
    response = SpatialWorkerResponse.from_json(outcome.response_text)
    assert response.status == WorkerStatus.OK, response.message
    return response


def node(response: SpatialWorkerResponse, tag: int) -> tuple[Six, Six]:
    return next((d, r) for t, d, r in response.node_results if t == tag)


def assert_close(actual: Sequence[float], expected: Sequence[float], scale: float) -> None:
    assert len(actual) == len(expected)
    for a, e in zip(actual, expected, strict=True):
        assert abs(a - e) <= REL * scale, (actual, expected)


def assert_disp(actual: Sequence[float], expected: Sequence[float], length: float) -> None:
    """Escalas: máx|u| para traslaciones y máx(|θ|, máx|u|/L) para giros."""
    trans = max(abs(v) for v in expected[:3])
    assert_close(actual[:3], expected[:3], trans)
    assert_close(actual[3:], expected[3:], max(*(abs(v) for v in expected[3:]), trans / length))


ORIENTATIONS = [
    ("+X", (2.0, 0.0, 0.0), 0.0),
    ("+Y", (0.0, 2.0, 0.0), 0.0),
    ("-X", (-2.0, 0.0, 0.0), 0.0),
    ("+Z", (0.0, 0.0, 2.0), 0.0),
    ("-Z", (0.0, 0.0, -2.0), 0.0),
    ("+X alfa=pi/2", (2.0, 0.0, 0.0), math.pi / 2),
    ("oblicua", OBLIQUE, 0.37),
]


@pytest.mark.parametrize(
    ("tip", "alpha"), [o[1:] for o in ORIENTATIONS], ids=[o[0] for o in ORIENTATIONS]
)
def test_nodal_load_displacements_and_local_force(tip: Triple, alpha: float) -> None:
    """Hechos 1 y 2: vecxz = ez reproduce ADR-001 y localForce sale sin cambio de signo."""
    beam = Cantilever(tip, alpha)
    response = run(beam.request())
    disp, reaction = node(response, 1)[0], node(response, 1)[1]
    assert_disp(node(response, 2)[0], _matvec(beam.flex, LOAD), beam.length)
    assert max(abs(v) for v in disp) == 0.0
    # pI = −(carga trasladada a I) en base local; pJ = carga en base local.
    load_local = _matvec(beam.t6(), LOAD)
    arm = _cross((beam.length, 0.0, 0.0), load_local[:3])
    p_i = [-v for v in load_local[:3]] + [-(load_local[3 + k] + arm[k]) for k in range(3)]
    force = dict(response.beam_forces)[1]
    scale_f, scale_m = max(abs(v) for v in LOAD[:3]), max(abs(v) for v in p_i[3:])
    assert_close(force[:3], p_i[:3], scale_f)
    assert_close(force[3:6], p_i[3:], scale_m)
    assert_close(force[6:9], load_local[:3], scale_f)
    assert_close(force[9:], load_local[3:], scale_m)
    # Reacción global en el empotramiento = Qᵀ·pI por bloques.
    expected_reaction = _matvec(_transpose(beam.t6()), p_i)
    assert_close(reaction[:3], expected_reaction[:3], scale_f)
    assert_close(reaction[3:], expected_reaction[3:], scale_m)


@pytest.mark.parametrize(
    ("tip", "alpha"), [o[1:] for o in ORIENTATIONS], ids=[o[0] for o in ORIENTATIONS]
)
@pytest.mark.parametrize(
    "w", [(0.0, 0.0, 1500.0), (-2500.0, 0.0, 0.0), (0.0, 4000.0, 0.0), (-2500.0, 4000.0, 1500.0)]
)
def test_beam_uniform_is_local_wy_wz_wx(tip: Triple, alpha: float, w: Triple) -> None:
    """Hecho 9: -beamUniform (Wy, Wz, Wx) en base local, frente a la fórmula del voladizo."""
    beam = Cantilever(tip, alpha)
    wy, wz, wx = w
    length = beam.length
    response = run(beam.request(nodal_loads=(), beam_loads=(SpatialBeamLoad(1, wy, wz, wx),)))
    tip_local = _matvec(beam.t6(), node(response, 2)[0])
    expected = [
        wx * length**2 / (2 * E * A),
        wy * length**4 / (8 * E * IZ),
        wz * length**4 / (8 * E * IY),
        0.0,
        -wz * length**3 / (6 * E * IY),
        wy * length**3 / (6 * E * IZ),
    ]
    assert_disp(tip_local, expected, length)
    p_i = [-wx * length, -wy * length, -wz * length, 0.0, wz * length**2 / 2, -wy * length**2 / 2]
    force = dict(response.beam_forces)[1]
    scale_f = max(abs(v) for v in p_i[:3])
    scale_m = max(*(abs(v) for v in p_i[3:]), scale_f * length)
    assert_close(force[:3], p_i[:3], scale_f)
    assert_close(force[3:6], p_i[3:], scale_m)
    assert_close(force[6:9], [0.0] * 3, scale_f)
    assert_close(force[9:], [0.0] * 3, scale_m)


def _chain(tag: int, normal: Triple, length: float, stiffness: float) -> SpatialChain:
    ref = (1.0, 0.0, 0.0) if abs(normal[0]) < 0.9 else (0.0, 1.0, 0.0)
    tangent = _unit(_cross(normal, ref))
    return SpatialChain(
        tag, f"r{tag}", 2, tag + 1, tag + 2, tag + 3, normal, tangent, length, stiffness
    )


@pytest.mark.parametrize("length", [0.016, 1.6, 16.0])
@pytest.mark.parametrize("count", [1, 2], ids=["rodillo", "deslizador"])
def test_inclined_roller_and_slider_are_exact(count: int, length: float) -> None:
    """Hechos 6, 8 y 11: cadena exacta con Lagrange; reacción en N en span(normales)."""
    beam = Cantilever(OBLIQUE, 0.37)
    normals = [_unit((0.3, -0.5, 0.81)), _unit((0.7, 0.6, -0.2))][:count]
    chains = tuple(_chain(10 * (k + 1), n, length, beam.max_diag) for k, n in enumerate(normals))
    response = run(beam.request(chains=chains))
    # Multiplicadores: u = F(P − Σλ_k n_k), n_i·u = 0 → (N F Nᵀ) λ = N F P.
    rows = [[*list(n), 0.0, 0.0, 0.0] for n in normals]
    f_n = [_matvec(beam.flex, r) for r in rows]
    gram: Matrix = [[sum(ri[k] * fn[k] for k in range(6)) for fn in f_n] for ri in rows]
    lam = _solve(
        gram, [sum(r[k] * v for k, v in enumerate(_matvec(beam.flex, LOAD))) for r in rows]
    )
    load = [LOAD[k] - sum(lam[i] * rows[i][k] for i in range(count)) for k in range(6)]
    disp, reaction = node(response, 2)
    expected = _matvec(beam.flex, load)
    assert_disp(disp, expected, beam.length)
    scale = max(abs(v) for v in disp[:3])
    for n in normals:
        assert abs(sum(n[k] * disp[k] for k in range(3))) <= REL * scale
    expected_reaction = [-sum(lam[i] * rows[i][k] for i in range(count)) for k in range(6)]
    force_scale = max(abs(v) for v in LOAD[:3])
    assert_close(reaction, expected_reaction, force_scale)
    for result in response.chain_results:
        # Alias de equalDOF (D3): traslaciones de C = traslaciones de N.
        assert_close(result.c_disp[:3], disp[:3], 1e3 * 2.2e-16 / REL * scale)
        assert max(abs(v) for v in result.r_reaction[3:]) <= REL * force_scale * length


@pytest.mark.parametrize(
    "stiffness",
    [(1e6, 2e7, 3e5, 4e4, 5e5, 6e3), (1e12, 1e3, 0.0, 1e9, 0.0, 1e2), (1e2,) * 6],
    ids=["moderados", "extremos", "blandos"],
)
def test_inclined_springs_and_ground_reaction(stiffness: Six) -> None:
    """Hecho 10: zeroLength orientado; B·nodeReaction(tierra) = −k·B·u."""
    beam = Cantilever(OBLIQUE, 0.37)
    axis_x = _unit((0.3, -0.5, 0.81))
    axis_y = _unit(_cross(axis_x, (1.0, 0.0, 0.0)))
    axis_z = _cross(axis_x, axis_y)
    basis = [list(axis_x), list(axis_y), list(axis_z)]
    springs = tuple(
        SpatialSpring(10 + d, "s", 2, 9, d + 1, k, axis_x, axis_y, False)
        for d, k in enumerate(stiffness)
        if k
    )
    max_diag = max(beam.max_diag, *stiffness)
    response = run(beam.request(springs=springs, alpha_sp=max_diag, alpha_mp=max_diag))
    tb: Matrix = [[0.0] * 6 for _ in range(6)]
    for b in range(2):
        for r in range(3):
            for c in range(3):
                tb[3 * b + r][3 * b + c] = basis[r][c]
    # (I + F·Bᵀ k B) u = F·P
    s = _matmul(
        _matmul(
            _transpose(tb), [[stiffness[i] if i == j else 0.0 for j in range(6)] for i in range(6)]
        ),
        tb,
    )
    fs = _matmul(beam.flex, s)
    system = [[(1.0 if i == j else 0.0) + fs[i][j] for j in range(6)] for i in range(6)]
    expected = _solve(system, _matvec(beam.flex, LOAD))
    disp = node(response, 2)[0]
    assert_disp(disp, expected, beam.length)
    ground = dict(response.ground_reactions)[9]
    in_basis = _matvec(tb, ground)
    expected_force = [-stiffness[k] * v for k, v in enumerate(_matvec(tb, expected))]
    assert_close(in_basis, expected_force, max(abs(v) for v in expected_force))


def test_wrong_protocol_version_is_reported() -> None:
    text = Cantilever((2.0, 0.0, 0.0)).request().to_json().replace('"version":2', '"version":1')
    outcome = run_worker(text, 60.0, worker=WORKER_3D)
    assert outcome.response_text is not None
    assert SpatialWorkerResponse.from_json(outcome.response_text).status == "PROTOCOL_ERROR"


def _constrained(
    beam: Cantilever, rows: list[list[float]], stiffness: Matrix | None = None
) -> tuple[Six, list[float]]:
    """u con resortes S y restricciones R·u = 0: F′ = (I + F·S)⁻¹F, (R F′ Rᵀ) λ = R F′ P."""
    flex = beam.flex
    if stiffness is not None:
        fs = _matmul(beam.flex, stiffness)
        system = [[(1.0 if i == j else 0.0) + fs[i][j] for j in range(6)] for i in range(6)]
        columns = [_solve(system, [beam.flex[i][j] for i in range(6)]) for j in range(6)]
        flex = _transpose(columns)
    f_r = [_matvec(flex, r) for r in rows]
    gram: Matrix = [[sum(ri[k] * fr[k] for k in range(6)) for fr in f_r] for ri in rows]
    lam = _solve(gram, [sum(r[k] * v for k, v in enumerate(_matvec(flex, LOAD))) for r in rows])
    load = [LOAD[k] - sum(lam[i] * rows[i][k] for i in range(len(rows))) for k in range(6)]
    a, b, c, d, e, f = _matvec(flex, load)
    return (a, b, c, d, e, f), lam


def test_global_fix_and_chain_on_the_same_node() -> None:
    """D4 (rodillo de T-FEM-004 en 3D): fix en Uy y cadena con normal en XZ en el mismo nudo."""
    beam = Cantilever(OBLIQUE, 0.37)
    normal = _unit((0.6, 0.0, 0.8))
    response = run(
        beam.request(
            fixes=(SpatialFix(1, (1, 1, 1, 1, 1, 1)), SpatialFix(2, (0, 1, 0, 0, 0, 0))),
            chains=(_chain(10, normal, beam.length, beam.max_diag),),
        )
    )
    rows = [[0.0, 1.0, 0.0, 0.0, 0.0, 0.0], [*normal, 0.0, 0.0, 0.0]]
    expected, lam = _constrained(beam, rows)
    disp, reaction = node(response, 2)
    assert_disp(disp, expected, beam.length)
    assert abs(disp[1]) <= 1e3 * 2.2e-16 * max(abs(v) for v in disp[:3])
    # Reacción del fix y de la cadena, ambas en N (hecho 8): −Rᵀλ; equilibrio global.
    force_scale = max(abs(v) for v in LOAD[:3])
    expected_reaction = [-sum(lam[i] * rows[i][k] for i in range(2)) for k in range(6)]
    assert_close(reaction, expected_reaction, force_scale)
    base = node(response, 1)[1]
    assert_close([base[k] + reaction[k] + LOAD[k] for k in range(3)], [0.0] * 3, force_scale)


def test_chain_and_oriented_springs_on_the_same_node() -> None:
    """D4: cadena y zeroLength orientados en el mismo nudo, con base común."""
    beam = Cantilever(OBLIQUE, 0.37)
    axis_x = _unit((0.3, -0.5, 0.81))
    axis_y = _unit(_cross(axis_x, (1.0, 0.0, 0.0)))
    axis_z = _cross(axis_x, axis_y)
    stiffness = (0.0, 2e7, 3e5, 4e4, 0.0, 6e3)
    springs = tuple(
        SpatialSpring(20 + d, "s", 2, 9, d + 1, k, axis_x, axis_y, False)
        for d, k in enumerate(stiffness)
        if k
    )
    chain = SpatialChain(10, "r", 2, 11, 12, 13, axis_x, axis_y, beam.length, beam.max_diag)
    response = run(beam.request(springs=springs, chains=(chain,)))
    basis = [list(axis_x), list(axis_y), list(axis_z)]
    tb: Matrix = [[0.0] * 6 for _ in range(6)]
    for b in range(2):
        for r in range(3):
            for c in range(3):
                tb[3 * b + r][3 * b + c] = basis[r][c]
    diag = [[stiffness[i] if i == j else 0.0 for j in range(6)] for i in range(6)]
    s = _matmul(_matmul(_transpose(tb), diag), tb)
    expected, lam = _constrained(beam, [[*axis_x, 0.0, 0.0, 0.0]], s)
    disp, reaction = node(response, 2)
    assert_disp(disp, expected, beam.length)
    # Cadena: reacción −λ·n en N. Resortes: B·nodeReaction(tierra) = −k·B·u (hecho 10).
    force_scale = max(abs(v) for v in LOAD[:3])
    assert_close(reaction, [-lam[0] * v for v in (*axis_x, 0.0, 0.0, 0.0)], force_scale)
    ground = dict(response.ground_reactions)[9]
    expected_force = [-stiffness[k] * v for k, v in enumerate(_matvec(tb, expected))]
    assert_close(_matvec(tb, ground), expected_force, force_scale)
    base = node(response, 1)[1]
    assert_close(
        [base[k] + reaction[k] + ground[k] + LOAD[k] for k in range(3)], [0.0] * 3, force_scale
    )


def test_transformation_with_chain_fails_silently(tmp_path: Path) -> None:
    """No regresión del motivo de D3: con Transformation la cadena da analyze = 0 y u erróneo.

    Montaje del hecho 6 (resorte de R de rigidez 1). Con kr = D el mismo encadenado falla con
    código −3 (visible). Si este test deja de cumplirse (OpenSees corregido), D3 sigue en
    Lagrange: solo cambia el motivo documentado en el plan.
    """
    source = WORKER_3D.read_text(encoding="utf-8")
    lagrange = 'ops.constraints("Lagrange", request["alpha_sp"], request["alpha_mp"])'
    assert source.count(lagrange) == 1
    worker = tmp_path / "worker_transformation.py"
    worker.write_text(source.replace(lagrange, 'ops.constraints("Transformation")'), "utf-8")
    beam = Cantilever(OBLIQUE, 0.37)
    normal = _unit((0.3, -0.5, 0.81))
    request = beam.request(chains=(_chain(10, normal, beam.length, 1.0),))
    outcome = run_worker(request.to_json(), 60.0, worker=worker)
    assert outcome.response_text is not None, outcome.stderr
    response = SpatialWorkerResponse.from_json(outcome.response_text)
    assert (response.status, response.analyze_code) == (WorkerStatus.OK, 0)
    exact = node(run(request), 2)[0]
    wrong = node(response, 2)[0]
    scale = max(abs(v) for v in exact[:3])
    assert scale > 0.0
    assert wrong[:3] == (0.0, 0.0, 0.0)
