"""Selección simple del visor (plan T-011, E2): un nudo o una barra, o nada.

Sin Qt ni PyVista. La selección referencia entidades por su identidad estable (UUID) y nunca
es fuente de verdad: si el modelo cambia, se vuelve a resolver contra la escena nueva.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID


class SelectionKind(StrEnum):
    """Tipo de entidad seleccionable."""

    NODE = "NODE"
    MEMBER = "MEMBER"


@dataclass(frozen=True, slots=True)
class Selection:
    """Entidad seleccionada: tipo e identidad."""

    kind: SelectionKind
    id: UUID
