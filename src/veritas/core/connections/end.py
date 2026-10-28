"""Configuración de un extremo I/J de barra (ADR-001 §1; planes T-003, D4, y T-010, D1).

Seis estados GDL en el orden común (Ux, Uy, Uz, Rx, Ry, Rz), expresados en la base LOCAL
de la barra:

- ``RIGID``: continuidad cinemática con el nudo (por defecto, CON-001);
- ``RELEASED``: no transmite la componente de esfuerzo conjugada;
- ``SPRING``: resorte lineal, elástico, bilateral y desacoplado, de rigidez finita > 0
  (N/m en traslaciones, N·m/rad en giros; valor SI guardado).

Rigidez nula o infinita no se admiten como números: son ``RELEASED`` y ``RIGID``
(propuesta v1.1.0 §6.2). Los presets solo construyen los seis estados; su nombre no se
guarda. Este módulo no juzga estabilidad ni compatibilidad con el elemento FEM.
"""

import sys
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Self

from veritas.core.diagnostics import Diagnostic, DiagnosticCode, InputValueError
from veritas.core.units import FORCE_PER_LENGTH, ROTATIONAL_STIFFNESS, Dimension, Quantity
from veritas.core.validation import invalid_type, require_real

_TRANSLATIONS: Final = 3


class Dof(StrEnum):
    """Grados de libertad locales de un extremo, en el orden de ADR-001 §1."""

    UX = "Ux"
    UY = "Uy"
    UZ = "Uz"
    RX = "Rx"
    RY = "Ry"
    RZ = "Rz"

    @property
    def position(self) -> int:
        """Posición en el orden (Ux, Uy, Uz, Rx, Ry, Rz)."""
        return list(Dof).index(self)

    @property
    def is_translation(self) -> bool:
        """True en Ux, Uy, Uz."""
        return self.position < _TRANSLATIONS

    @property
    def stiffness_dimension(self) -> Dimension:
        """Dimensión de la rigidez de resorte: N/m o N·m/rad."""
        return FORCE_PER_LENGTH if self.is_translation else ROTATIONAL_STIFFNESS


class DofState(StrEnum):
    """Estado de un GDL de extremo."""

    RIGID = "RIGID"
    RELEASED = "RELEASED"
    SPRING = "SPRING"


class MemberEnd(StrEnum):
    """Extremo de una barra dirigida I -> J."""

    I = "I"  # noqa: E741 (nombre del extremo en la SRS)
    J = "J"


type States = tuple[DofState, DofState, DofState, DofState, DofState, DofState]
type Stiffnesses = tuple[
    float | None, float | None, float | None, float | None, float | None, float | None
]

_NO_STIFFNESS: Final[Stiffnesses] = (None, None, None, None, None, None)


def _connection_error(key: str, params: dict[str, str]) -> InputValueError:
    return InputValueError(Diagnostic.create(DiagnosticCode.INPUT_OUT_OF_RANGE, key, params))


def _check_stiffness(dof: Dof, state: DofState, value: object) -> float | None:
    if state is not DofState.SPRING:
        if value is not None:
            raise invalid_type(f"rigidez {dof}", "None (solo SPRING tiene rigidez)", value)
        return None
    stiffness = require_real(value, f"rigidez {dof}")
    if stiffness < sys.float_info.min:  # Cero, negativa o subnormal.
        raise _connection_error(
            "connection.spring_nonpositive", {"dof": str(dof), "value": repr(stiffness)}
        )
    return stiffness


@dataclass(frozen=True, slots=True)
class EndConnection:
    """Seis estados GDL de un extremo y su rigidez SI (solo en ``SPRING``), orden ``Dof``."""

    states: States
    stiffness: Stiffnesses = _NO_STIFFNESS

    def __post_init__(self) -> None:
        if not isinstance(self.states, tuple) or len(self.states) != len(Dof):
            raise TypeError("EndConnection.states debe ser una tupla de seis DofState")
        if not all(isinstance(state, DofState) for state in self.states):
            raise TypeError("EndConnection.states solo admite DofState")
        if not isinstance(self.stiffness, tuple) or len(self.stiffness) != len(Dof):
            raise TypeError("EndConnection.stiffness debe ser una tupla de seis valores")
        checked = tuple(
            _check_stiffness(dof, state, value)
            for dof, state, value in zip(Dof, self.states, self.stiffness, strict=True)
        )
        object.__setattr__(self, "stiffness", checked)

    @classmethod
    def rigid(cls) -> Self:
        """Extremo rígido en los seis GDL (CON-001)."""
        return cls((DofState.RIGID,) * 6)

    @classmethod
    def pinned(cls) -> Self:
        """Preset articulado: Ry y Rz liberados; Rx y traslaciones rígidos (propuesta §6.2)."""
        return cls.rigid().with_dof(Dof.RY, DofState.RELEASED).with_dof(Dof.RZ, DofState.RELEASED)

    @classmethod
    def semi_rigid(cls, dof: Dof, stiffness: Quantity) -> Self:
        """Preset semirrígido: resorte en un giro; el resto rígido."""
        if not isinstance(dof, Dof):
            raise invalid_type("GDL", "Dof", dof)
        if dof.is_translation:
            raise _connection_error("connection.semi_rigid_dof", {"dof": str(dof)})
        return cls.rigid().with_dof(dof, DofState.SPRING, stiffness)

    def with_dof(self, dof: Dof, state: DofState, stiffness: Quantity | None = None) -> Self:
        """Copia con un GDL cambiado. ``stiffness`` solo en ``SPRING`` y de su dimensión."""
        if not isinstance(dof, Dof):
            raise invalid_type("GDL", "Dof", dof)
        if not isinstance(state, DofState):
            raise invalid_type("estado", "DofState", state)
        if state is DofState.SPRING and not isinstance(stiffness, Quantity):
            raise invalid_type(f"rigidez {dof}", "Quantity", stiffness)
        if state is not DofState.SPRING and stiffness is not None:
            raise invalid_type(f"rigidez {dof}", "None (solo SPRING tiene rigidez)", stiffness)
        value = (
            None
            if stiffness is None
            else stiffness.require(dof.stiffness_dimension).si_value  # UNITS_INCOMPATIBLE
        )
        states = list(self.states)
        values = list(self.stiffness)
        states[dof.position], values[dof.position] = state, value
        return type(self)(tuple(states), tuple(values))  # type: ignore[arg-type]

    def state(self, dof: Dof) -> DofState:
        """Estado de un GDL."""
        return self.states[dof.position]

    def stiffness_of(self, dof: Dof) -> Quantity | None:
        """Rigidez como ``Quantity`` de la dimensión del GDL, o ``None`` si no es resorte."""
        value = self.stiffness[dof.position]
        return None if value is None else Quantity(value, dof.stiffness_dimension)

    @property
    def is_rigid(self) -> bool:
        """True si los seis GDL son ``RIGID``."""
        return all(state is DofState.RIGID for state in self.states)
