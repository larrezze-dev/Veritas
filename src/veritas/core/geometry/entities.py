"""Revisiones inmutables de nudo y barra (ADR-001 §3; plan T-003, D2).

Identidad: UUID v4 inmutable; etiqueta editable e independiente. Una revisión es un
contenido inmutable; editar crea otra revisión con el mismo ``id``. La barra referencia
sus nudos por identidad lógica (``node_id``): su geometría depende del estado del modelo,
no solo de la revisión de barra (relevante para el hash de resultados, T-008/T-012).
"""

from dataclasses import dataclass, field
from uuid import UUID, uuid4

from veritas.core.connections.end import EndConnection
from veritas.core.diagnostics import Diagnostic, DiagnosticCode, InputValueError, Location
from veritas.core.geometry.local_axes import Orientation
from veritas.core.geometry.vector import Vec3

UUID_VERSION = 4


def new_entity_id() -> UUID:
    """Nuevo identificador de entidad (UUID v4, biblioteca estándar)."""
    return uuid4()


def _check_identity(entity_id: UUID, revision: int, label: str, kind: str) -> None:
    if not isinstance(entity_id, UUID) or entity_id.version != UUID_VERSION:
        raise TypeError(f"{kind}.id debe ser un UUID v4")
    if isinstance(revision, bool) or not isinstance(revision, int):
        raise TypeError(f"{kind}.revision debe ser un entero")
    if revision < 1:
        raise ValueError(f"{kind}.revision debe ser >= 1")
    if not isinstance(label, str):
        raise TypeError(f"{kind}.label debe ser texto")


@dataclass(frozen=True, slots=True)
class Node:
    """Revisión de nudo: posición en m en la base global."""

    id: UUID
    revision: int
    label: str
    position: Vec3

    def __post_init__(self) -> None:
        _check_identity(self.id, self.revision, self.label, "Node")
        if not isinstance(self.position, Vec3):
            raise TypeError("Node.position debe ser Vec3")


@dataclass(frozen=True, slots=True)
class Member:
    """Revisión de barra dirigida I -> J con orientación guardada y extremos I/J."""

    id: UUID
    revision: int
    label: str
    node_i: UUID
    node_j: UUID
    orientation: Orientation
    end_i: EndConnection = field(default_factory=EndConnection.rigid)
    end_j: EndConnection = field(default_factory=EndConnection.rigid)

    def __post_init__(self) -> None:
        _check_identity(self.id, self.revision, self.label, "Member")
        if not isinstance(self.node_i, UUID) or not isinstance(self.node_j, UUID):
            raise TypeError("Member.node_i y Member.node_j deben ser UUID")
        if self.node_i == self.node_j:
            raise InputValueError(
                Diagnostic.create(
                    DiagnosticCode.GEOMETRY_ZERO_LENGTH,
                    "geometry.same_node",
                    {"node": str(self.node_i)},
                    location=Location(entity_id=self.id, revision=self.revision, field="node_j"),
                )
            )
        if not isinstance(self.orientation, Orientation):
            raise TypeError("Member.orientation debe ser Orientation")
        if not isinstance(self.end_i, EndConnection) or not isinstance(self.end_j, EndConnection):
            raise TypeError("Member.end_i y Member.end_j deben ser EndConnection")
