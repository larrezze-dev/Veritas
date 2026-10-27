"""Apoyos en StructuralModel: tabla D5 del plan T-005 (revisiones y model_revision)."""

from collections.abc import Callable
from uuid import uuid4

import pytest

from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError
from veritas.core.geometry.entities import Node
from veritas.core.geometry.model import KEEP, StructuralModel
from veritas.core.materials.material import MaterialFamily
from veritas.core.supports.support import (
    Restraints,
    SupportBasis,
    SupportDof,
    build_restraints,
    fixed_restraints,
    pinned_restraints,
    roller_restraints,
)
from veritas.core.units import Quantity

D = DiagnosticCode


def m(value: float) -> Quantity:
    return Quantity.of(value, "m")


def make_model() -> tuple[StructuralModel, Node, Node]:
    model = StructuralModel()
    a = model.add_node(m(0), m(0), m(0), label="N1")
    b = model.add_node(m(3), m(0), m(0), label="N2")
    return model, a, b


def test_set_support_creates_revision_1_and_bumps_model_revision() -> None:
    model, a, _ = make_model()
    before = model.model_revision
    support = model.set_support(a.id, pinned_restraints())
    assert support.revision == 1
    assert support.label == ""
    assert support.node_id == a.id
    assert support.basis is SupportBasis.GLOBAL
    assert model.model_revision == before + 1
    assert model.support_at(a.id) is support
    assert model.supports[support.id] is support


def test_new_support_with_keep_label_is_empty_and_explicit_label_is_stored() -> None:
    model, a, b = make_model()
    assert model.set_support(a.id, fixed_restraints(), label=KEEP).label == ""
    assert model.set_support(b.id, fixed_restraints(), label="A2").label == "A2"


def test_changed_states_keep_label_and_id() -> None:
    model, a, _ = make_model()
    first = model.set_support(a.id, pinned_restraints(), label="A1")
    before = model.model_revision
    second = model.set_support(a.id, fixed_restraints())
    assert second.id == first.id
    assert second.revision == 2
    assert second.label == "A1"
    assert second.restraints == fixed_restraints()
    assert model.model_revision == before + 1
    assert model.support_at(a.id) is second
    assert len(model.supports) == 1


def test_changed_states_and_label() -> None:
    model, a, _ = make_model()
    first = model.set_support(a.id, pinned_restraints(), label="A1")
    before = model.model_revision
    second = model.set_support(a.id, fixed_restraints(), label="B")
    assert (second.id, second.revision, second.label) == (first.id, 2, "B")
    assert model.model_revision == before + 1


def test_only_label_changes_revision_without_model_revision() -> None:
    model, a, _ = make_model()
    first = model.set_support(a.id, pinned_restraints(), label="A1")
    before = model.model_revision
    second = model.set_support(a.id, pinned_restraints(), label="A9")
    assert (second.id, second.revision, second.label) == (first.id, 2, "A9")
    assert second.restraints == first.restraints
    assert model.model_revision == before
    assert model.support_at(a.id) is second


def test_identical_content_returns_same_object_without_change() -> None:
    model, a, _ = make_model()
    first = model.set_support(a.id, pinned_restraints(), label="A1")
    before = model.model_revision
    assert model.set_support(a.id, pinned_restraints()) is first  # KEEP
    assert model.set_support(a.id, pinned_restraints(), label="A1") is first
    assert first.revision == 1
    assert model.model_revision == before
    assert model.support_at(a.id) is first


def test_identical_inclined_content_is_unchanged() -> None:
    model, a, _ = make_model()
    basis = SupportBasis.inclined((1, 0, 1), (0, 0, 1))
    first = model.set_support(a.id, roller_restraints(), basis=basis)
    before = model.model_revision
    again = model.set_support(
        a.id, roller_restraints(), basis=SupportBasis.inclined((1, 0, 1), (0, 0, 1))
    )
    assert again is first
    assert model.model_revision == before


def test_only_basis_change_counts_as_analytical() -> None:
    model, a, _ = make_model()
    first = model.set_support(a.id, roller_restraints())
    before = model.model_revision
    second = model.set_support(
        a.id, roller_restraints(), basis=SupportBasis.inclined((1, 0, 1), (0, 0, 1))
    )
    assert second.revision == first.revision + 1
    assert second.restraints == first.restraints
    assert model.model_revision == before + 1


def test_only_stiffness_change_counts_as_analytical() -> None:
    model, a, _ = make_model()

    def with_spring(kn_per_m: float) -> Restraints:
        return build_restraints(
            [SupportDof.UX, SupportDof.UY], {SupportDof.UZ: Quantity.of(kn_per_m, "kN/m")}
        )

    first = model.set_support(a.id, with_spring(5000))
    before = model.model_revision
    second = model.set_support(a.id, with_spring(6000))
    assert second.revision == first.revision + 1
    assert second.restraint(SupportDof.UZ).stiffness == 6e6
    assert model.model_revision == before + 1
    assert model.set_support(a.id, with_spring(6000)) is second
    assert model.model_revision == before + 1


def test_only_roller_axis_change_counts_as_analytical() -> None:
    model, a, _ = make_model()
    first = model.set_support(a.id, roller_restraints(SupportDof.UZ))
    before = model.model_revision
    second = model.set_support(a.id, roller_restraints(SupportDof.UX))
    assert second.revision == first.revision + 1
    assert model.model_revision == before + 1


def test_relabel_support() -> None:
    model, a, _ = make_model()
    first = model.set_support(a.id, pinned_restraints(), label="A1")
    before = model.model_revision
    second = model.relabel_support(a.id, "Nuevo")
    assert (second.id, second.revision, second.label) == (first.id, 2, "Nuevo")
    assert second.restraints == first.restraints
    assert model.model_revision == before
    assert model.support_at(a.id) is second
    assert model.supports[first.id] is second
    assert model.relabel_support(a.id, "Nuevo") is second  # Sin cambio.
    assert second.revision == 2


def test_relabel_rejects_non_text_without_effects() -> None:
    model, a, _ = make_model()
    support = model.set_support(a.id, pinned_restraints())
    before = model.model_revision
    with pytest.raises(InputTypeError) as info:
        model.relabel_support(a.id, 5)  # type: ignore[arg-type]
    assert info.value.code is D.INPUT_INVALID_TYPE
    assert model.support_at(a.id) is support
    assert model.model_revision == before


def test_remove_support_bumps_model_revision_and_clears_lookup() -> None:
    model, a, b = make_model()
    support = model.set_support(a.id, pinned_restraints(), label="A1")
    other = model.set_support(b.id, fixed_restraints())
    before = model.model_revision
    removed = model.remove_support(a.id)
    assert removed is support
    assert model.model_revision == before + 1
    assert model.support_at(a.id) is None
    assert support.id not in model.supports
    assert model.supports == {other.id: other}
    assert model.support_at(b.id) is other


@pytest.mark.parametrize("restraints", [pinned_restraints(), fixed_restraints()])
def test_set_after_remove_creates_new_id_even_with_identical_states(
    restraints: Restraints,
) -> None:
    model, a, _ = make_model()
    model.set_support(a.id, pinned_restraints(), label="A1")
    removed = model.remove_support(a.id)
    before = model.model_revision
    again = model.set_support(a.id, restraints)
    assert again.id != removed.id
    assert again.revision == 1
    assert again.label == ""
    assert model.model_revision == before + 1
    assert model.support_at(a.id) is again
    assert set(model.supports) == {again.id}


def test_set_after_remove_identical_content_and_label() -> None:
    model, a, _ = make_model()
    first = model.set_support(a.id, pinned_restraints(), label="A1")
    model.remove_support(a.id)
    again = model.set_support(a.id, pinned_restraints(), label="A1")
    assert again.id != first.id
    assert again.revision == 1
    assert again.restraints == first.restraints


def test_support_ids_are_unique_per_node() -> None:
    model, a, b = make_model()
    sa = model.set_support(a.id, pinned_restraints())
    sb = model.set_support(b.id, pinned_restraints())
    assert sa.id != sb.id
    assert len(model.supports) == 2


def test_unknown_node_is_node_not_found_in_every_operation() -> None:
    model, a, _ = make_model()
    model.set_support(a.id, pinned_restraints())
    ghost = uuid4()
    before = model.model_revision
    calls: list[Callable[[], object]] = [
        lambda: model.set_support(ghost, pinned_restraints()),
        lambda: model.relabel_support(ghost, "x"),
        lambda: model.remove_support(ghost),
        lambda: model.support_at(ghost),
    ]
    for call in calls:
        with pytest.raises(InputValueError) as info:
            call()
        assert info.value.code is D.REFERENCE_NOT_FOUND
        assert dict(info.value.diagnostic.params)["kind"] == "nudo"
    assert model.model_revision == before
    assert len(model.supports) == 1


@pytest.mark.parametrize("operation", ["relabel", "remove"])
def test_node_without_support_is_support_not_found(operation: str) -> None:
    model, a, _ = make_model()
    before = model.model_revision
    call: Callable[[], object] = (
        (lambda: model.relabel_support(a.id, "x"))
        if operation == "relabel"
        else (lambda: model.remove_support(a.id))
    )
    with pytest.raises(InputValueError) as info:
        call()
    assert info.value.code is D.REFERENCE_NOT_FOUND
    assert dict(info.value.diagnostic.params)["kind"] == "apoyo"
    assert model.model_revision == before
    assert not model.supports


def test_second_remove_is_support_not_found() -> None:
    model, a, _ = make_model()
    model.set_support(a.id, pinned_restraints())
    model.remove_support(a.id)
    with pytest.raises(InputValueError) as info:
        model.remove_support(a.id)
    assert dict(info.value.diagnostic.params)["kind"] == "apoyo"


def test_rejected_set_support_has_no_effects_on_new_support() -> None:
    model, a, _ = make_model()
    before = model.model_revision
    for bad in [(), (None,) * 6, [True] * 6, tuple(pinned_restraints())[:5]]:
        with pytest.raises((InputTypeError, InputValueError)):
            model.set_support(a.id, bad)
    with pytest.raises(InputTypeError):
        model.set_support(a.id, pinned_restraints(), basis="GLOBAL")  # type: ignore[arg-type]
    assert model.model_revision == before
    assert model.support_at(a.id) is None
    assert not model.supports


def test_rejected_set_support_has_no_effects_on_existing_support() -> None:
    model, a, _ = make_model()
    support = model.set_support(a.id, pinned_restraints(), label="A1")
    before = model.model_revision
    with pytest.raises(InputValueError) as info:
        model.set_support(a.id, (build_restraints([SupportDof.UX])[1],) * 6)  # seis libres
    assert info.value.code is D.INPUT_OUT_OF_RANGE
    with pytest.raises(InputTypeError):
        model.set_support(a.id, fixed_restraints(), basis=object())  # type: ignore[arg-type]
    with pytest.raises(InputTypeError):
        model.set_support(a.id, fixed_restraints(), label=7)  # type: ignore[arg-type]
    assert model.support_at(a.id) is support
    assert support.revision == 1
    assert model.supports[support.id] is support
    assert model.model_revision == before


def test_move_node_does_not_alter_support() -> None:
    model, a, _ = make_model()
    support = model.set_support(a.id, pinned_restraints(), label="A1")
    before = model.model_revision
    model.move_node(a.id, m(1), m(2), m(3))
    assert model.support_at(a.id) is support
    assert support.revision == 1
    assert support.node_id == a.id
    assert model.model_revision == before + 1  # Solo por el movimiento del nudo.


def test_support_in_node_without_members_is_accepted() -> None:
    model, a, _ = make_model()
    assert not model.members
    support = model.set_support(a.id, roller_restraints())
    assert model.support_at(a.id) is support


def test_support_in_node_with_member_is_accepted_and_independent() -> None:
    model, a, b = make_model()
    steel = model.add_material(
        name="Acero",
        family=MaterialFamily.STEEL,
        source="test",
        elastic_modulus=Quantity.of(210, "GPa"),
        shear_modulus=Quantity.of(81, "GPa"),
        density=Quantity.of(7850, "kg/m³"),
    )
    section = model.add_rectangular_section(b=m(0.2), h=m(0.4), name="R")
    member = model.add_member(a.id, b.id, material=steel.ref, section=section.ref)
    support = model.set_support(a.id, fixed_restraints())
    assert model.member(member.id) is member
    assert model.node(a.id).revision == 1
    assert model.support_at(a.id) is support


def test_supports_view_is_read_only_and_consistent_after_remove_and_set() -> None:
    model, a, b = make_model()
    s1 = model.set_support(a.id, pinned_restraints())
    view = model.supports
    with pytest.raises(TypeError):
        view[uuid4()] = s1  # type: ignore[index]
    model.remove_support(a.id)
    s2 = model.set_support(a.id, pinned_restraints())
    s3 = model.set_support(b.id, roller_restraints())
    assert set(model.supports) == {s2.id, s3.id}
    assert model.support_at(a.id) is s2
    assert model.support_at(b.id) is s3
    for node_id in (a.id, b.id):
        found = model.support_at(node_id)
        assert found is not None
        assert model.supports[found.id] is found
        assert found.node_id == node_id
