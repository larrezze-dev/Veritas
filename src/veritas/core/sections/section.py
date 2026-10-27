"""Revisiones inmutables de sección (SEC-001, SEC-003; plan T-004, D3).

La geometría paramétrica (``RectangularParameters``) y las propiedades calculadas
(``SectionProperties``) son objetos separados dentro de la revisión. La sección no
conoce el material: la masa lineal se calcula al combinarlos (``sections.mass``).
"""

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from veritas.core.diagnostics import Diagnostic, DiagnosticCode, InputValueError, Location
from veritas.core.references import RevisionRef, check_identity
from veritas.core.sections.properties import PropertyStatus, SectionProperties
from veritas.core.sections.rectangular import (
    METHOD_CLOSED_FORM,
    METHOD_SAINT_VENANT,
    rectangular_properties,
)
from veritas.core.units import LENGTH, Quantity, get_unit
from veritas.core.validation import (
    checked_positive,
    invalid_type,
    out_of_range,
    positive_float,
    positive_si,
    require_text,
)

_PROPERTY_UNITS: Final = {
    "area": "m²",
    "centroid_y": "m",
    "centroid_z": "m",
    "iy": "m⁴",
    "iz": "m⁴",
    "iyz": "m⁴",
    "torsion_constant": "m⁴",
}
_STRICTLY_POSITIVE: Final = frozenset({"area", "iy", "iz", "torsion_constant"})
# El rectángulo se define con origen en su centroide y tiene doble simetría.
_EXACT_ZERO: Final = frozenset({"centroid_y", "centroid_z", "iyz"})
_RECTANGLE_METHODS: Final = frozenset({METHOD_CLOSED_FORM, METHOD_SAINT_VENANT})
MATCH_RELATIVE_TOLERANCE: Final = 1e-12
"""Tolerancia de coincidencia con el recálculo: independiente del redondeo de ``math.exp``."""


class SectionFamily(StrEnum):
    """Familia de sección. Solo el rectángulo macizo en T-004."""

    RECTANGULAR = "RECTANGULAR"


@dataclass(frozen=True, slots=True)
class RectangularParameters:
    """Rectángulo macizo: ``b`` según y local y ``h`` según z local, en m."""

    b: float
    h: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "b", positive_float(self.b, "b"))
        object.__setattr__(self, "h", positive_float(self.h, "h"))


@dataclass(frozen=True, slots=True)
class SectionRevision:
    """Revisión de sección. Un mismo par (id, revisión) es contenido inmutable."""

    id: UUID
    revision: int
    name: str
    family: SectionFamily
    parameters: RectangularParameters
    properties: SectionProperties

    def __post_init__(self) -> None:
        check_identity(self.id, self.revision, "SectionRevision")
        location = Location(entity_id=self.id, revision=self.revision)
        if not isinstance(self.name, str):
            raise invalid_type("name", "texto", self.name, location)
        if not isinstance(self.family, SectionFamily):
            raise invalid_type("family", "SectionFamily", self.family, location)
        if not isinstance(self.parameters, RectangularParameters):
            raise invalid_type("parameters", "RectangularParameters", self.parameters, location)
        if not isinstance(self.properties, SectionProperties):
            raise invalid_type("properties", "SectionProperties", self.properties, location)
        _check_properties(self.parameters, self.properties, self.id, self.revision)

    @property
    def ref(self) -> RevisionRef:
        """Referencia exacta (id, revisión) a esta revisión."""
        return RevisionRef(self.id, self.revision)


def _check_properties(
    parameters: RectangularParameters,
    properties: SectionProperties,
    entity_id: UUID,
    revision: int,
) -> None:
    """Invariantes de las propiedades, también para revisiones registradas desde fuera.

    Unidad SI esperada por campo; A, Iy, Iz y J positivas y normales; centroide e Iyz
    exactamente +0.0. Si TODAS las propiedades usan las versiones actuales de los métodos
    del rectángulo, deben coincidir con el recálculo a partir de b y h con tolerancia
    relativa 1e-12: no se introducen a mano (plan T-004, D3 y R1-ii). Un registro con un
    algoritmo de otra versión se acepta tal como se guardó: no se recalcula al cargar (O3).
    """
    for name, unit in _PROPERTY_UNITS.items():
        prop = getattr(properties, name)
        at = Location(entity_id=entity_id, revision=revision, field=name)
        Quantity(1.0, get_unit(prop.unit).dimension).require(get_unit(unit).dimension)
        value = prop.value
        if prop.status is not PropertyStatus.CALCULADO or value is None:
            continue  # NO_DISPONIBLE (value None) ya lo valida SectionProperty.
        if name in _STRICTLY_POSITIVE:
            checked_positive(value, name, at)
        elif name in _EXACT_ZERO and (value != 0.0 or math.copysign(1.0, value) < 0.0):
            raise out_of_range(name, value, "+0.0", at)
    methods = {getattr(properties, name).method for name in _PROPERTY_UNITS}
    if methods <= _RECTANGLE_METHODS and not _matches(
        properties, rectangular_properties(parameters.b, parameters.h)
    ):
        raise InputValueError(
            Diagnostic.create(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "section.properties_mismatch",
                location=Location(entity_id=entity_id, revision=revision, field="properties"),
            )
        )


def _matches(stored: SectionProperties, computed: SectionProperties) -> bool:
    for name in _PROPERTY_UNITS:
        old, new = getattr(stored, name), getattr(computed, name)
        if (old.unit, old.origin, old.method, old.status) != (
            new.unit,
            new.origin,
            new.method,
            new.status,
        ):
            return False
        if old.value is None or new.value is None:
            if old.value is not new.value:
                return False
        elif not math.isclose(old.value, new.value, rel_tol=MATCH_RELATIVE_TOLERANCE):
            return False
    return True


def build_rectangular_section(
    entity_id: UUID, revision: int, *, b: Quantity, h: Quantity, name: str = ""
) -> SectionRevision:
    """Crea una revisión de sección rectangular y calcula sus propiedades."""
    check_identity(entity_id, revision, "SectionRevision")

    def at(field: str) -> Location:
        return Location(entity_id=entity_id, revision=revision, field=field)

    if name:
        require_text(name, "name", at("name"))
    parameters = RectangularParameters(
        positive_si(b, LENGTH, "b", at("b")), positive_si(h, LENGTH, "h", at("h"))
    )
    return SectionRevision(
        id=entity_id,
        revision=revision,
        name=name,
        family=SectionFamily.RECTANGULAR,
        parameters=parameters,
        properties=rectangular_properties(
            parameters.b, parameters.h, Location(entity_id=entity_id, revision=revision)
        ),
    )
