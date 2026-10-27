"""Propiedades de sección con valor, unidad, origen, método y estado (SRS §5.2; T-004, D3).

Una propiedad no disponible tiene ``value=None``, nunca 0. Las propiedades calculadas
forman parte del contenido de la revisión de sección: se guardan, no se recalculan al
cargar; por eso ``method`` incluye la versión del algoritmo.
"""

import math
from dataclasses import dataclass
from enum import StrEnum

from veritas.core.units import Quantity, get_unit
from veritas.core.validation import invalid_type, require_text


class PropertyStatus(StrEnum):
    """Estado de una propiedad. Los estados de importación llegan con el catálogo (F2)."""

    CALCULADO = "CALCULADO"
    NO_DISPONIBLE = "NO_DISPONIBLE"


@dataclass(frozen=True, slots=True)
class SectionProperty:
    """Valor SI de una propiedad de sección con su trazabilidad."""

    value: float | None
    unit: str
    origin: str
    method: str
    status: PropertyStatus

    def __post_init__(self) -> None:
        get_unit(self.unit)  # Unidad SI registrada; lanza UNITS_UNKNOWN si no existe.
        if get_unit(self.unit).factor != 1.0:
            raise ValueError(f"SectionProperty.unit debe ser una unidad SI: {self.unit}")
        require_text(self.origin, "origin")
        require_text(self.method, "method")
        if not isinstance(self.status, PropertyStatus):
            raise invalid_type("status", "PropertyStatus", self.status)
        if self.status is PropertyStatus.NO_DISPONIBLE:
            if self.value is not None:
                raise ValueError("Una propiedad NO_DISPONIBLE no tiene valor")
            return
        if isinstance(self.value, bool) or not isinstance(self.value, float):
            raise invalid_type("value", "float", self.value)
        if not math.isfinite(self.value):
            raise ValueError("SectionProperty.value debe ser finito")

    @property
    def quantity(self) -> Quantity | None:
        """Magnitud con dimensión, o ``None`` si no está disponible."""
        if self.value is None:
            return None
        return Quantity(self.value, get_unit(self.unit).dimension)


@dataclass(frozen=True, slots=True)
class SectionProperties:
    """Propiedades en el sistema de definición de la sección (ejes y/z locales, ADR-001 §1).

    ``centroid_y``/``centroid_z`` son la posición del centroide en ese sistema; ``iy``,
    ``iz`` e ``iyz`` (= ∫yz dA, con este signo) son centroidales. ``torsion_constant`` es
    la constante de Saint-Venant J; nunca se deduce de Iy + Iz.
    """

    area: SectionProperty
    centroid_y: SectionProperty
    centroid_z: SectionProperty
    iy: SectionProperty
    iz: SectionProperty
    iyz: SectionProperty
    torsion_constant: SectionProperty

    def __post_init__(self) -> None:
        for name in self.__slots__:
            if not isinstance(getattr(self, name), SectionProperty):
                raise invalid_type(name, "SectionProperty", getattr(self, name))
