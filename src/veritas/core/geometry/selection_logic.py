"""Selección múltiple sin Qt (plan T-030 R2-D6, E2a; extiende la selección simple de T-011).

``SelectionSet`` es un conjunto inmutable de nudos y barras por identidad estable (UUID). Nunca es
fuente de verdad: tras cada aviso del editor se poda contra el modelo vigente (``pruned``). Las
selecciones por atributos son funciones puras de solo lectura sobre el modelo. Dominio puro: sin
Qt, PyVista ni OpenSeesPy; ``gui/selection.py`` reexporta estos nombres.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from veritas.core.geometry.model import StructuralModel
from veritas.core.references import RevisionRef


class SelectionKind(StrEnum):
    """Tipo de entidad seleccionable."""

    NODE = "NODE"
    MEMBER = "MEMBER"


@dataclass(frozen=True, slots=True)
class Selection:
    """Entidad seleccionada: tipo e identidad."""

    kind: SelectionKind
    id: UUID


@dataclass(frozen=True, slots=True)
class SelectionSet:
    """Nudos y barras seleccionados (inmutable); cada operación devuelve un conjunto nuevo."""

    nodes: frozenset[UUID] = frozenset()
    members: frozenset[UUID] = frozenset()

    def __len__(self) -> int:
        return len(self.nodes) + len(self.members)

    def __bool__(self) -> bool:
        return bool(self.nodes or self.members)

    def __contains__(self, item: object) -> bool:
        if not isinstance(item, Selection):
            return False
        return item.id in (self.nodes if item.kind is SelectionKind.NODE else self.members)

    @classmethod
    def of(cls, items: Iterable[Selection]) -> "SelectionSet":
        return cls().add(*items)

    @classmethod
    def from_selection(cls, selection: Selection | None) -> "SelectionSet":
        """Compatibilidad con la selección simple: ``None`` es el conjunto vacío."""
        return cls() if selection is None else cls.of((selection,))

    @property
    def single(self) -> Selection | None:
        """La entidad si hay exactamente una; en otro caso ``None``."""
        if len(self) != 1:
            return None
        if self.nodes:
            return Selection(SelectionKind.NODE, next(iter(self.nodes)))
        return Selection(SelectionKind.MEMBER, next(iter(self.members)))

    def items(self) -> tuple[Selection, ...]:
        """Todas las entidades, nudos primero (el orden dentro de cada tipo no está definido)."""
        return (
            *(Selection(SelectionKind.NODE, i) for i in self.nodes),
            *(Selection(SelectionKind.MEMBER, i) for i in self.members),
        )

    def replace(self, *items: Selection) -> "SelectionSet":
        return SelectionSet().add(*items)

    def add(self, *items: Selection) -> "SelectionSet":
        nodes, members = set(self.nodes), set(self.members)
        for item in items:
            (nodes if item.kind is SelectionKind.NODE else members).add(item.id)
        return SelectionSet(frozenset(nodes), frozenset(members))

    def remove(self, *items: Selection) -> "SelectionSet":
        nodes, members = set(self.nodes), set(self.members)
        for item in items:
            (nodes if item.kind is SelectionKind.NODE else members).discard(item.id)
        return SelectionSet(frozenset(nodes), frozenset(members))

    def toggle(self, *items: Selection) -> "SelectionSet":
        """Alterna cada entidad (Ctrl+clic); una repetida en ``items`` se alterna una sola vez."""
        nodes, members = set(self.nodes), set(self.members)
        for item in dict.fromkeys(items):
            target = nodes if item.kind is SelectionKind.NODE else members
            target.symmetric_difference_update({item.id})
        return SelectionSet(frozenset(nodes), frozenset(members))

    def pruned(self, model: StructuralModel) -> "SelectionSet":
        """Conserva solo lo que sigue existiendo en ``model``."""
        return SelectionSet(
            frozenset(i for i in self.nodes if i in model.nodes),
            frozenset(i for i in self.members if i in model.members),
        )


def members_by_section(model: StructuralModel, section: RevisionRef) -> SelectionSet:
    """Barras con exactamente esa revisión de sección."""
    return SelectionSet(
        members=frozenset(m.id for m in model.members.values() if m.section == section)
    )


def members_by_material(model: StructuralModel, material: RevisionRef) -> SelectionSet:
    """Barras con exactamente esa revisión de material."""
    return SelectionSet(
        members=frozenset(m.id for m in model.members.values() if m.material == material)
    )


def members_at_node(model: StructuralModel, node_id: UUID) -> SelectionSet:
    """Barras que tienen ese nudo como extremo; nudo inexistente: ``REFERENCE_NOT_FOUND``."""
    model.node(node_id)
    return SelectionSet(
        members=frozenset(m.id for m in model.members.values() if node_id in (m.node_i, m.node_j))
    )
