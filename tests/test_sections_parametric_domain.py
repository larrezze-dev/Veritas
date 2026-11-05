"""Dominio de los perfiles paramétricos: EDT-004, borrador, revisión y modelo (plan T-025 E1)."""

import math
from dataclasses import replace
from uuid import UUID

import pytest

from test_catalog_snapshot import section as catalog_section
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticCode, DiagnosticError
from veritas.core.geometry.model import StructuralModel
from veritas.core.geometry.replacement import compatibility_errors
from veritas.core.materials.material import MaterialFamily, MaterialRevision
from veritas.core.sections.catalog import PROFILE_SHAPES
from veritas.core.sections.parametric import (
    SHAPE_DIMENSIONS,
    ParametricDimension,
    ParametricParameters,
    ParametricShape,
)
from veritas.core.sections.properties import PropertyStatus
from veritas.core.sections.readiness import assess_section
from veritas.core.sections.section import (
    RectangularParameters,
    SectionFamily,
    SectionRevision,
    build_parametric_section,
    build_rectangular_section,
)
from veritas.section_editor import (
    SectionDraft,
    chs_draft,
    draft_from_values,
    i_draft,
    rhs_draft,
    shs_draft,
)

I, RHS, SHS, CHS = (  # noqa: E741 (etiquetas de forma)
    ParametricShape.I,
    ParametricShape.RHS,
    ParametricShape.SHS,
    ParametricShape.CHS,
)
ID = UUID(int=7 | (4 << 76) | (0b10 << 62))


def mm(**dims: float) -> dict[str, u.Quantity]:
    return {name: u.Quantity.of(value, "mm") for name, value in dims.items()}


def key_of(error: DiagnosticError) -> str:
    return error.diagnostic.message_key


# --- EDT-004: todos los rechazos, con clave y ubicación ---------------------------------

I_OK = {"h": 300.0, "b": 150.0, "tw": 7.1, "tf": 10.7}
RHS_OK = {"h": 300.0, "b": 200.0, "t": 10.0}

REJECTIONS = [
    (I, {**I_OK, "tf": 0.0}, "input.out_of_range", "tf"),
    (I, {**I_OK, "tw": -1.0}, "input.out_of_range", "tw"),
    (I, {**I_OK, "h": 0.0}, "input.out_of_range", "h"),
    (I, {**I_OK, "tf": 150.0}, "section.geometry_flange_too_thick", "tf"),  # 2·tf = h
    (I, {**I_OK, "tf": 200.0}, "section.geometry_flange_too_thick", "tf"),  # 2·tf > h
    (I, {**I_OK, "tw": 150.0}, "section.geometry_web_too_thick", "tw"),  # tw = b
    (I, {**I_OK, "tw": 400.0}, "section.geometry_web_too_thick", "tw"),
    (I, {**I_OK, "r": 71.5}, "section.geometry_radius_too_large", "r"),  # > (b − tw)/2 = 71,45
    (
        I,
        {**I_OK, "tf": 100.0, "r": 60.0},
        "section.geometry_radius_too_large",
        "r",
    ),  # > (h − 2tf)/2
    (I, {**I_OK, "r": -1.0}, "input.out_of_range", "r"),
    (RHS, {**RHS_OK, "t": 100.0}, "section.geometry_wall_too_thick", "t"),  # 2t = b
    (RHS, {**RHS_OK, "t": 0.0}, "input.out_of_range", "t"),
    (RHS, {**RHS_OK, "ro": 100.1}, "section.geometry_radius_too_large", "ro"),  # > b/2
    (RHS, {**RHS_OK, "ro": -0.5}, "input.out_of_range", "ro"),
    (SHS, {"b": 100.0, "t": 50.0}, "section.geometry_wall_too_thick", "t"),
    (SHS, {"b": 100.0, "t": 10.0, "ro": 50.1}, "section.geometry_radius_too_large", "ro"),
    (CHS, {"d": 100.0, "t": 50.0}, "section.geometry_wall_too_thick_circle", "t"),  # t = d/2
    (CHS, {"d": 100.0, "t": 60.0}, "section.geometry_wall_too_thick_circle", "t"),
    (CHS, {"d": -100.0, "t": 5.0}, "input.out_of_range", "d"),
]


@pytest.mark.parametrize(("shape", "dims", "key", "field"), REJECTIONS)
def test_invalid_geometry_is_rejected_with_key_and_location(
    shape: ParametricShape, dims: dict[str, float], key: str, field: str
) -> None:
    draft = draft_from_values(shape, dims, "mm")
    assert not draft.valid
    first = draft.diagnostics()[0]
    assert (first.message_key, first.location.field if first.location else None) == (key, field)
    assert first.code is DiagnosticCode.INPUT_OUT_OF_RANGE
    with pytest.raises(DiagnosticError) as info:
        build_parametric_section(ID, 1, shape=shape, dimensions=mm(**dims))
    assert key_of(info.value) == key
    with pytest.raises(DiagnosticError):
        draft.build(ID)


@pytest.mark.parametrize(
    ("shape", "dims"),
    [
        (I, {**I_OK, "r": 71.45}),  # r en la cota exacta (b − tw)/2: válido
        (I, {**I_OK, "tf": 149.0}),  # alma de 2 mm
        (RHS, {**RHS_OK, "ro": 100.0}),  # ro = b/2: lado menor semicircular
        (RHS, {**RHS_OK, "t": 99.0}),
        (SHS, {"b": 100.0, "t": 49.9, "ro": 50.0}),
        (CHS, {"d": 100.0, "t": 49.9}),
    ],
)
def test_boundary_geometry_is_accepted(shape: ParametricShape, dims: dict[str, float]) -> None:
    assert draft_from_values(shape, dims, "mm").valid
    section = build_parametric_section(ID, 1, shape=shape, dimensions=mm(**dims))
    assert section.family is SectionFamily.PARAMETRICA


def test_nonfinite_dimensions_are_rejected() -> None:
    for bad in (math.nan, math.inf, -math.inf):
        with pytest.raises(DiagnosticError) as info:
            i_draft(h=300.0, b=150.0, tw=7.1, tf=bad, unit="mm")
        assert info.value.code is DiagnosticCode.INPUT_NONFINITE
        with pytest.raises(DiagnosticError) as raw:
            ParametricDimension("t", bad)
        assert raw.value.code is DiagnosticCode.INPUT_NONFINITE


def test_incompatible_units_are_diagnosed_not_converted() -> None:
    draft = SectionDraft.of(CHS, {"d": u.Quantity.of(100, "mm"), "t": u.Quantity.of(5, "kg")})
    problems = draft.diagnostics()
    assert [p.code for p in problems] == [DiagnosticCode.UNITS_INCOMPATIBLE]
    assert not draft.valid
    with pytest.raises(DiagnosticError) as info:
        build_parametric_section(
            ID,
            1,
            shape=CHS,
            dimensions={"d": u.Quantity.of(100, "mm"), "t": u.Quantity.of(5, "kg")},
        )
    assert info.value.code is DiagnosticCode.UNITS_INCOMPATIBLE


def test_dimension_names_are_checked_per_shape() -> None:
    shs_with_h = SectionDraft.of(SHS, mm(h=100, b=100, t=5))
    names = [d.params_dict()["problem"] for d in shs_with_h.diagnostics()]
    assert names == ["desconocida"]  # SHS recibe una sola dimensión: b
    missing = SectionDraft.of(I, mm(h=300, b=150, tw=7.1))
    assert [d.params_dict()["name"] for d in missing.diagnostics()] == ["tf"]
    repeated = SectionDraft(
        CHS,
        (("d", u.Quantity.of(1, "m")), ("d", u.Quantity.of(2, "m")), ("t", u.Quantity.of(1, "mm"))),
    )
    assert "repetida" in [d.params_dict()["problem"] for d in repeated.diagnostics()]
    with pytest.raises(DiagnosticError) as info:
        ParametricParameters.from_values(SHS, {"h": 0.1, "b": 0.1, "t": 0.005})
    assert key_of(info.value) == "section.geometry_dimensions"
    with pytest.raises(DiagnosticError) as bad_order:
        ParametricParameters(
            I, tuple(ParametricDimension(n, 0.1) for n in ("b", "h", "tw", "tf", "r"))
        )
    assert key_of(bad_order.value) == "section.geometry_dimensions"


def test_draft_accumulates_every_problem() -> None:
    draft = i_draft(h=20.0, b=10.0, tw=15.0, tf=15.0, unit="mm")  # ala y alma imposibles
    keys = [d.message_key for d in draft.diagnostics()]
    assert keys == ["section.geometry_flange_too_thick", "section.geometry_web_too_thick"]


def test_shapes_and_dimension_names_match_the_catalog() -> None:
    assert SHAPE_DIMENSIONS[I] == ("h", "b", "tw", "tf", "r")
    assert SHAPE_DIMENSIONS[RHS] == ("h", "b", "t", "ro")
    assert SHAPE_DIMENSIONS[SHS] == ("b", "t", "ro")
    assert SHAPE_DIMENSIONS[CHS] == ("d", "t")
    assert {str(s) for s in ParametricShape} <= PROFILE_SHAPES


# --- Representación única ------------------------------------------------------------------


def test_representation_is_canonical_so_equal_geometry_is_equal_content() -> None:
    omitted = i_draft(h=300, b=150, tw=7.1, tf=10.7, unit="mm").build(ID)
    explicit = i_draft(h=300, b=150, tw=7.1, tf=10.7, r=0.0, unit="mm").build(ID)
    assert omitted == explicit
    params = omitted.require_parametric()
    assert [d.name for d in params.dimensions] == list(SHAPE_DIMENSIONS[I])
    assert params.get("r") == 0.0
    negative = ParametricParameters.from_values(
        I, {**{k: v / 1000 for k, v in I_OK.items()}, "r": -0.0}
    )
    assert math.copysign(1.0, negative.get("r")) > 0.0  # −0,0 → +0,0


def test_builders_cover_every_shape_and_unit() -> None:
    drafts = [
        i_draft(h=300, b=150, tw=7.1, tf=10.7, r=15, unit="mm"),
        rhs_draft(h=300, b=200, t=10, ro=15, unit="mm"),
        shs_draft(b=100, t=5, unit="mm"),
        chs_draft(d=0.1, t=0.005),
    ]
    for draft in drafts:
        assert draft.valid
        assert draft.build(ID, 1, name="x").family is SectionFamily.PARAMETRICA
    in_mm = chs_draft(d=100, t=5, unit="mm").build(ID)
    in_m = chs_draft(d=0.1, t=0.005, unit="m").build(ID)
    assert in_mm.properties.area.value == pytest.approx(in_m.properties.area.value, rel=1e-12)


def test_draft_from_section_round_trips_and_is_not_registered() -> None:
    section = rhs_draft(h=300, b=200, t=10, ro=15, unit="mm").build(ID)
    again = SectionDraft.from_section(section).build(ID)
    assert again == section
    model = StructuralModel()
    rhs_draft(h=300, b=200, t=10, unit="mm")  # crear un borrador no toca ningún modelo
    assert model.snapshot().sections == ()


# --- Revisión: antimanipulación y coherencia ----------------------------------------------


def section_i() -> SectionRevision:
    return i_draft(h=300, b=150, tw=7.1, tf=10.7, unit="mm").build(ID, name="I 300")


def test_hand_edited_properties_are_rejected() -> None:
    sec = section_i()
    for name in ("area", "iy", "iz"):
        prop = getattr(sec.properties, name)
        forged = replace(prop, value=prop.value * 1.01)
        with pytest.raises(DiagnosticError) as info:
            replace(sec, properties=replace(sec.properties, **{name: forged}))
        assert key_of(info.value) == "section.properties_mismatch"
    j = replace(sec.properties.torsion_constant, value=1e-6, status=PropertyStatus.CALCULADO)
    with pytest.raises(DiagnosticError) as info:
        replace(sec, properties=replace(sec.properties, torsion_constant=j))
    assert key_of(info.value) == "section.properties_mismatch"  # J inventada, no NO_DISPONIBLE


def test_a_record_with_another_method_version_is_accepted_as_stored() -> None:
    sec = section_i()
    other = replace(sec.properties.area, method="i-closed-form/2", value=1.0)
    accepted = replace(sec, properties=replace(sec.properties, area=other))
    assert accepted.properties.area.value == 1.0  # O3: no se recalcula una versión ajena


def test_centroid_and_product_of_inertia_must_be_positive_zero() -> None:
    sec = section_i()
    for name, value in (("centroid_y", 1e-3), ("iyz", -0.0)):
        forged = replace(getattr(sec.properties, name), value=value)
        with pytest.raises(DiagnosticError):
            replace(sec, properties=replace(sec.properties, **{name: forged}))


def test_family_and_parameters_must_agree_and_never_default_to_the_rectangle() -> None:
    sec = section_i()
    with pytest.raises(DiagnosticError) as info:
        replace(sec, parameters=RectangularParameters(0.1, 0.2))
    assert key_of(info.value) == "section.family_mismatch"
    with pytest.raises(DiagnosticError) as other:
        replace(sec, family=SectionFamily.RECTANGULAR)
    assert key_of(other.value) == "section.family_mismatch"
    with pytest.raises(DiagnosticError) as third:
        sec.require_rectangular()
    assert key_of(third.value) == "section.not_rectangular"
    rect = build_rectangular_section(ID, 1, b=u.Quantity.of(0.2, "m"), h=u.Quantity.of(0.4, "m"))
    with pytest.raises(DiagnosticError) as fourth:
        rect.require_parametric()
    assert key_of(fourth.value) == "section.not_parametric"


def test_shear_group_is_validated_when_present() -> None:
    sec = section_i()
    assert sec.shear is not None
    assert sec.shear.shear_z.value is not None
    forged = replace(sec.shear.shear_z, value=sec.shear.shear_z.value * 1.05)
    with pytest.raises(DiagnosticError) as info:
        replace(sec, shear=replace(sec.shear, shear_z=forged))
    assert key_of(info.value) == "section.shear_mismatch"
    invented = replace(sec.shear.shear_y, value=1e-3, status=PropertyStatus.CALCULADO)
    with pytest.raises(DiagnosticError) as invented_error:
        replace(sec, shear=replace(sec.shear, shear_y=invented))
    assert key_of(invented_error.value) == "section.shear_mismatch"  # NO_DISPONIBLE no se inventa
    contrasted = replace(sec.shear.shear_z, status=PropertyStatus.CONTRASTADO)
    with pytest.raises(DiagnosticError) as status:
        replace(sec, shear=replace(sec.shear, shear_z=contrasted))
    assert key_of(status.value) == "section.shear_invalid_status"
    foreign = replace(sec.shear.shear_z, method="shear-energy-straight-cut/2", value=2e-3)
    accepted = replace(
        sec,
        shear=replace(
            sec.shear,
            shear_z=foreign,
            shear_y=replace(sec.shear.shear_y, method="shear-energy-straight-cut/2"),
        ),
    )
    assert accepted.shear is not None
    assert accepted.shear.shear_z.value == 2e-3


def test_a_catalog_profile_never_carries_shear() -> None:
    sec = catalog_section()
    assert sec.shear is None
    donor = section_i().shear
    with pytest.raises(DiagnosticError) as info:
        replace(sec, shear=donor)
    assert key_of(info.value) == "section.shear_not_derivable"


# --- Rectángulo: D10-C ---------------------------------------------------------------------


def test_rectangle_shear_is_off_by_default_and_opt_in() -> None:
    q = u.Quantity.of
    plain = build_rectangular_section(ID, 1, b=q(0.2, "m"), h=q(0.4, "m"))
    assert plain.shear is None  # secciones, hashes y goldens aceptados no cambian
    with_shear = build_rectangular_section(ID, 1, b=q(0.2, "m"), h=q(0.4, "m"), with_shear=True)
    assert with_shear.shear is not None
    assert with_shear.shear.shear_z.value == pytest.approx(5.0 / 6.0 * 0.08, rel=1e-15)
    assert replace(with_shear, shear=None) == plain
    model = StructuralModel()
    assert model.add_rectangular_section(b=q(0.2, "m"), h=q(0.4, "m")).shear is None


def test_revising_a_rectangle_keeps_its_shear_group() -> None:
    q = u.Quantity.of
    model = StructuralModel()
    sec = model.register_section(
        build_rectangular_section(ID, 1, b=q(0.2, "m"), h=q(0.4, "m"), with_shear=True)
    )
    revised = model.revise_rectangular_section(sec.id, h=q(0.5, "m"))
    assert revised.shear is not None
    assert revised.shear.shear_z.value == pytest.approx(5.0 / 6.0 * 0.1, rel=1e-15)
    plain = model.add_rectangular_section(b=q(0.2, "m"), h=q(0.4, "m"))
    assert model.revise_rectangular_section(plain.id, h=q(0.5, "m")).shear is None


# --- Modelo: versionado ---------------------------------------------------------------------


def steel(
    model: StructuralModel, family: MaterialFamily = MaterialFamily.STEEL
) -> MaterialRevision:
    return model.add_material(
        name="M",
        family=family,
        source="test",
        elastic_modulus=u.Quantity.of(210_000, "N/mm²"),
        shear_modulus=u.Quantity.of(81_000, "N/mm²"),
        density=u.Quantity.of(7850, "kg/m³"),
    )


def test_changing_dimensions_makes_a_new_revision_and_members_keep_the_old() -> None:
    model = StructuralModel()
    material = steel(model)
    sec = model.add_parametric_section(I, mm(**I_OK), name="I 300")
    a = model.add_node(u.Quantity.of(0, "m"), u.Quantity.of(0, "m"), u.Quantity.of(0, "m"))
    b = model.add_node(u.Quantity.of(3, "m"), u.Quantity.of(0, "m"), u.Quantity.of(0, "m"))
    member = model.add_member(a.id, b.id, material=material.ref, section=sec.ref)
    revised = model.revise_parametric_section(sec.id, dimensions=mm(tf=12.0))
    assert (sec.revision, revised.revision) == (1, 2)
    assert model.member(member.id).section == sec.ref  # la barra conserva la revisión 1
    assert model.section(sec.ref).require_parametric().get("tf") == pytest.approx(0.0107)
    assert revised.require_parametric().get("tf") == pytest.approx(0.012)
    same = model.revise_parametric_section(sec.id, dimensions=mm(tf=12.0))
    assert same.revision == 2  # contenido idéntico: no hay revisión nueva
    renamed = model.revise_parametric_section(sec.id, name="otro")
    assert renamed.revision == 3
    assert renamed.name == "otro"


def test_revision_rejects_invalid_unknown_and_foreign_changes() -> None:
    model = StructuralModel()
    sec = model.add_parametric_section(CHS, mm(d=100, t=5))
    with pytest.raises(DiagnosticError) as invalid:
        model.revise_parametric_section(sec.id, dimensions=mm(t=60))
    assert key_of(invalid.value) == "section.geometry_wall_too_thick_circle"
    with pytest.raises(DiagnosticError) as unknown:
        model.revise_parametric_section(sec.id, dimensions=mm(h=60))
    assert key_of(unknown.value) == "section.geometry_dimensions"
    assert model.section(sec.ref).revision == 1
    assert len(model.section_history(sec.id)) == 1
    rect = model.add_rectangular_section(b=u.Quantity.of(0.2, "m"), h=u.Quantity.of(0.4, "m"))
    with pytest.raises(DiagnosticError) as foreign:
        model.revise_parametric_section(rect.id, dimensions=mm(t=1))
    assert key_of(foreign.value) == "section.not_parametric"
    with pytest.raises(DiagnosticError) as other:
        model.revise_rectangular_section(sec.id, h=u.Quantity.of(0.5, "m"))
    assert key_of(other.value) == "section.not_rectangular"


def test_readiness_follows_the_available_properties() -> None:
    model = StructuralModel()
    chs = model.add_parametric_section(CHS, mm(d=100, t=5))
    i = model.add_parametric_section(I, mm(**I_OK))
    rhs = model.add_parametric_section(RHS, mm(**RHS_OK))
    assert assess_section(chs).fem_ready
    assert assess_section(i).fem_missing == ("J",)
    assert assess_section(rhs).fem_missing == ("J",)
    for section in (chs, i, rhs):
        assert assess_section(section).code_coverage.value == "NO_COMPROBADO"


def test_parametric_profiles_are_metallic_for_the_material_compatibility_rule() -> None:
    model = StructuralModel()
    sec = model.add_parametric_section(SHS, mm(b=100, t=5))
    timber = steel(model, MaterialFamily.TIMBER)
    assert compatibility_errors(sec, timber, "B1")[0].message_key == (
        "section.replace_incompatible_material"
    )
    assert compatibility_errors(sec, steel(model), "B1") == ()
