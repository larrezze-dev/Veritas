"""Asientos impuestos en el adaptador espacial (plan T-016, E2.2).

Referencias de Euler–Bernoulli y de torsión de Saint-Venant con rigidez constante, en forma
cerrada y en fracciones (sin el solver). δ y θ son los valores prescritos con signo (ADR-001,
base global). La reacción es la acción del apoyo sobre la estructura (ADR-001 §2).

Signos por cuerpo libre, barra según +X:

- Flexión en XY (Iz): v = Uy y φ = dUy/dX = +Rz, de modo que Fy = F_v y Mz = M_φ. Con
  v_J = δ en una biempotrada, Fy_J = 12EIzδ/L³ y Mz_I = Mz_J = −6EIzδ/L² (en XZ el signo
  del momento se invierte, porque allí φ = −Ry: T-ASI-01 del plano).
- Torsión: con Rx_J = θ, Mx_J = +GJθ/L y Mx_I = −GJθ/L; T = +GJθ/L (giro creciente con x).
- Barra horizontal oblicua ex = (3, 4, 0)/5: ez = +Z y ey = ez × ex = (−4, 3, 0)/5. Un
  asiento Uz = δ en J da Fz_J = 12EIyδ/L³ y un momento 6EIyδ/L² según +ey en ambos extremos;
  ΣM_I = 12EIyδ/L²·ey + L·ex × (12EIyδ/L³·ez) = 0, porque ex × ez = −ey.
"""

import dataclasses
import itertools
import json
from fractions import Fraction
from pathlib import Path
from typing import Any

import pytest

from fem_support import E_PA, IY, IZ, Frame
from test_fem3d_end_penalty import grid_frame
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.diagnostics import DiagnosticCode, InputValueError
from veritas.core.geometry.entities import Node
from veritas.core.supports.support import (
    SupportBasis,
    SupportDof,
    build_restraints,
    fixed_restraints,
    pinned_restraints,
)
from veritas.core.units import Quantity
from veritas.fem.adapters import opensees_worker_3d
from veritas.fem.adapters.protocol import ProtocolError
from veritas.fem.benchmarks import SPATIAL_SPECS, _Case
from veritas.fem.results.run import RunStatus
from veritas.fem.runners.subprocess_runner import run_worker
from veritas.fem.spatial import WORKER_3D_PATH, analyze_spatial
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.postconditions import residual_check, settlement_check
from veritas.fem.spatial.protocol import (
    PROTOCOL_VERSION,
    SpatialOrphanFix,
    SpatialRequest,
    SpatialSettlement,
)
from veritas.fem.spatial.results import SpatialResults, normalize_spatial
from veritas.fem.spatial.translator import translate_spatial

pytestmark = pytest.mark.solver

D = DiagnosticCode
TOL = 1e-9
L = Fraction(4)
EIY = Fraction(E_PA) * Fraction(IY)
EIZ = Fraction(E_PA) * Fraction(IZ)
DELTA = Fraction(-1, 100)  # m
THETA = Fraction(2, 1000)  # rad
RELEASED_RZ = EndConnection.rigid().with_dof(Dof.RZ, DofState.RELEASED)
WORKER_V3 = Path(__file__).parent / "data" / "opensees_worker_3d_v3.py"
WORKER_V4 = Path(__file__).parent / "data" / "opensees_worker_3d_v4.py"
FX, FY, FZ, MX, MY, MZ = range(6)


def m(value: float | Fraction) -> Quantity:
    return Quantity.of(float(value), "m")


def rad(value: float | Fraction) -> Quantity:
    return Quantity.of(float(value), "rad")


def close(actual: object, expected: Fraction | float, scale: Fraction | float) -> None:
    assert isinstance(actual, float)
    assert abs(actual - float(expected)) <= TOL * abs(float(scale)), (actual, float(expected))


def line(frame: Frame, *points: tuple[float, float, float]) -> list[Node]:
    """Nudos (x, y, z) y barras consecutivas (al menos dos elementos por barra)."""
    nodes = [frame.node(x, z, y=y) for x, y, z in points]
    for a, b in itertools.pairwise(nodes):
        frame.member(a, b)
    return nodes


def straight(frame: Frame) -> list[Node]:
    return line(frame, (0, 0, 0), (2, 0, 0), (4, 0, 0))


def config(frame: Frame) -> SpatialAnalysisConfiguration:
    return SpatialAnalysisConfiguration(frame.case_id)


def solve(frame: Frame) -> SpatialResults:
    run = analyze_spatial(frame.model, config(frame))
    assert run.status is RunStatus.SUCCEEDED, [d.message_key for d in run.diagnostics]
    return normalize_spatial(run, frame.model)


def reaction(results: SpatialResults, node: Node) -> tuple[float | None, ...]:
    (found,) = (r for r in results.reactions if r.node_id == node.id)
    return found.global_.values()


def displacement(results: SpatialResults, node: Node) -> tuple[float | None, ...]:
    (found,) = (n for n in results.nodes if n.node_id == node.id)
    return found.displacement.values()


# --- Referencias 3D ----------------------------------------------------------------------


def test_t_asi_01_in_uy_bends_about_z() -> None:
    frame = Frame()
    a, mid, b = straight(frame)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.model.add_support_settlement(frame.case_id, b.id, uy=m(DELTA))
    results = solve(frame)
    force, moment = 12 * EIZ * DELTA / L**3, 6 * EIZ * DELTA / L**2
    close(reaction(results, b)[FY], force, force)
    close(reaction(results, a)[FY], -force, force)
    close(reaction(results, a)[MZ], -moment, moment)
    close(reaction(results, b)[MZ], -moment, moment)
    close(displacement(results, b)[1], DELTA, DELTA)
    close(displacement(results, mid)[MZ], 3 * DELTA / (2 * L), DELTA / L)  # Rz = +φ


def test_t_asi_01_in_uz_matches_planar_reference() -> None:
    frame = Frame()
    a, _mid, b = straight(frame)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    results = solve(frame)
    force, moment = 12 * EIY * DELTA / L**3, 6 * EIY * DELTA / L**2
    close(reaction(results, b)[FZ], force, force)
    close(reaction(results, a)[FZ], -force, force)
    close(reaction(results, a)[MY], moment, moment)
    close(reaction(results, b)[MY], moment, moment)


@pytest.mark.parametrize(
    ("component", "inertia", "force_index", "moment_index", "sign"),
    [("rz", IZ, FY, MZ, 1), ("ry", IY, FZ, MY, -1)],
    ids=["Rz", "Ry"],
)
def test_rotation_settlement_with_rigid_ends(
    component: str, inertia: float, force_index: int, moment_index: int, sign: int
) -> None:
    """Revisión E2.2, M2: giro asentado θ en J de una biempotrada.

    Rz (φ = +Rz): Mz_J = 4EIθ/L, Mz_I = 2EIθ/L, Fy_J = −6EIθ/L², Fy_I = +6EIθ/L².
    Ry (φ = −Ry): My_J = 4EIθ/L, My_I = 2EIθ/L, Fz_J = +6EIθ/L², Fz_I = −6EIθ/L².
    """
    frame = Frame()
    a, _mid, b = straight(frame)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.model.add_support_settlement(frame.case_id, b.id, **{component: rad(THETA)})
    results = solve(frame)
    ei = Fraction(E_PA) * Fraction(inertia)
    moment, shear = 4 * ei * THETA / L, 6 * ei * THETA / L**2
    close(reaction(results, b)[moment_index], moment, moment)
    close(reaction(results, a)[moment_index], moment / 2, moment)
    close(reaction(results, b)[force_index], -sign * shear, shear)
    close(reaction(results, a)[force_index], sign * shear, shear)
    close(displacement(results, b)[moment_index], THETA, THETA)


def test_t_asi_02_in_uy_propped() -> None:
    frame = Frame()
    a, _mid, b = straight(frame)
    frame.support(a, fixed_restraints())
    frame.support(b, pinned_restraints())
    frame.model.add_support_settlement(frame.case_id, b.id, uy=m(DELTA))
    results = solve(frame)
    force, moment = 3 * EIZ * DELTA / L**3, 3 * EIZ * DELTA / L**2
    close(reaction(results, b)[FY], force, force)
    close(reaction(results, a)[FY], -force, force)
    close(reaction(results, a)[MZ], -moment, moment)
    close(displacement(results, b)[MZ], 3 * DELTA / (2 * L), DELTA / L)


def test_t_asi_07_torsion_settlement() -> None:
    frame = Frame()
    a, _mid, b = straight(frame)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.model.add_support_settlement(frame.case_id, b.id, rx=rad(THETA))
    results = solve(frame)
    props = frame.model.section(frame.section.ref).properties
    assert props.torsion_constant.value is not None
    gj = Fraction(81e9) * Fraction(props.torsion_constant.value)
    torque = gj * THETA / L
    close(reaction(results, b)[MX], torque, torque)
    close(reaction(results, a)[MX], -torque, torque)
    for forces in results.members:
        close(forces.section_start.values()[3], torque, torque)  # T > 0


def test_t_asi_08_released_end_propped() -> None:
    """Rz liberado en J: el elemento lleva ``-releasez`` (hecho 14 de T-018), sin aux."""
    frame = Frame()
    a, _mid, b = straight(frame)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    (last,) = (x for x in frame.model.members.values() if x.node_j == b.id)
    frame.model.set_member_end(last.id, MemberEnd.J, RELEASED_RZ)
    frame.model.add_support_settlement(frame.case_id, b.id, uy=m(DELTA))
    results = solve(frame)
    force, moment = 3 * EIZ * DELTA / L**3, 3 * EIZ * DELTA / L**2
    close(reaction(results, b)[FY], force, force)
    close(reaction(results, b)[MZ], 0.0, moment)
    close(reaction(results, a)[MZ], -moment, moment)


def capture(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """(petición, respuesta) que llegan al control de asiento."""
    captured: list[Any] = []
    original = settlement_check

    def spy(request: SpatialRequest, response: Any) -> Any:
        captured.append((request, response))
        return original(request, response)

    monkeypatch.setattr("veritas.fem.spatial.settlement_check", spy)
    return captured


def test_t_asi_08_spring_end_sums_aux_reaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hecho 3 y E2-d: resorte de giro k = EIz/L en J (aux): la reacción Fy la lleva el aux.

    Condensación del giro ψ del extremo: (4EI/L + k)·ψ = 6EIδ/L² → ψ = 6δ/(5L); de ahí
    Fy_J = 12EIδ/L³ − 6EI/L²·ψ = 24EIδ/(5L³), Mz_I = 2EI/L·ψ − 6EIδ/L² = −18EIδ/(5L²) y
    Mz_J = −k·ψ = −6EIδ/(5L²); ΣMz_I = (−18 − 6 + 24)/5·EIδ/L² = 0.
    """
    frame = Frame()
    a, _mid, b = straight(frame)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    (last,) = (x for x in frame.model.members.values() if x.node_j == b.id)
    k = Quantity(float(EIZ / L), Dof.RZ.stiffness_dimension)
    frame.model.set_member_end(last.id, MemberEnd.J, EndConnection.semi_rigid(Dof.RZ, k))
    frame.model.add_support_settlement(frame.case_id, b.id, uy=m(DELTA))
    captured = capture(monkeypatch)
    results = solve(frame)
    force = 24 * EIZ * DELTA / (5 * L**3)
    scale = 6 * EIZ * abs(DELTA) / L**2
    close(reaction(results, b)[FY], force, force)
    close(reaction(results, a)[FY], -force, force)
    close(reaction(results, a)[MZ], -18 * EIZ * DELTA / (5 * L**2), scale)
    close(reaction(results, b)[MZ], -6 * EIZ * DELTA / (5 * L**2), scale)
    ((request, response),) = captured
    tag = next(n.tag for n in request.nodes if n.entity_id == str(b.id))
    (aux,) = request.aux_nodes
    master = next(r for t, _d, r in response.node_results if t == tag)
    aux_reaction = next(r for t, _d, r in response.aux_results if t == aux.tag)
    close(master[FY], 0.0, force)  # el maestro no lleva la reacción…
    close(aux_reaction[FY], force, force)  # …la lleva el aux


def test_oblique_bar_settlement_rotates_reference() -> None:
    frame = Frame()
    a, _mid, b = line(frame, (0, 0, 0), (1.5, 2, 0), (3, 4, 0))  # L = 5, ex = (3, 4, 0)/5
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    results = solve(frame)
    length = Fraction(5)
    force, moment = 12 * EIY * DELTA / length**3, 6 * EIY * DELTA / length**2
    ey = (Fraction(-4, 5), Fraction(3, 5))
    close(reaction(results, b)[FZ], force, force)
    close(reaction(results, a)[FZ], -force, force)
    for node in (a, b):
        close(reaction(results, node)[MX], moment * ey[0], moment)
        close(reaction(results, node)[MY], moment * ey[1], moment)
        close(reaction(results, node)[MZ], 0.0, moment)
    close(displacement(results, b)[FZ], DELTA, DELTA)


def test_rotation_settlement_with_released_end_has_no_moment() -> None:
    frame = Frame()
    a, _mid, b = straight(frame)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    (last,) = (x for x in frame.model.members.values() if x.node_j == b.id)
    frame.model.set_member_end(last.id, MemberEnd.J, RELEASED_RZ)
    frame.model.add_support_settlement(frame.case_id, b.id, rz=rad(THETA))
    results = solve(frame)
    moment, force = 4 * EIZ * THETA / L, 6 * EIZ * THETA / L**2
    close(reaction(results, b)[MZ], 0.0, moment)
    close(reaction(results, b)[FY], 0.0, force)
    close(displacement(results, b)[MZ], THETA, THETA)


def test_residual_uses_full_displacement(monkeypatch: pytest.MonkeyPatch) -> None:
    frame = Frame()
    a, _mid, b = straight(frame)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA), rx=rad(THETA))
    captured = capture(monkeypatch)
    solve(frame)
    ((request, response),) = captured
    assert residual_check(request, response, config(frame)) is None
    tag = next(n.tag for n in request.nodes if n.entity_id == str(b.id))
    assert request.settlements == (
        SpatialSettlement(tag, 3, float(DELTA)),
        SpatialSettlement(tag, 4, float(THETA)),
    )


# --- Rechazos (D4-A y D4-bis-A) ----------------------------------------------------------


def rejected(frame: Frame) -> InputValueError:
    with pytest.raises(InputValueError) as info:
        translate_spatial(frame.model, config(frame))
    return info.value


def oblique_basis() -> SupportBasis:
    return SupportBasis.inclined((0.6, 0.8, 0.0), (0.0, 0.0, 1.0))


def test_inclined_support_settlement_is_rejected() -> None:
    frame = Frame()
    a, _mid, b = straight(frame)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints(), oblique_basis())
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    error = rejected(frame)
    assert error.code is D.UNSUPPORTED_CONFIGURATION
    assert error.diagnostic.message_key == "fem.settlement_inclined_unsupported"


def settle_first(frame: Frame) -> None:
    """Asiento en el primer nudo empotrado (base global) de ``grid_frame``."""
    node = next(n for n in frame.model.nodes.values() if n.position.norm() == 0.0)
    frame.model.add_support_settlement(frame.case_id, node.id, uz=m(DELTA))


@pytest.mark.parametrize(
    ("build", "present"),
    [
        (lambda: grid_frame(50, "rz"), lambda r: r.end_penalties),
        (lambda: grid_frame(50, "rigid", chain=True), lambda r: r.chains),
    ],
    ids=["extremo penalizado", "cadena"],
)
def test_settlement_with_end_penalty_or_chain_is_rejected(build: Any, present: Any) -> None:
    frame = build()
    assert present(translate_spatial(frame.model, config(frame)).request)
    settle_first(frame)
    error = rejected(frame)
    assert error.code is D.UNSUPPORTED_CONFIGURATION
    assert error.diagnostic.message_key == "fem.settlement_with_penalty_unsupported"


def test_settlement_with_penalized_support_elsewhere_is_rejected() -> None:
    """Apoyo D4: un giro fijo según una dirección oblicua se representa por penalización."""
    frame = Frame()
    a, mid, b = straight(frame)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.support(mid, build_restraints([SupportDof.RX]), oblique_basis())
    frame.nodal(mid, fz=-1000.0)
    request = translate_spatial(frame.model, config(frame)).request
    assert any(s.penalty for s in request.springs)
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    assert rejected(frame).diagnostic.message_key == "fem.settlement_with_penalty_unsupported"


def test_settlement_with_unpenalized_inclined_support_elsewhere_runs() -> None:
    """Revisión E2.2, obs. 4: un empotramiento de base oblicua (exacto, sin penalización ni
    cadena) en el otro extremo no impide el asiento; da la referencia de T-ASI-01 en Uy."""
    frame = Frame()
    a, _mid, b = straight(frame)
    frame.support(a, fixed_restraints(), oblique_basis())
    frame.support(b, fixed_restraints())
    frame.model.add_support_settlement(frame.case_id, b.id, uy=m(DELTA))
    request = translate_spatial(frame.model, config(frame)).request
    assert not request.chains
    assert not any(s.penalty for s in request.springs)
    force = 12 * EIZ * DELTA / L**3
    close(reaction(solve(frame), b)[FY], force, force)


def test_protocol_rejects_settlements_with_penalties() -> None:
    """Revisión E2.2, M3: el invariante de D4-bis-A también en el protocolo y el worker."""
    frame = grid_frame(50, "rz")
    request = translate_spatial(frame.model, config(frame)).request
    assert request.end_penalties
    tag = next(f.node for f in request.fixes if f.fixity[2])
    with pytest.raises(ProtocolError):
        dataclasses.replace(request, settlements=(SpatialSettlement(tag, 3, 0.01),))
    data = request.to_dict()
    data["settlements"] = [[tag, 3, 0.01]]
    assert opensees_worker_3d._solve(data)["status"] == "PROTOCOL_ERROR"


def penalized_d4_frame() -> Frame:
    """Apoyo D4 penalizado (giro fijo según una dirección oblicua) en el nudo central."""
    frame = Frame()
    a, mid, b = straight(frame)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.support(mid, build_restraints([SupportDof.RX]), oblique_basis())
    frame.nodal(mid, fz=-1000.0)
    return frame


@pytest.mark.parametrize(
    "build",
    [
        lambda: grid_frame(50, "rz"),
        lambda: grid_frame(50, "rigid", chain=True),
        penalized_d4_frame,
    ],
    ids=["extremo penalizado", "cadena", "apoyo D4 penalizado"],
)
def test_worker_rejects_each_penalty_source_with_settlements(build: Any) -> None:
    """Revisión de código de E2 (M3): cada rama del rechazo del worker 3D por separado."""
    frame = build()
    request = translate_spatial(frame.model, config(frame)).request
    sources = [
        bool(request.end_penalties),
        bool(request.chains),
        any(s.penalty for s in request.springs),
    ]
    assert sum(sources) == 1, sources  # una sola fuente de penalización o cadena
    tag = next(f.node for f in request.fixes if f.fixity[2])
    data = request.to_dict()
    data["settlements"] = [[tag, 3, 0.01]]
    response = opensees_worker_3d._solve(data)
    assert response["status"] == "PROTOCOL_ERROR"
    assert "D4-bis-A" in response["message"]


def test_model_without_free_dofs_is_rejected() -> None:
    frame = Frame()
    a, b = line(frame, (0, 0, 0), (4, 0, 0))
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    with pytest.raises(InputValueError) as info:
        analyze_spatial(frame.model, config(frame))
    assert info.value.diagnostic.message_key == "fem.no_free_dofs"


# --- Protocolo v4 ------------------------------------------------------------------------


def settled_request() -> SpatialRequest:
    frame = Frame()
    a, _mid, b = straight(frame)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    return translate_spatial(frame.model, config(frame)).request


def test_request_round_trip_with_settlements() -> None:
    request = settled_request()
    assert request.settlements
    assert SpatialRequest.from_json(request.to_json()) == request
    assert json.loads(request.to_json())["version"] == PROTOCOL_VERSION == 5


@pytest.mark.parametrize(
    "settlements",
    [
        lambda t, free: (SpatialSettlement(free, 3, 0.01),),
        lambda t, free: (SpatialSettlement(t, 7, 0.01),),
        lambda t, free: (SpatialSettlement(t, 3, 0.01), SpatialSettlement(t, 3, 0.02)),
    ],
    ids=["fuera del fix", "gdl", "repetido"],
)
def test_invalid_settlement_is_protocol_error(settlements: Any) -> None:
    request = settled_request()
    tag = request.settlements[0].node
    free = next(n.tag for n in request.nodes if n.tag not in {f.node for f in request.fixes})
    with pytest.raises(ProtocolError):
        dataclasses.replace(request, settlements=settlements(tag, free))


def test_settled_rotation_in_orphan_fixes_is_protocol_error() -> None:
    request = settled_request()
    tag = request.settlements[0].node
    with pytest.raises(ProtocolError):
        dataclasses.replace(
            request,
            orphan_fixes=(SpatialOrphanFix(tag, (1, 0, 0)),),
            settlements=(SpatialSettlement(tag, 4, 0.001),),
        )


def test_worker_rejects_settlement_outside_fix() -> None:
    data = settled_request().to_dict()
    data["fixes"] = []
    assert opensees_worker_3d._solve(data)["status"] == "PROTOCOL_ERROR"


# --- Control del asiento (D7) ------------------------------------------------------------


def test_manipulated_displacement_fails_settlement_control(tmp_path: Path) -> None:
    """El worker real con el Uz del nudo asentado desplazado 1e-9 m: FAILED, primer control."""
    frame = Frame()
    a, _mid, b = straight(frame)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    request = translate_spatial(frame.model, config(frame)).request
    tag = request.settlements[0].node
    script = tmp_path / "wrapped_worker.py"
    script.write_text(
        "import json, runpy, sys\n"
        "path = sys.argv[1]\n"
        f"sys.argv = [{str(WORKER_3D_PATH)!r}, path]\n"
        "try:\n"
        f"    runpy.run_path({str(WORKER_3D_PATH)!r}, run_name='__main__')\n"
        "except SystemExit:\n"
        "    pass\n"
        "data = json.load(open(path, encoding='utf-8'))\n"
        f"[n[1].__setitem__(2, n[1][2] + 1e-9) for n in data['nodes'] if n[0] == {tag}]\n"
        "json.dump(data, open(path, 'w', encoding='utf-8'))\n",
        encoding="utf-8",
    )
    run = analyze_spatial(frame.model, config(frame), worker=script)
    assert run.status is RunStatus.FAILED
    assert run.raw is None
    first = run.diagnostics[0]
    assert first.code is D.ANALYSIS_FAILED
    assert first.message_key == "fem.settlement_mismatch"
    assert first.location is not None
    assert first.location.entity_id == b.id
    assert first.params_dict()["component"] == "Uz"


# --- Identidad sin asientos (E2-a) -------------------------------------------------------


@pytest.mark.parametrize("case_id", sorted(SPATIAL_SPECS))
def test_responses_without_settlements_match_v3(case_id: str) -> None:
    """Bit a bit: worker v3 congelado con la petición v3 y worker v4 con la misma en v4.

    Desde T-015 (protocolo v5) el v4 también es una copia congelada (``WORKER_V4``).
    """
    for spec in SPATIAL_SPECS[case_id]:
        case = _Case(spec.category)
        spec.build(case)
        request = translate_spatial(case.model, SpatialAnalysisConfiguration(case.load_case.id))
        assert request.request.settlements == ()
        assert request.request.member_end_loads == ()
        new_request = {**request.request.to_dict(), "version": 4}
        del new_request["member_end_loads"]
        old_request = {**new_request, "version": 3}
        del old_request["settlements"]
        old = run_worker(json.dumps(old_request), 60.0, WORKER_V3)
        new = run_worker(json.dumps(new_request), 60.0, WORKER_V4)
        assert old.response_text is not None, old.stderr
        assert new.response_text is not None, new.stderr
        before, after = json.loads(old.response_text), json.loads(new.response_text)
        assert (before.pop("version"), after.pop("version")) == (3, 4)
        assert after == before
