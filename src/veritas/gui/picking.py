"""Selección por clic en espacio de pantalla (plan T-011, E2; sin Qt ni PyVista).

El visor proyecta nudos y barras a píxeles y este módulo decide qué se tocó: el nudo más cercano
dentro de ``NODE_RADIUS`` y, si no hay ninguno, la barra cuyo segmento proyectado queda más cerca
dentro de ``MEMBER_RADIUS``. No usa el buffer de profundidad: entre entidades que se solapan en
pantalla gana la más cercana al cursor, no la más próxima a la cámara (límite declarado).
"""

import math
from collections.abc import Sequence
from typing import Final

from veritas.gui.selection import SelectionKind

type Pixel = tuple[float, float]

NODE_RADIUS: Final = 12.0
"""Tolerancia de clic sobre un nudo (píxeles)."""

MEMBER_RADIUS: Final = 7.0
"""Tolerancia de clic sobre una barra (píxeles)."""


def distance_to_segment(point: Pixel, start: Pixel, end: Pixel) -> float:
    """Distancia (píxeles) de ``point`` al segmento ``start``-``end``; admite segmento nulo."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    length_sq = dx * dx + dy * dy
    if length_sq == 0.0:
        return math.dist(point, start)
    t = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_sq
    t = max(0.0, min(1.0, t))
    return math.dist(point, (start[0] + t * dx, start[1] + t * dy))


def pick_nearest(
    click: Pixel,
    nodes: Sequence[Pixel],
    segments: Sequence[tuple[int, int]],
    *,
    node_radius: float = NODE_RADIUS,
    member_radius: float = MEMBER_RADIUS,
) -> tuple[SelectionKind, int] | None:
    """Índice de la entidad tocada en ``click`` (nudo preferente), o ``None`` si no hay ninguna.

    ``segments`` son pares de índices de ``nodes``. Con empate de distancia gana el índice menor.
    """
    best_node: tuple[float, int] | None = None
    for index, pixel in enumerate(nodes):
        distance = math.dist(click, pixel)
        if distance <= node_radius and (best_node is None or distance < best_node[0]):
            best_node = (distance, index)
    if best_node is not None:
        return (SelectionKind.NODE, best_node[1])
    best_member: tuple[float, int] | None = None
    for index, (i, j) in enumerate(segments):
        distance = distance_to_segment(click, nodes[i], nodes[j])
        if distance <= member_radius and (best_member is None or distance < best_member[0]):
            best_member = (distance, index)
    if best_member is not None:
        return (SelectionKind.MEMBER, best_member[1])
    return None
