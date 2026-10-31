"""Salto de extremo de barra, adaptador espacial (RES-001; plan T-020, E2.2: D1-D5).

Referencias independientes (por cuerpo libre, a mano):
- viga N1-N3-N2 de L = 4 m, empotrada en N1 y N2, P central, extremo J de B2 liberado: Ry =
  −P·L²/(32·EI) (ver ``test_end_jumps.py``); el mismo valor sale del adaptador plano;
- empotrada-articulada con q uniforme: la pendiente del extremo articulado es q·L³/(48·EI);
- biapoyada con q uniforme y los dos giros liberados: pendientes ∓q·L³/(24·EI);
- resorte de traslación o de giro: la fuerza o el momento publicado del extremo es −k·salto;
- Ux y Rx con resorte en serie: el nudo se mueve F·(L/EA + 1/k) y la barra F·L/EA, luego el
  salto es −F/k (y −T/k en torsión).
"""

from uuid import UUID

import pytest

from fem_support import E_PA, IY, IZ, Frame
from veritas import fem
from veritas.core import units as u
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.geometry.entities import Node
from veritas.core.loads.loads import LoadBasis
from veritas.core.supports.support import fixed_restraints, pinned_restraints
from veritas.fem.benchmarks import J_3D
from veritas.fem.results.normalized import (
    ComponentStatus,
    EndJump,
    ResultBasis,
    normalize_planar_xz,
)
from veritas.fem.spatial import analyze_spatial
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.jumps import full_p0
from veritas.fem.spatial.postconditions import beam_load_forces, member_fixed_end_forces
from veritas.fem.spatial.results import SpatialResults, normalize_spatial
from veritas.fem.spatial.stiffness import beam_matrices, condense_beam, local_beam_stiffness
from veritas.fem.spatial.translator import translate_spatial

pytestmark = pytest.mark.solver

L, P = 4.0, 10_000.0
EI, EIZ = E_PA * IY, E_PA * IZ
G_PA = 81e9
A_SECTION = 0.2 * 0.4
EA = E_PA * A_SECTION
RELEASED = EndConnection.rigid().with_dof(Dof.RY, DofState.RELEASED)
REFERENCE = -P * L**2 / (32 * EI)
UX, UY, UZ, RX, RY, RZ = range(6)


def released(*dofs: Dof) -> EndConnection:
    connection = EndConnection.rigid()
    for dof in dofs:
        connection = connection.with_dof(dof, DofState.RELEASED)
    return connection


def spring(dof: Dof, value: float) -> EndConnection:
    quantity = u.Quantity(value, dof.stiffness_dimension)
    return EndConnection.rigid().with_dof(dof, DofState.SPRING, quantity)


def solve(frame: Frame) -> SpatialResults:
    run = analyze_spatial(frame.model, SpatialAnalysisConfiguration(frame.case_id))
    return normalize_spatial(run, frame.model)


def beam_two(end_j: EndConnection) -> tuple[Frame, Node, UUID]:
    """N1-N3-N2 a lo largo de +X, B2 con ``end_j`` en J; el llamador pone la carga en N3."""
    frame = Frame()
    n1, n3, n2 = frame.node(0.0, 0.0), frame.node(L / 2, 0.0), frame.node(L, 0.0)
    b1, b2 = frame.member(n1, n3), frame.member(n3, n2)
    frame.support(n1, fixed_restraints())
    frame.support(n2, fixed_restraints())
    frame.model.set_member_end(b2.id, MemberEnd.J, end_j)
    assert b1 is not None
    return frame, n3, b2.id


def jump_of(results: SpatialResults, member_id: UUID, end: str) -> EndJump:
    (found,) = [j for j in results.end_jumps if (j.member_id, j.end) == (member_id, end)]
    return found


def value(jump: EndJump, index: int) -> float:
    cell = jump.value.components[index].value
    assert cell is not None
    return cell


def end_force(results: SpatialResults, member_id: UUID, end: str, index: int) -> float:
    member = next(m for m in results.members if m.member_id == member_id)
    force = (member.p_i if end == "I" else member.p_j).components[index].value
    assert force is not None
    return force


# --- Ry / Rz liberados: condensación inversa -----------------------------------------------


def test_released_ry_matches_the_closed_form() -> None:
    frame, n3, b2 = beam_two(RELEASED)
    frame.nodal(n3, fz=-P)
    jump = jump_of(solve(frame), b2, "J")
    assert value(jump, RY) == pytest.approx(REFERENCE, rel=1e-9)
    assert jump.value.basis is ResultBasis.MEMBER_LOCAL


def test_component_statuses_by_degree_of_freedom() -> None:
    frame, n3, b2 = beam_two(RELEASED)
    frame.nodal(n3, fz=-P)
    jump = jump_of(solve(frame), b2, "J")
    statuses = [c.status for c in jump.value.components]
    assert statuses[RY] is ComponentStatus.CALCULATED
    assert [s for i, s in enumerate(statuses) if i != RY] == [ComponentStatus.NOT_APPLICABLE] * 5
    assert all(c.value is None for i, c in enumerate(jump.value.components) if i != RY)


def test_released_rz_in_the_xy_plane_matches_the_closed_form() -> None:
    """Flexión en el plano xy con Iz: duy/dx = +Rz, luego Rz = +P·L²/(32·EIz)."""
    frame = Frame()
    a, b, c = frame.node(0, 0), frame.node(L / 2, 0), frame.node(L, 0)
    frame.member(a, b)
    second = frame.member(b, c)
    frame.support(a, fixed_restraints())
    frame.support(c, fixed_restraints())
    frame.model.set_member_end(second.id, MemberEnd.J, released(Dof.RZ))
    frame.nodal(b, fy=-P)
    jump = jump_of(solve(frame), second.id, "J")
    assert value(jump, RZ) == pytest.approx(P * L**2 / (32 * EIZ), rel=1e-9)


def test_uniform_load_uses_the_uncondensed_p0() -> None:
    """Empotrada-articulada con q uniforme: pendiente del extremo articulado qL³/(48EI)."""
    q = 3_000.0
    frame = Frame()
    n1, n2, n3 = frame.node(0.0, 0.0), frame.node(L, 0.0), frame.node(L + 1.0, 0.0)
    bar, stub = frame.member(n1, n2), frame.member(n2, n3)
    frame.support(n1, fixed_restraints())
    frame.support(n2, fixed_restraints())
    frame.model.set_member_end(bar.id, MemberEnd.J, RELEASED)
    frame.model.add_member_uniform_load(frame.case_id, bar.id, qz=u.Quantity.of(-q, "N/m"))
    jump = jump_of(solve(frame), bar.id, "J")
    assert value(jump, RY) == pytest.approx(-q * L**3 / (48 * EI), rel=1e-9)
    assert stub is not None


def test_distributed_member_load_gives_the_same_jump_as_the_uniform_one() -> None:
    """Dos caminos de p0 (qz uniforme y MemberDistributedLoad) con liberación: mismo salto."""
    q = 3_000.0
    jumps = []
    for distributed in (False, True):
        frame = Frame()
        n1, n2, n3 = frame.node(0.0, 0.0), frame.node(L, 0.0), frame.node(L + 1.0, 0.0)
        bar = frame.member(n1, n2)
        frame.member(n2, n3)
        frame.support(n1, fixed_restraints())
        frame.support(n2, fixed_restraints())
        frame.model.set_member_end(bar.id, MemberEnd.J, RELEASED)
        intensity = u.Quantity.of(-q, "N/m")
        if distributed:
            frame.model.add_member_distributed_load(
                frame.case_id,
                bar.id,
                start=0.0,
                end=1.0,
                basis=LoadBasis.GLOBAL,
                qz_start=intensity,
                qz_end=intensity,
            )
        else:
            frame.model.add_member_uniform_load(frame.case_id, bar.id, qz=intensity)
        jumps.append(value(jump_of(solve(frame), bar.id, "J"), RY))
    assert jumps[0] == pytest.approx(-q * L**3 / (48 * EI), rel=1e-9)
    assert jumps[1] == pytest.approx(jumps[0], rel=1e-9)


def test_uncondensed_p0_condenses_to_the_p0_of_the_request() -> None:
    """El p0 sin condensar del salto (uniforme de la petición + cargas de barra del dominio)
    condensa, con la misma k, exactamente al p0_c que viaja en la petición (D5: una sola
    formulación)."""
    frame = Frame()
    n1, n2, n3 = frame.node(0.0, 0.0), frame.node(L, 0.0), frame.node(L + 1.0, 0.0)
    bar = frame.member(n1, n2)
    frame.member(n2, n3)
    frame.support(n1, fixed_restraints())
    frame.support(n2, fixed_restraints())
    frame.model.set_member_end(bar.id, MemberEnd.J, RELEASED)
    frame.model.add_member_uniform_load(frame.case_id, bar.id, qz=u.Quantity.of(-3000.0, "N/m"))
    frame.model.add_member_distributed_load(
        frame.case_id,
        bar.id,
        start=0.1,
        end=0.7,
        basis=LoadBasis.GLOBAL,
        qz_start=u.Quantity.of(-500.0, "N/m"),
        qz_end=u.Quantity.of(-2000.0, "N/m"),
    )
    frame.model.add_member_point_load(
        frame.case_id, bar.id, position=0.4, fz=u.Quantity.of(-7000.0, "N")
    )
    request = translate_spatial(frame.model, SpatialAnalysisConfiguration(frame.case_id)).request
    beam = next(b for b in request.beams if UUID(b.entity_id) == bar.id)
    length = beam_matrices(request, beam).length
    full = full_p0(frame.model, request, beam, length)
    stiffness = local_beam_stiffness(beam, length)
    _, condensed = condense_beam(beam, stiffness, full, length=length)
    assert condensed is not None
    sent = beam_load_forces(request, beam, length)
    extra = member_fixed_end_forces(request).get(beam.tag, [0.0] * 12)
    scale = max(abs(v) for v in full)
    for got, uniform, loads in zip(condensed, sent, extra, strict=True):
        assert got == pytest.approx(uniform + loads, abs=1e-9 * scale)


def test_both_ends_released_in_a_plane_recover_both_rotations() -> None:
    """Biapoyada con q uniforme y los dos giros liberados (código 3): Ry_I = +qL³/(24EI),
    Ry_J = −qL³/(24EI), con los nudos empotrados."""
    q = 3_000.0
    frame = Frame()
    n1, n2, n3 = frame.node(0.0, 0.0), frame.node(L, 0.0), frame.node(L + 1.0, 0.0)
    bar = frame.member(n1, n2)
    frame.member(n2, n3)
    frame.support(n1, fixed_restraints())
    frame.support(n2, fixed_restraints())
    frame.model.set_member_end(bar.id, MemberEnd.I, RELEASED)
    frame.model.set_member_end(bar.id, MemberEnd.J, RELEASED)
    frame.model.add_member_uniform_load(frame.case_id, bar.id, qz=u.Quantity.of(-q, "N/m"))
    results = solve(frame)
    expected = q * L**3 / (24 * EI)
    assert value(jump_of(results, bar.id, "I"), RY) == pytest.approx(expected, rel=1e-9)
    assert value(jump_of(results, bar.id, "J"), RY) == pytest.approx(-expected, rel=1e-9)


def test_orphan_node_rotation_has_no_jump_value() -> None:
    frame = Frame()
    n1, n2 = frame.node(0.0, 0.0), frame.node(L, 0.0)
    bar = frame.member(n1, n2)
    frame.support(n1, fixed_restraints())
    frame.support(n2, pinned_restraints())
    frame.model.set_member_end(bar.id, MemberEnd.J, RELEASED)
    frame.model.add_member_uniform_load(frame.case_id, bar.id, qz=u.Quantity.of(-1000.0, "N/m"))
    jump = jump_of(solve(frame), bar.id, "J")
    rotation = jump.value.components[RY]
    assert rotation.value is None
    assert rotation.status is ComponentStatus.NOT_APPLICABLE


# --- Resortes: aux (Uy, Uz, Ry, Rz) y serie (Ux, Rx) ---------------------------------------


@pytest.mark.parametrize("ratio", [0.25, 1.0, 20.0])
def test_rotation_spring_through_the_aux_matches_the_plane_and_the_moment(ratio: float) -> None:
    k = ratio * EI / L
    frame, n3, b2 = beam_two(spring(Dof.RY, k))
    frame.nodal(n3, fz=-P)
    results = solve(frame)
    jump = jump_of(results, b2, "J")
    assert end_force(results, b2, "J", RY) == pytest.approx(
        -k * value(jump, RY), abs=1e-9 * P * L / 8
    )
    assert 0.0 < abs(value(jump, RY)) < abs(REFERENCE)
    # Mismo modelo y mismo salto en el adaptador plano.
    planar = Frame()
    a, c, d = planar.node(0, 0), planar.node(L / 2, 0), planar.node(L, 0)
    planar.member(a, c)
    second = planar.member(c, d)
    planar.support(a, fixed_restraints())
    planar.support(d, fixed_restraints())
    planar.model.set_member_end(second.id, MemberEnd.J, spring(Dof.RY, k))
    planar.nodal(c, fz=-P)
    run = fem.analyze_planar_xz(planar.model, planar.config())
    planar_jump = normalize_planar_xz(run, planar.model).end_jumps[0].value.components[RY].value
    assert planar_jump == pytest.approx(value(jump, RY), rel=1e-9)


def test_translation_spring_through_the_aux_matches_minus_force_over_k() -> None:
    k = 40.0 * EI / L**3
    frame, n3, b2 = beam_two(spring(Dof.UZ, k))
    frame.nodal(n3, fz=-P)
    results = solve(frame)
    jump = jump_of(results, b2, "J")
    assert end_force(results, b2, "J", UZ) == pytest.approx(-k * value(jump, UZ), abs=1e-9 * P)
    statuses = [c.status for c in jump.value.components]
    assert statuses[UZ] is ComponentStatus.CALCULATED
    assert statuses.count(ComponentStatus.NOT_APPLICABLE) == 5


def test_released_translation_slides_through_the_aux() -> None:
    """Uz liberado en J (nudo empotrado): el extremo de la barra se desliza y no transmite
    cortante; el salto es el desplazamiento relativo y el cortante publicado es nulo."""
    frame, n3, b2 = beam_two(released(Dof.UZ))
    frame.nodal(n3, fz=-P)
    results = solve(frame)
    jump = jump_of(results, b2, "J")
    assert value(jump, UZ) < 0.0
    assert end_force(results, b2, "J", UZ) == pytest.approx(0.0, abs=1e-9 * P)


def test_axial_spring_in_series_matches_the_closed_form() -> None:
    """N1 empotrado, Ux con resorte k en J y F en el nudo libre N2: u_nudo = F·(L/EA + 1/k)
    y u_barra = F·L/EA, luego el salto es −F/k."""
    f, k = 50_000.0, 2.0e8
    frame = Frame()
    n1, n2 = frame.node(0.0, 0.0), frame.node(L, 0.0)
    bar = frame.member(n1, n2)
    frame.support(n1, fixed_restraints())
    frame.model.set_member_end(bar.id, MemberEnd.J, spring(Dof.UX, k))
    frame.nodal(n2, fx=f)
    results = solve(frame)
    jump = jump_of(results, bar.id, "J")
    node = next(n for n in results.nodes if n.node_id == n2.id)
    ux_node = node.displacement.components[UX].value
    assert ux_node == pytest.approx(f * (L / EA + 1 / k), rel=1e-9)
    assert value(jump, UX) == pytest.approx(-f / k, rel=1e-9)
    assert ux_node is not None
    assert ux_node + value(jump, UX) == pytest.approx(f * L / EA, rel=1e-9)  # extremo de barra


def test_axial_spring_at_end_i_has_the_opposite_sign() -> None:
    """Resorte Ux en I con tracción N = F (carga en N1 alejándose de N2): el extremo I de la
    barra queda por detrás del nudo en −X; salto = bar − nudo = +F/k."""
    f, k = 50_000.0, 2.0e8
    frame = Frame()
    n1, n2 = frame.node(0.0, 0.0), frame.node(L, 0.0)
    bar = frame.member(n1, n2)
    frame.support(n2, fixed_restraints())
    frame.model.set_member_end(bar.id, MemberEnd.I, spring(Dof.UX, k))
    frame.nodal(n1, fx=-f)
    results = solve(frame)
    jump = jump_of(results, bar.id, "I")
    node = next(n for n in results.nodes if n.node_id == n1.id)
    assert node.displacement.components[UX].value == pytest.approx(-f * (L / EA + 1 / k), rel=1e-9)
    assert value(jump, UX) == pytest.approx(f / k, rel=1e-9)
    assert end_force(results, bar.id, "I", UX) == pytest.approx(-f, rel=1e-9)  # pI_x = −N


def test_released_ry_with_an_aux_in_an_oblique_bar_uses_the_node_rotation() -> None:
    """Ry liberado y, en el mismo extremo, un resorte Uy (aux): en la barra oblicua general
    el giro del aux solo está atado por penalización, y el salto de Ry sale respecto al giro
    del nudo y no el del aux. El resorte fuera del plano no cambia la flexión en ez."""
    d = (2.0 / 7.0, -3.0 / 7.0, 6.0 / 7.0)
    end = EndConnection.rigid().with_dof(Dof.RY, DofState.RELEASED)
    end = end.with_dof(
        Dof.UY, DofState.SPRING, u.Quantity(1e3 * EI / L**3, Dof.UY.stiffness_dimension)
    )
    frame = Frame()
    nodes = [frame.node(L * t * d[0], L * t * d[2], L * t * d[1]) for t in (0, 0.5, 1)]
    frame.member(nodes[0], nodes[1])
    second = frame.member(nodes[1], nodes[2])
    frame.support(nodes[0], fixed_restraints())
    frame.support(nodes[2], fixed_restraints())
    frame.model.set_member_end(second.id, MemberEnd.J, end)
    load = frame.model.axes(second.id).ez.scaled(-P)
    frame.model.add_nodal_load(
        frame.case_id,
        nodes[1].id,
        fx=u.Quantity.of(load.x, "N"),
        fy=u.Quantity.of(load.y, "N"),
        fz=u.Quantity.of(load.z, "N"),
    )
    jump = jump_of(solve(frame), second.id, "J")
    assert value(jump, RY) == pytest.approx(REFERENCE, rel=1e-6)
    assert jump.value.components[UY].status is ComponentStatus.CALCULATED


def test_torsional_spring_in_series_matches_the_closed_form() -> None:
    t, k = 8_000.0, 5.0e6
    frame = Frame()
    n1, n2 = frame.node(0.0, 0.0), frame.node(L, 0.0)
    bar = frame.member(n1, n2)
    frame.support(n1, fixed_restraints())
    frame.model.set_member_end(bar.id, MemberEnd.J, spring(Dof.RX, k))
    frame.nodal(n2, mx=t)
    results = solve(frame)
    jump = jump_of(results, bar.id, "J")
    node = next(n for n in results.nodes if n.node_id == n2.id)
    rx_node = node.displacement.components[RX].value
    assert rx_node == pytest.approx(t * (L / (G_PA * J_3D) + 1 / k), rel=1e-9)
    assert value(jump, RX) == pytest.approx(-t / k, rel=1e-9)


@pytest.mark.parametrize("dof", [Dof.UX, Dof.RX])
def test_released_axial_or_twist_is_not_applicable(dof: Dof) -> None:
    frame = Frame()
    n1, n2, n3 = frame.node(0.0, 0.0), frame.node(L, 0.0), frame.node(L + 1.0, 0.0)
    bar = frame.member(n1, n2)
    frame.member(n2, n3)  # tramo libre: sin GDL libres el modelo se rechaza
    frame.support(n1, fixed_restraints())
    frame.support(n2, fixed_restraints())
    frame.model.set_member_end(bar.id, MemberEnd.J, released(dof))
    frame.model.add_member_uniform_load(frame.case_id, bar.id, qz=u.Quantity.of(-1000.0, "N/m"))
    jump = jump_of(solve(frame), bar.id, "J")
    cell = jump.value.components[UX if dof is Dof.UX else RX]
    assert cell.value is None
    assert cell.status is ComponentStatus.NOT_APPLICABLE


def test_rigid_ends_have_no_entry() -> None:
    frame, n3, _ = beam_two(EndConnection.rigid())
    frame.nodal(n3, fz=-P)
    assert solve(frame).end_jumps == ()


# --- Base local y contraste con el adaptador plano --------------------------------------------


@pytest.mark.parametrize(
    "end",
    [RELEASED, spring(Dof.RY, EI / L)],
    ids=["liberada", "resorte"],
)
def test_inclined_bar_agrees_with_the_planar_adapter(end: EndConnection) -> None:
    """Viga a 30° en XZ: el salto local es el mismo en los dos adaptadores (1e-9)."""
    cos, sin = 0.8660254037844386, 0.5
    values = []
    for adapter in ("planar", "spatial"):
        frame = Frame()
        n1 = frame.node(0.0, 0.0)
        n3 = frame.node(L / 2 * cos, L / 2 * sin)
        n2 = frame.node(L * cos, L * sin)
        frame.member(n1, n3)
        b2 = frame.member(n3, n2)
        frame.support(n1, fixed_restraints())
        frame.support(n2, fixed_restraints())
        frame.model.set_member_end(b2.id, MemberEnd.J, end)
        frame.nodal(n3, fz=-P)
        if adapter == "planar":
            run = fem.analyze_planar_xz(frame.model, frame.config())
            jump = normalize_planar_xz(run, frame.model).end_jumps[0]
        else:
            jump = jump_of(solve(frame), b2.id, "J")
        values.append(value(jump, RY))
    assert abs(values[0]) > 1e-7
    assert values[1] == pytest.approx(values[0], rel=1e-9)


def test_released_ry_in_a_general_oblique_bar_keeps_the_local_rotation() -> None:
    """Barra en una dirección oblicua general (2, −3, 6)/7: el mismo problema girado (carga
    central a lo largo de −ez local) da el mismo Ry local que sobre X."""
    direction = (2.0 / 7.0, -3.0 / 7.0, 6.0 / 7.0)
    results = []
    for end in ((1.0, 0.0, 0.0), direction):
        frame = Frame()
        nodes = [frame.node(L * t * end[0], L * t * end[2], L * t * end[1]) for t in (0, 0.5, 1)]
        frame.member(nodes[0], nodes[1])
        second = frame.member(nodes[1], nodes[2])
        frame.support(nodes[0], fixed_restraints())
        frame.support(nodes[2], fixed_restraints())
        frame.model.set_member_end(second.id, MemberEnd.J, RELEASED)
        load = frame.model.axes(second.id).ez.scaled(-P)
        frame.model.add_nodal_load(
            frame.case_id,
            nodes[1].id,
            fx=u.Quantity.of(load.x, "N"),
            fy=u.Quantity.of(load.y, "N"),
            fz=u.Quantity.of(load.z, "N"),
        )
        results.append(value(jump_of(solve(frame), second.id, "J"), RY))
    assert results[0] == pytest.approx(REFERENCE, rel=1e-9)
    assert results[1] == pytest.approx(REFERENCE, rel=1e-9)
