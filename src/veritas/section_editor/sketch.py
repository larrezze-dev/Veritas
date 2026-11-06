"""Borrador de croquis libre (EDT-002, EDT-004, EDT-012; plan T-029 E2, R1).

Un ``SketchDraft`` es un objeto puro: contornos como polilíneas de vértices (y, z) con unidad y la
bandera ``closed``; sin identidad, sin registro en el modelo y sin persistencia. Acumula TODOS los
diagnósticos (contorno abierto, unidades, valores no finitos y las reglas geométricas de ``core``)
y solo un borrador válido se convierte en una ``SectionRevision`` asignable: ``build`` rechaza con
el primer diagnóstico si no lo es.

El cierre del contorno es implícito (no se repite el primer vértice); ``closed=False`` modela una
polilínea sin cerrar y se diagnostica como ``section.sketch_open_contour``.

Este módulo importa solo ``veritas.core``.
"""

import math
from dataclasses import dataclass
from uuid import UUID

from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    DiagnosticError,
    InputValueError,
    Location,
)
from veritas.core.sections.polygon import Contour, shape_diagnostic, sketch_diagnostics
from veritas.core.sections.section import SectionRevision, build_freeform_section
from veritas.core.units import LENGTH, Quantity
from veritas.core.validation import invalid_type, out_of_range

type QuantityPoint = tuple[Quantity, Quantity]


@dataclass(frozen=True, slots=True)
class SketchContour:
    """Polilínea de vértices (y, z) con unidad; ``closed`` indica si el contorno está cerrado."""

    points: tuple[QuantityPoint, ...]
    closed: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "points", tuple(tuple(p) for p in self.points))


@dataclass(frozen=True, slots=True)
class SketchDraft:
    """Contorno exterior y huecos de un croquis en edición."""

    outer: SketchContour
    holes: tuple[SketchContour, ...] = ()
    shape: str | None = None
    """Forma declarada (una de ``PROFILE_SHAPES``); ``None`` = sin declarar (T-031)."""

    def __post_init__(self) -> None:
        if not isinstance(self.outer, SketchContour):
            raise invalid_type("outer", "SketchContour", self.outer)
        holes = tuple(self.holes)
        if not all(isinstance(h, SketchContour) for h in holes):
            raise invalid_type("holes", "SketchContour", holes)
        object.__setattr__(self, "holes", holes)

    @classmethod
    def from_section(cls, section: SectionRevision) -> "SketchDraft":
        """Borrador con el croquis de una revisión ``FREEFORM`` existente (para editarla)."""
        parameters = section.require_freeform()

        def contour(points: Contour) -> SketchContour:
            return SketchContour(
                tuple((Quantity(y, LENGTH), Quantity(z, LENGTH)) for y, z in points)
            )

        return cls(
            contour(parameters.outer),
            tuple(contour(h) for h in parameters.holes),
            parameters.shape,
        )

    def diagnostics(self) -> tuple[Diagnostic, ...]:
        """Todas las violaciones de EDT-004; vacía si el borrador es válido."""
        found: list[Diagnostic] = []
        outer = _to_si(self.outer, "outer", found)
        holes = [_to_si(h, f"hole[{i}]", found) for i, h in enumerate(self.holes)]
        problem = shape_diagnostic(self.shape)
        if problem is not None:
            found.append(problem)
        if found or outer is None or any(h is None for h in holes):
            return tuple(found)
        if not outer:
            return (_empty_outer(),)
        return sketch_diagnostics(outer, [h for h in holes if h is not None])

    @property
    def valid(self) -> bool:
        """``True`` si el borrador no tiene diagnósticos de EDT-004."""
        return not self.diagnostics()

    def build(self, section_id: UUID, revision: int = 1, *, name: str = "") -> SectionRevision:
        """Revisión de sección asignable; rechaza con el primer diagnóstico si no es válido."""
        problems = self.diagnostics()
        if problems:
            raise InputValueError(problems[0])
        return build_freeform_section(
            section_id,
            revision,
            outer=self.outer.points,
            holes=[h.points for h in self.holes],
            name=name,
            shape=self.shape,
        )


def _empty_outer() -> Diagnostic:
    return Diagnostic.create(
        DiagnosticCode.INPUT_OUT_OF_RANGE,
        "section.sketch_too_few_vertices",
        {"field": "outer", "count": 0.0},
        location=Location(field="outer"),
    )


def _to_si(contour: SketchContour, field: str, found: list[Diagnostic]) -> Contour | None:
    """Vértices en m, o ``None`` si hay diagnósticos (se añaden a ``found``)."""
    before = len(found)
    if not contour.closed:
        found.append(
            Diagnostic.create(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "section.sketch_open_contour",
                {"field": field},
                location=Location(field=field),
            )
        )
    points: list[tuple[float, float]] = []
    for index, pair in enumerate(contour.points):
        at = f"{field}[{index}]"
        try:
            if not (isinstance(pair, tuple) and len(pair) == 2):  # noqa: PLR2004 (par y, z)
                raise invalid_type(at, "par (y, z) de Quantity", pair, Location(field=at))
            values: list[float] = []
            for item in pair:
                if not isinstance(item, Quantity):
                    raise invalid_type(at, "Quantity", item, Location(field=at))
                number = item.require(LENGTH).si_value
                if not math.isfinite(number):
                    raise out_of_range(at, number, "finito", Location(field=at))
                values.append(number + 0.0)
            points.append((values[0], values[1]))
        except DiagnosticError as error:
            found.append(error.diagnostic)
    return None if len(found) > before else tuple(points)
