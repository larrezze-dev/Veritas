"""Protocolo espacial y configuración (plan T-018, D2 y D6): validación estricta y round-trip."""

import json
from typing import Any
from uuid import uuid4

import pytest

from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError
from veritas.fem.adapters.protocol import PROTOCOL_ID as PLANAR_PROTOCOL_ID
from veritas.fem.adapters.protocol import NonFiniteValueError, ProtocolError
from veritas.fem.config import AnalysisConfiguration
from veritas.fem.spatial import SpatialAnalysisConfiguration
from veritas.fem.spatial.protocol import (
    PROTOCOL_ID,
    PROTOCOL_VERSION,
    SpatialBeam,
    SpatialBeamLoad,
    SpatialChain,
    SpatialFix,
    SpatialNodalLoad,
    SpatialNode,
    SpatialRequest,
    SpatialSpring,
    SpatialWorkerResponse,
)


def _request() -> SpatialRequest:
    return SpatialRequest(
        model_revision=3,
        case_id="caso",
        case_revision=1,
        alpha_sp=5.6e9,
        alpha_mp=5.6e9,
        nodes=(SpatialNode(1, "n1", 0.0, 0.0, 0.0), SpatialNode(2, "n2", 1.2, -0.7, 0.9)),
        beams=(
            SpatialBeam(1, "b1", 1, 2, 0.08, 210e9, 81e9, 7.3e-4, 1.0e-3, 2.6e-4, (0.1, 0.2, 0.97)),
        ),
        fixes=(SpatialFix(1, (1, 1, 1, 1, 1, 1)),),
        springs=(SpatialSpring(10, "s", 2, 11, 4, 1e6, (1.0, 0.0, 0.0), (0.0, 1.0, 0.0), False),),
        chains=(
            SpatialChain(20, "r", 2, 21, 22, 23, (0.3, -0.5, 0.81), (0.0, 0.85, 0.52), 1.6, 5.6e9),
        ),
        nodal_loads=(SpatialNodalLoad(2, (3000.0, -2000.0, -5000.0, 400.0, -700.0, 900.0)),),
        beam_loads=(SpatialBeamLoad(1, -2500.0, 4000.0, 1500.0),),
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
    text = request.to_json()
    assert SpatialRequest.from_json(text) == request
    assert SpatialRequest.from_json(text).to_json() == text
    data = json.loads(text)
    assert (data["protocol"], data["version"]) == (PROTOCOL_ID, PROTOCOL_VERSION)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("version",), 2),
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
    ],
)
def test_request_rejects_invalid_content(path: tuple[str | int, ...], value: object) -> None:
    with pytest.raises(ProtocolError):
        SpatialRequest.from_json(_mutated(path, value))


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


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "RARO"},
        {"version": 2},
        {"protocol": PLANAR_PROTOCOL_ID},
        {"beams": [[1, [0.0] * 6]]},
        {"nodes": [[1, [0.0] * 3, [0.0] * 3]]},
        {"chains": [[20, [0.0] * 6, [0.0] * 6]]},
        {"analyze_code": 1.5},
    ],
)
def test_response_rejects_invalid_content(overrides: dict[str, object]) -> None:
    with pytest.raises(ProtocolError):
        SpatialWorkerResponse.from_json(_response(**overrides))


def test_response_rejects_unknown_keys_and_invalid_json() -> None:
    with pytest.raises(ProtocolError):
        SpatialWorkerResponse.from_json(_response(aux=[]))
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
    }


def test_configuration_is_independent_of_the_planar_one() -> None:
    """D1-A: la configuración plana no cambia; la espacial no arrastra sus campos del plano."""
    spatial = set(SpatialAnalysisConfiguration(uuid4()).snapshot())
    planar = set(AnalysisConfiguration(uuid4()).snapshot())
    assert {"tol_plane", "tol_member_load_y"} <= planar - spatial
    assert spatial - planar == {"tol_penalty"}


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("timeout_s", 0.0, DiagnosticCode.INPUT_OUT_OF_RANGE),
        ("tol_pivot", -1.0, DiagnosticCode.INPUT_OUT_OF_RANGE),
        ("tol_penalty", 0.0, DiagnosticCode.INPUT_OUT_OF_RANGE),
        ("tol_axis", float("nan"), DiagnosticCode.INPUT_NONFINITE),
        ("penalty_factor", float("inf"), DiagnosticCode.INPUT_NONFINITE),
        ("max_free_dofs", 0, DiagnosticCode.INPUT_OUT_OF_RANGE),
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
