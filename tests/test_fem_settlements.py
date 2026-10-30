"""Asientos impuestos en el adaptador plano XZ (plan T-016, E2.1).

Referencias de Euler–Bernoulli con EI constante, en forma cerrada y en fracciones (sin el
solver). δ y θ son los valores prescritos con signo (ADR-001, base global; δ < 0 es un
descenso). La reacción es la acción del apoyo sobre la estructura (ADR-001 §2).

Signos por cuerpo libre en el plano X–Z con v = Uz y φ = dv/dX = −Ry: la rigidez de la viga
da las fuerzas en (v, φ) y la reacción en base de dominio es Fz = F_v y My = −M_φ. Ejemplo
(T-ASI-01, v_J = δ): F_v,J = 12EIδ/L³ y M_φ,I = M_φ,J = −6EIδ/L², luego My = +6EIδ/L² en
ambos extremos; ΣMy respecto a I = 2·6EIδ/L² − L·12EIδ/L³ = 0. Cada test declara sus escalas;
las referencias nulas usan la escala del caso.
"""

import dataclasses
import itertools
import json
from fractions import Fraction
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from fem_support import AREA, E_PA, IY, Frame, inclined, solved_pair
from veritas import fem
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.diagnostics import DiagnosticCode, InputValueError
from veritas.core.geometry.entities import Node
from veritas.core.supports.support import (
    SupportDof,
    build_restraints,
    fixed_restraints,
    pinned_restraints,
    roller_restraints,
)
from veritas.core.units import Quantity
from veritas.fem.adapters import opensees_worker
from veritas.fem.adapters.planar_xz import translate_planar_xz
from veritas.fem.adapters.protocol import (
    PROTOCOL_VERSION,
    PlanarRequest,
    PlanarSettlement,
    ProtocolError,
)
from veritas.fem.benchmarks import SPECS, _Case
from veritas.fem.config import AnalysisConfiguration
from veritas.fem.postconditions import residual_check
from veritas.fem.results.normalized import PlanarResults, ReactionSource, normalize_planar_xz
from veritas.fem.results.run import RunStatus
from veritas.fem.runners.subprocess_runner import WORKER_PATH, run_worker

D = DiagnosticCode
TOL = 1e-9
L = Fraction(4)
EI = Fraction(E_PA) * Fraction(IY)
EA = Fraction(E_PA) * Fraction(AREA)
DELTA = Fraction(-1, 100)  # m, descenso
THETA = Fraction(2, 1000)  # rad
RELEASED_RY = EndConnection.rigid().with_dof(Dof.RY, DofState.RELEASED)
WORKER_V2 = Path(__file__).parent / "data" / "opensees_worker_v2.py"

# Componentes de Vector6 (ADR-001): (Fx, Fy, Fz, Mx, My, Mz) o (Ux, Uy, Uz, Rx, Ry, Rz).
FX, FZ, MY = 0, 2, 4


def m(value: float | Fraction) -> Quantity:
    return Quantity.of(float(value), "m")


def rad(value: float | Fraction) -> Quantity:
    return Quantity.of(float(value), "rad")


def close(actual: object, expected: Fraction | float, scale: Fraction | float) -> None:
    assert isinstance(actual, float)
    assert abs(actual - float(expected)) <= TOL * abs(float(scale)), (actual, float(expected))


def beam(frame: Frame, *xs: float) -> list[Node]:
    """Nudos en z = 0 y barras consecutivas (al menos dos elementos por vano)."""
    nodes = [frame.node(x, 0.0) for x in xs]
    for a, b in itertools.pairwise(nodes):
        frame.member(a, b)
    return nodes


def solve(frame: Frame) -> PlanarResults:
    run = fem.analyze_planar_xz(frame.model, frame.config())
    assert run.status is RunStatus.SUCCEEDED, [d.message_key for d in run.diagnostics]
    return normalize_planar_xz(run, frame.model)


def reaction(results: PlanarResults, node: Node) -> tuple[float | None, ...]:
    (found,) = (r for r in results.reactions if r.node_id == node.id)
    return found.global_.values()


def displacement(results: PlanarResults, node: Node) -> tuple[float | None, ...]:
    (found,) = (n for n in results.nodes if n.node_id == node.id)
    return found.displacement.values()


def member_forces(results: PlanarResults, member_id: UUID) -> Any:
    (found,) = (f for f in results.members if f.member_id == member_id)
    return found


# --- Referencias T-ASI planas (01-06) ---------------------------------------------------


def test_t_asi_01_fixed_fixed_settlement() -> None:
    frame = Frame()
    a, mid, b = beam(frame, 0, 2, 4)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    results = solve(frame)
    force, moment = 12 * EI * DELTA / L**3, 6 * EI * DELTA / L**2
    close(reaction(results, b)[FZ], force, force)
    close(reaction(results, a)[FZ], -force, force)
    close(reaction(results, a)[MY], moment, moment)
    close(reaction(results, b)[MY], moment, moment)
    close(displacement(results, b)[2], DELTA, DELTA)
    close(displacement(results, mid)[2], DELTA / 2, DELTA)
    # Revisión E2.1, M3: sin carga de barra, pI = reacción en I: My = +6EIδ/L².
    (first,) = (x for x in frame.model.members.values() if x.node_i == a.id)
    close(member_forces(results, first.id).p_i.values()[MY], moment, moment)
    # u = δ(3ξ² − 2ξ³): φ(L/2) = 3δ/(2L) y Ry = −φ.
    close(displacement(results, mid)[4], -3 * DELTA / (2 * L), DELTA / L)


def test_t_asi_02_propped_settlement() -> None:
    frame = Frame()
    a, _mid, b = beam(frame, 0, 2, 4)
    frame.support(a, fixed_restraints())
    frame.support(b, pinned_restraints())
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    results = solve(frame)
    force, moment = 3 * EI * DELTA / L**3, 3 * EI * DELTA / L**2
    close(reaction(results, b)[FZ], force, force)
    close(reaction(results, a)[FZ], -force, force)
    close(reaction(results, a)[MY], moment, moment)
    assert reaction(results, b)[MY] is None  # Ry libre: no aplicable
    # Condensación de φ_J: 4EI/L·φ − 6EI/L²·δ = 0 → φ_J = 3δ/(2L), Ry = −φ.
    close(displacement(results, b)[4], -3 * DELTA / (2 * L), DELTA / L)


def test_t_asi_02_with_released_end_sums_aux_reaction(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hecho 3 y E2-d: la reacción del GDL asentado está en el aux y se suma al maestro."""
    frame = Frame()
    a, mid, b = beam(frame, 0, 2, 4)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    (last,) = (x for x in frame.model.members.values() if x.node_j == b.id)
    frame.model.set_member_end(last.id, MemberEnd.J, RELEASED_RY)
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    request, response, _config, run = solved_pair(frame, monkeypatch)
    assert run.status is RunStatus.SUCCEEDED
    results = normalize_planar_xz(run, frame.model)
    force, moment = 3 * EI * DELTA / L**3, 3 * EI * DELTA / L**2
    close(reaction(results, b)[FZ], force, force)
    close(reaction(results, b)[MY], 0.0, moment)
    close(reaction(results, a)[MY], moment, moment)
    tag = next(n.tag for n in request.nodes if n.entity_id == str(b.id))
    (link,) = request.end_links
    master = next(r for t, _d, r in response.node_results if t == tag)
    aux = next(r for t, _d, r in response.end_aux if t == link.aux)
    assert master[1] == 0.0  # el maestro no lleva la reacción…
    close(aux[1], force, force)  # …la lleva el aux (base del solver: u₂ = Uz)
    del mid


def test_t_asi_03_simply_supported_rigid_rotation() -> None:
    """Isostática: esfuerzos y reacciones nulos; u_z = δ·x/L y Ry = −δ/L."""
    frame = Frame()
    a, mid, b = beam(frame, 0, 2, 4)
    frame.support(a, pinned_restraints())
    frame.support(b, roller_restraints(SupportDof.UZ))
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    results = solve(frame)
    force_scale, moment_scale = 3 * EI * abs(DELTA) / L**3, 3 * EI * abs(DELTA) / L**2
    close(reaction(results, a)[FX], 0.0, force_scale)
    assert reaction(results, b)[FX] is None  # rodillo: Ux libre
    for node in (a, b):
        close(reaction(results, node)[FZ], 0.0, force_scale)
    for forces in results.members:
        for value, scale in zip(
            forces.p_i.values(), (force_scale,) * 3 + (moment_scale,) * 3, strict=True
        ):
            if value is not None:
                close(value, 0.0, scale)
    close(displacement(results, mid)[2], DELTA / 2, DELTA)
    for node in (a, mid, b):
        close(displacement(results, node)[4], -DELTA / L, DELTA / L)


def test_t_asi_04_fixed_fixed_rotation_settlement() -> None:
    frame = Frame()
    a, _mid, b = beam(frame, 0, 2, 4)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.model.add_support_settlement(frame.case_id, a.id, ry=rad(THETA))
    results = solve(frame)
    shear = 6 * EI * THETA / L**2
    close(reaction(results, a)[MY], 4 * EI * THETA / L, 4 * EI * THETA / L)
    close(reaction(results, b)[MY], 2 * EI * THETA / L, 4 * EI * THETA / L)
    close(reaction(results, a)[FZ], -shear, shear)
    close(reaction(results, b)[FZ], shear, shear)
    close(displacement(results, a)[4], THETA, THETA)


def test_t_asi_05_two_span_central_settlement() -> None:
    frame = Frame()
    a, _p, b, _q, c = beam(frame, 0, 2, 4, 6, 8)
    frame.support(a, pinned_restraints())
    frame.support(b, roller_restraints(SupportDof.UZ))
    frame.support(c, roller_restraints(SupportDof.UZ))
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    results = solve(frame)
    force = 6 * EI * DELTA / L**3
    close(reaction(results, b)[FZ], force, force)
    close(reaction(results, a)[FZ], -force / 2, force)
    close(reaction(results, c)[FZ], -force / 2, force)
    moment = 3 * EI * abs(DELTA) / L**2
    (left,) = (x for x in frame.model.members.values() if x.node_j == b.id)
    # Revisión E2.1, M3: q(L−) = pJ y, con δ < 0, flexión de tracción abajo en B: My(B) < 0.
    close(member_forces(results, left.id).p_j.values()[MY], 3 * EI * DELTA / L**2, moment)


def test_t_asi_06_axial_settlement() -> None:
    frame = Frame()
    a, _mid, b = beam(frame, 0, 2, 4)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    delta = Fraction(1, 1000)
    frame.model.add_support_settlement(frame.case_id, b.id, ux=m(delta))
    results = solve(frame)
    axial = EA * delta / L
    close(reaction(results, b)[FX], axial, axial)
    close(reaction(results, a)[FX], -axial, axial)
    for forces in results.members:
        close(forces.section_start.values()[0], axial, axial)  # N > 0: tracción


# --- Casos planos adicionales (revisión, hallazgos 9 y 10) ------------------------------


def test_mixed_support_settled_fixed_dof_with_spring() -> None:
    """Ux FIXED asentado y Uz SPRING en el mismo apoyo: N = EAδ/L y el resorte sin fuerza."""
    frame = Frame()
    a, _mid, b = beam(frame, 0, 2, 4)
    frame.support(a, fixed_restraints())
    springs = {SupportDof.UZ: Quantity.of(1e6, "N/m")}
    frame.support(b, build_restraints([SupportDof.UX], springs))
    delta = Fraction(1, 1000)
    frame.model.add_support_settlement(frame.case_id, b.id, ux=m(delta))
    results = solve(frame)
    axial = EA * delta / L
    close(reaction(results, b)[FX], axial, axial)
    close(reaction(results, b)[FZ], 0.0, axial)
    close(displacement(results, b)[2], 0.0, delta)
    (support,) = (r for r in results.reactions if r.node_id == b.id)
    assert dict(support.sources)[SupportDof.UX] is ReactionSource.FIX
    assert dict(support.sources)[SupportDof.UZ] is ReactionSource.SPRING


def test_rotation_settlement_with_all_ends_released_has_no_moment() -> None:
    frame = Frame()
    a, _mid, b = beam(frame, 0, 2, 4)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    (last,) = (x for x in frame.model.members.values() if x.node_j == b.id)
    frame.model.set_member_end(last.id, MemberEnd.J, RELEASED_RY)
    frame.model.add_support_settlement(frame.case_id, b.id, ry=rad(THETA))
    results = solve(frame)
    moment, force = 4 * EI * THETA / L, 6 * EI * THETA / L**2
    close(reaction(results, b)[MY], 0.0, moment)
    close(reaction(results, b)[FZ], 0.0, force)
    close(reaction(results, a)[MY], 0.0, moment)
    close(displacement(results, b)[4], THETA, THETA)


def test_settlement_and_residual_controls_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """D7: el residuo Tᵀ(K·u − F) usa el u completo, con el valor prescrito."""
    frame = Frame()
    a, _mid, b = beam(frame, 0, 2, 4)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA), ry=rad(THETA))
    request, response, config, run = solved_pair(frame, monkeypatch)
    assert run.status is RunStatus.SUCCEEDED
    assert residual_check(request, response, config) is None
    tag = next(n.tag for n in request.nodes if n.entity_id == str(b.id))
    assert request.settlements == (
        PlanarSettlement(tag, 2, float(DELTA)),
        PlanarSettlement(tag, 3, -float(THETA)),  # θ = −Ry
    )


# --- Rechazos del traductor (orden: fuera del plano, inclinado, penalización) -----------


def settled_beam(basis_b: Any = None) -> tuple[Frame, Node, Node]:
    frame = Frame()
    a, _mid, b = beam(frame, 0, 2, 4)
    frame.support(a, fixed_restraints())
    if basis_b is None:
        frame.support(b, fixed_restraints())
    else:
        frame.support(b, fixed_restraints(), basis_b)
    return frame, a, b


def rejected(frame: Frame) -> InputValueError:
    with pytest.raises(InputValueError) as info:
        translate_planar_xz(frame.model, frame.config())
    return info.value


def test_out_of_plane_settlement_is_rejected() -> None:
    frame, _a, b = settled_beam()
    frame.model.add_support_settlement(frame.case_id, b.id, uy=m(0.01))
    error = rejected(frame)
    assert error.code is D.UNSUPPORTED_CONFIGURATION
    assert error.diagnostic.message_key == "fem.settlement_out_of_plane"


def test_out_of_plane_wins_over_inclined() -> None:
    frame, _a, b = settled_beam(inclined(30.0))
    frame.model.add_support_settlement(frame.case_id, b.id, uy=m(0.01), uz=m(DELTA))
    assert rejected(frame).diagnostic.message_key == "fem.settlement_out_of_plane"


def test_inclined_support_settlement_is_rejected() -> None:
    frame, _a, b = settled_beam(inclined(30.0))
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    error = rejected(frame)
    assert error.code is D.UNSUPPORTED_CONFIGURATION
    assert error.diagnostic.message_key == "fem.settlement_inclined_unsupported"


def add_inclined_roller(frame: Frame) -> None:
    """Rodillo inclinado (penalizado, D4 de T-007) en un nudo distinto del asentado."""
    a = next(n for n in frame.model.nodes.values() if n.position.x == 2.0)
    frame.support(a, build_restraints([SupportDof.UX]), inclined(30.0))


def test_settlement_with_penalty_elsewhere_is_rejected() -> None:
    frame, _a, b = settled_beam()
    add_inclined_roller(frame)
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    error = rejected(frame)
    assert error.code is D.UNSUPPORTED_CONFIGURATION
    assert error.diagnostic.message_key == "fem.settlement_with_penalty_unsupported"


def test_inclined_wins_over_penalty() -> None:
    frame, _a, b = settled_beam(inclined(30.0))
    add_inclined_roller(frame)
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    assert rejected(frame).diagnostic.message_key == "fem.settlement_inclined_unsupported"


def test_settlement_with_unpenalized_inclined_support_elsewhere_runs() -> None:
    """D4-bis-A rechaza penalizaciones, no apoyos inclinados: un empotramiento inclinado
    (Ux′ y Uz′ FIXED, sin penalización) en otro nudo se calcula (revisión E2.1, M2)."""
    frame, a, b = settled_beam()
    frame.support(a, fixed_restraints(), inclined(30.0))
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    results = solve(frame)
    force = 12 * EI * DELTA / L**3
    close(reaction(results, b)[FZ], force, force)


def test_penalty_without_settlement_still_runs() -> None:
    frame, _a, _b = settled_beam()
    add_inclined_roller(frame)
    frame.nodal(next(iter(frame.model.nodes.values())), fz=-1000.0)
    assert translate_planar_xz(frame.model, frame.config()).request.settlements == ()


def test_model_without_free_dofs_is_rejected() -> None:
    """D8: barra única biempotrada y asentada; sin ecuaciones libres (hecho 7)."""
    frame = Frame()
    a, b = beam(frame, 0, 4)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    with pytest.raises(InputValueError) as info:
        fem.analyze_planar_xz(frame.model, frame.config())
    assert info.value.diagnostic.message_key == "fem.no_free_dofs"


# --- Protocolo v3 ------------------------------------------------------------------------


def settled_request() -> PlanarRequest:
    frame, _a, b = settled_beam()
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    return translate_planar_xz(frame.model, frame.config()).request


def test_request_round_trip_with_settlements() -> None:
    request = settled_request()
    assert request.settlements
    assert PlanarRequest.from_json(request.to_json()) == request
    assert json.loads(request.to_json())["version"] == PROTOCOL_VERSION == 3


def test_settlement_outside_fix_is_protocol_error() -> None:
    request = settled_request()
    free = next(n.tag for n in request.nodes if n.tag not in {f.node for f in request.fixes})
    with pytest.raises(ProtocolError):
        dataclasses.replace(request, settlements=(PlanarSettlement(free, 2, 0.01),))


@pytest.mark.parametrize(
    "settlements",
    [
        lambda t: (PlanarSettlement(t, 4, 0.01),),
        lambda t: (PlanarSettlement(t, 2, 0.01), PlanarSettlement(t, 2, 0.02)),
    ],
    ids=["gdl", "repetido"],
)
def test_invalid_settlement_is_protocol_error(settlements: Any) -> None:
    request = settled_request()
    tag = request.settlements[0].node
    with pytest.raises(ProtocolError):
        dataclasses.replace(request, settlements=settlements(tag))


def test_settled_rotation_in_orphan_fixes_is_protocol_error() -> None:
    request = settled_request()
    tag = request.settlements[0].node
    with pytest.raises(ProtocolError):
        dataclasses.replace(
            request, orphan_fixes=(tag,), settlements=(PlanarSettlement(tag, 3, 0.001),)
        )


def test_worker_rejects_settlement_outside_fix() -> None:
    data = settled_request().to_dict()
    data["fixes"] = []
    response = opensees_worker._solve(data)
    assert response["status"] == "PROTOCOL_ERROR"


# --- Control del asiento (D7) ------------------------------------------------------------


FAKE_WORKER = """
import json, sys
request = json.loads(sys.stdin.read())
data = {
    "protocol": "veritas.opensees.planar", "version": 3, "status": "OK",
    "analyze_code": 0, "message": "simulado",
    "versions": {"python": "x", "openseespy": "y", "openseespywin": None},
    "nodes": [[n[0], [0.0, 0.0, 0.0], [0.0, 0.0, 0.0]] for n in request["nodes"]],
    "aux": [[s[3], [0.0, 0.0, 0.0]] for s in request["springs"]],
    "beams": [[b[0], [0.0] * 6] for b in request["beams"]],
    "end_aux": [[k[5], [0.0] * 3, [0.0] * 3] for k in request["end_links"]],
}
with open(sys.argv[1], "w", encoding="utf-8") as stream:
    json.dump(data, stream)
"""


def test_manipulated_displacement_fails_settlement_control(tmp_path: Path) -> None:
    """Respuesta equilibrada (todo nulo) pero sin el asiento: FAILED por el primer control."""
    frame, _a, b = settled_beam()
    frame.model.add_support_settlement(frame.case_id, b.id, uz=m(DELTA))
    worker = tmp_path / "fake_worker.py"
    worker.write_text(FAKE_WORKER, encoding="utf-8")
    run = fem.analyze_planar_xz(frame.model, frame.config(), worker=worker)
    assert run.status is RunStatus.FAILED
    assert run.raw is None
    first = run.diagnostics[0]
    assert first.code is D.ANALYSIS_FAILED
    assert first.message_key == "fem.settlement_mismatch"
    assert first.location is not None
    assert first.location.entity_id == b.id
    assert first.params_dict()["component"] == "Uz"


# --- Identidad sin asientos (E2-a) -------------------------------------------------------


def _numeric(response: dict[str, Any]) -> dict[str, Any]:
    return {key: response[key] for key in ("nodes", "aux", "end_aux", "beams")}


@pytest.mark.parametrize("case_id", sorted(SPECS))
def test_responses_without_settlements_match_v2(case_id: str) -> None:
    """Bit a bit: worker v2 congelado con la petición v2 y worker v3 con la misma en v3."""
    for spec in SPECS[case_id]:
        case = _Case()
        spec.build(case)
        request = translate_planar_xz(case.model, AnalysisConfiguration(case.load_case.id)).request
        assert request.settlements == ()
        old_request = {**request.to_dict(), "version": 2}
        del old_request["settlements"]
        old = run_worker(json.dumps(old_request), 60.0, WORKER_V2)
        new = run_worker(request.to_json(), 60.0, WORKER_PATH)
        assert old.response_text is not None, old.stderr
        assert new.response_text is not None, new.stderr
        before, after = json.loads(old.response_text), json.loads(new.response_text)
        assert before["status"] == after["status"] == "OK", after["message"]
        assert (before["version"], after["version"]) == (2, 3)
        assert _numeric(after) == _numeric(before)


def test_rotation_mismatch_is_reported_in_domain_sign(tmp_path: Path) -> None:
    """Revisión E2.1, M4: el diagnóstico de un giro asentado muestra Ry, no θ = −Ry."""
    frame, _a, b = settled_beam()
    frame.model.add_support_settlement(frame.case_id, b.id, ry=rad(THETA))
    worker = tmp_path / "fake_worker.py"
    worker.write_text(FAKE_WORKER, encoding="utf-8")
    run = fem.analyze_planar_xz(frame.model, frame.config(), worker=worker)
    params = run.diagnostics[0].params_dict()
    assert run.diagnostics[0].message_key == "fem.settlement_mismatch"
    assert params["component"] == "Ry"
    assert params["prescribed"] == float(THETA)
