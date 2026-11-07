"""Borrado de una selección con vista previa (plan T-030 E2b-2; R2-D4, R2-D7), sin Qt.

``plan_deletion`` calcula, sobre una COPIA del modelo, lo que borraría la selección (nudos con sus
barras, cargas, apoyos y asientos; barras con sus cargas) usando las mismas órdenes del editor que
el borrado real: ``apply_deletion`` es la única función que borra, tanto en la copia como en el
modelo, de modo que la vista previa no puede divergir. Todo va en UN paso de deshacer.
"""

from dataclasses import dataclass
from uuid import UUID

from veritas.core.diagnostics import Diagnostic, DiagnosticError
from veritas.core.geometry.editing import ModelEditor
from veritas.core.geometry.model import StructuralModel
from veritas.core.geometry.selection_logic import SelectionSet

STEP_NAME = "Borrar selección"


@dataclass(frozen=True, slots=True)
class DeletionPlan:
    """Efecto previsto del borrado; ``error`` si el estado final no sería válido."""

    nodes: tuple[UUID, ...] = ()
    members: tuple[UUID, ...] = ()
    loads: tuple[UUID, ...] = ()
    supports: tuple[UUID, ...] = ()
    error: Diagnostic | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def empty(self) -> bool:
        return not (self.nodes or self.members or self.loads or self.supports)


def apply_deletion(editor: ModelEditor, selection: SelectionSet) -> None:
    """Borra la selección en un solo paso: primero las barras, después los nudos."""
    with editor.transaction(STEP_NAME):
        members = sorted(selection.members, key=str)
        if members:
            editor.remove_members(members)
        for node_id in sorted(selection.nodes, key=str):
            if node_id in editor.model.nodes:
                editor.remove_node_with_dependents(node_id)


def plan_deletion(model: StructuralModel, selection: SelectionSet) -> DeletionPlan:
    """Lo que borraría ``apply_deletion`` con ``selection``, sin tocar ``model``."""
    pruned = selection.pruned(model)
    copy = StructuralModel.restore(model.snapshot())
    try:
        apply_deletion(ModelEditor(copy), pruned)
    except DiagnosticError as error:
        return DeletionPlan(
            nodes=tuple(sorted(pruned.nodes, key=str)),
            members=tuple(sorted(pruned.members, key=str)),
            error=error.diagnostic,
        )
    return DeletionPlan(
        nodes=tuple(sorted(set(model.nodes) - set(copy.nodes), key=str)),
        members=tuple(sorted(set(model.members) - set(copy.members), key=str)),
        loads=tuple(sorted(set(model.loads) - set(copy.loads), key=str)),
        supports=tuple(sorted(set(model.supports) - set(copy.supports), key=str)),
    )


def describe(model: StructuralModel, plan: DeletionPlan) -> str:
    """Texto en español de la vista previa (nombres de nudos y barras, y totales)."""

    def names(ids: tuple[UUID, ...], pool: dict[UUID, str]) -> str:
        return ", ".join(pool.get(i, str(i)[:8]) for i in ids)

    node_names = {n.id: n.label or str(n.id)[:8] for n in model.nodes.values()}
    member_names = {m.id: m.label or str(m.id)[:8] for m in model.members.values()}
    lines = []
    if plan.nodes:
        lines.append(f"Nudos ({len(plan.nodes)}): {names(plan.nodes, node_names)}")
    if plan.members:
        lines.append(f"Barras ({len(plan.members)}): {names(plan.members, member_names)}")
    if plan.supports:
        lines.append(f"Apoyos: {len(plan.supports)}")
    if plan.loads:
        lines.append(f"Cargas (y asientos) asociadas: {len(plan.loads)}")
    return "\n".join(lines) if lines else "No hay nada que borrar."
