"""Masa lineal derivada de material y sección (SEC-002; plan T-004, D6).

m = ρ·A en kg/m. No se guarda en la sección: depende del material asignado a la barra.
"""

from veritas.core.diagnostics import Diagnostic, DiagnosticCode, InputValueError, Location
from veritas.core.materials.material import MaterialRevision
from veritas.core.sections.section import SectionRevision
from veritas.core.units import AREA, LINEAR_MASS, Quantity
from veritas.core.validation import checked_positive, invalid_type


def linear_mass(
    material: MaterialRevision, section: SectionRevision, location: Location | None = None
) -> Quantity:
    """Masa por unidad de longitud ρ·A (kg/m)."""
    if not isinstance(material, MaterialRevision):
        raise invalid_type("material", "MaterialRevision", material)
    if not isinstance(section, SectionRevision):
        raise invalid_type("section", "SectionRevision", section)
    area = _area(section, location)
    return Quantity(checked_positive(material.density * area, "masa lineal", location), LINEAR_MASS)


def section_area(section: SectionRevision, location: Location | None = None) -> Quantity:
    """Área A de la sección (m²); ``section.property_unavailable`` si no está disponible."""
    if not isinstance(section, SectionRevision):
        raise invalid_type("section", "SectionRevision", section)
    return Quantity(_area(section, location), AREA)


def _area(section: SectionRevision, location: Location | None) -> float:
    area = section.properties.area.value
    if area is None:
        raise InputValueError(
            Diagnostic.create(
                DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                "section.property_unavailable",
                {"property": "A"},
                location=location,
            )
        )
    return area
