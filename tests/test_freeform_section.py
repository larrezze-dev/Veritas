"""T-029 E1: croquis libre poligonal (EDT-004, EDT-005; T-SEC-003; ADR-013).

Las referencias son independientes de la fórmula del cordón: composición de rectángulos con el
teorema de Steiner y transformación de tensores de inercia.
"""

import json
import math
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticError
from veritas.core.geometry.model import StructuralModel
from veritas.core.materials.material import MaterialFamily
from veritas.core.sections.polygon import (
    FreeformParameters,
    freeform_properties,
    principal_axes,
)
from veritas.core.sections.properties import PropertyStatus, SectionProperty
from veritas.core.sections.readiness import assess_section
from veritas.core.sections.section import (
    SectionFamily,
    SectionRevision,
    build_freeform_section,
)
from veritas.core.sections.shear import rectangular_shear
from veritas.persistence.migrations import CURRENT_SCHEMA_VERSION, migrate
from veritas.persistence.project import open_project, save_project

Pt = tuple[float, float]


def uid(n: int) -> UUID:
    return UUID(int=n, version=4)


def rect(y0: float, z0: float, y1: float, z1: float) -> list[Pt]:
    return [(y0, z0), (y1, z0), (y1, z1), (y0, z1)]


def metres(points: list[Pt]) -> list[tuple[u.Quantity, u.Quantity]]:
    return [(u.Quantity.of(y, "m"), u.Quantity.of(z, "m")) for y, z in points]


def build(outer: list[Pt], holes: list[list[Pt]] | None = None) -> SectionRevision:
    return build_freeform_section(
        uid(1), 1, outer=metres(outer), holes=[metres(h) for h in holes or []], name="croquis"
    )


def value(section: SectionRevision, name: str) -> float:
    found = getattr(section.properties, name).value
    assert found is not None
    return float(found)


def key_of(error: DiagnosticError) -> str:
    return error.diagnostic.message_key


def composite(parts: list[tuple[float, float, float, float, float]]) -> dict[str, float]:
    """Rectángulos (y0, z0, y1, z1, signo): A, centroide, Iy, Iz, Iyz por Steiner."""
    area = sum(s * (y1 - y0) * (z1 - z0) for y0, z0, y1, z1, s in parts)
    cy = sum(s * (y1 - y0) * (z1 - z0) * (y0 + y1) / 2 for y0, z0, y1, z1, s in parts) / area
    cz = sum(s * (y1 - y0) * (z1 - z0) * (z0 + z1) / 2 for y0, z0, y1, z1, s in parts) / area
    iy = iz = iyz = 0.0
    for y0, z0, y1, z1, s in parts:
        b, h = y1 - y0, z1 - z0
        dy, dz = (y0 + y1) / 2 - cy, (z0 + z1) / 2 - cz
        iy += s * (b * h**3 / 12 + b * h * dz * dz)
        iz += s * (h * b**3 / 12 + b * h * dy * dy)
        iyz += s * (b * h * dy * dz)
    return {"area": area, "centroid_y": cy, "centroid_z": cz, "iy": iy, "iz": iz, "iyz": iyz}


def assert_matches(section: SectionRevision, expected: dict[str, float]) -> None:
    for name, ref in expected.items():
        got = value(section, name)
        assert got == pytest.approx(ref, rel=1e-12, abs=1e-15), name


# --- Propiedades -------------------------------------------------------------------------


def test_rectangle_offset_from_origin() -> None:
    section = build(rect(1.0, 2.0, 1.2, 2.5))
    assert_matches(
        section,
        {
            "area": 0.2 * 0.5,
            "centroid_y": 1.1,
            "centroid_z": 2.25,
            "iy": 0.2 * 0.5**3 / 12,
            "iz": 0.5 * 0.2**3 / 12,
            "iyz": 0.0,
        },
    )


def test_polygon_with_hole_gives_net_area_and_centroid() -> None:  # T-SEC-003
    outer, hole = rect(0.0, 0.0, 0.4, 0.6), rect(0.1, 0.2, 0.25, 0.5)
    section = build(outer, [hole])
    assert_matches(section, composite([(0, 0, 0.4, 0.6, 1.0), (0.1, 0.2, 0.25, 0.5, -1.0)]))
    assert value(section, "area") == pytest.approx(0.24 - 0.045, rel=1e-13)


def test_square_ring_matches_the_difference_of_squares() -> None:
    section = build(rect(-0.1, -0.1, 0.1, 0.1), [rect(-0.05, -0.05, 0.05, 0.05)])
    expected = (0.2**4 - 0.1**4) / 12
    assert value(section, "area") == pytest.approx(0.04 - 0.01, rel=1e-13)
    assert value(section, "iy") == pytest.approx(expected, rel=1e-12)
    assert value(section, "iz") == pytest.approx(expected, rel=1e-12)
    assert value(section, "centroid_y") == pytest.approx(0.0, abs=1e-16)


def test_asymmetric_l_section() -> None:
    # L de alas 0,2 × 0,02 (horizontal) y 0,02 × 0,18 (vertical, sobre el ala).
    outline = [(0, 0), (0.2, 0), (0.2, 0.02), (0.02, 0.02), (0.02, 0.2), (0, 0.2)]
    section = build(outline)
    expected = composite([(0, 0, 0.2, 0.02, 1.0), (0, 0.02, 0.02, 0.2, 1.0)])
    assert_matches(section, expected)
    assert value(section, "iyz") != 0.0
    axes = principal_axes(value(section, "iy"), value(section, "iz"), value(section, "iyz"))
    assert axes.i_max + axes.i_min == pytest.approx(value(section, "iy") + value(section, "iz"))
    assert axes.i_max * axes.i_min == pytest.approx(
        value(section, "iy") * value(section, "iz") - value(section, "iyz") ** 2, rel=1e-10
    )


def test_representation_is_independent_of_orientation_start_and_hole_order() -> None:
    outer = rect(0.0, 0.0, 1.0, 1.0)
    holes = [rect(0.1, 0.1, 0.2, 0.2), rect(0.6, 0.6, 0.8, 0.8)]
    first = build(outer, holes)
    second = build(outer[2:] + outer[:2], [list(reversed(h)) for h in reversed(holes)])
    assert first.parameters == second.parameters
    assert first.properties == second.properties


def test_rotated_rectangle_has_the_tensor_transformed_inertia_and_principal_angle() -> None:
    b, h, theta = 0.3, 0.1, math.radians(30.0)
    c, s = math.cos(theta), math.sin(theta)
    local = [(-b / 2, -h / 2), (b / 2, -h / 2), (b / 2, h / 2), (-b / 2, h / 2)]
    rotated = [(c * y - s * z, s * y + c * z) for y, z in local]
    section = build(rotated)
    i_u, i_v = h * b**3 / 12, b * h**3 / 12  # ∫y'² e ∫z'² en ejes de la barra
    iz = i_u * c * c + i_v * s * s  # ∫y²
    iy = i_u * s * s + i_v * c * c  # ∫z²
    iyz = (i_u - i_v) * s * c  # ∫yz
    assert value(section, "iy") == pytest.approx(iy, rel=1e-12)
    assert value(section, "iz") == pytest.approx(iz, rel=1e-12)
    assert value(section, "iyz") == pytest.approx(iyz, rel=1e-12)
    axes = principal_axes(iy, iz, iyz)
    # ADR-001: i_max = ∫y'² es la inercia respecto al eje z' (perpendicular al lado largo, a
    # θ + 90° ≡ θ − 90°); el eje de i_min (lado largo) está en θ.
    assert axes.angle == pytest.approx(theta - math.pi / 2, rel=1e-12)
    assert axes.i_max == pytest.approx(i_u, rel=1e-12)
    assert axes.i_min == pytest.approx(i_v, rel=1e-12)


def test_principal_axes_of_a_circle_like_tensor_and_of_a_symmetric_one() -> None:
    assert principal_axes(2.0, 2.0, 0.0).angle == pytest.approx(math.pi / 2)
    axes = principal_axes(1.0, 3.0, 0.0)  # Iz = ∫y² = 3 es la inercia respecto al eje z
    assert (axes.i_max, axes.i_min) == (3.0, 1.0)
    assert axes.angle == pytest.approx(math.pi / 2)
    assert principal_axes(3.0, 1.0, 0.0).angle == 0.0  # máxima respecto al eje y
    assert principal_axes(1.0, 0.5, -0.0).angle == 0.0  # cero negativo: sin salirse del rango
    for angle in (principal_axes(1.0, 0.5, 0.2).angle, principal_axes(1.0, 3.0, -0.4).angle):
        assert -math.pi / 2 < angle <= math.pi / 2


@pytest.mark.parametrize(
    "tensor", [(0.0, 1.0, 0.0), (1.0, -1.0, 0.0), (1.0, 1.0, 1.5), (math.nan, 1.0, 0.0)]
)
def test_principal_axes_reject_an_inadmissible_tensor(tensor: tuple[float, float, float]) -> None:
    with pytest.raises(DiagnosticError):
        principal_axes(*tensor)


def test_far_from_origin_keeps_precision() -> None:
    near = build(rect(0.0, 0.0, 0.2, 0.5))
    far = build(rect(1000.0, 2000.0, 1000.2, 2000.5))
    for name in ("area", "iy", "iz"):
        assert value(far, name) == pytest.approx(value(near, name), rel=1e-9)
    assert abs(value(far, "iyz")) < 1e-9 * value(near, "iy")


def test_torsion_is_unavailable_with_motive_and_no_shear() -> None:
    section = build(rect(0.0, 0.0, 0.2, 0.5))
    j = section.properties.torsion_constant
    assert j.status is PropertyStatus.NO_DISPONIBLE
    assert j.value is None
    assert j.origin
    assert section.shear is None
    assert section.family is SectionFamily.FREEFORM


def test_readiness_declares_the_freeform_section_not_fem_ready() -> None:
    readiness = assess_section(build(rect(0.0, 0.0, 0.2, 0.5)))
    assert not readiness.fem_ready
    assert "J" in readiness.fem_missing


# --- Validación EDT-004 ------------------------------------------------------------------

SQUARE = rect(0.0, 0.0, 1.0, 1.0)
BOWTIE = [(0.0, 0.0), (1.0, 1.0), (1.0, 0.0), (0.0, 1.0)]
NONCONVEX_OK: list[Pt] = [(0, 0), (3, 0), (3, 3), (2, 3), (2, 1), (1, 1), (1, 3), (0, 3)]  # U
NAN = float("nan")

INVALID: list[tuple[str, list[Pt], list[list[Pt]], str, str]] = [
    ("bowtie", BOWTIE, [], "section.sketch_self_intersection", "outer"),
    ("two vertices", [(0, 0), (1, 0)], [], "section.sketch_too_few_vertices", "outer"),
    ("empty", [], [], "section.sketch_too_few_vertices", "outer"),
    ("duplicate", [(0, 0), (0, 0), (1, 0), (1, 1)], [], "section.sketch_duplicate_vertex", "outer"),
    ("closing duplicate", [*SQUARE, SQUARE[0]], [], "section.sketch_duplicate_vertex", "outer"),
    ("zero area", [(0, 0), (1, 0), (2, 0)], [], "section.sketch_zero_area", "outer"),
    ("spike", [(0, 0), (2, 0), (1, 0), (1, 1)], [], "section.sketch_spike", "outer"),
    (
        "vertex on an edge",
        [(0, 0), (2, 0), (2, 2), (1, 0), (0, 2)],
        [],
        "section.sketch_self_intersection",
        "outer",
    ),
    (
        "hole outside",
        SQUARE,
        [rect(2.0, 2.0, 3.0, 3.0)],
        "section.sketch_hole_outside",
        "hole[0]",
    ),
    (
        "hole crossing the outer",
        SQUARE,
        [rect(0.5, 0.5, 1.5, 0.8)],
        "section.sketch_hole_touches_outer",
        "hole[0]",
    ),
    (
        "hole touching the outer at a vertex",
        SQUARE,
        [[(0.0, 0.5), (0.5, 0.2), (0.5, 0.8)]],
        "section.sketch_hole_touches_outer",
        "hole[0]",
    ),
    (
        "hole larger than the outer",
        rect(0.4, 0.4, 0.6, 0.6),
        [SQUARE],
        "section.sketch_hole_outside",
        "hole[0]",
    ),
    (
        "overlapping holes",
        SQUARE,
        [rect(0.1, 0.1, 0.5, 0.5), rect(0.4, 0.4, 0.8, 0.8)],
        "section.sketch_holes_overlap",
        "hole[1]",
    ),
    (
        "nested holes",
        SQUARE,
        [rect(0.1, 0.1, 0.9, 0.9), rect(0.4, 0.4, 0.6, 0.6)],
        "section.sketch_holes_overlap",
        "hole[1]",
    ),
    (
        "holes sharing an edge",
        SQUARE,
        [rect(0.1, 0.1, 0.4, 0.4), rect(0.4, 0.1, 0.7, 0.4)],
        "section.sketch_holes_overlap",
        "hole[1]",
    ),
    ("self-intersecting hole", SQUARE, [BOWTIE], "section.sketch_self_intersection", "hole[0]"),
]


@pytest.mark.parametrize(
    ("outer", "holes", "key", "field"),
    [pytest.param(o, h, k, f, id=name) for name, o, h, k, f in INVALID],
)
def test_invalid_sketch_is_rejected_with_key_and_field(
    outer: list[Pt], holes: list[list[Pt]], key: str, field: str
) -> None:
    with pytest.raises(DiagnosticError) as error:
        build(outer, holes)
    assert key_of(error.value) == key
    assert error.value.diagnostic.location is not None
    assert error.value.diagnostic.location.field == field


@pytest.mark.parametrize("bad", [NAN, math.inf, -math.inf])
def test_nonfinite_vertices_are_rejected(bad: float) -> None:
    with pytest.raises(DiagnosticError):
        build([(0.0, 0.0), (1.0, 0.0), (bad, 1.0)])


def test_malformed_vertices_are_rejected() -> None:
    with pytest.raises(DiagnosticError):
        FreeformParameters(((0.0, 0.0, 0.0), (1.0, 0.0), (1.0, 1.0)), ())  # type: ignore[arg-type]
    with pytest.raises(DiagnosticError):
        build_freeform_section(uid(1), 1, outer=[(1.0, 2.0)] * 3)  # type: ignore[list-item]


def test_valid_nonconvex_and_multi_hole_sketches_are_accepted() -> None:
    assert value(build(NONCONVEX_OK), "area") == pytest.approx(9 - 2.0)
    section = build(SQUARE, [rect(0.1, 0.1, 0.3, 0.3), rect(0.5, 0.5, 0.9, 0.9)])
    assert value(section, "area") == pytest.approx(1 - 0.04 - 0.16, rel=1e-13)


def test_a_hole_in_the_concavity_of_a_nonconvex_outer_is_outside() -> None:
    with pytest.raises(DiagnosticError) as error:
        build(NONCONVEX_OK, [rect(1.2, 1.5, 1.8, 2.5)])  # dentro de la ranura de la U
    assert key_of(error.value) == "section.sketch_hole_outside"


def test_hand_edited_properties_are_rejected_but_another_method_version_is_kept() -> None:
    section = build(rect(0.0, 0.0, 0.2, 0.5))
    forged = replace(
        section.properties, area=replace(section.properties.area, value=value(section, "area") * 2)
    )
    with pytest.raises(DiagnosticError) as error:
        replace(section, properties=forged)
    assert key_of(error.value) == "section.properties_mismatch"
    older = replace(
        section.properties,
        **{
            n: replace(getattr(section.properties, n), method="polygon-green/0")
            for n in ("area", "centroid_y", "centroid_z", "iy", "iz", "iyz")
        },
    )
    assert replace(section, properties=older).properties == older


def l_section() -> SectionRevision:
    return build([(0, 0), (0.2, 0), (0.2, 0.02), (0.02, 0.02), (0.02, 0.2), (0, 0.2)])


def test_one_foreign_method_does_not_switch_off_the_recomputation_of_the_others() -> None:
    """Regresión de la revisión estructural (H1): J inventada + Iyz = 0 con otro método en J."""
    section = l_section()
    forged_j = SectionProperty(1e-6, "m⁴", "x", "manual/1", PropertyStatus.CALCULADO)
    with pytest.raises(DiagnosticError) as j_error:
        replace(
            section,
            properties=replace(
                section.properties,
                iyz=replace(section.properties.iyz, value=0.0),
                torsion_constant=forged_j,
            ),
        )
    assert key_of(j_error.value) == "section.freeform_torsion_not_available"
    # Aun con J legítima (NO_DISPONIBLE), un Iyz falsificado con el método actual se detecta.
    with pytest.raises(DiagnosticError) as iyz_error:
        replace(
            section,
            properties=replace(section.properties, iyz=replace(section.properties.iyz, value=0.0)),
        )
    assert key_of(iyz_error.value) == "section.properties_mismatch"
    # Un método de otra versión exime solo a esa propiedad.
    other = replace(section.properties.iyz, method="polygon-green/0", value=0.0)
    with pytest.raises(DiagnosticError):
        replace(
            section,
            properties=replace(
                section.properties, iyz=other, area=replace(section.properties.area, value=1.0)
            ),
        )
    assert (
        replace(section, properties=replace(section.properties, iyz=other)).properties.iyz == other
    )


def test_inadmissible_inertia_tensor_and_verified_status_are_rejected() -> None:
    section = l_section()
    iy = value(section, "iy")
    huge = replace(section.properties.iyz, method="polygon-green/0", value=2 * iy)
    with pytest.raises(DiagnosticError):
        replace(section, properties=replace(section.properties, iyz=huge))
    verified = replace(section.properties.area, status=PropertyStatus.VERIFICADO)
    with pytest.raises(DiagnosticError) as status:
        replace(section, properties=replace(section.properties, area=verified))
    assert key_of(status.value) == "section.freeform_invalid_status"


def test_every_accepted_sketch_reopens_in_its_canonical_form() -> None:
    for outer, holes in (
        (list(reversed(rect(0.0, 0.0, 1.0, 1.0))), [rect(0.2, 0.2, 0.4, 0.4)]),
        (NONCONVEX_OK[3:] + NONCONVEX_OK[:3], []),
    ):
        first = FreeformParameters(tuple(outer), tuple(tuple(h) for h in holes))
        assert FreeformParameters(first.outer, first.holes) == first


def test_shear_and_foreign_parameters_are_not_allowed_on_a_freeform_section() -> None:
    section = build(rect(0.0, 0.0, 0.2, 0.5))
    with pytest.raises(DiagnosticError) as error:
        replace(section, shear=rectangular_shear(0.2, 0.5))
    assert key_of(error.value) == "section.shear_not_derivable"
    with pytest.raises(DiagnosticError):
        replace(section, family=SectionFamily.RECTANGULAR)
    with pytest.raises(DiagnosticError) as not_freeform:
        build_rect().require_freeform()
    assert key_of(not_freeform.value) == "section.not_freeform"


def build_rect() -> SectionRevision:
    return StructuralModel().add_rectangular_section(
        b=u.Quantity.of(0.2, "m"), h=u.Quantity.of(0.4, "m")
    )


# --- Persistencia v7 ---------------------------------------------------------------------


def model_with(section: SectionRevision) -> StructuralModel:
    model = StructuralModel()
    a = model.add_node(u.Quantity.of(0, "m"), u.Quantity.of(0, "m"), u.Quantity.of(0, "m"))
    b = model.add_node(u.Quantity.of(3, "m"), u.Quantity.of(0, "m"), u.Quantity.of(0, "m"))
    steel = model.add_material(
        name="Acero",
        family=MaterialFamily.STEEL,
        source="CTE DB-SE-A",
        standard_edition="2019",
        elastic_modulus=u.Quantity.of(210_000, "N/mm²"),
        shear_modulus=u.Quantity.of(81_000, "N/mm²"),
        poisson_ratio=0.3,
        density=u.Quantity.of(7850, "kg/m³"),
        specific_weight=u.Quantity.of(78.5, "kN/m³"),
    )
    registered = model.register_section(section)
    model.add_member(a.id, b.id, material=steel.ref, section=registered.ref)
    return model


def test_v7_round_trip_is_exact_and_hash_is_stable(tmp_path: Path) -> None:
    section = build(rect(0.1, 0.2, 0.7, 0.9), [rect(0.2, 0.3, 0.4, 0.5)])
    model = model_with(section)
    saved = save_project(tmp_path / "a.json", model)
    opened = open_project(tmp_path / "a.json")
    assert opened.schema_version == CURRENT_SCHEMA_VERSION == 7
    assert opened.model_hash == saved.model_hash
    loaded = [s for s in opened.model.snapshot().sections if s.family is SectionFamily.FREEFORM]
    assert loaded == [section]
    assert repr(loaded[0].parameters) == repr(section.parameters)
    resaved = save_project(tmp_path / "b.json", opened.model)
    assert resaved.model_hash == saved.model_hash


def test_a_manipulated_v7_file_is_rejected_on_open(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    save_project(path, model_with(build(SQUARE, [rect(0.1, 0.1, 0.3, 0.3)])))
    data = json.loads(path.read_text(encoding="utf-8"))
    sections = data["content"]["model"]["sections"]
    freeform = next(s for s in sections if s["family"] == "FREEFORM")
    freeform["parameters"]["holes"] = [[[0.5, 0.5], [0.6, 0.5], [0.6, 0.6], [0.5, 0.6]]]
    tampered = tmp_path / "t.json"
    tampered.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DiagnosticError):
        open_project(tampered)
    freeform["parameters"]["holes"] = [[[0.5, 0.5], "x"]]
    tampered.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(DiagnosticError):
        open_project(tampered)


def test_migration_6_to_7_only_verifies_and_rejects_freeform_in_a_v6_file() -> None:
    clean: dict[str, object] = {
        "model": {"sections": [{"family": "PARAMETRICA"}]},
        "settings": {},
        "runs": [],
    }
    assert migrate(clean, 6) == clean
    with pytest.raises(DiagnosticError) as error:
        migrate({"model": {"sections": [{"family": "FREEFORM"}]}}, 6)
    assert key_of(error.value) == "project.invalid_field"


def test_freeform_properties_are_deterministic() -> None:
    parameters = FreeformParameters(((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)))
    assert freeform_properties(parameters) == freeform_properties(parameters)
