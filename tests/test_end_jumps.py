"""Salto de extremo de barra, adaptador plano (RES-001; plan T-020, E2.1: D1-D3, D5).

Referencia independiente (por cuerpo libre, a mano): viga N1-N3-N2 de L = 4 m, empotrada en N1
y N2, con P en N3 y el extremo J de B2 (sobre N2) liberado. Es una viga empotrada-articulada:
el giro del extremo de la barra respecto a su tangente en N1 es θ_B = (1/EI)·∫M dx = P·L²/(32·EI)
(dUz/dx), de modo que Ry = −dUz/dx = −P·L²/(32·EI). N2 está empotrado: el giro del nudo es 0.
Con un resorte k, el momento de extremo publicado es M = −k·salto.
"""

import pytest

from fem_support import E_PA, IY, Frame
from veritas import fem
from veritas.core import units as u
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.supports.support import fixed_restraints, pinned_restraints
from veritas.fem.results.normalized import (
    ComponentStatus,
    EndJump,
    PlanarResults,
    ResultBasis,
    normalize_planar_xz,
)

L, P = 4.0, 10_000.0
EI = E_PA * IY
RELEASED = EndConnection.rigid().with_dof(Dof.RY, DofState.RELEASED)
REFERENCE = -P * L**2 / (32 * EI)  # Ry local del extremo J de B2, liberado


def spring(ratio: float = 1.0) -> tuple[EndConnection, float]:
    k = ratio * EI / L
    return EndConnection.semi_rigid(Dof.RY, u.Quantity(k, Dof.RY.stiffness_dimension)), k


def solve(
    end_j: EndConnection | None, direction: float = 1.0
) -> tuple[Frame, PlanarResults, list[object]]:
    """N1-N3-N2 a lo largo de ``direction``·X; B1 = N1→N3 y B2 = N3→N2 con ``end_j`` en J."""
    frame = Frame()
    n1 = frame.node(0.0, 0.0)
    n3 = frame.node(direction * L / 2, 0.0)
    n2 = frame.node(direction * L, 0.0)
    b1, b2 = frame.member(n1, n3), frame.member(n3, n2)
    frame.support(n1, fixed_restraints())
    frame.support(n2, fixed_restraints())
    if end_j is not None:
        frame.model.set_member_end(b2.id, MemberEnd.J, end_j)
    frame.nodal(n3, fz=-P)
    run = fem.analyze_planar_xz(frame.model, frame.config())
    return frame, normalize_planar_xz(run, frame.model), [b1.id, b2.id]


def jumps_of(results: PlanarResults) -> dict[tuple[object, str], EndJump]:
    return {(j.member_id, j.end): j for j in results.end_jumps}


@pytest.mark.solver
def test_released_end_matches_the_closed_form() -> None:
    _, results, (_, b2) = solve(RELEASED)
    (jump,) = results.end_jumps
    assert (jump.member_id, jump.end) == (b2, "J")
    ry = jump.value.components[4].value
    assert ry == pytest.approx(REFERENCE, rel=1e-9)
    assert jump.value.basis is ResultBasis.MEMBER_LOCAL


@pytest.mark.solver
def test_component_statuses_by_degree_of_freedom() -> None:
    """Calculado en el GDL no rígido; no aplicable en los rígidos del plano; n/c fuera del plano."""
    _, results, _ = solve(RELEASED)
    (jump,) = results.end_jumps
    statuses = [c.status for c in jump.value.components]
    assert statuses == [
        ComponentStatus.NOT_APPLICABLE,  # Ux, rígido
        ComponentStatus.NOT_CALCULATED,  # Uy, fuera del plano
        ComponentStatus.NOT_APPLICABLE,  # Uz, rígido
        ComponentStatus.NOT_CALCULATED,  # Rx
        ComponentStatus.CALCULATED,  # Ry, liberado
        ComponentStatus.NOT_CALCULATED,  # Rz
    ]
    assert all(c.value is None for i, c in enumerate(jump.value.components) if i != 4)


@pytest.mark.solver
def test_rigid_ends_have_no_entry() -> None:
    _, results, _ = solve(None)
    assert results.end_jumps == ()


@pytest.mark.solver
@pytest.mark.parametrize("ratio", [0.25, 1.0, 20.0])
def test_spring_jump_is_consistent_with_the_published_end_moment(ratio: float) -> None:
    connection, k = spring(ratio)
    _, results, (_, b2) = solve(connection)
    (jump,) = results.end_jumps
    ry = jump.value.components[4].value
    assert ry is not None
    member = next(m for m in results.members if m.member_id == b2)
    moment = member.p_j.components[4].value
    assert moment is not None
    # M = −k·salto, con la escala de los momentos (P·L/8) como en `analyze uniones`.
    assert abs(moment + k * ry) <= 1e-9 * P * L / 8
    # Entre rígida (0) y liberada: 0 < |salto| < |salto liberado| para un único resorte.
    assert 0.0 < abs(ry) < abs(REFERENCE)


@pytest.mark.solver
def test_spring_jump_tends_to_the_released_one_when_the_spring_is_soft() -> None:
    connection, _ = spring(1e-6)
    _, results, _ = solve(connection)
    ry = results.end_jumps[0].value.components[4].value
    assert ry == pytest.approx(REFERENCE, rel=1e-5)


@pytest.mark.solver
def test_local_base_is_independent_of_the_member_direction() -> None:
    """La misma viga a lo largo de −X (ex = −X, ey = −Y): el Ry local es el mismo."""
    _, forward, _ = solve(RELEASED)
    _, mirrored, _ = solve(RELEASED, direction=-1.0)
    a = forward.end_jumps[0].value.components[4].value
    b = mirrored.end_jumps[0].value.components[4].value
    assert a == pytest.approx(REFERENCE, rel=1e-9)
    assert b == pytest.approx(REFERENCE, rel=1e-9)
    # Los ejes locales publicados corresponden a la dirección de la barra.
    assert mirrored.end_jumps[0].axes[0].x == pytest.approx(-1.0)
    assert mirrored.end_jumps[0].axes[1].y == pytest.approx(-1.0)


@pytest.mark.solver
def test_orphan_node_rotation_has_no_jump_value() -> None:
    """Con todas las barras del nudo liberadas, el giro del nudo no tiene valor propio y el
    salto de giro no es aplicable (nunca un cero)."""
    frame = Frame()
    n1, n2 = frame.node(0.0, 0.0), frame.node(L, 0.0)
    bar = frame.member(n1, n2)
    frame.support(n1, fixed_restraints())
    frame.support(n2, pinned_restraints())
    frame.model.set_member_end(bar.id, MemberEnd.J, RELEASED)
    frame.model.add_member_uniform_load(frame.case_id, bar.id, qz=u.Quantity.of(-1000.0, "N/m"))
    run = fem.analyze_planar_xz(frame.model, frame.config())
    results = normalize_planar_xz(run, frame.model)
    (jump,) = results.end_jumps
    rotation = jump.value.components[4]
    assert rotation.value is None
    assert rotation.status is ComponentStatus.NOT_APPLICABLE


@pytest.mark.solver
def test_jump_does_not_change_the_other_results() -> None:
    """Publicar el salto no altera los demás resultados: Uz(N3) de la liberada es el de
    `analyze uniones`, −7·P·L³/(768·EI)."""
    _, results, _ = solve(RELEASED)
    uz = [n.displacement.components[2].value for n in results.nodes]
    expected = -7 * P * L**3 / (768 * EI)
    assert any(v is not None and v == pytest.approx(expected, rel=1e-9) for v in uz)
