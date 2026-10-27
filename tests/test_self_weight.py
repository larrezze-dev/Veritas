"""Peso propio derivado bajo demanda (LD-003; plan T-006, D4).

Referencias independientes (cuerpo libre a mano): fixture ``self_weight_prismatic`` de
VALIDATION_PLAN y barra inclinada (0,0,0)->(3,0,4), L = 5 m. Tolerancia relativa 1e-12:
pocos productos en doble precisión.
"""

import math

import pytest

from veritas.core.diagnostics import DiagnosticCode, InputValueError
from veritas.core.geometry.entities import Member
from veritas.core.geometry.model import StructuralModel
from veritas.core.loads.load_case import LoadCase, LoadCategory
from veritas.core.materials.material import MaterialFamily
from veritas.core.units import Quantity

D = DiagnosticCode
G10 = Quantity.of(10, "m/s²")


def m(value: float) -> Quantity:
    return Quantity.of(value, "m")


def build(
    end: tuple[float, float, float], density: float = 1000.0
) -> tuple[StructuralModel, Member, LoadCase]:
    model = StructuralModel()
    material = model.add_material(
        name="Sintético",
        family=MaterialFamily.STEEL,
        source="test",
        elastic_modulus=Quantity.of(210, "GPa"),
        shear_modulus=Quantity.of(81, "GPa"),
        density=Quantity.of(density, "kg/m³"),
    )
    section = model.add_rectangular_section(b=m(0.2), h=m(0.4))
    a = model.add_node(m(0), m(0), m(0))
    b = model.add_node(m(end[0]), m(end[1]), m(end[2]))
    member = model.add_member(a.id, b.id, material=material.ref, section=section.ref)
    case = model.add_load_case("G1", LoadCategory.PERMANENT)
    return model, member, case


def test_self_weight_prismatic_fixture() -> None:
    model, member, case = build((2, 0, 0))
    model.set_self_weight(case.id, gravity=G10)
    qx, qy, qz = model.self_weight_load(member.id)
    assert qz.to("N/m") == pytest.approx(-800.0, rel=1e-12)
    assert (str(qx.si_value), str(qy.si_value)) == ("0.0", "0.0")  # +0.0, no -0.0
    resultant = model.case_resultant(case.id)
    assert resultant.force[2].to("N") == pytest.approx(-1600.0, rel=1e-12)
    # Momento respecto al origen: r_medio (1,0,0) × (0,0,-1600) = (0, +1600, 0).
    assert resultant.moment[1].to("N·m") == pytest.approx(1600.0, rel=1e-12)
    assert resultant.load_count == 1


def test_inclined_member_uses_real_length_not_projected() -> None:
    model, member, case = build((3, 0, 4))
    model.set_self_weight(case.id, gravity=G10)
    assert model.self_weight_load(member.id)[2].to("N/m") == pytest.approx(-800.0, rel=1e-12)
    resultant = model.case_resultant(case.id)
    fx, fy, fz = (c.to("N") for c in resultant.force)
    assert (fx, fy) == (0.0, 0.0)
    assert fz == pytest.approx(-4000.0, rel=1e-12)  # La longitud proyectada daría -2400 N.
    # r_medio (1,5; 0; 2) × (0; 0; -4000) = (0; +6000; 0).
    mx, my, mz = (c.to("N·m") for c in resultant.moment)
    assert my == pytest.approx(6000.0, rel=1e-12)
    assert mx == 0.0
    assert mz == 0.0


def test_default_gravity_is_standard() -> None:
    model, member, case = build((2, 0, 0))
    model.set_self_weight(case.id)
    expected = 1000.0 * 0.08 * 9.80665
    assert model.self_weight_load(member.id)[2].to("N/m") == pytest.approx(-expected, rel=1e-12)


def test_self_weight_follows_material_reassignment() -> None:
    model, member, case = build((2, 0, 0))
    sw = model.set_self_weight(case.id, gravity=G10)
    heavier = model.add_material(
        name="Sintético 2",
        family=MaterialFamily.STEEL,
        source="test",
        elastic_modulus=Quantity.of(210, "GPa"),
        shear_modulus=Quantity.of(81, "GPa"),
        density=Quantity.of(2000, "kg/m³"),
    )
    before = model.model_revision
    model.assign_material(member.id, heavier.ref)
    assert model.model_revision == before + 1  # La reasignación ya invalida.
    assert model.self_weight is sw  # La definición no cambia.
    assert model.self_weight_load(member.id)[2].to("N/m") == pytest.approx(-1600.0, rel=1e-12)


def test_self_weight_load_without_definition_is_an_error_not_zero() -> None:
    model, member, _ = build((2, 0, 0))
    with pytest.raises(InputValueError) as info:
        model.self_weight_load(member.id)
    assert info.value.code is D.REFERENCE_NOT_FOUND
    assert info.value.diagnostic.message_key == "load.self_weight_missing"
    assert info.value.diagnostic.location is not None
    assert info.value.diagnostic.location.entity_id == member.id


def test_self_weight_applies_to_every_member() -> None:
    model, member, case = build((2, 0, 0))
    material, section = model.member(member.id).material, model.member(member.id).section
    c = model.add_node(m(2), m(0), m(3))
    second = model.add_member(member.node_j, c.id, material=material, section=section)
    model.set_self_weight(case.id, gravity=G10)
    resultant = model.case_resultant(case.id)
    # 800 N/m · (2 m + 3 m).
    assert resultant.force[2].to("N") == pytest.approx(-4000.0, rel=1e-12)
    assert model.self_weight_load(second.id)[2].to("N/m") == pytest.approx(-800.0, rel=1e-12)


def test_tiny_density_and_gravity_give_unrepresentable_not_zero() -> None:
    model, member, case = build((2, 0, 0), density=1e-200)
    model.set_self_weight(case.id, gravity=Quantity(1e-200, G10.dimension))
    with pytest.raises(InputValueError) as info:
        model.self_weight_load(member.id)
    assert info.value.code is D.UNITS_UNREPRESENTABLE
    with pytest.raises(InputValueError):
        model.case_resultant(case.id)


def test_standard_gravity_value_is_exact() -> None:
    assert math.isclose(9.80665, Quantity.of(9.80665, "m/s²").si_value, rel_tol=0.0)
