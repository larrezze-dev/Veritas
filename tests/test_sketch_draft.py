"""T-029 E2: borrador de croquis (EDT-004, EDT-012), plantillas U, L, T (TK-027) y revisiones."""

import math
from dataclasses import replace
from uuid import UUID

import pytest

from test_freeform_section import composite
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticError
from veritas.core.geometry.model import StructuralModel
from veritas.core.sections.parametric import ParametricShape
from veritas.core.sections.polygon import principal_axes
from veritas.core.sections.properties import PropertyStatus
from veritas.core.sections.section import (
    SectionFamily,
    SectionRevision,
    build_parametric_section,
)
from veritas.section_editor import (
    TEMPLATE_DIMENSIONS,
    SketchContour,
    SketchDraft,
    template_draft,
)

Pt = tuple[float, float]


def uid(n: int) -> UUID:
    return UUID(int=n, version=4)


def quantities(points: list[Pt], unit: str = "m") -> tuple[tuple[u.Quantity, u.Quantity], ...]:
    return tuple((u.Quantity.of(y, unit), u.Quantity.of(z, unit)) for y, z in points)


def draft(
    outer: list[Pt], holes: list[list[Pt]] | None = None, *, closed: bool = True
) -> SketchDraft:
    return SketchDraft(
        SketchContour(quantities(outer), closed),
        tuple(SketchContour(quantities(h)) for h in holes or []),
    )


SQUARE: list[Pt] = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]
BOWTIE: list[Pt] = [(0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0)]


def keys(sketch: SketchDraft) -> list[str]:
    return [d.message_key for d in sketch.diagnostics()]


def value(section: SectionRevision, name: str) -> float:
    found = getattr(section.properties, name).value
    assert found is not None
    return float(found)


# --- Borrador ----------------------------------------------------------------------------


def test_a_valid_draft_builds_a_revision_and_registers_nothing() -> None:
    sketch = draft(SQUARE, [[(0.2, 0.2), (0.4, 0.2), (0.4, 0.4), (0.2, 0.4)]])
    assert sketch.valid
    section = sketch.build(uid(1), 3, name="cuadro")
    assert (section.family, section.revision, section.name) == (SectionFamily.FREEFORM, 3, "cuadro")
    assert value(section, "area") == pytest.approx(1.0 - 0.04, rel=1e-13)


def test_the_draft_accumulates_every_problem() -> None:
    sketch = draft(BOWTIE, [[(5.0, 5.0), (6.0, 5.0), (6.0, 6.0)]])
    assert keys(sketch) == ["section.sketch_self_intersection"]  # el hueco se juzga después
    sketch = draft([(0.0, 0.0), (1.0, 0.0)], [[(0.0, 0.0), (1.0, 0.0)]], closed=False)
    assert keys(sketch) == ["section.sketch_open_contour"]
    both = draft(SQUARE, [BOWTIE, [(0.1, 0.1), (0.2, 0.1)]])
    assert keys(both) == [
        "section.sketch_self_intersection",
        "section.sketch_too_few_vertices",
    ]


def test_an_open_contour_is_diagnosed_and_never_built() -> None:
    sketch = draft(SQUARE, closed=False)
    assert keys(sketch) == ["section.sketch_open_contour"]
    assert not sketch.valid
    with pytest.raises(DiagnosticError) as error:
        sketch.build(uid(1))
    assert error.value.diagnostic.message_key == "section.sketch_open_contour"
    hole_open = SketchDraft(
        SketchContour(quantities(SQUARE)),
        (SketchContour(quantities([(0.2, 0.2), (0.4, 0.2), (0.4, 0.4)]), closed=False),),
    )
    found = hole_open.diagnostics()
    assert [d.message_key for d in found] == ["section.sketch_open_contour"]
    assert found[0].location is not None
    assert found[0].location.field == "hole[0]"


def test_units_and_values_are_diagnosed_not_converted() -> None:
    with_area = SketchContour(((u.Quantity.of(1.0, "m²"), u.Quantity.of(0.0, "m")),) * 3)
    assert keys(SketchDraft(with_area))  # una área no es una longitud
    with pytest.raises(DiagnosticError):  # Quantity ya rechaza lo no finito
        u.Quantity(math.inf, u.LENGTH)
    millimetres = draft([(0.0, 0.0), (1000.0, 0.0), (1000.0, 1000.0), (0.0, 1000.0)])
    assert value(millimetres.build(uid(1)), "area") == pytest.approx(1.0e6)  # sin unidad: m
    in_mm = SketchDraft(SketchContour(quantities([(0, 0), (200, 0), (200, 100), (0, 100)], "mm")))
    assert value(in_mm.build(uid(1)), "area") == pytest.approx(0.02)


def test_from_section_round_trips_the_sketch() -> None:
    section = draft(SQUARE, [[(0.2, 0.2), (0.4, 0.2), (0.4, 0.4), (0.2, 0.4)]]).build(uid(1))
    again = SketchDraft.from_section(section).build(uid(1))
    assert again == section
    with pytest.raises(DiagnosticError):
        SketchDraft.from_section(
            build_parametric_section(
                uid(2),
                1,
                shape=ParametricShape.CHS,
                dimensions={"d": u.Quantity.of(0.1, "m"), "t": u.Quantity.of(0.005, "m")},
            )
        )


# --- Plantillas U, L y T -----------------------------------------------------------------


def mm(**dims: float) -> dict[str, u.Quantity]:
    return {k: u.Quantity.of(v, "mm") for k, v in dims.items()}


def test_l_template_matches_the_steiner_reference() -> None:
    h, b, t = 0.2, 0.15, 0.02
    section = template_draft("l", mm(h=200, b=150, t=20)).build(uid(1))
    ref = composite([(0, 0, b, t, 1.0), (0, t, t, h, 1.0)])
    for name, expected in ref.items():
        assert value(section, name) == pytest.approx(expected, rel=1e-12, abs=1e-15), name
    assert value(section, "iyz") != 0.0
    axes = principal_axes(value(section, "iy"), value(section, "iz"), value(section, "iyz"))
    assert axes.i_max * axes.i_min == pytest.approx(
        value(section, "iy") * value(section, "iz") - value(section, "iyz") ** 2, rel=1e-10
    )


def test_u_template_matches_the_steiner_reference() -> None:
    h, b, tw, tf = 0.3, 0.1, 0.008, 0.012
    section = template_draft("u", mm(h=300, b=100, tw=8, tf=12)).build(uid(1))
    ref = composite([(0, 0, b, tf, 1.0), (0, h - tf, b, h, 1.0), (0, tf, tw, h - tf, 1.0)])
    for name, expected in ref.items():
        assert value(section, name) == pytest.approx(expected, rel=1e-12, abs=1e-15), name
    assert value(section, "centroid_y") > tw  # el centroide de la U no está en el alma
    assert value(section, "centroid_z") == pytest.approx(h / 2, rel=1e-13)
    assert abs(value(section, "iyz")) < 1e-12 * value(section, "iy")  # simetría respecto a z = h/2


def test_t_template_matches_the_steiner_reference() -> None:
    h, b, tw, tf = 0.2, 0.15, 0.01, 0.02
    section = template_draft("t", mm(h=200, b=150, tw=10, tf=20)).build(uid(1))
    web_left = (b - tw) / 2
    ref = composite([(0, h - tf, b, h, 1.0), (web_left, 0, web_left + tw, h - tf, 1.0)])
    for name, expected in ref.items():
        assert value(section, name) == pytest.approx(expected, rel=1e-12, abs=1e-15), name
    assert value(section, "centroid_y") == pytest.approx(b / 2, rel=1e-13)
    assert abs(value(section, "iyz")) < 1e-12 * value(section, "iy")


def test_the_polygon_of_an_i_matches_the_closed_form_of_the_parametric_i() -> None:
    """Contraste entre dos vías independientes: polígono general frente a fórmula cerrada."""
    h, b, tw, tf = 0.1, 0.05, 0.004, 0.006
    left, right = (b - tw) / 2, (b + tw) / 2
    outline: list[Pt] = [
        (0, 0), (b, 0), (b, tf), (right, tf), (right, h - tf), (b, h - tf),
        (b, h), (0, h), (0, h - tf), (left, h - tf), (left, tf), (0, tf),
    ]  # fmt: skip
    free = draft(outline).build(uid(1))
    closed = build_parametric_section(
        uid(2),
        1,
        shape=ParametricShape.I,
        dimensions=mm(h=100, b=50, tw=4, tf=6),
    )
    for name in ("area", "iy", "iz"):
        assert value(free, name) == pytest.approx(value(closed, name), rel=1e-12), name
    assert value(free, "centroid_y") == pytest.approx(b / 2, rel=1e-12)
    assert value(free, "centroid_z") == pytest.approx(h / 2, rel=1e-12)


@pytest.mark.parametrize(
    ("shape", "dims", "field"),
    [
        ("l", dict(h=200, b=150, t=150), "t"),  # t = b
        ("l", dict(h=200, b=150, t=0), "t"),
        ("l", dict(h=200, b=150, t=-1), "t"),
        ("u", dict(h=300, b=100, tw=100, tf=12), "tw"),  # tw = b
        ("u", dict(h=300, b=100, tw=8, tf=150), "tf"),  # 2tf = h
        ("t", dict(h=200, b=150, tw=150, tf=20), "tw"),
        ("t", dict(h=200, b=150, tw=10, tf=200), "tf"),  # tf = h
    ],
)
def test_invalid_template_measures_are_rejected_with_the_field(
    shape: str, dims: dict[str, float], field: str
) -> None:
    with pytest.raises(DiagnosticError) as error:
        template_draft(shape, mm(**dims))
    assert error.value.diagnostic.location is not None
    assert error.value.diagnostic.location.field == field


def test_template_names_and_shape_are_checked() -> None:
    for shape, expected in TEMPLATE_DIMENSIONS.items():
        assert len(expected) >= 3
        with pytest.raises(DiagnosticError) as missing:
            template_draft(shape, mm(**{expected[0]: 100}))
        assert missing.value.diagnostic.message_key == "editor.dimension_name"
    with pytest.raises(DiagnosticError):
        template_draft("l", mm(h=200, b=150, t=20, q=1))
    with pytest.raises(DiagnosticError):
        template_draft("x", mm(h=200))
    with pytest.raises(DiagnosticError):
        template_draft("l", {"h": 200, "b": 150, "t": 20})  # type: ignore[dict-item]


# --- Revisiones (EDT-010 parcial) --------------------------------------------------------


def test_changing_the_sketch_makes_a_new_revision_and_members_keep_the_old() -> None:
    model = StructuralModel()
    section = model.add_freeform_section(quantities(SQUARE), name="cuadro")
    assert section.revision == 1
    same = model.revise_freeform_section(section.id)
    assert same == section  # sin cambios: la misma revisión
    bigger = model.revise_freeform_section(
        section.id, outer=quantities([(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)])
    )
    assert bigger.revision == 2
    assert value(bigger, "area") == pytest.approx(2.0)
    assert value(model.section(section.ref), "area") == pytest.approx(1.0)  # la 1 sigue intacta
    with_hole = model.revise_freeform_section(
        section.id, holes=[quantities([(0.2, 0.2), (0.4, 0.2), (0.4, 0.4), (0.2, 0.4)])]
    )
    assert with_hole.revision == 3
    assert len(with_hole.require_freeform().holes) == 1
    renamed = model.revise_freeform_section(section.id, name="otro")
    assert (renamed.revision, renamed.name) == (4, "otro")


def test_an_invalid_revision_is_rejected_and_nothing_is_registered() -> None:
    model = StructuralModel()
    section = model.add_freeform_section(quantities(SQUARE))
    with pytest.raises(DiagnosticError):
        model.revise_freeform_section(section.id, outer=quantities(BOWTIE))
    assert len(model.snapshot().sections) == 1
    with pytest.raises(DiagnosticError):
        model.add_freeform_section(quantities(BOWTIE))
    assert len(model.snapshot().sections) == 1


def test_a_geometry_change_starts_again_from_calculated_properties() -> None:
    model = StructuralModel()
    section = model.add_freeform_section(quantities(SQUARE))
    revised = model.revise_freeform_section(
        section.id, outer=quantities([(0.0, 0.0), (3.0, 0.0), (3.0, 1.0), (0.0, 1.0)])
    )
    assert revised.properties.area.status.value == "CALCULADO"
    assert value(revised, "area") == pytest.approx(3.0)


def test_nearly_degenerate_template_measures_are_rejected_by_the_draft_itself() -> None:
    eps = 1e-12  # m: bajo la tolerancia geométrica relativa (1e-9 · diagonal)
    cases = {
        "u": dict(h=0.3, b=0.1, tw=0.008, tf=0.15 - eps),  # 2·tf = h − 2ε
        "l": dict(h=0.2, b=0.15, t=0.15 - eps),  # t = b − ε
        "t": dict(h=0.2, b=0.15, tw=0.15 - eps, tf=0.02),  # tw = b − ε
    }
    for shape, dims in cases.items():
        with pytest.raises(DiagnosticError):
            template_draft(shape, {k: u.Quantity.of(v, "m") for k, v in dims.items()})


def test_revising_after_a_contrast_keeps_it_only_when_nothing_changes() -> None:
    model = StructuralModel()
    section = model.add_freeform_section(quantities(SQUARE), name="cuadro")
    contrasted = replace(
        section,
        revision=2,
        properties=replace(
            section.properties,
            area=replace(section.properties.area, status=PropertyStatus.CONTRASTADO, origin="x"),
        ),
    )
    model.register_section(contrasted)
    assert model.revise_freeform_section(section.id) == contrasted  # mismo contenido: se conserva
    bigger = model.revise_freeform_section(
        section.id, outer=quantities([(0.0, 0.0), (2.0, 0.0), (2.0, 1.0), (0.0, 1.0)])
    )
    assert bigger.revision == 3
    assert bigger.properties.area.status is PropertyStatus.CALCULADO
    renamed = model.revise_freeform_section(section.id, name="otro")
    assert renamed.properties.area.status is PropertyStatus.CALCULADO  # sin herencia
