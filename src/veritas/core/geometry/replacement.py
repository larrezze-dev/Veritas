"""Informe de la sustitución masiva de secciones (SEC-012; T-024 E2, D5-A).

Datos puros del resultado de ``StructuralModel.replace_sections``: por barra, qué ocurrió y por
qué. Nunca hay resultado parcial silencioso: toda barra de la selección consta en el informe,
con su estado, los motivos de bloqueo y los avisos.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from veritas.core.diagnostics import Diagnostic, DiagnosticCode, Severity
from veritas.core.materials.material import MaterialFamily, MaterialRevision
from veritas.core.references import RevisionRef
from veritas.core.sections.catalog import CatalogSnapshot
from veritas.core.sections.parametric import ParametricParameters
from veritas.core.sections.readiness import assess_section
from veritas.core.sections.section import SectionRevision

MASS_CHANGE_WARNING: Final = 0.20
"""Variación relativa de masa lineal a partir de la cual se avisa (no bloquea)."""

METAL_SHAPES: Final = frozenset({"I", "U", "L", "T", "SHS", "RHS", "CHS"})
"""Formas de perfil laminado o extruido: incompatibles con un material de madera (D5-A)."""


class ReplacementStatus(StrEnum):
    """Resultado por barra: ``BLOQUEADA`` = incompatible; ``OMITIDA`` = compatible pero no
    aplicada porque el modo estricto rechazó la sustitución completa."""

    APPLIED = "APLICADA"
    UNCHANGED = "SIN_CAMBIO"
    OMITTED = "OMITIDA"
    BLOCKED = "BLOQUEADA"


@dataclass(frozen=True, slots=True)
class MemberReplacement:
    """Qué pasó con una barra: estado, bloqueos (errores) y avisos."""

    member_id: UUID
    label: str
    status: ReplacementStatus
    previous: RevisionRef
    blocking: tuple[Diagnostic, ...] = ()
    warnings: tuple[Diagnostic, ...] = ()
    mass_change: float | None = None
    """Variación relativa de masa lineal (nueva/anterior − 1), o ``None`` si no es comparable."""


@dataclass(frozen=True, slots=True)
class ReplacementReport:
    """Informe completo de una sustitución."""

    section: RevisionRef
    partial: bool
    members: tuple[MemberReplacement, ...]
    model_revision_before: int
    model_revision_after: int

    @property
    def applied(self) -> tuple[MemberReplacement, ...]:
        return tuple(m for m in self.members if m.status is ReplacementStatus.APPLIED)

    @property
    def rejected(self) -> tuple[MemberReplacement, ...]:
        """Barras bloqueadas por incompatibilidad u omitidas por el modo parcial."""
        return tuple(
            m
            for m in self.members
            if m.status in (ReplacementStatus.BLOCKED, ReplacementStatus.OMITTED)
        )

    @property
    def changed(self) -> bool:
        return self.model_revision_after != self.model_revision_before


def compatibility_errors(
    section: SectionRevision, material: MaterialRevision, member: str
) -> tuple[Diagnostic, ...]:
    """Bloqueos de asignar ``section`` a una barra de ``material`` (D5-A; T-028, ADR-012).

    Dos reglas independientes que se acumulan (R3):

    - Forma: un perfil metálico (I, U, L, T, SHS, RHS, CHS) con un material de madera se bloquea.
    - Material declarado (R1): si el perfil de catálogo declara ``compatible_materials`` y la
      familia del material de la barra no figura, se bloquea con la lista declarada. Sin
      declarar (vacío, o snapshot de un proyecto anterior) no se infiere nada (R2/R5).
    """
    params = section.parameters
    # Los perfiles paramétricos (I, RHS, SHS, CHS; T-025) son todos de forma metálica.
    shape = (
        str(params.shape) if isinstance(params, CatalogSnapshot | ParametricParameters) else None
    )
    errors: list[Diagnostic] = []
    if shape in METAL_SHAPES and material.family is MaterialFamily.TIMBER:
        errors.append(
            Diagnostic.create(
                DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                "section.replace_incompatible_material",
                {"member": member, "shape": str(shape), "family": str(material.family)},
            )
        )
    if (
        isinstance(params, CatalogSnapshot)
        and params.compatible_materials
        and str(material.family) not in params.compatible_materials
    ):
        errors.append(
            Diagnostic.create(
                DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                "section.replace_material_not_declared",
                {
                    "member": member,
                    "declared": ", ".join(params.compatible_materials),
                    "family": str(material.family),
                },
            )
        )
    return tuple(errors)


def _warning(key: str, **params: str) -> Diagnostic:
    return Diagnostic.create(
        DiagnosticCode.UNSUPPORTED_CONFIGURATION, key, params, severity=Severity.WARNING
    )


def replacement_warnings(
    old: SectionRevision, new: SectionRevision, member: str
) -> tuple[tuple[Diagnostic, ...], float | None]:
    """Avisos (no bloquean) de una sustitución y la variación relativa de masa lineal.

    La densidad es la misma antes y después (una barra tiene un solo material): la variación
    de masa ρ·A es el cociente de áreas menos 1.
    """
    warnings: list[Diagnostic] = []
    area_old, area_new = old.properties.area.value, new.properties.area.value
    change = None if area_old is None or area_new is None else area_new / area_old - 1.0
    if change is None:
        warnings.append(_warning("section.replace_mass_unknown", member=member))
    elif abs(change) > MASS_CHANGE_WARNING:
        warnings.append(
            _warning("section.replace_mass_change", member=member, change=f"{change * 100:+.1f} %")
        )
    missing = assess_section(new).fem_missing
    if missing:
        warnings.append(
            _warning("section.replace_not_fem_ready", member=member, missing=", ".join(missing))
        )
    return tuple(warnings), change
