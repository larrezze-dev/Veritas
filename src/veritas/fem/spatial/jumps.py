"""Salto de extremo de barra en el adaptador espacial (plan T-020, E2.2; D1-D5).

Salto = movimiento del extremo de la barra − movimiento del nudo, por GDL local del extremo
(ADR-001), en la base local de la barra. Solo hay entrada en los extremos con algún GDL no
rígido. Una sola formulación por tipo de GDL, con las matrices del propio adaptador:

- Uy, Uz no rígidos y Ry, Rz con resorte: el extremo de la barra es el nudo auxiliar; el salto
  es su desplazamiento (``RawSpatialEndAux``) menos el del nudo, proyectado a la base local.
- Ry, Rz liberados (``-release*``): no hay aux; se recupera el giro condensado del elemento,
  u_r = −K_rr⁻¹·(K_rc·u_c + p0_r), con la k local sin condensar y el p0 sin condensar de las
  cargas de la barra (convenio localForce = k·u + p0, con fuerza nula en el GDL liberado), y se
  resta el giro del nudo (no el del aux) en la base local.
- Ux, Rx con resorte (en serie con A_ef y J_ef, exacto con N y T constantes): el resorte
  transmite la fuerza publicada del extremo, p = −k·salto, luego salto = −p/k.
- Ux, Rx liberados: el salto es indeterminado (NOT_APPLICABLE); GDL rígidos: NOT_APPLICABLE.
- Giro con el nudo en ``orphan_fixes`` (sin valor propio): NOT_APPLICABLE.

Nada se publica como cero si no se calcula.
"""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING
from uuid import UUID

from veritas.core.connections.end import Dof, DofState, EndConnection
from veritas.core.geometry.model import StructuralModel
from veritas.core.geometry.vector import Vec3
from veritas.core.loads.loads import MemberDistributedLoad, MemberDistributedMoment, MemberPointLoad
from veritas.fem.results.normalized import (
    Component,
    ComponentStatus,
    EndJump,
    ResultBasis,
    Vector6,
)
from veritas.fem.spatial.protocol import SpatialBeam, SpatialRequest
from veritas.fem.spatial.stiffness import (
    beam_matrices,
    fixed_end_forces,
    local_beam_stiffness,
    released_indices,
)
from veritas.fem.spatial.translator import member_load_p0

if TYPE_CHECKING:
    from veritas.fem.spatial.results import RawSpatialResults

type Twelve = Sequence[float]

_NC = Component(None, ComponentStatus.NOT_CALCULATED)
_NA = Component(None, ComponentStatus.NOT_APPLICABLE)
_DOFS_PER_NODE = 6
_ROTATION_STIFFNESS_DOFS = (Dof.RY, Dof.RZ)


def _calc(value: float) -> Component:
    return Component(value, ComponentStatus.CALCULATED)


def _si_stiffness(connection: EndConnection, dof: Dof) -> float | None:
    spring = connection.stiffness_of(dof)
    return None if spring is None else spring.si_value


def _dot(row: Sequence[float], vector: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(row, vector, strict=True))


def _solve(matrix: list[list[float]], rhs: list[float]) -> list[float]:
    """Eliminación de Gauss con pivote parcial (sistemas de 1 a 4 incógnitas)."""
    n = len(rhs)
    rows = [[*row, value] for row, value in zip(matrix, rhs, strict=True)]
    for column in range(n):
        pivot = max(range(column, n), key=lambda r: abs(rows[r][column]))
        rows[column], rows[pivot] = rows[pivot], rows[column]
        for r in range(column + 1, n):
            factor = rows[r][column] / rows[column][column]
            rows[r] = [a - factor * b for a, b in zip(rows[r], rows[column], strict=True)]
    solution = [0.0] * n
    for r in range(n - 1, -1, -1):
        known = sum(rows[r][c] * solution[c] for c in range(r + 1, n))
        solution[r] = (rows[r][n] - known) / rows[r][r]
    return solution


def full_p0(
    model: StructuralModel, request: SpatialRequest, beam: SpatialBeam, length: float
) -> list[float]:
    """p0 local 12 SIN condensar de todas las cargas de la barra en el caso de la petición."""
    p0 = [0.0] * 2 * _DOFS_PER_NODE
    for beam_load in request.beam_loads:
        if beam_load.beam == beam.tag:
            for k, value in enumerate(fixed_end_forces(beam_load, length)):
                p0[k] += value
    member_id = UUID(beam.entity_id)
    for load in model.loads_in_case(UUID(request.case_id)):
        if (
            isinstance(load, MemberPointLoad | MemberDistributedLoad | MemberDistributedMoment)
            and load.member_id == member_id
        ):
            for k, value in enumerate(member_load_p0(model, load, length)):
                p0[k] += value
    return p0


def _recovered_rotations(
    model: StructuralModel,
    request: SpatialRequest,
    beam: SpatialBeam,
    length: float,
    u_local: Sequence[float],
) -> dict[int, float]:
    """Giros locales de los GDL de ``-release*`` por condensación inversa."""
    released = list(released_indices(beam))
    if not released:
        return {}
    k = local_beam_stiffness(beam, length)
    p0 = full_p0(model, request, beam, length)
    retained = [i for i in range(2 * _DOFS_PER_NODE) if i not in released]
    rhs = [-(sum(k[r][c] * u_local[c] for c in retained) + p0[r]) for r in released]
    matrix = [[k[r][s] for s in released] for r in released]
    try:
        return dict(zip(released, _solve(matrix, rhs), strict=True))
    except ZeroDivisionError:  # K_rr singular: el salto queda no calculado, nunca un cero
        return {}


def end_jumps(
    model: StructuralModel,
    request: SpatialRequest,
    raw: "RawSpatialResults",
    forces: Mapping[UUID, Twelve],
) -> tuple[EndJump, ...]:
    """Saltos de los extremos no rígidos. ``forces``: fuerzas de extremo locales publicadas
    (pI, pJ) por barra, con el p0 de las cargas incluido."""
    master = {node.node_id: node.displacement for node in raw.nodes}
    aux = {(item.member_id, item.end): item.displacement for item in raw.end_aux}
    tags = {UUID(node.entity_id): node.tag for node in request.nodes}
    orphans = {fix.node for fix in request.orphan_fixes if any(fix.rotations)}
    result = []
    for beam in request.beams:
        member_id = UUID(beam.entity_id)
        member = model.member(member_id)
        ends = (("I", member.end_i, member.node_i, 0), ("J", member.end_j, member.node_j, 6))
        if all(connection.is_rigid for _, connection, _, _ in ends):
            continue
        q = model.axes(member_id)
        matrices = beam_matrices(request, beam)
        u_global: list[float] = []
        for end, _connection, node, _offset in ends:
            u_global += aux.get((member_id, end), master[node])
        u_local = [_dot(row, u_global) for row in matrices.transformation]
        needs_recovery = any(
            connection.state(dof) is DofState.RELEASED
            for _, connection, _, _ in ends
            for dof in _ROTATION_STIFFNESS_DOFS
        )
        recovered = (
            _recovered_rotations(model, request, beam, matrices.length, u_local)
            if needs_recovery
            else {}
        )
        for end, connection, node, offset in ends:
            if connection.is_rigid:
                continue
            relative: tuple[Vec3, Vec3] | None = None
            if (member_id, end) in aux:
                d = aux[(member_id, end)]
                moved = master[node]
                relative = (
                    q.to_local(Vec3(*(d[i] - moved[i] for i in range(3)))),
                    q.to_local(Vec3(*(d[i] - moved[i] for i in range(3, 6)))),
                )
            orphan = tags[node] in orphans
            # Giro del nudo en la base local: el de referencia de un Ry/Rz liberado (el giro
            # del aux, con barra oblicua, solo está atado por penalización; M1 de la revisión).
            node_rotation = q.to_local(Vec3(*master[node][3:])).components()
            cells = []
            for dof in Dof:
                cells.append(
                    _cell(
                        connection.state(dof),
                        dof,
                        orphan=orphan,
                        relative=relative,
                        stiffness=_si_stiffness(connection, dof),
                        force=forces[member_id][offset + dof.position],
                        recovered=recovered.get(offset + dof.position),
                        node_rotation=node_rotation[dof.position % 3],
                    )
                )
            value = Vector6(ResultBasis.MEMBER_LOCAL, tuple(cells))  # type: ignore[arg-type]
            result.append(EndJump(member_id, end, (q.ex, q.ey, q.ez), value))
    return tuple(result)


def _cell(  # noqa: PLR0911, PLR0913 (un GDL: un retorno por fuente de cálculo)
    state: DofState,
    dof: Dof,
    *,
    orphan: bool,
    relative: tuple[Vec3, Vec3] | None,
    stiffness: float | None,
    force: float,
    recovered: float | None,
    node_rotation: float,
) -> Component:
    if state is DofState.RIGID:
        return _NA
    if orphan and not dof.is_translation:
        return _NA
    if dof in (Dof.UX, Dof.RX):  # en serie con A_ef o J_ef
        if state is DofState.RELEASED or stiffness is None:
            return _NA
        return _calc(-force / stiffness)
    if dof.is_translation or state is DofState.SPRING:  # a través del aux
        if relative is None:
            return _NC
        vector = relative[0] if dof.is_translation else relative[1]
        return _calc(vector.components()[dof.position % 3])
    if recovered is None:  # Ry, Rz liberados (-release*)
        return _NC
    return _calc(recovered - node_rotation)
