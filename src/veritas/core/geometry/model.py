"""Agregado de modelo en memoria (plan T-003, D3; ADR-001 §4).

Guarda la revisión actual de cada nudo y barra. Cada transacción válida que cambia el
modelo analítico incrementa ``model_revision``; una operación rechazada no cambia nada.
Cambiar solo la etiqueta crea una revisión de la entidad pero no altera ``model_revision``
(dato de presentación). Sin borrado, deshacer ni persistencia.
"""

from collections.abc import Sequence
from dataclasses import replace
from types import MappingProxyType
from typing import Literal
from uuid import UUID

from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    InputTypeError,
    InputValueError,
    Location,
)
from veritas.core.geometry.entities import Member, Node, new_entity_id
from veritas.core.geometry.local_axes import (
    LocalAxes,
    compute_local_axes,
    recompute_local_axes,
    validate_alpha,
)
from veritas.core.geometry.vector import Vec3
from veritas.core.units import ANGLE, Quantity

type Direction = Vec3 | Sequence[object]


def _not_found(kind: Literal["nudo", "barra"], entity_id: object) -> InputValueError:
    return InputValueError(
        Diagnostic.create(
            DiagnosticCode.REFERENCE_NOT_FOUND,
            "model.reference_not_found",
            {"kind": kind, "entity_id": str(entity_id)},
        )
    )


def _alpha_rad(alpha: Quantity | None, location: Location) -> float:
    if alpha is None:
        return 0.0
    if not isinstance(alpha, Quantity):
        raise InputTypeError(
            Diagnostic.create(
                DiagnosticCode.INPUT_INVALID_TYPE,
                "input.invalid_type",
                {"field": "alfa", "expected": "Quantity", "received": type(alpha).__name__},
                location=location,
            )
        )
    return validate_alpha(alpha.require(ANGLE).si_value, location)


class StructuralModel:
    """Nudos y barras con identidad UUID y contador ``model_revision``."""

    def __init__(self) -> None:
        self._nodes: dict[UUID, Node] = {}
        self._members: dict[UUID, Member] = {}
        self._model_revision = 0

    @property
    def model_revision(self) -> int:
        """Contador de transacciones válidas que cambian el modelo analítico."""
        return self._model_revision

    @property
    def nodes(self) -> MappingProxyType[UUID, Node]:
        """Revisión actual de cada nudo (vista de solo lectura)."""
        return MappingProxyType(self._nodes)

    @property
    def members(self) -> MappingProxyType[UUID, Member]:
        """Revisión actual de cada barra (vista de solo lectura)."""
        return MappingProxyType(self._members)

    def node(self, node_id: UUID) -> Node:
        """Revisión actual de un nudo o ``REFERENCE_NOT_FOUND``."""
        try:
            return self._nodes[node_id]
        except (KeyError, TypeError):
            raise _not_found("nudo", node_id) from None

    def member(self, member_id: UUID) -> Member:
        """Revisión actual de una barra o ``REFERENCE_NOT_FOUND``."""
        try:
            return self._members[member_id]
        except (KeyError, TypeError):
            raise _not_found("barra", member_id) from None

    def axes(self, member_id: UUID) -> LocalAxes:
        """Base local actual de una barra con su orientación guardada."""
        member = self.member(member_id)
        return recompute_local_axes(
            self._nodes[member.node_i].position,
            self._nodes[member.node_j].position,
            member.orientation,
            location=Location(entity_id=member.id, revision=member.revision),
        )

    def add_node(self, x: Quantity, y: Quantity, z: Quantity, *, label: str = "") -> Node:
        """Crea un nudo (revisión 1) en la posición global dada."""
        node = Node(new_entity_id(), 1, label, Vec3.from_lengths(x, y, z))
        self._nodes[node.id] = node
        self._model_revision += 1
        return node

    def move_node(self, node_id: UUID, x: Quantity, y: Quantity, z: Quantity) -> Node:
        """Mueve un nudo; recalcula la base de sus barras con la referencia guardada.

        Si alguna barra degenera (longitud o referencia), se rechaza todo sin cambios.
        Mover a la misma posición no es un cambio: devuelve la revisión actual.
        """
        current = self.node(node_id)
        position = Vec3.from_lengths(x, y, z)
        if position == current.position:
            return current
        moved = replace(current, revision=current.revision + 1, position=position)

        def resolved(uid: UUID) -> Vec3:
            return position if uid == node_id else self._nodes[uid].position

        for member in self._members.values():
            if node_id in (member.node_i, member.node_j):
                recompute_local_axes(
                    resolved(member.node_i),
                    resolved(member.node_j),
                    member.orientation,
                    location=Location(entity_id=member.id, revision=member.revision),
                )
        self._nodes[node_id] = moved
        self._model_revision += 1
        return moved

    def relabel_node(self, node_id: UUID, label: str) -> Node:
        """Nueva revisión con otra etiqueta; no altera ``model_revision``."""
        current = self.node(node_id)
        if not isinstance(label, str):
            raise TypeError("label debe ser texto")
        if label == current.label:
            return current
        relabeled = replace(current, revision=current.revision + 1, label=label)
        self._nodes[node_id] = relabeled
        return relabeled

    def add_member(
        self,
        node_i: UUID,
        node_j: UUID,
        *,
        reference: Direction | None = None,
        alpha: Quantity | None = None,
        label: str = "",
    ) -> Member:
        """Crea una barra I -> J con extremos rígidos (CON-001).

        ``reference`` es la dirección local +z (adimensional); sin ella se usa +Z con
        fallback -X para barras verticales. Se guarda la referencia efectiva resuelta.
        """
        member_id = new_entity_id()
        location = Location(entity_id=member_id, revision=1)
        start, end = self.node(node_i), self.node(node_j)
        if node_i == node_j:
            raise InputValueError(
                Diagnostic.create(
                    DiagnosticCode.GEOMETRY_ZERO_LENGTH,
                    "geometry.same_node",
                    {"node": str(node_i)},
                    location=Location(entity_id=member_id, revision=1, field="node_j"),
                )
            )
        axes = compute_local_axes(
            start.position,
            end.position,
            reference=None if reference is None else Vec3.direction(reference),
            alpha=_alpha_rad(alpha, location),
            location=location,
        )
        member = Member(member_id, 1, label, node_i, node_j, axes.orientation)
        self._members[member.id] = member
        self._model_revision += 1
        return member

    def set_member_orientation(
        self,
        member_id: UUID,
        *,
        reference: Direction | None = None,
        alpha: Quantity | None = None,
    ) -> Member:
        """Reorientación explícita: nueva revisión de barra (ADR-001 §1, paso 5).

        Sin ``reference`` se vuelve a resolver la referencia por defecto (+Z, fallback -X)
        para la geometría actual. Sin ``alpha`` se conserva el giro actual. Si la orientación
        resultante es idéntica a la guardada, no hay cambio y se devuelve la revisión actual.
        """
        current = self.member(member_id)
        location = Location(entity_id=current.id, revision=current.revision + 1)
        axes = compute_local_axes(
            self._nodes[current.node_i].position,
            self._nodes[current.node_j].position,
            reference=None if reference is None else Vec3.direction(reference),
            alpha=current.orientation.alpha if alpha is None else _alpha_rad(alpha, location),
            location=location,
        )
        if axes.orientation == current.orientation:
            return current
        updated = replace(current, revision=current.revision + 1, orientation=axes.orientation)
        self._members[member_id] = updated
        self._model_revision += 1
        return updated
