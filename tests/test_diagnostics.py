"""Pruebas del contrato de diagnósticos (ADR-001 §5)."""

import re
from pathlib import Path
from uuid import uuid4

import pytest

import veritas
from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    DiagnosticError,
    InputTypeError,
    InputValueError,
    Location,
    Severity,
)
from veritas.core.messages_es import MESSAGES_ES, render_message

ADR_001_CODES = {
    "INPUT_INVALID_TYPE",
    "INPUT_NONFINITE",
    "UNITS_INCOMPATIBLE",
    "UNITS_UNREPRESENTABLE",
    "GEOMETRY_ZERO_LENGTH",
    "GEOMETRY_INVALID_ORIENTATION",
    "REFERENCE_NOT_FOUND",
    "REVISION_CONFLICT",
    "UNSUPPORTED_CONFIGURATION",
    "ANALYSIS_SINGULAR",
    "ANALYSIS_NONCONVERGENCE",
    "ANALYSIS_FAILED",
}


def test_codes_cover_adr_001_table_plus_documented_extensions() -> None:
    assert {code.value for code in DiagnosticCode} == ADR_001_CODES | {"UNITS_UNKNOWN"}
    assert all(code.value.isascii() for code in DiagnosticCode)


def test_diagnostic_is_immutable_with_sorted_params() -> None:
    diagnostic = Diagnostic.create(
        DiagnosticCode.UNITS_UNKNOWN, "units.unknown", {"symbol": "x", "a": 1}
    )
    assert diagnostic.params == (("a", 1), ("symbol", "x"))
    assert diagnostic.severity is Severity.ERROR
    with pytest.raises(AttributeError):
        diagnostic.code = DiagnosticCode.ANALYSIS_FAILED  # type: ignore[misc]
    assert hash(diagnostic) == hash(
        Diagnostic.create(DiagnosticCode.UNITS_UNKNOWN, "units.unknown", {"a": 1, "symbol": "x"})
    )


def test_cause_is_kept_but_does_not_affect_equality() -> None:
    first = Diagnostic.create(DiagnosticCode.ANALYSIS_FAILED, "x.y", cause="traza A")
    second = Diagnostic.create(DiagnosticCode.ANALYSIS_FAILED, "x.y", cause="traza B")
    assert first.cause == "traza A"
    assert first == second


@pytest.mark.parametrize(
    "params",
    [{"ok": True}, {"ok": [1]}, {"": 1}, {"ñ": 1}],
)
def test_rejects_invalid_params(params: dict[str, object]) -> None:
    with pytest.raises(TypeError):
        Diagnostic.create(DiagnosticCode.ANALYSIS_FAILED, "x.y", params)  # type: ignore[arg-type]


def test_rejects_invalid_fields() -> None:
    with pytest.raises(TypeError):
        Diagnostic(code="UNITS_UNKNOWN", message_key="x.y")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        Diagnostic(code=DiagnosticCode.UNITS_UNKNOWN, message_key="")
    with pytest.raises(TypeError):
        Diagnostic(code=DiagnosticCode.UNITS_UNKNOWN, message_key="x", severity="ERROR")  # type: ignore[arg-type]


def test_location_full() -> None:
    entity = uuid4()
    location = Location(entity_id=entity, revision=2, field="geometry", end="J", station=1.5)
    assert location.entity_id == entity
    assert location.end == "J"


@pytest.mark.parametrize(
    ("kwargs", "error"),
    [
        ({"entity_id": "abc"}, TypeError),
        ({"revision": True}, TypeError),
        ({"revision": 0}, ValueError),
        ({"field": ""}, TypeError),
        ({"end": "K"}, ValueError),
        ({"station": -1.0}, ValueError),
        ({"station": float("nan")}, ValueError),
        ({"station": "1"}, TypeError),
    ],
)
def test_location_validation(kwargs: dict[str, object], error: type[Exception]) -> None:
    with pytest.raises(error):
        Location(**kwargs)  # type: ignore[arg-type]


def test_exceptions_keep_standard_hierarchy_and_code() -> None:
    diagnostic = Diagnostic.create(
        DiagnosticCode.UNITS_INCOMPATIBLE, "units.incompatible", {"left": "L", "right": "M"}
    )
    value_error = InputValueError(diagnostic)
    type_error = InputTypeError(diagnostic)
    assert isinstance(value_error, ValueError)
    assert isinstance(value_error, DiagnosticError)
    assert isinstance(type_error, TypeError)
    assert value_error.code is DiagnosticCode.UNITS_INCOMPATIBLE
    assert value_error.diagnostic is diagnostic
    assert str(value_error) == "UNITS_INCOMPATIBLE: Dimensiones incompatibles: L frente a M."


def test_render_falls_back_without_crashing() -> None:
    unknown_key = Diagnostic.create(DiagnosticCode.ANALYSIS_FAILED, "no.existe")
    assert render_message(unknown_key) == "no.existe"
    missing_param = Diagnostic.create(DiagnosticCode.UNITS_UNKNOWN, "units.unknown")
    assert render_message(missing_param) == MESSAGES_ES["units.unknown"]


def test_every_emitted_message_key_is_in_catalog() -> None:
    package_root = Path(veritas.__file__).resolve().parent
    pattern = re.compile(r'"([a-z]+\.[a-z_]+)"')  # Toda cadena con forma de clave.
    keys = {
        key
        for path in package_root.rglob("*.py")
        if path.name != "messages_es.py"
        for key in pattern.findall(path.read_text(encoding="utf-8"))
    }
    assert keys
    assert keys <= set(MESSAGES_ES)
