"""Gestos de modelado sin Qt (plan T-030 E3b-1; R3-D4).

``GestureMachine`` es una máquina de estados pura: recibe lo capturado bajo el cursor
(``Capture``: punto y, si es un nudo existente, su id) y devuelve una ACCIÓN descriptiva
(``CreateNode``, ``CreateMember``, ``MoveNode``) cuando el gesto termina. No escribe nada: el
modelo solo cambia en ``apply``, que ejecuta la acción en UNA transacción del ``ModelEditor``
(un gesto = un paso de deshacer). Cancelar (Esc) o cambiar de modo descarta el gesto sin huella.

- *Nudo*: un clic crea un nudo en el punto capturado (sobre un nudo existente no hace nada).
- *Barra*: clic en I y clic en J (el segundo, sobre un punto sin nudo, crea el nudo); la barra
  une nudos reutilizados si el punto coincide con uno existente (``capture.coincident_node``).
- *Seleccionar*: arrastrar un nudo YA seleccionado y soltar lo mueve (un ``move_node``); un
  desplazamiento ≤ ``CLICK_TOLERANCE`` es un clic y no mueve nada.

La entrada numérica (``type_entry``) fija el punto del gesto por teclado (``capture.parse_entry``).
Unidades SI (m). Sin Qt, PyVista ni numpy.
"""

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final
from uuid import UUID

from veritas.core import units as u
from veritas.core.diagnostics import Diagnostic, DiagnosticCode, InputValueError
from veritas.core.geometry.editing import ModelEditor
from veritas.core.geometry.entities import Member, Node
from veritas.core.geometry.local_axes import MIN_MEMBER_LENGTH
from veritas.core.references import RevisionRef
from veritas.gui import capture, forms
from veritas.gui.capture import Dimensions, Point, SnapResult

CLICK_PIXELS: Final = 4.0
"""Desplazamiento máximo (píxeles) pulsar→soltar para que cuente como clic (como el visor)."""

GESTURE_TOLERANCE: Final = MIN_MEMBER_LENGTH
"""Dos puntos a menos de esta distancia (m) son el mismo para un gesto: es la longitud mínima de
barra del dominio, así que el rechazo llega antes de consumir el primer punto."""

type Pixel = tuple[float, float]


class Mode(StrEnum):
    """Herramienta activa de la barra de herramientas."""

    SELECT = "SELECT"
    NODE = "NODE"
    MEMBER = "MEMBER"


class GestureState(StrEnum):
    IDLE = "IDLE"
    ANCHORED = "ANCHORED"  # Barra: primer punto fijado
    DRAGGING = "DRAGGING"  # Seleccionar: nudo agarrado


@dataclass(frozen=True, slots=True)
class Capture:
    """Punto capturado; ``node_id`` si es un nudo existente."""

    point: Point
    node_id: UUID | None = None


def capture_from_snap(result: SnapResult, node_ids: Sequence[UUID]) -> Capture:
    """Convierte un ``SnapResult`` (índices de la escena) en ``Capture`` (ids del dominio)."""
    node_id = node_ids[result.node] if result.node is not None else None
    return Capture(result.point, node_id)


@dataclass(frozen=True, slots=True)
class CreateNode:
    point: Point


@dataclass(frozen=True, slots=True)
class CreateMember:
    start: Capture
    end: Capture


@dataclass(frozen=True, slots=True)
class MoveNode:
    node_id: UUID
    point: Point


type Action = CreateNode | CreateMember | MoveNode


@dataclass(frozen=True, slots=True)
class Preview:
    """Vista previa elástica: no toca el modelo."""

    start: Point
    end: Point
    dimensions: Dimensions
    moving: UUID | None = None  # nudo que se arrastra (None = barra en creación)


class GestureMachine:
    """Estado del gesto en curso; ver el docstring del módulo."""

    def __init__(self, mode: Mode = Mode.SELECT) -> None:
        self._mode = mode
        self._state = GestureState.IDLE
        self._anchor: Capture | None = None
        self._cursor: Capture | None = None
        self._grab: UUID | None = None
        self._grab_origin: Point | None = None
        self._press: Pixel | None = None
        self._ignore_release = False

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def state(self) -> GestureState:
        return self._state

    @property
    def anchor(self) -> Capture | None:
        """Primer punto de la barra en creación."""
        return self._anchor

    def set_mode(self, mode: Mode) -> None:
        """Cambia de herramienta; un gesto a medias se descarta sin huella."""
        self.cancel()
        self._mode = mode

    def cancel(self) -> bool:
        """Esc: descarta el gesto en curso. ``True`` si había algo que descartar."""
        had = self._state is not GestureState.IDLE or self._press is not None
        self._abandon_press()
        self._state = GestureState.IDLE
        self._anchor = self._cursor = self._grab = self._grab_origin = None
        return had

    def _abandon_press(self) -> None:
        """El botón sigue pulsado pero el gesto ya no cuenta: su ``release`` se ignora."""
        if self._press is not None:
            self._ignore_release = True
        self._press = None

    # --- Ratón ---------------------------------------------------------------------------

    def press(
        self,
        pixel: Pixel,
        hit: Capture | None,
        *,
        selected_nodes: Collection[UUID] = (),
    ) -> None:
        """Pulsar el botón. En Seleccionar, sobre un nudo seleccionado, agarra el nudo."""
        if self._state is GestureState.DRAGGING:  # arrastre caducado (release perdido)
            self._state = GestureState.IDLE
            self._grab = self._grab_origin = None
        self._ignore_release = False
        self._press = pixel
        self._cursor = hit
        if (
            self._mode is Mode.SELECT
            and hit is not None
            and hit.node_id is not None
            and hit.node_id in selected_nodes
        ):
            self._state = GestureState.DRAGGING
            self._grab = hit.node_id
            self._grab_origin = hit.point

    def move(self, hit: Capture | None) -> None:
        """Mueve el cursor (con o sin botón pulsado). ``None`` = fuera del plano."""
        self._cursor = hit

    def release(self, pixel: Pixel, hit: Capture | None) -> Action | None:
        """Soltar el botón. Devuelve la acción a aplicar si el gesto terminó."""
        press, self._press = self._press, None
        self._cursor = hit
        if press is None:  # sin pulsación vigente (Esc, cambio de modo, teclado o suelto suelto)
            self._ignore_release = False
            return None
        clicked = (
            abs(pixel[0] - press[0]) <= CLICK_PIXELS and abs(pixel[1] - press[1]) <= CLICK_PIXELS
        )
        if self._mode is Mode.SELECT:
            return self._finish_drag(hit, clicked=clicked)
        if not clicked or hit is None:
            return None  # un arrastre en modo Nudo/Barra es orbitar la cámara, no un gesto
        if self._mode is Mode.NODE:
            return None if hit.node_id is not None else CreateNode(hit.point)
        return self._member_click(hit)

    def _finish_drag(self, hit: Capture | None, *, clicked: bool) -> Action | None:
        grab, self._grab, self._grab_origin = self._grab, None, None
        if self._state is GestureState.DRAGGING:
            self._state = GestureState.IDLE
        if grab is None or clicked or hit is None:
            return None
        return MoveNode(grab, hit.point)

    def _member_click(self, hit: Capture) -> Action | None:
        if self._state is not GestureState.ANCHORED or self._anchor is None:
            self._anchor, self._state = hit, GestureState.ANCHORED
            return None
        start = self._anchor
        if _same(start, hit):
            return None  # mismo punto: se sigue esperando el segundo
        self._anchor, self._state = None, GestureState.IDLE
        return CreateMember(start, hit)

    # --- Teclado -------------------------------------------------------------------------

    def type_entry(self, text: str) -> Action | None:
        """Punto tecleado durante el gesto; ``InputValueError`` si no se entiende."""
        origin = self._anchor.point if self._anchor is not None else self._grab_origin
        cursor = self._cursor.point if self._cursor is not None else None
        point = capture.parse_entry(text, anchor=origin, cursor=cursor)
        typed = Capture(point)
        self._abandon_press()  # el botón físico aún pulsado no debe crear nada al soltarse
        if self._state is GestureState.DRAGGING and self._grab is not None:
            action: Action = MoveNode(self._grab, point)
            self.cancel()
            return action
        if self._mode is Mode.NODE:
            return CreateNode(point)
        if self._mode is Mode.MEMBER:
            return self._member_click(typed)
        return None

    # --- Vista previa --------------------------------------------------------------------

    def preview(self) -> Preview | None:
        """Goma elástica y cotas del gesto en curso (``None`` si no hay)."""
        if self._cursor is None:
            return None
        if self._state is GestureState.ANCHORED and self._anchor is not None:
            start = self._anchor.point
            return Preview(start, self._cursor.point, capture.dimensions(start, self._cursor.point))
        if self._state is GestureState.DRAGGING and self._grab_origin is not None:
            start = self._grab_origin
            return Preview(
                start,
                self._cursor.point,
                capture.dimensions(start, self._cursor.point),
                moving=self._grab,
            )
        return None


def _same(a: Capture, b: Capture) -> bool:
    if a.node_id is not None and a.node_id == b.node_id:
        return True
    return capture.coincident_node(a.point, [b.point], GESTURE_TOLERANCE) is not None


# --- Aplicar al modelo ---------------------------------------------------------------------


def _meters(point: Point) -> tuple[u.Quantity, u.Quantity, u.Quantity]:
    return (
        u.Quantity.of(point[0], "m"),
        u.Quantity.of(point[1], "m"),
        u.Quantity.of(point[2], "m"),
    )


def _resolve(editor: ModelEditor, end: Capture) -> UUID:
    """Nudo existente (por id o por coincidencia ≤ 1e-9 m) o uno nuevo en ``end.point``."""
    if end.node_id is not None:
        return end.node_id
    nodes = editor.model.nodes
    ids = list(nodes)
    found = capture.coincident_node(
        end.point, [nodes[i].position.components() for i in ids], GESTURE_TOLERANCE
    )
    if found is not None:
        return ids[found]
    x, y, z = _meters(end.point)
    return editor.add_node(x, y, z).id


def _reject_duplicate(editor: ModelEditor, point: Point, *, ignore: UUID | None) -> None:
    """El dominio no rechaza nudos coincidentes: el gesto sí (R3-D4, revisión de E3b-1)."""
    nodes = {i: n for i, n in editor.model.nodes.items() if i != ignore}
    ids = list(nodes)
    found = capture.coincident_node(
        point, [nodes[i].position.components() for i in ids], GESTURE_TOLERANCE
    )
    if found is not None:
        label = nodes[ids[found]].label or str(ids[found])
        raise InputValueError(
            Diagnostic.create(
                DiagnosticCode.GEOMETRY_ZERO_LENGTH, "gestures.duplicate_node", {"node": label}
            )
        )


def apply(
    action: Action,
    editor: ModelEditor,
    *,
    material: RevisionRef | None = None,
    section: RevisionRef | None = None,
) -> Node | Member:
    """Ejecuta ``action`` en UNA transacción (un paso de deshacer; todo o nada).

    ``CreateMember`` exige ``material`` y ``section``; si los extremos son el mismo nudo se
    rechaza con diagnóstico y no queda ningún nudo nuevo.
    """
    if isinstance(action, CreateNode):
        _reject_duplicate(editor, action.point, ignore=None)
        x, y, z = _meters(action.point)
        return editor.add_node(x, y, z)
    if isinstance(action, MoveNode):
        _reject_duplicate(editor, action.point, ignore=action.node_id)
        x, y, z = _meters(action.point)
        return editor.move_node(action.node_id, x, y, z)
    mat = forms.chosen(material, "un material", "Material")
    sec = forms.chosen(section, "una sección", "Sección")
    with editor.transaction("Crear barra"):
        first = _resolve(editor, action.start)
        second = _resolve(editor, action.end)
        if first == second:
            raise InputValueError(
                Diagnostic.create(
                    DiagnosticCode.GEOMETRY_ZERO_LENGTH, "forms.same_nodes", {"field": "Nudo J"}
                )
            )
        return editor.add_member(first, second, material=mat, section=sec)
