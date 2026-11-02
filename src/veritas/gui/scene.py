"""Escena 3D derivada del modelo (plan T-011, E1; ADR-008).

``build_scene`` convierte un ``ModelSnapshot`` en datos planos de dibujo: puntos de nudo,
segmentos de barra, marcas de apoyo y ejes locales. Es una **proyección de solo lectura**: la
fuente de verdad sigue siendo el dominio y la escena se reconstruye entera tras cada cambio.

Este módulo no importa PySide6 ni PyVista (se prueba sin ellos). Las coordenadas son las del
dominio sin transformar (m, base global X, Y, Z; ADR-001 §1); los ejes locales se obtienen de
``recompute_local_axes`` con la orientación guardada, nunca se recalculan aquí.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from uuid import UUID

from veritas.core.diagnostics import Location
from veritas.core.geometry.entities import Member, Node
from veritas.core.geometry.local_axes import recompute_local_axes
from veritas.core.geometry.snapshot import ModelSnapshot
from veritas.core.geometry.vector import Vec3
from veritas.core.supports.support import BasisKind, RestraintKind, Support

type Point = tuple[float, float, float]

DEFAULT_GLYPH_LENGTH = 1.0
"""Longitud de los ejes dibujados (m) cuando el modelo no tiene extensión."""

GLYPH_FRACTION = 0.1
"""Fracción de la diagonal del modelo usada para dibujar los ejes locales."""


def _point(vector: Vec3) -> Point:
    return (vector.x, vector.y, vector.z)


@dataclass(frozen=True, slots=True)
class SceneNode:
    """Nudo dibujable: identidad estable y posición en m."""

    id: UUID
    label: str
    position: Point


@dataclass(frozen=True, slots=True)
class SceneMember:
    """Barra dibujable: índices de sus nudos (``Scene.nodes``) y su base local."""

    id: UUID
    label: str
    node_i: int
    node_j: int
    length: float
    midpoint: Point
    ex: Point
    ey: Point
    ez: Point


@dataclass(frozen=True, slots=True)
class SceneSupport:
    """Apoyo dibujable: nudo, estado de los seis GDL y si su base es la global."""

    id: UUID
    label: str
    node: int
    restraints: tuple[RestraintKind, ...]
    global_basis: bool

    @property
    def translations_fixed(self) -> int:
        """Número de traslaciones fijas (0 a 3)."""
        return sum(kind is RestraintKind.FIXED for kind in self.restraints[:3])

    @property
    def rotations_fixed(self) -> int:
        """Número de giros fijos (0 a 3)."""
        return sum(kind is RestraintKind.FIXED for kind in self.restraints[3:])


@dataclass(frozen=True, slots=True)
class Scene:
    """Datos de dibujo del modelo; se reconstruyen enteros, nunca se editan."""

    nodes: tuple[SceneNode, ...]
    members: tuple[SceneMember, ...]
    supports: tuple[SceneSupport, ...]
    bounds: tuple[Point, Point]
    glyph_length: float
    node_index: Mapping[UUID, int]
    member_index: Mapping[UUID, int]

    @property
    def points(self) -> tuple[Point, ...]:
        """Posiciones de los nudos, en el orden de ``nodes``."""
        return tuple(node.position for node in self.nodes)

    @property
    def segments(self) -> tuple[tuple[int, int], ...]:
        """Pares de índices de nudo (I, J) de cada barra, en el orden de ``members``."""
        return tuple((member.node_i, member.node_j) for member in self.members)

    def axes_of(self, member_id: UUID) -> tuple[Point, Point, Point, Point]:
        """Origen (punto medio) y ejes ``ex, ey, ez`` de una barra para dibujarlos."""
        member = self.members[self.member_index[member_id]]
        return (member.midpoint, member.ex, member.ey, member.ez)


def _bounds(nodes: tuple[SceneNode, ...]) -> tuple[Point, Point]:
    if not nodes:
        return ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    xs, ys, zs = zip(*(node.position for node in nodes), strict=True)
    return ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))


def _glyph_length(bounds: tuple[Point, Point]) -> float:
    low, high = bounds
    diagonal = math.dist(low, high)
    return GLYPH_FRACTION * diagonal if diagonal > 0.0 else DEFAULT_GLYPH_LENGTH


def _scene_member(
    member: Member, nodes: Mapping[UUID, Node], index: Mapping[UUID, int]
) -> SceneMember:
    for node_id in (member.node_i, member.node_j):
        if node_id not in index:
            raise ValueError(f"La barra {member.label or member.id} usa un nudo inexistente")
    p_i = nodes[member.node_i].position
    p_j = nodes[member.node_j].position
    axes = recompute_local_axes(
        p_i,
        p_j,
        member.orientation,
        location=Location(entity_id=member.id, revision=member.revision),
    )
    return SceneMember(
        id=member.id,
        label=member.label,
        node_i=index[member.node_i],
        node_j=index[member.node_j],
        length=axes.length,
        midpoint=((p_i.x + p_j.x) / 2, (p_i.y + p_j.y) / 2, (p_i.z + p_j.z) / 2),
        ex=_point(axes.ex),
        ey=_point(axes.ey),
        ez=_point(axes.ez),
    )


def _scene_support(support: Support, index: Mapping[UUID, int]) -> SceneSupport:
    if support.node_id not in index:
        raise ValueError(f"El apoyo {support.label or support.id} usa un nudo inexistente")
    return SceneSupport(
        id=support.id,
        label=support.label,
        node=index[support.node_id],
        restraints=tuple(restraint.kind for restraint in support.restraints),
        global_basis=support.basis.kind is BasisKind.GLOBAL,
    )


def build_scene(snapshot: ModelSnapshot) -> Scene:
    """Escena de un modelo; ``ValueError`` si una barra o apoyo apunta a un nudo ausente."""
    nodes = tuple(SceneNode(n.id, n.label, _point(n.position)) for n in snapshot.nodes)
    node_index = MappingProxyType({node.id: i for i, node in enumerate(nodes)})
    by_id = {node.id: node for node in snapshot.nodes}
    members = tuple(_scene_member(m, by_id, node_index) for m in snapshot.members)
    supports = tuple(_scene_support(s, node_index) for s in snapshot.supports)
    bounds = _bounds(nodes)
    return Scene(
        nodes=nodes,
        members=members,
        supports=supports,
        bounds=bounds,
        glyph_length=_glyph_length(bounds),
        node_index=node_index,
        member_index=MappingProxyType({member.id: i for i, member in enumerate(members)}),
    )
