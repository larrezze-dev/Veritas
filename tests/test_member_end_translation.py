"""Traducción y salvaguarda de extremos no rígidos, sin solver (plan T-010, D2-D4 y D8)."""

import math

import pytest

from fem_support import AREA, E_PA, IY, IZ, Frame
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.diagnostics import DiagnosticCode, InputValueError, Severity
from veritas.core.geometry.entities import Member
from veritas.core.supports.support import fixed_restraints, pinned_restraints
from veritas.core.units import Quantity
from veritas.fem.adapters.planar_xz import (
    HYPOTHESIS_END_LINKS,
    HYPOTHESIS_ORPHAN,
    NEAR_RIGID_RATIO,
    Translation,
    translate_planar_xz,
)
from veritas.fem.adapters.protocol import PlanarRequest
from veritas.fem.adapters.singularity import (
    check_singularity,
    full_stiffness,
    reduced_stiffness,
    translational_diagonal,
)

L = 2.0
EI = E_PA * IY


def released(dof: Dof = Dof.RY) -> EndConnection:
    return EndConnection.rigid().with_dof(dof, DofState.RELEASED)


def spring(k: float, dof: Dof = Dof.RY) -> EndConnection:
    return EndConnection.rigid().with_dof(
        dof, DofState.SPRING, Quantity(k, dof.stiffness_dimension)
    )


def cantilever(connection: EndConnection, *, alpha: float | None = None) -> tuple[Frame, Member]:
    """Voladizo empotrado en N1 con el extremo I modificado; carga en N2."""
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(L, 0)
    member = frame.member(a, b, alpha=alpha)
    frame.support(a, fixed_restraints())
    frame.nodal(b, fz=-1000.0)
    frame.model.set_member_end(member.id, MemberEnd.I, connection)
    return frame, member


def translate(frame: Frame) -> Translation:
    return translate_planar_xz(frame.model, frame.config())


def rejected_key(frame: Frame) -> str:
    with pytest.raises(InputValueError) as info:
        translate(frame)
    assert info.value.code is DiagnosticCode.UNSUPPORTED_CONFIGURATION
    return info.value.diagnostic.message_key


@pytest.mark.parametrize("dof", [Dof.UX, Dof.UY, Dof.UZ])
@pytest.mark.parametrize("state", ["released", "spring"])
def test_translation_dofs_rejected(dof: Dof, state: str) -> None:
    connection = released(dof) if state == "released" else spring(1e6, dof)
    frame, _ = cantilever(connection)
    assert rejected_key(frame) == "fem.member_end_translation_unsupported"


@pytest.mark.parametrize("dof", [Dof.RX, Dof.RZ])
def test_out_of_plane_rotation_rejected_in_iy_branch(dof: Dof) -> None:
    frame, _ = cantilever(released(dof))
    assert rejected_key(frame) == "fem.member_end_out_of_plane_unsupported"


def test_iz_branch_uses_rz_as_in_plane_rotation() -> None:
    # alfa = π/2: ez ∥ ±Y, flexión del plano con Iz y giro del plano = Rz local.
    frame, _ = cantilever(released(Dof.RZ), alpha=math.pi / 2)
    request = translate(frame).request
    (beam,) = request.beams
    assert (beam.inertia_axis, beam.inertia) == ("Iz", IZ)
    (link,) = request.end_links
    assert link.stiffness is None
    frame, _ = cantilever(released(Dof.RY), alpha=math.pi / 2)
    assert rejected_key(frame) == "fem.member_end_out_of_plane_unsupported"


def test_rigid_model_has_no_links_or_new_hypotheses() -> None:
    frame, _ = cantilever(EndConnection.rigid())
    translation = translate(frame)
    assert translation.request.end_links == ()
    assert translation.request.orphan_fixes == ()
    assert HYPOTHESIS_END_LINKS not in translation.hypotheses


def test_link_content_and_hypothesis() -> None:
    frame, member = cantilever(spring(5e6))
    translation = translate(frame)
    request = translation.request
    (link,) = request.end_links
    (beam,) = request.beams
    assert link.member_id == str(member.id)
    assert (link.beam, link.end, link.stiffness) == (beam.tag, "I", 5e6)
    assert link.master == beam.node_i
    assert request.element_node(beam, "I") == link.aux
    assert request.element_node(beam, "J") == beam.node_j
    assert HYPOTHESIS_END_LINKS in translation.hypotheses
    assert PlanarRequest.from_json(request.to_json()) == request


def test_near_rigid_spring_warns() -> None:
    ratio = 10 * NEAR_RIGID_RATIO
    frame, member = cantilever(spring(ratio * EI / L))
    warnings = [
        w for w in translate(frame).warnings if w.message_key == "fem.end_spring_near_rigid"
    ]
    assert len(warnings) == 1
    assert warnings[0].severity is Severity.WARNING
    assert warnings[0].location is not None
    assert warnings[0].location.entity_id == member.id
    frame, _ = cantilever(spring(1.0 * EI / L))
    assert not [
        w for w in translate(frame).warnings if w.message_key == "fem.end_spring_near_rigid"
    ]


def pinned_beam(moment: float = 0.0) -> Frame:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(L, 0)
    member = frame.member(a, b)
    frame.support(a, pinned_restraints())
    frame.support(b, pinned_restraints())
    frame.nodal(b, fz=-1000.0, my=moment)
    frame.model.set_member_end(member.id, MemberEnd.J, released())
    return frame


def test_orphan_rotation_restrained_and_flagged() -> None:
    translation = translate(pinned_beam())
    request = translation.request
    (link,) = request.end_links
    assert request.orphan_fixes == (link.master,)
    assert HYPOTHESIS_ORPHAN in translation.hypotheses
    assert translation.orphan_loaded == ()
    keys = [w.message_key for w in translation.warnings]
    assert keys.count("fem.orphan_rotation_restrained") == 1


def test_loaded_orphan_is_not_restrained() -> None:
    frame = pinned_beam(moment=200.0)
    translation = translate(frame)
    assert translation.request.orphan_fixes == ()
    (node_id,) = translation.orphan_loaded
    assert frame.model.node(node_id).position.x == L


def test_spring_end_is_not_orphan() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(L, 0)
    member = frame.member(a, b)
    frame.support(a, pinned_restraints())
    frame.support(b, pinned_restraints())
    frame.nodal(b, fz=-1000.0)
    frame.model.set_member_end(member.id, MemberEnd.J, spring(1e6))
    assert translate(frame).request.orphan_fixes == ()


def test_condensed_stiffness_of_released_end_matches_hand_formula() -> None:
    # Empotrado en N1 con el extremo I liberado: condensando el giro del aux, la rigidez en
    # N2 es la de una barra articulada-libre: EA/L, 3EI/L³, −3EI/L² y 3EI/L (revisión M2).
    frame, _ = cantilever(released())
    request = translate(frame).request
    matrix, labels = reduced_stiffness(request)
    aux = request.end_links[0].aux
    a = next(i for i, label in enumerate(labels) if label.node == aux)
    free = [i for i in range(len(labels)) if i != a]
    condensed = [
        [matrix[i][j] - matrix[i][a] * matrix[a][j] / matrix[a][a] for j in free] for i in free
    ]
    expected = [
        [E_PA * AREA / L, 0.0, 0.0],
        [0.0, 3 * EI / L**3, -3 * EI / L**2],
        [0.0, -3 * EI / L**2, 3 * EI / L],
    ]
    for row, expected_row in zip(condensed, expected, strict=True):
        for value, reference in zip(row, expected_row, strict=True):
            assert value == pytest.approx(reference, rel=1e-12, abs=1e-6 * EI)


def test_end_spring_is_relative_between_master_and_aux() -> None:
    k = 7e6
    frame, _ = cantilever(spring(k))
    request = translate(frame).request
    stiffness, index = full_stiffness(request)
    link = request.end_links[0]
    master = 3 * index[link.master] + 2
    aux = 3 * len(request.nodes)
    beam_only = 4 * EI / L
    assert stiffness[master][master] == pytest.approx(k)
    assert stiffness[aux][aux] == pytest.approx(k + beam_only)
    assert stiffness[master][aux] == pytest.approx(-k)
    assert stiffness[aux][master] == pytest.approx(-k)


def test_released_ends_do_not_change_translational_penalty_scale() -> None:
    rigid, _ = cantilever(EndConnection.rigid())
    hinged, _ = cantilever(released())
    assert translational_diagonal(translate(rigid).request) == pytest.approx(
        translational_diagonal(translate(hinged).request)
    )


def test_member_released_at_both_ends_is_not_a_spurious_mechanism() -> None:
    # Barra con los dos giros de extremo liberados sobre dos articulaciones: los giros de los
    # aux quedan acoplados por la propia barra y los de los nudos se restringen por huérfanos;
    # es estable (revisión M5) y la salvaguarda no debe declararla singular.
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(L, 0)
    member = frame.member(a, b)
    frame.support(a, pinned_restraints())
    frame.support(b, pinned_restraints())
    frame.nodal(b, fz=-1000.0)
    both = released()
    frame.model.set_member_end(member.id, MemberEnd.I, both)
    frame.model.set_member_end(member.id, MemberEnd.J, both)
    report = check_singularity(translate(frame).request, 1e-12)
    assert not report.singular
