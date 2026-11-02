"""Selección por clic en espacio de pantalla (plan T-011, E2): sin Qt ni PyVista."""

import pytest

from veritas.gui.picking import (
    MEMBER_RADIUS,
    NODE_RADIUS,
    distance_to_segment,
    pick_nearest,
)
from veritas.gui.selection import SelectionKind

# Pórtico proyectado: N0 (0,0), N1 (0,100), N2 (100,100).
NODES = [(0.0, 0.0), (0.0, 100.0), (100.0, 100.0)]
SEGMENTS = [(0, 1), (1, 2)]


def test_distance_to_segment_projects_onto_the_interior_and_clamps_the_ends() -> None:
    assert distance_to_segment((5.0, 50.0), (0.0, 0.0), (0.0, 100.0)) == pytest.approx(5.0)
    assert distance_to_segment((3.0, 104.0), (0.0, 0.0), (0.0, 100.0)) == pytest.approx(5.0)
    assert distance_to_segment((0.0, -7.0), (0.0, 0.0), (0.0, 100.0)) == pytest.approx(7.0)


def test_distance_to_a_degenerate_segment_is_the_distance_to_its_point() -> None:
    assert distance_to_segment((3.0, 4.0), (0.0, 0.0), (0.0, 0.0)) == pytest.approx(5.0)


def test_click_on_a_node_selects_the_node() -> None:
    assert pick_nearest((2.0, 101.0), NODES, SEGMENTS) == (SelectionKind.NODE, 1)


def test_node_wins_over_the_member_that_ends_there() -> None:
    # A NODE_RADIUS/2 del nudo 1 también está sobre la barra 0: el nudo tiene prioridad.
    click = (0.0, 100.0 - NODE_RADIUS / 2)
    assert pick_nearest(click, NODES, SEGMENTS) == (SelectionKind.NODE, 1)


def test_click_on_the_interior_of_a_member_selects_the_member() -> None:
    assert pick_nearest((50.0, 100.0 + MEMBER_RADIUS / 2), NODES, SEGMENTS) == (
        SelectionKind.MEMBER,
        1,
    )
    assert pick_nearest((MEMBER_RADIUS / 2, 50.0), NODES, SEGMENTS) == (SelectionKind.MEMBER, 0)


def test_click_in_the_empty_space_selects_nothing() -> None:
    assert pick_nearest((50.0, 20.0), NODES, SEGMENTS) is None
    assert pick_nearest((50.0, 100.0 + MEMBER_RADIUS + 1.0), NODES, SEGMENTS) is None


def test_empty_scene_selects_nothing() -> None:
    assert pick_nearest((0.0, 0.0), [], []) is None


def test_the_nearest_member_wins_when_two_overlap() -> None:
    nodes = [(0.0, 0.0), (100.0, 0.0), (0.0, 4.0), (100.0, 4.0)]
    segments = [(0, 1), (2, 3)]
    assert pick_nearest((50.0, 3.0), nodes, segments) == (SelectionKind.MEMBER, 1)
    assert pick_nearest((50.0, 1.0), nodes, segments) == (SelectionKind.MEMBER, 0)
