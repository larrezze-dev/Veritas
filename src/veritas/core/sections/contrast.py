"""Comparación con un perfil de catálogo y estado CONTRASTADO (EDT-011, TK-021; T-025 E2, D12-B).

``compare_with_snapshot`` devuelve, por propiedad (A, Iy, Iz, J), el valor de Veritas, el del
catálogo, la diferencia relativa CON SIGNO ``(Veritas − catálogo) / catálogo`` y un dictamen frente
a una tolerancia. No toca ningún estado y nunca rellena una propiedad ausente de un lado.

``contrasted_properties`` promueve de ``CALCULADO`` a ``CONTRASTADO`` solo las propiedades dentro
de tolerancia: el valor sigue siendo el calculado por Veritas (nunca se adopta el del catálogo) y
el ``origin`` cita catálogo, perfil, revisión, ``source_sha256``, diferencia y tolerancia.
``VERIFICADO`` no lo emite ningún código. ``CONTRASTADO`` es una afirmación registrada, no una
prueba criptográfica: solo ``source_sha256`` permite comprobarla contra el catálogo.

Módulo de ``core``: no importa catálogo, persistencia ni capas superiores.
"""

import math
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Final

from veritas.core.diagnostics import Diagnostic, DiagnosticCode, InputValueError
from veritas.core.sections.catalog import CatalogSnapshot
from veritas.core.sections.parametric import ParametricParameters
from veritas.core.sections.polygon import FreeformParameters
from veritas.core.sections.properties import PropertyStatus, SectionProperties, SectionProperty
from veritas.core.sections.section import SectionFamily, SectionRevision

COMPARED_PROPERTIES: Final = ("area", "iy", "iz", "torsion_constant")
"""Propiedades que el catálogo aporta y que se comparan (el centroide y Iyz no se comparan)."""

DEFAULT_TOLERANCE: Final = 0.01
"""Tolerancia relativa por defecto: parámetro de software, NO un valor normativo (editable)."""

_FILLET_NAMES: Final = ("r", "ro")


class ContrastVerdict(StrEnum):
    """Dictamen de una propiedad frente a la tolerancia."""

    WITHIN = "DENTRO"
    OUTSIDE = "FUERA"
    NOT_COMPARABLE = "NO_COMPARABLE"


@dataclass(frozen=True, slots=True)
class PropertyComparison:
    """Una propiedad: valores SI de ambos lados, diferencia relativa con signo y dictamen."""

    name: str
    computed: float | None
    catalog: float | None
    relative_difference: float | None
    verdict: ContrastVerdict
    reason: str = ""


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    """Informe de comparación de una sección con un perfil de catálogo."""

    snapshot_label: str
    source_sha256: str
    tolerance: float
    items: tuple[PropertyComparison, ...]
    notes: tuple[str, ...] = ()

    def item(self, name: str) -> PropertyComparison:
        """La comparación de la propiedad ``name``."""
        for entry in self.items:
            if entry.name == name:
                return entry
        raise KeyError(name)

    @property
    def within(self) -> tuple[str, ...]:
        """Propiedades dentro de tolerancia (las promovibles a CONTRASTADO)."""
        return tuple(i.name for i in self.items if i.verdict is ContrastVerdict.WITHIN)

    @property
    def all_within(self) -> bool:
        """``True`` si todas las propiedades comparables están dentro y hay al menos una."""
        verdicts = [
            i.verdict for i in self.items if i.verdict is not ContrastVerdict.NOT_COMPARABLE
        ]
        return bool(verdicts) and all(v is ContrastVerdict.WITHIN for v in verdicts)


def _check_tolerance(tolerance: float) -> float:
    if (
        isinstance(tolerance, bool)
        or not isinstance(tolerance, int | float)
        or not math.isfinite(tolerance)
        or not 0.0 < tolerance < 1.0
    ):
        raise InputValueError(
            Diagnostic.create(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "editor.compare_tolerance",
                {"value": str(tolerance)},
            )
        )
    return float(tolerance)


def _one(
    name: str, computed: SectionProperty, reference: SectionProperty, tolerance: float
) -> PropertyComparison:
    if computed.value is None:
        return PropertyComparison(
            name, None, reference.value, None, ContrastVerdict.NOT_COMPARABLE, "veritas"
        )
    if reference.value is None:
        return PropertyComparison(
            name, computed.value, None, None, ContrastVerdict.NOT_COMPARABLE, "catalogo"
        )
    difference = (computed.value - reference.value) / reference.value
    verdict = ContrastVerdict.WITHIN if abs(difference) <= tolerance else ContrastVerdict.OUTSIDE
    return PropertyComparison(name, computed.value, reference.value, difference, verdict)


def _notes(section: SectionRevision, snapshot: CatalogSnapshot) -> tuple[str, ...]:
    notes: list[str] = []
    parameters = section.parameters
    if isinstance(parameters, ParametricParameters):
        if str(parameters.shape) != snapshot.shape:
            notes.append("editor.compare_shape_differs")
        has_fillet = any(parameters.as_dict().get(n, 0.0) > 0.0 for n in _FILLET_NAMES)
        listed = {d.name for d in snapshot.dimensions}
        if has_fillet != any(n in listed for n in _FILLET_NAMES):
            notes.append("editor.compare_fillet_differs")
    elif isinstance(parameters, FreeformParameters):
        pass  # Un croquis libre no declara forma: el aviso de forma no aplica (T-029 E2).
    elif snapshot.shape != "RECT":
        notes.append("editor.compare_shape_differs")
    return tuple(notes)


def compare_with_snapshot(
    section: SectionRevision,
    snapshot: CatalogSnapshot,
    tolerance: float = DEFAULT_TOLERANCE,
) -> ComparisonReport:
    """Informe de A, Iy, Iz y J de ``section`` frente a ``snapshot``; no cambia ningún estado."""
    if section.family is SectionFamily.CATALOGO:
        raise InputValueError(
            Diagnostic.create(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "editor.compare_catalog_section",
                {"family": str(section.family)},
            )
        )
    limit = _check_tolerance(tolerance)
    items = tuple(
        _one(name, getattr(section.properties, name), getattr(snapshot, name), limit)
        for name in COMPARED_PROPERTIES
    )
    return ComparisonReport(
        snapshot.label, snapshot.source_sha256, limit, items, _notes(section, snapshot)
    )


def _origin(report: ComparisonReport, item: PropertyComparison) -> str:
    return (
        f"contrastado con {report.snapshot_label} (source_sha256 {report.source_sha256}); "
        f"diferencia relativa {item.relative_difference:+.6%}; tolerancia {report.tolerance:.6%}"
    )


def contrasted_properties(
    properties: SectionProperties, report: ComparisonReport
) -> SectionProperties:
    """Propiedades con ``CONTRASTADO`` en las que están dentro de tolerancia y son ``CALCULADO``.

    El valor, la unidad y el método no cambian. Una propiedad fuera de tolerancia, no comparable o
    que no sea ``CALCULADO`` se deja exactamente como estaba.
    """
    changes: dict[str, SectionProperty] = {}
    for name in report.within:
        old: SectionProperty = getattr(properties, name)
        if (
            old.status is not PropertyStatus.CALCULADO
            and old.status is not PropertyStatus.CONTRASTADO
        ):
            continue
        changes[name] = replace(
            old, origin=_origin(report, report.item(name)), status=PropertyStatus.CONTRASTADO
        )
    return replace(properties, **changes)
