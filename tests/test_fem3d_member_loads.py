"""Cargas de barra de Veritas en el adaptador espacial (plan T-015, E2.2).

Referencias independientes de ``member_loads``:

- R-CANT: voladizo oblicuo de Euler–Bernoulli en forma cerrada (flexión y torsión);
- R-SUB: el mismo modelo subdividido en los puntos de carga, cargado con tipos ya aceptados
  (``NodalLoad`` y ``MemberUniformLoad``), con ``-releasey``/``-releasez`` (códigos 2 y 3) y
  con extremo en aux con resorte penalizado (ADR-004, D11);
- contraste D1: el mismo modelo en el plano XZ con los dos adaptadores;
- R-EQ: ΣR + ``case_resultant`` = 0.

Cargas en base LOCAL salvo que se indique: la subdivisión conserva la base local de la barra.
La identidad bit a bit sin cargas nuevas compara el worker 3D v4 congelado con el v5.
"""

import dataclasses
import itertools
import json
from pathlib import Path
from typing import Any

import pytest

from fem_support import Frame
from veritas import fem
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.diagnostics import DiagnosticCode, InputValueError
from veritas.core.geometry.entities import Member, Node
from veritas.core.geometry.vector import Vec3
from veritas.core.loads.loads import LoadBasis
from veritas.core.supports.support import fixed_restraints, pinned_restraints
from veritas.core.units import Quantity
from veritas.fem.adapters.protocol import NonFiniteValueError, ProtocolError
from veritas.fem.benchmarks import SPATIAL_SPECS, _Case
from veritas.fem.results.normalized import normalize_planar_xz
from veritas.fem.results.run import RunStatus
from veritas.fem.runners.subprocess_runner import run_worker
from veritas.fem.spatial import WORKER_3D_PATH, analyze_spatial
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.postconditions import member_fixed_end_forces
from veritas.fem.spatial.protocol import (
    PROTOCOL_VERSION,
    SpatialMemberEndLoad,
    SpatialRequest,
)
from veritas.fem.spatial.results import SpatialResults, normalize_spatial
from veritas.fem.spatial.translator import _orphan_fixes, translate_spatial

D = DiagnosticCode
TOL = 1e-9
WORKER_V4 = Path(__file__).parent / "data" / "opensees_worker_3d_v4.py"
FX, FY, FZ, MX, MY, MZ = range(6)


def n(value: float) -> Quantity:
    return Quantity.of(value, "N")


def nm(value: float) -> Quantity:
    return Quantity.of(value, "N*m")


def npm(value: float) -> Quantity:
    return Quantity.of(value, "N/m")


def nmpm(value: float) -> Quantity:
    return Quantity.of(value, "N·m/m")


def point(frame: Frame, x: float, y: float, z: float) -> Node:
    return frame.node(x, z, y=y)


def config(frame: Frame) -> SpatialAnalysisConfiguration:
    return SpatialAnalysisConfiguration(frame.case_id)


def solve(frame: Frame) -> SpatialResults:
    run = analyze_spatial(frame.model, config(frame))
    assert run.status is RunStatus.SUCCEEDED, [d.message_key for d in run.diagnostics]
    return normalize_spatial(run, frame.model)


def close(actual: float | None, expected: float, scale: float, tol: float = TOL) -> None:
    assert actual is not None
    assert abs(actual - expected) <= tol * abs(scale), (actual, expected)


def displacement(results: SpatialResults, node: Node) -> tuple[float | None, ...]:
    return next(r.displacement.values() for r in results.nodes if r.node_id == node.id)


def reaction(results: SpatialResults, node: Node) -> tuple[float | None, ...]:
    return next(r.global_.values() for r in results.reactions if r.node_id == node.id)


def ends(results: SpatialResults, member: Member) -> tuple[tuple[float | None, ...], ...]:
    forces = next(m for m in results.members if m.member_id == member.id)
    return forces.p_i.values(), forces.p_j.values()


def along(values: tuple[float | None, ...], axis: Vec3, offset: int = 0) -> float:
    """Proyección de la terna (traslaciones u ``offset`` = 3 giros) sobre ``axis``."""
    x, y, z = (values[offset + k] or 0.0 for k in range(3))
    return x * axis.x + y * axis.y + z * axis.z


def spring(dof: Dof, k: float) -> EndConnection:
    return EndConnection.rigid().with_dof(
        dof, DofState.SPRING, Quantity(k, dof.stiffness_dimension)
    )


def released(*dofs: Dof) -> EndConnection:
    connection = EndConnection.rigid()
    for dof in dofs:
        connection = connection.with_dof(dof, DofState.RELEASED)
    return connection


# --- Voladizo oblicuo (R-CANT) -----------------------------------------------------------

TIP = (2.0, 3.0, 1.5)


def cantilever() -> tuple[Frame, Node, Member]:
    frame = Frame()
    a, b = point(frame, 0.0, 0.0, 0.0), point(frame, *TIP)
    member = frame.member(a, b)
    frame.support(a, fixed_restraints())
    return frame, b, member


def section(frame: Frame) -> tuple[float, float, float, float]:
    """(L, E·Iy, E·Iz, G·J) de la barra traducida."""
    request = translate_spatial(frame.model, config(frame)).request
    beam = request.beams[0]
    length = sum(v * v for v in TIP) ** 0.5
    return (
        length,
        beam.elastic_modulus * beam.inertia_y,
        beam.elastic_modulus * beam.inertia_z,
        beam.shear_modulus * beam.torsion_constant,
    )


@pytest.mark.solver
@pytest.mark.parametrize("xi", [0.3, 0.75])
def test_cantilever_point_forces(xi: float) -> None:
    """δ = P·a²(3L − a)/(6EI) en la punta, en ey (con Iz) y en ez (con Iy)."""
    frame, b, member = cantilever()
    py, pz = -6e3, 9e3
    frame.model.add_member_point_load(
        frame.case_id, member.id, position=xi, basis=LoadBasis.LOCAL, fy=n(py), fz=n(pz)
    )
    length, eiy, eiz, _gj = section(frame)
    a = xi * length
    axes = frame.model.axes(member.id)
    u = displacement(solve(frame), b)
    delta_y = py * a**2 * (3 * length - a) / (6 * eiz)
    delta_z = pz * a**2 * (3 * length - a) / (6 * eiy)
    close(along(u, axes.ey), delta_y, delta_y)
    close(along(u, axes.ez), delta_z, delta_z)


@pytest.mark.solver
def test_cantilever_point_torque() -> None:
    """φ = T·a/(GJ) en la punta."""
    frame, b, member = cantilever()
    torque = 5e3
    frame.model.add_member_point_load(
        frame.case_id, member.id, position=0.4, basis=LoadBasis.LOCAL, mx=nm(torque)
    )
    length, _eiy, _eiz, gj = section(frame)
    phi = torque * 0.4 * length / gj
    u = displacement(solve(frame), b)
    close(along(u, frame.model.axes(member.id).ex, 3), phi, phi)


@pytest.mark.solver
def test_cantilever_distributed_torque() -> None:
    """φ = mₓ·L²/(2GJ) en la punta y reacción de torsión mₓ·L."""
    frame, b, member = cantilever()
    m = 2e3
    frame.model.add_member_distributed_moment(
        frame.case_id,
        member.id,
        start=0.0,
        end=1.0,
        basis=LoadBasis.LOCAL,
        mx_start=nmpm(m),
        mx_end=nmpm(m),
    )
    length, _eiy, _eiz, gj = section(frame)
    phi = m * length**2 / (2 * gj)
    results = solve(frame)
    ex = frame.model.axes(member.id).ex
    close(along(displacement(results, b), ex, 3), phi, phi)
    a = next(node for node in frame.model.nodes.values() if node.id != b.id)
    close(along(reaction(results, a), ex, 3), -m * length, m * length)


# --- Subdivisión (R-SUB) ------------------------------------------------------------------

XA, XB, XC = (0.0, 0.0, 0.0), (2.0, 3.0, 1.5), (5.0, 2.0, -1.0)
F_LOCAL, M_LOCAL = (1.5e3, -8e3, 6e3), (900.0, -2.5e3, 4e3)  # Puntual en 0,3.
Q_LOCAL = (0.0, -3e3, 2e3)  # Uniforme parcial en [0,5; 0,8].


def two_bars(
    connections: tuple[EndConnection | None, EndConnection | None], xs: list[float]
) -> tuple[Frame, list[Node], list[Member], Member]:
    """Barra A → B (subdividida en ``xs``) y barra B → C; A y C empotrados."""
    frame = Frame()
    a, b, c = (point(frame, *p) for p in (XA, XB, XC))
    inner = [point(frame, *(p + x * (q - p) for p, q in zip(XA, XB, strict=True))) for x in xs]
    nodes = [a, *inner, b]
    members = [frame.member(p, q) for p, q in itertools.pairwise(nodes)]
    other = frame.member(b, c)
    frame.support(a, fixed_restraints())
    frame.support(c, fixed_restraints())
    end_i, end_j = connections
    if end_i is not None:
        frame.model.set_member_end(members[0].id, MemberEnd.I, end_i)
    if end_j is not None:
        frame.model.set_member_end(members[-1].id, MemberEnd.J, end_j)
    return frame, nodes, members, other


def loaded_whole(connections: tuple[EndConnection | None, EndConnection | None]) -> Any:
    frame, nodes, members, other = two_bars(connections, [])
    case, member = frame.case_id, members[0].id
    fx, fy, fz = (n(v) for v in F_LOCAL)
    mx, my, mz = (nm(v) for v in M_LOCAL)
    frame.model.add_member_point_load(
        case, member, position=0.3, basis=LoadBasis.LOCAL, fx=fx, fy=fy, fz=fz, mx=mx, my=my, mz=mz
    )
    _qx, qy, qz = (npm(v) for v in Q_LOCAL)
    frame.model.add_member_distributed_load(
        case,
        member,
        start=0.5,
        end=0.8,
        basis=LoadBasis.LOCAL,
        qy_start=qy,
        qz_start=qz,
        qy_end=qy,
        qz_end=qz,
    )
    return frame, nodes, members, other


def loaded_split(connections: tuple[EndConnection | None, EndConnection | None]) -> Any:
    frame, nodes, members, other = two_bars(connections, [0.3, 0.5, 0.8])
    axes = frame.model.axes(members[0].id)
    force, moment = axes.to_global(Vec3(*F_LOCAL)), axes.to_global(Vec3(*M_LOCAL))
    frame.nodal(
        nodes[1],
        fx=force.x,
        fy=force.y,
        fz=force.z,
        mx=moment.x,
        my=moment.y,
        mz=moment.z,
    )
    _qx, qy, qz = (npm(v) for v in Q_LOCAL)
    frame.model.add_member_uniform_load(
        frame.case_id, members[2].id, basis=LoadBasis.LOCAL, qy=qy, qz=qz
    )
    return frame, nodes, members, other


SUB_CASES: dict[str, tuple[EndConnection | None, EndConnection | None]] = {
    "rigido": (None, None),
    "releasey_3": (released(Dof.RY), released(Dof.RY)),
    "releasez_2": (None, released(Dof.RZ)),
    "releasey_1_releasez_2": (released(Dof.RY), released(Dof.RZ)),
    "resorte_penalizado": (None, spring(Dof.RY, 5e6)),
}


@pytest.mark.solver
@pytest.mark.parametrize("name", sorted(SUB_CASES))
def test_subdivision_reference(name: str) -> None:
    """R-SUB: desplazamientos, reacciones y esfuerzos de extremo iguales al subdividir."""
    connections = SUB_CASES[name]
    whole, w_nodes, w_members, w_other = loaded_whole(connections)
    split, s_nodes, s_members, s_other = loaded_split(connections)
    request = translate_spatial(whole.model, config(whole)).request
    assert len(request.member_end_loads) == 2
    assert bool(request.end_penalties) == (name == "resorte_penalizado")
    got, ref = solve(whole), solve(split)
    pairs = [(displacement(got, w_nodes[-1]), displacement(ref, s_nodes[-1]))]
    forces = [
        (reaction(got, w_nodes[0]), reaction(ref, s_nodes[0])),
        (ends(got, w_members[0])[0], ends(ref, s_members[0])[0]),
        (ends(got, w_members[0])[1], ends(ref, s_members[-1])[1]),
        (ends(got, w_other)[0], ends(ref, s_other)[0]),
    ]
    for group in (pairs, forces):
        scale = max(abs(v) for _g, e in group for v in e if v is not None)
        for g, e in group:
            for got_value, expected in zip(g, e, strict=True):
                assert expected is not None
                close(got_value, expected, scale)


# --- Contraste D1: plano XZ con los dos adaptadores ---------------------------------------


@pytest.mark.solver
@pytest.mark.parametrize("end_i", [None, released(Dof.RY)], ids=["rigido", "liberado"])
def test_planar_model_matches_planar_adapter(end_i: EndConnection | None) -> None:
    """Un modelo en el plano XZ con cargas nuevas da lo mismo con los dos adaptadores."""
    frame = Frame()
    a, b = frame.node(0.0, 0.0), frame.node(3.0, 4.0)
    member = frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.support(b, pinned_restraints())
    if end_i is not None:
        frame.model.set_member_end(member.id, MemberEnd.I, end_i)
    model, case = frame.model, frame.case_id
    model.add_member_point_load(case, member.id, position=0.3, fx=n(4e3), fz=n(-12e3))
    model.add_member_point_load(case, member.id, position=0.6, my=nm(7e3))
    model.add_member_distributed_load(
        case, member.id, start=0.2, end=0.7, qz_start=npm(-3e3), qz_end=npm(-1e3)
    )
    model.add_member_distributed_moment(
        case, member.id, start=0.1, end=0.9, my_start=nmpm(2e3), my_end=nmpm(-1e3)
    )
    run = fem.analyze_planar_xz(model, frame.config())
    assert run.status is RunStatus.SUCCEEDED, [d.message_key for d in run.diagnostics]
    planar = normalize_planar_xz(run, model)
    spatial = solve(frame)
    p_ends = next(m for m in planar.members if m.member_id == member.id)
    s_ends = ends(spatial, member)
    groups = [
        [
            (
                next(r.displacement.values() for r in planar.nodes if r.node_id == b.id),
                displacement(spatial, b),
            )
        ],
        [
            (
                next(r.global_.values() for r in planar.reactions if r.node_id == a.id),
                reaction(spatial, a),
            ),
            (p_ends.p_i.values(), s_ends[0]),
            (p_ends.p_j.values(), s_ends[1]),
        ],
    ]
    for group in groups:
        scale = max(abs(x) for pair in group for v in pair for x in v if x is not None)
        for planar_values, spatial_values in group:
            for k in (FX, FZ, MY):
                expected = planar_values[k]
                assert expected is not None
                close(spatial_values[k], expected, scale)
            for k in (FY, MX, MZ):  # Fuera del plano: nulo salvo redondeo.
                close(spatial_values[k], 0.0, scale)


# --- R-EQ, asiento (D11) y huérfanos ------------------------------------------------------


@pytest.mark.solver
@pytest.mark.parametrize("name", ["rigido", "releasey_3", "resorte_penalizado"])
def test_reactions_balance_case_resultant(name: str) -> None:
    """R-EQ: ΣR + resultante del caso = 0 (fuerzas y momentos respecto al origen), con cargas
    en base GLOBAL, trapezoidal y momento distribuido además de las de R-SUB."""
    frame, _nodes, members, _other = loaded_whole(SUB_CASES[name])
    model, case, member = frame.model, frame.case_id, members[0].id
    model.add_member_distributed_load(
        case, member, start=0.1, end=0.6, qx_start=npm(1e3), qz_start=npm(-2e3), qy_end=npm(4e3)
    )
    model.add_member_distributed_moment(
        case, member, start=0.2, end=0.9, my_start=nmpm(1.5e3), mz_end=nmpm(-2e3)
    )
    results = solve(frame)
    resultant = model.case_resultant(case)
    force = Vec3(*(c.si_value for c in resultant.force))
    moment = Vec3(*(c.si_value for c in resultant.moment))
    scale = force.norm()
    for r in results.reactions:
        values = [0.0 if v is None else v for v in r.global_.values()]
        f, m = Vec3(*values[:3]), Vec3(*values[3:])
        force, moment = force + f, moment + m + model.node(r.node_id).position.cross(f)
        scale += f.norm()
    for value in (*force.components(), *(v / 5.0 for v in moment.components())):
        close(value, 0.0, scale)


@pytest.mark.solver
def test_settlement_superposes_with_member_load() -> None:
    """Carga nueva + asiento = suma de las dos corridas por separado (lineal)."""

    def build(load: bool, settle: bool) -> tuple[Frame, Node]:
        frame = Frame()
        a, b = point(frame, 0.0, 0.0, 0.0), point(frame, *TIP)
        member = frame.member(a, b)
        frame.support(a, fixed_restraints())
        frame.support(b, pinned_restraints())
        if load:
            frame.model.add_member_point_load(
                frame.case_id, member.id, position=0.3, fy=n(5e3), fz=n(-8e3), mx=nm(1e3)
            )
        if settle:
            frame.model.add_support_settlement(frame.case_id, b.id, uz=Quantity.of(-0.002, "m"))
        return frame, a

    combined, a = build(True, True)
    parts = [build(True, False), build(False, True)]
    total = reaction(solve(combined), a)
    pieces = [reaction(solve(f), node) for f, node in parts]
    scale = max(abs(v) for v in total if v is not None)
    for index, value in enumerate(total):
        assert value is not None
        close(value, sum(p[index] or 0.0 for p in pieces), scale)


@pytest.mark.solver
def test_released_end_on_orphan_node_is_not_a_mechanism() -> None:
    """Rz liberado en J sobre un nudo articulado solo con esa barra: el giro huérfano se fija
    (R4) y la carga de barra no lo carga (el momento condensado en J es nulo)."""
    frame = Frame()
    a, b = point(frame, 0.0, 0.0, 0.0), point(frame, 4.0, 0.0, 0.0)
    member = frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.support(b, pinned_restraints())
    frame.model.set_member_end(member.id, MemberEnd.J, released(Dof.RZ))
    frame.model.add_member_point_load(frame.case_id, member.id, position=0.25, fy=n(-10.0))
    translation = translate_spatial(frame.model, config(frame))
    assert translation.request.orphan_fixes
    assert translation.orphan_loaded == ()
    p_i, p_j = ends(solve(frame), member)
    # Apuntalada (P = 10, a = 1, b = 3, L = 4): R_J = P·a²(3L − a)/(2L³) y
    # M_I = P·a·b(L + b)/(2L²).
    close(p_j[MZ], 0.0, 10.0)
    close(p_i[FY], 10.0 - 10.0 * 11.0 / 128.0, 10.0)
    moment_i = p_i[MZ]
    assert moment_i is not None
    close(abs(moment_i), 10.0 * 3.0 * 7.0 / 32.0, 10.0)


def test_orphan_rule_uses_tol_axis_for_member_end_moments() -> None:
    """E2.2-b: una componente ≤ tol_axis·‖m‖ en el eje huérfano no lo carga; mayor, sí."""
    frame = Frame()
    a, b = point(frame, 0.0, 0.0, 0.0), point(frame, 4.0, 0.0, 0.0)
    member = frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.support(b, pinned_restraints())
    frame.model.set_member_end(member.id, MemberEnd.J, released(Dof.RZ))
    frame.model.add_member_point_load(frame.case_id, member.id, position=0.25, fy=n(-10.0))
    request = translate_spatial(frame.model, config(frame)).request
    node_b = request.beams[0].node_j
    for z, loaded in ((1e-12, False), (1e-6, True)):  # Con ‖m‖ ≈ 1 N·m (My, rígido).
        loads = tuple(
            SpatialMemberEndLoad(
                e.beam, e.node, (*e.values[:4], 1.0, z) if e.node == node_b else e.values
            )
            for e in request.member_end_loads
        )
        probe = dataclasses.replace(request, member_end_loads=loads, orphan_fixes=())
        _restrained, nodes = _orphan_fixes(probe, 1e-9)
        assert (node_b in nodes) is loaded


# --- D8 -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("connection", "kind", "values", "key"),
    [
        (spring(Dof.UX, 1e8), "point", {"fx": 1.0}, "fem3d.end_axial_with_axial_load"),
        (released(Dof.UX), "distributed", {"qx_end": 1.0}, "fem3d.end_axial_with_axial_load"),
        (spring(Dof.RX, 1e6), "point", {"mx": 1.0}, "fem3d.end_torsion_with_torsional_load"),
        (released(Dof.RX), "moment", {"mx_start": 1.0}, "fem3d.end_torsion_with_torsional_load"),
    ],
)
def test_series_end_rejects_axial_or_torsional_member_loads(
    connection: EndConnection, kind: str, values: dict[str, float], key: str
) -> None:
    frame = frame_with(kind, LoadBasis.LOCAL, **values)
    member = next(iter(frame.model.members))
    frame.model.set_member_end(member, MemberEnd.J, connection)
    with pytest.raises(InputValueError) as info:
        translate_spatial(frame.model, config(frame))
    assert info.value.code is D.UNSUPPORTED_CONFIGURATION
    assert info.value.diagnostic.message_key == key


@pytest.mark.parametrize("kind", ["point", "distributed"])
def test_series_end_accepts_transverse_member_loads(kind: str) -> None:
    values = {"fz": 1.0} if kind == "point" else {"qz_end": 1.0}
    frame = frame_with(kind, LoadBasis.LOCAL, **values)
    member = next(iter(frame.model.members))
    frame.model.set_member_end(
        member, MemberEnd.J, spring(Dof.UX, 1e8).with_dof(Dof.RX, DofState.RELEASED)
    )
    request = translate_spatial(frame.model, config(frame)).request
    assert len(request.member_end_loads) == 2


def frame_with(kind: str, basis: LoadBasis, **values: float) -> Frame:
    frame, _b, member = cantilever()
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


# --- Traductor y protocolo v5 -------------------------------------------------------------


def test_aux_end_receives_uncondensed_load() -> None:
    """Un extremo con resorte va a un aux: la carga equivalente va al aux (D7, hecho 7)."""
    frame = frame_with("point", LoadBasis.LOCAL, fz=1e3)
    member = next(iter(frame.model.members))
    frame.model.set_member_end(member, MemberEnd.J, spring(Dof.RY, 1e6))
    request = translate_spatial(frame.model, config(frame)).request
    (aux,) = request.aux_nodes
    beam = request.beams[0]
    assert [e.node for e in request.member_end_loads] == [beam.node_i, aux.tag]


def test_recovered_fixed_end_forces_zero_released_rows() -> None:
    """E2.2-c: p0_c recuperado (−T·g) con los GDL de ``-release*`` nulos exactos."""
    frame, _nodes, members, _other = loaded_whole(SUB_CASES["releasey_1_releasez_2"])
    request = translate_spatial(frame.model, config(frame)).request
    (p0,) = member_fixed_end_forces(request).values()
    assert p0[4] == 0.0  # Ry en I
    assert p0[11] == 0.0  # Rz en J
    assert any(abs(v) > 1.0 for v in p0)
    assert members


def test_protocol_v5_round_trip_and_validation() -> None:
    frame, _nodes, _members, _other = loaded_whole((None, spring(Dof.RY, 5e6)))
    request = translate_spatial(frame.model, config(frame)).request
    assert PROTOCOL_VERSION == 5
    assert SpatialRequest.from_json(request.to_json()) == request
    data = json.loads(request.to_json())
    assert data["version"] == 5
    with pytest.raises(ProtocolError):
        SpatialRequest.from_json(json.dumps({**data, "version": 4}))
    first = request.member_end_loads[0]
    bad: list[tuple[SpatialMemberEndLoad, type[Exception]]] = [
        (SpatialMemberEndLoad(99, first.node, first.values), ProtocolError),
        (SpatialMemberEndLoad(first.beam, 99, first.values), ProtocolError),
        (SpatialMemberEndLoad(first.beam, first.node, (float("nan"),) * 6), NonFiniteValueError),
    ]
    for load, error in bad:
        with pytest.raises(error):
            dataclasses.replace(request, member_end_loads=(load,))
    with pytest.raises(ProtocolError):
        dataclasses.replace(request, member_end_loads=(first, first))


def _numeric(response: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in response.items() if k not in ("version", "versions")}


@pytest.mark.solver
@pytest.mark.parametrize("case_id", sorted(SPATIAL_SPECS))
def test_responses_without_member_loads_match_v4(case_id: str) -> None:
    """Bit a bit: worker v4 congelado con la petición v4 y worker v5 con la misma en v5."""
    for spec in SPATIAL_SPECS[case_id]:
        case = _Case(spec.category)
        spec.build(case)
        request = translate_spatial(case.model, SpatialAnalysisConfiguration(case.load_case.id))
        assert request.request.member_end_loads == ()
        new_request = request.request.to_dict()
        old_request = {**new_request, "version": 4}
        del old_request["member_end_loads"]
        old = run_worker(json.dumps(old_request), 60.0, WORKER_V4)
        new = run_worker(json.dumps(new_request), 60.0, WORKER_3D_PATH)
        assert old.response_text is not None, old.stderr
        assert new.response_text is not None, new.stderr
        before, after = json.loads(old.response_text), json.loads(new.response_text)
        assert (before["version"], after["version"]) == (4, 5)
        assert before["status"] == after["status"] == "OK", after["message"]
        assert _numeric(after) == _numeric(before)
