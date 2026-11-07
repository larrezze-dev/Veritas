"""T-030 E3b-1 (R3-D4, R3-D6): máquina de gestos sin Qt: un gesto = un paso de deshacer,
Esc sin huella, nudos reutilizados, arrastre = un move_node, entrada numérica y R4/R6/R8/R12–R15."""

import math
from collections.abc import Callable

import pytest

from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticError
from veritas.core.geometry.editing import ModelEditor
from veritas.core.geometry.local_axes import MIN_MEMBER_LENGTH
from veritas.core.geometry.model import StructuralModel
from veritas.core.materials.material import MaterialFamily
from veritas.core.messages_es import render_message
from veritas.core.presentation import PROFILE_SI
from veritas.core.references import RevisionRef
from veritas.gui import capture, gestures
from veritas.gui.capture import PlaneAxis, SnapKind, WorkPlane
from veritas.gui.gestures import (
    Action,
    Capture,
    CreateMember,
    CreateNode,
    GestureMachine,
    GestureState,
    Mode,
    MoveNode,
)
from veritas.persistence.canonical import model_hash

M = u.Quantity.of


def setup() -> tuple[ModelEditor, RevisionRef, RevisionRef]:
    editor = ModelEditor(StructuralModel())
    steel = editor.add_material(
        name="Acero",
        family=MaterialFamily.STEEL,
        source="prueba",
        elastic_modulus=M(210_000, "N/mm²"),
        shear_modulus=M(81_000, "N/mm²"),
        density=M(7850, "kg/m³"),
    )
    section = editor.add_rectangular_section(b=M(0.2, "m"), h=M(0.4, "m"), name="R")
    return editor, steel.ref, section.ref


def state(machine: GestureMachine) -> GestureState:
    return machine.state


def digest(editor: ModelEditor) -> str:
    return model_hash(editor.model.snapshot())


def click(
    machine: GestureMachine, hit: Capture | None, pixel: tuple[float, float] = (10.0, 10.0)
) -> Action | None:
    machine.press(pixel, hit)
    return machine.release(pixel, hit)


def origin_node(editor: ModelEditor) -> Capture:
    node = editor.add_node(M(0, "m"), M(0, "m"), M(0, "m"))
    return Capture((0.0, 0.0, 0.0), node.id)


# --- Nudo y barra -------------------------------------------------------------------------


def test_node_mode_click_creates_one_undoable_node() -> None:
    editor, _, _ = setup()
    machine = GestureMachine(Mode.NODE)
    before = digest(editor)
    action = click(machine, Capture((1.0, 2.0, 3.0)))
    assert action == CreateNode((1.0, 2.0, 3.0))
    gestures.apply(action, editor)
    assert len(editor.model.nodes) == 1
    editor.undo()
    assert digest(editor) == before


def test_node_mode_on_an_existing_node_does_nothing() -> None:
    editor, _, _ = setup()
    assert click(GestureMachine(Mode.NODE), origin_node(editor)) is None


def test_member_with_new_nodes_is_one_step_and_undo_restores_the_hash() -> None:
    editor, mat, sec = setup()
    before = digest(editor)
    machine = GestureMachine(Mode.MEMBER)
    assert click(machine, Capture((0.0, 0.0, 0.0))) is None
    assert state(machine) is GestureState.ANCHORED
    action = click(machine, Capture((4.0, 0.0, 0.0)))
    assert isinstance(action, CreateMember)
    assert state(machine) is GestureState.IDLE
    gestures.apply(action, editor, material=mat, section=sec)
    assert (len(editor.model.nodes), len(editor.model.members)) == (2, 1)
    assert editor.undo()  # UN solo paso: nudos y barra a la vez
    assert digest(editor) == before


def test_member_reuses_existing_and_coincident_nodes() -> None:
    editor, mat, sec = setup()
    a = origin_node(editor)
    editor.add_node(M(4, "m"), M(0, "m"), M(0, "m"))
    action = CreateMember(a, Capture((4.0 + 5e-10, 0.0, 0.0)))
    gestures.apply(action, editor, material=mat, section=sec)
    assert (len(editor.model.nodes), len(editor.model.members)) == (2, 1)


def test_member_with_coincident_ends_is_rejected_and_leaves_no_node() -> None:
    editor, mat, sec = setup()
    before = digest(editor)
    action = CreateMember(Capture((1.0, 1.0, 1.0)), Capture((1.0, 1.0, 1.0 + 1e-10)))
    with pytest.raises(DiagnosticError):
        gestures.apply(action, editor, material=mat, section=sec)
    assert digest(editor) == before


def test_member_needs_material_and_section() -> None:
    editor, _, _ = setup()
    action = CreateMember(Capture((0.0, 0.0, 0.0)), Capture((1.0, 0.0, 0.0)))
    with pytest.raises(DiagnosticError):
        gestures.apply(action, editor)
    assert len(editor.model.nodes) == 0


def test_second_click_on_the_same_point_keeps_waiting() -> None:
    machine = GestureMachine(Mode.MEMBER)
    click(machine, Capture((0.0, 0.0, 0.0)))
    assert click(machine, Capture((0.0, 0.0, 0.0))) is None
    assert state(machine) is GestureState.ANCHORED


def test_two_storeys_frame_is_built_only_with_actions_and_undone_step_by_step() -> None:
    editor, mat, sec = setup()
    machine = GestureMachine(Mode.MEMBER)
    hashes = [digest(editor)]
    corners = [
        (0.0, 0.0, 0.0),
        (4.0, 0.0, 0.0),
        (0.0, 0.0, 3.0),
        (4.0, 0.0, 3.0),
        (0.0, 0.0, 6.0),
        (4.0, 0.0, 6.0),
    ]
    segments = [(0, 2), (1, 3), (2, 3), (2, 4), (3, 5), (4, 5)]
    for i, j in segments:
        click(machine, Capture(corners[i]))
        action = click(machine, Capture(corners[j]))
        assert action is not None
        gestures.apply(action, editor, material=mat, section=sec)
        hashes.append(digest(editor))
    assert (len(editor.model.nodes), len(editor.model.members)) == (6, 6)
    for expected in reversed(hashes[:-1]):
        assert editor.undo()
        assert digest(editor) == expected


# --- Cancelar, modos y arrastre -----------------------------------------------------------


def test_escape_and_mode_change_discard_the_gesture_without_trace() -> None:
    editor, _, _ = setup()
    before = digest(editor)
    machine = GestureMachine(Mode.MEMBER)
    click(machine, Capture((0.0, 0.0, 0.0)))
    assert machine.cancel()
    assert state(machine) is GestureState.IDLE
    assert machine.anchor is None
    assert not machine.cancel()
    click(machine, Capture((0.0, 0.0, 0.0)))
    machine.set_mode(Mode.NODE)
    assert machine.anchor is None
    assert machine.mode is Mode.NODE
    assert digest(editor) == before


def test_drag_of_a_selected_node_is_one_move_and_one_undo_step() -> None:
    editor, _, _ = setup()
    node = editor.add_node(M(1, "m"), M(0, "m"), M(0, "m"))
    before = digest(editor)
    machine = GestureMachine()
    hit = Capture((1.0, 0.0, 0.0), node.id)
    machine.press((100.0, 100.0), hit, selected_nodes={node.id})
    assert state(machine) is GestureState.DRAGGING
    machine.move(Capture((2.0, 0.0, 0.0)))
    machine.move(Capture((3.0, 0.0, 0.0)))
    preview = machine.preview()
    assert preview is not None
    assert preview.moving == node.id
    assert preview.dimensions.length == pytest.approx(2.0)
    assert digest(editor) == before  # la vista previa no toca el modelo
    action = machine.release((150.0, 100.0), Capture((3.0, 0.0, 0.0)))
    assert action == MoveNode(node.id, (3.0, 0.0, 0.0))
    gestures.apply(action, editor)
    assert editor.model.node(node.id).position.x == pytest.approx(3.0)
    assert editor.undo()
    assert digest(editor) == before


def test_drag_needs_a_selected_node_and_a_small_move_is_a_click() -> None:
    editor, _, _ = setup()
    hit = origin_node(editor)
    machine = GestureMachine()
    machine.press((0.0, 0.0), hit)  # no seleccionado
    assert state(machine) is GestureState.IDLE
    assert hit.node_id is not None
    machine.press((0.0, 0.0), hit, selected_nodes={hit.node_id})
    assert machine.release((3.0, 3.0), Capture((0.1, 0.0, 0.0))) is None  # ≤ 4 px: clic
    assert state(machine) is GestureState.IDLE


def test_escape_during_a_drag_leaves_no_trace() -> None:
    editor, _, _ = setup()
    hit = origin_node(editor)
    assert hit.node_id is not None
    machine = GestureMachine()
    machine.press((0.0, 0.0), hit, selected_nodes={hit.node_id})
    assert machine.cancel()
    assert machine.release((50.0, 0.0), Capture((1.0, 0.0, 0.0))) is None


def test_orbiting_the_camera_in_member_mode_is_not_a_gesture() -> None:
    machine = GestureMachine(Mode.MEMBER)
    machine.press((0.0, 0.0), Capture((0.0, 0.0, 0.0)))
    assert machine.release((80.0, 0.0), Capture((1.0, 0.0, 0.0))) is None
    assert state(machine) is GestureState.IDLE


# --- Entrada numérica ---------------------------------------------------------------------


def test_typed_length_and_increments_fix_the_second_point() -> None:
    machine = GestureMachine(Mode.MEMBER)
    click(machine, Capture((1.0, 0.0, 0.0)))
    machine.move(Capture((1.0, 5.0, 0.0)))
    action = machine.type_entry("2")
    assert isinstance(action, CreateMember)
    assert action.end.point == pytest.approx((1.0, 2.0, 0.0))
    click(machine, Capture((1.0, 0.0, 0.0)))
    action = machine.type_entry("@1; 0; 3")
    assert isinstance(action, CreateMember)
    assert action.end.point == pytest.approx((2.0, 0.0, 3.0))


def test_typed_absolute_point_creates_a_node_or_anchors_a_member() -> None:
    assert GestureMachine(Mode.NODE).type_entry("1; 2; 3") == CreateNode((1.0, 2.0, 3.0))
    machine = GestureMachine(Mode.MEMBER)
    assert machine.type_entry("0; 0; 0") is None
    assert machine.anchor == Capture((0.0, 0.0, 0.0))


def test_typed_entry_moves_the_grabbed_node_and_bad_entry_changes_nothing() -> None:
    editor, _, _ = setup()
    hit = origin_node(editor)
    assert hit.node_id is not None
    machine = GestureMachine()
    machine.press((0.0, 0.0), hit, selected_nodes={hit.node_id})
    with pytest.raises(DiagnosticError):
        machine.type_entry("no")
    assert state(machine) is GestureState.DRAGGING
    assert machine.type_entry("5; 0; 0") == MoveNode(hit.node_id, (5.0, 0.0, 0.0))
    assert state(machine) is GestureState.IDLE


def test_capture_from_snap_maps_indices_to_ids() -> None:
    editor, _, _ = setup()
    ids = [editor.add_node(M(i, "m"), M(0, "m"), M(0, "m")).id for i in range(2)]
    node = capture.SnapResult((1.0, 0.0, 0.0), SnapKind.NODE, node=1)
    assert gestures.capture_from_snap(node, ids) == Capture((1.0, 0.0, 0.0), ids[1])
    grid = capture.SnapResult((1.0, 1.0, 0.0), SnapKind.GRID)
    assert gestures.capture_from_snap(grid, ids).node_id is None


# --- Ampliaciones de capture (R3–R6, R8, R12–R15) -----------------------------------------

SCALE = 100.0


def top_project(p: capture.Point) -> tuple[float, float]:
    return (p[0] * SCALE, p[1] * SCALE)


def top_ray(pixel: tuple[float, float]) -> tuple[capture.Point, capture.Point]:
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
        plane=WorkPlane(),
        **kwargs,  # type: ignore[arg-type]
    )


def test_r3_excluded_nodes_and_their_members_are_not_captured() -> None:
    result = do_snap((400.0, 0.0), exclude_nodes={1})
    assert result is not None
    assert result.kind is SnapKind.GRID  # tampoco las barras 0 y 1, que llegan al nudo 1
    free = do_snap((200.0, 1.0), exclude_members={0})
    assert free is not None
    assert free.kind is SnapKind.GRID


def test_r12_projected_nodes_give_the_same_result_without_reprojecting() -> None:
    calls: list[capture.Point] = []

    def spy(p: capture.Point) -> tuple[float, float]:
        calls.append(p)
        return top_project(p)

    pre = [top_project(p) for p in NODES]
    pixel = (402.0, 3.0)
    first = capture.snap(
        pixel,
        top_ray(pixel),
        project=spy,
        nodes=NODES,
        members=MEMBERS,
        plane=WorkPlane(),
        projected=pre,
    )
    assert first == do_snap(pixel)
    assert NODES[1] not in calls
    with pytest.raises(ValueError, match="un píxel por nudo"):
        capture.snap(
            pixel,
            top_ray(pixel),
            project=spy,
            nodes=NODES,
            members=MEMBERS,
            plane=WorkPlane(),
            projected=pre[:1],
        )


def test_r4_coincident_node_picks_the_closest_within_tolerance() -> None:
    nodes = [(0.0, 0.0, 0.0), (1e-10, 0.0, 0.0), (5.0, 0.0, 0.0)]
    assert capture.coincident_node((4e-10, 0.0, 0.0), nodes) == 1
    assert capture.coincident_node((1e-8, 0.0, 0.0), nodes) is None
    assert capture.coincident_node((1e-8, 0.0, 0.0), nodes, tol=1e-6) == 1


def test_r6_relative_parallel_tolerance_max_distance_and_zero_length_ray() -> None:
    plane = WorkPlane(PlaneAxis.Z, 0.0)
    tiny = ((0.0, 0.0, 1e-3), (1e-3, 0.0, 1e-3 - 1e-16))  # rayo diminuto casi paralelo
    assert capture.ray_plane(tiny[0], tiny[1], plane) is None
    assert capture.ray_plane((0.0, 0.0, 1.0), (0.0, 0.0, 1.0), plane) is None
    near, far = (0.0, 0.0, 10.0), (0.0, 0.0, -10.0)
    assert capture.ray_plane(near, far, plane, max_distance=9.0) is None
    assert capture.ray_plane(near, far, plane, max_distance=11.0) == pytest.approx((0, 0, 0))


def test_r6_oblique_isometric_like_ray_and_grazing_ray() -> None:
    near, far = (10.0, 10.0, 10.0), (-10.0, -10.0, -10.0)
    assert capture.ray_plane(near, far, WorkPlane()) == pytest.approx((0, 0, 0))
    hit = capture.ray_plane((0.0, 0.0, 1.0), (1000.0, 0.0, 0.0), WorkPlane())
    assert hit is not None
    assert hit[0] == pytest.approx(1000.0)
    assert (
        capture.ray_plane((0.0, 0.0, 1.0), (1000.0, 0.0, 0.0), WorkPlane(), max_distance=100.0)
        is None
    )


def test_r13_grid_step_limits_and_rounding() -> None:
    for bad in (0.0, -1.0, 1e-7, math.inf, math.nan):
        with pytest.raises(DiagnosticError):
            capture.grid_point((1.0, 1.0, 0.0), WorkPlane(), bad)
    with pytest.raises(DiagnosticError):  # el cociente desbordaría
        capture.grid_point((1e308, 0.0, 0.0), WorkPlane(), 1e-6)
    assert capture.grid_point((0.26, 0.74, 5.0), WorkPlane(), 0.5) == (0.5, 0.5, 0.0)
    assert capture.grid_point((0.25, 0.75, 0.0), WorkPlane(), 0.5)[:2] == (0.0, 1.0)  # a par


def test_levels_lists_distinct_sorted_heights() -> None:
    nodes = [(0.0, 0.0, 3.0), (1.0, 0.0, 0.0), (2.0, 0.0, 3.0 + 1e-10), (3.0, 0.0, 6.0)]
    assert capture.levels(nodes, PlaneAxis.Z) == (0.0, 3.0, 6.0)
    assert capture.levels(nodes, PlaneAxis.X) == (0.0, 1.0, 2.0, 3.0)


def test_r5_entry_accepts_the_presentation_profile() -> None:
    result = capture.parse_entry("1; 2; 3", anchor=None, cursor=None, profile=PROFILE_SI)
    assert result == (1.0, 2.0, 3.0)


@pytest.mark.parametrize(
    ("text", "fragment"),
    [("0", "mayor que cero"), ("-2", "mayor que cero"), ("1 2 3", "«;»"), ("@ 1 2 3", "«;»")],
)
def test_r8_r14_entry_messages(text: str, fragment: str) -> None:
    with pytest.raises(DiagnosticError) as info:
        capture.parse_entry(text, anchor=(0.0, 0.0, 0.0), cursor=(1.0, 0.0, 0.0))
    assert fragment in render_message(info.value.diagnostic)


def test_r8_nearly_null_direction_is_rejected() -> None:
    with pytest.raises(DiagnosticError):
        capture.parse_entry("1", anchor=(0.0, 0.0, 0.0), cursor=(1e-10, 0.0, 0.0))


def test_r15_entry_gaps() -> None:
    a, c = (1.0, 1.0, 1.0), (2.0, 1.0, 1.0)
    assert capture.parse_entry("@1; 2; 3", anchor=a, cursor=None) == (2.0, 3.0, 4.0)
    assert capture.parse_entry("1e1; 2E0; 3", anchor=None, cursor=None) == (10.0, 2.0, 3.0)
    assert capture.parse_entry("50 cm", anchor=a, cursor=c) == pytest.approx((1.5, 1.0, 1.0))
    assert capture.parse_entry("1,5; 0; 0", anchor=None, cursor=None) == (1.5, 0.0, 0.0)
    for text in ("1; 2; 3;", "1; 2", "@5"):
        with pytest.raises(DiagnosticError):
            capture.parse_entry(text, anchor=a, cursor=c)
    with pytest.raises(DiagnosticError):  # «@» sin ancla
        capture.parse_entry("@1; 2; 3", anchor=None, cursor=c)


def test_r15_snap_ties_resolve_to_the_lowest_index() -> None:
    nodes = [(0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (9.0, 0.0, 0.0)]
    result = capture.snap(
        (1.0, 1.0),
        top_ray((1.0, 1.0)),
        project=top_project,
        nodes=nodes,
        members=[],
        plane=WorkPlane(),
    )
    assert result is not None
    assert result.node == 0


@pytest.mark.parametrize("mode", [Mode.NODE, Mode.MEMBER])
def test_escape_or_mode_change_with_the_button_down_leaves_no_trace(mode: Mode) -> None:
    hit = Capture((1.0, 0.0, 0.0))
    abandons: list[Callable[[GestureMachine], object]] = [
        lambda m: m.cancel(),
        lambda m: m.set_mode(Mode.NODE),
    ]
    for abandon in abandons:
        machine = GestureMachine(mode)
        machine.press((5.0, 5.0), hit)
        abandon(machine)
        assert machine.release((5.0, 5.0), hit) is None
        assert machine.anchor is None
        assert state(machine) is GestureState.IDLE


def test_mode_change_during_a_drag_does_not_anchor_or_create() -> None:
    editor, _, _ = setup()
    hit = origin_node(editor)
    assert hit.node_id is not None
    machine = GestureMachine()
    machine.press((0.0, 0.0), hit, selected_nodes={hit.node_id})
    machine.set_mode(Mode.MEMBER)
    assert machine.release((0.0, 0.0), hit) is None
    assert machine.anchor is None


def test_typing_with_the_button_down_does_not_leave_a_phantom_anchor() -> None:
    machine = GestureMachine(Mode.MEMBER)
    click(machine, Capture((0.0, 0.0, 0.0)))
    machine.press((9.0, 9.0), Capture((0.0, 5.0, 0.0)))
    assert isinstance(machine.type_entry("@2; 0; 0"), CreateMember)
    assert machine.release((9.0, 9.0), Capture((0.0, 5.0, 0.0))) is None
    assert machine.anchor is None


def test_a_lost_release_does_not_leave_a_stale_drag() -> None:
    editor, _, _ = setup()
    hit = origin_node(editor)
    assert hit.node_id is not None
    machine = GestureMachine()
    machine.press((0.0, 0.0), hit, selected_nodes={hit.node_id})  # su release se pierde
    machine.press((50.0, 50.0), Capture((3.0, 3.0, 0.0)))  # cae en vacío
    assert state(machine) is GestureState.IDLE
    assert machine.release((90.0, 90.0), Capture((4.0, 4.0, 0.0))) is None


def test_release_without_press_does_nothing() -> None:
    assert GestureMachine(Mode.NODE).release((0.0, 0.0), Capture((1.0, 1.0, 1.0))) is None


def test_node_creation_and_move_onto_an_existing_node_are_rejected_without_trace() -> None:
    editor, _, _ = setup()
    a = origin_node(editor)
    b = editor.add_node(M(2, "m"), M(0, "m"), M(0, "m"))
    before = digest(editor)
    for action in (
        CreateNode((0.0, 0.0, 5e-7)),  # dentro de la longitud mínima de barra
        MoveNode(b.id, (0.0, 0.0, 0.0)),
    ):
        with pytest.raises(DiagnosticError) as info:
            gestures.apply(action, editor)
        assert "Ya existe un nudo" in render_message(info.value.diagnostic)
    assert digest(editor) == before
    assert a.node_id is not None
    gestures.apply(MoveNode(a.node_id, (0.0, 0.0, 0.0)), editor)  # sobre sí mismo: no es duplicado
    gestures.apply(CreateNode((0.0, 0.0, 2e-6)), editor)  # fuera de la tolerancia


def test_gesture_tolerance_is_the_domain_minimum_member_length() -> None:
    assert gestures.GESTURE_TOLERANCE == MIN_MEMBER_LENGTH
    machine = GestureMachine(Mode.MEMBER)
    click(machine, Capture((0.0, 0.0, 0.0)))
    assert click(machine, Capture((5e-7, 0.0, 0.0))) is None  # no gasta el primer punto
    assert state(machine) is GestureState.ANCHORED
