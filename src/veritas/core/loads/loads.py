"""Cargas nodales, de barra, peso propio y asientos (LD-002, LD-003; T-006, T-015, T-016).

Convenciones (ADR-001 §1-§2, ampliación aprobada en el plan T-006):

- Carga nodal: fuerza (N) y momento (N·m) en base GLOBAL, positivos en +X/+Y/+Z y por la
  regla de la mano derecha.
- Carga uniforme de barra: solo fuerzas, en toda la longitud, por metro de longitud REAL
  (no proyectada). En base LOCAL las componentes siguen a la base local actual de la
  barra: q_global = Qᵀ q_local.
- Cargas de barra ampliadas (plan T-015, D1-D4): puntual (fuerza N y par N·m) en
  ξ = x/L con 0 < ξ < 1; fuerza distribuida (N/m) y momento distribuido (N·m/m) lineales
  entre ξa y ξb (0 ≤ ξa < ξb ≤ 1), nulos fuera del tramo. Base GLOBAL o LOCAL como la carga
  uniforme; intensidades por metro de longitud real.
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
from veritas.core.validation import invalid_type, positive_float, require_real

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


# Longitud relativa mínima de un tramo de carga distribuida (plan T-015, D2-A).
MIN_SEGMENT: Final = 1e-9


def _member_refs(
    load: "MemberPointLoad | MemberDistributedLoad | MemberDistributedMoment",
) -> Location:
    """Identidad, referencias y base de una carga de barra ampliada."""
    check_entity_identity(load.id, load.revision, type(load).__name__)
    location = _check_refs(
        load.id, load.revision, {"case_id": load.case_id, "member_id": load.member_id}
    )
    if not isinstance(load.basis, LoadBasis):
        raise invalid_type("basis", "LoadBasis", load.basis, location)
    return location


def _position_error(
    field: str, value: float, condition: str, location: Location
) -> InputValueError:
    return InputValueError(
        Diagnostic.create(
            DiagnosticCode.INPUT_OUT_OF_RANGE,
            "load.position_out_of_range",
            {"field": field, "value": repr(value), "condition": condition},
            location=location,
        )
    )


def _segment(start: object, end: object, location: Location) -> tuple[float, float]:
    """(ξa, ξb) reales con 0 ≤ ξa < ξb ≤ 1 y ξb − ξa > ``MIN_SEGMENT``."""
    start = require_real(start, "start", location)
    end = require_real(end, "end", location)
    if not 0.0 <= start <= 1.0:
        raise _position_error("start", start, "0 ≤ ξ ≤ 1", location)
    if not 0.0 <= end <= 1.0:
        raise _position_error("end", end, "0 ≤ ξ ≤ 1", location)
    if end - start <= MIN_SEGMENT:
        raise InputValueError(
            Diagnostic.create(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "load.segment_too_short",
                {"start": repr(start), "end": repr(end), "minimum": repr(MIN_SEGMENT)},
                location=location,
            )
        )
    return start, end


@dataclass(frozen=True, slots=True)
class MemberPointLoad:
    """Fuerza (N) y par (N·m) concentrados en ξ = x/L, 0 < ξ < 1, base global o local.

    En los extremos (ξ = 0 o 1) se usa una carga nodal (plan T-015, D2-A).
    """

    id: UUID
    revision: int
    case_id: UUID
    member_id: UUID
    basis: LoadBasis
    position: float
    force: Vec3
    moment: Vec3

    def __post_init__(self) -> None:
        location = _member_refs(self)
        position = require_real(self.position, "position", location)
        if not 0.0 < position < 1.0:
            raise _position_error("position", position, "0 < ξ < 1", location)
        object.__setattr__(self, "position", position)
        check_load_vector({"force": self.force, "moment": self.moment}, location)


@dataclass(frozen=True, slots=True)
class MemberDistributedLoad:
    """Fuerza distribuida (N/m de longitud real) lineal entre ξa y ξb, nula fuera.

    ``intensity_start`` en ξa e ``intensity_end`` en ξb; iguales = parcial uniforme.
    """

    id: UUID
    revision: int
    case_id: UUID
    member_id: UUID
    basis: LoadBasis
    start: float
    end: float
    intensity_start: Vec3
    intensity_end: Vec3

    def __post_init__(self) -> None:
        location = _member_refs(self)
        start, end = _segment(self.start, self.end, location)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        check_load_vector(
            {"intensity_start": self.intensity_start, "intensity_end": self.intensity_end},
            location,
        )


@dataclass(frozen=True, slots=True)
class MemberDistributedMoment:
    """Momento distribuido (N·m/m de longitud real) lineal entre ξa y ξb, nulo fuera.

    La componente x (base local) es torsor; y, z flectores.
    """

    id: UUID
    revision: int
    case_id: UUID
    member_id: UUID
    basis: LoadBasis
    start: float
    end: float
    intensity_start: Vec3
    intensity_end: Vec3

    def __post_init__(self) -> None:
        location = _member_refs(self)
        start, end = _segment(self.start, self.end, location)
        object.__setattr__(self, "start", start)
        object.__setattr__(self, "end", end)
        check_load_vector(
            {"intensity_start": self.intensity_start, "intensity_end": self.intensity_end},
            location,
        )


type MemberLinearLoad = MemberDistributedLoad | MemberDistributedMoment

type Load = (
    NodalLoad
    | MemberUniformLoad
    | MemberPointLoad
    | MemberDistributedLoad
    | MemberDistributedMoment
    | SelfWeight
    | SupportSettlement
)


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


def linear_contribution(r_a: Vec3, r_b: Vec3, q_a: Vec3, q_b: Vec3) -> tuple[Vec3, Vec3]:
    """(F, M) respecto al origen de una fuerza distribuida lineal en el tramo r_a -> r_b.

    q(t) = q_a + t·(q_b − q_a) y r(t) = r_a + t·d, con d = r_b − r_a, t ∈ [0, 1] y s = |d|:
    F = s·(q_a + q_b)/2 y M = s·[r_a × q_a + r_a × Δq/2 + d × q_a/2 + d × Δq/3], Δq = q_b − q_a.
    """
    d = r_b - r_a
    s = d.norm()
    delta = q_b - q_a
    force = (q_a + q_b).scaled(s / 2.0)
    for q, f in zip((q_a + q_b).components(), force.components(), strict=True):
        if q != 0.0 and abs(f) < sys.float_info.min:
            raise _unrepresentable("resultante de la carga distribuida", None)
    moment = (
        r_a.cross(q_a)
        + r_a.cross(delta).scaled(0.5)
        + d.cross(q_a).scaled(0.5)
        + d.cross(delta).scaled(1.0 / 3.0)
    ).scaled(s)
    return force, moment


def linear_moment_contribution(r_a: Vec3, r_b: Vec3, m_a: Vec3, m_b: Vec3) -> tuple[Vec3, Vec3]:
    """(0, M) de un momento distribuido lineal en el tramo r_a -> r_b: M = s·(m_a + m_b)/2."""
    s = (r_b - r_a).norm()
    moment = (m_a + m_b).scaled(s / 2.0)
    for m, total in zip((m_a + m_b).components(), moment.components(), strict=True):
        if m != 0.0 and abs(total) < sys.float_info.min:
            raise _unrepresentable("resultante del momento distribuido", None)
    return Vec3(0.0, 0.0, 0.0), moment
