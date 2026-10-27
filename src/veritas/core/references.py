"""Referencias versionadas y registro de revisiones inmutables (ADR-001 §3; plan T-004, D2).

Una referencia es el par exacto (entity_id, revision), nunca «la última». El registro
guarda todas las revisiones de un tipo de entidad (material o sección) sin modificarlas:

- una revisión nueva de un id existente es siempre max+1;
- registrar un par existente con el mismo contenido no cambia nada; con distinto
  contenido, o dejando huecos en la numeración, es ``REVISION_CONFLICT``;
- una revisión no puede cambiar la familia de la entidad.
"""

from collections.abc import Hashable
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    InputTypeError,
    InputValueError,
    Location,
)

UUID_VERSION = 4


def check_identity(entity_id: object, revision: object, kind: str) -> None:
    """Valida un par (UUID v4, revisión entera >= 1); bool no es una revisión."""
    if not isinstance(entity_id, UUID) or entity_id.version != UUID_VERSION:
        raise TypeError(f"{kind}.id debe ser un UUID v4")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise TypeError(f"{kind}.revision debe ser un entero")
    if revision < 1:
        raise ValueError(f"{kind}.revision debe ser >= 1")


@dataclass(frozen=True, slots=True)
class RevisionRef:
    """Referencia exacta a una revisión inmutable de material o sección."""

    entity_id: UUID
    revision: int

    def __post_init__(self) -> None:
        check_identity(self.entity_id, self.revision, "RevisionRef")

    def __str__(self) -> str:
        return f"{self.entity_id} rev {self.revision}"


class VersionedEntity(Protocol):
    """Revisión inmutable con identidad y familia."""

    @property
    def id(self) -> UUID: ...

    @property
    def revision(self) -> int: ...

    @property
    def family(self) -> Hashable: ...

    @property
    def ref(self) -> RevisionRef: ...


def require_ref(value: object, field: str, location: Location | None = None) -> RevisionRef:
    """Devuelve ``value`` si es una ``RevisionRef``; si no, ``INPUT_INVALID_TYPE``."""
    if not isinstance(value, RevisionRef):
        raise InputTypeError(
            Diagnostic.create(
                DiagnosticCode.INPUT_INVALID_TYPE,
                "input.invalid_type",
                {"field": field, "expected": "RevisionRef", "received": type(value).__name__},
                location=location,
            )
        )
    return value


class RevisionRegistry[T: VersionedEntity]:
    """Todas las revisiones de un tipo de entidad, indexadas por id y número."""

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._revisions: dict[UUID, list[T]] = {}

    def __contains__(self, ref: object) -> bool:
        if not isinstance(ref, RevisionRef):
            return False
        history = self._revisions.get(ref.entity_id, [])
        return ref.revision <= len(history)

    def get(self, ref: RevisionRef, location: Location | None = None) -> T:
        """Revisión exacta o ``REFERENCE_NOT_FOUND`` (con la ubicación de quien la usa)."""
        if ref not in self:
            raise self._not_found(str(ref), location)
        return self._revisions[ref.entity_id][ref.revision - 1]

    def latest(self, entity_id: UUID) -> T:
        """Última revisión de una entidad o ``REFERENCE_NOT_FOUND``."""
        try:
            return self._revisions[entity_id][-1]
        except (KeyError, TypeError):
            raise self._not_found(str(entity_id)) from None

    def history(self, entity_id: UUID) -> tuple[T, ...]:
        """Todas las revisiones de una entidad, de la 1 a la última."""
        self.latest(entity_id)
        return tuple(self._revisions[entity_id])

    def register(self, revision: T) -> T:
        """Alta de una revisión; devuelve la guardada (la existente si es idéntica)."""
        history = self._revisions.get(revision.id, [])
        if revision.revision <= len(history):
            stored = history[revision.revision - 1]
            if stored != revision:
                raise self._conflict(revision, "mismo par con distinto contenido")
            return stored
        if revision.revision != len(history) + 1:
            raise self._conflict(revision, f"se esperaba la revisión {len(history) + 1}")
        if history and history[-1].family != revision.family:
            raise InputValueError(
                Diagnostic.create(
                    DiagnosticCode.INPUT_OUT_OF_RANGE,
                    "model.family_change",
                    {
                        "kind": self._kind,
                        "current": str(history[-1].family),
                        "requested": str(revision.family),
                    },
                    location=Location(
                        entity_id=revision.id, revision=revision.revision, field="family"
                    ),
                )
            )
        self._revisions.setdefault(revision.id, []).append(revision)
        return revision

    def _not_found(self, entity_id: str, location: Location | None = None) -> InputValueError:
        return InputValueError(
            Diagnostic.create(
                DiagnosticCode.REFERENCE_NOT_FOUND,
                "model.reference_not_found",
                {"kind": self._kind, "entity_id": entity_id},
                location=location,
            )
        )

    def _conflict(self, revision: T, reason: str) -> InputValueError:
        return InputValueError(
            Diagnostic.create(
                DiagnosticCode.REVISION_CONFLICT,
                "model.revision_conflict",
                {
                    "kind": self._kind,
                    "entity_id": str(revision.id),
                    "revision": revision.revision,
                    "reason": reason,
                },
                location=Location(entity_id=revision.id, revision=revision.revision),
            )
        )
