"""Contenido de las tablas de barras y nudos (plan T-011, E2; sin Qt ni PyVista).

Las filas siguen el orden de ``Scene.members`` y ``Scene.nodes``: el índice de fila es el índice
de la escena. Los textos usan ``core.presentation`` (perfil SI, longitudes en m). Es solo
lectura: nada de aquí vuelve al dominio.
"""

from typing import Final
from uuid import UUID

from veritas.core import units as u
from veritas.core.presentation import PROFILE_SI, QuantityKind, format_quantity
from veritas.core.supports.support import RestraintKind
from veritas.gui.scene import Point, Scene, SceneSupport

MEMBER_HEADERS: Final = ("Barra", "Nudo I", "Nudo J", "Longitud", "Eje x local")
NODE_HEADERS: Final = ("Nudo", "X", "Y", "Z", "Apoyo")

type Row = tuple[str, ...]


def is_fully_fixed(support: SceneSupport) -> bool:
    """Empotramiento: las tres traslaciones y los tres giros fijos."""
    return support.translations_fixed + support.rotations_fixed == len(support.restraints)


def entity_name(label: str, entity_id: UUID) -> str:
    """Etiqueta de la entidad o, si no tiene, el inicio de su identidad."""
    return label or str(entity_id)[:8]


def _length(value: float) -> str:
    return format_quantity(u.Quantity.of(value, "m"), QuantityKind.LENGTH, PROFILE_SI)


def _direction(vector: Point) -> str:
    return "(" + ", ".join(f"{component:.3f}" for component in vector) + ")"


def support_text(support: SceneSupport) -> str:
    """Descripción breve de un apoyo; los resortes se nombran, no se confunden con fijos."""
    springs = sum(kind is RestraintKind.SPRING for kind in support.restraints)
    if is_fully_fixed(support):
        text = "Empotrado"
    else:
        text = f"{support.translations_fixed} traslaciones y {support.rotations_fixed} giros fijos"
    if springs:
        text += f" + {springs} con resorte"
    if not support.global_basis:
        text += " (base inclinada)"
    return text


def member_rows(scene: Scene) -> tuple[Row, ...]:
    """Una fila por barra: etiqueta, nudos I/J, longitud y eje x local."""
    return tuple(
        (
            entity_name(member.label, member.id),
            entity_name(scene.nodes[member.node_i].label, scene.nodes[member.node_i].id),
            entity_name(scene.nodes[member.node_j].label, scene.nodes[member.node_j].id),
            _length(member.length),
            _direction(member.ex),
        )
        for member in scene.members
    )


def node_rows(scene: Scene) -> tuple[Row, ...]:
    """Una fila por nudo: etiqueta, coordenadas y apoyos que lo restringen."""
    supports: dict[int, list[str]] = {}
    for support in scene.supports:
        supports.setdefault(support.node, []).append(support_text(support))
    return tuple(
        (
            entity_name(node.label, node.id),
            _length(node.position[0]),
            _length(node.position[1]),
            _length(node.position[2]),
            "; ".join(supports.get(index, ())) or "—",
        )
        for index, node in enumerate(scene.nodes)
    )
