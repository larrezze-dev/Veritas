"""Casos y cargas en StructuralModel: tabla D6 del plan T-006 (revisiones y model_revision)."""

from uuid import uuid4

import pytest

from veritas.core.diagnostics import DiagnosticCode, InputTypeError, InputValueError
from veritas.core.geometry.entities import Member, Node
from veritas.core.geometry.model import StructuralModel
from veritas.core.loads.load_case import LoadCategory
from veritas.core.loads.loads import (
    STANDARD_GRAVITY,
    LoadBasis,
    MemberUniformLoad,
    NodalLoad,
    SelfWeight,
)
from veritas.core.materials.material import MaterialFamily
from veritas.core.units import Quantity

D = DiagnosticCode
P = LoadCategory.PERMANENT
V = LoadCategory.VARIABLE


def m(value: float) -> Quantity:
    return Quantity.of(value, "m")


def kn(value: float) -> Quantity:
    return Quantity.of(value, "kN")


def make_model() -> tuple[StructuralModel, Node, Node, Member]:
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
    member = model.add_member(a.id, b.id, material=material.ref, section=section.ref)
    return model, a, b, member


# --- Casos ------------------------------------------------------------------------------


def test_add_load_case_bumps_model_revision() -> None:
    model, *_ = make_model()
    before = model.model_revision
    case = model.add_load_case("G1", P, description="Peso propio")
    assert (case.revision, case.name, case.category) == (1, "G1", P)
    assert model.model_revision == before + 1
    assert model.load_case(case.id) is case
    assert model.load_cases[case.id] is case


def test_case_names_are_not_unique() -> None:
    model, *_ = make_model()
    first = model.add_load_case("Q", V)
    second = model.add_load_case("Q", V)
    assert first.id != second.id


def test_revise_name_or_description_is_presentation_only() -> None:
    model, *_ = make_model()
    case = model.add_load_case("Q1", V)
    before = model.model_revision
    renamed = model.revise_load_case(case.id, name="Q uso", description="Uso")
    assert (renamed.id, renamed.revision, renamed.name) == (case.id, 2, "Q uso")
    assert renamed.category is V
    assert model.model_revision == before


def test_revise_category_bumps_model_revision() -> None:
    model, *_ = make_model()
    case = model.add_load_case("A1", V)
    before = model.model_revision
    revised = model.revise_load_case(case.id, category=LoadCategory.ACCIDENTAL)
    assert revised.revision == 2
    assert model.model_revision == before + 1


def test_identical_revision_returns_current() -> None:
    model, *_ = make_model()
    case = model.add_load_case("Q1", V, description="x")
    before = model.model_revision
    assert model.revise_load_case(case.id, name="Q1", category=V, description="x") is case
    assert model.revise_load_case(case.id) is case
    assert model.model_revision == before


def test_revise_rejects_invalid_values_without_changes() -> None:
    model, *_ = make_model()
    case = model.add_load_case("Q1", V)
    before = model.model_revision
    with pytest.raises(InputTypeError):
        model.revise_load_case(case.id, name="  ")
    with pytest.raises(InputTypeError):
        model.revise_load_case(case.id, category="VARIABLE")  # type: ignore[arg-type]
    assert model.load_case(case.id) is case
    assert model.model_revision == before


def test_unknown_case_is_reference_not_found() -> None:
    model, a, *_ = make_model()
    for call in (
        lambda: model.load_case(uuid4()),
        lambda: model.revise_load_case(uuid4(), name="x"),
        lambda: model.remove_load_case(uuid4()),
        lambda: model.loads_in_case(uuid4()),
        lambda: model.add_nodal_load(uuid4(), a.id, fz=kn(-1)),
        lambda: model.set_self_weight(uuid4()),
        lambda: model.case_resultant(uuid4()),
    ):
        with pytest.raises(InputValueError) as info:
            call()
        assert info.value.code is D.REFERENCE_NOT_FOUND
        assert info.value.diagnostic.params_dict()["kind"] == "caso de carga"


# --- Cargas nodales y de barra ----------------------------------------------------------


def test_add_nodal_load_stores_si_components() -> None:
    model, _, b, _ = make_model()
    case = model.add_load_case("Q1", V)
    before = model.model_revision
    load = model.add_nodal_load(case.id, b.id, fz=kn(-10), my=Quantity.of(2, "kN·m"))
    assert isinstance(load, NodalLoad)
    assert load.revision == 1
    assert load.force.components() == (0.0, 0.0, -10_000.0)
    assert load.moment.components() == (0.0, 2000.0, 0.0)
    assert model.model_revision == before + 1
    assert model.loads[load.id] is load


def test_nodal_load_checks_dimensions_types_and_zero() -> None:
    model, _, b, _ = make_model()
    case = model.add_load_case("Q1", V)
    before = model.model_revision
    with pytest.raises(InputValueError) as info:
        model.add_nodal_load(case.id, b.id, fz=Quantity.of(1, "kN/m"))
    assert info.value.code is D.UNITS_INCOMPATIBLE
    with pytest.raises(InputValueError) as info:
        model.add_nodal_load(case.id, b.id, mx=kn(1))
    assert info.value.code is D.UNITS_INCOMPATIBLE
    with pytest.raises(InputTypeError):
        model.add_nodal_load(case.id, b.id, fz=-10.0)  # type: ignore[arg-type]
    with pytest.raises(InputValueError) as info:
        model.add_nodal_load(case.id, b.id)
    assert info.value.code is D.INPUT_OUT_OF_RANGE
    with pytest.raises(InputValueError) as info:
        model.add_nodal_load(case.id, uuid4(), fz=kn(1))
    assert info.value.code is D.REFERENCE_NOT_FOUND
    assert model.model_revision == before
    assert model.loads == {}


def test_several_nodal_loads_on_the_same_node_are_kept() -> None:
    model, _, b, _ = make_model()
    case = model.add_load_case("Q1", V)
    first = model.add_nodal_load(case.id, b.id, fz=kn(-1))
    second = model.add_nodal_load(case.id, b.id, fz=kn(-1))
    assert model.loads_in_case(case.id) == (first, second)


def test_member_uniform_load() -> None:
    model, *_, member = make_model()
    case = model.add_load_case("Q1", V)
    load = model.add_member_uniform_load(
        case.id, member.id, basis=LoadBasis.LOCAL, qz=Quantity.of(-2, "kN/m")
    )
    assert isinstance(load, MemberUniformLoad)
    assert load.intensity.components() == (0.0, 0.0, -2000.0)
    with pytest.raises(InputValueError) as info:
        model.add_member_uniform_load(case.id, member.id, qz=kn(-2))
    assert info.value.code is D.UNITS_INCOMPATIBLE
    with pytest.raises(InputValueError) as info:
        model.add_member_uniform_load(case.id, member.id)
    assert info.value.code is D.INPUT_OUT_OF_RANGE
    with pytest.raises(InputTypeError):
        model.add_member_uniform_load(
            case.id,
            member.id,
            basis="LOCAL",  # type: ignore[arg-type]
            qz=Quantity.of(1, "N/m"),
        )
    with pytest.raises(InputValueError) as info:
        model.add_member_uniform_load(case.id, uuid4(), qz=Quantity.of(1, "N/m"))
    assert info.value.code is D.REFERENCE_NOT_FOUND


def test_remove_load_and_ids_not_recycled() -> None:
    model, _, b, _ = make_model()
    case = model.add_load_case("Q1", V)
    load = model.add_nodal_load(case.id, b.id, fz=kn(-1))
    before = model.model_revision
    assert model.remove_load(load.id) is load
    assert model.model_revision == before + 1
    assert model.loads_in_case(case.id) == ()
    again = model.add_nodal_load(case.id, b.id, fz=kn(-1))
    assert again.id != load.id
    with pytest.raises(InputValueError) as info:
        model.remove_load(load.id)
    assert info.value.code is D.REFERENCE_NOT_FOUND
    assert info.value.diagnostic.params_dict()["kind"] == "carga"


def test_remove_load_case_cascades_once() -> None:
    model, _, b, member = make_model()
    g1 = model.add_load_case("G1", P)
    q1 = model.add_load_case("Q1", V)
    sw = model.set_self_weight(g1.id)
    kept = model.add_nodal_load(q1.id, b.id, fz=kn(-1))
    removed_loads = (
        model.add_nodal_load(g1.id, b.id, fz=kn(-1)),
        model.add_member_uniform_load(g1.id, member.id, qz=Quantity.of(-1, "kN/m")),
    )
    before = model.model_revision
    case, removed = model.remove_load_case(g1.id)
    assert case is g1
    assert removed == (sw, *removed_loads)
    assert model.model_revision == before + 1
    assert model.self_weight is None
    assert list(model.loads.values()) == [kept]
    assert g1.id not in model.load_cases
    # El peso propio puede volver a definirse en otro caso permanente.
    g2 = model.add_load_case("G2", P)
    assert model.set_self_weight(g2.id).id != sw.id


# --- Peso propio: sin duplicidad --------------------------------------------------------


def test_set_self_weight_default_gravity_and_revision() -> None:
    model, *_ = make_model()
    g1 = model.add_load_case("G1", P)
    before = model.model_revision
    sw = model.set_self_weight(g1.id)
    assert isinstance(sw, SelfWeight)
    assert (sw.revision, sw.gravity) == (1, STANDARD_GRAVITY.si_value)
    assert model.self_weight is sw
    assert model.model_revision == before + 1
    revised = model.set_self_weight(g1.id, gravity=Quantity.of(10, "m/s²"))
    assert (revised.id, revised.revision, revised.gravity) == (sw.id, 2, 10.0)
    assert model.model_revision == before + 2
    assert model.set_self_weight(g1.id, gravity=Quantity.of(10, "m/s²")) is revised
    assert model.model_revision == before + 2


def test_revised_self_weight_keeps_its_position() -> None:
    model, _, b, _ = make_model()
    g1 = model.add_load_case("G1", P)
    model.set_self_weight(g1.id)
    nodal = model.add_nodal_load(g1.id, b.id, fz=kn(-1))
    revised = model.set_self_weight(g1.id, gravity=Quantity.of(10, "m/s²"))
    assert model.loads_in_case(g1.id) == (revised, nodal)


def test_second_self_weight_is_rejected() -> None:
    model, *_ = make_model()
    g1 = model.add_load_case("G1", P)
    g2 = model.add_load_case("G2", P)
    model.set_self_weight(g1.id)
    before = model.model_revision
    with pytest.raises(InputValueError) as info:
        model.set_self_weight(g2.id)
    assert info.value.code is D.INPUT_OUT_OF_RANGE
    assert info.value.diagnostic.message_key == "load.self_weight_duplicate"
    assert model.model_revision == before


@pytest.mark.parametrize("category", [LoadCategory.VARIABLE, LoadCategory.ACCIDENTAL])
def test_self_weight_only_in_permanent_case(category: LoadCategory) -> None:
    model, *_ = make_model()
    case = model.add_load_case("X", category)
    before = model.model_revision
    with pytest.raises(InputValueError) as info:
        model.set_self_weight(case.id)
    assert info.value.code is D.INPUT_OUT_OF_RANGE
    assert info.value.diagnostic.message_key == "load.self_weight_category"
    assert model.self_weight is None
    assert model.model_revision == before


def test_case_with_self_weight_cannot_stop_being_permanent() -> None:
    model, *_ = make_model()
    g1 = model.add_load_case("G1", P)
    model.set_self_weight(g1.id)
    before = model.model_revision
    with pytest.raises(InputValueError) as info:
        model.revise_load_case(g1.id, category=V)
    assert info.value.diagnostic.message_key == "load.self_weight_category"
    assert model.load_case(g1.id) is g1
    assert model.model_revision == before


@pytest.mark.parametrize(
    ("gravity", "code"),
    [
        (Quantity.of(0, "m/s²"), D.INPUT_OUT_OF_RANGE),
        (Quantity.of(-9.81, "m/s²"), D.INPUT_OUT_OF_RANGE),
        (Quantity.of(10, "m"), D.UNITS_INCOMPATIBLE),
    ],
)
def test_set_self_weight_rejects_bad_gravity(gravity: Quantity, code: DiagnosticCode) -> None:
    model, *_ = make_model()
    g1 = model.add_load_case("G1", P)
    with pytest.raises(InputValueError) as info:
        model.set_self_weight(g1.id, gravity=gravity)
    assert info.value.code is code
    assert model.self_weight is None


def test_set_self_weight_gravity_type() -> None:
    model, *_ = make_model()
    g1 = model.add_load_case("G1", P)
    with pytest.raises(InputTypeError):
        model.set_self_weight(g1.id, gravity=9.81)  # type: ignore[arg-type]


def test_model_revision_trace() -> None:
    """Traza de la tabla D6, incluidos rechazos y cascada."""
    model, _, b, member = make_model()
    start = model.model_revision
    g1 = model.add_load_case("G1", P)  # +1
    model.set_self_weight(g1.id)  # +1
    q1 = model.add_load_case("Q1", V)  # +1
    model.add_nodal_load(q1.id, b.id, fz=kn(-10))  # +1
    model.add_member_uniform_load(q1.id, member.id, qz=Quantity.of(-2, "kN/m"))  # +1
    model.revise_load_case(q1.id, description="uso")  # 0
    with pytest.raises(InputValueError):
        model.add_nodal_load(q1.id, b.id)  # rechazada: 0
    model.remove_load_case(q1.id)  # +1
    assert model.model_revision == start + 6
