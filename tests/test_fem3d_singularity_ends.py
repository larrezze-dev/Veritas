"""Salvaguarda espacial con extremos no rígidos (plan T-018, pieza E2.3; R1, R3, R4, E2-g, O3).

k condensada en los GDL de ``-release*`` con su p0, A y J efectivos, aux como restricciones
relativas exactas aux−nudo, igualdad bit a bit en las peticiones rígidas, batería de
mecanismos con el modo (etiquetas de extremo, E2.3-a) y márgenes de ``tol_pivot``.

Contraste E2.3-c: la solución de la K reducida (K_red·q = Tᵀ·F, ``fem3d_support``) frente a
la referencia independiente ``EndModel.reference`` (condensación con GDL extra, Python puro)
y, con la marca ``solver``, frente al worker, en nudos de dominio y aux. Los casos con
penalización solo se contrastan con la referencia: la salvaguarda la aplica de forma exacta.
"""

import dataclasses
import math
import time
import types
from collections.abc import Sequence

import pytest

from fem3d_support import reference_displacements
from fem_support import Frame
from test_fem3d_singularity import (
    LEGIT_MIN,
    NORMAL,
    TOL,
    _grid,
    _portal_with_self_weight,
    along,
    loaded_cantilever,
    request_of,
)
from test_fem3d_solver import LOAD, OBLIQUE, assert_disp, node, run
from test_fem3d_solver_ends import (
    END_OFFSET,
    ORIENTATIONS,
    RX,
    RY,
    RZ,
    UX,
    W_NO_AXIAL,
    EndModel,
    Extras,
    Orientation,
    W,
    _released,
    assert_force,
)
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.loads.loads import LoadBasis
from veritas.core.sections.rectangular import torsion_constant
from veritas.core.supports.support import (
    SupportBasis,
    SupportDof,
    build_restraints,
    fixed_restraints,
    pinned_restraints,
)
from veritas.core.units import Quantity
from veritas.fem import spatial
from veritas.fem.results.run import MechanismDof
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.protocol import Six, SpatialBeam, SpatialRequest
from veritas.fem.spatial.protocol import Vec3 as Triple
from veritas.fem.spatial.stiffness import (
    SpatialPivotReport,
    beam_fixed_end_forces,
    beam_matrices,
    check_singularity,
    condense,
    fixed_end_forces,
    local_beam_stiffness,
    reduced_coordinates,
)
from veritas.fem.spatial.translator import translate_spatial

TORSION = torsion_constant(0.2, 0.4)
HORIZONTAL_OBLIQUE = (1.2, -0.7, 0.0)  # Horizontal no alineada: ez = Z (ADR-001).
K_ROT = 3e7
K_TORSION = 2e6
K_AXIAL = 5e8
K_TRANS = 4e6
UY, UZ = 1, 2  # Índices locales de Uy y Uz en la 12×12.
MECHANISM_MAX = TOL / 100


def _rot(value: float) -> Quantity:
    return Quantity.of(value, "N*m/rad")


def _trans(value: float) -> Quantity:
    return Quantity.of(value, "N/m")


RIGID = EndConnection.rigid()


# --- k condensada y p0_c (protocolo, sin dominio) --------------------------------------


def _beam(request: SpatialRequest, tag: int) -> SpatialBeam:
    return next(beam for beam in request.beams if beam.tag == tag)


def _local_force(request: SpatialRequest, solution: dict[int, Six], tag: int = 1) -> list[float]:
    """localForce de la barra ``tag`` con la k y el p0 de la salvaguarda: k_c·T·u + p0_c."""
    beam = _beam(request, tag)
    loads = [load for load in request.beam_loads if load.beam == tag]
    matrices = beam_matrices(request, beam)
    u = [
        *solution.get(request.element_node(beam, "I"), (0.0,) * 6),
        *solution.get(request.element_node(beam, "J"), (0.0,) * 6),
    ]
    t, k = matrices.transformation, matrices.local
    d = [sum(t[r][c] * u[c] for c in range(12)) for r in range(12)]
    p0 = beam_fixed_end_forces(beam, loads[0], matrices.length) if loads else [0.0] * 12
    return [sum(k[r][c] * d[c] for c in range(12)) + p0[r] for r in range(12)]


@pytest.mark.parametrize("orientation", ORIENTATIONS)
@pytest.mark.parametrize("code", [1, 2, 3], ids=["I", "J", "ambos"])
@pytest.mark.parametrize("component", ["y", "z", "yz"])
def test_condensed_beam_matches_reference(
    orientation: Orientation, code: int, component: str
) -> None:
    """k_c y p0_c (hecho 14) frente a la condensación con GDL extra de ``EndModel``."""
    model = EndModel(*orientation)
    codes = {"release_y": code if "y" in component else 0}
    codes["release_z"] = code if "z" in component else 0
    released = _released(component, code)
    request = model.request(model.beam(**codes))
    solution = reference_displacements(request)
    ref = model.reference(dict.fromkeys(released))
    assert_disp(solution[2], ref.u, model.length)
    assert_force(_local_force(request, solution), ref.force, zero=released)


def test_condensed_rows_are_exactly_zero_and_symmetric() -> None:
    model = EndModel(OBLIQUE, 0.37)
    request = model.request(model.beam(release_y=3, release_z=2))
    k = beam_matrices(request, request.beams[0]).local
    for i in (4, 10, 11):
        assert k[i] == [0.0] * 12
        assert [row[i] for row in k] == [0.0] * 12
    assert all(k[i][j] == k[j][i] for i in range(12) for j in range(12))


@pytest.mark.parametrize("component", ["y", "z"])
def test_code_3_bending_block_is_exactly_zero(component: str) -> None:
    """Revisión de E2.3, B1: biela en el plano (código 3) → bloque de flexión nulo exacto
    (la eliminación deja ≈ 1e-11) y p0_c con los cortantes de biapoyada y momentos nulos."""
    model = EndModel(OBLIQUE, 0.37)
    if component == "y":
        field, block = "release_y", (2, 4, 8, 10)
    else:
        field, block = "release_z", (1, 5, 7, 11)
    beam = model.beam(**{field: 3})
    request = model.request(beam)
    k = beam_matrices(request, beam).local
    for i in block:
        assert k[i] == [0.0] * 12
        assert [row[i] for row in k] == [0.0] * 12
    load = request.beam_loads[0]
    p0 = fixed_end_forces(load, model.length)
    condensed = beam_fixed_end_forces(beam, load, model.length)
    for i in block:
        assert condensed[i] == (p0[i] if i % 6 < 3 else 0.0)


@pytest.mark.parametrize("orientation", ORIENTATIONS)
@pytest.mark.parametrize("component", ["y", "z"])
def test_propped_cantilever_closed_form(orientation: Orientation, component: str) -> None:
    """p0_c sola (J empotrado: sin GDL libres), como el hecho 14 con el worker: R_J = 3qL/8,
    M_I = qL²/8 con los signos de ADR-001 y momento liberado exactamente nulo."""
    q = 10e3
    w = (0.0, -q, 0.0) if component == "y" else (-q, 0.0, 0.0)
    model = EndModel(*orientation, w, propped=True)
    codes = {"release_y": 2} if component == "y" else {"release_z": 2}
    request = model.request(model.beam(**codes))
    assert not reduced_coordinates(request)
    force = _local_force(request, {})
    ell = model.length
    shear, moment, released = (8, 4, 10) if component == "y" else (7, 5, 11)
    sign = -1.0 if component == "y" else 1.0
    assert force[shear] == pytest.approx(3 * q * ell / 8, rel=1e-12)
    assert force[moment] == pytest.approx(sign * q * ell**2 / 8, rel=1e-12)
    assert force[released] == 0.0


@pytest.mark.parametrize("ends", ["J", "ambos"])
@pytest.mark.parametrize("which", ["axial", "torsión"])
def test_released_axial_and_torsion(ends: str, which: str) -> None:
    """Ux/Rx liberados: A = 0 / J = 0 en la barra (hecho 15). En un extremo la referencia es
    la condensación; en los dos, la 12×12 propia con A = 0 / J = 0 (R7)."""
    model = EndModel(OBLIQUE, 0.37, w=W_NO_AXIAL)
    index = UX if which == "axial" else RX
    if which == "axial":
        beam = model.beam(area=0.0, axial_released=True)
    else:
        beam = model.beam(torsion_constant=0.0, torsion_released=True)
    request = model.request(beam)
    if ends == "J":
        ref = model.reference({6 + index: None})
    elif which == "axial":
        ref = model.reference({}, area=0.0)
    else:
        ref = model.reference({}, torsion=0.0)
    assert_disp(reference_displacements(request)[2], ref.u, model.length)


# --- Igualdad bit a bit en las peticiones rígidas --------------------------------------


@pytest.mark.parametrize("build", [loaded_cantilever, _portal_with_self_weight, _grid])
def test_rigid_requests_are_bit_identical_to_e1(build: object) -> None:
    """Sin liberaciones ni aux, k y p0 son los de E1 (``==``) y no hay etiquetas de extremo."""
    request = request_of(build())  # type: ignore[operator]
    for beam in request.beams:
        matrices = beam_matrices(request, beam)
        k = local_beam_stiffness(beam, matrices.length)
        assert matrices.local == k
        assert condense(k, ()) == (k, None)
    beams = {beam.tag: beam for beam in request.beams}
    for load in request.beam_loads:
        length = beam_matrices(request, beams[load.beam]).length
        assert beam_fixed_end_forces(beams[load.beam], load, length) == fixed_end_forces(
            load, length
        )
    assert all(label.member is None for label, _ in reduced_coordinates(request))


# --- Contraste E2.3-c: modelos del dominio ---------------------------------------------


def _domain(
    direction: Triple,
    alpha: float,
    connections: dict[MemberEnd, EndConnection],
    w: Triple = W,
    *,
    chain: bool = False,
) -> tuple[SpatialRequest, EndModel, int, int]:
    """Montaje de ``test_fem3d_translate_solver``: (petición, referencia, tag de J, tag de
    B1). Los tags dependen de los UUID."""
    model = EndModel(direction, alpha, w=w, torsion=TORSION)
    frame = Frame()
    i, j, k = (frame.node(p[0], p[2], y=p[1]) for p in ((0.0, 0.0, 0.0), model.xj, model.xk))
    b1 = frame.member(i, j, alpha=alpha)
    b2 = frame.member(j, k)
    frame.support(i, fixed_restraints())
    frame.support(k, fixed_restraints())
    if chain:  # Traslación fija según NORMAL: cadena (hecho 6), C→N→aux (R3).
        basis = SupportBasis.inclined(NORMAL, (1.0, 0.0, 0.0))
        frame.support(j, build_restraints((SupportDof.UX,)), basis)
    names = ("fx", "fy", "fz", "mx", "my", "mz")
    frame.nodal(j, **dict(zip(names, LOAD, strict=True)))
    wy, wz, wx = w
    frame.model.add_member_uniform_load(
        frame.case_id,
        b1.id,
        basis=LoadBasis.LOCAL,
        qx=Quantity.of(wx, "N/m"),
        qy=Quantity.of(wy, "N/m"),
        qz=Quantity.of(wz, "N/m"),
    )
    for end, connection in connections.items():
        frame.model.set_member_end(b1.id, end, connection)
    request = translate_spatial(frame.model, SpatialAnalysisConfiguration(frame.case_id)).request
    assert bool(request.chains) is chain
    beams = {beam.entity_id: beam for beam in request.beams}
    reference = EndModel(direction, alpha, w=w, torsion=TORSION, b2_vecxz=beams[str(b2.id)].vecxz)
    tag_j = next(n.tag for n in request.nodes if n.entity_id == str(j.id))
    return request, reference, tag_j, beams[str(b1.id)].tag


PINNED = EndConnection.pinned()
EXACT = [
    pytest.param(OBLIQUE, 0.37, {MemberEnd.J: PINNED}, {6 + RY: None, 6 + RZ: None}, W, id="J art"),
    pytest.param(
        OBLIQUE,
        0.37,
        {MemberEnd.I: PINNED, MemberEnd.J: PINNED},
        {RY: None, RZ: None, 6 + RY: None, 6 + RZ: None},
        W,
        id="I y J art",
    ),
    pytest.param(
        HORIZONTAL_OBLIQUE,
        0.0,
        {MemberEnd.J: RIGID.with_dof(Dof.RZ, DofState.SPRING, _rot(K_ROT))},
        {6 + RZ: K_ROT},
        W,
        id="Rz resorte coordenado",
    ),
    pytest.param(
        OBLIQUE,
        0.37,
        {MemberEnd.J: RIGID.with_dof(Dof.RX, DofState.SPRING, _rot(K_TORSION))},
        {6 + RX: K_TORSION},
        W,
        id="Rx resorte serie",
    ),
    pytest.param(
        OBLIQUE,
        0.37,
        {MemberEnd.J: RIGID.with_dof(Dof.RX, DofState.RELEASED)},
        {6 + RX: None},
        W,
        id="Rx liberado",
    ),
    pytest.param(
        OBLIQUE,
        0.37,
        {MemberEnd.J: RIGID.with_dof(Dof.UX, DofState.RELEASED)},
        {6 + UX: None},
        W_NO_AXIAL,
        id="Ux liberado",
    ),
    pytest.param(
        OBLIQUE,
        0.37,
        {MemberEnd.J: RIGID.with_dof(Dof.UX, DofState.SPRING, _trans(K_AXIAL))},
        {6 + UX: K_AXIAL},
        W_NO_AXIAL,
        id="Ux resorte serie",
    ),
    pytest.param(
        (1.0, 0.0, 0.0),
        0.0,
        {MemberEnd.J: RIGID.with_dof(Dof.UY, DofState.RELEASED)},
        {6 + UY: None},
        W,
        id="Uy libre alineado",
    ),
    pytest.param(
        (1.0, 0.0, 0.0),
        0.0,
        {MemberEnd.J: RIGID.with_dof(Dof.UZ, DofState.SPRING, _trans(K_TRANS))},
        {6 + UZ: K_TRANS},
        W,
        id="Uz resorte alineado",
    ),
    pytest.param(
        (1.0, 0.0, 0.0),
        0.0,
        {
            MemberEnd.J: RIGID.with_dof(Dof.RY, DofState.RELEASED).with_dof(
                Dof.RZ, DofState.SPRING, _rot(K_ROT)
            )
        },
        {6 + RY: None, 6 + RZ: K_ROT},
        W,
        id="R3 Ry libre y Rz resorte",
    ),
    pytest.param(
        (1.0, 0.0, 0.0),
        0.0,
        {
            MemberEnd.J: RIGID.with_dof(Dof.UY, DofState.RELEASED).with_dof(
                Dof.UZ, DofState.SPRING, _trans(K_TRANS)
            )
        },
        {6 + UY: None, 6 + UZ: K_TRANS},
        W,
        id="Uy libre y Uz resorte coordenado",
    ),
    pytest.param(
        (1.0, 0.0, 0.0),
        0.0,
        {MemberEnd.I: RIGID.with_dof(Dof.UY, DofState.RELEASED)},
        {UY: None},
        W,
        id="Uy libre en I",
    ),
    pytest.param(
        OBLIQUE,
        0.37,
        {MemberEnd.I: RIGID.with_dof(Dof.RX, DofState.RELEASED)},
        {RX: None},
        W,
        id="Rx liberado en I",
    ),
]
PENALTY = [
    pytest.param(
        OBLIQUE,
        0.37,
        {MemberEnd.J: RIGID.with_dof(Dof.UY, DofState.SPRING, _trans(K_TRANS))},
        {6 + UY: K_TRANS},
        W,
        id="Uy resorte oblicuo",
    ),
    pytest.param(
        OBLIQUE,
        0.37,
        {MemberEnd.J: RIGID.with_dof(Dof.RZ, DofState.SPRING, _rot(K_ROT))},
        {6 + RZ: K_ROT},
        W,
        id="Rz resorte oblicuo",
    ),
    pytest.param(
        OBLIQUE,
        0.37,
        {MemberEnd.J: RIGID.with_dof(Dof.UZ, DofState.RELEASED)},
        {6 + UZ: None},
        W,
        id="Uz libre oblicuo",
    ),
]
CASE_ARGS = ("direction", "alpha", "connections", "extras", "w")


def _element_released(beam: SpatialBeam, end: str) -> list[int]:
    """Índices locales de ``-release*`` en ``end``: internos al elemento, atados aux−nudo
    (R3)."""
    bit = 1 if end == "I" else 2
    return [
        END_OFFSET[end] + local
        for code, local in ((beam.release_y, RY), (beam.release_z, RZ))
        if code & bit
    ]


@pytest.mark.parametrize(CASE_ARGS, [*EXACT, *PENALTY])
def test_reduced_stiffness_matches_reference(
    direction: Triple,
    alpha: float,
    connections: dict[MemberEnd, EndConnection],
    extras: Extras,
    w: Triple,
) -> None:
    """E2.3-c sin OpenSees: la K reducida reproduce la condensación exacta en J, en el aux
    y en el localForce de B1 (k_c, A/J efectivos, aux y p0_c)."""
    request, model, tag_j, tag_b1 = _domain(direction, alpha, connections, w)
    solution = reference_displacements(request, aux=True)
    ref = model.reference(extras)
    assert_disp(solution[tag_j], ref.u, model.length)
    for aux in request.aux_nodes:
        released_at = _element_released(_beam(request, tag_b1), aux.end)
        expected = ref.end_global(aux.end, released_at)
        assert_disp(solution[aux.tag], expected, model.length)
    released = [i for i, k in extras.items() if k is None and i % 6 >= 3]
    assert_force(_local_force(request, solution, tag_b1), ref.force, zero=released)


@pytest.mark.solver
@pytest.mark.parametrize(CASE_ARGS, EXACT)
def test_reduced_stiffness_matches_worker(
    direction: Triple,
    alpha: float,
    connections: dict[MemberEnd, EndConnection],
    extras: Extras,
    w: Triple,
) -> None:
    """E2.3-c: u = T·q de la salvaguarda frente al worker en 1e-9, nudos de dominio y aux."""
    request, model, _, _ = _domain(direction, alpha, connections, w)
    solution = reference_displacements(request, aux=True)
    response = run(request)
    for current in request.nodes:
        if any(f.node == current.tag and all(f.fixity) for f in request.fixes):
            continue
        assert_disp(node(response, current.tag)[0], solution[current.tag], model.length)
    aux_results = {tag: disp for tag, disp, _ in response.aux_results}
    assert set(aux_results) == {aux.tag for aux in request.aux_nodes}
    for tag, disp in aux_results.items():
        assert_disp(disp, solution[tag], model.length)


CHAIN_EXTRAS: Extras = {6 + UY: None}
CHAIN_END = {MemberEnd.J: RIGID.with_dof(Dof.UY, DofState.RELEASED)}


def test_chain_node_aux_matches_reference() -> None:
    """R3 (revisión de E2.3, M2): cadena en J y aux en J (C→N→aux); la réplica de las
    columnas tangentes del nudo en el aux es exacta."""
    request, model, tag_j, tag_b1 = _domain((1.0, 0.0, 0.0), 0.0, CHAIN_END, chain=True)
    solution = reference_displacements(request, aux=True)
    ref = model.reference(CHAIN_EXTRAS, normals=(NORMAL,))
    assert_disp(solution[tag_j], ref.u, model.length)
    (aux,) = request.aux_nodes
    assert_disp(solution[aux.tag], ref.end_global("J"), model.length)
    assert_force(_local_force(request, solution, tag_b1), ref.force)


@pytest.mark.solver
def test_chain_node_aux_matches_worker() -> None:
    request, model, tag_j, _ = _domain((1.0, 0.0, 0.0), 0.0, CHAIN_END, chain=True)
    solution = reference_displacements(request, aux=True)
    response = run(request)
    assert_disp(node(response, tag_j)[0], solution[tag_j], model.length)
    (aux,) = request.aux_nodes
    (result,) = [disp for tag, disp, _ in response.aux_results if tag == aux.tag]
    assert_disp(result, solution[aux.tag], model.length)


# --- Batería de mecanismos con extremos ------------------------------------------------


def _end(frame: Frame, member_index: int, end: MemberEnd, connection: EndConnection) -> None:
    member_id = list(frame.model.members)[member_index]
    frame.model.set_member_end(member_id, end, connection)


def _bar(direction: Sequence[float], alpha: float = 0.37) -> tuple[Frame, object, object]:
    frame = Frame()
    a = frame.node(0.0, 0.0)
    b = along(frame, a, direction, 3.0)
    frame.member(a, b, alpha=alpha)
    return frame, a, b


def _oblique_orphan() -> Frame:
    """J articulado sobre un apoyo articulado: giro sin rigidez ⊥ ex, oblicuo (E2.3-b)."""
    frame, a, b = _bar(OBLIQUE)
    frame.support(a, fixed_restraints())  # type: ignore[arg-type]
    frame.support(b, pinned_restraints())  # type: ignore[arg-type]
    _end(frame, 0, MemberEnd.J, PINNED)
    frame.nodal(b, fz=-1000.0)  # type: ignore[arg-type]
    return frame


def _torsion_released() -> Frame:
    """Rx liberado en los dos extremos de una barra oblicua biarticulada: J_ef = 0 (R1)."""
    frame, a, b = _bar(OBLIQUE)
    for node_ in (a, b):
        frame.support(node_, pinned_restraints())  # type: ignore[arg-type]
    for end in MemberEnd:
        _end(frame, 0, end, RIGID.with_dof(Dof.RX, DofState.RELEASED))
    frame.nodal(b, fz=-1000.0)  # type: ignore[arg-type]
    return frame


def _axial_released() -> Frame:
    """Ux liberado en los dos extremos de una ménsula oblicua: A_ef = 0."""
    frame, a, b = _bar(OBLIQUE)
    frame.support(a, fixed_restraints())  # type: ignore[arg-type]
    for end in MemberEnd:
        _end(frame, 0, end, RIGID.with_dof(Dof.UX, DofState.RELEASED))
    frame.nodal(b, fz=-1000.0)  # type: ignore[arg-type]
    return frame


def _translations_released() -> Frame:
    """R4: Ux, Uy y Uz liberados en el único extremo de un nudo sin apoyo (A_ef = 0)."""
    frame, a, b = _bar((1.0, 0.0, 0.0), alpha=0.0)
    frame.support(a, fixed_restraints())  # type: ignore[arg-type]
    connection = RIGID
    for dof in (Dof.UX, Dof.UY, Dof.UZ):
        connection = connection.with_dof(dof, DofState.RELEASED)
    _end(frame, 0, MemberEnd.J, connection)
    frame.nodal(b, fz=-1000.0)  # type: ignore[arg-type]
    return frame


def _pinned_truss() -> Frame:
    """E2-g: trípode de barras articuladas con apoyos sin giros fijos."""
    frame = Frame()
    apex = frame.node(0.3, 4.0, y=0.2)
    for x, y in ((3.0, 0.0), (-1.5, 2.6), (-1.5, -2.6)):
        base = frame.node(x, 0.0, y=y)
        frame.member(base, apex)
        frame.support(base, pinned_restraints())
    for index in range(3):
        for end in MemberEnd:
            _end(frame, index, end, PINNED)
    frame.nodal(apex, fz=-1000.0, fx=300.0)
    return frame


def _hanging_strut() -> Frame:
    """Revisión de E2.3, B1: biela en +X empotrada en a y libre en b: Uy y Uz de b sin
    rigidez (el bloque transversal debe ser nulo exacto, no ruido)."""
    frame, a, b = _bar((1.0, 0.0, 0.0), alpha=0.0)
    frame.support(a, fixed_restraints())  # type: ignore[arg-type]
    for end in MemberEnd:
        _end(frame, 0, end, PINNED)
    frame.nodal(b, fx=1000.0)  # type: ignore[arg-type]
    return frame


def _planar_truss() -> Frame:
    """Revisión de E2.3, B1: celosía triangular en XZ, articulada, sin coacción en Y."""
    frame = Frame()
    a, b, c = frame.node(0.0, 0.0), frame.node(4.0, 0.0), frame.node(2.0, 3.0)
    for start, stop in ((a, b), (b, c), (a, c)):
        frame.member(start, stop)
    for index in range(3):
        for end in MemberEnd:
            _end(frame, index, end, PINNED)
    frame.support(a, pinned_restraints())
    frame.support(b, pinned_restraints())
    frame.nodal(c, fz=-1000.0, fx=200.0)
    return frame


def _o3() -> Frame:
    """O3: Rz liberado en I y Uy en J; J articulado sin otra barra: giro ez sin rigidez."""
    frame, a, b = _bar((1.0, 0.0, 0.0), alpha=0.0)
    frame.support(a, fixed_restraints())  # type: ignore[arg-type]
    frame.support(b, pinned_restraints())  # type: ignore[arg-type]
    _end(frame, 0, MemberEnd.I, RIGID.with_dof(Dof.RZ, DofState.RELEASED))
    _end(frame, 0, MemberEnd.J, RIGID.with_dof(Dof.UY, DofState.RELEASED))
    frame.nodal(b, fz=-1000.0)  # type: ignore[arg-type]
    return frame


def _involved(report: SpatialPivotReport) -> set[tuple[str, str | None]]:
    """(componente, extremo) con participación apreciable."""
    assert report.mode is not None
    return {
        (label.component, label.end)
        for label, p in zip(report.labels, report.mode.participation, strict=True)
        if p > 1e-3
    }


ROTATIONS = {("Rx", None), ("Ry", None), ("Rz", None)}
TRANSLATIONS = {("Ux", None), ("Uy", None), ("Uz", None)}
MECHANISMS = [
    ("huérfano oblicuo", _oblique_orphan, 2, ROTATIONS),
    ("Rx liberado en I y J", _torsion_released, 2, ROTATIONS),
    ("Ux liberado en I y J", _axial_released, 1, TRANSLATIONS),
    ("traslaciones liberadas (R4)", _translations_released, 3, None),
    ("celosía biarticulada (E2-g)", _pinned_truss, 9, None),
    ("biela colgada en +X", _hanging_strut, 2, {("Uy", None), ("Uz", None)}),
    # Uy del vértice y, como E2-g, 6 giros en el plano atados solo por 3 torsiones: 1 + 3.
    ("celosía plana sin coacción en Y", _planar_truss, 4, None),
    ("Rz en I y Uy en J (O3)", _o3, 1, {("Rz", None), ("Uy", "J")}),
]


@pytest.mark.parametrize(
    ("name", "build", "nullity", "components"), MECHANISMS, ids=[m[0] for m in MECHANISMS]
)
def test_end_mechanisms_are_singular_with_conclusive_mode(
    name: str, build: object, nullity: int | None, components: set[tuple[str, str | None]] | None
) -> None:
    report = check_singularity(request_of(build()), TOL)  # type: ignore[operator]
    assert report.singular, name
    assert report.min_pivot is not None
    assert report.min_pivot <= MECHANISM_MAX, report.min_pivot
    assert report.mode is not None
    assert report.mode.conclusive
    if nullity is not None:
        assert report.mode.nullity == nullity
    if components is not None:
        assert _involved(report) == components


def test_released_translations_mode_names_the_aux_end() -> None:
    """R4: el modo incluye las traslaciones propias del aux, con barra y extremo (E2.3-a)."""
    request = request_of(_translations_released())
    report = check_singularity(request, TOL)
    (aux,) = request.aux_nodes
    own = [label for label in report.labels if label.member is not None]
    assert [(label.component, label.member, label.end) for label in own] == [
        ("Uy", aux.beam, "J"),
        ("Uz", aux.beam, "J"),
    ]
    assert all(label.node == aux.master for label in own)
    assert {("Ux", None), ("Uy", None), ("Uz", None)} <= _involved(report)


def test_truss_mode_contains_only_rotations() -> None:
    """E2-g: el giro de los nudos es el único modo; las traslaciones del trípode son firmes."""
    report = check_singularity(request_of(_pinned_truss()), TOL)
    assert report.mode is not None
    assert {component for component, _ in _involved(report)} <= {"Rx", "Ry", "Rz"}


@pytest.mark.parametrize("build", [m[1] for m in MECHANISMS], ids=[m[0] for m in MECHANISMS])
def test_end_mode_is_reproducible_over_20_rebuilds(build: object) -> None:
    frame = build()  # type: ignore[operator]
    first = check_singularity(request_of(frame), TOL)
    for _ in range(20):
        report = check_singularity(request_of(frame), TOL)
        assert report.labels == first.labels
        assert report.mode == first.mode


# --- Estructuras legítimas con extremos: margen frente a tol_pivot ---------------------


def _pinned_tripod() -> Frame:
    """Trípode articulado con bases empotradas: sin huérfanos (R8)."""
    frame = Frame()
    apex = frame.node(0.3, 4.0, y=0.2)
    for x, y in ((3.0, 0.0), (-1.5, 2.6), (-1.5, -2.6)):
        base = frame.node(x, 0.0, y=y)
        frame.member(base, apex)
        frame.support(base, fixed_restraints())
    for index in range(3):
        for end in MemberEnd:
            _end(frame, index, end, PINNED)
    frame.nodal(apex, fz=-1000.0, fx=300.0)
    return frame


def _spring_cantilever(dof: Dof, value: Quantity) -> Frame:
    frame, a, b = _bar(OBLIQUE)
    frame.support(a, fixed_restraints())  # type: ignore[arg-type]
    _end(frame, 0, MemberEnd.J, RIGID.with_dof(dof, DofState.SPRING, value))
    frame.nodal(b, fz=-1000.0, fy=200.0)  # type: ignore[arg-type]
    return frame


def _grid_with_ends() -> Frame:
    """Pórtico 4×5×2 con vigas articuladas en J y Rz en resorte en I (aux): unos 300 GDL
    reducidos con aux."""
    frame = _grid(4, 5, 2)
    model = frame.model
    for index, member in enumerate(model.members.values()):
        if member.end_i.is_rigid and index % 3:
            model.set_member_end(member.id, MemberEnd.J, PINNED)
            model.set_member_end(
                member.id, MemberEnd.I, RIGID.with_dof(Dof.RZ, DofState.SPRING, _rot(K_ROT))
            )
    return frame


LEGITIMATE = [
    ("trípode articulado", _pinned_tripod),
    ("ménsula oblicua con Rz en resorte", lambda: _spring_cantilever(Dof.RZ, _rot(K_ROT))),
    ("ménsula oblicua con Uy en resorte", lambda: _spring_cantilever(Dof.UY, _trans(K_TRANS))),
    ("pórtico con extremos", _grid_with_ends),
]


@pytest.mark.parametrize(("name", "build"), LEGITIMATE, ids=[n for n, _ in LEGITIMATE])
def test_legitimate_end_structures_pass_with_margin(name: str, build: object) -> None:
    request = request_of(build())  # type: ignore[operator]
    assert request.aux_nodes or any(b.release_y or b.release_z for b in request.beams), name
    report = check_singularity(request, TOL)
    assert not report.singular, name
    assert report.min_pivot is not None
    assert report.min_pivot >= LEGIT_MIN, report.min_pivot


def test_safeguard_time_with_aux() -> None:
    """Unos 300 GDL reducidos con aux; cota holgada como en E1 (D6)."""
    request = request_of(_grid_with_ends())
    start = time.perf_counter()
    report = check_singularity(request, TOL)
    assert report.free_dofs >= 300
    assert len(request.aux_nodes) > 0
    assert time.perf_counter() - start < 30.0
    assert math.isfinite(report.min_pivot or 0.0)


# --- E2.3-b: la clave oblicua solo si domina un giro huérfano (revisión de E2.3, M3) -----


@pytest.mark.parametrize(
    ("component", "key"), [("Rx", "fem3d.orphan_rotation_oblique"), ("Ux", "fem.singular")]
)
def test_oblique_key_requires_a_dominant_orphan_rotation(component: str, key: str) -> None:
    """Con un huérfano oblicuo en el modelo, si el participante principal del modo no es un
    giro de ese nudo, la causa puede ser otra: ``fem.singular``."""
    frame = _oblique_orphan()
    config = SpatialAnalysisConfiguration(frame.case_id)
    request = request_of(frame)
    report = check_singularity(request, TOL)
    pivot = spatial._pivot_summary(frame.model, request, report)
    (orphan_node,) = {item.node_id for item in pivot.mechanism}
    forced = dataclasses.replace(pivot, mechanism=(MechanismDof(orphan_node, component, 1.0),))
    run = types.SimpleNamespace(pivot=forced, warnings=[])
    failure = spatial._singular_outcome(run, frame.model, request, report, config)  # type: ignore[arg-type]
    assert failure.key == key
