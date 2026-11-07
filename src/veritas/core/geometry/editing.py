"""Edición del modelo con deshacer/rehacer (plan T-030 E1; GEO-009, TK-009, TK-011; ADR-014).

``ModelEditor`` es la ÚNICA vía por la que una interfaz debe modificar un ``StructuralModel``:
cada orden es un **comando atómico con nombre** y se puede deshacer y rehacer. Es dominio puro:
no importa Qt, PyVista ni OpenSeesPy.

Mecanismo (D2-A): cada comando guarda el ``ModelSnapshot`` anterior y el posterior (pila acotada);
deshacer y rehacer son ``StructuralModel.replace_content``, el mismo contrato que usan el guardado
y el hash, de modo que el estado restaurado es idéntico (entidades, conectividad y hash). Si un
comando falla, el modelo vuelve exactamente al estado previo (atomicidad por instantánea, aunque
el dominio hubiera mutado algo a medias). Un comando que no cambia nada no deja paso de deshacer.

El objeto ``editor.model`` es SIEMPRE el mismo (el contenido se sustituye en el sitio), de modo que
una referencia como ``Project.model`` sigue vigente tras deshacer, rehacer o un comando fallido.
Las vistas de solo lectura obtenidas antes (``model.nodes``, ``model.members``…) quedan obsoletas.

Una **transacción** agrupa varios comandos en UN paso de deshacer (``with editor.transaction(…)``);
si algo falla o se sale con excepción, no queda nada a medias. Una transacción interna que falla
restaura su propio punto de guardado aunque la exterior capture la excepción.

El editor **no es reentrante**: una acción de ``execute`` ni un aviso pueden llamar al editor
(``execute``, ``transaction``, ``undo``, ``redo``); se rechaza con ``editing.reentrant``.

Avisos (plan T-030 R2-D2, E2a): ``generation`` es un entero que sube tras cada comando que cambia
el modelo, cada deshacer, cada rehacer y cada fallo con restauración; es la clave fiable de cambio
(``model_revision`` no lo es). ``subscribe`` registra funciones sin argumentos que se llaman DESPUÉS
de terminar la operación (nunca dentro de una acción) y UNA sola vez por paso: una transacción emite
un único aviso al cerrar. Si un aviso lanza, se avisa a los demás y se propaga la primera excepción
una vez completada la operación (que ya no se revierte); en un fallo prevalece la original.
"""

from collections import deque
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    DiagnosticError,
    InputValueError,
)
from veritas.core.geometry.entities import Member, Node
from veritas.core.geometry.model import Direction, StructuralModel
from veritas.core.geometry.replacement import compatibility_errors
from veritas.core.geometry.snapshot import ModelSnapshot
from veritas.core.references import RevisionRef
from veritas.core.units import Quantity

DEFAULT_HISTORY_LIMIT: Final = 100
"""Pasos de deshacer que se conservan por defecto (los más antiguos se descartan)."""


@dataclass(frozen=True, slots=True)
class EditStep:
    """Un paso del historial: nombre del comando y estados anterior y posterior."""

    name: str
    before: ModelSnapshot
    after: ModelSnapshot


@dataclass(frozen=True, slots=True)
class CascadePreview:
    """Lo que borraría una cascada, calculado sin mutar el modelo (R2-D4).

    ``members`` va en el orden de borrado real; ``member_loads``, ``nodal_loads`` y ``settlements``
    son ids de carga; ``cleaned_exclusions`` son las barras cuya exclusión del peso propio se
    limpiará. ``error`` es el diagnóstico que rechazaría el borrado (estado final no válido), o
    ``None`` si se puede ejecutar. Vista previa y borrado comparten las mismas funciones.
    """

    members: tuple[UUID, ...] = ()
    member_loads: tuple[UUID, ...] = ()
    support: UUID | None = None
    nodal_loads: tuple[UUID, ...] = ()
    settlements: tuple[UUID, ...] = ()
    cleaned_exclusions: tuple[UUID, ...] = ()
    error: Diagnostic | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


def _error(
    key: str, *, code: DiagnosticCode = DiagnosticCode.INPUT_OUT_OF_RANGE, **params: str | int
) -> InputValueError:
    return InputValueError(Diagnostic.create(code, key, params))


def _check_name(name: object) -> None:
    if not isinstance(name, str) or not name.strip():
        raise _error("editing.invalid_name")


def _excluded_first(model: StructuralModel, member_ids: Iterable[UUID]) -> tuple[UUID, ...]:
    """Ids sin repetir, con primero las barras excluidas del peso propio.

    La regla «el peso propio no puede excluir todas las barras» se comprueba tras cada borrado:
    quitar antes las excluidas (que limpian su exclusión) hace que el orden no importe cuando el
    estado final es válido.
    """
    weight = model.self_weight
    excluded = weight.excluded_members if weight is not None else frozenset()
    return tuple(sorted(dict.fromkeys(member_ids), key=lambda member_id: member_id not in excluded))


def _cascade_members(model: StructuralModel, member_ids: Iterable[UUID]) -> tuple[Member, ...]:
    removed = []
    for member_id in _excluded_first(model, member_ids):
        for load_id in model.member_dependents(member_id):
            model.remove_load(load_id)
        removed.append(model.remove_member(member_id))
    return tuple(removed)


def _cascade_node(model: StructuralModel, node_id: UUID) -> Node:
    dependents = model.node_dependents(node_id)
    _cascade_members(model, dependents.members)
    for load_id in (*dependents.settlements, *dependents.loads):
        model.remove_load(load_id)
    if dependents.support is not None:
        model.remove_support(node_id)
    return model.remove_node(node_id)


def _dry_run(
    model: StructuralModel, action: Callable[[StructuralModel], object]
) -> tuple[StructuralModel, Diagnostic | None]:
    """Aplica ``action`` a una copia; devuelve la copia y el diagnóstico de rechazo, o ``None``."""
    copy = StructuralModel.restore(model.snapshot())
    try:
        action(copy)
    except DiagnosticError as error:
        return copy, error.diagnostic
    return copy, None


def _preview(
    model: StructuralModel,
    member_ids: tuple[UUID, ...],
    action: Callable[[StructuralModel], object],
    *,
    node_id: UUID | None = None,
) -> CascadePreview:
    members = _excluded_first(model, member_ids)
    weight = model.self_weight
    excluded = weight.excluded_members if weight is not None else frozenset()
    dependents = None if node_id is None else model.node_dependents(node_id)
    member_loads = tuple(load for m in members for load in model.member_dependents(m))
    support = None if dependents is None else dependents.support
    nodal_loads = () if dependents is None else dependents.loads
    settlements = () if dependents is None else dependents.settlements
    copy, error = _dry_run(model, action)
    if error is None:
        # Lo que realmente borró la copia manda: si el borrado gana efectos, la vista previa los
        # muestra (las listas por consulta solo ordenan y clasifican).
        gone_members = set(model.members) - set(copy.members)
        gone_loads = set(model.loads) - set(copy.loads)
        gone_supports = set(model.supports) - set(copy.supports)
        extra = tuple(sorted(gone_members - set(members), key=str))
        members = (*(m for m in members if m in gone_members), *extra)
        classified = {*nodal_loads, *settlements}
        member_loads = (
            *(load for load in member_loads if load in gone_loads),
            *sorted(gone_loads - set(member_loads) - classified, key=str),
        )
        nodal_loads = tuple(load for load in nodal_loads if load in gone_loads)
        settlements = tuple(load for load in settlements if load in gone_loads)
        support = support if support in gone_supports else None
    return CascadePreview(
        members=members,
        member_loads=tuple(member_loads),
        support=support,
        nodal_loads=nodal_loads,
        settlements=settlements,
        cleaned_exclusions=tuple(m for m in members if m in excluded),
        error=error,
    )


def _as_ids(member_ids: UUID | Iterable[UUID]) -> tuple[UUID, ...]:
    ids = (member_ids,) if isinstance(member_ids, UUID) else tuple(dict.fromkeys(member_ids))
    if not ids:
        raise _error("model.empty_selection")
    return ids


def _check_compatible(
    model: StructuralModel, material: RevisionRef, section: RevisionRef, label: str
) -> None:
    """Regla forma/material de ``replace_sections`` aplicada en el editor (R2-D5, TK-036)."""
    errors = compatibility_errors(model.section(section), model.material(material), label)
    if errors:
        raise InputValueError(errors[0])


class ModelEditor:
    """Comandos atómicos con nombre, transacciones y pilas de deshacer/rehacer."""

    def __init__(
        self, model: StructuralModel | None = None, *, limit: int = DEFAULT_HISTORY_LIMIT
    ) -> None:
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise _error("editing.invalid_limit", limit=str(limit))
        self._model = StructuralModel() if model is None else model
        self._undo: deque[EditStep] = deque(maxlen=limit)
        self._redo: list[EditStep] = []
        self._depth = 0
        self._running = False
        self._notifying = False
        self._generation = 0
        self._listeners: list[Callable[[], None]] = []

    # --- Estado ---------------------------------------------------------------------------

    @property
    def model(self) -> StructuralModel:
        """El modelo vigente: siempre el mismo objeto, cuyo contenido el editor sustituye."""
        return self._model

    @property
    def generation(self) -> int:
        """Clave de cambio: estrictamente creciente tras cada operación que cambia o restaura."""
        return self._generation

    def subscribe(self, callback: Callable[[], None]) -> None:
        """Registra ``callback`` (sin argumentos); se llama tras cada operación terminada."""
        if not callable(callback):
            raise _error("editing.invalid_callback")
        if callback not in self._listeners:
            self._listeners.append(callback)

    def unsubscribe(self, callback: Callable[[], None]) -> None:
        if callback not in self._listeners:
            raise _error("editing.not_subscribed")
        self._listeners.remove(callback)

    def _publish(self, *, raising: bool) -> None:
        self._generation += 1
        first: Exception | None = None
        self._notifying = True
        try:
            for callback in tuple(self._listeners):
                try:
                    callback()
                except Exception as exc:  # se avisa a todos y se propaga la primera
                    first = first or exc
        finally:
            self._notifying = False
        if first is not None and raising:
            raise first

    @property
    def _idle(self) -> bool:
        return self._depth == 0 and not self._running and not self._notifying

    @property
    def can_undo(self) -> bool:
        return bool(self._undo) and self._idle

    @property
    def can_redo(self) -> bool:
        return bool(self._redo) and self._idle

    @property
    def undo_names(self) -> tuple[str, ...]:
        """Nombres de los pasos que se pueden deshacer, del más antiguo al más reciente."""
        return tuple(step.name for step in self._undo)

    @property
    def redo_names(self) -> tuple[str, ...]:
        """Nombres de los pasos que se pueden rehacer, del próximo al más lejano."""
        return tuple(step.name for step in reversed(self._redo))

    # --- Comandos -------------------------------------------------------------------------

    def execute[T](self, name: str, action: Callable[[StructuralModel], T]) -> T:
        """Ejecuta ``action`` sobre el modelo como UN comando atómico llamado ``name``.

        Si lanza, el modelo vuelve al estado previo y la excepción se propaga. Si no cambia el
        modelo no se registra ningún paso. Fuera de una transacción, un comando nuevo vacía la pila
        de rehacer; dentro, el paso lo registra la transacción. ``action`` no puede llamar al
        editor.
        """
        _check_name(name)
        self._require_not_running()
        before = self._model.snapshot()
        self._running = True
        try:
            try:
                result = action(self._model)
            finally:
                self._running = False
        except BaseException:
            self._model.replace_content(before)
            if self._depth == 0:
                self._publish(raising=False)
            raise
        if self._depth == 0 and self._record(name, before, self._model.snapshot()):
            self._publish(raising=True)
        return result

    @contextmanager
    def transaction(self, name: str) -> Generator[None]:
        """Agrupa los comandos del bloque en UN paso de deshacer; todo o nada.

        Las transacciones anidadas se unen a la exterior, pero cada una restaura su propio punto
        de guardado si falla. Dentro de una no se puede deshacer ni rehacer.
        """
        _check_name(name)
        self._require_not_running()
        outermost = self._depth == 0
        before = self._model.snapshot()
        self._depth += 1
        try:
            yield
        except BaseException:
            self._depth -= 1
            self._model.replace_content(before)
            if outermost:
                self._publish(raising=False)
            raise
        self._depth -= 1
        if outermost and self._record(name, before, self._model.snapshot()):
            self._publish(raising=True)

    def _record(self, name: str, before: ModelSnapshot, after: ModelSnapshot) -> bool:
        if before == after:
            return False  # Sin cambio: no hay paso que deshacer ni aviso.
        self._undo.append(EditStep(name, before, after))
        self._redo.clear()
        return True

    # --- Deshacer y rehacer ---------------------------------------------------------------

    def undo(self) -> str:
        """Deshace el último paso y devuelve su nombre."""
        self._require_idle()
        if not self._undo:
            raise _error("editing.nothing_to_undo")
        step = self._undo[-1]
        self._model.replace_content(step.before)  # si falla, el paso sigue en la pila
        self._undo.pop()
        self._redo.append(step)
        self._publish(raising=True)
        return step.name

    def redo(self) -> str:
        """Rehace el último paso deshecho y devuelve su nombre."""
        self._require_idle()
        if not self._redo:
            raise _error("editing.nothing_to_redo")
        step = self._redo[-1]
        self._model.replace_content(step.after)
        self._redo.pop()
        self._undo.append(step)
        self._publish(raising=True)
        return step.name

    def _require_not_running(self) -> None:
        if self._running or self._notifying:
            raise _error("editing.reentrant", code=DiagnosticCode.UNSUPPORTED_CONFIGURATION)

    def _require_idle(self) -> None:
        self._require_not_running()
        if self._depth:
            raise _error("editing.in_transaction", code=DiagnosticCode.UNSUPPORTED_CONFIGURATION)

    # --- Comandos con nombre (geometría) --------------------------------------------------

    def add_node(self, x: Quantity, y: Quantity, z: Quantity, *, label: str = "") -> Node:
        return self.execute("Crear nudo", lambda m: m.add_node(x, y, z, label=label))

    def move_node(self, node_id: UUID, x: Quantity, y: Quantity, z: Quantity) -> Node:
        return self.execute("Mover nudo", lambda m: m.move_node(node_id, x, y, z))

    def relabel_node(self, node_id: UUID, label: str) -> Node:
        return self.execute("Renombrar nudo", lambda m: m.relabel_node(node_id, label))

    def remove_node(self, node_id: UUID) -> Node:
        """Borra un nudo sin dependientes; con dependientes rechaza (use el borrado en cascada)."""
        return self.execute("Borrar nudo", lambda m: m.remove_node(node_id))

    def remove_member(self, member_id: UUID) -> Member:
        """Borra una barra sin cargas de barra; con ellas rechaza (use el borrado en cascada)."""
        return self.execute("Borrar barra", lambda m: m.remove_member(member_id))

    def remove_member_with_dependents(self, member_id: UUID) -> Member:
        """Borra la barra y sus cargas de barra como UN solo paso de deshacer (D4-A)."""

        return self.execute(
            "Borrar barra con dependientes", lambda m: _cascade_members(m, (member_id,))[0]
        )

    def remove_node_with_dependents(self, node_id: UUID) -> Node:
        """Borra el nudo con sus barras (y las cargas de estas), apoyo, asientos y cargas nodales
        como UN solo paso de deshacer (D4-A). Si el estado final no es válido no cambia nada."""

        return self.execute("Borrar nudo con dependientes", lambda m: _cascade_node(m, node_id))

    def remove_members(self, member_ids: Iterable[UUID]) -> tuple[Member, ...]:
        """Borra varias barras (con sus cargas) como UN solo paso de deshacer.

        Los ids repetidos se ignoran y el resultado va en el orden de borrado (las barras
        excluidas del peso propio primero), que no depende del orden recibido.
        """

        ids = tuple(member_ids)
        return self.execute("Borrar barras", lambda m: _cascade_members(m, ids))

    # --- Vista previa de la cascada (R2-D4) ------------------------------------------------

    def preview_remove_members(self, member_ids: Iterable[UUID]) -> CascadePreview:
        """Lo que borraría ``remove_members(member_ids)``, sin mutar el modelo."""
        ids = tuple(member_ids)
        return _preview(self._model, ids, lambda m: _cascade_members(m, ids))

    def preview_remove_node(self, node_id: UUID) -> CascadePreview:
        """Lo que borraría ``remove_node_with_dependents(node_id)``, sin mutar el modelo."""
        dependents = self._model.node_dependents(node_id)
        return _preview(
            self._model,
            dependents.members,
            lambda m: _cascade_node(m, node_id),
            node_id=node_id,
        )

    # --- Comandos con nombre (barras, R2-D3) ------------------------------------------------

    def add_member(  # noqa: PLR0913 (material y sección obligatorios por nombre, GEO-003)
        self,
        node_i: UUID,
        node_j: UUID,
        *,
        material: RevisionRef,
        section: RevisionRef,
        reference: Direction | None = None,
        alpha: Quantity | None = None,
        label: str = "",
    ) -> Member:
        """Crea una barra aplicando antes la regla forma/material (R2-D5)."""

        def action(model: StructuralModel) -> Member:
            _check_compatible(model, material, section, label or "nueva barra")
            return model.add_member(
                node_i,
                node_j,
                material=material,
                section=section,
                reference=reference,
                alpha=alpha,
                label=label,
            )

        return self.execute("Crear barra", action)

    def set_member_orientation(
        self,
        member_id: UUID,
        *,
        reference: Direction | None = None,
        alpha: Quantity | None = None,
    ) -> Member:
        return self.execute(
            "Orientar barra",
            lambda m: m.set_member_orientation(member_id, reference=reference, alpha=alpha),
        )

    def assign_material(
        self, member_ids: UUID | Iterable[UUID], material: RevisionRef
    ) -> tuple[Member, ...]:
        """Asigna el material a una o varias barras en UN paso; rechaza si alguna combinación con
        su sección es inválida (R2-D5), sin dejar nada a medias."""
        ids = _as_ids(member_ids)

        def action(model: StructuralModel) -> tuple[Member, ...]:
            for member_id in ids:
                member = model.member(member_id)
                _check_compatible(model, material, member.section, member.label or str(member_id))
            return tuple(model.assign_material(member_id, material) for member_id in ids)

        return self.execute("Asignar material", action)

    def assign_section(
        self, member_ids: UUID | Iterable[UUID], section: RevisionRef
    ) -> tuple[Member, ...]:
        """Asigna la sección a una o varias barras en UN paso; rechaza si alguna combinación con
        su material es inválida (R2-D5), sin dejar nada a medias."""
        ids = _as_ids(member_ids)

        def action(model: StructuralModel) -> tuple[Member, ...]:
            for member_id in ids:
                member = model.member(member_id)
                _check_compatible(model, member.material, section, member.label or str(member_id))
            return tuple(model.assign_section(member_id, section) for member_id in ids)

        return self.execute("Asignar sección", action)
