"""Asientos de apoyo en StructuralModel y rechazo en los adaptadores (plan T-016, E1)."""

import math
from uuid import uuid4

import pytest

from fem_support import Frame, inclined
from veritas.core.diagnostics import DiagnosticCode, InputValueError
from veritas.core.geometry.entities import Node
from veritas.core.geometry.model import StructuralModel
from veritas.core.loads.load_case import LoadCategory
from veritas.core.materials.material import MaterialFamily
from veritas.core.supports.support import (
    FIXED,
    FREE,
    DofRestraint,
    SupportBasis,
    SupportDof,
    build_restraints,
    fixed_restraints,
    pinned_restraints,
)
from veritas.core.units import Quantity
from veritas.fem.adapters.planar_xz import translate_planar_xz
from veritas.fem.adapters.protocol import PlanarSettlement
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.protocol import SpatialSettlement
from veritas.fem.spatial.translator import translate_spatial

D = DiagnosticCode
V = LoadCategory.VARIABLE


def m(value: float) -> Quantity:
    return Quantity.of(value, "m")


def make_model() -> tuple[StructuralModel, Node, Node]:
    model = StructuralModel()
    material = model.add_material(
        name="Sintético",
        family=MaterialFamily.STEEL,
        source="test",
        elastic_modulus=Quantity.of(210, "GPa"),
        shear_modulus=Quantity.of(81, "GPa"),
        density=Quantity.of(1000, "kg/m³"),
    )
    section = model.add_rectangular_section(b=m(0.2), h=m(0.4))
    a = model.add_node(m(0), m(0), m(0))
    b = model.add_node(m(2), m(0), m(0))
    model.add_member(a.id, b.id, material=material.ref, section=section.ref)
    return model, a, b


def expect(info: pytest.ExceptionInfo[InputValueError], code: DiagnosticCode, key: str) -> None:
    assert info.value.code is code
    assert info.value.diagnostic.message_key == key


# --- Positivos --------------------------------------------------------------------------


def test_settlement_global_basis_converts_units_to_si() -> None:
    model, a, _ = make_model()
    support = model.set_support(a.id, fixed_restraints())
    case = model.add_load_case("Q1", V)
    before = model.model_revision
    load = model.add_support_settlement(
        case.id, a.id, uz=Quantity.of(-5, "mm"), ry=Quantity.of(0.5, "deg")
    )
    assert (load.revision, load.case_id, load.support_id) == (1, case.id, support.id)
    assert load.translation.components() == (0.0, 0.0, -0.005)
    assert load.rotation.y == pytest.approx(math.radians(0.5), rel=1e-15)
    assert model.model_revision == before + 1
    assert model.loads[load.id] is load
    assert model.loads_in_case(case.id) == (load,)


def test_settlement_in_inclined_basis_is_kept_in_support_basis() -> None:
    model, a, _ = make_model()
    model.set_support(a.id, fixed_restraints(), basis=inclined(30))
    case = model.add_load_case("Q1", V)
    load = model.add_support_settlement(case.id, a.id, ux=m(-0.002))
    assert load.translation.components() == (-0.002, 0.0, 0.0)  # sin girar a global


def test_settlement_on_fixed_dof_of_mixed_support() -> None:
    model, a, _ = make_model()
    restraints = build_restraints(
        fixed=[SupportDof.UZ], springs={SupportDof.UX: Quantity.of(1e6, "N/m")}
    )
    model.set_support(a.id, restraints)
    case = model.add_load_case("Q1", V)
    assert model.add_support_settlement(case.id, a.id, uz=m(-0.01)).translation.z == -0.01


def test_same_support_in_two_cases_and_two_supports_in_one_case() -> None:
    model, a, b = make_model()
    model.set_support(a.id, fixed_restraints())
    model.set_support(b.id, pinned_restraints())
    q1, q2 = model.add_load_case("Q1", V), model.add_load_case("Q2", V)
    for case in (q1, q2):
        model.add_support_settlement(case.id, a.id, uz=m(-0.01))
    model.add_support_settlement(q1.id, b.id, uz=m(-0.01))
    assert len(model.loads) == 3


def test_remove_load_retires_settlement_and_allows_new_one() -> None:
    model, a, _ = make_model()
    model.set_support(a.id, fixed_restraints())
    case = model.add_load_case("Q1", V)
    load = model.add_support_settlement(case.id, a.id, uz=m(-0.01))
    before = model.model_revision
    assert model.remove_load(load.id) is load
    assert model.model_revision == before + 1
    assert model.loads == {}
    again = model.add_support_settlement(case.id, a.id, uz=m(-0.02))
    assert again.id != load.id


def test_case_resultant_counts_settlement_without_force_or_moment() -> None:
    model, a, b = make_model()
    model.set_support(a.id, fixed_restraints())
    case = model.add_load_case("Q1", V)
    model.add_nodal_load(case.id, b.id, fz=Quantity.of(-10, "kN"), my=Quantity.of(1, "kN*m"))
    base = model.case_resultant(case.id)
    model.add_support_settlement(case.id, a.id, uz=m(-0.01), ry=Quantity.of(1, "deg"))
    after = model.case_resultant(case.id)
    assert after.force == base.force
    assert after.moment == base.moment
    assert (base.load_count, after.load_count) == (1, 2)


def test_case_with_only_settlement_is_not_empty() -> None:
    model, a, _ = make_model()
    model.set_support(a.id, fixed_restraints())
    case = model.add_load_case("Q1", V)
    model.add_support_settlement(case.id, a.id, uz=m(-0.01))
    result = model.case_resultant(case.id)
    assert result.load_count == 1
    assert [q.si_value for q in (*result.force, *result.moment)] == [0.0] * 6


def test_remove_load_case_retires_its_settlements() -> None:
    model, a, _ = make_model()
    model.set_support(a.id, fixed_restraints())
    case = model.add_load_case("Q1", V)
    load = model.add_support_settlement(case.id, a.id, uz=m(-0.01))
    _, removed = model.remove_load_case(case.id)
    assert removed == (load,)
    assert model.loads == {}
    model.remove_support(a.id)  # ya sin asientos


def test_relabel_support_with_settlement_is_allowed() -> None:
    model, a, _ = make_model()
    model.set_support(a.id, fixed_restraints())
    case = model.add_load_case("Q1", V)
    model.add_support_settlement(case.id, a.id, uz=m(-0.01))
    assert model.relabel_support(a.id, "A").label == "A"


def test_set_support_compatible_change_with_settlement_is_allowed() -> None:
    model, a, _ = make_model()
    model.set_support(a.id, fixed_restraints())
    case = model.add_load_case("Q1", V)
    model.add_support_settlement(case.id, a.id, uz=m(-0.01))
    revised = model.set_support(a.id, pinned_restraints())  # Uz sigue FIXED
    assert revised.revision == 2


# --- Negativas --------------------------------------------------------------------------


def test_node_without_support_is_reference_not_found() -> None:
    model, a, _ = make_model()
    case = model.add_load_case("Q1", V)
    before = model.model_revision
    with pytest.raises(InputValueError) as info:
        model.add_support_settlement(case.id, a.id, uz=m(-0.01))
    assert info.value.code is D.REFERENCE_NOT_FOUND
    assert model.model_revision == before


def test_unknown_case_is_reference_not_found() -> None:
    model, a, _ = make_model()
    model.set_support(a.id, fixed_restraints())
    with pytest.raises(InputValueError) as info:
        model.add_support_settlement(uuid4(), a.id, uz=m(-0.01))
    assert info.value.code is D.REFERENCE_NOT_FOUND


def test_unknown_node_is_reference_not_found() -> None:
    model, a, _ = make_model()
    model.set_support(a.id, fixed_restraints())
    case = model.add_load_case("Q1", V)
    with pytest.raises(InputValueError) as info:
        model.add_support_settlement(case.id, uuid4(), uz=m(-0.01))
    assert info.value.code is D.REFERENCE_NOT_FOUND


@pytest.mark.parametrize("state", ["FREE", "SPRING"])
def test_component_on_non_fixed_dof_is_rejected(state: str) -> None:
    model, a, _ = make_model()
    restraint = (
        FREE if state == "FREE" else DofRestraint.spring(SupportDof.UZ, Quantity.of(1e6, "N/m"))
    )
    states = [FIXED] * 6
    states[SupportDof.UZ.value] = restraint
    model.set_support(a.id, tuple(states))
    case = model.add_load_case("Q1", V)
    before = model.model_revision
    with pytest.raises(InputValueError) as info:
        model.add_support_settlement(case.id, a.id, uz=m(-0.01))
    expect(info, D.INPUT_OUT_OF_RANGE, "load.settlement_dof_not_fixed")
    assert dict(info.value.diagnostic.params) == {"dof": "Uz", "state": state}
    assert (model.model_revision, dict(model.loads)) == (before, {})


def test_rotation_on_free_rotation_is_rejected() -> None:
    model, a, _ = make_model()
    model.set_support(a.id, pinned_restraints())
    case = model.add_load_case("Q1", V)
    with pytest.raises(InputValueError) as info:
        model.add_support_settlement(case.id, a.id, uz=m(-0.01), ry=Quantity.of(1, "deg"))
    expect(info, D.INPUT_OUT_OF_RANGE, "load.settlement_dof_not_fixed")
    assert dict(info.value.diagnostic.params)["dof"] == "Ry"


def test_all_zero_wrong_dimension_and_subnormal() -> None:
    model, a, _ = make_model()
    model.set_support(a.id, fixed_restraints())
    case = model.add_load_case("Q1", V)
    with pytest.raises(InputValueError) as info:
        model.add_support_settlement(case.id, a.id)
    expect(info, D.INPUT_OUT_OF_RANGE, "load.all_zero")
    with pytest.raises(InputValueError) as info:
        model.add_support_settlement(case.id, a.id, uz=Quantity.of(1, "kN"))
    assert info.value.code is D.UNITS_INCOMPATIBLE
    with pytest.raises(InputValueError) as info:
        model.add_support_settlement(case.id, a.id, ry=m(1))  # longitud en un giro
    assert info.value.code is D.UNITS_INCOMPATIBLE
    with pytest.raises(InputValueError) as info:
        model.add_support_settlement(case.id, a.id, uz=m(1e-320))
    assert info.value.code is D.UNITS_UNREPRESENTABLE
    assert dict(model.loads) == {}


def test_duplicate_in_same_case_and_support_is_rejected() -> None:
    model, a, _ = make_model()
    model.set_support(a.id, fixed_restraints())
    case = model.add_load_case("Q1", V)
    model.add_support_settlement(case.id, a.id, uz=m(-0.01))
    before = model.model_revision
    with pytest.raises(InputValueError) as info:
        model.add_support_settlement(case.id, a.id, ux=m(0.01))
    expect(info, D.INPUT_OUT_OF_RANGE, "load.settlement_duplicate")
    assert model.model_revision == before
    assert len(model.loads) == 1


def test_set_support_that_frees_settled_dof_is_rejected() -> None:
    model, a, _ = make_model()
    original = model.set_support(a.id, fixed_restraints())
    case = model.add_load_case("Q1", V)
    model.add_support_settlement(case.id, a.id, uz=m(-0.01))
    before = model.model_revision
    with pytest.raises(InputValueError) as info:
        model.set_support(a.id, build_restraints(fixed=[SupportDof.UX, SupportDof.UY]))
    expect(info, D.INPUT_OUT_OF_RANGE, "load.settlement_support_conflict")
    params = dict(info.value.diagnostic.params)
    assert params["case"] == "Q1"
    assert "Uz" in str(params["detail"])
    assert model.support_at(a.id) is original
    assert model.model_revision == before


def test_set_support_that_turns_settled_dof_into_spring_is_rejected() -> None:
    model, a, _ = make_model()
    original = model.set_support(a.id, fixed_restraints())
    case = model.add_load_case("Q1", V)
    model.add_support_settlement(case.id, a.id, uz=m(-0.01))
    restraints = build_restraints(
        fixed=[SupportDof.UX], springs={SupportDof.UZ: Quantity.of(1e6, "N/m")}
    )
    with pytest.raises(InputValueError) as info:
        model.set_support(a.id, restraints)
    expect(info, D.INPUT_OUT_OF_RANGE, "load.settlement_support_conflict")
    assert model.support_at(a.id) is original


def test_set_support_that_changes_basis_is_rejected() -> None:
    model, a, _ = make_model()
    original = model.set_support(a.id, fixed_restraints())
    case = model.add_load_case("Q1", V)
    model.add_support_settlement(case.id, a.id, uz=m(-0.01))
    with pytest.raises(InputValueError) as info:
        model.set_support(a.id, fixed_restraints(), basis=inclined(30))
    expect(info, D.INPUT_OUT_OF_RANGE, "load.settlement_support_conflict")
    assert "base" in str(dict(info.value.diagnostic.params)["detail"])
    assert model.support_at(a.id) is original
    assert original.basis == SupportBasis.GLOBAL


def test_remove_support_with_settlement_is_rejected() -> None:
    model, a, _ = make_model()
    original = model.set_support(a.id, fixed_restraints())
    case = model.add_load_case("Q1", V)
    load = model.add_support_settlement(case.id, a.id, uz=m(-0.01))
    before = model.model_revision
    with pytest.raises(InputValueError) as info:
        model.remove_support(a.id)
    expect(info, D.INPUT_OUT_OF_RANGE, "load.settlement_support_conflict")
    assert model.support_at(a.id) is original
    assert model.model_revision == before
    model.remove_load(load.id)
    model.remove_support(a.id)  # tras retirar el asiento, se permite


# --- Adaptadores (E1: rechazo explícito; E2.1 y E2.2 lo admiten, E2-b) -----------------


def settled_frame() -> Frame:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(2, 0)
    frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.nodal(b, fz=-1000.0)
    frame.model.add_support_settlement(frame.case_id, a.id, uz=m(-0.01))
    return frame


def test_planar_adapter_translates_settlement() -> None:
    """E2.1 (E2-b): la misma petición que E1 rechazaba lleva ahora el asiento en u₂."""
    frame = settled_frame()
    request = translate_planar_xz(frame.model, frame.config()).request
    (node,) = (n.tag for n in request.nodes if n.x1 == 0.0)
    assert request.settlements == (PlanarSettlement(node, 2, -0.01),)


def test_spatial_adapter_translates_settlement() -> None:
    """E2.2 (E2-b): la misma petición que E1 rechazaba lleva ahora el asiento en Uz (GDL 3)."""
    frame = settled_frame()
    request = translate_spatial(frame.model, SpatialAnalysisConfiguration(frame.case_id)).request
    (node,) = (n.tag for n in request.nodes if n.x == 0.0)
    assert request.settlements == (SpatialSettlement(node, 3, -0.01),)


def test_settlement_only_case_is_not_empty() -> None:
    frame = Frame()
    a, b = frame.node(0, 0), frame.node(2, 0)
    frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.model.add_support_settlement(frame.case_id, a.id, uz=m(-0.01))
    request = translate_planar_xz(frame.model, frame.config()).request  # sin fem.empty_case
    assert not request.nodal_loads
    assert not request.beam_loads
    assert len(request.settlements) == 1
