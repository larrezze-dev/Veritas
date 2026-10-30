"""Cargas de barra de Veritas en el adaptador plano XZ (plan T-015, E2.1).

Referencias independientes de ``member_loads``:

- R-CANT: voladizos isostáticos de Euler–Bernoulli en forma cerrada;
- R-PROP: barra apoyada-empotrada (hecho 8 del plan);
- R-SUB: el mismo modelo subdividido en los puntos de carga, cargado con tipos ya aceptados
  (``NodalLoad`` y ``MemberUniformLoad``), con extremo liberado, resorte y barra oblicua;
- R-UNI: distribuida en [0, 1] con extremos iguales frente a ``MemberUniformLoad``;
- R-EQ: ΣR + ``case_resultant`` = 0.

Signos en el plano X–Z: v = Uz, θ = dv/dX = −Ry; un par o momento distribuido My actúa sobre
θ como −My. La identidad bit a bit sin cargas nuevas compara el worker v3 congelado con el v4.
"""

import dataclasses
import itertools
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from fem_support import E_PA, Frame, inclined
from veritas import fem
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.diagnostics import DiagnosticCode, InputValueError
from veritas.core.geometry.entities import Member, Node
from veritas.core.geometry.vector import Vec3
from veritas.core.loads.loads import LoadBasis
from veritas.core.supports.support import fixed_restraints, pinned_restraints, roller_restraints
from veritas.core.units import Quantity
from veritas.fem.adapters.planar_xz import translate_planar_xz
from veritas.fem.adapters.protocol import (
    PROTOCOL_VERSION,
    PlanarMemberEndLoad,
    PlanarRequest,
    ProtocolError,
)
from veritas.fem.benchmarks import SPECS, _Case
from veritas.fem.config import AnalysisConfiguration
from veritas.fem.results.normalized import PlanarResults, normalize_planar_xz
from veritas.fem.results.run import RunStatus
from veritas.fem.runners.subprocess_runner import WORKER_PATH, run_worker

D = DiagnosticCode
TOL = 1e-9
WORKER_V3 = Path(__file__).parent / "data" / "opensees_worker_v3.py"
# Componentes de Vector6 (ADR-001): (Fx, Fy, Fz, Mx, My, Mz) o (Ux, Uy, Uz, Rx, Ry, Rz).
FX, FZ, MY = 0, 2, 4
RELEASED_RY = EndConnection.rigid().with_dof(Dof.RY, DofState.RELEASED)


def n(value: float) -> Quantity:
    return Quantity.of(value, "N")


def nm(value: float) -> Quantity:
    return Quantity.of(value, "N*m")


def npm(value: float) -> Quantity:
    return Quantity.of(value, "N/m")


def nmpm(value: float) -> Quantity:
    return Quantity.of(value, "N·m/m")


def spring_ry(k: float) -> EndConnection:
    return EndConnection.rigid().with_dof(
        Dof.RY, DofState.SPRING, Quantity(k, Dof.RY.stiffness_dimension)
    )


def solve(frame: Frame, **overrides: float) -> PlanarResults:
    run = fem.analyze_planar_xz(frame.model, frame.config(**overrides))
    assert run.status is RunStatus.SUCCEEDED, [d.message_key for d in run.diagnostics]
    return normalize_planar_xz(run, frame.model)


def close(actual: float | None, expected: float, scale: float, tol: float = TOL) -> None:
    assert actual is not None
    assert abs(actual - expected) <= tol * abs(scale), (actual, expected)


def ei(frame: Frame) -> float:
    beam = translate_planar_xz(frame.model, frame.config()).request.beams[0]
    return E_PA * beam.inertia


def ea(frame: Frame) -> float:
    beam = translate_planar_xz(frame.model, frame.config()).request.beams[0]
    return E_PA * beam.area


def displacement(results: PlanarResults, node: Node) -> tuple[float | None, ...]:
    return next(r.displacement.values() for r in results.nodes if r.node_id == node.id)


def reaction(results: PlanarResults, node: Node) -> tuple[float | None, ...]:
    return next(r.global_.values() for r in results.reactions if r.node_id == node.id)


def ends(results: PlanarResults, member: Member) -> tuple[tuple[float | None, ...], ...]:
    forces = next(m for m in results.members if m.member_id == member.id)
    return forces.p_i.values(), forces.p_j.values()


# --- Voladizo (R-CANT) -------------------------------------------------------------------

L = 4.0


def cantilever() -> tuple[Frame, Node, Node, Member]:
    frame = Frame()
    a, b = frame.node(0.0, 0.0), frame.node(L, 0.0)
    member = frame.member(a, b)
    frame.support(a, fixed_restraints())
    return frame, a, b, member


@pytest.mark.solver
@pytest.mark.parametrize("xi", [0.25, 0.6])
def test_cantilever_point_load(xi: float) -> None:
    """δ = P·a²(3L − a)/(6EI) y θ = P·a²/(2EI) en la punta; Ry = −θ."""
    frame, _a, b, member = cantilever()
    p = -10e3
    frame.model.add_member_point_load(frame.case_id, member.id, position=xi, fz=n(p))
    a = xi * L
    stiffness = ei(frame)
    delta, theta = p * a**2 * (3 * L - a) / (6 * stiffness), p * a**2 / (2 * stiffness)
    u = displacement(solve(frame), b)
    close(u[FZ], delta, delta)
    close(u[MY], -theta, theta)


@pytest.mark.solver
@pytest.mark.parametrize(("q_i", "q_j", "factor"), [(1.0, 0.0, 1 / 30), (0.0, 1.0, 11 / 120)])
def test_cantilever_triangular_load(q_i: float, q_j: float, factor: float) -> None:
    """Triangular: máximo en el empotramiento δ = wL⁴/(30EI); en la punta 11wL⁴/(120EI)."""
    frame, _a, b, member = cantilever()
    w = -5e3
    frame.model.add_member_distributed_load(
        frame.case_id, member.id, start=0.0, end=1.0, qz_start=npm(w * q_i), qz_end=npm(w * q_j)
    )
    delta = factor * w * L**4 / ei(frame)
    close(displacement(solve(frame), b)[FZ], delta, delta)


@pytest.mark.solver
def test_cantilever_distributed_moment() -> None:
    """m_θ = −my: Uz = −my·L³/(3EI), Ry = my·L²/(2EI) y reacción My = −my·L."""
    frame, a, b, member = cantilever()
    my = 3e3
    frame.model.add_member_distributed_moment(
        frame.case_id, member.id, start=0.0, end=1.0, my_start=nmpm(my), my_end=nmpm(my)
    )
    stiffness = ei(frame)
    results = solve(frame)
    u = displacement(results, b)
    close(u[FZ], -my * L**3 / (3 * stiffness), my * L**3 / (3 * stiffness))
    close(u[MY], my * L**2 / (2 * stiffness), my * L**2 / (2 * stiffness))
    r = reaction(results, a)
    close(r[MY], -my * L, my * L)
    close(r[FZ], 0.0, my)


@pytest.mark.solver
def test_cantilever_axial_point_load() -> None:
    """u = P·a/(EA) en la punta."""
    frame, _a, b, member = cantilever()
    p = 50e3
    frame.model.add_member_point_load(frame.case_id, member.id, position=0.3, fx=n(p))
    u = p * 0.3 * L / ea(frame)
    close(displacement(solve(frame), b)[FX], u, u)


@pytest.mark.solver
def test_cantilever_local_basis_matches_global() -> None:
    """Base LOCAL de una barra horizontal frente a la misma carga en GLOBAL."""
    results = []
    for basis in (LoadBasis.GLOBAL, LoadBasis.LOCAL):
        frame, _a, b, member = cantilever()
        q = frame.model.axes(member.id).to_local if basis is LoadBasis.LOCAL else None
        force = Vec3(0.0, 0.0, -10e3) if q is None else q(Vec3(0.0, 0.0, -10e3))
        frame.model.add_member_point_load(
            frame.case_id,
            member.id,
            position=0.4,
            basis=basis,
            fx=n(force.x),
            fy=n(force.y),
            fz=n(force.z),
        )
        results.append(displacement(solve(frame), b))
    for got, expected in zip(results[1], results[0], strict=True):
        if expected is not None:
            close(got, expected, max(abs(v) for v in results[0] if v is not None))


# --- Extremo liberado y resorte (R-PROP, hecho 8) -----------------------------------------


def propped(end: EndConnection) -> tuple[Frame, Node, Node, Member]:
    """Barra de L = 4 con I sobre un nudo empotrado (extremo ``end``) y J empotrado."""
    frame = Frame()
    a, b = frame.node(0.0, 0.0), frame.node(L, 0.0)
    member = frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.model.set_member_end(member.id, MemberEnd.I, end)
    frame.model.add_member_point_load(frame.case_id, member.id, position=0.25, fz=n(-10.0))
    return frame, a, b, member


@pytest.mark.solver
def test_released_end_gives_propped_cantilever() -> None:
    """R-PROP: R_I = P·b²(a + 2L)/(2L³), M_I = 0, |M_J| = P·a·b(L + a)/(2L²)."""
    frame, a, b, member = propped(RELEASED_RY)
    results = solve(frame)
    p, a_, b_ = 10.0, 1.0, 3.0
    r_i = p * b_**2 * (a_ + 2 * L) / (2 * L**3)
    m_j = p * a_ * b_ * (L + a_) / (2 * L**2)
    close(reaction(results, a)[FZ], r_i, p)
    close(reaction(results, b)[FZ], p - r_i, p)
    p_i, p_j = ends(results, member)
    close(p_i[MY], 0.0, m_j)
    moment_j = p_j[MY]
    assert moment_j is not None
    close(abs(moment_j), m_j, m_j)


@pytest.mark.solver
def test_spring_end_satisfies_spring_relation() -> None:
    """Resorte de giro: el momento publicado en I es la reacción de giro del nudo I."""
    frame, a, _b, member = propped(spring_ry(1e9))
    results = solve(frame)
    p_i, _p_j = ends(results, member)
    close(p_i[MY], reaction(results, a)[MY] or 0.0, 10.0)
    moment_i = p_i[MY]
    assert moment_i is not None
    assert abs(moment_i) > 0.1


# --- Subdivisión (R-SUB) ------------------------------------------------------------------

DX, DZ = 3.0, 4.0  # barra oblicua de L = 5


def oblique(
    end_i: EndConnection | None, support_j: Callable[[Frame, Node], None], xs: list[float]
) -> tuple[Frame, list[Node], list[Member]]:
    frame = Frame()
    nodes = [frame.node(DX * x, DZ * x) for x in xs]
    members = [frame.member(p, q) for p, q in itertools.pairwise(nodes)]
    frame.support(nodes[0], fixed_restraints())
    support_j(frame, nodes[-1])
    if end_i is not None:
        frame.model.set_member_end(members[0].id, MemberEnd.I, end_i)
    return frame, nodes, members


def fixed_j(frame: Frame, node: Node) -> None:
    frame.support(node, fixed_restraints())


def pinned_j(frame: Frame, node: Node) -> None:
    frame.support(node, pinned_restraints())


def inclined_roller_j(frame: Frame, node: Node) -> None:
    frame.support(node, roller_restraints(), basis=inclined(25.0))


FP_X, FP_Z, C_Y, Q_Z = 4e3, -12e3, 7e3, -3e3  # puntual en 0,3; par en 0,6; parcial [0,2; 0,7]


def loaded_whole(end_i: EndConnection | None, support: Callable[[Frame, Node], None]) -> Any:
    frame, nodes, members = oblique(end_i, support, [0.0, 1.0])
    case, member = frame.case_id, members[0].id
    frame.model.add_member_point_load(case, member, position=0.3, fx=n(FP_X), fz=n(FP_Z))
    frame.model.add_member_point_load(case, member, position=0.6, my=nm(C_Y))
    frame.model.add_member_distributed_load(
        case, member, start=0.2, end=0.7, qz_start=npm(Q_Z), qz_end=npm(Q_Z)
    )
    return frame, nodes, members


def loaded_split(end_i: EndConnection | None, support: Callable[[Frame, Node], None]) -> Any:
    frame, nodes, members = oblique(end_i, support, [0.0, 0.2, 0.3, 0.6, 0.7, 1.0])
    frame.nodal(nodes[2], fx=FP_X, fz=FP_Z)
    frame.nodal(nodes[3], my=C_Y)
    for member in members[1:4]:
        frame.model.add_member_uniform_load(frame.case_id, member.id, qz=npm(Q_Z))
    return frame, nodes, members


SUB_CASES = {
    # Biempotrada sin GDL libres: fem.no_free_dofs (T-009); el caso rígido apoya J.
    "rigido": (None, pinned_j, TOL),
    "liberado": (RELEASED_RY, fixed_j, TOL),
    "resorte": (spring_ry(5e6), pinned_j, TOL),
}
# Apoyo inclinado penalizado (D11) solo en R-EQ: el modelo subdividido de referencia, que solo
# usa tipos ya aceptados, falla a veces el equilibrio de nudos de la normalización según el
# orden de tags (hallazgo previo a T-015, registrado en el plan).
EQ_CASES = {**SUB_CASES, "penalizado": (None, inclined_roller_j, TOL)}


@pytest.mark.solver
@pytest.mark.parametrize("name", sorted(SUB_CASES))
def test_subdivision_reference(name: str) -> None:
    """R-SUB: desplazamientos, reacciones y esfuerzos de extremo iguales al subdividir."""
    end_i, support, tol = SUB_CASES[name]
    whole, w_nodes, w_members = loaded_whole(end_i, support)
    split, s_nodes, s_members = loaded_split(end_i, support)
    got, ref = solve(whole), solve(split)
    pairs = [
        (displacement(got, w_nodes[0]), displacement(ref, s_nodes[0])),
        (displacement(got, w_nodes[1]), displacement(ref, s_nodes[-1])),
    ]
    forces = [
        (reaction(got, w_nodes[0]), reaction(ref, s_nodes[0])),
        (reaction(got, w_nodes[1]), reaction(ref, s_nodes[-1])),
        (ends(got, w_members[0])[0], ends(ref, s_members[0])[0]),
        (ends(got, w_members[0])[1], ends(ref, s_members[-1])[1]),
    ]
    for group in (pairs, forces):
        scale = max(abs(v) for _g, e in group for v in e if v is not None)
        for g, e in group:
            for got_value, expected in zip(g, e, strict=True):
                if expected is None:
                    assert got_value is None
                else:
                    close(got_value, expected, scale, tol)


@pytest.mark.solver
def test_full_span_distributed_matches_uniform() -> None:
    """R-UNI: distribuida en [0, 1] con extremos iguales = ``MemberUniformLoad``."""
    results = []
    for uniform in (True, False):
        frame, nodes, members = oblique(RELEASED_RY, fixed_j, [0.0, 1.0])
        if uniform:
            frame.model.add_member_uniform_load(frame.case_id, members[0].id, qz=npm(Q_Z))
        else:
            frame.model.add_member_distributed_load(
                frame.case_id, members[0].id, start=0.0, end=1.0, qz_start=npm(Q_Z), qz_end=npm(Q_Z)
            )
        r = solve(frame)
        results.append(
            (*displacement(r, nodes[1]), *ends(r, members[0])[0], *ends(r, members[0])[1])
        )
    scale = max(abs(v) for v in results[0] if v is not None)
    for got, expected in zip(results[1], results[0], strict=True):
        if expected is not None:
            close(got, expected, scale, 1e-12)


@pytest.mark.solver
@pytest.mark.parametrize("name", sorted(EQ_CASES))
def test_reactions_balance_case_resultant(name: str) -> None:
    """R-EQ: ΣR + resultante del caso = 0 (fuerzas y momentos respecto al origen)."""
    end_i, support, _tol = EQ_CASES[name]
    frame, _nodes, _members = loaded_whole(end_i, support)
    frame.model.add_member_distributed_moment(
        frame.case_id, _members[0].id, start=0.1, end=0.9, my_start=nmpm(2e3), my_end=nmpm(-1e3)
    )
    results = solve(frame)
    resultant = frame.model.case_resultant(frame.case_id)
    fx, fz = resultant.force[0].si_value, resultant.force[2].si_value
    my = resultant.moment[1].si_value
    for r in results.reactions:
        node = frame.model.node(r.node_id).position
        rx, _ry, rz, _mx, ry_m, _mz = (0.0 if v is None else v for v in r.global_.values())
        fx, fz = fx + rx, fz + rz
        my += ry_m + node.z * rx - node.x * rz
    scale = abs(FP_Z) + abs(Q_Z) * 5
    close(fx, 0.0, scale)
    close(fz, 0.0, scale)
    close(my, 0.0, scale * 5)


# --- Asiento con carga nueva (D11) --------------------------------------------------------


@pytest.mark.solver
def test_settlement_superposes_with_member_load() -> None:
    """Carga nueva + asiento = suma de las dos corridas por separado (lineal)."""

    def build(load: bool, settle: bool) -> tuple[Frame, Node]:
        frame = Frame()
        a, b = frame.node(0.0, 0.0), frame.node(L, 0.0)
        member = frame.member(a, b)
        frame.support(a, fixed_restraints())
        frame.support(b, pinned_restraints())
        if load:
            frame.model.add_member_point_load(frame.case_id, member.id, position=0.3, fz=n(-8e3))
        if settle:
            frame.model.add_support_settlement(frame.case_id, b.id, uz=Quantity.of(-0.002, "m"))
        return frame, a

    combined, a = build(True, True)
    parts = [build(True, False), build(False, True)]
    total = reaction(solve(combined), a)
    pieces = [reaction(solve(f), node) for f, node in parts]
    scale = max(abs(v) for v in total if v is not None)
    for index, value in enumerate(total):
        if value is not None:
            expected = sum(p[index] or 0.0 for p in pieces)
            close(value, expected, scale)


# --- Traductor (D9) y protocolo v4 --------------------------------------------------------


def frame_with(kind: str, basis: LoadBasis = LoadBasis.GLOBAL, **values: float) -> Frame:
    frame, _a, _b, member = cantilever()
    model, case = frame.model, frame.case_id
    if kind == "point":
        units = {"fx": n, "fy": n, "fz": n, "mx": nm, "my": nm, "mz": nm}
        model.add_member_point_load(
            case,
            member.id,
            position=0.5,
            basis=basis,
            **{k: units[k](v) for k, v in values.items()},
        )
    elif kind == "distributed":
        model.add_member_distributed_load(
            case,
            member.id,
            start=0.0,
            end=0.5,
            basis=basis,
            **{k: npm(v) for k, v in values.items()},
        )
    else:
        model.add_member_distributed_moment(
            case,
            member.id,
            start=0.0,
            end=0.5,
            basis=basis,
            **{k: nmpm(v) for k, v in values.items()},
        )
    return frame


@pytest.mark.parametrize(
    ("kind", "values", "key"),
    [
        ("point", {"fy": 1.0}, "fem.member_point_load_out_of_plane"),
        ("point", {"mx": 1.0}, "fem.member_point_load_out_of_plane"),
        ("point", {"mz": 1.0}, "fem.member_point_load_out_of_plane"),
        ("distributed", {"qy_end": 1.0}, "fem.member_load_out_of_plane"),
        ("moment", {"mx_start": 1.0}, "fem.member_moment_out_of_plane"),
        ("moment", {"mz_end": 1.0}, "fem.member_moment_out_of_plane"),
    ],
)
def test_out_of_plane_components_are_rejected(
    kind: str, values: dict[str, float], key: str
) -> None:
    """D9: toda componente fuera del plano se rechaza; nunca se ignora."""
    for basis in (LoadBasis.GLOBAL, LoadBasis.LOCAL):
        frame = frame_with(kind, basis, **values)
        with pytest.raises(InputValueError) as info:
            translate_planar_xz(frame.model, frame.config())
        assert info.value.code is D.UNSUPPORTED_CONFIGURATION
        assert info.value.diagnostic.message_key == key


def test_point_couple_maps_to_minus_ry() -> None:
    """θ = −Ry: un par My en el centro da la carga equivalente de un par −My en θ."""
    frame = frame_with("point", my=8.0)
    request = translate_planar_xz(frame.model, frame.config()).request
    loads = {load.node: load for load in request.member_end_loads}
    beam = request.beams[0]
    load_i, load_j = loads[beam.node_i], loads[beam.node_j]
    # Par c = −My en ξ = 0,5 de una barra horizontal (revisión de E2.0): p0[M_I] = c·b(2a−b)/L²
    # = c/4, p0[M_J] = c·a(2b−a)/L² = c/4 y p0[V_I] = 6c·a·b/L³; la carga equivalente es −p0.
    c = -8.0
    close(load_i.m, -c / 4, 8.0)
    close(load_j.m, -c / 4, 8.0)
    close(load_i.p2, -6 * c * 2 * 2 / L**3, 8.0)
    close(load_i.p2 + load_j.p2, 0.0, 8.0)


def test_released_end_load_goes_to_aux() -> None:
    """D7/D9: la carga del extremo liberado va al aux, no al maestro."""
    frame, _a, _b, _member = propped(RELEASED_RY)
    request = translate_planar_xz(frame.model, frame.config()).request
    (link,) = request.end_links
    beam = request.beams[0]
    assert {load.node for load in request.member_end_loads} == {link.aux, beam.node_j}
    assert link.master == beam.node_i
    assert request.orphan_fixes == ()


def test_protocol_v4_round_trip_and_validation() -> None:
    frame, _a, _b, _member = propped(RELEASED_RY)
    request = translate_planar_xz(frame.model, frame.config()).request
    data = json.loads(request.to_json())
    assert data["version"] == PROTOCOL_VERSION == 4
    assert PlanarRequest.from_json(request.to_json()) == request
    with pytest.raises(ProtocolError):
        PlanarRequest.from_json(json.dumps({**data, "version": 3}))
    missing = {key: value for key, value in data.items() if key != "member_end_loads"}
    with pytest.raises(ProtocolError):
        PlanarRequest.from_json(json.dumps(missing))
    (link,) = request.end_links
    first = request.member_end_loads[0]
    for bad in (
        (*request.member_end_loads, first),  # repetida
        (PlanarMemberEndLoad(first.beam, link.master, 0.0, 1.0, 0.0),),  # maestro, no el aux
        (PlanarMemberEndLoad(99, 2, 0.0, 1.0, 0.0),),  # barra desconocida
    ):
        with pytest.raises(ProtocolError):
            dataclasses.replace(request, member_end_loads=bad)


# --- Identidad bit a bit sin cargas nuevas (worker v3 congelado) --------------------------


def _numeric(response: dict[str, Any]) -> dict[str, Any]:
    return {key: response[key] for key in ("nodes", "aux", "end_aux", "beams")}


@pytest.mark.solver
@pytest.mark.parametrize("case_id", sorted(SPECS))
def test_responses_without_member_loads_match_v3(case_id: str) -> None:
    """Bit a bit: worker v3 congelado con la petición v3 y worker v4 con la misma en v4."""
    for spec in SPECS[case_id]:
        case = _Case()
        spec.build(case)
        request = translate_planar_xz(case.model, AnalysisConfiguration(case.load_case.id)).request
        assert request.member_end_loads == ()
        old_request = {**request.to_dict(), "version": 3}
        del old_request["member_end_loads"]
        old = run_worker(json.dumps(old_request), 60.0, WORKER_V3)
        new = run_worker(request.to_json(), 60.0, WORKER_PATH)
        assert old.response_text is not None, old.stderr
        assert new.response_text is not None, new.stderr
        before, after = json.loads(old.response_text), json.loads(new.response_text)
        assert before["status"] == after["status"] == "OK", after["message"]
        assert (before["version"], after["version"]) == (3, 4)
        assert _numeric(after) == _numeric(before)


def test_no_member_loads_leave_request_unchanged() -> None:
    """Sin cargas nuevas, la petición v4 es la v3 más la versión y el campo vacío."""
    frame, _a, _b, member = cantilever()
    frame.model.add_member_uniform_load(frame.case_id, member.id, qz=npm(-1e3))
    data = translate_planar_xz(frame.model, frame.config()).request.to_dict()
    assert data["member_end_loads"] == []
    assert data["version"] == PROTOCOL_VERSION
    assert set(data) - {"member_end_loads"} == {
        "protocol",
        "version",
        "model_revision",
        "case_id",
        "case_revision",
        "nodes",
        "beams",
        "fixes",
        "springs",
        "nodal_loads",
        "beam_loads",
        "end_links",
        "orphan_fixes",
        "settlements",
    }
