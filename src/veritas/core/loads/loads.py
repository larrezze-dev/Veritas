"""Cargas nodales, de barra, peso propio y asientos de apoyo (LD-002, LD-003; T-006, T-016).

Convenciones (ADR-001 §1-§2, ampliación aprobada en el plan T-006):

- Carga nodal: fuerza (N) y momento (N·m) en base GLOBAL, positivos en +X/+Y/+Z y por la
  regla de la mano derecha.
- Carga uniforme de barra: solo fuerzas, en toda la longitud, por metro de longitud REAL
  (no proyectada). En base LOCAL las componentes siguen a la base local actual de la
  barra: q_global = Qᵀ q_local.
- Peso propio: q = ρ·A·g por metro de longitud real, dirección -Z global, derivado bajo
  demanda de la revisión actual de material y sección de cada barra. g se declara en la
  definición; por defecto la gravedad normal convencional 9,80665 m/s² (CGPM 1901).
- Asiento de apoyo (plan T-016): desplazamiento (m) y giro (rad) impuestos en la BASE DEL
  APOYO (global o inclinada, ADR-001 §1): traslación positiva según +x', +y', +z' y giro
  positivo por la regla de la mano derecha (un descenso en base global es Uz < 0). Es una
  acción sin fuerza prescrita: no aporta a la resultante ΣF ni ΣM.

Las cargas nodales, de barra y los asientos son inmutables (revisión 1); el peso propio
admite revisiones. Una carga con todas las componentes nulas no se admite. Las acciones son
prescritas: su resultante es una comprobación de entrada, no un resultado de análisis.
"""

import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    InputValueError,
    Location,
)
from veritas.core.geometry.vector import Vec3
from veritas.core.loads.load_case import check_entity_identity
from veritas.core.units import (
    ACCELERATION,
    FORCE,
    FORCE_PER_LENGTH,
    LINEAR_MASS,
    MOMENT,
    Dimension,
    Quantity,
)
from veritas.core.validation import invalid_type, positive_float

STANDARD_GRAVITY: Final = Quantity(9.80665, ACCELERATION)


class LoadBasis(StrEnum):
    """Base de las componentes de una carga de barra."""

    GLOBAL = "GLOBAL"
    LOCAL = "LOCAL"


def _unrepresentable(operation: str, location: Location | None) -> InputValueError:
    return InputValueError(
        Diagnostic.create(
            DiagnosticCode.UNITS_UNREPRESENTABLE,
            "units.unrepresentable",
            {"operation": operation},
            location=location,
        )
    )


def check_load_vector(vectors: dict[str, object], location: Location) -> None:
    """``Vec3`` sin componentes subnormales y con alguna componente no nula."""
    for field, vector in vectors.items():
        if not isinstance(vector, Vec3):
            raise invalid_type(field, "Vec3", vector, location)
        for component in vector.components():
            if component != 0.0 and abs(component) < sys.float_info.min:
                raise _unrepresentable(f"componente subnormal de {field}", location)
    if all(c == 0.0 for v in vectors.values() if isinstance(v, Vec3) for c in v.components()):
        raise InputValueError(
            Diagnostic.create(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "load.all_zero",
                {"fields": ", ".join(vectors)},
                location=location,
            )
        )


def _check_refs(entity_id: UUID, revision: int, refs: dict[str, object]) -> Location:
    location = Location(entity_id=entity_id, revision=revision)
    for field, value in refs.items():
        if not isinstance(value, UUID):
            raise invalid_type(field, "UUID", value, location)
    return location


@dataclass(frozen=True, slots=True)
class NodalLoad:
    """Fuerza (N) y momento (N·m) en base global aplicados en un nudo."""

    id: UUID
    revision: int
    case_id: UUID
    node_id: UUID
    force: Vec3
    moment: Vec3

    def __post_init__(self) -> None:
        check_entity_identity(self.id, self.revision, "NodalLoad")
        location = _check_refs(
            self.id, self.revision, {"case_id": self.case_id, "node_id": self.node_id}
        )
        check_load_vector({"force": self.force, "moment": self.moment}, location)


@dataclass(frozen=True, slots=True)
class MemberUniformLoad:
    """Carga uniforme (N/m de longitud real) en toda la barra, base global o local."""

    id: UUID
    revision: int
    case_id: UUID
    member_id: UUID
    basis: LoadBasis
    intensity: Vec3

    def __post_init__(self) -> None:
        check_entity_identity(self.id, self.revision, "MemberUniformLoad")
        location = _check_refs(
            self.id, self.revision, {"case_id": self.case_id, "member_id": self.member_id}
        )
        if not isinstance(self.basis, LoadBasis):
            raise invalid_type("basis", "LoadBasis", self.basis, location)
        check_load_vector({"intensity": self.intensity}, location)


@dataclass(frozen=True, slots=True)
class SelfWeight:
    """Peso propio de todas las barras con gravedad ``gravity`` (m/s², SI) hacia -Z."""

    id: UUID
    revision: int
    case_id: UUID
    gravity: float

    def __post_init__(self) -> None:
        check_entity_identity(self.id, self.revision, "SelfWeight")
        _check_refs(self.id, self.revision, {"case_id": self.case_id})
        location = Location(entity_id=self.id, revision=self.revision, field="gravity")
        object.__setattr__(self, "gravity", positive_float(self.gravity, "gravity", location))


@dataclass(frozen=True, slots=True)
class SupportSettlement:
    """Asiento impuesto (traslación en m, giro en rad) en la base de un apoyo.

    Cada componente no nula exige un GDL FIXED del apoyo; lo comprueba el modelo.
    """

    id: UUID
    revision: int
    case_id: UUID
    support_id: UUID
    translation: Vec3
    rotation: Vec3

    def __post_init__(self) -> None:
        check_entity_identity(self.id, self.revision, "SupportSettlement")
        location = _check_refs(
            self.id, self.revision, {"case_id": self.case_id, "support_id": self.support_id}
        )
        check_load_vector({"translation": self.translation, "rotation": self.rotation}, location)


type Load = NodalLoad | MemberUniformLoad | SelfWeight | SupportSettlement


def self_weight_intensity(linear_mass: Quantity, gravity: Quantity) -> Quantity:
    """Módulo ρ·A·g (N/m), estrictamente positivo; la dirección (-Z) la aplica el modelo."""
    if not isinstance(linear_mass, Quantity):
        raise invalid_type("linear_mass", "Quantity", linear_mass)
    if not isinstance(gravity, Quantity):
        raise invalid_type("gravity", "Quantity", gravity)
    mass = linear_mass.require(LINEAR_MASS)
    positive_float(gravity.require(ACCELERATION).si_value, "gravity")
    return (mass * gravity).require(FORCE_PER_LENGTH)


def vector_quantities(vector: Vec3, dimension: Dimension) -> tuple[Quantity, Quantity, Quantity]:
    """Tres magnitudes de la dimensión dada a partir de un vector SI."""
    x, y, z = vector.components()
    return (Quantity(x, dimension), Quantity(y, dimension), Quantity(z, dimension))


@dataclass(frozen=True, slots=True)
class LoadResultant:
    """ΣF (N) y ΣM (N·m, respecto al origen global) de las acciones prescritas de un caso.

    Comprobación de la entrada, no un resultado de análisis. ``load_count`` = 0 indica un
    caso sin cargas (no un cero calculado).
    """

    force: tuple[Quantity, Quantity, Quantity]
    moment: tuple[Quantity, Quantity, Quantity]
    load_count: int

    @classmethod
    def from_vectors(cls, force: Vec3, moment: Vec3, load_count: int) -> "LoadResultant":
        """Resultante a partir de vectores SI."""
        return cls(vector_quantities(force, FORCE), vector_quantities(moment, MOMENT), load_count)


def point_contribution(position: Vec3, force: Vec3, moment: Vec3) -> tuple[Vec3, Vec3]:
    """(F, M + r × F) de una acción puntual en ``position`` respecto al origen."""
    return force, moment + position.cross(force)


def uniform_contribution(p_i: Vec3, p_j: Vec3, q_global: Vec3) -> tuple[Vec3, Vec3]:
    """(F, r_medio × F) con F = q·L de una carga uniforme en toda la barra I -> J."""
    length = (p_j - p_i).norm()
    force = q_global.scaled(length)
    for q, f in zip(q_global.components(), force.components(), strict=True):
        if q != 0.0 and abs(f) < sys.float_info.min:
            raise _unrepresentable("resultante q·L", None)
    middle = (p_i + p_j).scaled(0.5)
    return force, middle.cross(force)
