"""Dictámenes independientes de una sección: «apta para FEM» y «cubierta por una norma»
(SEC-013; T-024 E2, D4-A).

Son dos preguntas distintas y nunca se funden en un único «apta»:

- ``fem_ready``: están disponibles todas las propiedades que necesita el análisis (A, Iy, Iz, J,
  centroide e ``Iyz``); por cada una ausente hay un diagnóstico ``section.fem_missing``. Que una
  sección sea apta para FEM no dice nada de la normativa.
- ``code_coverage``: cobertura normativa. Mientras no exista una verificación normativa validada
  es siempre ``NO_COMPROBADO``, con su motivo, también para una sección rectangular completa.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from veritas.core.diagnostics import Diagnostic, DiagnosticCode, Severity
from veritas.core.sections.properties import PropertyStatus
from veritas.core.sections.section import SectionRevision

FEM_PROPERTIES: Final = (
    ("area", "A"),
    ("iy", "Iy"),
    ("iz", "Iz"),
    ("torsion_constant", "J"),
    ("centroid_y", "centroide y"),
    ("centroid_z", "centroide z"),
    ("iyz", "Iyz"),
)
"""Propiedades exigidas para «apta para FEM» (campo de ``SectionProperties`` y etiqueta)."""


class CodeCoverage(StrEnum):
    """Cobertura normativa. Solo existe ``NO_COMPROBADO`` hasta que haya una norma validada."""

    NO_COMPROBADO = "NO_COMPROBADO"


@dataclass(frozen=True, slots=True)
class SectionReadiness:
    """Los dos dictámenes de una sección, por separado."""

    fem_ready: bool
    fem_missing: tuple[str, ...]
    code_coverage: CodeCoverage
    code_reason_key: str

    def diagnostics(self) -> tuple[Diagnostic, ...]:
        """Un aviso por cada propiedad FEM ausente y el motivo de la cobertura normativa."""
        missing = tuple(
            Diagnostic.create(
                DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                "section.fem_missing",
                {"property": name},
                severity=Severity.WARNING,
            )
            for name in self.fem_missing
        )
        reason = Diagnostic.create(
            DiagnosticCode.UNSUPPORTED_CONFIGURATION,
            self.code_reason_key,
            severity=Severity.WARNING,
        )
        return (*missing, reason)


def assess_section(section: SectionRevision) -> SectionReadiness:
    """Dictámenes FEM y de norma de una revisión de sección."""
    missing = tuple(
        label
        for field, label in FEM_PROPERTIES
        if getattr(section.properties, field).status is PropertyStatus.NO_DISPONIBLE
        or getattr(section.properties, field).value is None
    )
    return SectionReadiness(
        fem_ready=not missing,
        fem_missing=missing,
        code_coverage=CodeCoverage.NO_COMPROBADO,
        code_reason_key="section.code_not_verified",
    )
