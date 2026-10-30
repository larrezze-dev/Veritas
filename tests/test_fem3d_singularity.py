"""Rigidez espacial y salvaguarda de singularidad (plan T-018, pieza 4; D3, D4, D6, DEV-1).

Unitarios de la matriz, de las fuerzas de empotramiento y de las coordenadas reducidas;
mecanismos 3D con su modo; márgenes de ``tol_pivot`` en estructuras legítimas esbeltas;
tiempo con unos 300 GDL; y contraste con el worker (marca ``solver``). Un cambio de signo
o un intercambio Iy/Iz lo detectan sin OpenSees las fórmulas cerradas (Iy ≠ Iz, tres
orientaciones) y, de forma independiente, el contraste con el worker.
"""

import dataclasses
import itertools
import math
import time
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import pytest

from fem3d_support import reference_displacements
from fem_support import AREA, E_PA, IY, IZ, Frame
from veritas.core.geometry.entities import Node
from veritas.core.loads.loads import LoadBasis
from veritas.core.sections.rectangular import torsion_constant
from veritas.core.supports.support import (
    Restraints,
    SupportBasis,
    SupportDof,
    build_restraints,
    fixed_restraints,
    pinned_restraints,
)
from veritas.core.units import Quantity
from veritas.fem.adapters import opensees_worker_3d
from veritas.fem.adapters.protocol import WorkerStatus
from veritas.fem.adapters.singularity import InvalidStiffnessError
from veritas.fem.runners.subprocess_runner import run_worker
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.protocol import (
    Six,
    SpatialAuxNode,
    SpatialBeamLoad,
    SpatialEndEqual,
    SpatialEndSpring,
    SpatialOrphanFix,
    SpatialRequest,
    SpatialWorkerResponse,
)
from veritas.fem.spatial.stiffness import (
    CHAIN_SOLVER_DOFS,
    beam_matrices,
    check_singularity,
    fixed_end_forces,
    free_directions,
    full_stiffness,
    reduced_coordinates,
    solver_free_dofs,
)
from veritas.fem.spatial.translator import translate_spatial

TOL = 1e-12  # tol_pivot de partida (D6).
# Pivote mínimo exigido a las legítimas: dos órdenes por encima de tol_pivot. Medido en la
# batería (pieza 4): 1,7e-8 en el voladizo oblicuo con L/h = 5000.
LEGIT_MIN = 1e-10
REL = 1e-9
REL_SLENDER = 1e-7  # DEV-1: L/h > 1000 frente a stiffness.py.
G_PA = 81e9
J = torsion_constant(0.2, 0.4)
OBLIQUE = (1.2, -0.7, 0.9)
NORMAL = (0.3, -0.5, 0.81)
WORKER_3D = Path(opensees_worker_3d.__file__).resolve()


def request_of(frame: Frame) -> SpatialRequest:
    return translate_spatial(frame.model, SpatialAnalysisConfiguration(frame.case_id)).request


def along(frame: Frame, start: Node, direction: Sequence[float], length: float) -> Node:
    norm = math.sqrt(sum(c * c for c in direction))
    p = start.position
    d = [c / norm * length for c in direction]
    return frame.node(p.x + d[0], p.z + d[2], y=p.y + d[1])


def oblique_cantilever(length: float = 2.0, direction: Sequence[float] = OBLIQUE) -> Frame:
    frame = Frame()
    a = frame.node(0.0, 0.0)
    b = along(frame, a, direction, length)
    frame.member(a, b, alpha=0.37)
    frame.support(a, fixed_restraints())
    return frame


def tip(frame: Frame) -> Node:
    return next(n for n in frame.model.nodes.values() if n.position.norm() > 0.0)


def point(request: SpatialRequest, tag: int) -> tuple[float, float, float]:
    node = next(n for n in request.nodes if n.tag == tag)
    return node.x, node.y, node.z


def loaded_cantilever() -> Frame:
    """Voladizo oblicuo con una carga nodal cualquiera (el traductor exige un caso no vacío)."""
    frame = oblique_cantilever()
    frame.nodal(tip(frame), fz=-1000.0)
    return frame


def local_to_global(frame: Frame, local: Sequence[float]) -> tuple[float, float, float]:
    member_id = next(iter(frame.model.members))
    axes = frame.model.axes(member_id)
    x, y, z = local
    return tuple(  # type: ignore[return-value]
        x * a + y * b + z * c
        for a, b, c in zip(
            axes.ex.components(), axes.ey.components(), axes.ez.components(), strict=True
        )
    )


def assert_close(
    actual: Sequence[float], expected: Sequence[float], scale: float, rel: float = REL
) -> None:
    for a, e in zip(actual, expected, strict=True):
        assert abs(a - e) <= rel * scale, (actual, expected)


# --- Matriz de la barra -----------------------------------------------------------------


def test_element_stiffness_is_symmetric_with_six_rigid_body_modes() -> None:
    frame = loaded_cantilever()
    request = request_of(frame)
    k = beam_matrices(request, request.beams[0]).stiffness
    scale = max(abs(v) for row in k for v in row)
    assert all(abs(k[i][j] - k[j][i]) <= 1e-15 * scale for i in range(12) for j in range(12))
    beam = request.beams[0]
    xi, xj = point(request, beam.node_i), point(request, beam.node_j)
    modes: list[list[float]] = []
    for axis in range(3):  # Traslación y giro rígidos: u = c + θ × x.
        t = [0.0, 0.0, 0.0]
        t[axis] = 1.0
        modes.append([*t, 0.0, 0.0, 0.0, *t, 0.0, 0.0, 0.0])
        theta = [0.0, 0.0, 0.0]
        theta[axis] = 1.0
        u = []
        for x in (xi, xj):
            u += [
                theta[1] * x[2] - theta[2] * x[1],
                theta[2] * x[0] - theta[0] * x[2],
                theta[0] * x[1] - theta[1] * x[0],
                *theta,
            ]
        modes.append(u)
    for mode in modes:
        force = [sum(row[j] * mode[j] for j in range(12)) for row in k]
        assert max(abs(f) for f in force) <= 1e-12 * scale


def test_beam_axes_match_domain_axes() -> None:
    frame = loaded_cantilever()
    request = request_of(frame)
    axes = beam_matrices(request, request.beams[0]).axes
    domain = frame.model.axes(next(iter(frame.model.members)))
    for got, expected in zip(axes, (domain.ex, domain.ey, domain.ez), strict=True):
        assert_close(got, expected.components(), 1.0, 1e-15)


def test_fixed_end_forces_are_in_equilibrium() -> None:
    """Σ fuerzas = −w·L y Σ momentos respecto a I = 0 con p0 (barra +X, L = 3)."""
    p0 = fixed_end_forces(SpatialBeamLoad(1, wy=2.0, wz=-5.0, wx=7.0), 3.0)
    assert p0[0] + p0[6] == pytest.approx(-21.0)
    assert p0[1] + p0[7] == pytest.approx(-6.0)
    assert p0[2] + p0[8] == pytest.approx(15.0)
    # Momentos respecto a I de las fuerzas de extremo y de la carga (resultante en L/2).
    assert p0[4] + p0[10] - 3.0 * p0[8] - 1.5 * (-5.0 * 3.0) == pytest.approx(0.0, abs=1e-12)
    assert p0[5] + p0[11] + 3.0 * p0[7] + 1.5 * (2.0 * 3.0) == pytest.approx(0.0, abs=1e-12)
    # Signo directo (revisión m3): las ecuaciones anteriores no lo distinguen.
    assert (p0[5], p0[11]) == pytest.approx((-2.0 * 9.0 / 12, 2.0 * 9.0 / 12))
    assert (p0[4], p0[10]) == pytest.approx((-5.0 * 9.0 / 12, 5.0 * 9.0 / 12))


# --- Referencia exacta frente a fórmulas cerradas (T-FEM3D-01 y 05) --------------------


@pytest.mark.parametrize("direction", [(0.0, 1.0, 0.0), OBLIQUE, (0.0, 0.0, -1.0)])
def test_reference_cantilever_matches_closed_form(direction: tuple[float, float, float]) -> None:
    length = 2.0
    frame = oblique_cantilever(length, direction)
    n, py, pz, t = 1500.0, 700.0, -1000.0, 300.0
    f = local_to_global(frame, (n, py, pz))
    m = local_to_global(frame, (t, 0.0, 0.0))
    frame.nodal(tip(frame), fx=f[0], fy=f[1], fz=f[2], mx=m[0], my=m[1], mz=m[2])
    request = request_of(frame)
    u = reference_displacements(request)[request.beams[0].node_j]
    axes = beam_matrices(request, request.beams[0]).axes
    local_u = [sum(a * v for a, v in zip(axis, u[:3], strict=True)) for axis in axes]
    local_r = [sum(a * v for a, v in zip(axis, u[3:], strict=True)) for axis in axes]
    ell = length
    expected_u = (
        n * ell / (E_PA * AREA),
        py * ell**3 / (3 * E_PA * IZ),
        pz * ell**3 / (3 * E_PA * IY),
    )
    expected_r = (
        t * ell / (G_PA * J),
        -pz * ell**2 / (2 * E_PA * IY),
        py * ell**2 / (2 * E_PA * IZ),
    )
    assert_close(local_u, expected_u, max(abs(v) for v in expected_u))
    assert_close(local_r, expected_r, max(abs(v) for v in expected_r))


def test_reference_beam_uniform_matches_closed_form_and_end_forces() -> None:
    """Hecho 9: flechas qL⁴/(8EI), qL²/(2EA) y pI = (−wxL, −wyL, −wzL, 0, +wzL²/2, −wyL²/2)."""
    length = 2.0
    frame = oblique_cantilever(length)
    wx, wy, wz = 400.0, -900.0, 1300.0
    member_id = next(iter(frame.model.members))
    frame.model.add_member_uniform_load(
        frame.case_id,
        member_id,
        basis=LoadBasis.LOCAL,
        qx=Quantity.of(wx, "N/m"),
        qy=Quantity.of(wy, "N/m"),
        qz=Quantity.of(wz, "N/m"),
    )
    request = request_of(frame)
    disp = reference_displacements(request)
    matrices = beam_matrices(request, request.beams[0])
    u_tip = disp[request.beams[0].node_j]
    local = [sum(a * v for a, v in zip(axis, u_tip[:3], strict=True)) for axis in matrices.axes]
    ell = length
    expected = (
        wx * ell**2 / (2 * E_PA * AREA),
        wy * ell**4 / (8 * E_PA * IZ),
        wz * ell**4 / (8 * E_PA * IY),
    )
    assert_close(local, expected, max(abs(v) for v in expected))
    u = [*disp[request.beams[0].node_i], *u_tip]
    tu = [sum(row[j] * u[j] for j in range(12)) for row in matrices.transformation]
    p0 = fixed_end_forces(request.beam_loads[0], ell)
    p = [sum(row[j] * tu[j] for j in range(12)) + p0[i] for i, row in enumerate(matrices.local)]
    expected_pi = (-wx * ell, -wy * ell, -wz * ell, 0.0, wz * ell**2 / 2, -wy * ell**2 / 2)
    assert_close(p[:6], expected_pi, max(abs(v) for v in expected_pi))


# --- Coordenadas reducidas -------------------------------------------------------------


def _orthonormal_complement(
    free: Sequence[Sequence[float]], constraints: Sequence[Sequence[float]]
) -> None:
    def dot(a: Sequence[float], b: Sequence[float]) -> float:
        return sum(x * y for x, y in zip(a, b, strict=True))

    assert len(free) + len(constraints) == 3
    for i, a in enumerate(free):
        assert dot(a, a) == pytest.approx(1.0, abs=1e-15)
        for b in free[i + 1 :]:
            assert abs(dot(a, b)) <= 1e-15
        for c in constraints:
            assert abs(dot(a, c)) <= 1e-15


def test_free_directions_without_constraints_are_global_axes() -> None:
    assert free_directions(()) == ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    assert free_directions(((0.0, 1.0, 0.0),)) == ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0))


@pytest.mark.parametrize(
    "constraints",
    [
        ((0.3, -0.5, 0.81),),
        ((1.0, 0.0, 0.0), (0.0, 0.6, 0.8)),
        ((0.6, 0.0, 0.8), (0.0, 1.0, 0.0), (0.8, 0.0, -0.6)),
    ],
    ids=["normal", "fix+cadena", "rango 3"],
)
def test_free_directions_are_an_orthonormal_complement(
    constraints: tuple[tuple[float, ...], ...],
) -> None:
    normalized = [tuple(c / math.sqrt(sum(x * x for x in v)) for c in v) for v in constraints]
    free = free_directions(normalized)  # type: ignore[arg-type]
    _orthonormal_complement(free, normalized)
    assert free == free_directions(normalized)  # type: ignore[arg-type]  # Determinista.


def test_redundant_constraints_are_internal_error() -> None:
    with pytest.raises(InvalidStiffnessError):
        free_directions(((0.0, 0.0, 1.0), (1e-9, 0.0, 1.0)))


MALFORMED = [
    (
        "área nula",
        lambda r: dataclasses.replace(r, beams=(dataclasses.replace(r.beams[0], area=0.0),)),
    ),
    (
        "vecxz paralelo a ex",
        lambda r: dataclasses.replace(
            r, beams=(dataclasses.replace(r.beams[0], vecxz=_axis_of(r)),)
        ),
    ),
    (
        "resorte negativo",
        lambda r: dataclasses.replace(
            r, springs=(dataclasses.replace(r.springs[0], stiffness=-1.0),)
        ),
    ),
    (
        "orient degenerado",
        lambda r: dataclasses.replace(
            r, springs=(dataclasses.replace(r.springs[0], axis_yp=r.springs[0].axis_x),)
        ),
    ),
    # Extremos (E2.3, R10): referencias rotas o valores nulos sin su marca.
    (
        "J nula sin marca",
        lambda r: dataclasses.replace(
            r, beams=(dataclasses.replace(r.beams[0], torsion_constant=0.0),)
        ),
    ),
    (
        "marca axial con A > 0",
        lambda r: dataclasses.replace(
            r, beams=(dataclasses.replace(r.beams[0], axial_released=True),)
        ),
    ),
    ("aux con maestro ajeno", lambda r: _with_aux(r, master=r.beams[0].node_i)),
    ("aux de barra inexistente", lambda r: _with_aux(r, beam=999)),
    ("aux con tag de nudo", lambda r: _with_aux(r, tag=r.beams[0].node_i)),
    ("aux con tag de tierra", lambda r: _with_aux(r, tag=r.springs[0].ground)),
    ("dos aux en un extremo", lambda r: _with_aux(r, twice=True)),
    ("equalDOF sin aux", lambda r: _with_aux(r, equals=(SpatialEndEqual(998, (1, 2, 3)),))),
    (
        "equalDOF repetido",
        lambda r: _with_aux(
            r, equals=(SpatialEndEqual(AUX_TAG, (1, 2, 3)), SpatialEndEqual(AUX_TAG, (4, 5, 6)))
        ),
    ),
    ("resorte de extremo nulo", lambda r: _with_aux(r, spring=0.0)),
    ("resorte de extremo sin aux", lambda r: _with_aux(r, spring=1e6, spring_aux=998)),
    ("equalDOF y penalización redundantes", lambda r: _with_aux(r, spring=1e6, penalty=True)),
    (
        "huérfano fuera del dominio",
        lambda r: dataclasses.replace(r, orphan_fixes=(SpatialOrphanFix(998, (1, 0, 0)),)),
    ),
]

AUX_TAG = 990


def _with_aux(  # noqa: PLR0913 (variantes del caso, por nombre)
    request: SpatialRequest,
    *,
    tag: int = AUX_TAG,
    master: int | None = None,
    beam: int | None = None,
    twice: bool = False,
    equals: tuple[SpatialEndEqual, ...] | None = None,
    spring: float | None = None,
    spring_aux: int = AUX_TAG,
    penalty: bool = False,
) -> SpatialRequest:
    """Aux válido en el extremo J de la barra (todo atado con ``equalDOF``) con una variante
    que lo rompe."""
    first = request.beams[0]
    aux = SpatialAuxNode(tag, first.node_j if master is None else master, beam or first.tag, "J")
    nodes = (aux, dataclasses.replace(aux, tag=tag + 1)) if twice else (aux,)
    links = (SpatialEndEqual(tag, (1, 2, 3, 4, 5, 6)),) if equals is None else equals
    springs: tuple[SpatialEndSpring, ...] = ()
    if spring is not None:
        axes = beam_matrices(request, first).axes
        springs = (SpatialEndSpring(991, spring_aux, 1, spring, axes[0], axes[1], penalty),)
    return dataclasses.replace(request, aux_nodes=nodes, end_equals=links, end_springs=springs)


def test_valid_aux_passes_validation() -> None:
    """Referencia de ``_with_aux``: sin variante, la petición es válida y no singular."""
    request = _with_aux(_tip_support((), {SupportDof.UX: 1e6}))
    assert not check_singularity(request, TOL).singular


@pytest.mark.parametrize(("name", "change"), MALFORMED, ids=[n for n, _ in MALFORMED])
def test_malformed_request_is_internal_error(name: str, change: object) -> None:
    """Defensa de la salvaguarda (revisión m5): el traductor nunca emite estas peticiones."""
    request = _tip_support((), {SupportDof.UX: 1e6})
    with pytest.raises(InvalidStiffnessError):
        check_singularity(change(request), TOL)  # type: ignore[operator]


@pytest.mark.parametrize(
    "change",
    [{"length": 0.0}, {"stiffness": 0.0}, {"tangent": NORMAL}],
    ids=["a nula", "kr nula", "tangente paralela"],
)
def test_malformed_chain_is_internal_error(change: dict[str, object]) -> None:
    request = _tip_support((SupportDof.UX,))
    chain = dataclasses.replace(request.chains[0], **change)  # type: ignore[arg-type]
    with pytest.raises(InvalidStiffnessError):
        check_singularity(dataclasses.replace(request, chains=(chain,)), TOL)


def _axis_of(request: SpatialRequest) -> tuple[float, float, float]:
    beam = request.beams[0]
    xi, xj = point(request, beam.node_i), point(request, beam.node_j)
    return xj[0] - xi[0], xj[1] - xi[1], xj[2] - xi[2]


@pytest.mark.parametrize(
    "dof", [SupportDof.UY, SupportDof.UZ, SupportDof.RX, SupportDof.RY, SupportDof.RZ]
)
def test_spring_direction_follows_support_basis(dof: SupportDof) -> None:
    """k·b·bᵀ con b = fila (dof − 1) % 3 de la base del apoyo, en su terna (revisión O5)."""
    k = 3.0e6
    request = _tip_support((), {dof: k})
    plain, index = full_stiffness(dataclasses.replace(request, springs=()))
    stiffness, _ = full_stiffness(request)
    b = SupportBasis.inclined(NORMAL, (1.0, 0.0, 0.0)).matrix()[dof.value % 3]
    base = 6 * index[request.springs[0].node] + (0 if dof.is_translation else 3)
    for i in range(3):
        for j in range(3):
            added = stiffness[base + i][base + j] - plain[base + i][base + j]
            assert added == pytest.approx(k * b[i] * b[j], rel=1e-12, abs=1e-6)


def _tip_support(
    fixed: Sequence[SupportDof], springs: dict[SupportDof, float] | None = None
) -> SpatialRequest:
    frame = oblique_cantilever()
    frame.nodal(tip(frame), fz=-1000.0)
    basis = SupportBasis.inclined((0.3, -0.5, 0.81), (1.0, 0.0, 0.0))
    quantities = {
        dof: Quantity.of(k, "N/m" if dof.is_translation else "N*m/rad")
        for dof, k in (springs or {}).items()
    }
    frame.support(tip(frame), build_restraints(fixed, quantities), basis)
    return request_of(frame)


def test_chain_is_exact_tangent_subspace() -> None:
    request = _tip_support((SupportDof.UX,))
    (chain,) = request.chains
    labels = [label for label, _ in reduced_coordinates(request) if label.node == chain.node]
    assert [label.component for label in labels] == ["t1", "t2", "Rx", "Ry", "Rz"]
    _orthonormal_complement([label.direction for label in labels[:2]], [chain.normal])
    assert solver_free_dofs(request) == 6 + CHAIN_SOLVER_DOFS


def test_penalty_rotation_is_exact_constraint_and_not_stiffness() -> None:
    request = _tip_support((SupportDof.RX, SupportDof.RY))
    assert all(spring.penalty for spring in request.springs)
    stiffness, _ = full_stiffness(request)
    labels = [
        label for label, _ in reduced_coordinates(request) if label.node == request.springs[0].node
    ]
    assert [label.component for label in labels] == ["Ux", "Uy", "Uz", "r1"]
    third = request.springs[0].axis_x, request.springs[0].axis_yp
    normal = (
        third[0][1] * third[1][2] - third[0][2] * third[1][1],
        third[0][2] * third[1][0] - third[0][0] * third[1][2],
        third[0][0] * third[1][1] - third[0][1] * third[1][0],
    )
    assert_close(labels[3].direction, normal, 1.0, 1e-15)  # Solo el giro según z′ queda libre.
    plain = dataclasses.replace(request, springs=())
    assert stiffness == full_stiffness(plain)[0]  # Sin rigidez añadida.


def test_oriented_springs_add_k_b_bt() -> None:
    k = 3.0e6
    request = _tip_support((), {SupportDof.UX: k})
    plain, index = full_stiffness(dataclasses.replace(request, springs=()))
    stiffness, _ = full_stiffness(request)
    b = (0.3, -0.5, 0.81)
    norm = math.sqrt(sum(c * c for c in b))
    b = (b[0] / norm, b[1] / norm, b[2] / norm)
    base = 6 * index[request.springs[0].node]
    for i in range(3):
        for j in range(3):
            assert stiffness[base + i][base + j] - plain[base + i][base + j] == pytest.approx(
                k * b[i] * b[j], rel=1e-12, abs=1e-6
            )


# --- Mecanismos 3D ---------------------------------------------------------------------


def _simply_supported() -> Frame:
    frame = Frame()
    a, b = frame.node(0.0, 0.0), frame.node(5.0, 0.0)
    frame.member(a, b)
    frame.support(a, pinned_restraints())
    frame.support(b, pinned_restraints())
    frame.nodal(b, fz=1.0)
    return frame


def _collinear() -> Frame:
    frame = Frame()
    a, b, c = frame.node(0.0, 0.0), frame.node(3.0, 0.0, y=1.0), frame.node(6.0, 0.0, y=2.0)
    frame.member(a, b)
    frame.member(b, c)
    frame.support(a, pinned_restraints())
    frame.support(c, pinned_restraints())
    frame.nodal(b, fz=1.0)
    return frame


def _portal_overturning() -> Frame:
    """Pórtico en XZ con bases articuladas en traslación: vuelco fuera del plano (Obs. 15)."""
    frame = Frame()
    a, b, c, d = frame.node(0, 0), frame.node(0, 3), frame.node(4, 3), frame.node(4, 0)
    frame.member(a, b)
    frame.member(b, c)
    frame.member(d, c)
    frame.support(a, pinned_restraints())
    frame.support(d, pinned_restraints())
    frame.nodal(b, fx=1.0)
    return frame


def _unsupported() -> Frame:
    frame = Frame()
    a, b = frame.node(0.0, 0.0), frame.node(2.0, 1.0, y=1.0)
    frame.member(a, b)
    frame.nodal(b, fz=1.0)
    return frame


def _loose() -> Frame:
    frame = Frame()
    a, b = frame.node(0.0, 0.0), frame.node(2.0, 0.0)
    c, d = frame.node(5.0, 0.0), frame.node(6.0, 1.0, y=2.0)
    frame.member(a, b)
    frame.member(c, d)
    frame.support(a, fixed_restraints())
    frame.nodal(b, fz=1.0)
    return frame


def _slender_pinned_continuous() -> Frame:
    """Continua oblicua L/h = 5000 con todos los apoyos articulados: torsión alrededor de
    su eje (revisión M1)."""
    return _slender_continuous(5000, first=pinned_restraints())


def _slender_simply_supported() -> Frame:
    frame = oblique_cantilever(0.4 * 5000)
    frame.model.remove_support(next(iter(frame.model.supports.values())).node_id)
    for node in list(frame.model.nodes.values()):
        frame.support(node, pinned_restraints())
    frame.nodal(tip(frame), fz=-1.0)
    return frame


def _tangent_slide() -> Frame:
    """Barra oblicua esbelta sobre dos rodillos inclinados de la misma normal, con los giros
    fijos en uno: la traslación tangente (t1, t2) queda libre."""
    frame = oblique_cantilever(0.4 * 5000)
    origin = next(iter(frame.model.supports.values())).node_id
    frame.model.remove_support(origin)
    basis = SupportBasis.inclined(NORMAL, (1.0, 0.0, 0.0))
    fixed = (SupportDof.UX, SupportDof.RX, SupportDof.RY, SupportDof.RZ)
    frame.support(frame.model.node(origin), build_restraints(fixed), basis)
    frame.support(tip(frame), build_restraints((SupportDof.UX,)), basis)
    frame.nodal(tip(frame), fz=-1.0)
    return frame


MECHANISMS = [
    ("biapoyada sin Rx", _simply_supported, 1, {"Rx"}),
    ("dos barras colineales sin torsión", _collinear, 1, {"Rx", "Ry"}),
    ("vuelco fuera del plano", _portal_overturning, 1, {"Uy", "Rx"}),
    ("sin apoyos", _unsupported, 6, None),
    ("subestructura suelta", _loose, 6, None),
    ("continua oblicua articulada L/h=5000", _slender_pinned_continuous, 1, {"Rx", "Ry", "Rz"}),
    ("biapoyada oblicua L/h=5000", _slender_simply_supported, 1, {"Rx", "Ry", "Rz"}),
    ("deslizamiento tangente L/h=5000", _tangent_slide, 2, {"t1", "t2"}),
]
# Pivote máximo medido en los mecanismos: 1,5e-15 (revisión M1). Se exige ≤ tol_pivot/100.
MECHANISM_MAX = TOL / 100


@pytest.mark.parametrize(
    ("name", "build", "nullity", "components"), MECHANISMS, ids=[m[0] for m in MECHANISMS]
)
def test_mechanisms_are_singular_with_conclusive_mode(
    name: str, build: object, nullity: int, components: set[str] | None
) -> None:
    report = check_singularity(request_of(build()), TOL)  # type: ignore[operator]
    assert report.singular, name
    assert report.min_pivot is not None
    assert report.min_pivot <= MECHANISM_MAX, report.min_pivot
    assert report.mode is not None
    assert report.mode.conclusive
    assert report.mode.nullity == nullity
    if components is not None:  # Componentes con participación apreciable.
        involved = {
            label.component
            for label, p in zip(report.labels, report.mode.participation, strict=True)
            if p > 1e-3
        }
        assert involved == components


def test_torsion_mode_is_the_rx_of_both_ends() -> None:
    report = check_singularity(request_of(_simply_supported()), TOL)
    assert report.mode is not None
    rx = [
        p
        for label, p in zip(report.labels, report.mode.participation, strict=True)
        if label.component == "Rx"
    ]
    assert rx == pytest.approx([0.5, 0.5], abs=1e-12)


def test_mode_is_reproducible_over_20_rebuilds() -> None:
    """Misma petición reconstruida 20 veces desde el mismo modelo (los tags dependen de los
    UUID, así que modelos distintos numeran distinto)."""
    frame = _collinear()
    first = check_singularity(request_of(frame), TOL)
    for _ in range(20):
        report = check_singularity(request_of(frame), TOL)
        assert report.labels == first.labels
        assert report.mode == first.mode


# --- Estructuras legítimas: margen frente a tol_pivot ----------------------------------


def _slender_continuous(ratio: float, first: Restraints | None = None) -> Frame:
    frame = Frame()
    span = 0.4 * ratio
    nodes = [frame.node(0.0, 0.0)]
    for _ in range(10):
        nodes.append(along(frame, nodes[-1], OBLIQUE, span))
    for a, b in itertools.pairwise(nodes):
        frame.member(a, b, alpha=0.37)
    frame.support(nodes[0], first or fixed_restraints())
    for node in nodes[2::2]:
        frame.support(node, pinned_restraints())
    frame.nodal(nodes[1], fz=-1000.0)
    return frame


def _slender_cantilever(ratio: float) -> Frame:
    frame = oblique_cantilever(0.4 * ratio)
    frame.nodal(tip(frame), fz=-1000.0, fy=300.0)
    return frame


def _grid(nx: int = 5, ny: int = 5, levels: int = 2) -> Frame:
    """Pórtico espacial de nudos rígidos con bases empotradas: 300 GDL reducidos con 5×5×2."""
    frame = Frame()
    nodes: dict[tuple[int, int, int], Node] = {}
    for k in range(levels + 1):
        for i in range(nx):
            for j in range(ny):
                node = nodes[i, j, k] = frame.node(4.0 * i, 3.0 * k, y=5.0 * j)
                if k == 0:
                    frame.support(node, fixed_restraints())
                    continue
                frame.member(nodes[i, j, k - 1], node)
                if i > 0:
                    frame.member(nodes[i - 1, j, k], node)
                if j > 0:
                    frame.member(nodes[i, j - 1, k], node)
    frame.nodal(nodes[nx - 1, ny - 1, levels], fx=1000.0, fy=-500.0)
    return frame


LEGITIMATE = [
    *((f"voladizo oblicuo L/h={r}", lambda r=r: _slender_cantilever(r)) for r in (10, 1000, 5000)),
    *((f"continua oblicua L/h={r}", lambda r=r: _slender_continuous(r)) for r in (10, 1000, 5000)),
    ("rodillo inclinado", lambda: _roller_frame(2.0)),
    ("pórtico espacial", _grid),
]


def _roller_frame(length: float) -> Frame:
    frame = oblique_cantilever(length)
    frame.support(
        tip(frame),
        build_restraints((SupportDof.UX,)),
        SupportBasis.inclined((0.3, -0.5, 0.81), (1.0, 0.0, 0.0)),
    )
    frame.nodal(tip(frame), fz=-1000.0, fx=200.0)
    return frame


@pytest.mark.parametrize(("name", "build"), LEGITIMATE, ids=[n for n, _ in LEGITIMATE])
def test_legitimate_structures_pass_with_margin(name: str, build: object) -> None:
    report = check_singularity(request_of(build()), TOL)  # type: ignore[operator]
    assert not report.singular, name
    assert report.min_pivot is not None
    assert report.min_pivot >= LEGIT_MIN, report.min_pivot


def test_safeguard_time_with_300_dofs() -> None:
    """D6: unos 10 s como referencia; medido ≈ 0,3 s legítimo y ≈ 1,3 s con mecanismo.
    Cota holgada para no depender de la máquina de la CI."""
    for build in (_grid, _grid_unsupported):
        request = request_of(build())
        start = time.perf_counter()
        report = check_singularity(request, TOL)
        assert report.free_dofs >= 300
        assert time.perf_counter() - start < 30.0


def _grid_unsupported() -> Frame:
    """El mismo pórtico sin apoyos: mecanismo de nulidad 6 (factoriza dos veces)."""
    frame = _grid()
    for support in list(frame.model.supports.values()):
        frame.model.remove_support(support.node_id)
    return frame


# --- Contraste con el worker (solver) --------------------------------------------------


def _run(request: SpatialRequest) -> SpatialWorkerResponse:
    outcome = run_worker(request.to_json(), 120.0, worker=WORKER_3D)
    assert outcome.response_text is not None, outcome.stderr
    response = SpatialWorkerResponse.from_json(outcome.response_text)
    assert response.status == WorkerStatus.OK, response.message
    return response


def _max_error(request: SpatialRequest) -> float:
    """Error relativo máximo worker − referencia, por escalas de traslación y de giro."""
    reference = reference_displacements(request)
    worker: dict[int, Six] = {tag: disp for tag, disp, _ in _run(request).node_results}
    longest = max(beam_matrices(request, beam).length for beam in request.beams)
    trans = max(abs(v) for u in reference.values() for v in u[:3])
    rot = max(*(abs(v) for u in reference.values() for v in u[3:]), trans / longest)
    error = 0.0
    for tag, expected in reference.items():
        got = worker[tag]
        error = max(
            error, *(abs(g - e) / trans for g, e in zip(got[:3], expected[:3], strict=True))
        )
        error = max(error, *(abs(g - e) / rot for g, e in zip(got[3:], expected[3:], strict=True)))
    return error


def _loaded_cantilever(direction: Sequence[float]) -> Frame:
    frame = oblique_cantilever(2.0, direction)
    frame.nodal(tip(frame), fx=1100.0, fy=-700.0, fz=-1000.0, mx=300.0, my=-200.0, mz=150.0)
    member_id = next(iter(frame.model.members))
    for basis in (LoadBasis.LOCAL, LoadBasis.GLOBAL):
        frame.model.add_member_uniform_load(
            frame.case_id,
            member_id,
            basis=basis,
            qx=Quantity.of(400.0, "N/m"),
            qy=Quantity.of(-900.0, "N/m"),
            qz=Quantity.of(1300.0, "N/m"),
        )
    return frame


def _spring_tip() -> Frame:
    frame = _loaded_cantilever(OBLIQUE)
    springs = {SupportDof.UY: Quantity.of(2e6, "N/m"), SupportDof.RZ: Quantity.of(5e5, "N*m/rad")}
    frame.support(
        tip(frame),
        build_restraints((SupportDof.UX,), springs),
        SupportBasis.inclined((0.3, -0.5, 0.81), (1.0, 0.0, 0.0)),
    )
    return frame


def _portal_with_self_weight() -> Frame:
    frame = Frame()
    a, b, c, d = frame.node(0, 0), frame.node(0, 3), frame.node(4, 3), frame.node(4, 0)
    frame.member(a, b)
    frame.member(b, c)
    frame.member(d, c)
    frame.support(a, fixed_restraints())
    frame.support(d, fixed_restraints())
    frame.nodal(b, fx=5000.0, fy=10000.0)
    frame.model.set_self_weight(frame.case_id)
    return frame


UX, UY, UZ = SupportDof.UX, SupportDof.UY, SupportDof.UZ
# Base con z′ = +Y exacto y x′ oblicua: UZ′ da un fix en Uy y UX′ una cadena.
FIX_AND_CHAIN = SupportBasis.inclined((0.6, 0.0, 0.8), (0.0, 1.0, 0.0))
SLIDER = SupportBasis.inclined(NORMAL, (1.0, 0.0, 0.0))

CONTRAST = [
    ("voladizo +Y", lambda: _loaded_cantilever((0.0, 1.0, 0.0))),
    ("voladizo oblicuo", lambda: _loaded_cantilever(OBLIQUE)),
    ("voladizo vertical -Z", lambda: _loaded_cantilever((0.0, 0.0, -1.0))),
    ("cadena y resortes orientados", _spring_tip),
    ("pórtico con peso propio", _portal_with_self_weight),
    ("pórtico espacial 300 GDL", _grid),
    ("fix y cadena en el mismo nudo", lambda: _tip_constraint(FIX_AND_CHAIN, (UX, UZ))),
    ("deslizador con dos cadenas", lambda: _tip_constraint(SLIDER, (UX, UY))),
]


def _tip_constraint(basis: SupportBasis, fixed: tuple[SupportDof, ...]) -> Frame:
    frame = _loaded_cantilever(OBLIQUE)
    frame.support(tip(frame), build_restraints(fixed), basis)
    return frame


@pytest.mark.solver
@pytest.mark.parametrize(("name", "build"), CONTRAST, ids=[n for n, _ in CONTRAST])
def test_worker_matches_exact_assembly(name: str, build: object) -> None:
    assert _max_error(request_of(build())) <= REL, name  # type: ignore[operator]


@pytest.mark.solver
def test_dev1_slender_margin() -> None:
    """DEV-1: L/h = 5000 girada, con rodillo inclinado y carga de barra, frente a
    stiffness.py con 1e-7. Medido 7,1e-9 (margen 14×, anotado en el plan); un margen menor
    de 10× se informa, no es un fallo de la suite (revisión m2)."""
    frame = _roller_frame(0.4 * 5000)
    member_id = next(iter(frame.model.members))
    frame.model.add_member_uniform_load(frame.case_id, member_id, qz=Quantity.of(-50.0, "N/m"))
    assert _max_error(request_of(frame)) <= REL_SLENDER


@pytest.mark.solver
def test_slender_continuous_matches_exact_assembly() -> None:
    assert _max_error(request_of(_slender_continuous(5000))) <= REL_SLENDER


# --- Batería con penalización (D6) y Obs. 8 --------------------------------------------
# Error medido con penalty_factor = 1e8 (por defecto) y alphas = D: ≤ 2,1e-8, margen de
# unas 47× sobre tol_penalty = 1e-6. Con 1e9-1e10 domina el redondeo (hasta 2,3e-6); con
# alphas = máx(D, k_pen) no mejora (hasta 4,5e-8). Valores en plans/T-018.md.

PENALTY_BASIS = SupportBasis.inclined(NORMAL, (1.0, 0.0, 0.0))
TOL_PENALTY = SpatialAnalysisConfiguration(uuid4()).tol_penalty


def _penalty_tip(length: float, fixed: tuple[SupportDof, ...]) -> Frame:
    frame = oblique_cantilever(length)
    frame.nodal(tip(frame), fx=1100.0, fy=-700.0, fz=-1000.0, mx=300.0, my=-200.0, mz=150.0)
    frame.support(tip(frame), build_restraints(fixed), PENALTY_BASIS)
    return frame


def _penalty_frame(ratio: float) -> Frame:
    """Dos barras oblicuas: base con traslaciones y dos giros oblicuos fijos, otra empotrada."""
    frame = Frame()
    length = 0.4 * ratio
    a = frame.node(0.0, 0.0)
    b = along(frame, a, OBLIQUE, length)
    c = along(frame, b, (0.2, 1.0, -0.3), length / 2)
    frame.member(a, b, alpha=0.37)
    frame.member(b, c, alpha=-0.2)
    fixed = (SupportDof.UX, SupportDof.UY, SupportDof.UZ, SupportDof.RX, SupportDof.RY)
    frame.support(a, build_restraints(fixed), PENALTY_BASIS)
    frame.support(c, fixed_restraints())
    frame.nodal(b, fx=1000.0, fz=-3000.0, my=500.0)
    return frame


RX, RY = SupportDof.RX, SupportDof.RY
PENALTY = [
    ("un giro", lambda: _penalty_tip(2.0, (RX,))),
    ("dos giros", lambda: _penalty_tip(2.0, (RX, RY))),
    ("traslación y dos giros", lambda: _penalty_tip(2.0, (UX, RX, RY))),
    ("dos giros L/h=1000", lambda: _penalty_tip(400.0, (RX, RY))),
    ("dos giros L/h=5000", lambda: _penalty_tip(2000.0, (RX, RY))),
    *((f"dos barras L/h={r}", lambda r=r: _penalty_frame(r)) for r in (10, 1000, 5000)),
]


@pytest.mark.solver
@pytest.mark.parametrize(("name", "build"), PENALTY, ids=[n for n, _ in PENALTY])
def test_penalty_runs_within_tol_penalty(name: str, build: object) -> None:
    request = request_of(build())  # type: ignore[operator]
    assert any(spring.penalty for spring in request.springs), name
    assert _max_error(request) <= TOL_PENALTY


def _long_chain(long: float, fixed: tuple[SupportDof, ...]) -> Frame:
    """Obs. 8: cadena con a = barra más larga del modelo ≫ barras del nudo (0,5 m)."""
    frame = Frame()
    a = frame.node(0.0, 0.0)
    b = along(frame, a, OBLIQUE, 0.5)
    c = along(frame, a, (0.2, 1.0, -0.3), long)
    frame.member(a, b, alpha=0.37)
    frame.member(a, c)
    frame.support(c, fixed_restraints())
    frame.support(b, build_restraints(fixed), PENALTY_BASIS)
    frame.support(a, build_restraints((UZ,)), PENALTY_BASIS)
    frame.nodal(b, fx=1100.0, fy=-700.0, fz=-1000.0, mx=300.0)
    frame.nodal(a, fz=-500.0, my=40.0)
    return frame


@pytest.mark.solver
@pytest.mark.parametrize("long", [20.0, 400.0, 2000.0])
@pytest.mark.parametrize("fixed", [(UX,), (UX, UY)], ids=["rodillo", "deslizador"])
def test_chain_much_longer_than_node_bars_is_exact(
    long: float, fixed: tuple[SupportDof, ...]
) -> None:
    """Medido ≤ 2,8e-11 con a/L_nudo = 4000 (margen ≥ 36× sobre 1e-9)."""
    request = request_of(_long_chain(long, fixed))
    assert request.chains[0].length == pytest.approx(long)
    assert _max_error(request) <= REL
