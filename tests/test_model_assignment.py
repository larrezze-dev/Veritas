"""Un material y una sección por barra; registro de revisiones (plan T-004, D1/D2, GEO-003)."""

import dataclasses
from fractions import Fraction
from typing import Any
from uuid import uuid4

import pytest

from veritas.core.diagnostics import (
    DiagnosticCode,
    InputTypeError,
    InputValueError,
    Location,
)
from veritas.core.geometry.entities import Member
from veritas.core.geometry.model import StructuralModel
from veritas.core.materials.material import MaterialFamily, MaterialRevision, build_material
from veritas.core.references import RevisionRef
from veritas.core.sections.mass import linear_mass
from veritas.core.sections.section import SectionRevision, build_rectangular_section
from veritas.core.units import LINEAR_MASS, Quantity


def m(value: float) -> Quantity:
    return Quantity.of(value, "m")


def add_steel(model: StructuralModel, **overrides: Any) -> MaterialRevision:
    fields: dict[str, Any] = {
        "name": "Acero",
        "family": MaterialFamily.STEEL,
        "source": "test",
        "elastic_modulus": Quantity.of(210, "GPa"),
        "shear_modulus": Quantity.of(81, "GPa"),
        "density": Quantity.of(7850, "kg/m³"),
    }
    fields.update(overrides)
    return model.add_material(**fields)


def setup() -> tuple[StructuralModel, MaterialRevision, SectionRevision, Member]:
    model = StructuralModel()
    material = add_steel(model)
    section = model.add_rectangular_section(b=m(0.2), h=m(0.4), name="R")
    a = model.add_node(m(0), m(0), m(0))
    b = model.add_node(m(3), m(0), m(0))
    member = model.add_member(a.id, b.id, material=material.ref, section=section.ref)
    return model, material, section, member


def test_member_has_exactly_one_material_and_section() -> None:  # GEO-003
    model, material, section, member = setup()
    assert member.material == material.ref
    assert member.section == section.ref
    assert model.material(member.material) is material
    assert model.section(member.section) is section


def test_material_and_section_are_mandatory() -> None:  # T-MAT-001
    model, material, section, member = setup()
    with pytest.raises(TypeError):
        model.add_member(member.node_i, member.node_j, section=section.ref)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        model.add_member(member.node_i, member.node_j, material=material.ref)  # type: ignore[call-arg]


@pytest.mark.parametrize("field", ["material", "section"])
def test_sequence_of_references_rejected(field: str) -> None:  # T-MAT-001
    model, material, section, member = setup()
    refs: dict[str, Any] = {"material": material.ref, "section": section.ref}
    refs[field] = [refs[field], refs[field]]
    revision = model.model_revision
    with pytest.raises(InputTypeError) as info:
        model.add_member(member.node_i, member.node_j, **refs)
    assert info.value.code is DiagnosticCode.INPUT_INVALID_TYPE
    assert model.model_revision == revision
    assert len(model.members) == 1
    with pytest.raises(TypeError):
        dataclasses.replace(member, **{field: refs[field]})
    with pytest.raises(InputTypeError):
        model.assign_material(member.id, [material.ref])  # type: ignore[arg-type]


def test_unknown_and_crossed_references() -> None:
    model, material, section, member = setup()
    revision = model.model_revision
    cases: list[tuple[RevisionRef, RevisionRef, str]] = [
        (RevisionRef(uuid4(), 1), section.ref, "material"),
        (RevisionRef(material.id, 2), section.ref, "material"),
        (material.ref, RevisionRef(section.id, 5), "sección"),
        (section.ref, material.ref, "material"),  # Referencias cruzadas.
    ]
    for mat, sec, kind in cases:
        with pytest.raises(InputValueError) as info:
            model.add_member(member.node_i, member.node_j, material=mat, section=sec)
        assert info.value.code is DiagnosticCode.REFERENCE_NOT_FOUND
        assert info.value.diagnostic.params_dict()["kind"] == kind
    assert model.model_revision == revision
    assert len(model.members) == 1


def test_library_operations_do_not_change_model_revision() -> None:
    model, material, _, member = setup()
    revision = model.model_revision
    add_steel(model, name="Otro")
    model.add_rectangular_section(b=m(0.1), h=m(0.1))
    revised = model.revise_material(material.id, elastic_modulus=Quantity.of(200, "GPa"))
    assert revised.revision == 2
    assert revised.elastic_modulus == 200e9
    assert revised.shear_modulus == material.shear_modulus
    assert model.model_revision == revision
    assert model.member(member.id) == member  # La barra conserva la revisión 1.
    assert model.material(material.ref) is material
    assert model.material_history(material.id) == (material, revised)


def test_revise_without_change_returns_latest() -> None:
    model, material, section, _ = setup()
    assert model.revise_material(material.id) is material
    assert model.revise_material(material.id, density=Quantity.of(7850, "kg/m³")) is material
    assert model.revise_rectangular_section(section.id, b=m(0.2)) is section
    assert model.material_history(material.id) == (material,)


def test_revise_section_keeps_member_reference() -> None:
    model, _, section, member = setup()
    revised = model.revise_rectangular_section(section.id, h=m(0.5))
    assert revised.revision == 2
    assert revised.parameters.h == 0.5
    assert revised.name == "R"
    assert model.member(member.id).section == section.ref
    assert model.section_history(section.id) == (section, revised)


def test_revise_cannot_change_family() -> None:
    model, material, _, _ = setup()
    with pytest.raises(InputValueError) as info:
        model.revise_material(material.id, family=MaterialFamily.TIMBER)
    assert info.value.code is DiagnosticCode.INPUT_OUT_OF_RANGE
    assert info.value.diagnostic.message_key == "model.family_change"
    assert model.material_history(material.id) == (material,)


def test_explicit_reassignment_creates_member_revision() -> None:
    model, material, _, member = setup()
    revised = model.revise_material(material.id, elastic_modulus=Quantity.of(200, "GPa"))
    revision = model.model_revision
    updated = model.assign_material(member.id, revised.ref)
    assert updated.revision == member.revision + 1
    assert updated.material == revised.ref
    assert model.model_revision == revision + 1
    assert model.assign_material(member.id, revised.ref) is updated
    assert model.model_revision == revision + 1
    other = model.add_rectangular_section(b=m(0.1), h=m(0.3))
    again = model.assign_section(member.id, other.ref)
    assert again.revision == member.revision + 2
    assert again.section == other.ref
    assert again.material == revised.ref
    assert model.model_revision == revision + 2
    assert model.assign_section(member.id, other.ref) is again


def test_rejected_assignment_has_no_effect() -> None:
    model, _, _, member = setup()
    revision = model.model_revision
    with pytest.raises(InputValueError):
        model.assign_section(member.id, RevisionRef(uuid4(), 1))
    with pytest.raises(InputValueError):
        model.assign_material(uuid4(), member.material)
    assert model.member(member.id) is member
    assert model.model_revision == revision


def test_register_rules() -> None:
    model, material, section, _ = setup()
    assert model.register_material(dataclasses.replace(material)) is material
    with pytest.raises(InputValueError) as info:
        model.register_material(dataclasses.replace(material, name="Distinto"))
    assert info.value.code is DiagnosticCode.REVISION_CONFLICT
    with pytest.raises(InputValueError) as info:  # Hueco: falta la revisión 2.
        model.register_material(dataclasses.replace(material, revision=3))
    assert info.value.code is DiagnosticCode.REVISION_CONFLICT
    second = model.register_material(dataclasses.replace(material, revision=2, name="Rev 2"))
    assert model.revise_material(material.id, name="Rev 3").revision == 3
    assert model.material(second.ref) is second
    with pytest.raises(InputValueError) as info:
        model.register_material(
            dataclasses.replace(material, revision=4, family=MaterialFamily.ALUMINIUM)
        )
    assert info.value.code is DiagnosticCode.INPUT_OUT_OF_RANGE
    with pytest.raises(InputValueError) as info:  # Id nuevo que no empieza en 1.
        model.register_section(dataclasses.replace(section, id=uuid4(), revision=2))
    assert info.value.code is DiagnosticCode.REVISION_CONFLICT
    external = build_rectangular_section(uuid4(), 1, b=m(1), h=m(1))
    assert model.register_section(external) is external
    with pytest.raises(InputTypeError):
        model.register_material(section)  # type: ignore[arg-type]
    with pytest.raises(InputTypeError):
        model.register_section(material)  # type: ignore[arg-type]


def test_linear_mass_is_rho_times_area() -> None:
    model, material, section, member = setup()
    mass = model.linear_mass(member.id)
    assert mass.dimension == LINEAR_MASS
    exact = Fraction(0.2) * Fraction(0.4) * Fraction(7850.0)
    assert abs(Fraction(mass.si_value) - exact) <= 2 * Fraction(2.0**-52) * exact
    assert mass.to("kg/m") == pytest.approx(628.0, rel=1e-15)
    assert linear_mass(material, section) == mass


def test_linear_mass_unrepresentable() -> None:
    light = build_material(
        uuid4(),
        1,
        name="Ligero",
        family=MaterialFamily.STEEL,
        source="test",
        elastic_modulus=Quantity.of(1, "Pa"),
        shear_modulus=Quantity.of(1, "Pa"),
        density=Quantity.of(1e-200, "kg/m³"),
    )
    tiny = build_rectangular_section(uuid4(), 1, b=m(1e-60), h=m(1e-60))
    with pytest.raises(InputValueError) as info:
        linear_mass(light, tiny)
    assert info.value.code is DiagnosticCode.UNITS_UNREPRESENTABLE
    with pytest.raises(InputTypeError):
        linear_mass(tiny, light)  # type: ignore[arg-type]


def test_revision_ref_validation() -> None:
    ref = RevisionRef(uuid4(), 1)
    assert str(ref).endswith("rev 1")
    with pytest.raises(ValueError, match=">= 1"):
        RevisionRef(uuid4(), 0)
    with pytest.raises(TypeError, match="entero"):
        RevisionRef(uuid4(), True)
    with pytest.raises(TypeError, match="UUID v4"):
        RevisionRef("x", 1)  # type: ignore[arg-type]


def test_revise_material_can_clear_optional_fields() -> None:
    model = StructuralModel()
    material = add_steel(model, poisson_ratio=0.3, standard_edition="ed. 1")
    kept = model.revise_material(material.id, name="Otro")
    assert kept.poisson_ratio == 0.3
    assert kept.standard_edition == "ed. 1"
    cleared = model.revise_material(material.id, poisson_ratio=None, standard_edition=None)
    assert cleared.revision == 3
    assert cleared.poisson_ratio is None
    assert cleared.standard_edition is None


def test_rejected_revisions_leave_history_untouched() -> None:
    model, material, section, member = setup()
    revision = model.model_revision
    with pytest.raises(InputValueError) as info:
        model.revise_material(material.id, elastic_modulus=Quantity.of(0, "Pa"))
    assert info.value.code is DiagnosticCode.INPUT_OUT_OF_RANGE
    with pytest.raises(InputValueError) as info:
        model.revise_rectangular_section(section.id, b=m(0))
    assert info.value.code is DiagnosticCode.INPUT_OUT_OF_RANGE
    with pytest.raises(InputValueError):
        model.revise_material(uuid4())
    assert model.material_history(material.id) == (material,)
    assert model.section_history(section.id) == (section,)
    assert model.model_revision == revision
    assert model.member(member.id) is member


def test_not_found_carries_member_location() -> None:
    model, _, section, member = setup()
    with pytest.raises(InputValueError) as info:
        model.assign_material(member.id, section.ref)
    location = info.value.diagnostic.location
    assert location is not None
    assert location.entity_id == member.id
    assert location.field == "material"


def test_registered_section_with_tampered_area_is_rejected() -> None:
    model, _, section, _ = setup()
    bad_area = dataclasses.replace(section.properties.area, value=0.0)
    with pytest.raises(InputValueError) as info:
        model.register_section(
            dataclasses.replace(
                section,
                revision=2,
                properties=dataclasses.replace(section.properties, area=bad_area),
            )
        )
    assert info.value.code is DiagnosticCode.UNITS_UNREPRESENTABLE
    assert model.section_history(section.id) == (section,)


def test_linear_mass_overflow_with_location() -> None:
    heavy = build_material(
        uuid4(),
        1,
        name="Denso",
        family=MaterialFamily.STEEL,
        source="test",
        elastic_modulus=Quantity.of(1, "Pa"),
        shear_modulus=Quantity.of(1, "Pa"),
        density=Quantity.of(1e300, "kg/m³"),
    )
    big = build_rectangular_section(uuid4(), 1, b=m(1e10), h=m(1e10))
    with pytest.raises(InputValueError) as info:
        linear_mass(heavy, big, Location(entity_id=heavy.id))
    assert info.value.code is DiagnosticCode.UNITS_UNREPRESENTABLE
    assert info.value.diagnostic.location == Location(entity_id=heavy.id)
