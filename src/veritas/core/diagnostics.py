"""Diagnósticos estructurados según ADR-001 §5.

Un diagnóstico tiene código estable ASCII, clave de mensaje traducible, parámetros
tipados, severidad, ubicación opcional y causa técnica separada. La lógica del
programa decide por ``code``; el texto traducido es solo para presentación.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal
from uuid import UUID

type ParamValue = str | int | float


class Severity(StrEnum):
    """Severidad de un diagnóstico. ERROR bloquea; WARNING no habilita lo no soportado."""

    ERROR = "ERROR"
    WARNING = "WARNING"


class DiagnosticCode(StrEnum):
    """Códigos estables. Tabla inicial de ADR-001 §5 más ampliaciones documentadas."""

    INPUT_INVALID_TYPE = "INPUT_INVALID_TYPE"
    INPUT_NONFINITE = "INPUT_NONFINITE"
    UNITS_INCOMPATIBLE = "UNITS_INCOMPATIBLE"
    UNITS_UNREPRESENTABLE = "UNITS_UNREPRESENTABLE"
    UNITS_UNKNOWN = "UNITS_UNKNOWN"
    GEOMETRY_ZERO_LENGTH = "GEOMETRY_ZERO_LENGTH"
    GEOMETRY_INVALID_ORIENTATION = "GEOMETRY_INVALID_ORIENTATION"
    REFERENCE_NOT_FOUND = "REFERENCE_NOT_FOUND"
    REVISION_CONFLICT = "REVISION_CONFLICT"
    UNSUPPORTED_CONFIGURATION = "UNSUPPORTED_CONFIGURATION"
    ANALYSIS_SINGULAR = "ANALYSIS_SINGULAR"
    ANALYSIS_NONCONVERGENCE = "ANALYSIS_NONCONVERGENCE"
    ANALYSIS_FAILED = "ANALYSIS_FAILED"


def _is_ascii_identifier(text: str) -> bool:
    return bool(text) and text.isascii() and text.strip() == text


@dataclass(frozen=True, slots=True)
class Location:
    """Ubicación opcional de un diagnóstico.

    ``station`` es la abscisa s en metros a lo largo de la barra (ADR-001 §1).
    """

    entity_id: UUID | None = None
    revision: int | None = None
    field: str | None = None
    end: Literal["I", "J"] | None = None
    station: float | None = None

    def __post_init__(self) -> None:
        if self.entity_id is not None and not isinstance(self.entity_id, UUID):
            raise TypeError("Location.entity_id debe ser UUID")
        if self.revision is not None and (
            isinstance(self.revision, bool) or not isinstance(self.revision, int)
        ):
            raise TypeError("Location.revision debe ser un entero")
        if self.revision is not None and self.revision < 1:
            raise ValueError("Location.revision debe ser >= 1")
        if self.field is not None and (not isinstance(self.field, str) or not self.field):
            raise TypeError("Location.field debe ser un texto no vacío")
        if self.end is not None and self.end not in ("I", "J"):
            raise ValueError("Location.end debe ser 'I' o 'J'")
        if self.station is not None:
            if isinstance(self.station, bool) or not isinstance(self.station, int | float):
                raise TypeError("Location.station debe ser un número real")
            if not math.isfinite(self.station) or self.station < 0:
                raise ValueError("Location.station debe ser finita y >= 0")


def _freeze_params(params: Mapping[str, ParamValue]) -> tuple[tuple[str, ParamValue], ...]:
    frozen: list[tuple[str, ParamValue]] = []
    for name, value in params.items():
        if not isinstance(name, str) or not _is_ascii_identifier(name):
            raise TypeError(f"Nombre de parámetro de diagnóstico inválido: {name!r}")
        if isinstance(value, bool) or not isinstance(value, str | int | float):
            raise TypeError(f"Valor de parámetro de diagnóstico no admitido: {name}={value!r}")
        frozen.append((name, value))
    return tuple(sorted(frozen))


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """Diagnóstico inmutable (ADR-001 §5)."""

    code: DiagnosticCode
    message_key: str
    params: tuple[tuple[str, ParamValue], ...] = ()
    severity: Severity = Severity.ERROR
    location: Location | None = None
    cause: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.code, DiagnosticCode):
            raise TypeError("Diagnostic.code debe ser DiagnosticCode")
        if not isinstance(self.message_key, str) or not _is_ascii_identifier(self.message_key):
            raise TypeError("Diagnostic.message_key debe ser texto ASCII no vacío")
        if not isinstance(self.severity, Severity):
            raise TypeError("Diagnostic.severity debe ser Severity")
        if self.location is not None and not isinstance(self.location, Location):
            raise TypeError("Diagnostic.location debe ser Location")

    @classmethod
    def create(  # noqa: PLR0913 (campos del contrato ADR-001 §5, opcionales por nombre)
        cls,
        code: DiagnosticCode,
        message_key: str,
        params: Mapping[str, ParamValue] | None = None,
        *,
        severity: Severity = Severity.ERROR,
        location: Location | None = None,
        cause: str | None = None,
    ) -> "Diagnostic":
        """Construye un diagnóstico validando y congelando los parámetros."""
        return cls(
            code=code,
            message_key=message_key,
            params=_freeze_params(params or {}),
            severity=severity,
            location=location,
            cause=cause,
        )

    def params_dict(self) -> dict[str, ParamValue]:
        """Parámetros como diccionario (copia)."""
        return dict(self.params)


class DiagnosticError(Exception):
    """Excepción que transporta un ``Diagnostic``."""

    def __init__(self, diagnostic: Diagnostic) -> None:
        from veritas.core.messages_es import render_message  # noqa: PLC0415 (evita ciclo)

        self.diagnostic = diagnostic
        super().__init__(f"{diagnostic.code}: {render_message(diagnostic)}")

    @property
    def code(self) -> DiagnosticCode:
        return self.diagnostic.code


class InputTypeError(DiagnosticError, TypeError):
    """Tipo de entrada incorrecto, con diagnóstico."""


class InputValueError(DiagnosticError, ValueError):
    """Valor de entrada inválido, con diagnóstico."""
