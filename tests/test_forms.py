"""T-030 E2b-1 (R2-D7): formularios de nudo y barra sin Qt: análisis del texto, unidades,
rechazos con diagnóstico en español y órdenes que pasan por el editor."""

import math
from uuid import uuid4

import pytest

from gui_support import frame_model
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticCode, DiagnosticError
from veritas.core.geometry.editing import ModelEditor
from veritas.core.geometry.local_axes import ReferenceSource
from veritas.core.materials.material import MaterialFamily
from veritas.core.messages_es import render_message
from veritas.core.presentation import QuantityKind
from veritas.core.sections.parametric import ParametricShape as Shape
from veritas.gui import forms
from veritas.persistence.canonical import model_hash

TOL = 1e-12


def code_of(error: DiagnosticError) -> DiagnosticCode:
    return error.diagnostic.code


@pytest.mark.parametrize(
    ("text", "si"),
    [
        ("3", 3.0),
        ("3.5", 3.5),
        ("3,5", 3.5),
        (" 3,5 m ", 3.5),
        ("350 cm", 3.5),
        ("1500 mm", 1.5),
        ("-2e1", -20.0),
        (".5", 0.5),
    ],
)
def test_lengths_accept_decimal_comma_and_units(text: str, si: float) -> None:
    assert abs(forms.parse_length(text, field="X").to("m") - si) < TOL


@pytest.mark.parametrize("text", ["", "   ", "abc", "1..2", "inf", "nan", "1e999"])
def test_bad_lengths_are_rejected_with_the_field_name(text: str) -> None:
    with pytest.raises(DiagnosticError) as info:
        forms.parse_length(text, field="Coordenada Y")
    assert "Coordenada Y" in str(info.value) or "Coordenada Y" in render_message(
        info.value.diagnostic
    )


def test_a_length_with_another_dimension_is_rejected() -> None:
    with pytest.raises(DiagnosticError) as info:
        forms.parse_length("3 kN", field="X")
    assert code_of(info.value) is DiagnosticCode.UNITS_INCOMPATIBLE
    with pytest.raises(DiagnosticError) as unknown:
        forms.parse_length("3 zz", field="X")
    assert code_of(unknown.value) is DiagnosticCode.UNITS_UNKNOWN
    with pytest.raises(DiagnosticError) as stray:
        forms.parse_length("3 3", field="X")  # un segundo número no es una unidad
    assert code_of(stray.value) is DiagnosticCode.UNITS_UNKNOWN


def test_angles_default_to_degrees() -> None:
    assert abs(forms.parse_angle("90", field="a").to("rad") - math.pi / 2) < TOL
    assert abs(forms.parse_angle("1,5 rad", field="a").to("rad") - 1.5) < TOL
    with pytest.raises(DiagnosticError):
        forms.parse_angle("3 m", field="a")
    assert QuantityKind.ANGLE.dimension == u.ANGLE


def test_vectors_need_three_finite_components_and_cannot_be_zero() -> None:
    assert forms.parse_vector("0; 1; 0", field="v") == (0.0, 1.0, 0.0)
    assert forms.parse_vector("0 1,5 0", field="v") == (0.0, 1.5, 0.0)
    for bad in ("", "1; 2", "1; 2; 3; 4", "a; b; c", "1 m; 0; 0", "1,0,0"):
        with pytest.raises(DiagnosticError):
            forms.parse_vector(bad, field="v")
    with pytest.raises(DiagnosticError) as info:
        forms.parse_vector("0; 0; 0", field="v")
    assert info.value.diagnostic.message_key == "forms.zero_vector"


def test_node_form_creates_one_undoable_step() -> None:
    editor = ModelEditor()
    form = forms.parse_node("  N1 ", "0", "0,5", "300 cm")
    node = forms.create_node(editor, form)
    assert node.label == "N1"
    assert (node.position.x, node.position.y, node.position.z) == (0.0, 0.5, 3.0)
    assert len(editor.undo_names) == 1
    editor.undo()
    assert len(editor.model.snapshot().nodes) == 0


def test_a_rejected_node_form_never_touches_the_editor() -> None:
    editor = ModelEditor()
    gen = editor.generation
    with pytest.raises(DiagnosticError):
        forms.parse_node("N", "1", "x", "3")
    assert editor.generation == gen


def test_move_node_from_text_goes_through_the_editor() -> None:
    model, ids = frame_model()
    editor = ModelEditor(model)
    before = model_hash(model.snapshot())
    forms.move_node_from_text(editor, ids["n2"], "1", "0", "3,5")
    moved = model.node(ids["n2"]).position
    assert (moved.x, moved.y, moved.z) == (1.0, 0.0, 3.5)
    editor.undo()
    assert model_hash(model.snapshot()) == before


def test_choices_offer_the_latest_revision_with_readable_names() -> None:
    model, ids = frame_model()
    assert {c.value for c in forms.node_choices(model)} == {
        ids[k] for k in ("n1", "n2", "n3", "n4")
    }
    assert [c.text for c in forms.node_choices(model)] == ["N1", "N2", "N3", "N4"]
    (material,) = forms.material_choices(model)
    (section,) = forms.section_choices(model)
    assert "Acero" in material.text
    assert "R 200×400" in section.text
    revised = model.revise_material(material.value.entity_id, density=u.Quantity.of(7800, "kg/m³"))
    (latest,) = forms.material_choices(model)
    assert latest.value == revised.ref
    assert latest.value.revision == material.value.revision + 1


def test_member_form_requires_two_distinct_nodes_material_and_section() -> None:
    model, ids = frame_model()
    (material,) = forms.material_choices(model)
    (section,) = forms.section_choices(model)
    base = {
        "node_i": ids["n1"],
        "node_j": ids["n3"],
        "material": material.value,
        "section": section.value,
    }
    for missing in ("node_i", "node_j", "material", "section"):
        with pytest.raises(DiagnosticError) as info:
            forms.parse_member(**{**base, missing: None})  # type: ignore[arg-type]
        assert info.value.diagnostic.message_key == "forms.no_choice"
    with pytest.raises(DiagnosticError) as same:
        forms.parse_member(**{**base, "node_j": ids["n1"]})  # type: ignore[arg-type]
    assert same.value.diagnostic.message_key == "forms.same_nodes"
    with pytest.raises(DiagnosticError):
        forms.parse_member(**base, reference="0; 0; 0")  # type: ignore[arg-type]


def test_member_form_creates_a_member_with_orientation_in_one_step() -> None:
    model, ids = frame_model()
    editor = ModelEditor(model)
    (material,) = forms.material_choices(model)
    (section,) = forms.section_choices(model)
    before = model_hash(model.snapshot())
    form = forms.parse_member(
        node_i=ids["n1"],
        node_j=ids["n3"],
        material=material.value,
        section=section.value,
        reference="0; 1; 0",
        alpha="30",
        label="  Diagonal ",
    )
    member = forms.create_member(editor, form)
    assert member.label == "Diagonal"
    assert member.material == material.value
    assert len(editor.undo_names) == 1
    editor.undo()
    assert model_hash(model.snapshot()) == before
    editor.redo()
    assert model.member(member.id).label == "Diagonal"


def test_member_with_unknown_node_is_a_domain_rejection_not_a_crash() -> None:
    model, ids = frame_model()
    editor = ModelEditor(model)
    (material,) = forms.material_choices(model)
    (section,) = forms.section_choices(model)
    form = forms.parse_member(
        node_i=ids["n1"], node_j=uuid4(), material=material.value, section=section.value
    )
    before = model_hash(model.snapshot())
    with pytest.raises(DiagnosticError):
        forms.create_member(editor, form)
    assert model_hash(model.snapshot()) == before


def test_set_orientation_from_text_edits_through_the_editor() -> None:
    model, ids = frame_model()
    editor = ModelEditor(model)
    before = model_hash(model.snapshot())
    forms.set_orientation_from_text(editor, ids["d"], "0; 1; 0", "")
    assert model_hash(model.snapshot()) != before
    editor.undo()
    assert model_hash(model.snapshot()) == before
    with pytest.raises(DiagnosticError):
        forms.set_orientation_from_text(editor, ids["d"], "no", "")


# --- Materiales y secciones (E2b-2) -----------------------------------------------------------


def test_material_form_creates_an_undoable_material_available_to_members() -> None:
    editor = ModelEditor()
    form = forms.parse_material(
        name=" Acero S275 ",
        family=MaterialFamily.STEEL,
        elastic_modulus="210000 N/mm²",
        shear_modulus="81000",  # sin unidad: la del perfil SI (Pa)
        density="7850 kg/m³",
    )
    assert abs(form.shear_modulus.to("Pa") - 81000.0) < TOL
    material = forms.create_material(editor, form)
    assert material.name == "Acero S275"
    assert material.source == forms.USER_SOURCE
    assert [c.value for c in forms.material_choices(editor.model)] == [material.ref]
    editor.undo()
    assert forms.material_choices(editor.model) == ()
    editor.redo()
    assert len(forms.material_choices(editor.model)) == 1


def test_material_form_rejects_empty_name_family_and_wrong_units() -> None:
    base = {
        "name": "A",
        "family": MaterialFamily.TIMBER,
        "elastic_modulus": "11000 N/mm²",
        "shear_modulus": "690 N/mm²",
        "density": "420 kg/m³",
    }
    forms.parse_material(**base)  # type: ignore[arg-type]
    for field, bad in (
        ("name", " "),
        ("family", None),
        ("elastic_modulus", "3 m"),
        ("density", "7850 N/mm²"),
        ("shear_modulus", ""),
    ):
        with pytest.raises(DiagnosticError):
            forms.parse_material(**{**base, field: bad})  # type: ignore[arg-type]


def test_timber_has_no_suggested_values_and_steel_presets_parse() -> None:
    assert MaterialFamily.TIMBER not in forms.MATERIAL_PRESETS
    for e, g, rho in forms.MATERIAL_PRESETS.values():
        forms.parse_quantity(e, QuantityKind.STRESS, field="E")
        forms.parse_quantity(g, QuantityKind.STRESS, field="G")
        forms.parse_quantity(rho, QuantityKind.DENSITY, field="d")


def test_rectangular_section_form_creates_an_undoable_section() -> None:
    editor = ModelEditor()
    form = forms.parse_rectangular_section(name="R 20x40", b="20 cm", h="0,4")
    section = forms.create_rectangular_section(editor, form)
    assert section.name == "R 20x40"
    assert len(forms.section_choices(editor.model)) == 1
    editor.undo()
    assert forms.section_choices(editor.model) == ()
    with pytest.raises(DiagnosticError):
        forms.parse_rectangular_section(name="", b="x", h="1")
    with pytest.raises(DiagnosticError):
        forms.create_rectangular_section(
            editor, forms.parse_rectangular_section(name="", b="0", h="1")
        )
    assert forms.section_choices(editor.model) == ()


def test_parametric_form_handles_optional_radii_and_rejects_bad_geometry() -> None:
    assert forms.parametric_fields(Shape.CHS) == (("d", False), ("t", False))
    assert forms.parametric_fields(Shape.I)[-1] == ("r", True)
    editor = ModelEditor()
    form = forms.parse_parametric_section(
        name="IPE-like",
        shape=Shape.I,
        values={"h": "200 mm", "b": "100 mm", "tw": "5.6 mm", "tf": "8.5 mm", "r": ""},
    )
    assert "r" not in form.dimensions
    forms.create_parametric_section(editor, form)
    assert len(forms.section_choices(editor.model)) == 1
    with pytest.raises(DiagnosticError):  # falta una dimensión obligatoria
        forms.parse_parametric_section(name="", shape=Shape.CHS, values={"d": "100 mm"})
    with pytest.raises(DiagnosticError):  # forma sin elegir
        forms.parse_parametric_section(name="", shape=None, values={})
    thick = forms.parse_parametric_section(
        name="", shape=Shape.CHS, values={"d": "100 mm", "t": "60 mm"}
    )
    before = model_hash(editor.model.snapshot())
    with pytest.raises(DiagnosticError):  # geometría inválida: no se registra nada
        forms.create_parametric_section(editor, thick)
    assert model_hash(editor.model.snapshot()) == before


def test_a_cell_edit_changes_one_coordinate_and_keeps_the_others_exact() -> None:
    model, _ = frame_model()
    editor = ModelEditor(model)
    node = model.add_node(
        u.Quantity.of(0.123456789, "m"), u.Quantity.of(0.987654321, "m"), u.Quantity.of(2, "m")
    )
    forms.move_node_coordinate(editor, node.id, "z", "250 cm")
    p = model.node(node.id).position
    assert (p.x, p.y) == (0.123456789, 0.987654321)  # no se redondean al editar otra celda
    assert abs(p.z - 2.5) < TOL
    with pytest.raises(DiagnosticError):
        forms.move_node_coordinate(editor, node.id, "z", "abc")
    with pytest.raises(DiagnosticError):
        forms.move_node_coordinate(editor, node.id, "w", "1")


@pytest.mark.parametrize("text", ["1,000", "12,345 m", "-1,000", "999,999"])
def test_ambiguous_thousands_separators_are_rejected(text: str) -> None:
    with pytest.raises(DiagnosticError) as info:
        forms.parse_length(text, field="X")
    assert info.value.diagnostic.message_key == "forms.invalid_number"


@pytest.mark.parametrize(("text", "si"), [("0,125", 0.125), ("1,5", 1.5), ("10,25", 10.25)])
def test_decimal_commas_that_are_not_thousands_still_parse(text: str, si: float) -> None:
    assert abs(forms.parse_length(text, field="X").to("m") - si) < TOL


def test_session_forms_and_deletion_do_not_import_qt() -> None:
    import subprocess  # noqa: PLC0415
    import sys  # noqa: PLC0415

    code = "; ".join(
        (
            "import sys, veritas.gui.session, veritas.gui.forms, veritas.gui.deletion",
            "import veritas.gui.capture, veritas.gui.gestures",
            "bad = [m for m in ('PySide6', 'pyvista', 'pyvistaqt', 'vtk') if m in sys.modules]",
            "sys.exit(1 if bad else 0)",
        )
    )
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, check=False)
    assert result.returncode == 0, result.stderr.decode()


def test_orientation_defaults_prefill_the_current_values_when_common() -> None:
    """H1: el diálogo muestra la orientación actual si es común a toda la selección."""
    model, ids = frame_model()
    editor = ModelEditor(model)
    assert forms.orientation_defaults(model, [ids["d"]]) == forms.OrientationDefaults()
    forms.set_orientation_from_text(editor, ids["d"], "0; 1; 0", "30")
    shown = forms.orientation_defaults(model, [ids["d"]])
    assert forms.parse_vector(shown.reference, field="r") == pytest.approx((0.0, 1.0, 0.0))
    assert float(shown.alpha) == pytest.approx(30.0)
    assert not (shown.reference_mixed)
    assert not (shown.alpha_mixed)


def test_accepting_the_prefilled_dialog_changes_nothing_even_with_rounding_text() -> None:
    """R1: reaplicar el texto redondeado no crea revisión ni paso de deshacer."""
    model, ids = frame_model()
    editor = ModelEditor(model)
    forms.set_orientation_from_text(editor, ids["d"], "1; 2; 3", "21.2")
    revision, steps = model.member(ids["d"]).revision, len(editor.undo_names)
    shown = forms.orientation_defaults(model, [ids["d"]])
    forms.orient_members(editor, [ids["d"]], shown.reference, shown.alpha, shown)
    assert model.member(ids["d"]).revision == revision
    assert len(editor.undo_names) == steps


def test_mixed_selection_keeps_each_explicit_reference_when_only_alpha_changes() -> None:
    """R2: con orientaciones distintas, vacío = conservar; cambiar solo el giro no resetea."""
    model, ids = frame_model()
    editor = ModelEditor(model)
    members = [ids["d"], next(i for i in ids.values() if i != ids["d"] and _is_member(model, i))]
    forms.set_orientation_from_text(editor, members[0], "0; 1; 0", "")
    forms.set_orientation_from_text(editor, members[1], "1; 1; 0", "")
    before = [model.member(m).orientation for m in members]
    shown = forms.orientation_defaults(model, members)
    assert shown.reference == ""
    assert shown.reference_mixed
    assert not shown.alpha_mixed
    steps = len(editor.undo_names)
    forms.orient_members(editor, members, "", "15", shown)
    assert len(editor.undo_names) == steps + 1  # una sola transacción
    for member, old in zip(members, before, strict=True):
        new = model.member(member).orientation
        assert new.alpha == pytest.approx(math.radians(15.0))
        assert new.reference.x == pytest.approx(old.reference.x)
        assert new.reference.y == pytest.approx(old.reference.y)
        assert new.reference.z == pytest.approx(old.reference.z)
        assert new.source is old.source
    # alfa distinto con la misma referencia: la referencia sigue mostrándose
    same = forms.orientation_defaults(model, [members[0]])
    assert same.reference != ""
    assert not same.reference_mixed
    forms.set_orientation_from_text(editor, members[1], "0; 1; 0", "40")
    both = forms.orientation_defaults(model, members)
    assert both.alpha_mixed
    assert not both.reference_mixed
    assert both.reference != ""


def test_clearing_a_prefilled_reference_resets_it_to_the_default() -> None:
    model, ids = frame_model()
    editor = ModelEditor(model)
    forms.set_orientation_from_text(editor, ids["d"], "0; 1; 0", "")
    shown = forms.orientation_defaults(model, [ids["d"]])
    forms.orient_members(editor, [ids["d"]], "", "", shown)
    assert model.member(ids["d"]).orientation.source is not ReferenceSource.EXPLICIT


def test_invalid_orientation_text_changes_nothing() -> None:
    model, ids = frame_model()
    editor = ModelEditor(model)
    before = model_hash(model.snapshot())
    with pytest.raises(DiagnosticError):
        forms.orient_members(editor, [ids["d"]], "no", "", forms.OrientationDefaults())
    assert model_hash(model.snapshot()) == before


@pytest.mark.parametrize("text", ["1.000", "2.500 mm", "1,000", "12.345", "-3.000"])
def test_thousands_looking_numbers_are_rejected_as_ambiguous(text: str) -> None:
    """R7: «1.000» puede ser mil o uno: se rechaza, igual que «1,000»."""
    with pytest.raises(DiagnosticError):
        forms.parse_length(text, field="X")


@pytest.mark.parametrize(
    ("text", "si"), [("0.125", 0.125), ("1.5", 1.5), ("1.50", 1.5), ("10.25", 10.25)]
)
def test_ordinary_decimals_are_still_accepted(text: str, si: float) -> None:
    assert forms.parse_length(text, field="X").si_value == pytest.approx(si)


def _is_member(model: object, ident: object) -> bool:
    try:
        model.member(ident)  # type: ignore[attr-defined]
    except Exception:
        return False
    return True
