"""Configuraciones de normativas: entidades inmutables y validación (ADR-007; plan T-014, D2).

Una configuración de normativa es un **dato versionado**: identifica un sistema normativo,
su jurisdicción, edición y documentos, y agrupa parámetros con unidad y fuente. No ejecuta
comprobaciones ni declara cobertura: la cobertura la fija el código (``builtin.coverage``),
nunca estos datos (NOR-004, NOR-010).

Convenciones:

- Cada ``NormConfiguration`` es una revisión inmutable identificada por (UUID v4, revisión).
  Editar crea la revisión n+1 (ver ``registry.NormRegistry``); las anteriores no cambian.
- Un parámetro guarda su valor en SI (como el resto del dominio), su ``dimension`` y el
  símbolo de presentación ``unit``; la fuente (cláusula o documento) es obligatoria
  (NOR-002). No hay catálogo cerrado de claves: no se inventa contenido normativo.
- El −0,0 se conserva tal cual (igualdad de ``float``), como en el resto del dominio.
- Dominio puro: no importa PySide6, PyVista, OpenSeesPy, ``fem`` ni ``persistence``.
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from veritas.core.diagnostics import Diagnostic, DiagnosticCode, InputValueError, Location
from veritas.core.references import RevisionRef, check_identity, require_ref
from veritas.core.units import Dimension, get_unit
from veritas.core.validation import invalid_type, require_real, require_text

_KEY_PATTERN: Final = re.compile(r"[a-z0-9_.]+")


class NormKind(StrEnum):
    """Origen de la configuración: ``PROTECTED`` viene en el código; ``USER`` la crea el usuario."""

    PROTECTED = "PROTECTED"
    USER = "USER"


class NormSystem(StrEnum):
    """Sistema normativo. Solo identifica; EN y SIA no tienen paquete propio todavía."""

    CTE = "CTE"
    EN = "EN"
    SIA = "SIA"
    OTHER = "OTHER"


def _bad_value(
    message_key: str, params: dict[str, str | int], location: Location
) -> InputValueError:
    return InputValueError(
        Diagnostic.create(DiagnosticCode.INPUT_OUT_OF_RANGE, message_key, params, location=location)
    )


@dataclass(frozen=True, slots=True)
class NormParameter:
    """Parámetro con valor SI finito, dimensión, unidad de presentación y fuente."""

    key: str
    value: float
    dimension: Dimension
    unit: str
    source: str
    note: str = ""

    def __post_init__(self) -> None:
        location = Location(field=f"parameter.{self.key}" if isinstance(self.key, str) else None)
        if not isinstance(self.key, str):
            raise invalid_type("key", "texto", self.key, location)
        if _KEY_PATTERN.fullmatch(self.key) is None:
            raise _bad_value("norm.invalid_key", {"key": self.key}, Location(field="key"))
        field = Location(field=f"parameter.{self.key}")
        object.__setattr__(self, "value", require_real(self.value, "value", field))
        if not isinstance(self.dimension, Dimension):
            raise invalid_type("dimension", "Dimension", self.dimension, field)
        if not isinstance(self.unit, str):
            raise invalid_type("unit", "texto", self.unit, field)
        unit = get_unit(self.unit)
        if unit.dimension != self.dimension:
            raise InputValueError(
                Diagnostic.create(
                    DiagnosticCode.UNITS_INCOMPATIBLE,
                    "units.incompatible",
                    {"left": str(self.dimension), "right": str(unit.dimension)},
                    location=field,
                )
            )
        object.__setattr__(self, "unit", unit.symbol)
        require_text(self.source, "source", field)
        if not isinstance(self.note, str):
            raise invalid_type("note", "texto", self.note, field)


@dataclass(frozen=True, slots=True)
class NormConfiguration:
    """Revisión inmutable de una configuración de normativa."""

    id: UUID
    revision: int
    name: str
    kind: NormKind
    system: NormSystem
    jurisdiction: str
    edition: str
    documents: tuple[str, ...]
    description: str
    derived_from: RevisionRef | None
    parameters: tuple[NormParameter, ...]

    def __post_init__(self) -> None:
        check_identity(self.id, self.revision, "NormConfiguration")

        def at(field: str) -> Location:
            return Location(entity_id=self.id, revision=self.revision, field=field)

        require_text(self.name, "name", at("name"))
        if not isinstance(self.kind, NormKind):
            raise invalid_type("kind", "NormKind", self.kind, at("kind"))
        if not isinstance(self.system, NormSystem):
            raise invalid_type("system", "NormSystem", self.system, at("system"))
        require_text(self.jurisdiction, "jurisdiction", at("jurisdiction"))
        require_text(self.edition, "edition", at("edition"))
        self._check_documents(at("documents"))
        if not isinstance(self.description, str):
            raise invalid_type("description", "texto", self.description, at("description"))
        if self.derived_from is not None:
            ref = require_ref(self.derived_from, "derived_from", at("derived_from"))
            if ref.entity_id == self.id:
                raise _bad_value(
                    "norm.self_derived", {"entity_id": str(self.id)}, at("derived_from")
                )
        self._check_parameters(at("parameters"))

    def _check_documents(self, location: Location) -> None:
        if not isinstance(self.documents, tuple):
            raise invalid_type("documents", "tupla de textos", self.documents, location)
        seen: set[str] = set()
        for document in self.documents:
            require_text(document, "documents", location)
            if document in seen:
                raise _bad_value("norm.duplicate_document", {"document": document}, location)
            seen.add(document)

    def _check_parameters(self, location: Location) -> None:
        if not isinstance(self.parameters, tuple):
            raise invalid_type("parameters", "tupla de NormParameter", self.parameters, location)
        seen: set[str] = set()
        for parameter in self.parameters:
            if not isinstance(parameter, NormParameter):
                raise invalid_type("parameters", "NormParameter", parameter, location)
            if parameter.key in seen:
                raise _bad_value("norm.duplicate_parameter", {"key": parameter.key}, location)
            seen.add(parameter.key)

    @property
    def ref(self) -> RevisionRef:
        """Referencia exacta (id, revisión) a esta revisión."""
        return RevisionRef(self.id, self.revision)
