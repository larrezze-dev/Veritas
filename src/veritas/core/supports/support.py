"""Apoyo de nudo por GDL (plan T-005, D1-D5 y R1-R3; ADR-001 §1, §3 y §5).

Un apoyo es una entidad independiente del nudo (UUID v4, revisión, etiqueta) que guarda la
identidad lógica de su nudo, su base y el estado de cada uno de los seis GDL
(Ux, Uy, Uz, Rx, Ry, Rz) expresados en esa base:

- ``FREE``: sin restricción;
- ``FIXED``: desplazamiento o giro impedido;
- ``SPRING``: resorte lineal, elástico, bilateral y desacoplado, de rigidez finita > 0
  (N/m en traslaciones, N·m/rad en giros; valor SI guardado).

Base GLOBAL o INCLINADA (ADR-001 §1, «Base de apoyo inclinada»): absoluta, fija en el
espacio, con Q de filas ex', ey', ez'; v_apoyo = Q v_global. Un apoyo restringe al menos
un GDL. Los presets (empotramiento, articulación, rodillo) solo construyen los seis
estados; su nombre no se guarda. Este módulo no juzga estabilidad ni mecanismos.
"""

import math
import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum, StrEnum
from typing import ClassVar, Final
from uuid import UUID

from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    InputTypeError,
    InputValueError,
    Location,
)
from veritas.core.geometry.local_axes import ORIENTATION_TOLERANCE, ORTHONORMALITY_TOLERANCE
from veritas.core.geometry.vector import Vec3
from veritas.core.units import FORCE_PER_LENGTH, ROTATIONAL_STIFFNESS, Dimension, Quantity
from veritas.core.validation import invalid_type, out_of_range, require_real

UUID_VERSION: Final = 4
_TRANSLATIONS: Final = 3


class SupportDof(Enum):
    """GDL de apoyo en la base del apoyo, orden de ADR-001 §1.

    ``Enum`` simple (no ``StrEnum``): un GDL de apoyo nunca es igual a un GDL local de
    extremo de barra (``connections.end.Dof``), tampoco en ejecución.
    """

    UX = 0
    UY = 1
    UZ = 2
    RX = 3
    RY = 4
    RZ = 5

    @property
    def label(self) -> str:
        """Nombre visible: Ux, Uy, Uz, Rx, Ry, Rz."""
        return self.name[0] + self.name[1].lower()

    @property
    def is_translation(self) -> bool:
        """True en Ux, Uy, Uz."""
        return self.value < _TRANSLATIONS

    @property
    def stiffness_dimension(self) -> Dimension:
        """Dimensión de la rigidez de resorte: N/m o N·m/rad."""
        return FORCE_PER_LENGTH if self.is_translation else ROTATIONAL_STIFFNESS


class RestraintKind(StrEnum):
    """Estado de un GDL de apoyo."""

    FREE = "FREE"
    FIXED = "FIXED"
    SPRING = "SPRING"


def _support_error(
    code: DiagnosticCode,
    key: str,
    params: Mapping[str, str | int | float] | None = None,
    location: Location | None = None,
) -> InputValueError:
    return InputValueError(Diagnostic.create(code, key, params, location=location))


@dataclass(frozen=True, slots=True)
class DofRestraint:
    """Estado de un GDL; ``stiffness`` (SI) solo en ``SPRING``."""

    kind: RestraintKind
    stiffness: float | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RestraintKind):
            raise invalid_type("restricción", "RestraintKind", self.kind)
        if self.kind is not RestraintKind.SPRING:
            if self.stiffness is not None:
                raise invalid_type("rigidez", "None (solo SPRING tiene rigidez)", self.stiffness)
            return
        stiffness = require_real(self.stiffness, "rigidez")
        if stiffness < sys.float_info.min:  # Cero, negativa o subnormal.
            raise _support_error(
                DiagnosticCode.INPUT_OUT_OF_RANGE,
                "support.spring_nonpositive",
                {"value": repr(stiffness)},
            )
        object.__setattr__(self, "stiffness", stiffness)

    @classmethod
    def spring(cls, dof: SupportDof, stiffness: Quantity) -> "DofRestraint":
        """Resorte con rigidez de la dimensión del GDL (``UNITS_INCOMPATIBLE`` si no)."""
        if not isinstance(dof, SupportDof):
            raise invalid_type("GDL", "SupportDof", dof)
        if not isinstance(stiffness, Quantity):
            raise invalid_type("rigidez", "Quantity", stiffness)
        return cls(RestraintKind.SPRING, stiffness.require(dof.stiffness_dimension).si_value)

    def quantity(self, dof: SupportDof) -> Quantity | None:
        """Rigidez como ``Quantity`` de la dimensión del GDL, o ``None`` si no es resorte."""
        if self.stiffness is None:
            return None
        return Quantity(self.stiffness, dof.stiffness_dimension)


FREE: Final = DofRestraint(RestraintKind.FREE)
FIXED: Final = DofRestraint(RestraintKind.FIXED)

type Restraints = tuple[
    DofRestraint, DofRestraint, DofRestraint, DofRestraint, DofRestraint, DofRestraint
]


class BasisKind(StrEnum):
    """Tipo de base de un apoyo."""

    GLOBAL = "GLOBAL"
    INCLINED = "INCLINED"


def _unit_direction(value: object, field: str) -> Vec3:
    direction = Vec3.direction(value, field)  # type: ignore[arg-type]  # Valida el tipo.
    magnitude = direction.norm()
    if magnitude < sys.float_info.min or not math.isfinite(magnitude):  # Nula o subnormal.
        raise _support_error(
            DiagnosticCode.GEOMETRY_INVALID_ORIENTATION,
            "support.basis_direction_invalid",
            {"field": field},
            Location(field=field),
        )
    return direction.scaled(1.0 / magnitude)


def _check_orthonormal(rows: tuple[Vec3, Vec3, Vec3]) -> None:
    deviation = max(
        abs(a.dot(b) - (1.0 if i == j else 0.0))
        for i, a in enumerate(rows)
        for j, b in enumerate(rows)
    )
    determinant = rows[0].dot(rows[1].cross(rows[2]))
    if deviation > ORTHONORMALITY_TOLERANCE or determinant <= 0.0:
        raise _support_error(
            DiagnosticCode.GEOMETRY_INVALID_ORIENTATION,
            "geometry.basis_not_orthonormal",
            {"deviation": deviation, "determinant": determinant},
        )


@dataclass(frozen=True, slots=True)
class SupportBasis:
    """Base de un apoyo: GLOBAL o INCLINADA con filas ortonormales ex', ey', ez' (global)."""

    GLOBAL: ClassVar["SupportBasis"]

    kind: BasisKind
    rows: tuple[Vec3, Vec3, Vec3] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, BasisKind):
            raise invalid_type("base", "BasisKind", self.kind)
        if self.kind is BasisKind.GLOBAL:
            if self.rows is not None:
                raise invalid_type("filas de la base", "None en base GLOBAL", self.rows)
            return
        if (
            not isinstance(self.rows, tuple)
            or len(self.rows) != _TRANSLATIONS
            or not all(isinstance(row, Vec3) for row in self.rows)
        ):
            raise invalid_type("filas de la base", "tres Vec3", self.rows)
        _check_orthonormal(self.rows)

    @classmethod
    def inclined(cls, x_dir: object, z_ref: object) -> "SupportBasis":
        """Base inclinada absoluta según ADR-001 §1 («Base de apoyo inclinada»), sin fallback."""
        ex = _unit_direction(x_dir, "x_dir")
        zhat = _unit_direction(z_ref, "z_ref")
        w = zhat - ex.scaled(zhat.dot(ex))
        if w.norm() <= ORIENTATION_TOLERANCE:
            raise _support_error(
                DiagnosticCode.GEOMETRY_INVALID_ORIENTATION,
                "support.basis_reference_parallel",
                {"projection": w.norm(), "tolerance": ORIENTATION_TOLERANCE},
                Location(field="z_ref"),
            )
        w = w - ex.scaled(w.dot(ex))  # Segunda pasada de Gram-Schmidt.
        ez = w.scaled(1.0 / w.norm())
        ey = ez.cross(ex)
        return cls(BasisKind.INCLINED, (ex, ey, ez))

    def matrix(self) -> tuple[tuple[float, float, float], ...]:
        """Q con filas ex', ey', ez' (identidad en GLOBAL)."""
        if self.rows is None:
            return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        return tuple(row.components() for row in self.rows)


SupportBasis.GLOBAL = SupportBasis(BasisKind.GLOBAL)


def _check_identity(entity_id: object, revision: object, label: object) -> None:
    if not isinstance(entity_id, UUID) or entity_id.version != UUID_VERSION:
        raise invalid_type("Support.id", "UUID v4", entity_id)
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise invalid_type("Support.revision", "entero", revision)
    if revision < 1:
        raise out_of_range("Support.revision", revision, ">= 1")
    if not isinstance(label, str):
        raise invalid_type("Support.label", "texto", label)


def check_restraints(value: object, location: Location | None = None) -> Restraints:
    """Seis ``DofRestraint`` en el orden de ``SupportDof``, al menos uno no libre."""
    if (
        not isinstance(value, tuple)
        or len(value) != len(SupportDof)
        or not all(isinstance(item, DofRestraint) for item in value)
    ):
        raise InputTypeError(
            Diagnostic.create(
                DiagnosticCode.INPUT_INVALID_TYPE,
                "support.invalid_states",
                {"received": repr(value)},
                location=location,
            )
        )
    if all(item.kind is RestraintKind.FREE for item in value):
        raise _support_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE, "support.no_restraint", None, location
        )
    return value


@dataclass(frozen=True, slots=True)
class Support:
    """Revisión inmutable de apoyo: nudo, base y seis estados en el orden de ``SupportDof``."""

    id: UUID
    revision: int
    label: str
    node_id: UUID
    basis: SupportBasis
    restraints: Restraints

    def __post_init__(self) -> None:
        _check_identity(self.id, self.revision, self.label)
        location = Location(entity_id=self.id, revision=self.revision)
        if not isinstance(self.node_id, UUID):
            raise invalid_type("Support.node_id", "UUID", self.node_id, location)
        if not isinstance(self.basis, SupportBasis):
            raise invalid_type("Support.basis", "SupportBasis", self.basis, location)
        check_restraints(self.restraints, location)

    def restraint(self, dof: SupportDof) -> DofRestraint:
        """Estado de un GDL de apoyo (un GDL local de extremo da ``INPUT_INVALID_TYPE``)."""
        if not isinstance(dof, SupportDof):
            raise invalid_type("GDL", "SupportDof", dof)
        return self.restraints[dof.value]


def fixed_restraints() -> Restraints:
    """Preset empotramiento: seis GDL fijos."""
    return (FIXED,) * 6


def pinned_restraints() -> Restraints:
    """Preset articulación: traslaciones fijas, giros libres."""
    return (FIXED, FIXED, FIXED, FREE, FREE, FREE)


def roller_restraints(axis: SupportDof = SupportDof.UZ) -> Restraints:
    """Preset rodillo: solo la traslación según ``axis`` (de la base del apoyo) fija."""
    if not isinstance(axis, SupportDof):
        raise invalid_type("eje del rodillo", "SupportDof", axis)
    if not axis.is_translation:
        raise _support_error(
            DiagnosticCode.INPUT_OUT_OF_RANGE, "support.roller_axis", {"axis": axis.label}
        )
    return tuple(FIXED if dof is axis else FREE for dof in SupportDof)  # type: ignore[return-value]


def build_restraints(
    fixed: Iterable[SupportDof] = (), springs: Mapping[SupportDof, Quantity] | None = None
) -> Restraints:
    """Seis estados a partir de GDL fijos y resortes; un GDL repetido se rechaza."""
    states: dict[SupportDof, DofRestraint] = {}
    pairs: list[tuple[SupportDof, DofRestraint | Quantity]] = [(dof, FIXED) for dof in fixed]
    pairs.extend((springs or {}).items())
    for dof, value in pairs:
        if not isinstance(dof, SupportDof):
            raise invalid_type("GDL", "SupportDof", dof)
        if dof in states:
            raise _support_error(
                DiagnosticCode.INPUT_OUT_OF_RANGE, "support.duplicate_dof", {"dof": dof.label}
            )
        states[dof] = value if isinstance(value, DofRestraint) else DofRestraint.spring(dof, value)
    return check_restraints(tuple(states.get(dof, FREE) for dof in SupportDof))
