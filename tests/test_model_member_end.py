"""``StructuralModel.set_member_end``: revisiones e independencia I/J (T-010, T-CON-002)."""

import pytest

from veritas.core.connections.end import Dof, DofState, EndConnection, MemberEnd
from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError
from veritas.core.geometry.entities import Member
from veritas.core.geometry.model import StructuralModel
from veritas.core.materials.material import MaterialFamily
from veritas.core.units import Quantity


def m(value: float) -> Quantity:
    return Quantity.of(value, "m")


def setup() -> tuple[StructuralModel, Member, Member]:
    """Dos barras que comparten el nudo N2."""
    model = StructuralModel()
    material = model.add_material(
        name="Acero",
        family=MaterialFamily.STEEL,
        source="test",
        elastic_modulus=Quantity.of(210, "GPa"),
        shear_modulus=Quantity.of(81, "GPa"),
        density=Quantity.of(7850, "kg/m³"),
    )
    section = model.add_rectangular_section(b=m(0.2), h=m(0.4), name="R")
    n1, n2, n3 = (model.add_node(m(x), m(0), m(0)) for x in (0, 3, 6))
    b1 = model.add_member(n1.id, n2.id, material=material.ref, section=section.ref, label="B1")
    b2 = model.add_member(n2.id, n3.id, material=material.ref, section=section.ref, label="B2")
    return model, b1, b2


def test_new_members_are_rigid_at_both_ends() -> None:  # T-CON-001
    _, b1, b2 = setup()
    for member in (b1, b2):
        assert member.end_i == EndConnection.rigid()
        assert member.end_j == EndConnection.rigid()


def test_release_one_end_changes_only_that_end() -> None:  # T-CON-002
    model, b1, b2 = setup()
    before = model.model_revision
    released = EndConnection.rigid().with_dof(Dof.RY, DofState.RELEASED)
    updated = model.set_member_end(b1.id, MemberEnd.J, released)
    assert updated.revision == b1.revision + 1
    assert updated.id == b1.id
    assert updated.end_j == released
    assert updated.end_i == EndConnection.rigid()
    assert (updated.material, updated.section, updated.orientation) == (
        b1.material,
        b1.section,
        b1.orientation,
    )
    assert model.member(b1.id) is updated
    assert model.member(b2.id) is b2  # La otra barra del nudo N2 no cambia.
    assert model.model_revision == before + 1


def test_end_i_spring_then_identical_is_no_change() -> None:
    model, b1, _ = setup()
    spring = EndConnection.semi_rigid(Dof.RY, Quantity.of(5000, "kN·m/rad"))
    first = model.set_member_end(b1.id, MemberEnd.I, spring)
    revision = model.model_revision
    again = model.set_member_end(b1.id, MemberEnd.I, spring)
    assert again is first
    assert model.model_revision == revision
    assert first.end_i == spring
    assert first.end_j == EndConnection.rigid()


def test_back_to_rigid_creates_another_revision() -> None:
    model, b1, _ = setup()
    model.set_member_end(b1.id, MemberEnd.I, EndConnection.pinned())
    restored = model.set_member_end(b1.id, MemberEnd.I, EndConnection.rigid())
    assert restored.revision == b1.revision + 2
    assert restored.end_i == EndConnection.rigid()


def test_set_member_end_rejects_invalid_arguments() -> None:
    model, b1, _ = setup()
    revision = model.model_revision
    with pytest.raises(InputTypeError):
        model.set_member_end(b1.id, "I", EndConnection.pinned())  # type: ignore[arg-type]
    with pytest.raises(InputTypeError):
        model.set_member_end(b1.id, MemberEnd.I, None)  # type: ignore[arg-type]
    with pytest.raises(InputValueError) as info:
        model.set_member_end(b1.node_i, MemberEnd.I, EndConnection.pinned())
    assert info.value.code is DiagnosticCode.REFERENCE_NOT_FOUND
    assert model.model_revision == revision
    assert model.member(b1.id) is b1
