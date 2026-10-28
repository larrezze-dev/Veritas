"""Referencias analíticas T-FEM-001…004 con OpenSeesPy real (plan T-009).

Cada valor esperado figura dos veces: como literal del plan y como fórmula con E, b, h, L,
P, w y k de este archivo, sin usar las propiedades de sección ni la Q del código. Hipótesis:
Euler-Bernoulli sin cortante, ``geomTransf Linear``, estática lineal, EI y EA constantes y
cargas consistentes, con lo que los valores nodales y de extremo son exactos (plan T-009).

Signos de ADR-001 §2: My > 0 tracciona z > 0; Ry > 0 hace bajar la tangente en +X.
Criterio (revisión M1): |calc − ref| ≤ tol·max(|ref|, S_dim), con S_dim el mayor |ref| de la
misma dimensión (traslación, giro, fuerza, momento) dentro del caso.
"""

import itertools
import json
import math
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from uuid import UUID

import pytest

from fem_support import Frame, inclined
from veritas.core.geometry.entities import Node
from veritas.core.supports.support import (
    Restraints,
    SupportBasis,
    SupportDof,
    build_restraints,
    fixed_restraints,
    pinned_restraints,
    roller_restraints,
)
from veritas.core.units import Quantity
from veritas.fem import (
    AnalysisRun,
    ComponentStatus,
    PlanarResults,
    ReactionSource,
    analyze_planar_xz,
    normalize_planar_xz,
)

pytestmark = pytest.mark.solver

E = 210e9
B, H = 0.2, 0.4
EI = E * B * H**3 / 12  # 2,24e8 N·m²
EA = E * B * H  # 1,68e10 N
P = 10_000.0  # N
W = 10_000.0  # N/m
L1 = 4.0  # m, T-FEM-001, -003 y -004
L2 = 2.0  # m, T-FEM-002
TOL_EXACT = 1e-9
TOL_PENALTY = 1e-6
NA = ComponentStatus.NOT_APPLICABLE
SIN30, COS30, TAN30 = (
    math.sin(math.radians(30)),
    math.cos(math.radians(30)),
    math.tan(math.radians(30)),
)

# Índices ADR-001 de un Vector6.
UX, UZ, RY = 0, 2, 4
FX, FZ, MY = 0, 2, 4
N, VZ = 0, 2


@dataclass(frozen=True)
class Expect:
    """Valor esperado: literal del plan, fórmula y dimensión."""

    name: str
    literal: float
    formula: float
    dim: str  # "u" traslación, "r" giro, "f" fuerza, "m" momento
    computed: float | None


def check_case(items: Iterable[Expect], tol: float) -> None:
    rows = list(items)
    for item in rows:
        # El literal del plan (6 cifras) y la fórmula deben coincidir.
        assert item.formula == pytest.approx(item.literal, rel=1e-5, abs=1e-12), item
    scale: dict[str, float] = {}
    for item in rows:
        scale[item.dim] = max(scale.get(item.dim, 0.0), abs(item.formula))
    for item in rows:
        assert item.computed is not None, item
        bound = tol * max(abs(item.formula), scale[item.dim])
        assert abs(item.computed - item.formula) <= bound, (item, bound)


def solve(frame: Frame) -> tuple[AnalysisRun, PlanarResults]:
    run = analyze_planar_xz(frame.model, frame.config())
    assert run.raw is not None, run.diagnostics
    return run, normalize_planar_xz(run, frame.model)


def disp(res: PlanarResults, node: Node, index: int) -> float | None:
    item = next(n for n in res.nodes if n.node_id == node.id)
    return item.displacement.components[index].value


def react(res: PlanarResults, node: Node, index: int) -> float | None:
    item = next(r for r in res.reactions if r.node_id == node.id)
    return item.global_.components[index].value


def start(res: PlanarResults, member_id: UUID, index: int) -> float | None:
    item = next(m for m in res.members if m.member_id == member_id)
    return item.section_start.components[index].value


def end(res: PlanarResults, member_id: UUID, index: int) -> float | None:
    item = next(m for m in res.members if m.member_id == member_id)
    return item.section_end.components[index].value


def warning_keys(run: AnalysisRun) -> list[tuple[str, str]]:
    return sorted((d.message_key, str(dict(d.params).get("dof", ""))) for d in run.diagnostics)


@dataclass
class Beam:
    """Biapoyada de dos barras, L = 4 m, P hacia abajo en N2 (T-FEM-001, -003, -004)."""

    frame: Frame
    n1: Node
    n2: Node
    n3: Node
    b1: UUID
    b2: UUID


def two_span_beam(support3: Restraints, basis3: SupportBasis = SupportBasis.GLOBAL) -> Beam:
    frame = Frame()
    n1, n2, n3 = frame.node(0, 0), frame.node(L1 / 2, 0), frame.node(L1, 0)
    b1, b2 = frame.member(n1, n2).id, frame.member(n2, n3).id
    frame.support(n1, pinned_restraints())
    frame.support(n3, support3, basis3)
    frame.nodal(n2, fz=-P)
    return Beam(frame, n1, n2, n3, b1, b2)


# --- T-FEM-001: biapoyada con P central ------------------------------------------------


def test_tfem001_simply_supported_central_load() -> None:
    beam = two_span_beam(roller_restraints())
    run, res = solve(beam.frame)
    n1, n2, n3, b1, b2 = beam.n1, beam.n2, beam.n3, beam.b1, beam.b2
    check_case(
        [
            Expect("R_Z N1", 5000.0, P / 2, "f", react(res, n1, FZ)),
            Expect("R_Z N3", 5000.0, P / 2, "f", react(res, n3, FZ)),
            Expect("R_X N1", 0.0, 0.0, "f", react(res, n1, FX)),
            Expect("My(L-) B1", -10_000.0, -P * L1 / 4, "m", end(res, b1, MY)),
            Expect("My(0+) B2", -10_000.0, -P * L1 / 4, "m", start(res, b2, MY)),
            Expect("Vz(L-) B1", -5000.0, -P / 2, "f", end(res, b1, VZ)),
            Expect("Vz(0+) B2", 5000.0, P / 2, "f", start(res, b2, VZ)),
            Expect("Uz N2", -5.95238e-5, -P * L1**3 / (48 * EI), "u", disp(res, n2, UZ)),
            Expect("Ry N1", 4.46429e-5, P * L1**2 / (16 * EI), "r", disp(res, n1, RY)),
            Expect("Ry N3", -4.46429e-5, -P * L1**2 / (16 * EI), "r", disp(res, n3, RY)),
            Expect("Ry N2", 0.0, 0.0, "r", disp(res, n2, RY)),
        ],
        TOL_EXACT,
    )
    r1 = next(r for r in res.reactions if r.node_id == n1.id)
    r3 = next(r for r in res.reactions if r.node_id == n3.id)
    assert r1.global_.components[MY].status is NA
    assert r3.global_.components[MY].status is NA
    assert r3.global_.components[FX].status is NA
    assert warning_keys(run) == WARNINGS_PINNED


# --- T-FEM-002: voladizo con carga uniforme ---------------------------------------------


def cantilever_deflection(a: float) -> float:
    return -W * a**2 * (6 * L2**2 - 4 * L2 * a + a**2) / (24 * EI)


def cantilever_rotation(a: float) -> float:
    return W * (L2**3 - (L2 - a) ** 3) / (6 * EI)


INTERIOR_LITERALS = {
    # a: (Uz, Ry, Vz(0+), My(0+)) del plan (O2, O3).
    0.5: (-9.41685e-6, 3.44122e-5, -15_000.0, 11_250.0),
    1.0: (-3.16220e-5, 5.20833e-5, -10_000.0, 5000.0),
    1.5: (-5.96401e-5, 5.85938e-5, -5000.0, 1250.0),
}


@pytest.mark.parametrize("count", [1, 2, 4])
def test_tfem002_cantilever_uniform_load(count: int) -> None:
    frame = Frame()
    nodes = [frame.node(L2 * i / count, 0) for i in range(count + 1)]
    members = [frame.member(a, b).id for a, b in itertools.pairwise(nodes)]
    frame.support(nodes[0], fixed_restraints())
    for member_id in members:
        frame.model.add_member_uniform_load(frame.case_id, member_id, qz=Quantity.of(-W, "N/m"))
    run, res = solve(frame)
    root, tip = nodes[0], nodes[-1]
    items = [
        Expect("R_Z", 20_000.0, W * L2, "f", react(res, root, FZ)),
        Expect("R_My", -20_000.0, -W * L2**2 / 2, "m", react(res, root, MY)),
        Expect("R_X", 0.0, 0.0, "f", react(res, root, FX)),
        Expect("My(0+) 1", 20_000.0, W * L2**2 / 2, "m", start(res, members[0], MY)),
        Expect("Vz(0+) 1", -20_000.0, -W * L2, "f", start(res, members[0], VZ)),
        Expect("My(L-) n", 0.0, 0.0, "m", end(res, members[-1], MY)),
        Expect("Vz(L-) n", 0.0, 0.0, "f", end(res, members[-1], VZ)),
        Expect("Uz punta", -8.92857e-5, -W * L2**4 / (8 * EI), "u", disp(res, tip, UZ)),
        Expect("Ry punta", 5.95238e-5, W * L2**3 / (6 * EI), "r", disp(res, tip, RY)),
    ]
    for index in range(1, count):
        a = L2 * index / count
        uz, ry, vz, my = INTERIOR_LITERALS[a]
        node, member_id, previous = nodes[index], members[index], members[index - 1]
        items += [
            Expect(f"Uz({a})", uz, cantilever_deflection(a), "u", disp(res, node, UZ)),
            Expect(f"Ry({a})", ry, cantilever_rotation(a), "r", disp(res, node, RY)),
            Expect(f"Vz(0+)({a})", vz, -W * (L2 - a), "f", start(res, member_id, VZ)),
            Expect(f"My(0+)({a})", my, W * (L2 - a) ** 2 / 2, "m", start(res, member_id, MY)),
            # Continuidad sin carga concentrada (ADR-001 §2).
            Expect(f"Vz cont ({a})", vz, -W * (L2 - a), "f", end(res, previous, VZ)),
            Expect(f"My cont ({a})", my, W * (L2 - a) ** 2 / 2, "m", end(res, previous, MY)),
        ]
    check_case(items, TOL_EXACT)
    assert warning_keys(run) == []


# --- T-FEM-003: apoyo con resorte traslacional ------------------------------------------


@pytest.mark.parametrize(
    ("k", "literals"),
    [
        (1e6, (-5.0e-3, -2.559524e-3, 1.294643e-3, 1.205357e-3)),
        (1e8, (-5.0e-5, -8.45238e-5, 5.71429e-5, None)),
    ],
)
def test_tfem003a_global_spring(k: float, literals: tuple[float, ...]) -> None:
    beam = two_span_beam(build_restraints(springs={SupportDof.UZ: Quantity.of(k, "N/m")}))
    run, res = solve(beam.frame)
    n1, n2, n3 = beam.n1, beam.n2, beam.n3
    uz3, uz2, ry1, ry3 = literals
    u3 = -P / (2 * k)
    items = [
        Expect("R_Z N1", 5000.0, P / 2, "f", react(res, n1, FZ)),
        Expect("R_Z N3", 5000.0, P / 2, "f", react(res, n3, FZ)),
        Expect("R_X N1", 0.0, 0.0, "f", react(res, n1, FX)),
        Expect("Uz N3", uz3, u3, "u", disp(res, n3, UZ)),
        Expect("Uz N2", uz2, -P * L1**3 / (48 * EI) + u3 / 2, "u", disp(res, n2, UZ)),
        Expect("Ry N1", ry1, P * L1**2 / (16 * EI) - u3 / L1, "r", disp(res, n1, RY)),
    ]
    if ry3 is not None:
        items.append(Expect("Ry N3", ry3, -P * L1**2 / (16 * EI) - u3 / L1, "r", disp(res, n3, RY)))
    check_case(items, TOL_EXACT)
    # R = −k·u en el GDL del resorte (T-005 O5): salidas independientes del solver.
    r3, uz = react(res, n3, FZ), disp(res, n3, UZ)
    assert r3 is not None
    assert uz is not None
    assert r3 == pytest.approx(-k * uz, rel=TOL_EXACT)
    reaction = next(r for r in res.reactions if r.node_id == n3.id)
    assert reaction.sources == ((SupportDof.UZ, ReactionSource.SPRING),)
    assert reaction.global_.components[FX].status is NA
    assert warning_keys(run) == WARNINGS_PINNED


def test_tfem003b_inclined_spring() -> None:
    k = 1e6
    beam = two_span_beam(
        build_restraints(springs={SupportDof.UZ: Quantity.of(k, "N/m")}), inclined(30)
    )
    run, res = solve(beam.frame)
    n1, n2, n3 = beam.n1, beam.n2, beam.n3
    rn = P / (2 * COS30)
    ux3 = -(P / 2) * TAN30 * L1 / EA
    uz3 = (-rn / k + ux3 * SIN30) / COS30
    ux3_c, uz3_c = disp(res, n3, UX), disp(res, n3, UZ)
    assert ux3_c is not None
    assert uz3_c is not None
    along_ez = -SIN30 * ux3_c + COS30 * uz3_c  # u·ez′, ez′ = (−sin30, 0, cos30)
    reaction = next(r for r in res.reactions if r.node_id == n3.id)
    assert reaction.support_basis is not None
    rz_prime = reaction.support_basis.components[UZ].value
    check_case(
        [
            Expect("R_z′ N3", 5773.503, rn, "f", rz_prime),
            Expect("R_X N1", 2886.751, (P / 2) * TAN30, "f", react(res, n1, FX)),
            Expect("R_X N3", -2886.751, -rn * SIN30, "f", react(res, n3, FX)),
            Expect("R_Z N3", 5000.0, rn * COS30, "f", react(res, n3, FZ)),
            Expect("u·ez′ N3", -5.773503e-3, -rn / k, "u", along_ez),
            Expect("Ux N3", -6.87322e-7, ux3, "u", ux3_c),
            Expect("Uz N3", -6.66706e-3, uz3, "u", uz3_c),
            Expect("Uz N2", -3.393056e-3, -P * L1**3 / (48 * EI) + uz3 / 2, "u", disp(res, n2, UZ)),
            Expect("Ry N1", 1.711409e-3, P * L1**2 / (16 * EI) - uz3 / L1, "r", disp(res, n1, RY)),
        ],
        TOL_EXACT,
    )
    assert rz_prime is not None
    assert rz_prime == pytest.approx(-k * along_ez, rel=TOL_EXACT)  # R = −k·u en la base
    assert reaction.support_basis.components[UX].status is NA
    assert reaction.support_basis.components[UX].value is None
    assert reaction.sources == ((SupportDof.UZ, ReactionSource.SPRING),)
    assert "fem.hypothesis_penalty" not in res.hypotheses
    assert warning_keys(run) == WARNINGS_PINNED


# --- T-FEM-004: rodillo inclinado -------------------------------------------------------


def test_tfem004_inclined_roller() -> None:
    beam = two_span_beam(roller_restraints(), inclined(30))
    run, res = solve(beam.frame)
    n1, n2, n3, b1, b2 = beam.n1, beam.n2, beam.n3, beam.b1, beam.b2
    rn = P / (2 * COS30)
    axial = -(P / 2) * TAN30
    ux3 = axial * L1 / EA
    uz3 = TAN30 * ux3
    reaction = next(r for r in res.reactions if r.node_id == n3.id)
    assert reaction.support_basis is not None
    rz_prime = reaction.support_basis.components[UZ].value
    check_case(
        [
            Expect("R_z′ N3", 5773.503, rn, "f", rz_prime),
            Expect("R_X N1", 2886.751, -axial, "f", react(res, n1, FX)),
            Expect("R_X N3", -2886.751, axial, "f", react(res, n3, FX)),
            Expect("R_Z N1", 5000.0, P / 2, "f", react(res, n1, FZ)),
            Expect("R_Z N3", 5000.0, P / 2, "f", react(res, n3, FZ)),
            Expect("N(0+) B1", -2886.751, axial, "f", start(res, b1, N)),
            Expect("N(L-) B2", -2886.751, axial, "f", end(res, b2, N)),
            Expect("Ux N3", -6.87322e-7, ux3, "u", disp(res, n3, UX)),
            Expect("Uz N3", -3.96825e-7, uz3, "u", disp(res, n3, UZ)),
            Expect("Uz N2", -5.97222e-5, -P * L1**3 / (48 * EI) + uz3 / 2, "u", disp(res, n2, UZ)),
            Expect("Ry N1", 4.47421e-5, P * L1**2 / (16 * EI) - uz3 / L1, "r", disp(res, n1, RY)),
            Expect("Ry N3", -4.45437e-5, -P * L1**2 / (16 * EI) - uz3 / L1, "r", disp(res, n3, RY)),
        ],
        TOL_PENALTY,
    )
    # «Nula en el plano» (revisión M2): cuatro comprobaciones.
    # 1. R_x′ no aplicable, sin valor.
    assert reaction.support_basis.components[UX].status is NA
    assert reaction.support_basis.components[UX].value is None
    # 2. El muelle solo da fuerza según ez′: proyección global sobre ex′ ≈ 0.
    assert reaction.axes is not None
    ex = reaction.axes[0]
    rx, rz = react(res, n3, FX), react(res, n3, FZ)
    assert rx is not None
    assert rz is not None
    assert abs(rx * ex.x + rz * ex.z) <= 64 * sys.float_info.epsilon * math.hypot(rx, rz)
    # 3. Residuo de restricción u·ez′ ≈ −R_n/k_pen, con k_pen leída de la petición.
    assert run.request_json is not None
    springs = json.loads(run.request_json)["springs"]
    (penalty,) = [s for s in springs if s[8]]
    k_pen = penalty[5]
    ux, uz = disp(res, n3, UX), disp(res, n3, UZ)
    assert ux is not None
    assert uz is not None
    residue = -SIN30 * ux + COS30 * uz
    assert residue == pytest.approx(-rn / k_pen, rel=1e-3)
    # 4. Origen e hipótesis declarados.
    assert reaction.sources == ((SupportDof.UZ, ReactionSource.PENALTY),)
    assert "fem.hypothesis_penalty" in res.hypotheses
    assert warning_keys(run) == WARNINGS_PINNED


# Avisos esperados (revisión O4): con articulación y rodillo o resorte, ningún apoyo
# restringe Rx ni Rz; el voladizo empotrado no deja GDL fuera del plano sin restringir.
WARNINGS_PINNED: list[tuple[str, str]] = [
    ("fem.out_of_plane_implicit_restraint", "Rx"),
    ("fem.out_of_plane_implicit_restraint", "Rz"),
]
