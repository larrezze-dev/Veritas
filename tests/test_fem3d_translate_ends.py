"""Traductor espacial con extremos no rígidos (plan T-018, pieza E2.2: D5-A, E2-a, E2-d, R1-R4).

Representación por componente, serie de Rx/Ux, rechazo de Wx con Ux no rígido, aux exacto,
coordenado y con penalización, tags, avisos y giros huérfanos como subespacio.
"""

import math

import pytest

from fem_support import AREA, E_PA, IY, IZ, Frame, inclined
from test_fem3d_translate import G_PA, OBLIQUE, J, cantilever, rejected, translate
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.loads.loads import LoadBasis
from veritas.core.supports.support import SupportDof, build_restraints, fixed_restraints
from veritas.core.units import Quantity
from veritas.fem.spatial.protocol import SpatialRequest
from veritas.fem.spatial.stiffness import solver_free_dofs
from veritas.fem.spatial.translator import (
    HYPOTHESIS_END_LINKS,
    HYPOTHESIS_END_PENALTY,
    HYPOTHESIS_END_RELEASES,
    HYPOTHESIS_END_SERIES,
    HYPOTHESIS_ORPHAN,
    HYPOTHESIS_PENALTY,
    SpatialTranslation,
    orphan_rotations,
)

ROT_UNIT = "N*m/rad"
TRANS_UNIT = "N/m"
END_PENALTY_FACTOR = 1e1  # ADR-004 §2: los extremos no usan penalty_factor (D4).
HORIZONTAL_OBLIQUE = (1.2, -0.7, 0.0)  # Horizontal no alineada: ez = Z (ADR-001).

type Tip = tuple[float, float, float]


def _set_end(frame: Frame, end: MemberEnd, connection: EndConnection, index: int = 0) -> None:
    member_id = list(frame.model.members)[index]
    frame.model.set_member_end(member_id, end, connection)


def _spring(dof: Dof, value: float) -> EndConnection:
    unit = TRANS_UNIT if dof.is_translation else ROT_UNIT
    return EndConnection.rigid().with_dof(dof, DofState.SPRING, Quantity.of(value, unit))


def _released(*dofs: Dof) -> EndConnection:
    connection = EndConnection.rigid()
    for dof in dofs:
        connection = connection.with_dof(dof, DofState.RELEASED)
    return connection


def _tip(request: SpatialRequest) -> int:
    return next(n.tag for n in request.nodes if (n.x, n.y, n.z) != (0.0, 0.0, 0.0))


def _cantilever_with(
    connection: EndConnection,
    end: MemberEnd = MemberEnd.J,
    tip: Tip = (2.0, 0.0, 0.0),
    alpha: float | None = None,
) -> SpatialTranslation:
    frame = cantilever(Frame(), tip, alpha=alpha)
    _set_end(frame, end, connection)
    return translate(frame)


# --- Liberaciones de flexión (hecho 14) ---------------------------------------------------


@pytest.mark.parametrize(
    ("ends", "code"),
    [((MemberEnd.I,), 1), ((MemberEnd.J,), 2), ((MemberEnd.I, MemberEnd.J), 3)],
    ids=["I", "J", "ambos"],
)
@pytest.mark.parametrize("dof", [Dof.RY, Dof.RZ])
def test_released_bending_uses_element_release_codes(
    ends: tuple[MemberEnd, ...], code: int, dof: Dof
) -> None:
    frame = cantilever(Frame(), OBLIQUE, alpha=0.37)
    for end in ends:
        _set_end(frame, end, _released(dof))
    translation = translate(frame)
    (beam,) = translation.request.beams
    assert (beam.release_y, beam.release_z) == ((code, 0) if dof is Dof.RY else (0, code))
    assert (beam.area, beam.torsion_constant) == (AREA, J)  # Sin serie.
    assert not translation.request.aux_nodes  # Ry/Rz liberados nunca usan aux (E2-a).
    assert HYPOTHESIS_END_RELEASES in translation.hypotheses


def test_pinned_preset_releases_both_bending_rotations() -> None:
    frame = cantilever(Frame(), OBLIQUE, alpha=0.37)
    for end in MemberEnd:
        _set_end(frame, end, EndConnection.pinned())
    (beam,) = translate(frame).request.beams
    assert (beam.release_y, beam.release_z) == (3, 3)
    assert (beam.axial_released, beam.torsion_released) == (False, False)


# --- Rx y Ux en serie (hechos 5 y 15) -------------------------------------------------------


def _series(rigidity: float, length: float, *springs: float) -> float:
    return length / (length / rigidity + sum(1.0 / k for k in springs))


@pytest.mark.parametrize(
    ("dof", "springs"),
    [(Dof.RX, (2e6,)), (Dof.RX, (2e6, 5e6)), (Dof.UX, (5e8,)), (Dof.UX, (5e8, 1e9))],
)
def test_series_springs_give_effective_properties(dof: Dof, springs: tuple[float, ...]) -> None:
    frame = cantilever(Frame())
    for end, k in zip(MemberEnd, springs, strict=False):
        _set_end(frame, end, _spring(dof, k))
    translation = translate(frame)
    (beam,) = translation.request.beams
    length = 2.0
    if dof is Dof.RX:
        assert beam.torsion_constant == pytest.approx(
            _series(G_PA * J, length, *springs) / G_PA, rel=1e-14
        )
        assert beam.area == AREA
    else:
        assert beam.area == pytest.approx(_series(E_PA * AREA, length, *springs) / E_PA, rel=1e-14)
        assert beam.torsion_constant == J
    assert (beam.axial_released, beam.torsion_released) == (False, False)
    assert not translation.request.aux_nodes
    assert HYPOTHESIS_END_SERIES in translation.hypotheses


@pytest.mark.parametrize("dof", [Dof.RX, Dof.UX])
def test_released_series_component_is_zero_with_its_flag(dof: Dof) -> None:
    """Liberado en un extremo y con resorte en el otro: 0 y marca (E2-b)."""
    frame = cantilever(Frame())
    _set_end(frame, MemberEnd.I, _released(dof))
    _set_end(frame, MemberEnd.J, _spring(dof, 1e7))
    (beam,) = translate(frame).request.beams
    if dof is Dof.RX:
        assert (beam.torsion_constant, beam.torsion_released) == (0.0, True)
    else:
        assert (beam.area, beam.axial_released) == (0.0, True)


# --- E2-d: Ux no rígido exige Wx = 0 -------------------------------------------------------------


def _axial_frame(tip: tuple[float, float, float], connection: EndConnection) -> Frame:
    frame = cantilever(Frame(), tip)
    _set_end(frame, MemberEnd.J, connection)
    return frame


@pytest.mark.parametrize(
    "connection", [_released(Dof.UX), _spring(Dof.UX, 5e8)], ids=["libre", "resorte"]
)
def test_axial_end_with_local_axial_load_is_rejected(connection: EndConnection) -> None:
    frame = _axial_frame((2.0, 0.0, 0.0), connection)
    (member_id,) = frame.model.members
    frame.model.add_member_uniform_load(
        frame.case_id, member_id, basis=LoadBasis.LOCAL, qx=Quantity.of(100.0, "N/m")
    )
    rejected(frame, "fem3d.end_axial_with_axial_load")


def test_axial_end_with_self_weight_of_an_inclined_bar_is_rejected() -> None:
    frame = _axial_frame((2.0, 0.0, 1.0), _released(Dof.UX))
    frame.model.set_self_weight(frame.case_id)
    rejected(frame, "fem3d.end_axial_with_axial_load")


@pytest.mark.parametrize(
    "tip", [(2.0, 0.0, 0.0), HORIZONTAL_OBLIQUE], ids=["+X", "horizontal oblicua"]
)
def test_axial_end_with_self_weight_of_a_horizontal_bar_is_admitted(tip: Tip) -> None:
    """R9: el redondeo de Wx en una barra horizontal queda bajo TOL_AXIAL_LOAD."""
    frame = _axial_frame(tip, _released(Dof.UX))
    frame.model.set_self_weight(frame.case_id)
    (beam,) = translate(frame).request.beams
    assert beam.axial_released


def test_axial_end_with_transverse_local_load_is_admitted() -> None:
    frame = _axial_frame((2.0, 0.0, 1.0), _spring(Dof.UX, 5e8))
    (member_id,) = frame.model.members
    frame.model.add_member_uniform_load(
        frame.case_id, member_id, basis=LoadBasis.LOCAL, qz=Quantity.of(-100.0, "N/m")
    )
    translate(frame)


# --- Aux (E2-a, R3) ------------------------------------------------------------------------------


def _aux_request(connection: EndConnection, tip: Tip, alpha: float | None = None) -> SpatialRequest:
    return _cantilever_with(connection, tip=tip, alpha=alpha).request


def test_aligned_rotation_spring_is_exact() -> None:
    """+X, alfa = 0: Rz con resorte → equalDOF en Ux…Ry y zeroLength exacto en Rz."""
    k = 3e7
    request = _aux_request(_spring(Dof.RZ, k), (2.0, 0.0, 0.0))
    (aux,) = request.aux_nodes
    (beam,) = request.beams
    assert (aux.master, aux.beam, aux.end) == (_tip(request), beam.tag, "J")
    assert request.element_node(beam, "J") == aux.tag
    (equal,) = request.end_equals
    assert (equal.aux, equal.dofs) == (aux.tag, (1, 2, 3, 4, 5))
    (spring,) = request.end_springs
    assert (spring.aux, spring.dof, spring.stiffness, spring.penalty) == (aux.tag, 6, k, False)


def test_horizontal_oblique_bar_with_rz_spring_is_coordinate() -> None:
    """E2-a: barra oblicua horizontal (ez = Z): equalDOF en Rx y Ry globales, exacto."""
    translation = _cantilever_with(_spring(Dof.RZ, 3e7), tip=HORIZONTAL_OBLIQUE)
    request = translation.request
    (equal,) = request.end_equals
    assert equal.dofs == (1, 2, 3, 4, 5)
    (spring,) = request.end_springs
    assert spring.dof == 6
    assert not spring.penalty
    ex = request.end_springs[0].axis_x
    assert ex == pytest.approx(
        tuple(c / math.hypot(*HORIZONTAL_OBLIQUE) for c in HORIZONTAL_OBLIQUE)
    )
    assert HYPOTHESIS_PENALTY not in translation.hypotheses
    assert HYPOTHESIS_END_LINKS in translation.hypotheses


def _scales(request: SpatialRequest, node: int) -> tuple[float, float]:
    rotation = translation = 0.0
    coordinates = {n.tag: (n.x, n.y, n.z) for n in request.nodes}
    for beam in request.beams:
        if node in (beam.node_i, beam.node_j):
            length = math.dist(coordinates[beam.node_i], coordinates[beam.node_j])
            rotation += max(4 * E_PA * IY, 4 * E_PA * IZ, G_PA * J) / length
            translation += max(E_PA * AREA / length, 12 * E_PA * max(IY, IZ) / length**3)
    return END_PENALTY_FACTOR * rotation, END_PENALTY_FACTOR * translation


def test_oblique_rotation_spring_uses_rotation_penalty() -> None:
    k = 3e7
    translation = _cantilever_with(_spring(Dof.RY, k), tip=OBLIQUE, alpha=0.37)
    request = translation.request
    rotation, _ = _scales(request, _tip(request))
    (equal,) = request.end_equals
    assert equal.dofs == (1, 2, 3)  # Traslaciones rígidas: equalDOF global exacto.
    springs = {s.dof: s for s in request.end_springs}
    assert sorted(springs) == [4, 5, 6]
    assert (springs[5].stiffness, springs[5].penalty) == (k, False)
    for dof in (4, 6):
        assert springs[dof].penalty
        assert springs[dof].stiffness == pytest.approx(rotation, rel=1e-14)
    assert HYPOTHESIS_END_PENALTY in translation.hypotheses
    assert HYPOTHESIS_PENALTY not in translation.hypotheses  # Solo apoyos D4 (ADR-004 §8).


def test_oblique_released_translation_uses_translation_penalty() -> None:
    request = _aux_request(_released(Dof.UY), OBLIQUE, 0.37)
    _, scale = _scales(request, _tip(request))
    (equal,) = request.end_equals
    assert equal.dofs == (4, 5, 6)
    springs = {s.dof: s for s in request.end_springs}
    assert sorted(springs) == [1, 3]  # Uy liberado: sin resorte.
    for spring in springs.values():
        assert spring.penalty
        assert spring.stiffness == pytest.approx(scale, rel=1e-14)


def test_aligned_released_transverse_translations_are_exact() -> None:
    request = _aux_request(_released(Dof.UY, Dof.UZ), (2.0, 0.0, 0.0))
    (equal,) = request.end_equals
    assert equal.dofs == (1, 4, 5, 6)
    assert not request.end_springs


def test_release_and_aux_on_the_same_end_tie_the_released_rotation() -> None:
    """R3: Ry liberado (elemento) y Rz con resorte (aux): el aux ata Ry rígido."""
    connection = _released(Dof.RY).with_dof(Dof.RZ, DofState.SPRING, Quantity.of(3e7, ROT_UNIT))
    request = _aux_request(connection, (2.0, 0.0, 0.0))
    (beam,) = request.beams
    assert (beam.release_y, beam.release_z) == (2, 0)
    (equal,) = request.end_equals
    assert equal.dofs == (1, 2, 3, 4, 5)
    assert [s.dof for s in request.end_springs] == [6]


def _two_bars_at_a_node() -> Frame:
    frame = Frame()
    a, b, c = frame.node(0, 0), frame.node(2, 0), frame.node(2, 0, y=2.0)
    frame.member(a, b)
    frame.member(b, c)
    frame.support(a, fixed_restraints())
    frame.support(c, fixed_restraints())
    frame.nodal(b, fz=-1000.0)
    return frame


def test_two_aux_on_a_node_are_each_tied_to_the_node_with_contiguous_tags() -> None:
    frame = _two_bars_at_a_node()
    for index in range(2):
        member = list(frame.model.members.values())[index]
        end = (
            MemberEnd.J
            if member.node_j
            == next(
                n.id
                for n in frame.model.nodes.values()
                if (n.position.x, n.position.y) == (2.0, 0.0)
            )
            else MemberEnd.I
        )
        _set_end(frame, end, _spring(Dof.RZ, 3e7), index)
    request = translate(frame).request
    middle = next(n.tag for n in request.nodes if (n.x, n.y, n.z) == (2.0, 0.0, 0.0))
    assert [a.master for a in request.aux_nodes] == [middle, middle]
    assert [a.beam for a in request.aux_nodes] == sorted(a.beam for a in request.aux_nodes)
    domain = [n.tag for n in request.nodes]
    assert [a.tag for a in request.aux_nodes] == [len(domain) + 1, len(domain) + 2]
    assert {e.aux for e in request.end_equals} == {a.tag for a in request.aux_nodes}
    elements = [s.tag for s in request.end_springs]
    assert elements == list(range(len(request.beams) + 1, len(request.beams) + 3))


def test_aux_tags_follow_support_auxiliaries_and_translation_is_deterministic() -> None:
    frame = cantilever(Frame(), (2.0, 0.0, 0.0))
    tip = next(n for n in frame.model.nodes.values() if n.position.x != 0.0)
    frame.support(
        tip, build_restraints((), {SupportDof.UZ: Quantity.of(1e6, TRANS_UNIT)}), inclined(30.0)
    )
    _set_end(frame, MemberEnd.J, _spring(Dof.RZ, 3e7))
    first, second = translate(frame), translate(frame)
    assert first.request.to_json() == second.request.to_json()
    request = first.request
    support_nodes = {s.ground for s in request.springs}
    (aux,) = request.aux_nodes
    assert aux.tag > max(support_nodes)
    assert min(s.tag for s in request.end_springs) > max(s.tag for s in request.springs)


NEAR_RIGID_REFERENCE = {  # Rigidez de referencia de cada GDL, L = 2 m (Uy con Iz, Uz con Iy).
    Dof.RY: E_PA * IY / 2.0,
    Dof.RZ: E_PA * IZ / 2.0,
    Dof.UY: 12 * E_PA * IZ / 8.0,
    Dof.UZ: 12 * E_PA * IY / 8.0,
}


@pytest.mark.parametrize("dof", list(NEAR_RIGID_REFERENCE))
@pytest.mark.parametrize(("ratio", "warned"), [(2e8, True), (5e7, False)])
def test_near_rigid_end_spring_warning(dof: Dof, ratio: float, warned: bool) -> None:
    """E2.2-c: con IY = 4·IZ, intercambiar las inercias cambia el resultado en un sentido u
    otro (2e8 → 5e7 o 5e7 → 2e8), así que cada caso distingue Iy de Iz (revisión, m2)."""
    translation = _cantilever_with(_spring(dof, ratio * NEAR_RIGID_REFERENCE[dof]))
    keys = [w.message_key for w in translation.warnings]
    assert ("fem3d.end_spring_near_rigid" in keys) is warned


def test_series_spring_never_warns_near_rigid() -> None:
    """Rx y Ux se pliegan en J_ef/A_ef: no hay elemento ni aviso."""
    translation = _cantilever_with(_spring(Dof.RX, 1e30))
    assert "fem3d.end_spring_near_rigid" not in [w.message_key for w in translation.warnings]


def _odd_section_frame(connection: EndConnection | None = None) -> Frame:
    """Barra inclinada con sección 0,13×0,37: (E·A)/E ≠ A en coma flotante (revisión, M1)."""
    frame = Frame()
    frame.section = frame.model.add_rectangular_section(
        b=Quantity.of(0.13, "m"), h=Quantity.of(0.37, "m")
    )
    a, b = frame.node(0, 0), frame.node(2.0, 1.0)
    member = frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.model.set_self_weight(frame.case_id)
    if connection is not None:
        frame.model.set_member_end(member.id, MemberEnd.J, connection)
    return frame


def test_rigid_bar_keeps_section_values_bit_for_bit() -> None:
    """M1: con los dos extremos rígidos A y J son los de la sección, sin serie ni E2-d."""
    frame = _odd_section_frame()
    props = frame.section.properties
    area, torsion = props.area.value, props.torsion_constant.value
    assert area is not None
    assert torsion is not None
    assert (E_PA * area) / E_PA != area  # El caso que fallaba.
    translation = translate(frame)
    (beam,) = translation.request.beams
    assert (beam.area, beam.torsion_constant) == (area, torsion)
    assert HYPOTHESIS_END_SERIES not in translation.hypotheses


def test_huge_axial_spring_is_still_a_series_end() -> None:
    """M1, hueco simétrico: un resorte Ux enorme sigue siendo SPRING (E2-d y serie)."""
    rejected(_odd_section_frame(_spring(Dof.UX, 1e300)), "fem3d.end_axial_with_axial_load")


# --- Giros huérfanos (R1, R4) --------------------------------------------------------------------


def test_coordinate_orphan_is_fixed_with_warning() -> None:
    """+X articulado en J: el nudo libre solo tiene rigidez de giro en ex → Ry, Rz fijados."""
    translation = _cantilever_with(EndConnection.pinned())
    request = translation.request
    (orphan,) = request.orphan_fixes
    assert (orphan.node, orphan.rotations) == (_tip(request), (0, 1, 1))
    (warning,) = translation.warnings
    assert warning.message_key == "fem3d.orphan_rotation_restrained"
    assert warning.params_dict()["rotations"] == "Ry, Rz"
    assert HYPOTHESIS_ORPHAN in translation.hypotheses


def test_oblique_orphan_is_not_fixed() -> None:
    translation = _cantilever_with(EndConnection.pinned(), tip=OBLIQUE, alpha=0.37)
    request = translation.request
    assert not request.orphan_fixes
    (orphan,) = orphan_rotations(request, 1e-9)
    assert orphan.axes is None
    assert len(orphan.basis) == 2


def test_orphan_with_applied_moment_is_not_fixed() -> None:
    frame = cantilever(Frame())
    _set_end(frame, MemberEnd.J, EndConnection.pinned())
    tip = next(n for n in frame.model.nodes.values() if n.position.x != 0.0)
    frame.nodal(tip, mz=50.0)
    translation = translate(frame)
    assert not translation.request.orphan_fixes
    assert translation.orphan_loaded == (tip.id,)


def test_moment_outside_the_orphan_still_fixes_it() -> None:
    frame = cantilever(Frame())
    _set_end(frame, MemberEnd.J, EndConnection.pinned())
    tip = next(n for n in frame.model.nodes.values() if n.position.x != 0.0)
    frame.nodal(tip, mx=50.0)
    translation = translate(frame)
    assert [o.rotations for o in translation.request.orphan_fixes] == [(0, 1, 1)]
    assert translation.orphan_loaded == ()


def test_torsion_released_at_i_removes_ex_also_at_j() -> None:
    """R1: con Rx liberado en I la barra no da torsión en J: huérfano de dimensión 3."""
    frame = cantilever(Frame())
    _set_end(frame, MemberEnd.I, _released(Dof.RX))
    _set_end(frame, MemberEnd.J, EndConnection.pinned())
    (orphan,) = translate(frame).request.orphan_fixes
    assert orphan.rotations == (1, 1, 1)


def test_tripod_apex_has_no_orphan() -> None:
    """T-FEM3D-04: tres barras articuladas no coplanarias dan los tres ejes de giro (ex)."""
    frame = Frame()
    apex = frame.node(0.0, 3.0)
    for x, y in ((2.0, 0.0), (-1.0, 1.7), (-1.0, -1.7)):
        base = frame.node(x, 0.0, y=y)
        frame.member(base, apex)
        frame.support(base, fixed_restraints())
    for index in range(3):
        for end in MemberEnd:
            _set_end(frame, end, EndConnection.pinned(), index)
    frame.nodal(apex, fx=1000.0, fz=-5000.0)
    request = translate(frame).request
    assert not request.orphan_fixes
    assert orphan_rotations(request, 1e-9) == ()
    assert all((b.release_y, b.release_z) == (3, 3) for b in request.beams)


def test_solver_free_dofs_count_aux_and_orphans() -> None:
    request = _cantilever_with(
        _released(Dof.UY).with_dof(Dof.RY, DofState.RELEASED).with_dof(Dof.RZ, DofState.RELEASED)
    ).request
    nodes = len(request.nodes) + len(request.aux_nodes)
    fixed = sum(sum(f.fixity) for f in request.fixes)
    orphan = sum(sum(o.rotations) for o in request.orphan_fixes)
    assert request.aux_nodes
    assert orphan == 2
    assert solver_free_dofs(request) == 6 * nodes - fixed - orphan


def test_rigid_models_translate_as_in_e1() -> None:
    """Sin extremos no rígidos, la petición no lleva nada de E2 (criterio de salida)."""
    request = translate(cantilever(Frame(), OBLIQUE, alpha=0.37)).request
    assert request.orphan_fixes == ()
    assert request.aux_nodes == ()
    assert request.end_equals == ()
    assert request.end_springs == ()
    assert all(
        (b.release_y, b.release_z, b.axial_released, b.torsion_released) == (0, 0, False, False)
        for b in request.beams
    )
