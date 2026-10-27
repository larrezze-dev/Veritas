"""Protocolo padre ↔ worker y configuración (plan T-007): validación estricta y round-trip."""

import json
from uuid import uuid4

import pytest

from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError
from veritas.fem.adapters.protocol import (
    PROTOCOL_ID,
    PROTOCOL_VERSION,
    NonFiniteValueError,
    PlanarBeam,
    PlanarBeamLoad,
    PlanarFix,
    PlanarNodalLoad,
    PlanarNode,
    PlanarRequest,
    PlanarSpring,
    ProtocolError,
    WorkerResponse,
    dumps,
)
from veritas.fem.config import AnalysisConfiguration


def request() -> PlanarRequest:
    return PlanarRequest(
        model_revision=7,
        case_id=str(uuid4()),
        case_revision=1,
        nodes=(PlanarNode(1, str(uuid4()), 0.0, 0.0), PlanarNode(2, str(uuid4()), 2.0, -0.0)),
        beams=(PlanarBeam(1, str(uuid4()), 1, 2, 0.08, 2.1e11, 1.0e-3, "Iy"),),
        fixes=(PlanarFix(1, (1, 1, 1)),),
        springs=(PlanarSpring(2, str(uuid4()), 2, 3, 2, 1e6, (0.6, 0.8), (-0.8, 0.6), False),),
        nodal_loads=(PlanarNodalLoad(2, 0.0, -1000.0, 5.0),),
        beam_loads=(PlanarBeamLoad(1, -10.0, 2.5),),
    )


def response(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "protocol": PROTOCOL_ID,
        "version": PROTOCOL_VERSION,
        "status": "OK",
        "analyze_code": 0,
        "message": "",
        "versions": {"python": "3.12.9", "openseespy": "3.8.0.0", "openseespywin": None},
        "nodes": [[1, [0.0, 0.0, 0.0], [1.0, 2.0, 3.0]]],
        "aux": [[3, [0.0, 1.0, 0.0]]],
        "beams": [[1, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]]],
    }
    data.update(overrides)
    return data


def test_request_round_trip_is_exact() -> None:
    original = request()
    text = original.to_json()
    assert PlanarRequest.from_json(text) == original
    assert PlanarRequest.from_json(text).to_json() == text
    assert json.loads(text)["protocol"] == PROTOCOL_ID


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("protocol",), "otro"),
        (("version",), 2),
        (("extra",), 1),
        (("model_revision",), 1.5),
        (("model_revision",), True),
        (("nodes",), [[1, "x", 0.0]]),
        (("nodes",), [[1, "x", "0", 0.0]]),
        (("springs",), [[2, "s", 2, 3, 2, 1e6, [0.6], [-0.8, 0.6], False]]),
        (("springs",), [[2, "s", 2, 3, 2, 1e6, [0.6, 0.8], [-0.8, 0.6], 0]]),
    ],
)
def test_request_rejects_invalid_content(path: tuple[str, ...], value: object) -> None:
    data = request().to_dict()
    data[path[0]] = value
    with pytest.raises(ProtocolError):
        PlanarRequest.from_json(json.dumps(data))


def test_nan_is_rejected_on_both_sides() -> None:
    data = request().to_dict()
    data["nodal_loads"] = [[2, float("nan"), 0.0, 0.0]]
    with pytest.raises(ValueError, match="not JSON compliant"):
        dumps(data)  # El padre nunca envía NaN/Inf.
    with pytest.raises(NonFiniteValueError):
        PlanarRequest.from_json(json.dumps(data))
    with pytest.raises(NonFiniteValueError):
        WorkerResponse.from_json(json.dumps(response(nodes=[[1, [float("inf"), 0, 0], [0, 0, 0]]])))


def test_response_parsing() -> None:
    parsed = WorkerResponse.from_json(json.dumps(response()))
    assert parsed.status == "OK"
    assert parsed.node_results == ((1, (0.0, 0.0, 0.0), (1.0, 2.0, 3.0)),)
    assert parsed.aux_reactions == ((3, (0.0, 1.0, 0.0)),)
    assert parsed.versions["openseespywin"] is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "MAYBE"},
        {"version": 99},
        {"analyze_code": "0"},
        {"beams": [[1, [1.0, 2.0]]]},
        {"versions": {"python": "3.12"}},
    ],
)
def test_response_rejects_invalid_content(overrides: dict[str, object]) -> None:
    with pytest.raises(ProtocolError):
        WorkerResponse.from_json(json.dumps(response(**overrides)))


def test_invalid_json_is_protocol_error() -> None:
    with pytest.raises(ProtocolError):
        WorkerResponse.from_json("{no es json")
    with pytest.raises(ProtocolError):
        WorkerResponse.from_json("[]")


def test_configuration_defaults_and_snapshot() -> None:
    case_id = uuid4()
    config = AnalysisConfiguration(case_id)
    snapshot = config.snapshot()
    assert snapshot["load_case_id"] == str(case_id)
    assert snapshot["tol_pivot"] == 1e-12
    assert snapshot["penalty_factor"] == 1e8
    assert snapshot["timeout_s"] == 60.0
    assert snapshot["max_free_dofs"] == 300


@pytest.mark.parametrize(
    ("field", "value", "code"),
    [
        ("timeout_s", 0.0, DiagnosticCode.INPUT_OUT_OF_RANGE),
        ("tol_pivot", -1.0, DiagnosticCode.INPUT_OUT_OF_RANGE),
        ("tol_plane", float("nan"), DiagnosticCode.INPUT_NONFINITE),
        ("max_free_dofs", 0, DiagnosticCode.INPUT_OUT_OF_RANGE),
    ],
)
def test_configuration_rejects_invalid_values(
    field: str, value: float, code: DiagnosticCode
) -> None:
    with pytest.raises(InputValueError) as info:
        AnalysisConfiguration(uuid4(), **{field: value})  # type: ignore[arg-type]
    assert info.value.code is code


def test_configuration_rejects_wrong_types() -> None:
    with pytest.raises(InputTypeError):
        AnalysisConfiguration("caso")  # type: ignore[arg-type]
    with pytest.raises(InputTypeError):
        AnalysisConfiguration(uuid4(), max_free_dofs=2.5)  # type: ignore[arg-type]
