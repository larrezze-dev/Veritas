"""Protocolo espacial y configuración (plan T-018, D2 y D6; v3 de ADR-004): validación
estricta y round-trip."""

import json
from typing import Any
from uuid import uuid4

import pytest

from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError
from veritas.fem.adapters import opensees_worker_3d
from veritas.fem.adapters.protocol import PROTOCOL_ID as PLANAR_PROTOCOL_ID
from veritas.fem.adapters.protocol import NonFiniteValueError, ProtocolError
from veritas.fem.config import AnalysisConfiguration
from veritas.fem.spatial import SpatialAnalysisConfiguration
from veritas.fem.spatial.protocol import (
    PROTOCOL_ID,
    PROTOCOL_VERSION,
    SpatialAuxNode,
    SpatialBeam,
    SpatialBeamLoad,
    SpatialChain,
    SpatialEndEqual,
    SpatialEndPenalty,
    SpatialEndSpring,
    SpatialFix,
    SpatialNodalLoad,
    SpatialNode,
    SpatialOrphanFix,
    SpatialRequest,
    SpatialSpring,
    SpatialWorkerResponse,
    penalty_step,
)


def _request() -> SpatialRequest:
    return SpatialRequest(
        model_revision=3,
        case_id="caso",
        case_revision=1,
        alpha_sp=5.6e9,
        alpha_mp=5.6e9,
        nodes=(
            SpatialNode(1, "n1", 0.0, 0.0, 0.0),
            SpatialNode(2, "n2", 1.2, -0.7, 0.9),
            SpatialNode(3, "n3", 2.0, 0.0, 0.0),
        ),
        beams=(
            SpatialBeam(1, "b1", 1, 2, 0.08, 210e9, 81e9, 7.3e-4, 1.0e-3, 2.6e-4, (0.1, 0.2, 0.97)),
            # E2-b: códigos de liberación y A, J efectivos nulos con su marca.
            SpatialBeam(
                2,
                "b2",
                2,
                3,
                0.0,
                210e9,
                81e9,
                0.0,
                1e-3,
                2.6e-4,
                (0.0, 0.0, 1.0),
                3,
                2,
                True,
                True,
            ),
        ),
        fixes=(SpatialFix(1, (1, 1, 1, 1, 1, 1)), SpatialFix(3, (1, 1, 1, 0, 0, 0))),
        springs=(SpatialSpring(10, "s", 2, 11, 4, 1e6, (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), False),),
        chains=(
            SpatialChain(20, "r", 2, 21, 22, 23, (0.3, -0.5, 0.81), (0.0, 0.85, 0.52), 1.6, 5.6e9),
        ),
        nodal_loads=(SpatialNodalLoad(2, (3000.0, -2000.0, -5000.0, 400.0, -700.0, 900.0)),),
        beam_loads=(SpatialBeamLoad(1, -2500.0, 4000.0, 1500.0),),
        orphan_fixes=(SpatialOrphanFix(3, (0, 1, 1)),),
        aux_nodes=(SpatialAuxNode(30, 2, 1, "J"), SpatialAuxNode(31, 2, 2, "I")),
        end_equals=(SpatialEndEqual(30, (1, 2, 3)), SpatialEndEqual(31, (4, 5))),
        end_springs=(
            SpatialEndSpring(40, 30, 5, 2.5e7, (0.6, 0.0, 0.8), (0.0, 1.0, 0.0), False),
            SpatialEndSpring(41, 30, 4, 5.6e15, (0.6, 0.0, 0.8), (0.0, 1.0, 0.0), True),
        ),
        end_penalties=(SpatialEndPenalty(41, (0.6, 0.0, 0.8), 3.5e4),),
        end_penalty_tolerance=1e-10,
        end_penalty_stagnation=1e-6,
        end_penalty_max_iterations=20,
    )


def _mutated(path: tuple[str | int, ...], value: object) -> str:
    data: Any = _request().to_dict()
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    return json.dumps(data)


def test_request_round_trip_is_exact() -> None:
    request = _request()
    assert request.element_node(request.beams[0], "J") == 30
    assert request.element_node(request.beams[0], "I") == 1
    assert request.element_node(request.beams[1], "I") == 31
    text = request.to_json()
    assert SpatialRequest.from_json(text) == request
    assert SpatialRequest.from_json(text).to_json() == text
    data = json.loads(text)
    assert (data["protocol"], data["version"]) == (PROTOCOL_ID, PROTOCOL_VERSION)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("version",), 1),
        (("protocol",), PLANAR_PROTOCOL_ID),
        (("alpha_sp",), "1"),
        (("nodes", 0), [1, "n1", 0.0, 0.0]),
        (("beams", 0, 10), [0.0, 1.0]),
        (("fixes", 0, 1), [1, 1, 1, 1, 1, 2]),
        (("fixes", 0, 1), [1, 1, 1, 1, 1]),
        (("springs", 0, 4), 7),
        (("springs", 0, 4), 0),
        (("springs", 0, 8), 1),
        (("chains", 0, 6), [0.3, -0.5]),
        (("nodal_loads", 0, 1), [1.0, 2.0, 3.0]),
        (("beam_loads", 0), [1, 1.0, 2.0]),
        (("extra",), 1),
        # E2-b: códigos, A y J efectivos con su marca, aux, enlaces y huérfanos.
        (("beams", 0, 11), 4),
        (("beams", 0, 12), -1),
        (("beams", 0, 13), 0),
        (("beams", 0, 4), 0.0),
        (("beams", 0, 7), 0.0),
        (("beams", 0, 4), -0.08),
        (("beams", 1, 13), False),
        (("beams", 1, 14), False),
        (("beams", 0), [1, "b1", 1, 2, 0.08, 210e9, 81e9, 7.3e-4, 1e-3, 2.6e-4, [0, 0, 1]]),
        (("fixes", 1, 0), 1),
        (("orphan_fixes", 0, 1), [0, 1, 1, 0, 0, 0]),
        (("orphan_fixes", 0, 1), [0, 2, 1]),
        (("orphan_fixes",), [[3, [0, 1, 1]], [3, [1, 0, 0]]]),
        (("aux_nodes", 0, 3), "K"),
        (("aux_nodes", 1), [31, 2, 1, "J"]),
        (("end_equals", 0, 1), []),
        (("end_equals", 0, 1), [2, 1]),
        (("end_equals", 0, 1), [1, 1]),
        (("end_equals", 0, 1), [1, 7]),
        (("end_springs", 0, 2), 0),
        (("end_springs", 0, 6), 1),
        (("end_springs", 0), [40, 30, 5, 2.5e7, [0.6, 0.0, 0.8], [0.0, 1.0, 0.0]]),
        # v3 (ADR-004): una entrada por resorte de penalización, S_s ≥ 0, parada coherente.
        (("version",), 2),
        (("end_penalties",), []),
        (("end_penalties", 0, 0), 40),
        (("end_penalties",), [[41, [0.6, 0.0, 0.8], 1.0], [41, [0.6, 0.0, 0.8], 1.0]]),
        (("end_penalties", 0, 1), [0.6, 0.0]),
        (("end_penalties", 0, 2), -1.0),
        (("end_penalties", 0), [41, [0.6, 0.0, 0.8]]),
        (("end_springs", 1, 6), False),
        (("end_penalty_tolerance",), 0.0),
        (("end_penalty_tolerance",), 1e-6),
        (("end_penalty_stagnation",), "1e-6"),
        (("end_penalty_max_iterations",), 0),
        (("end_penalty_max_iterations",), 2.5),
    ],
)
def test_request_rejects_invalid_content(path: tuple[str | int, ...], value: object) -> None:
    with pytest.raises(ProtocolError):
        SpatialRequest.from_json(_mutated(path, value))


@pytest.mark.parametrize(
    "key",
    [
        "end_penalties",
        "end_penalty_tolerance",
        "end_penalty_stagnation",
        "end_penalty_max_iterations",
    ],
)
def test_request_requires_the_v3_fields(key: str) -> None:
    data = _request().to_dict()
    del data[key]
    with pytest.raises(ProtocolError):
        SpatialRequest.from_json(json.dumps(data))


def test_protocol_version_matches_the_worker() -> None:
    """ADR-004 §5: padre y worker cambian juntos (el worker no importa ``veritas``)."""
    assert (opensees_worker_3d.PROTOCOL_ID, opensees_worker_3d.PROTOCOL_VERSION) == (
        PROTOCOL_ID,
        PROTOCOL_VERSION,
    )
    assert PROTOCOL_VERSION == 5  # T-015: cargas de barra


@pytest.mark.parametrize(
    ("force", "scale", "expected"),
    [(2.0, 4.0, 0.5), (-2.0, 4.0, 0.5), (0.0, 0.0, 0.0), (1e-30, 0.0, float("inf"))],
)
def test_penalty_step_matches_the_worker(force: float, scale: float, expected: float) -> None:
    assert penalty_step(force, scale) == expected
    assert opensees_worker_3d._step(force, scale) == expected


def test_nan_is_rejected_on_both_sides() -> None:
    request = _request()
    bad = SpatialRequest(
        **{**{f: getattr(request, f) for f in request.__slots__}, "alpha_sp": float("nan")}
    )
    with pytest.raises(ValueError, match="JSON compliant"):
        bad.to_json()
    with pytest.raises(NonFiniteValueError):
        SpatialRequest.from_json(request.to_json().replace("5600000000.0", "NaN", 1))


def _response(**overrides: object) -> str:
    data: dict[str, object] = {
        "protocol": PROTOCOL_ID,
        "version": PROTOCOL_VERSION,
        "status": "OK",
        "analyze_code": 0,
        "message": "",
        "versions": {"python": "3.12.9", "openseespy": "3.8.0.0", "openseespywin": None},
        "nodes": [[1, [0.0] * 6, [1.0] * 6]],
        "grounds": [[11, [0.5] * 6]],
        "chains": [[20, [0.1] * 6, [0.2] * 6, [0.0] * 6]],
        "beams": [[1, [0.0] * 12]],
        "aux": [[30, [0.3] * 6, [0.0] * 6]],
        "end_springs": [[40, 12.5, 5e-7, 0.0], [41, -3.0, -5e-16, 41.5]],
        "end_penalty_iterations": 4,
        "end_penalty_stop": "tolerance",
        "end_penalty_converged": True,
    }
    data.update(overrides)
    return json.dumps(data)


def test_response_parsing() -> None:
    response = SpatialWorkerResponse.from_json(_response())
    assert response.status == "OK"
    assert response.node_results == ((1, (0.0,) * 6, (1.0,) * 6),)
    assert response.ground_reactions == ((11, (0.5,) * 6),)
    assert response.chain_results[0].r_disp == (0.2,) * 6
    assert response.beam_forces == ((1, (0.0,) * 12),)
    assert response.aux_results == ((30, (0.3,) * 6, (0.0,) * 6),)
    assert response.end_spring_results == ((40, 12.5, 5e-7, 0.0), (41, -3.0, -5e-16, 41.5))
    assert (
        response.end_penalty_iterations,
        response.end_penalty_stop,
        response.end_penalty_converged,
    ) == (4, "tolerance", True)


@pytest.mark.parametrize(
    ("iterations", "stop", "converged"),
    [(0, "none", True), (3, "stagnation", True), (20, "max_iterations", False)],
)
def test_response_accepts_coherent_stops(iterations: int, stop: str, converged: bool) -> None:
    response = SpatialWorkerResponse.from_json(
        _response(
            end_penalty_iterations=iterations,
            end_penalty_stop=stop,
            end_penalty_converged=converged,
        )
    )
    assert response.end_penalty_converged is converged


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "RARO"},
        {"version": 1},
        {"protocol": PLANAR_PROTOCOL_ID},
        {"beams": [[1, [0.0] * 6]]},
        {"nodes": [[1, [0.0] * 3, [0.0] * 3]]},
        {"chains": [[20, [0.0] * 6, [0.0] * 6]]},
        {"analyze_code": 1.5},
        {"aux": [[30, [0.0] * 6]]},
        {"end_springs": [[40, [12.5], 5e-7, 0.0]]},
        {"end_springs": [[40, 12.5, 5e-7]]},
        {"version": 2},
        {"end_penalty_stop": "otro"},
        {"end_penalty_iterations": -1},
        {"end_penalty_iterations": 0},
        {"end_penalty_stop": "none"},
        {"end_penalty_converged": False},
        {"end_penalty_stop": "max_iterations"},
        {"end_penalty_converged": 1},
    ],
)
def test_response_rejects_invalid_content(overrides: dict[str, object]) -> None:
    with pytest.raises(ProtocolError):
        SpatialWorkerResponse.from_json(_response(**overrides))


def test_response_rejects_unknown_keys_and_invalid_json() -> None:
    with pytest.raises(ProtocolError):
        SpatialWorkerResponse.from_json(_response(grounds_extra=[]))
    with pytest.raises(ProtocolError):
        SpatialWorkerResponse.from_json("{")


def test_configuration_defaults_and_snapshot() -> None:
    case_id = uuid4()
    snapshot = SpatialAnalysisConfiguration(case_id).snapshot()
    assert snapshot == {
        "load_case_id": str(case_id),
        "timeout_s": 60.0,
        "tol_axis": 1e-9,
        "tol_pivot": 1e-12,
        "penalty_factor": 1e8,
        "tol_penalty": 1e-6,
        "tol_equilibrium": 1e-9,
        "max_free_dofs": 300,
        "end_penalty_factor": 1e1,
        "end_penalty_tolerance": 1e-10,
        "end_penalty_max_iterations": 20,
    }


def test_configuration_is_independent_of_the_planar_one() -> None:
    """D1-A: la configuración plana no cambia; la espacial no arrastra sus campos del plano."""
    spatial = set(SpatialAnalysisConfiguration(uuid4()).snapshot())
    planar = set(AnalysisConfiguration(uuid4()).snapshot())
    assert {"tol_plane", "tol_member_load_y"} <= planar - spatial
    assert spatial - planar == {
        "tol_penalty",
        "end_penalty_factor",
        "end_penalty_tolerance",
        "end_penalty_max_iterations",
    }


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("timeout_s", 0.0, DiagnosticCode.INPUT_OUT_OF_RANGE),
        ("tol_pivot", -1.0, DiagnosticCode.INPUT_OUT_OF_RANGE),
        ("tol_penalty", 0.0, DiagnosticCode.INPUT_OUT_OF_RANGE),
        ("tol_axis", float("nan"), DiagnosticCode.INPUT_NONFINITE),
        ("penalty_factor", float("inf"), DiagnosticCode.INPUT_NONFINITE),
        ("max_free_dofs", 0, DiagnosticCode.INPUT_OUT_OF_RANGE),
        ("end_penalty_factor", 0.0, DiagnosticCode.INPUT_OUT_OF_RANGE),
        ("end_penalty_tolerance", 0.0, DiagnosticCode.INPUT_OUT_OF_RANGE),
        ("end_penalty_tolerance", 1e-6, DiagnosticCode.INPUT_OUT_OF_RANGE),
        ("end_penalty_tolerance", float("nan"), DiagnosticCode.INPUT_NONFINITE),
        ("end_penalty_max_iterations", 0, DiagnosticCode.INPUT_OUT_OF_RANGE),
    ],
)
def test_configuration_rejects_invalid_values(
    field: str, value: float, code: DiagnosticCode
) -> None:
    with pytest.raises(InputValueError) as info:
        SpatialAnalysisConfiguration(uuid4(), **{field: value})  # type: ignore[arg-type]
    assert info.value.code is code


def test_configuration_rejects_wrong_types() -> None:
    with pytest.raises(InputTypeError):
        SpatialAnalysisConfiguration("caso")  # type: ignore[arg-type]
    with pytest.raises(InputTypeError):
        SpatialAnalysisConfiguration(uuid4(), max_free_dofs=2.5)  # type: ignore[arg-type]
    with pytest.raises(InputTypeError):
        SpatialAnalysisConfiguration(uuid4(), max_free_dofs=True)
    with pytest.raises(InputTypeError):
        SpatialAnalysisConfiguration(uuid4(), end_penalty_max_iterations=20.0)  # type: ignore[arg-type]
    with pytest.raises(InputTypeError):
        SpatialAnalysisConfiguration(uuid4(), end_penalty_max_iterations=True)
