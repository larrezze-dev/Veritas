"""Caso de carga (LD-001; plan T-006, D1; ADR-001 §3).

Entidad con UUID v4, revisión, nombre visible, categoría y descripción. El nombre no es
único (D1-A): la identidad es el UUID. Las categorías son la clasificación general de
acciones permanentes, variables y accidentales (DB-SE, cláusula a verificar con la
edición vigente). Sin subtipos, ψ ni combinaciones (F5A).
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from veritas.core.diagnostics import Location
from veritas.core.validation import invalid_type, out_of_range, require_text

UUID_VERSION: Final = 4


class LoadCategory(StrEnum):
    """Categoría de un caso de carga."""

    PERMANENT = "PERMANENT"
    VARIABLE = "VARIABLE"
    ACCIDENTAL = "ACCIDENTAL"


def check_entity_identity(entity_id: object, revision: object, kind: str) -> None:
    """Par (UUID v4, revisión entera >= 1) con diagnósticos ``INPUT_*``."""
    if not isinstance(entity_id, UUID) or entity_id.version != UUID_VERSION:
        raise invalid_type(f"{kind}.id", "UUID v4", entity_id)
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise invalid_type(f"{kind}.revision", "entero", revision)
    if revision < 1:
        raise out_of_range(f"{kind}.revision", revision, ">= 1")


@dataclass(frozen=True, slots=True)
class LoadCase:
    """Revisión inmutable de un caso de carga."""

    id: UUID
    revision: int
    name: str
    category: LoadCategory
    description: str = ""

    def __post_init__(self) -> None:
        check_entity_identity(self.id, self.revision, "LoadCase")

        def at(field: str) -> Location:
            return Location(entity_id=self.id, revision=self.revision, field=field)

        require_text(self.name, "name", at("name"))
        if not isinstance(self.category, LoadCategory):
            raise invalid_type("category", "LoadCategory", self.category, at("category"))
        if not isinstance(self.description, str):
            raise invalid_type("description", "texto", self.description, at("description"))
