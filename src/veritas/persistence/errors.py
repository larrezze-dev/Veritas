"""Errores del archivo de proyecto: diagnósticos con claves ``project.*`` (plan T-012, D5-A).

Se reutilizan los códigos existentes de ADR-001 §5; no se añade ninguno. Una falla de
integridad tiene su propia clase para que la frontera (CLI) pueda distinguirla de un
archivo bien formado pero inválido.
"""

from collections.abc import Mapping

from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    InputTypeError,
    InputValueError,
    ParamValue,
)


class IntegrityError(InputValueError):
    """El resumen SHA-256 del archivo no coincide con su contenido."""


def format_error(
    key: str, params: Mapping[str, ParamValue] | None = None, *, path: str = ""
) -> InputTypeError:
    """Estructura, tipo o campo erróneo (``INPUT_INVALID_TYPE``)."""
    merged: dict[str, ParamValue] = {"path": path or "(raíz)"}
    merged.update(params or {})
    return InputTypeError(Diagnostic.create(DiagnosticCode.INPUT_INVALID_TYPE, key, merged))


def value_error(
    code: DiagnosticCode, key: str, params: Mapping[str, ParamValue] | None = None
) -> InputValueError:
    """Valor del archivo fuera de rango, no soportado o inconsistente."""
    return InputValueError(Diagnostic.create(code, key, params))


def integrity_error(expected: str, actual: str) -> IntegrityError:
    """El archivo fue alterado o truncado: el resumen guardado no coincide."""
    return IntegrityError(
        Diagnostic.create(
            DiagnosticCode.INPUT_OUT_OF_RANGE,
            "project.integrity",
            {"expected": expected, "actual": actual},
        )
    )
