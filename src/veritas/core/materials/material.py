"""Revisiones inmutables de material (MAT-001, MAT-002; plan T-004, D5).

E y G son valores primarios independientes (rigidez para el análisis): no se comprueba
G = E/2(1+ν), porque los valores de la propia norma no lo cumplen exactamente
(DB-SE-A §4.2: 210 000 / 81 000 / 0,3 difiere un 0,29 %), y la madera es ortótropa.
ν es informativo y opcional. Unidades SI: Pa para E y G, kg/m³ para ρ y N/m³ para el
peso específico γ, opcional (plan T-019, D1-A): no se compara con ρ·g, porque γ puede
incluir recargos legítimos (D9-A).

Las resistencias se difieren a F7A/F7B (MAT-001 parcial). La familia ALUMINIUM se
analiza por FEM, pero su verificación normativa es NO_COMPROBADA (sin DB-SE de aluminio).
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from veritas.core.diagnostics import Location
from veritas.core.references import RevisionRef, check_identity
from veritas.core.units import DENSITY, DIMENSIONLESS, SPECIFIC_WEIGHT, STRESS, Quantity
from veritas.core.validation import (
    invalid_type,
    out_of_range,
    positive_float,
    positive_si,
    require_real,
    require_text,
)

POISSON_RANGE = "-1 < ν < 0.5"


class MaterialFamily(StrEnum):
    """Familia de material. No existe familia compuesta: una barra, un material."""

    STEEL = "STEEL"
    ALUMINIUM = "ALUMINIUM"
    TIMBER = "TIMBER"


def _check_poisson(value: object, location: Location) -> float | None:
    if value is None:
        return None
    number = require_real(value, "poisson_ratio", location)
    if not -1.0 < number < 0.5:  # noqa: PLR2004 (límites físicos del material isótropo)
        raise out_of_range("poisson_ratio", number, POISSON_RANGE, location)
    return number


@dataclass(frozen=True, slots=True)
class MaterialRevision:
    """Revisión de material en SI. Un mismo par (id, revisión) es contenido inmutable."""

    id: UUID
    revision: int
    name: str
    family: MaterialFamily
    source: str
    standard_edition: str | None
    elastic_modulus: float
    shear_modulus: float
    density: float
    poisson_ratio: float | None = None
    specific_weight: float | None = None

    def __post_init__(self) -> None:
        check_identity(self.id, self.revision, "MaterialRevision")
        location = Location(entity_id=self.id, revision=self.revision)

        def at(field: str) -> Location:
            return Location(entity_id=self.id, revision=self.revision, field=field)

        require_text(self.name, "name", at("name"))
        require_text(self.source, "source", at("source"))
        if self.standard_edition is not None:
            require_text(self.standard_edition, "standard_edition", at("standard_edition"))
        if not isinstance(self.family, MaterialFamily):
            raise invalid_type("family", "MaterialFamily", self.family, location)
        for name in ("elastic_modulus", "shear_modulus", "density"):
            object.__setattr__(self, name, positive_float(getattr(self, name), name, at(name)))
        object.__setattr__(
            self, "poisson_ratio", _check_poisson(self.poisson_ratio, at("poisson_ratio"))
        )
        if self.specific_weight is not None:
            object.__setattr__(
                self,
                "specific_weight",
                positive_float(self.specific_weight, "specific_weight", at("specific_weight")),
            )

    @property
    def ref(self) -> RevisionRef:
        """Referencia exacta (id, revisión) a esta revisión."""
        return RevisionRef(self.id, self.revision)


def build_material(  # noqa: PLR0913 (campos de MAT-002, por nombre)
    entity_id: UUID,
    revision: int,
    *,
    name: str,
    family: MaterialFamily,
    source: str,
    elastic_modulus: Quantity,
    shear_modulus: Quantity,
    density: Quantity,
    poisson_ratio: Quantity | float | None = None,
    standard_edition: str | None = None,
    specific_weight: Quantity | None = None,
) -> MaterialRevision:
    """Crea una revisión a partir de magnitudes con unidad (frontera con ``Quantity``)."""
    check_identity(entity_id, revision, "MaterialRevision")

    def at(field: str) -> Location:
        return Location(entity_id=entity_id, revision=revision, field=field)

    nu: object = poisson_ratio
    if isinstance(poisson_ratio, Quantity):
        nu = poisson_ratio.require(DIMENSIONLESS).si_value
    return MaterialRevision(
        id=entity_id,
        revision=revision,
        name=name,
        family=family,
        source=source,
        standard_edition=standard_edition,
        elastic_modulus=positive_si(
            elastic_modulus, STRESS, "elastic_modulus", at("elastic_modulus")
        ),
        shear_modulus=positive_si(shear_modulus, STRESS, "shear_modulus", at("shear_modulus")),
        density=positive_si(density, DENSITY, "density", at("density")),
        poisson_ratio=_check_poisson(nu, at("poisson_ratio")),
        specific_weight=(
            None
            if specific_weight is None
            else positive_si(
                specific_weight, SPECIFIC_WEIGHT, "specific_weight", at("specific_weight")
            )
        ),
    )
