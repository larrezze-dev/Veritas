"""Visor 3D PyVista dentro de un widget Qt (plan T-011, E2; ADR-008).

Dibuja una ``Scene`` (nudos, barras, apoyos y, de la barra seleccionada, sus ejes locales x/y/z
rojo/verde/azul) y resuelve la selección (múltiple con Ctrl o Mayús) por clic. No
modifica el dominio ni conserva geometría propia; ``set_scene`` redibuja desde la escena.
Requiere el extra opcional ``gui``; no debe importarse sin él.
"""

from typing import Any, Final

import numpy as np
import pyvista as pv
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QVBoxLayout, QWidget
from pyvistaqt import QtInteractor

from veritas.gui.picking import Pixel, pick_nearest
from veritas.gui.scene import Point, Scene, SceneMember
from veritas.gui.selection import Selection, SelectionKind, SelectionSet
from veritas.gui.tables import entity_name, is_fully_fixed

CLICK_TOLERANCE: Final = 4.0
"""Desplazamiento máximo (píxeles) entre pulsar y soltar para que cuente como clic y no arrastre."""

COLOR_MEMBERS: Final = "#3f6fb5"
COLOR_NODES: Final = "#222222"
COLOR_SUPPORTS: Final = "#2e8b57"
COLOR_SELECTED: Final = "#ff8c00"
AXIS_COLORS: Final = (("x", "#d62728"), ("y", "#2ca02c"), ("z", "#1f77b4"))
"""Ejes locales de la barra seleccionada: x rojo, y verde, z azul."""

_SCENE_ACTORS: Final = ("members", "nodes", "supports", "node-labels", "member-labels")


class Viewport(QWidget):
    """Widget con el visor PyVista; emite ``selection_changed`` (``Selection`` o ``None``)."""

    selection_changed = Signal(object)
    selection_set_changed = Signal(object)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._plotter: Any = QtInteractor(self)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._plotter.interactor)
        self._scene: Scene | None = None
        self._selection: SelectionSet = SelectionSet()
        self._overlay: list[str] = []
        self._press: Pixel | None = None
        self._plotter.set_background("white")
        self._plotter.add_axes()
        self._plotter.iren.add_observer("LeftButtonPressEvent", self._on_press)
        self._plotter.iren.add_observer("LeftButtonReleaseEvent", self._on_release)

    # ------------------------------------------------------------------ escena

    @property
    def selection(self) -> Selection | None:
        """La entidad seleccionada si es exactamente una; si hay varias o ninguna, ``None``."""
        return self._selection.single

    @property
    def selection_set(self) -> SelectionSet:
        """Selección múltiple actual (la última fijada por ``select_set`` o por un clic)."""
        return self._selection

    def set_scene(self, scene: Scene, *, reset_camera: bool = True) -> None:
        """Redibuja desde ``scene``; conserva lo seleccionado que sigue existiendo.

        ``reset_camera=False`` mantiene el encuadre (ediciones); abrir un proyecto lo reencuadra.
        """
        plotter = self._plotter
        for name in (*_SCENE_ACTORS, *self._overlay):
            plotter.remove_actor(name, render=False)
        self._overlay = []
        self._scene = scene
        if scene.nodes:
            self._draw_scene(scene)
        self._selection = self._existing(self._selection)
        self._draw_selection()
        if reset_camera:
            plotter.view_isometric()
            plotter.reset_camera()
        plotter.render()

    def _draw_scene(self, scene: Scene) -> None:
        plotter = self._plotter
        points = np.array(scene.points, dtype=float)
        if scene.members:
            members = pv.PolyData(points)
            lines = np.array([[2, m.node_i, m.node_j] for m in scene.members], dtype=np.int64)
            members.lines = lines.ravel()
            plotter.add_mesh(
                members, color=COLOR_MEMBERS, line_width=4, name="members", pickable=False
            )
        plotter.add_mesh(
            pv.PolyData(points),
            color=COLOR_NODES,
            point_size=10,
            render_points_as_spheres=True,
            name="nodes",
            pickable=False,
        )
        plotter.add_point_labels(
            points,
            [entity_name(n.label, n.id) for n in scene.nodes],
            font_size=12,
            text_color=COLOR_NODES,
            shape=None,
            show_points=False,
            always_visible=True,
            name="node-labels",
            pickable=False,
        )
        if scene.members:
            middles = np.array([m.midpoint for m in scene.members], dtype=float)
            plotter.add_point_labels(
                middles,
                [entity_name(m.label, m.id) for m in scene.members],
                font_size=12,
                text_color=COLOR_MEMBERS,
                shape=None,
                show_points=False,
                always_visible=True,
                name="member-labels",
                pickable=False,
            )
        glyphs = self._support_glyphs(scene)
        if glyphs:
            plotter.add_mesh(
                pv.merge(glyphs), color=COLOR_SUPPORTS, name="supports", pickable=False
            )

    @staticmethod
    def _support_glyphs(scene: Scene) -> list[pv.PolyData]:
        """Cubo si es empotramiento, cono bajo el nudo si fija traslaciones, esfera si no."""
        size = 0.5 * scene.glyph_length
        down = np.array([0.0, 0.0, -size / 2])
        glyphs: list[pv.PolyData] = []
        for support in scene.supports:
            position = np.array(scene.nodes[support.node].position, dtype=float)
            if is_fully_fixed(support):
                glyphs.append(
                    pv.Cube(center=position + down, x_length=size, y_length=size, z_length=size)
                )
            elif support.translations_fixed > 0:
                glyphs.append(
                    pv.Cone(
                        center=position + down,
                        direction=(0.0, 0.0, 1.0),
                        height=size,
                        radius=0.45 * size,
                        resolution=24,
                    )
                )
            else:
                glyphs.append(pv.Sphere(radius=0.3 * size, center=position))
        return glyphs

    # --------------------------------------------------------------- selección

    def select(self, selection: Selection | None) -> None:
        """Fija una selección de una sola entidad y la resalta. No emite señales."""
        self.select_set(SelectionSet.from_selection(selection))

    def select_set(self, selection: SelectionSet) -> None:
        """Fija la selección múltiple (lo que no existe en la escena se descarta) y la resalta."""
        self._selection = self._existing(selection)
        self._draw_selection()
        self._plotter.render()

    def _existing(self, selection: SelectionSet) -> SelectionSet:
        return SelectionSet.of(i for i in selection.items() if self._resolve(i) is not None)

    def _resolve(self, selection: Selection) -> int | None:
        """Índice de la entidad en la escena actual, o ``None`` si ya no existe."""
        if self._scene is None:
            return None
        index = (
            self._scene.node_index
            if selection.kind is SelectionKind.NODE
            else self._scene.member_index
        )
        return index.get(selection.id)

    def _draw_selection(self) -> None:
        plotter = self._plotter
        for name in self._overlay:
            plotter.remove_actor(name, render=False)
        self._overlay = []
        scene = self._scene
        if scene is None:
            return
        members: list[SceneMember] = []
        for number, selection in enumerate(self._selection.items()):
            index = self._resolve(selection)
            if index is None:
                continue
            if selection.kind is SelectionKind.NODE:
                position = scene.nodes[index].position
                radius = 0.08 * scene.glyph_length
                self._add_overlay(pv.Sphere(radius=radius, center=position), f"sel-node-{number}")
            else:
                member = scene.members[index]
                start = scene.nodes[member.node_i].position
                end = scene.nodes[member.node_j].position
                self._add_overlay(pv.Line(start, end), f"sel-member-{number}", line_width=8)
                members.append(member)
        if len(members) == 1 and len(self._selection) == 1:
            self._draw_axes(scene, members[0])

    def _draw_axes(self, scene: Scene, member: SceneMember) -> None:
        plotter = self._plotter
        origin, *axes = scene.axes_of(member.id)
        tips: list[Point] = []
        for (name, color), vector in zip(AXIS_COLORS, axes, strict=True):
            arrow = pv.Arrow(start=origin, direction=vector, scale=scene.glyph_length)
            self._add_overlay(arrow, f"sel-axis-{name}", color=color)
            tips.append(
                (
                    origin[0] + vector[0] * scene.glyph_length,
                    origin[1] + vector[1] * scene.glyph_length,
                    origin[2] + vector[2] * scene.glyph_length,
                )
            )
        plotter.add_point_labels(
            np.array(tips, dtype=float),
            [name for name, _ in AXIS_COLORS],
            font_size=14,
            text_color="black",
            shape=None,
            show_points=False,
            always_visible=True,
            name="sel-axis-labels",
            pickable=False,
        )
        self._overlay.append("sel-axis-labels")

    def _add_overlay(
        self, mesh: pv.PolyData, name: str, *, color: str = COLOR_SELECTED, line_width: int = 1
    ) -> None:
        self._plotter.add_mesh(mesh, color=color, line_width=line_width, name=name, pickable=False)
        self._overlay.append(name)

    # ------------------------------------------------------------------- clic

    def display_position(self, point: Point) -> Pixel:
        """Píxel (origen abajo a la izquierda, como VTK) donde se proyecta un punto del modelo."""
        renderer = self._plotter.renderer
        renderer.SetWorldPoint(point[0], point[1], point[2], 1.0)
        renderer.WorldToDisplay()
        x, y, _ = renderer.GetDisplayPoint()
        return (float(x), float(y))

    def pick_at(self, pixel: Pixel) -> Selection | None:
        """Entidad bajo ``pixel`` (coordenadas de VTK), o ``None`` si no hay ninguna."""
        scene = self._scene
        if scene is None:
            return None
        projected = [self.display_position(node.position) for node in scene.nodes]
        hit = pick_nearest(pixel, projected, scene.segments)
        if hit is None:
            return None
        kind, index = hit
        entity = scene.nodes[index] if kind is SelectionKind.NODE else scene.members[index]
        return Selection(kind, entity.id)

    def click_at(self, pixel: Pixel, *, additive: bool = False) -> None:
        """Procesa un clic: selecciona lo que haya bajo ``pixel`` (o limpia) y emite las señales.

        Con ``additive`` (Ctrl o Mayús) alterna la entidad en la selección; un clic en vacío no
        la modifica.
        """
        hit = self.pick_at(pixel)
        if additive:
            new = self._selection.toggle(hit) if hit is not None else self._selection
        else:
            new = SelectionSet.from_selection(hit)
        self.select_set(new)
        self.selection_set_changed.emit(self._selection)
        self.selection_changed.emit(self._selection.single)

    def _event_pixel(self) -> Pixel:
        x, y = self._plotter.iren.interactor.GetEventPosition()
        return (float(x), float(y))

    def _on_press(self, *_: object) -> None:
        self._press = self._event_pixel()

    def _on_release(self, *_: object) -> None:
        press, self._press = self._press, None
        if press is None:
            return
        release = self._event_pixel()
        if abs(release[0] - press[0]) <= CLICK_TOLERANCE and (
            abs(release[1] - press[1]) <= CLICK_TOLERANCE
        ):
            interactor = self._plotter.iren.interactor
            additive = bool(interactor.GetControlKey() or interactor.GetShiftKey())
            self.click_at(release, additive=additive)

    def close_viewport(self) -> None:
        """Libera los recursos de VTK; debe llamarse antes de destruir la ventana."""
        self._plotter.close()
