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

El editor **no es reentrante**: una acción de ``execute`` no puede llamar al editor (``execute``,
``transaction``, ``undo``, ``redo``); se rechaza con ``editing.reentrant``.
"""

from collections import deque
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from veritas.core.diagnostics import Diagnostic, DiagnosticCode, InputValueError
from veritas.core.geometry.entities import Member, Node
from veritas.core.geometry.model import StructuralModel
from veritas.core.geometry.snapshot import ModelSnapshot
from veritas.core.units import Quantity

DEFAULT_HISTORY_LIMIT: Final = 100
"""Pasos de deshacer que se conservan por defecto (los más antiguos se descartan)."""


@dataclass(frozen=True, slots=True)
class EditStep:
    """Un paso del historial: nombre del comando y estados anterior y posterior."""

    name: str
    before: ModelSnapshot
    after: ModelSnapshot


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

    # --- Estado ---------------------------------------------------------------------------

    @property
    def model(self) -> StructuralModel:
        """El modelo vigente: siempre el mismo objeto, cuyo contenido el editor sustituye."""
        return self._model

    @property
    def can_undo(self) -> bool:
        return bool(self._undo) and self._depth == 0 and not self._running

    @property
    def can_redo(self) -> bool:
        return bool(self._redo) and self._depth == 0 and not self._running

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
            raise
        if self._depth == 0:
            self._record(name, before, self._model.snapshot())
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
            raise
        self._depth -= 1
        if outermost:
            self._record(name, before, self._model.snapshot())

    def _record(self, name: str, before: ModelSnapshot, after: ModelSnapshot) -> None:
        if before == after:
            return  # Sin cambio: no hay paso que deshacer.
        self._undo.append(EditStep(name, before, after))
        self._redo.clear()

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
        return step.name

    def _require_not_running(self) -> None:
        if self._running:
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

        def cascade(model: StructuralModel) -> Member:
            for load_id in model.member_dependents(member_id):
                model.remove_load(load_id)
            return model.remove_member(member_id)

        return self.execute("Borrar barra con dependientes", cascade)

    def remove_node_with_dependents(self, node_id: UUID) -> Node:
        """Borra el nudo con sus barras (y las cargas de estas), apoyo, asientos y cargas nodales
        como UN solo paso de deshacer (D4-A). Si el estado final no es válido no cambia nada."""

        def cascade(model: StructuralModel) -> Node:
            dependents = model.node_dependents(node_id)
            for member_id in _excluded_first(model, dependents.members):
                for load_id in model.member_dependents(member_id):
                    model.remove_load(load_id)
                model.remove_member(member_id)
            for load_id in (*dependents.settlements, *dependents.loads):
                model.remove_load(load_id)
            if dependents.support is not None:
                model.remove_support(node_id)
            return model.remove_node(node_id)

        return self.execute("Borrar nudo con dependientes", cascade)

    def remove_members(self, member_ids: Iterable[UUID]) -> tuple[Member, ...]:
        """Borra varias barras (con sus cargas) como UN solo paso de deshacer.

        Los ids repetidos se ignoran y el resultado va en el orden de borrado (las barras
        excluidas del peso propio primero), que no depende del orden recibido.
        """

        def cascade(model: StructuralModel) -> tuple[Member, ...]:
            removed = []
            for member_id in _excluded_first(model, member_ids):
                for load_id in model.member_dependents(member_id):
                    model.remove_load(load_id)
                removed.append(model.remove_member(member_id))
            return tuple(removed)

        return self.execute("Borrar barras", cascade)
