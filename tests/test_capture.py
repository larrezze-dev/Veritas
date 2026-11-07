"""T-030 E3a (R3-D2…D6): captura geométrica sin Qt: rayo→plano, rejilla, snapping, cotas y entrada
numérica. La cámara es sintética: ortográfica desde +Z (X,Y → píxel) o en perspectiva simple."""

import math

import pytest

from veritas.core.diagnostics import DiagnosticError
from veritas.core.messages_es import render_message
from veritas.gui import capture
from veritas.gui.capture import PlaneAxis, SnapKind, WorkPlane

SCALE = 100.0  # píxeles por metro en la vista en planta


def top_project(p: capture.Point) -> tuple[float, float]:
    return (p[0] * SCALE, p[1] * SCALE)


def top_ray(pixel: tuple[float, float]) -> tuple[capture.Point, capture.Point]:
    """Rayo vertical hacia abajo por el píxel (cámara ortográfica sobre el plano XY)."""
    x, y = pixel[0] / SCALE, pixel[1] / SCALE
    return ((x, y, 100.0), (x, y, -100.0))


NODES = [(0.0, 0.0, 0.0), (4.0, 0.0, 0.0), (4.0, 3.0, 0.0)]
MEMBERS = [(0, 1), (1, 2)]


def do_snap(pixel: tuple[float, float], **kwargs: object) -> capture.SnapResult | None:
    return capture.snap(
        pixel,
        top_ray(pixel),
        project=top_project,
        nodes=NODES,
        members=MEMBERS,
        plane=kwargs.pop("plane", WorkPlane()),  # type: ignore[arg-type]
        **kwargs,  # type: ignore[arg-type]
    )


# --- Plano y rayo ----------------------------------------------------------------------------


def test_ray_hits_each_plane_orientation() -> None:
    near, far = (1.0, 2.0, 10.0), (3.0, 4.0, -10.0)
    z = capture.ray_plane(near, far, WorkPlane(PlaneAxis.Z, 3.0))
    assert z == pytest.approx((1.0 + 2.0 * 7 / 20, 2.0 + 2.0 * 7 / 20, 3.0))
    x = capture.ray_plane((0.0, 0.0, 0.0), (10.0, 5.0, 2.0), WorkPlane(PlaneAxis.X, 4.0))
    assert x == pytest.approx((4.0, 2.0, 0.8))
    y = capture.ray_plane((0.0, 0.0, 0.0), (10.0, 5.0, 2.0), WorkPlane(PlaneAxis.Y, 1.0))
    assert y == pytest.approx((2.0, 1.0, 0.4))


def test_parallel_or_backward_rays_do_not_hit() -> None:
    plane = WorkPlane(PlaneAxis.Z, 0.0)
    assert capture.ray_plane((0.0, 0.0, 5.0), (1.0, 0.0, 5.0), plane) is None  # paralelo
    assert capture.ray_plane((0.0, 0.0, 5.0), (0.0, 0.0, 9.0), plane) is None  # detrás de cámara


def test_workplane_projects_and_validates() -> None:
    assert WorkPlane(PlaneAxis.Y, 2.0).project((1.0, 9.0, 3.0)) == (1.0, 2.0, 3.0)
    with pytest.raises(ValueError, match="finito"):
        WorkPlane(PlaneAxis.Z, math.inf)
    with pytest.raises(TypeError):
        WorkPlane("Z")  # type: ignore[arg-type]


# --- Rejilla ---------------------------------------------------------------------------------


def test_grid_rounds_in_plane_coordinates_and_keeps_the_plane_offset() -> None:
    plane = WorkPlane(PlaneAxis.Z, 3.0)
    assert capture.grid_point((1.26, -0.74, 99.0), plane, 0.5) == (1.5, -0.5, 3.0)
    vertical = WorkPlane(PlaneAxis.X, 2.0)
    assert capture.grid_point((7.0, 1.1, 2.9), vertical, 1.0) == (2.0, 1.0, 3.0)


def test_grid_never_returns_negative_zero_and_rejects_bad_steps() -> None:
    result = capture.grid_point((-0.01, 0.01, 0.0), WorkPlane(), 0.5)
    assert str(result[0]) == "0.0"
    for step in (0.0, -1.0, math.nan, math.inf):
        with pytest.raises(DiagnosticError) as info:
            capture.grid_point((0.0, 0.0, 0.0), WorkPlane(), step)
        assert "paso de rejilla" in render_message(info.value.diagnostic)


# --- Snapping --------------------------------------------------------------------------------


def test_node_has_priority_over_everything_within_its_radius() -> None:
    result = do_snap((402.0, 3.0))  # a 3 px del nudo 1, también encima de la barra 0
    assert result is not None
    assert (result.kind, result.node, result.point) == (SnapKind.NODE, 1, NODES[1])


def test_midpoint_beats_on_member_and_grid() -> None:
    result = do_snap((201.0, 2.0))
    assert result is not None
    assert (result.kind, result.member, result.point) == (SnapKind.MIDPOINT, 0, (2.0, 0.0, 0.0))


def test_on_member_gives_the_point_on_the_segment() -> None:
    result = do_snap((100.0, 4.0))  # a 4 px de la barra 0, lejos de nudos y punto medio
    assert result is not None
    assert result.kind is SnapKind.ON_MEMBER
    assert result.member == 0
    assert result.point == pytest.approx((1.0, 0.0, 0.0))


def test_grid_then_free_when_nothing_else_is_near() -> None:
    result = do_snap((137.0, 212.0))
    assert result is not None
    assert result.kind is SnapKind.GRID
    assert result.point == (1.5, 2.0, 0.0)
    free = do_snap((137.0, 212.0), modes={SnapKind.FREE})
    assert free is not None
    assert free.kind is SnapKind.FREE
    assert free.point == pytest.approx((1.37, 2.12, 0.0))


def test_disabled_modes_are_skipped_and_nothing_enabled_gives_none() -> None:
    result = do_snap((402.0, 3.0), modes={SnapKind.GRID})
    assert result is not None
    assert result.kind is SnapKind.GRID
    assert do_snap((137.0, 212.0), modes=set()) is None


def test_a_ray_parallel_to_the_plane_captures_nothing_free() -> None:
    horizontal = ((0.0, -50.0, 5.0), (0.0, 50.0, 5.0))
    result = capture.snap(
        (900.0, 900.0),
        horizontal,
        project=top_project,
        nodes=NODES,
        members=MEMBERS,
        plane=WorkPlane(),
    )
    assert result is None


def test_ties_go_to_the_lowest_index() -> None:
    nodes = [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0)]  # superpuestos
    result = capture.snap(
        (0.0, 0.0),
        top_ray((0.0, 0.0)),
        project=top_project,
        nodes=nodes,
        members=[],
        plane=WorkPlane(),
    )
    assert result is not None
    assert result.node == 0


def test_on_member_uses_depth_correct_3d_point_in_perspective() -> None:
    """Con perspectiva, el punto de la barra sale del rayo 3D, no de interpolar en pantalla."""
    eye = (0.0, -10.0, 0.0)

    def project(p: capture.Point) -> tuple[float, float]:
        depth = p[1] - eye[1]
        return (p[0] / depth * 1000.0, p[2] / depth * 1000.0)

    a, b = (-1.0, 0.0, 0.0), (1.0, 20.0, 0.0)  # barra que se aleja de la cámara
    target = (0.0, 10.0, 0.0)  # punto medio 3D
    pixel = project(target)
    far = (target[0] * 2, target[1] * 2 + 10.0, 0.0)
    result = capture.snap(
        pixel,
        (eye, (eye[0] + (far[0] - eye[0]), far[1], 0.0)),
        project=project,
        nodes=[a, b],
        members=[(0, 1)],
        plane=WorkPlane(),
        modes={SnapKind.ON_MEMBER},
    )
    assert result is not None
    assert result.kind is SnapKind.ON_MEMBER
    assert result.point == pytest.approx(target, abs=1e-6)
    assert math.dist(project(result.point), pixel) < 1e-6


# --- Cotas -----------------------------------------------------------------------------------


def test_dimensions_and_texts() -> None:
    d = capture.dimensions((0.0, 0.0, 0.0), (3.0, 4.0, 12.0))
    assert (d.length, d.dx, d.dy, d.dz) == (13.0, 3.0, 4.0, 12.0)
    assert "L = 13.000 m" in d.text()
    assert "ΔZ = 12.000" in d.text()
    assert capture.cursor_text((1.0, 2.0, 3.0)) == "X = 1.000  Y = 2.000  Z = 3.000 m"


# --- Entrada numérica ------------------------------------------------------------------------


def test_entry_accepts_absolute_relative_and_length_forms() -> None:
    anchor, cursor = (1.0, 1.0, 0.0), (1.0, 5.0, 0.0)
    assert capture.parse_entry("2; 3; 4", anchor=None, cursor=None) == (2.0, 3.0, 4.0)
    assert capture.parse_entry("50 cm; 0,5; 1 m", anchor=None, cursor=None) == (0.5, 0.5, 1.0)
    assert capture.parse_entry("@1; 0; 2", anchor=anchor, cursor=None) == (2.0, 1.0, 2.0)
    assert capture.parse_entry("2,5", anchor=anchor, cursor=cursor) == pytest.approx(
        (1.0, 3.5, 0.0)
    )


@pytest.mark.parametrize(
    ("text", "anchor", "cursor", "fragment"),
    [
        ("", None, None, "vacío"),
        ("abc", None, None, "número"),
        ("1,000", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), "número"),
        ("1; 2", None, None, "longitud"),
        ("@1", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), "longitud"),
        ("3", None, None, "primer punto"),
        ("@1; 2; 3", None, None, "primer punto"),
        ("3", (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), "dirección"),
        ("3 kg", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), "unidad"),
    ],
)
def test_bad_entries_give_a_spanish_diagnostic(
    text: str, anchor: capture.Point | None, cursor: capture.Point | None, fragment: str
) -> None:
    with pytest.raises(DiagnosticError) as info:
        capture.parse_entry(text, anchor=anchor, cursor=cursor)
    assert fragment in render_message(info.value.diagnostic)
