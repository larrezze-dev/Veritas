"""Borrador de perfil paramétrico (EDT-004, EDT-012; plan T-025 E1, D11-A).

Un ``SectionDraft`` es un objeto puro: forma y dimensiones con unidad, sin identidad, sin registro
en el modelo y sin persistencia. Acumula TODOS los diagnósticos de EDT-004 (nombres, unidades,
valores y reglas geométricas de ``core``) y solo un borrador válido se convierte en una
``SectionRevision`` asignable: ``build`` rechaza con el primer diagnóstico si no lo es.

Este módulo importa solo ``veritas.core``: nunca catálogo, persistencia, solver ni GUI. La
comparación con un perfil de catálogo (E2) recibirá un ``CatalogSnapshot``, que es de ``core``.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    DiagnosticError,
    InputValueError,
    Location,
)
from veritas.core.sections.parametric import (
    OPTIONAL_DIMENSIONS,
    SHAPE_DIMENSIONS,
    ParametricShape,
    geometry_diagnostics,
)
from veritas.core.sections.section import SectionRevision, build_parametric_section
from veritas.core.units import LENGTH, Quantity
from veritas.core.validation import invalid_type


@dataclass(frozen=True, slots=True)
class SectionDraft:
    """Forma y dimensiones (longitudes con unidad) de un perfil paramétrico en edición."""

    shape: ParametricShape
    dimensions: tuple[tuple[str, Quantity], ...]

    def __post_init__(self) -> None:
        if not isinstance(self.shape, ParametricShape):
            raise invalid_type("shape", "ParametricShape", self.shape)
        entries = tuple(self.dimensions)
        for entry in entries:
            if not (
                isinstance(entry, tuple)
                and len(entry) == 2  # noqa: PLR2004 (par nombre-valor)
                and isinstance(entry[0], str)
            ):
                raise invalid_type("dimensions", "pares (nombre, Quantity)", entry)
        object.__setattr__(self, "dimensions", entries)

    @classmethod
    def of(cls, shape: ParametricShape, dimensions: Mapping[str, Quantity]) -> "SectionDraft":
        """Borrador desde un diccionario de dimensiones (nombre → longitud con unidad)."""
        return cls(shape, tuple(dimensions.items()))

    @classmethod
    def from_section(cls, section: SectionRevision) -> "SectionDraft":
        """Borrador con las medidas de una revisión paramétrica existente (para editarla)."""
        parameters = section.require_parametric()
        return cls.of(
            parameters.shape, {n: Quantity(v, LENGTH) for n, v in parameters.as_dict().items()}
        )

    def diagnostics(self) -> tuple[Diagnostic, ...]:
        """Todas las violaciones de EDT-004; vacía si el borrador es válido."""
        expected = SHAPE_DIMENSIONS[self.shape]
        found: list[Diagnostic] = []
        names = [n for n, _ in self.dimensions]
        for name in sorted({n for n in names if n not in expected}):
            found.append(_names_diagnostic(self.shape, expected, name, "desconocida"))
        for name in sorted({n for n in names if names.count(n) > 1}):
            found.append(_names_diagnostic(self.shape, expected, name, "repetida"))
        for name in expected:
            if name not in names and name not in OPTIONAL_DIMENSIONS:
                found.append(_names_diagnostic(self.shape, expected, name, "falta"))
        values: dict[str, float] = {}
        for name, quantity in self.dimensions:
            if name not in expected:
                continue
            try:
                if not isinstance(quantity, Quantity):
                    raise invalid_type(name, "Quantity", quantity, Location(field=name))
                values.setdefault(name, quantity.require(LENGTH).si_value)
            except DiagnosticError as error:
                found.append(error.diagnostic)
        if found:
            return tuple(found)
        complete = {n: values.get(n, 0.0) for n in expected}
        return geometry_diagnostics(self.shape, complete)

    @property
    def valid(self) -> bool:
        """``True`` si el borrador no tiene diagnósticos de EDT-004."""
        return not self.diagnostics()

    def build(self, section_id: UUID, revision: int = 1, *, name: str = "") -> SectionRevision:
        """Revisión de sección asignable; rechaza con el primer diagnóstico si no es válido."""
        problems = self.diagnostics()
        if problems:
            raise InputValueError(problems[0])
        return build_parametric_section(
            section_id,
            revision,
            shape=self.shape,
            dimensions=dict(self.dimensions),
            name=name,
        )


def _names_diagnostic(
    shape: ParametricShape, expected: tuple[str, ...], name: str, problem: str
) -> Diagnostic:
    return Diagnostic.create(
        DiagnosticCode.INPUT_OUT_OF_RANGE,
        "editor.dimension_name",
        {"shape": str(shape), "name": name, "problem": problem, "expected": ", ".join(expected)},
        location=Location(field=name),
    )
