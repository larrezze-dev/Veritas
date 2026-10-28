"""Residuo independiente Tᵀ(K·u − F) del plan T-017, D2, y su vector F.

Referencias de F a mano (revisión, 2): biempotrada con carga uniforme, barra inclinada con
wx y wy, y extremo liberado cargado (el momento de empotramiento va al θ del aux). Después,
la batería de referencias pasa con tol_equilibrium = 1e-14 (hecho 5), y un u perturbado da
``fem.residual_failed``.
"""

import dataclasses
import math
from uuid import uuid4

import pytest

from fem_support import E_PA, IY, Frame, solved_pair
from veritas import fem
from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.loads.loads import LoadBasis
from veritas.core.messages_es import render_message
from veritas.core.supports.support import fixed_restraints
from veritas.core.units import Quantity
from veritas.fem import RunStatus, benchmarks, postconditions
from veritas.fem.adapters.protocol import (
    PlanarBeam,
    PlanarBeamLoad,
    PlanarEndLink,
    PlanarFix,
    PlanarNode,
    PlanarRequest,
    WorkerResponse,
)
from veritas.fem.adapters.singularity import TANGENT, aux_rotation_index
from veritas.fem.config import AnalysisConfiguration
from veritas.fem.postconditions import (
    CheckFailure,
    global_equivalent_loads,
    load_vector,
    local_equivalent_loads,
    residual_check,
)

L = 4.0
WY, WX = -10_000.0, 2_500.0  # N/m en la base local del elemento (``beamUniform``).


def request(
    end: tuple[float, float],
    loads: tuple[PlanarBeamLoad, ...],
    links: tuple[PlanarEndLink, ...] = (),
) -> PlanarRequest:
    """Una barra 1→2 de N1 = (0, 0) a N2 = ``end``, empotrada en ambos nudos."""
    return PlanarRequest(
        model_revision=1,
        case_id="c",
        case_revision=1,
        nodes=(PlanarNode(1, "n1", 0.0, 0.0), PlanarNode(2, "n2", *end)),
        beams=(PlanarBeam(1, "b1", 1, 2, 0.08, 210e9, 1e-3, "Iy"),),
        fixes=(PlanarFix(1, (1, 1, 1)), PlanarFix(2, (1, 1, 1))),
        springs=(),
        nodal_loads=(),
        beam_loads=loads,
        end_links=links,
    )


def close(values: list[float], expected: list[float]) -> None:
    scale = max(abs(value) for value in expected)
    assert all(abs(a - b) <= 1e-12 * scale for a, b in zip(values, expected, strict=True)), (
        values,
        expected,
    )


def test_clamped_beam_equivalent_loads_by_hand() -> None:
    """F en los nudos = qL/2 y ±qL²/12 (barra horizontal: base local = global)."""
    req = request((L, 0.0), (PlanarBeamLoad(1, WY, 0.0),))
    expected = [0.0, WY * L / 2, WY * L**2 / 12, 0.0, WY * L / 2, -WY * L**2 / 12]
    close(local_equivalent_loads(req)[1], expected)
    close(load_vector(req), expected)


def test_loads_on_the_same_member_are_summed() -> None:
    req = request((L, 0.0), (PlanarBeamLoad(1, WY, 0.0), PlanarBeamLoad(1, WY, WX)))
    expected = [WX * L / 2, WY * L, WY * L**2 / 6, WX * L / 2, WY * L, -WY * L**2 / 6]
    close(local_equivalent_loads(req)[1], expected)


def test_inclined_member_rotation_by_hand() -> None:
    """Barra a 30° con wx y wy: Tᵀ·F_eq a mano y la resultante del control de equilibrio."""
    c, s = math.cos(math.radians(30.0)), math.sin(math.radians(30.0))
    req = request((L * c, L * s), (PlanarBeamLoad(1, WY, WX),))
    axial, shear, moment = WX * L / 2, WY * L / 2, WY * L**2 / 12
    fx, fz = c * axial - s * shear, s * axial + c * shear
    expected = [fx, fz, moment, fx, fz, -moment]
    close(global_equivalent_loads(req)[1], expected)
    # Misma resultante que ``equilibrium_residual``: q₁ = wx·ux − wy·uz, q₂ = wx·uz + wy·ux.
    q1, q2 = WX * c - WY * s, WX * s + WY * c
    close([fx + fx, fz + fz], [q1 * L, q2 * L])


def test_released_end_moment_goes_to_aux_rotation() -> None:
    """Con J liberado, −wy·L²/12 va al θ del aux; el θ del maestro no recibe nada."""
    link = PlanarEndLink(10, "b1", 1, "J", 2, 100, None)
    req = request((L, 0.0), (PlanarBeamLoad(1, WY, 0.0),), (link,))
    load = load_vector(req)
    aux = aux_rotation_index(req)[100]
    assert len(load) == 7
    close(load, [0.0, WY * L / 2, WY * L**2 / 12, 0.0, WY * L / 2, 0.0, -WY * L**2 / 12])
    assert aux == 6


# --- Corridas reales ---------------------------------------------------------------------

Captured = list[tuple[str, PlanarRequest, WorkerResponse]]


def captured_references(monkeypatch: pytest.MonkeyPatch) -> Captured:
    """(caso, petición, respuesta) de toda la batería de referencias."""
    captured: Captured = []
    current = [""]
    original = fem.equilibrium_residual

    def spy(
        req: PlanarRequest, response: WorkerResponse, config: AnalysisConfiguration
    ) -> tuple[bool, float, float]:
        captured.append((current[0], req, response))
        return original(req, response, config)

    monkeypatch.setattr(fem, "equilibrium_residual", spy)
    for case in benchmarks.CASES:
        current[0] = case
        for outcome in benchmarks.run_reference(case):
            assert outcome.matches, case
    return captured


@pytest.mark.solver
def test_reference_battery_residual_within_1e_14(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hecho 5: ≤ 1e-14 relativo sin penalización; T-FEM-004 pasa con el término de k_pen."""
    captured = captured_references(monkeypatch)
    assert {case for case, _req, _resp in captured} == set(benchmarks.CASES)
    config = AnalysisConfiguration(uuid4(), tol_equilibrium=1e-14)
    for case, req, response in captured:
        assert residual_check(req, response, config) is None, case


@pytest.mark.solver
def test_penalty_term_is_needed_only_on_the_tangent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sin el término c·eps·k_pen·‖u‖, T-FEM-004 falla en la tangente del nudo penalizado."""
    captured = [item for item in captured_references(monkeypatch) if item[0] == "T-FEM-004"]
    (_case, req, response), *_ = captured
    without_penalty = dataclasses.replace(
        req,
        springs=tuple(dataclasses.replace(s, stiffness=0.0) for s in req.springs),
    )
    config = AnalysisConfiguration(uuid4(), tol_equilibrium=1e-14)
    failure = residual_check(without_penalty, response, config)
    assert failure is not None
    assert failure.params["component"] == TANGENT


def clamped_released(stiffness_ratio: float | None) -> Frame:
    """Biempotrada N1-N3-N2 con q en B2 y el extremo J de B2 liberado o con resorte."""
    frame = Frame()
    a, m, b = frame.node(0, 0), frame.node(L / 2, 0), frame.node(L, 0)
    frame.support(a, fixed_restraints())
    frame.support(b, fixed_restraints())
    frame.member(a, m)
    b2 = frame.member(m, b)
    frame.model.add_member_uniform_load(
        frame.case_id, b2.id, basis=LoadBasis.GLOBAL, qz=Quantity.of(-10_000.0, "N/m")
    )
    if stiffness_ratio is None:
        end = EndConnection.rigid().with_dof(Dof.RY, DofState.RELEASED)
    else:
        k = stiffness_ratio * E_PA * IY / L
        end = EndConnection.semi_rigid(Dof.RY, Quantity(k, Dof.RY.stiffness_dimension))
    frame.model.set_member_end(b2.id, MemberEnd.J, end)
    return frame


@pytest.mark.solver
@pytest.mark.parametrize("ratio", [None, 1.0, 1e12])
def test_loaded_non_rigid_end_passes(ratio: float | None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Extremo liberado cargado y resorte hasta kL/EI = 1e12: pasan D1 y D2 con 1e-14."""
    req, response, config, run = solved_pair(clamped_released(ratio), monkeypatch)
    assert run.status is RunStatus.SUCCEEDED, run.diagnostics
    tight = AnalysisConfiguration(config.load_case_id, tol_equilibrium=1e-14)
    assert residual_check(req, response, tight) is None


@pytest.mark.solver
def test_inclined_member_run_residual_within_1e_14(monkeypatch: pytest.MonkeyPatch) -> None:
    """Referencia de la corrida de la barra inclinada 30° con wx y wy (revisión E1, 10)."""
    from test_fem_postconditions import inclined_frame  # noqa: PLC0415

    for variant in ("rígida", "B1 liberada en I", "B2 liberada en I y J"):
        req, response, config, run = solved_pair(inclined_frame(variant), monkeypatch)
        assert run.status is RunStatus.SUCCEEDED, run.diagnostics
        assert any(load.wx != 0.0 and load.wy != 0.0 for load in req.beam_loads)
        tight = AnalysisConfiguration(config.load_case_id, tol_equilibrium=1e-14)
        assert residual_check(req, response, tight) is None, variant


@pytest.mark.solver
def test_perturbed_node_displacement_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    req, response, config, _run = solved_pair(clamped_released(None), monkeypatch)
    free = next(tag for tag, disp, _r in response.node_results if disp[1] != 0.0)
    nodes = tuple(
        (tag, (d[0], d[1] * (1 + 1e-6), d[2]) if tag == free else d, r)
        for tag, d, r in response.node_results
    )
    bad = dataclasses.replace(response, node_results=nodes)
    failure = residual_check(req, bad, config)
    assert failure is not None
    assert failure.key == "fem.residual_failed"
    assert failure.node_id == next(n.entity_id for n in req.nodes if n.tag == free)
    assert failure.member_id is None


def test_aux_rotation_residual_is_reported_on_member_end() -> None:
    """Sin solver: el único GDL libre es el θ del aux de J; un θ arbitrario no equilibra."""
    link = PlanarEndLink(10, "b1", 1, "J", 2, 100, None)
    req = request((L, 0.0), (PlanarBeamLoad(1, WY, 0.0),), (link,))
    zero = (0.0, 0.0, 0.0)
    response = WorkerResponse(
        "OK",
        0,
        "",
        {},
        ((1, zero, zero), (2, zero, zero)),
        (),
        ((1, (0.0,) * 6),),
        ((100, (0.0, 0.0, 1e-3), zero),),
    )
    failure = residual_check(req, response, AnalysisConfiguration(uuid4()))
    assert failure is not None
    assert failure.key == "fem.residual_failed"
    assert failure.member_id == "b1"
    assert failure.params["end"] == "J"
    assert failure.params["component"] == "theta"


@pytest.mark.solver
def test_residual_failure_is_rendered(monkeypatch: pytest.MonkeyPatch) -> None:
    """Integración: ``fem.residual_failed`` con el nudo como sujeto y sin resultados."""
    frame = clamped_released(None)
    original = postconditions.residual_check

    def tampered(
        req: PlanarRequest, response: WorkerResponse, config: AnalysisConfiguration
    ) -> CheckFailure | None:
        free = next(tag for tag, disp, _r in response.node_results if disp[1] != 0.0)
        nodes = tuple(
            (tag, (d[0], d[1] * 2, d[2]) if tag == free else d, r)
            for tag, d, r in response.node_results
        )
        bad = dataclasses.replace(response, node_results=nodes)
        return original(req, bad, config)

    monkeypatch.setattr(fem, "residual_check", tampered)
    run = fem.analyze_planar_xz(frame.model, frame.config())
    assert run.status is RunStatus.FAILED
    assert run.raw is None
    (diagnostic,) = run.diagnostics
    assert diagnostic.message_key == "fem.residual_failed"
    assert diagnostic.location is not None
    assert diagnostic.location.entity_id in frame.model.nodes
    assert "el nudo" in render_message(diagnostic)


@pytest.mark.solver
def test_stiff_end_spring_rows_keep_residual_control(monkeypatch: pytest.MonkeyPatch) -> None:
    """Desviación 4 de E1: con kL/EI = 1e12 y el maestro girando, un k alterado un 1 %
    deja un residuo ≈ 0,01·|M| en las filas de giro, y D2 lo detecta (antes, la cota
    relativa valía ≈ 125·|M|)."""
    from test_fem_postconditions import clamped, spring_ry  # noqa: PLC0415

    frame = clamped(MemberEnd.I, spring_ry(1e12), only_b2=True)
    req, response, config, run = solved_pair(frame, monkeypatch)
    assert run.status is RunStatus.SUCCEEDED, run.diagnostics
    assert residual_check(req, response, config) is None
    (link,) = req.end_links
    assert link.stiffness is not None
    altered = dataclasses.replace(link, stiffness=link.stiffness * 1.01)
    failure = residual_check(dataclasses.replace(req, end_links=(altered,)), response, config)
    assert failure is not None
    assert failure.params["component"] == "theta"
