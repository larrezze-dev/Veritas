"""Contraste traductor + worker con extremos no rígidos (plan T-018, pieza E2.2).

Modelos del dominio con el montaje de ``test_fem3d_solver_ends`` (I empotrado, B1 de I a J
con el extremo J no rígido, B2 rígida de J a K empotrado, carga local en B1 y carga nodal en
J), traducidos con ``translate_spatial`` y ejecutados con el worker, saltándose la salvaguarda
y la guarda E2.2-d. Referencia: ``EndModel.reference`` con los vecxz y la J del dominio.
"""

import pytest

from fem_support import Frame
from test_fem3d_solver import LOAD, OBLIQUE, assert_disp, node, run
from test_fem3d_solver_ends import RX, RY, RZ, EndModel, Extras, W, assert_force
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.loads.loads import LoadBasis
from veritas.core.sections.rectangular import torsion_constant
from veritas.core.supports.support import fixed_restraints
from veritas.core.units import Quantity
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.protocol import Vec3 as Triple
from veritas.fem.spatial.translator import translate_spatial

pytestmark = pytest.mark.solver

TORSION = torsion_constant(0.2, 0.4)
HORIZONTAL_OBLIQUE = (1.2, -0.7, 0.0)
K_ROT = 3e7
K_TORSION = 2e6
UY = 1  # Índice local de Uy en la 12×12.

CASES = [
    pytest.param(
        OBLIQUE, 0.37, EndConnection.pinned(), {6 + RY: None, 6 + RZ: None}, id="articulado"
    ),
    pytest.param(
        HORIZONTAL_OBLIQUE,
        0.0,
        EndConnection.rigid().with_dof(Dof.RZ, DofState.SPRING, Quantity.of(K_ROT, "N*m/rad")),
        {6 + RZ: K_ROT},
        id="Rz resorte coordenado",
    ),
    pytest.param(
        OBLIQUE,
        0.37,
        EndConnection.rigid().with_dof(Dof.RX, DofState.SPRING, Quantity.of(K_TORSION, "N*m/rad")),
        {6 + RX: K_TORSION},
        id="Rx resorte en serie",
    ),
    pytest.param(
        (1.0, 0.0, 0.0),
        0.0,
        EndConnection.rigid().with_dof(Dof.UY, DofState.RELEASED),
        {6 + UY: None},
        id="Uy libre alineado",
    ),
    pytest.param(
        (1.0, 0.0, 0.0),
        0.0,
        EndConnection.rigid()
        .with_dof(Dof.RY, DofState.RELEASED)
        .with_dof(Dof.RZ, DofState.SPRING, Quantity.of(K_ROT, "N*m/rad")),
        {6 + RY: None, 6 + RZ: K_ROT},
        id="R3 Ry libre y Rz resorte",
    ),
]


@pytest.mark.parametrize(("direction", "alpha", "connection", "extras"), CASES)
def test_translated_end_matches_reference(
    direction: Triple, alpha: float, connection: EndConnection, extras: Extras
) -> None:
    model = EndModel(direction, alpha, torsion=TORSION)
    frame = Frame()
    i, j, k = (frame.node(p[0], p[2], y=p[1]) for p in ((0.0, 0.0, 0.0), model.xj, model.xk))
    b1 = frame.member(i, j, alpha=alpha)
    b2 = frame.member(j, k)
    frame.support(i, fixed_restraints())
    frame.support(k, fixed_restraints())
    names = ("fx", "fy", "fz", "mx", "my", "mz")
    frame.nodal(j, **dict(zip(names, LOAD, strict=True)))
    wy, wz, wx = W
    frame.model.add_member_uniform_load(
        frame.case_id,
        b1.id,
        basis=LoadBasis.LOCAL,
        qx=Quantity.of(wx, "N/m"),
        qy=Quantity.of(wy, "N/m"),
        qz=Quantity.of(wz, "N/m"),
    )
    frame.model.set_member_end(b1.id, MemberEnd.J, connection)
    request = translate_spatial(frame.model, SpatialAnalysisConfiguration(frame.case_id)).request
    beams = {beam.entity_id: beam for beam in request.beams}
    assert beams[str(b1.id)].vecxz == pytest.approx(model.vecxz, abs=1e-15)
    reference_model = EndModel(direction, alpha, torsion=TORSION, b2_vecxz=beams[str(b2.id)].vecxz)
    tag_j = next(n.tag for n in request.nodes if n.entity_id == str(j.id))
    response = run(request)
    ref = reference_model.reference(extras)
    assert_disp(node(response, tag_j)[0], ref.u, reference_model.length)
    force = next(f for t, f in response.beam_forces if t == beams[str(b1.id)].tag)
    released = [
        index for index, stiffness in extras.items() if stiffness is None and index % 6 >= 3
    ]
    assert_force(force, ref.force, zero=released)
