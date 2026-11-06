"""Revisiones inmutables de sección (SEC-001, SEC-003; plan T-004, D3).

La geometría paramétrica (``RectangularParameters``) y las propiedades calculadas
(``SectionProperties``) son objetos separados dentro de la revisión. La sección no
conoce el material: la masa lineal se calcula al combinarlos (``sections.mass``).
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from veritas.core.diagnostics import Diagnostic, DiagnosticCode, InputValueError, Location
from veritas.core.references import RevisionRef, check_identity
from veritas.core.sections.catalog import (
    BISYMMETRIC_SHAPES,
    SYMMETRY_METHOD,
    SYMMETRY_ORIGIN,
    CatalogSnapshot,
)
from veritas.core.sections.parametric import ParametricParameters, ParametricShape
from veritas.core.sections.parametric_properties import parametric_properties
from veritas.core.sections.polygon import FreeformParameters, freeform_properties
from veritas.core.sections.properties import PropertyStatus, SectionProperties, SectionProperty
from veritas.core.sections.rectangular import (
    METHOD_CLOSED_FORM,
    METHOD_SAINT_VENANT,
    rectangular_properties,
)
from veritas.core.sections.shear import ShearAreas, parametric_shear, rectangular_shear
from veritas.core.units import AREA, LENGTH, Quantity, get_unit
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
_CATALOG_COPIED: Final = ("area", "iy", "iz", "torsion_constant")
_CATALOG_ABSENT: Final = ("centroid_y", "centroid_z", "iyz")
MATCH_RELATIVE_TOLERANCE: Final = 1e-12
"""Tolerancia de coincidencia con el recálculo: independiente del redondeo de ``math.exp``."""


class SectionFamily(StrEnum):
    """Familia de sección: el rectángulo macizo (T-004), el perfil de catálogo (T-024, ADR-010)
    los perfiles paramétricos I, RHS, SHS y CHS (T-025, ADR-011) y el croquis libre poligonal
    (T-029, ADR-013)."""

    RECTANGULAR = "RECTANGULAR"
    CATALOGO = "CATALOGO"
    PARAMETRICA = "PARAMETRICA"
    FREEFORM = "FREEFORM"


@dataclass(frozen=True, slots=True)
class RectangularParameters:
    """Rectángulo macizo: ``b`` según y local y ``h`` según z local, en m."""

    b: float
    h: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "b", positive_float(self.b, "b"))
        object.__setattr__(self, "h", positive_float(self.h, "h"))


type SectionParameters = (
    RectangularParameters | ParametricParameters | CatalogSnapshot | FreeformParameters
)


@dataclass(frozen=True, slots=True)
class SectionRevision:
    """Revisión de sección. Un mismo par (id, revisión) es contenido inmutable."""

    id: UUID
    revision: int
    name: str
    family: SectionFamily
    parameters: SectionParameters
    properties: SectionProperties
    shear: ShearAreas | None = None
    """Áreas de cortante energéticas (T-025, D9-B): ``None`` = nunca calculadas (no es 0)."""

    def __post_init__(self) -> None:
        check_identity(self.id, self.revision, "SectionRevision")
        location = Location(entity_id=self.id, revision=self.revision)
        if not isinstance(self.name, str):
            raise invalid_type("name", "texto", self.name, location)
        if not isinstance(self.family, SectionFamily):
            raise invalid_type("family", "SectionFamily", self.family, location)
        if not isinstance(
            self.parameters,
            RectangularParameters | ParametricParameters | CatalogSnapshot | FreeformParameters,
        ):
            raise invalid_type(
                "parameters",
                "RectangularParameters, ParametricParameters, CatalogSnapshot o FreeformParameters",
                self.parameters,
                location,
            )
        if not isinstance(self.properties, SectionProperties):
            raise invalid_type("properties", "SectionProperties", self.properties, location)
        if self.shear is not None and not isinstance(self.shear, ShearAreas):
            raise invalid_type("shear", "ShearAreas o None", self.shear, location)
        expected = _PARAMETERS_OF[self.family]
        if not isinstance(self.parameters, expected):
            raise InputValueError(
                Diagnostic.create(
                    DiagnosticCode.INPUT_INVALID_TYPE,
                    "section.family_mismatch",
                    {"family": str(self.family), "found": type(self.parameters).__name__},
                    location=Location(
                        entity_id=self.id, revision=self.revision, field="parameters"
                    ),
                )
            )
        _check_properties(self.parameters, self.properties, self.id, self.revision)
        _check_shear(self.parameters, self.shear, self.id, self.revision)

    @property
    def ref(self) -> RevisionRef:
        """Referencia exacta (id, revisión) a esta revisión."""
        return RevisionRef(self.id, self.revision)

    def require_rectangular(self) -> RectangularParameters:
        """Parámetros b y h, o un error explícito si la sección no es un rectángulo."""
        if not isinstance(self.parameters, RectangularParameters):
            raise InputValueError(
                Diagnostic.create(
                    DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                    "section.not_rectangular",
                    {"family": str(self.family)},
                    location=Location(entity_id=self.id, revision=self.revision),
                )
            )
        return self.parameters

    def require_parametric(self) -> ParametricParameters:
        """Parámetros de un perfil paramétrico, o un error explícito si es de otra familia."""
        if not isinstance(self.parameters, ParametricParameters):
            raise InputValueError(
                Diagnostic.create(
                    DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                    "section.not_parametric",
                    {"family": str(self.family)},
                    location=Location(entity_id=self.id, revision=self.revision),
                )
            )
        return self.parameters

    def require_freeform(self) -> FreeformParameters:
        """Parámetros de un croquis libre, o un error explícito si es de otra familia."""
        if not isinstance(self.parameters, FreeformParameters):
            raise InputValueError(
                Diagnostic.create(
                    DiagnosticCode.UNSUPPORTED_CONFIGURATION,
                    "section.not_freeform",
                    {"family": str(self.family)},
                    location=Location(entity_id=self.id, revision=self.revision),
                )
            )
        return self.parameters


_PARAMETERS_OF: Final = {
    SectionFamily.RECTANGULAR: RectangularParameters,
    SectionFamily.CATALOGO: CatalogSnapshot,
    SectionFamily.PARAMETRICA: ParametricParameters,
    SectionFamily.FREEFORM: FreeformParameters,
}


def _check_properties(
    parameters: SectionParameters,
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
        if prop.status is PropertyStatus.NO_DISPONIBLE or value is None:
            continue  # NO_DISPONIBLE (value None) ya lo valida SectionProperty.
        if name in _STRICTLY_POSITIVE:
            checked_positive(value, name, at)
        elif (
            name in _EXACT_ZERO
            and not isinstance(parameters, FreeformParameters)  # el croquis no está centrado
            and (value != 0.0 or math.copysign(1.0, value) < 0.0)
        ):
            raise out_of_range(name, value, "+0.0", at)
    if isinstance(parameters, CatalogSnapshot):
        _check_catalog_properties(parameters, properties, entity_id, revision)
        return
    methods = {getattr(properties, name).method for name in _PROPERTY_UNITS}
    if isinstance(parameters, ParametricParameters):
        expected = parametric_properties(parameters)
        recomputed = methods <= {getattr(expected, name).method for name in _PROPERTY_UNITS}
        match = recomputed and _matches(properties, expected)
    elif isinstance(parameters, FreeformParameters):
        _check_freeform_properties(parameters, properties, entity_id, revision)
        return
    else:
        recomputed = methods <= _RECTANGLE_METHODS
        match = recomputed and _matches(
            properties, rectangular_properties(parameters.b, parameters.h)
        )
    if recomputed and not match:
        raise InputValueError(
            Diagnostic.create(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "section.properties_mismatch",
                location=Location(entity_id=entity_id, revision=revision, field="properties"),
            )
        )


def _check_freeform_properties(
    parameters: FreeformParameters,
    properties: SectionProperties,
    entity_id: UUID,
    revision: int,
) -> None:
    """Invariantes del croquis libre que NO dependen del método declarado (T-029, ADR-013).

    - J es siempre ``NO_DISPONIBLE`` (D6-A): no hay método validado, así que ningún valor de J
      puede entrar a mano ni por archivo.
    - Los estados son ``CALCULADO`` o ``CONTRASTADO``; el tensor de inercia es admisible
      (Iyz² ≤ Iy·Iz).
    - Cada propiedad cuyo método sea el actual debe coincidir con el recálculo, una a una: un
      método de otra versión solo exime a esa propiedad (O3), nunca a las demás.
    """

    def error(key: str, name: str) -> InputValueError:
        return InputValueError(
            Diagnostic.create(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                key,
                {"field": name},
                location=Location(entity_id=entity_id, revision=revision, field=name),
            )
        )

    if properties.torsion_constant.status is not PropertyStatus.NO_DISPONIBLE:
        raise error("section.freeform_torsion_not_available", "torsion_constant")
    for name in _PROPERTY_UNITS:
        if getattr(properties, name).status not in _FREEFORM_STATUSES | {
            PropertyStatus.NO_DISPONIBLE
        }:
            raise error("section.freeform_invalid_status", name)
    iy, iz, iyz = properties.iy.value, properties.iz.value, properties.iyz.value
    if iy is not None and iz is not None and iyz is not None and iyz * iyz > iy * iz:
        raise error("section.properties_mismatch", "iyz")
    expected = freeform_properties(parameters)
    for name in _PROPERTY_UNITS:
        stored, computed = getattr(properties, name), getattr(expected, name)
        if stored.method == computed.method and not _same_property(stored, computed):
            raise error("section.properties_mismatch", "properties")


def _check_catalog_properties(
    snapshot: CatalogSnapshot,
    properties: SectionProperties,
    entity_id: UUID,
    revision: int,
) -> None:
    """Perfil de catálogo (ADR-010): las propiedades son las del snapshot y nada más.

    A, Iy, Iz y J son idénticas a las copiadas del catálogo (valor, unidad, origen, método y
    estado). El centroide e Iyz, que el catálogo no aporta, son ``NO_DISPONIBLE`` o, solo en
    formas doblemente simétricas (T-024 E2, TK-026), exactamente +0.0 ``CALCULADO`` con el
    origen y el método de la simetría; nunca un valor a mano.
    """

    def mismatch(key: str, name: str) -> InputValueError:
        return InputValueError(
            Diagnostic.create(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                key,
                {"field": name},
                location=Location(entity_id=entity_id, revision=revision, field=name),
            )
        )

    for name in _CATALOG_COPIED:
        if getattr(properties, name) != getattr(snapshot, name):
            raise mismatch("section.catalog_properties_mismatch", name)
    for name in _CATALOG_ABSENT:
        prop = getattr(properties, name)
        if prop.status is PropertyStatus.NO_DISPONIBLE:
            continue
        if not (
            snapshot.shape in BISYMMETRIC_SHAPES
            and prop.status is PropertyStatus.CALCULADO
            and prop.value == 0.0
            and math.copysign(1.0, prop.value) > 0.0
            and prop.origin == SYMMETRY_ORIGIN
            and prop.method == SYMMETRY_METHOD
        ):
            raise mismatch("section.catalog_not_derivable", name)


def _same_property(old: SectionProperty, new: SectionProperty) -> bool:
    if old.status is PropertyStatus.CONTRASTADO and new.status is PropertyStatus.CALCULADO:
        # T-025 E2 (D12-B): CONTRASTADO conserva el valor y el método calculados; solo cambian el
        # estado y el origen (que cita el contraste). El valor se sigue recalculando.
        old = SectionProperty(old.value, old.unit, new.origin, old.method, new.status)
    if (old.unit, old.origin, old.method, old.status) != (
        new.unit,
        new.origin,
        new.method,
        new.status,
    ):
        return False
    if old.value is None or new.value is None:
        return old.value is new.value
    return math.isclose(old.value, new.value, rel_tol=MATCH_RELATIVE_TOLERANCE)


def _matches(stored: SectionProperties, computed: SectionProperties) -> bool:
    return all(
        _same_property(getattr(stored, name), getattr(computed, name)) for name in _PROPERTY_UNITS
    )


_FREEFORM_STATUSES: Final = frozenset({PropertyStatus.CALCULADO, PropertyStatus.CONTRASTADO})
_SHEAR_STATUSES: Final = frozenset({PropertyStatus.CALCULADO, PropertyStatus.NO_DISPONIBLE})
_SHEAR_NAMES: Final = ("shear_y", "shear_z")


def _check_shear(
    parameters: SectionParameters,
    shear: ShearAreas | None,
    entity_id: UUID,
    revision: int,
) -> None:
    """Invariantes del grupo opcional de áreas de cortante (T-025, D9-B).

    Ausente (``None``) = nunca calculado: no se exige nada. Presente: unidad de área, estado
    ``CALCULADO`` o ``NO_DISPONIBLE``, valor positivo, y con el método actual debe coincidir con
    el recálculo a partir de los parámetros (no se introduce a mano; método de otra versión: se
    acepta tal como se guardó, O3). Un perfil de catálogo no lleva cortante: el catálogo no lo
    aporta y no se deduce.
    """

    def error(key: str, name: str) -> InputValueError:
        return InputValueError(
            Diagnostic.create(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                key,
                {"field": name},
                location=Location(entity_id=entity_id, revision=revision, field=name),
            )
        )

    if shear is None:
        return
    if isinstance(parameters, CatalogSnapshot | FreeformParameters):
        raise error("section.shear_not_derivable", "shear")
    for name in _SHEAR_NAMES:
        prop: SectionProperty = getattr(shear, name)
        Quantity(1.0, get_unit(prop.unit).dimension).require(AREA)
        if prop.status not in _SHEAR_STATUSES:
            raise error("section.shear_invalid_status", name)
        if prop.value is not None:
            checked_positive(
                prop.value, name, Location(entity_id=entity_id, revision=revision, field=name)
            )
    expected = (
        parametric_shear(parameters)
        if isinstance(parameters, ParametricParameters)
        else rectangular_shear(parameters.b, parameters.h)
    )
    current = {getattr(expected, name).method for name in _SHEAR_NAMES}
    if {getattr(shear, name).method for name in _SHEAR_NAMES} <= current and not all(
        _same_property(getattr(shear, name), getattr(expected, name)) for name in _SHEAR_NAMES
    ):
        raise error("section.shear_mismatch", "shear")


def build_rectangular_section(  # noqa: PLR0913 (identidad, geometría y opciones, por nombre)
    entity_id: UUID,
    revision: int,
    *,
    b: Quantity,
    h: Quantity,
    name: str = "",
    with_shear: bool = False,
) -> SectionRevision:
    """Crea una revisión de sección rectangular y calcula sus propiedades.

    ``with_shear`` añade el grupo de áreas de cortante (T-025, D10-C); por defecto no, para que
    las secciones y los hashes existentes no cambien.
    """
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
        shear=rectangular_shear(parameters.b, parameters.h) if with_shear else None,
    )


def build_parametric_section(
    entity_id: UUID,
    revision: int,
    *,
    shape: ParametricShape,
    dimensions: Mapping[str, Quantity],
    name: str = "",
) -> SectionRevision:
    """Crea una revisión de perfil paramétrico (I, RHS, SHS o CHS) con propiedades y cortante.

    ``dimensions`` son longitudes por nombre (los radios ``r``/``ro`` son opcionales y valen 0).
    Una geometría inválida (EDT-004) se rechaza con su diagnóstico: no existe revisión inválida.
    """
    check_identity(entity_id, revision, "SectionRevision")
    if name:
        require_text(name, "name", Location(entity_id=entity_id, revision=revision, field="name"))
    values: dict[str, float] = {}
    for key, quantity in dimensions.items():
        at = Location(entity_id=entity_id, revision=revision, field=key)
        if not isinstance(quantity, Quantity):
            raise invalid_type(key, "Quantity", quantity, at)
        values[key] = quantity.require(LENGTH).si_value
    parameters = ParametricParameters.from_values(ParametricShape(shape), values)
    return SectionRevision(
        id=entity_id,
        revision=revision,
        name=name,
        family=SectionFamily.PARAMETRICA,
        parameters=parameters,
        properties=parametric_properties(
            parameters, Location(entity_id=entity_id, revision=revision)
        ),
        shear=parametric_shear(parameters),
    )


def build_freeform_section(
    entity_id: UUID,
    revision: int,
    *,
    outer: Sequence[tuple[Quantity, Quantity]],
    holes: Sequence[Sequence[tuple[Quantity, Quantity]]] = (),
    name: str = "",
) -> SectionRevision:
    """Crea una revisión de croquis libre (exterior y huecos poligonales) con sus propiedades.

    Los vértices son pares (y, z) de longitudes. Una geometría inválida (EDT-004:
    autointersección, hueco fuera o tocando, área nula…) se rechaza con su diagnóstico: no existe
    revisión inválida. La sección no lleva áreas de cortante (no hay método para un croquis).
    """
    check_identity(entity_id, revision, "SectionRevision")
    if name:
        require_text(name, "name", Location(entity_id=entity_id, revision=revision, field="name"))

    def contour(
        points: Sequence[tuple[Quantity, Quantity]], field: str
    ) -> tuple[tuple[float, float], ...]:
        result: list[tuple[float, float]] = []
        for i, pair in enumerate(points):
            at = Location(entity_id=entity_id, revision=revision, field=f"{field}[{i}]")
            if not (isinstance(pair, tuple | list) and len(pair) == 2):  # noqa: PLR2004 (par y, z)
                raise invalid_type(f"{field}[{i}]", "par (y, z) de Quantity", pair, at)
            for item in pair:
                if not isinstance(item, Quantity):
                    raise invalid_type(f"{field}[{i}]", "Quantity", item, at)
            result.append((pair[0].require(LENGTH).si_value, pair[1].require(LENGTH).si_value))
        return tuple(result)

    parameters = FreeformParameters(
        contour(outer, "outer"), tuple(contour(h, f"hole[{i}]") for i, h in enumerate(holes))
    )
    return SectionRevision(
        id=entity_id,
        revision=revision,
        name=name,
        family=SectionFamily.FREEFORM,
        parameters=parameters,
        properties=freeform_properties(parameters),
    )
