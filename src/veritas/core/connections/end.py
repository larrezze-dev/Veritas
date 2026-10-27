"""Configuración de un extremo I/J de barra (ADR-001 §1; plan T-003, D4).

Seis estados GDL en el orden común (Ux, Uy, Uz, Rx, Ry, Rz), expresados en la base LOCAL
de la barra. En T-003 solo existe el estado ``RIGID``; liberaciones y resortes llegan en
T-010 con su semántica y pruebas.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Self


class Dof(StrEnum):
    """Grados de libertad locales de un extremo, en el orden de ADR-001 §1."""

    UX = "Ux"
    UY = "Uy"
    UZ = "Uz"
    RX = "Rx"
    RY = "Ry"
    RZ = "Rz"


class DofState(StrEnum):
    """Estado de un GDL de extremo. Solo ``RIGID`` en T-003."""

    RIGID = "RIGID"


@dataclass(frozen=True, slots=True)
class EndConnection:
    """Seis estados GDL de un extremo, en el orden de ``Dof``."""

    states: tuple[DofState, DofState, DofState, DofState, DofState, DofState]

    def __post_init__(self) -> None:
        if not isinstance(self.states, tuple) or len(self.states) != len(Dof):
            raise TypeError("EndConnection.states debe ser una tupla de seis DofState")
        if not all(isinstance(state, DofState) for state in self.states):
            raise TypeError("EndConnection.states solo admite DofState")

    @classmethod
    def rigid(cls) -> Self:
        """Extremo rígido en los seis GDL (CON-001)."""
        return cls((DofState.RIGID,) * 6)

    def state(self, dof: Dof) -> DofState:
        """Estado de un GDL."""
        return self.states[list(Dof).index(dof)]
