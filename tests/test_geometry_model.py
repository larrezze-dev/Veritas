"""Nudos, barras, extremos I/J y modelo en memoria (plan T-003; T-GEO-001, T-CON-001)."""

import dataclasses
import math
from uuid import UUID, uuid4

import pytest

from veritas.core.connections.end import Dof, DofState, EndConnection
from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError
from veritas.core.geometry.entities import Member, Node
from veritas.core.geometry.local_axes import MIN_MEMBER_LENGTH, ReferenceSource
from veritas.core.geometry.model import StructuralModel
from veritas.core.geometry.vector import Vec3
from veritas.core.units import Quantity

TOL = 1e-14


def m(value: float) -> Quantity:
    return Quantity.of(value, "m")


def assert_vec(actual: Vec3, expected: Vec3) -> None:
    assert (actual - expected).norm() <= TOL, f"{actual} != {expected}"


def frame() -> tuple[StructuralModel, Node, Node, Member]:
    model = StructuralModel()
    a = model.add_node(m(0.0), m(0.0), m(0.0), label="A")
    b = model.add_node(m(2.0), m(0.0), m(0.0), label="B")
    member = model.add_member(a.id, b.id, label="V1")
    return model, a, b, member


# --- Nudos -----------------------------------------------------------------------------


def test_node_identity_and_units() -> None:
    model = StructuralModel()
    node = model.add_node(Quantity.of(150, "cm"), Quantity.of(-20, "mm"), m(3.0), label="N1")
    assert isinstance(node.id, UUID)
    assert node.id.version == 4
    assert str(node.id) == str(node.id).lower()
    assert node.revision == 1
    assert node.position == Vec3(1.5, -0.02, 3.0)
    assert model.model_revision == 1
    assert model.node(node.id) is node


def test_nodes_get_distinct_ids() -> None:
    model = StructuralModel()
    ids = {model.add_node(m(i), m(0.0), m(0.0)).id for i in range(50)}
    assert len(ids) == 50


@pytest.mark.parametrize(
    ("x", "error", "code"),
    [
        (Quantity.of(1, "kN"), InputValueError, DiagnosticCode.UNITS_INCOMPATIBLE),
        (1.0, InputTypeError, DiagnosticCode.INPUT_INVALID_TYPE),
    ],
)
def test_node_rejects_non_length(x: object, error: type[Exception], code: DiagnosticCode) -> None:
    model = StructuralModel()
    with pytest.raises(error) as info:
        model.add_node(x, m(0.0), m(0.0))  # type: ignore[arg-type]
    assert info.value.code is code  # type: ignore[attr-defined]
    assert model.model_revision == 0
    assert not model.nodes


@pytest.mark.parametrize("bad", [math.nan, math.inf])
def test_nonfinite_coordinates_rejected_at_quantity(bad: float) -> None:
    with pytest.raises(InputValueError) as info:
        m(bad)
    assert info.value.code is DiagnosticCode.INPUT_NONFINITE


def test_node_validation() -> None:
    with pytest.raises(TypeError):
        Node(uuid4(), True, "", Vec3(0.0, 0.0, 0.0))
    with pytest.raises(ValueError, match=">= 1"):
        Node(uuid4(), 0, "", Vec3(0.0, 0.0, 0.0))
    with pytest.raises(TypeError, match="v4"):
        Node(UUID(int=1), 1, "", Vec3(0.0, 0.0, 0.0))
    with pytest.raises(InputTypeError):
        Vec3(True, 0.0, 0.0)


# --- Barras ----------------------------------------------------------------------------


def test_member_defaults_rigid_ends() -> None:  # T-CON-001
    model, a, b, member = frame()
    assert member.revision == 1
    assert (member.node_i, member.node_j) == (a.id, b.id)
    for end in (member.end_i, member.end_j):
        assert end == EndConnection.rigid()
        assert all(end.state(dof) is DofState.RIGID for dof in Dof)
        assert len(end.states) == 6
    assert [dof.value for dof in Dof] == ["Ux", "Uy", "Uz", "Rx", "Ry", "Rz"]
    assert member.end_i is not member.end_j
    assert model.model_revision == 3


def test_member_stores_resolved_reference() -> None:
    model, _, _, member = frame()
    assert member.orientation.source is ReferenceSource.DEFAULT_Z
    assert member.orientation.reference == Vec3(0.0, 0.0, 1.0)
    axes = model.axes(member.id)
    assert_vec(axes.ey, Vec3(0.0, 1.0, 0.0))


def test_member_with_explicit_reference_and_alpha() -> None:
    model = StructuralModel()
    a = model.add_node(m(0.0), m(0.0), m(0.0))
    b = model.add_node(m(2.0), m(0.0), m(0.0))
    member = model.add_member(a.id, b.id, reference=(0, 0, 3), alpha=Quantity.of(90, "deg"))
    assert member.orientation.source is ReferenceSource.EXPLICIT
    assert member.orientation.reference == Vec3(0.0, 0.0, 1.0)
    assert member.orientation.alpha == pytest.approx(math.pi / 2, abs=0.0)
    axes = model.axes(member.id)
    assert_vec(axes.ey, Vec3(0.0, 0.0, 1.0))
    assert_vec(axes.ez, Vec3(0.0, -1.0, 0.0))


def test_full_turn_in_degrees_is_accepted() -> None:
    model = StructuralModel()
    a = model.add_node(m(0.0), m(0.0), m(0.0))
    b = model.add_node(m(2.0), m(0.0), m(0.0))
    member = model.add_member(a.id, b.id, alpha=Quantity.of(360, "deg"))
    assert member.orientation.alpha == 2 * math.pi


@pytest.mark.parametrize(
    ("kwargs", "code"),
    [
        ({"alpha": Quantity.of(1, "m")}, DiagnosticCode.UNITS_INCOMPATIBLE),
        ({"alpha": Quantity.of(361, "deg")}, DiagnosticCode.GEOMETRY_INVALID_ORIENTATION),
        ({"reference": (0, 1, 0)}, DiagnosticCode.GEOMETRY_INVALID_ORIENTATION),
        ({"reference": (m(0), m(0), m(1))}, DiagnosticCode.UNITS_INCOMPATIBLE),
        ({"reference": (0, math.nan, 1)}, DiagnosticCode.INPUT_NONFINITE),
        ({"reference": (0, 0, 0)}, DiagnosticCode.GEOMETRY_INVALID_ORIENTATION),
    ],
)
def test_member_rejects_invalid_orientation(
    kwargs: dict[str, object], code: DiagnosticCode
) -> None:
    model = StructuralModel()
    a = model.add_node(m(0.0), m(0.0), m(0.0))
    b = model.add_node(m(0.0), m(2.0), m(0.0))
    with pytest.raises(InputValueError) as info:
        model.add_member(a.id, b.id, **kwargs)  # type: ignore[arg-type]
    assert info.value.code is code
    assert model.model_revision == 2
    assert not model.members


def test_reference_accepts_dimensionless_quantities() -> None:
    model = StructuralModel()
    a = model.add_node(m(0.0), m(0.0), m(0.0))
    b = model.add_node(m(2.0), m(0.0), m(0.0))
    one = Quantity.of(1, "1")
    member = model.add_member(a.id, b.id, reference=(one * 0, one * 0, one))
    assert member.orientation.reference == Vec3(0.0, 0.0, 1.0)


def test_reference_with_wrong_shape_is_rejected() -> None:
    model = StructuralModel()
    a = model.add_node(m(0.0), m(0.0), m(0.0))
    b = model.add_node(m(2.0), m(0.0), m(0.0))
    for bad in ((0, 1), "0,0,1"):
        with pytest.raises(InputTypeError):
            model.add_member(a.id, b.id, reference=bad)


def test_coincident_nodes_rejected() -> None:  # T-GEO-001
    model = StructuralModel()
    a = model.add_node(m(1.0), m(1.0), m(1.0))
    b = model.add_node(m(1.0), m(1.0), m(1.0))
    with pytest.raises(InputValueError) as info:
        model.add_member(a.id, b.id)
    assert info.value.code is DiagnosticCode.GEOMETRY_ZERO_LENGTH
    location = info.value.diagnostic.location
    assert location is not None
    assert location.revision == 1
    assert location.entity_id is not None
    assert not model.members


def test_min_length_boundary_in_metres() -> None:
    model = StructuralModel()
    a = model.add_node(m(0.0), m(0.0), m(0.0))
    ok = model.add_node(m(MIN_MEMBER_LENGTH), m(0.0), m(0.0))
    short = model.add_node(m(math.nextafter(MIN_MEMBER_LENGTH, 0.0)), m(0.0), m(0.0))
    assert model.axes(model.add_member(a.id, ok.id).id).length == MIN_MEMBER_LENGTH
    with pytest.raises(InputValueError) as info:
        model.add_member(a.id, short.id)
    assert info.value.code is DiagnosticCode.GEOMETRY_ZERO_LENGTH


def test_same_node_rejected() -> None:
    model = StructuralModel()
    a = model.add_node(m(0.0), m(0.0), m(0.0))
    with pytest.raises(InputValueError) as info:
        model.add_member(a.id, a.id)
    assert info.value.code is DiagnosticCode.GEOMETRY_ZERO_LENGTH
    assert info.value.diagnostic.message_key == "geometry.same_node"
    with pytest.raises(InputValueError):
        Member(uuid4(), 1, "", a.id, a.id, frame()[3].orientation)


def test_unknown_references() -> None:
    model, a, _, _ = frame()
    for call in (
        lambda: model.add_member(a.id, uuid4()),
        lambda: model.node(uuid4()),
        lambda: model.member(uuid4()),
        lambda: model.move_node(uuid4(), m(0), m(0), m(0)),
        lambda: model.set_member_orientation(uuid4()),
    ):
        with pytest.raises(InputValueError) as info:
            call()
        assert info.value.code is DiagnosticCode.REFERENCE_NOT_FOUND
    assert model.model_revision == 3


# --- Edición ---------------------------------------------------------------------------


def test_move_node_recomputes_with_stored_reference() -> None:
    model, _, b, member = frame()
    moved = model.move_node(b.id, m(2.0), m(2.0), m(0.0))
    assert moved.id == b.id
    assert moved.revision == 2
    assert model.model_revision == 4
    assert model.member(member.id) is member  # La revisión de barra no cambia (D2).
    axes = model.axes(member.id)
    r = 1.0 / math.sqrt(2.0)
    assert_vec(axes.ex, Vec3(r, r, 0.0))
    assert_vec(axes.ez, Vec3(0.0, 0.0, 1.0))
    assert b.position == Vec3(2.0, 0.0, 0.0)  # La revisión histórica no se modifica.


def test_stored_reference_degenerates_and_is_rejected_atomically() -> None:
    model, a, b, member = frame()
    with pytest.raises(InputValueError) as info:
        model.move_node(b.id, m(0.0), m(0.0), m(3.0))  # La barra quedaría vertical.
    assert info.value.code is DiagnosticCode.GEOMETRY_INVALID_ORIENTATION
    assert info.value.diagnostic.message_key == "geometry.stored_reference_degenerate"
    assert info.value.diagnostic.location is not None
    assert info.value.diagnostic.location.entity_id == member.id
    assert model.node(b.id) is b
    assert model.member(member.id) is member
    assert model.model_revision == 3
    # Flujo en dos pasos: reorientar (referencia válida para ambas geometrías) y mover.
    reoriented = model.set_member_orientation(member.id, reference=(0, 1, 0))
    assert reoriented.revision == 2
    model.move_node(b.id, m(0.0), m(0.0), m(3.0))
    assert model.model_revision == 5
    assert_vec(model.axes(member.id).ez, Vec3(0.0, 1.0, 0.0))
    assert model.node(a.id) is a


def test_move_rejected_when_length_vanishes() -> None:
    model, _, b, _ = frame()
    with pytest.raises(InputValueError) as info:
        model.move_node(b.id, m(0.0), m(0.0), m(0.0))
    assert info.value.code is DiagnosticCode.GEOMETRY_ZERO_LENGTH
    assert model.node(b.id) is b
    assert model.model_revision == 3


def test_fallback_member_keeps_its_reference_after_edit() -> None:
    model = StructuralModel()
    a = model.add_node(m(0.0), m(0.0), m(0.0))
    b = model.add_node(m(0.0), m(0.0), m(3.0))
    column = model.add_member(a.id, b.id)
    assert column.orientation.source is ReferenceSource.FALLBACK_NEG_X
    model.move_node(b.id, m(0.5), m(0.0), m(3.0))  # Ya no es vertical: no se vuelve a +Z.
    assert model.member(column.id).orientation.reference == Vec3(-1.0, 0.0, 0.0)
    axes = model.axes(column.id)
    assert axes.ez.x < 0.0
    updated = model.set_member_orientation(column.id)  # Reorientación explícita: +Z.
    assert updated.revision == 2
    assert updated.orientation.source is ReferenceSource.DEFAULT_Z
    assert model.member(column.id).orientation.alpha == 0.0


def test_relabel_does_not_change_model_revision() -> None:
    model, a, _, _ = frame()
    renamed = model.relabel_node(a.id, "Apoyo")
    assert (renamed.label, renamed.revision, renamed.position) == ("Apoyo", 2, a.position)
    assert model.model_revision == 3


def test_revisions_are_immutable() -> None:
    _, a, _, member = frame()
    with pytest.raises(dataclasses.FrozenInstanceError):
        a.position = Vec3(9.0, 9.0, 9.0)  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        member.end_i = EndConnection.rigid()  # type: ignore[misc]
    with pytest.raises(TypeError):
        StructuralModel().nodes[a.id] = a  # type: ignore[index]


def test_end_connection_validation() -> None:
    with pytest.raises(TypeError):
        EndConnection((DofState.RIGID,) * 5)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        EndConnection(("RIGID",) * 6)  # type: ignore[arg-type]


def test_reorientation_keeps_alpha_unless_given() -> None:
    model = StructuralModel()
    a = model.add_node(m(0.0), m(0.0), m(0.0))
    b = model.add_node(m(2.0), m(0.0), m(0.0))
    member = model.add_member(a.id, b.id, alpha=Quantity.of(30, "deg"))
    updated = model.set_member_orientation(member.id, reference=(0, 1, 1))
    assert updated.orientation.alpha == member.orientation.alpha
    assert updated.orientation.source is ReferenceSource.EXPLICIT
    zeroed = model.set_member_orientation(member.id, alpha=Quantity.of(0, "rad"))
    assert zeroed.orientation.alpha == 0.0
    assert zeroed.orientation.source is ReferenceSource.DEFAULT_Z  # Sin referencia: +Z.
    assert zeroed.revision == 3


def test_operations_without_change_do_not_create_revisions() -> None:
    model, a, b, member = frame()
    assert model.move_node(b.id, m(2.0), m(0.0), m(0.0)) is b
    assert model.relabel_node(a.id, "A") is a
    assert model.set_member_orientation(member.id) is member
    assert model.model_revision == 3
