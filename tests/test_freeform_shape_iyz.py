"""T-031: forma declarada del croquis libre (TK-032, esquema v8) e Iyz de ruido (TK-033,
método ``polygon-green/2``). Datos sintéticos; el fixture v7 es un proyecto auténtico guardado
por el código de T-029 E2 antes del cambio."""

import json
import math
from dataclasses import replace
from pathlib import Path
from uuid import UUID

import pytest

from fem_support import E_PA, Frame
from test_catalog_snapshot import CATALOG, profile
from veritas.catalogs import bridge
from veritas.catalogs.model import ProfileFamily
from veritas.cli import main
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticError
from veritas.core.geometry.replacement import ReplacementStatus
from veritas.core.materials.material import MaterialFamily
from veritas.core.sections.contrast import compare_with_snapshot
from veritas.core.sections.polygon import (
    IYZ_NOISE_RELATIVE,
    METHOD_POLYGON,
    METHOD_POLYGON_V1,
    FreeformParameters,
    freeform_properties,
)
from veritas.core.sections.section import SectionFamily, SectionRevision, build_freeform_section
from veritas.core.supports.support import fixed_restraints
from veritas.fem import analyze_planar_xz
from veritas.fem.results.run import RunStatus
from veritas.fem.spatial import analyze_spatial
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.persistence.canonical import model_hash
from veritas.persistence.migrations import CURRENT_SCHEMA_VERSION, migrate
from veritas.persistence.project import open_project, save_project
from veritas.section_editor import SketchContour, SketchDraft, template_draft

REFERENCE_V7 = Path(__file__).parent / "data" / "proyecto_v7_ref.json"
REFERENCE_V7_HASH = "e80373d39fae2783ea53e13c8c54991db6637e46b4b625da38b9db5b6879d564"

Pt = tuple[float, float]


def uid(n: int) -> UUID:
    return UUID(int=n, version=4)


def mm(**dims: float) -> dict[str, u.Quantity]:
    return {k: u.Quantity.of(v, "mm") for k, v in dims.items()}


def metres(points: list[Pt]) -> list[tuple[u.Quantity, u.Quantity]]:
    return [(u.Quantity.of(y, "m"), u.Quantity.of(z, "m")) for y, z in points]


SQUARE: list[Pt] = [(0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0)]


def u_section(revision: int = 1) -> SectionRevision:
    return template_draft("u", mm(h=300, b=100, tw=8, tf=12)).build(uid(1), revision)


def key_of(error: DiagnosticError) -> str:
    return error.diagnostic.message_key


# --- TK-032: forma declarada -------------------------------------------------------------


@pytest.mark.parametrize(
    ("shape", "dims"),
    [
        ("u", dict(h=300, b=100, tw=8, tf=12)),
        ("l", dict(h=200, b=150, t=20)),
        ("t", dict(h=200, b=150, tw=10, tf=20)),
    ],
)
def test_templates_declare_their_shape(shape: str, dims: dict[str, float]) -> None:
    section = template_draft(shape, mm(**dims)).build(uid(1))
    assert section.require_freeform().shape == shape.upper()


def test_the_shape_is_validated_and_optional() -> None:
    assert FreeformParameters(tuple(SQUARE)).shape is None
    assert FreeformParameters(tuple(SQUARE), shape="RECT").shape == "RECT"
    for bad in ("U ", "u", "VIGA", 3):
        with pytest.raises(DiagnosticError):
            FreeformParameters(tuple(SQUARE), shape=bad)  # type: ignore[arg-type]
    draft = SketchDraft(SketchContour(tuple(metres(SQUARE))), shape="VIGA")
    assert [d.message_key for d in draft.diagnostics()] == ["section.sketch_shape_invalid"]
    assert SketchDraft.from_section(u_section()).shape == "U"


def timber_frame() -> Frame:
    frame = Frame(MaterialFamily.TIMBER)
    a, b = frame.node(0.0, 0.0), frame.node(3.0, 0.0)
    frame.member(a, b)
    return frame


def assign(frame: Frame, section: SectionRevision) -> ReplacementStatus:
    registered = frame.model.register_section(section)
    report = frame.model.replace_sections(list(frame.model.members), registered.ref)
    return report.members[0].status


def test_a_declared_metal_shape_is_blocked_with_timber_like_the_catalog_one() -> None:
    frame = timber_frame()
    status = assign(frame, u_section())
    assert status is ReplacementStatus.BLOCKED
    report = frame.model.replace_sections(
        list(frame.model.members), frame.model.snapshot().sections[-1].ref
    )
    blocking = report.members[0].blocking
    assert [d.message_key for d in blocking] == ["section.replace_incompatible_material"]


def test_an_undeclared_freeform_with_timber_is_not_blocked_but_warns() -> None:
    frame = timber_frame()
    section = build_freeform_section(uid(2), 1, outer=metres(SQUARE), name="sin forma")
    registered = frame.model.register_section(section)
    report = frame.model.replace_sections(list(frame.model.members), registered.ref)
    member = report.members[0]
    assert member.status is ReplacementStatus.APPLIED
    assert "section.replace_freeform_shape_undeclared" in [d.message_key for d in member.warnings]


def test_a_declared_non_metal_shape_and_steel_are_accepted_silently() -> None:
    rect = build_freeform_section(uid(3), 1, outer=metres(SQUARE), shape="RECT")
    timber = timber_frame()
    registered = timber.model.register_section(rect)
    report = timber.model.replace_sections(list(timber.model.members), registered.ref)
    assert report.members[0].status is ReplacementStatus.APPLIED
    assert "section.replace_freeform_shape_undeclared" not in [
        d.message_key for d in report.members[0].warnings
    ]
    steel = Frame()
    steel.member(steel.node(0.0, 0.0), steel.node(3.0, 0.0))
    assert assign(steel, u_section()) is ReplacementStatus.APPLIED


def snapshot_of(family: ProfileFamily):  # type: ignore[no-untyped-def]
    return bridge.snapshot_from_profile(profile(family=family), CATALOG)


def test_the_contrast_warns_about_the_shape_when_declared_and_when_not() -> None:
    chs = snapshot_of(ProfileFamily.CHS)
    labelled = compare_with_snapshot(u_section(), chs).notes
    assert "editor.compare_shape_differs" in labelled
    plain = build_freeform_section(uid(2), 1, outer=metres(SQUARE))
    assert "editor.compare_shape_undeclared" in compare_with_snapshot(plain, chs).notes
    same = replace(u_section(), parameters=replace(u_section().require_freeform(), shape="CHS"))
    assert "editor.compare_shape_differs" not in compare_with_snapshot(same, chs).notes
    assert "editor.compare_shape_undeclared" not in compare_with_snapshot(same, chs).notes


# --- Esquema v8 y proyecto v7 auténtico --------------------------------------------------


def encoded_parameters(path: Path) -> dict[str, object]:
    sections = json.loads(path.read_text(encoding="utf-8"))["content"]["model"]["sections"]
    found = [s["parameters"] for s in sections if s["family"] == "FREEFORM"]
    assert len(found) == 1
    return dict(found[0])


def one_section_model(section: SectionRevision):  # type: ignore[no-untyped-def]
    frame = Frame()
    frame.member(frame.node(0.0, 0.0), frame.node(3.0, 0.0))
    frame.model.register_section(section)
    return frame.model


def test_v8_writes_the_shape_only_when_declared_and_round_trips(tmp_path: Path) -> None:
    labelled = tmp_path / "a.json"
    saved = save_project(labelled, one_section_model(u_section()))
    assert saved.schema_version == CURRENT_SCHEMA_VERSION == 8
    assert encoded_parameters(labelled)["shape"] == "U"
    opened = open_project(labelled)
    assert opened.model_hash == saved.model_hash
    restored = [s for s in opened.model.snapshot().sections if s.family is SectionFamily.FREEFORM]
    assert restored == [u_section()]
    plain = tmp_path / "b.json"
    save_project(plain, one_section_model(build_freeform_section(uid(2), 1, outer=metres(SQUARE))))
    assert "shape" not in encoded_parameters(plain)


def test_a_manipulated_shape_is_rejected_on_open(tmp_path: Path) -> None:
    path = tmp_path / "p.json"
    save_project(path, one_section_model(u_section()))
    for bad in ("VIGA", 7, None):
        data = json.loads(path.read_text(encoding="utf-8"))
        for section in data["content"]["model"]["sections"]:
            if section["family"] == "FREEFORM":
                section["parameters"]["shape"] = bad
        tampered = tmp_path / "t.json"
        tampered.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(DiagnosticError):
            open_project(tampered)


def test_migration_7_to_8_only_verifies_and_rejects_a_shape_in_a_v7_file() -> None:
    clean: dict[str, object] = {
        "model": {"sections": [{"family": "FREEFORM", "parameters": {"outer": [], "holes": []}}]}
    }
    assert migrate(clean, 7) == clean
    labelled: dict[str, object] = {
        "model": {"sections": [{"family": "FREEFORM", "parameters": {"shape": "U"}}]}
    }
    with pytest.raises(DiagnosticError) as error:
        migrate(labelled, 7)
    assert key_of(error.value) == "project.unknown_fields"


def test_a_real_v7_project_opens_under_v8_with_identical_hash_and_noisy_iyz(
    tmp_path: Path,
) -> None:
    raw = json.loads(REFERENCE_V7.read_text(encoding="utf-8"))
    assert raw["schema_version"] == 7
    assert raw["model_hash"] == REFERENCE_V7_HASH  # guardado por el código v7, antes del cambio
    opened = open_project(REFERENCE_V7)
    assert opened.model_hash == model_hash(opened.model.snapshot()) == REFERENCE_V7_HASH
    section = next(s for s in opened.model.snapshot().sections if s.name == "U300")
    assert section.require_freeform().shape is None  # sin declarar: lo que significa su ausencia
    iyz = section.properties.iyz
    assert iyz.method == METHOD_POLYGON_V1
    assert iyz.value is not None
    assert iyz.value != 0.0  # el valor guardado NO se reinterpreta ni se recalcula al abrir
    resaved = save_project(tmp_path / "v8.json", opened.model, runs=opened.runs)
    assert resaved.schema_version == 8
    assert open_project(tmp_path / "v8.json").model_hash == REFERENCE_V7_HASH


def test_a_stored_v1_section_is_still_checked_against_the_v1_recomputation() -> None:
    opened = open_project(REFERENCE_V7)
    section = next(s for s in opened.model.snapshot().sections if s.name == "U300")
    forged = replace(
        section.properties, area=replace(section.properties.area, value=0.5)
    )  # método /1 con otro valor
    with pytest.raises(DiagnosticError) as error:
        replace(section, properties=forged)
    assert key_of(error.value) == "section.properties_mismatch"
    # El método viejo reproduce exactamente el valor guardado, con su ruido.
    again = freeform_properties(section.require_freeform(), snap=False)
    assert again.iyz == section.properties.iyz


# --- TK-033: Iyz de ruido ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("shape", "dims"),
    [
        ("u", dict(h=300, b=100, tw=8, tf=12)),
        ("u", dict(h=240, b=85, tw=9.5, tf=13)),
        ("t", dict(h=200, b=150, tw=10, tf=20)),
        ("t", dict(h=180, b=120, tw=7, tf=11)),
    ],
)
def test_symmetric_templates_have_an_exact_zero_iyz(shape: str, dims: dict[str, float]) -> None:
    section = template_draft(shape, mm(**dims)).build(uid(1))
    assert section.properties.iyz.value == 0.0
    assert math.copysign(1.0, section.properties.iyz.value or 0.0) > 0.0
    assert section.properties.iyz.method == METHOD_POLYGON
    noisy = freeform_properties(section.require_freeform(), snap=False)
    assert noisy.iyz.value is not None
    assert noisy.iyz.value != 0.0  # sin el ajuste habría ruido: el ajuste actúa donde debe


def test_an_asymmetric_or_slightly_coupled_section_keeps_its_real_iyz() -> None:
    l_section = template_draft("l", mm(h=200, b=150, t=20)).build(uid(1))
    assert l_section.properties.iyz.value not in (None, 0.0)
    sheared: list[Pt] = [(0.0, 0.0), (1.0, 0.0), (1.0 + 1e-6, 1.0), (1e-6, 1.0)]
    coupled = build_freeform_section(uid(2), 1, outer=metres(sheared))
    value = coupled.properties.iyz.value
    iy, iz = coupled.properties.iy.value, coupled.properties.iz.value
    assert value is not None
    assert iy is not None
    assert iz is not None
    assert abs(value) > IYZ_NOISE_RELATIVE * math.sqrt(iy * iz)
    assert value != 0.0


def test_a_coupling_below_the_declared_bound_is_treated_as_zero() -> None:
    tiny: list[Pt] = [(0.0, 0.0), (1.0, 0.0), (1.0 + 1e-14, 1.0), (1e-14, 1.0)]
    section = build_freeform_section(uid(2), 1, outer=metres(tiny))
    assert section.properties.iyz.value == 0.0
    raw = freeform_properties(section.require_freeform(), snap=False).iyz.value
    assert raw is not None
    assert abs(raw) <= IYZ_NOISE_RELATIVE * math.sqrt(
        float(section.properties.iy.value or 0.0) * float(section.properties.iz.value or 0.0)
    )


def test_a_v2_iyz_cannot_be_forged_but_the_snap_is_part_of_the_recomputation() -> None:
    section = u_section()
    noisy = freeform_properties(section.require_freeform(), snap=False).iyz
    forged = replace(section.properties.iyz, value=noisy.value)  # método /2 con valor de ruido
    with pytest.raises(DiagnosticError) as error:
        replace(section, properties=replace(section.properties, iyz=forged))
    assert key_of(error.value) == "section.properties_mismatch"


def test_a_planar_analysis_of_a_u_template_bar_now_succeeds_and_matches_pl3_over_3ei() -> None:
    load, length = 1000.0, 3.0
    frame = Frame()
    a, b = frame.node(0.0, 0.0), frame.node(length, 0.0)
    frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.nodal(b, fz=-load)
    section = frame.model.register_section(u_section())
    assert frame.model.replace_sections(list(frame.model.members), section.ref).applied
    run = analyze_planar_xz(frame.model, frame.config())
    assert run.status is RunStatus.SUCCEEDED, run.diagnostics
    assert run.raw is not None
    iy = section.properties.iy.value
    assert iy is not None
    tip = next(n for n in run.raw.nodes if n.node_id == b.id)
    assert tip.displacement[1] == pytest.approx(-load * length**3 / (3 * E_PA * iy), rel=1e-6)


def test_the_spatial_adapter_still_rejects_it_because_j_is_unavailable() -> None:
    frame = Frame()
    a, b = frame.node(0.0, 0.0), frame.node(3.0, 0.0)
    frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.nodal(b, fz=-1000.0)
    section = frame.model.register_section(u_section())
    assert frame.model.replace_sections(list(frame.model.members), section.ref).applied
    with pytest.raises(DiagnosticError) as error:
        analyze_spatial(frame.model, SpatialAnalysisConfiguration(frame.case_id))
    assert key_of(error.value) == "fem.section_property_unavailable"


# --- CLI ---------------------------------------------------------------------------------


def test_cli_forma_is_declared_validated_and_shown(capsys: pytest.CaptureFixture[str]) -> None:
    sketch = ["section", "sketch", "--exterior", "0,0;1,0;1,1;0,1"]
    assert main(sketch) == 0
    assert "Forma declarada: sin declarar" in capsys.readouterr().out
    assert main([*sketch, "--forma", "rect"]) == 0  # sin distinguir mayúsculas
    assert "Forma declarada: RECT" in capsys.readouterr().out
    assert main([*sketch, "--forma", "viga"]) == 2
    assert capsys.readouterr().err
    assert main(["section", "u", "--dim", "h=300", "b=100", "tw=8", "tf=12", "--unidad", "mm"]) == 0
    assert "Forma declarada: U" in capsys.readouterr().out


def test_cli_blocks_a_declared_u_with_a_timber_bar_and_warns_when_undeclared(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["project", "demo", "--out", "p.json", "--caso", "viga"]) == 0
    project = open_project(tmp_path / "p.json")
    model = project.model
    timber = model.add_material(
        name="Madera",
        family=MaterialFamily.TIMBER,
        source="CTE DB-SE-M",
        standard_edition="2019",
        elastic_modulus=u.Quantity.of(11_000, "N/mm²"),
        shear_modulus=u.Quantity.of(690, "N/mm²"),
        poisson_ratio=0.3,
        density=u.Quantity.of(420, "kg/m³"),
        specific_weight=u.Quantity.of(4.1, "kN/m³"),
    )
    member = next(iter(model.members.values()))
    model.assign_material(member.id, timber.ref)
    save_project(tmp_path / "p.json", model, project.user_profiles, runs=project.runs)
    capsys.readouterr()
    declared = ["project", "add-section", "p.json", "u", "--dim", "h=300", "b=100", "tw=8"]
    assert main([*declared, "tf=12", "--unidad", "mm", "--nombre", "U300"]) == 0
    assert main(["project", "assign-section", "p.json", "--todas", "U300"]) == 2
    out = capsys.readouterr().out
    assert "BLOQUEADA" in out
    assert "perfil de forma U (metálico) no es compatible" in out
    before = (tmp_path / "p.json").read_bytes()
    plain = ["project", "add-sketch", "p.json", "--exterior", "0,0;1,0;1,1;0,1", "--nombre", "X"]
    assert main(plain) == 0
    assert main(["project", "assign-section", "p.json", "--todas", "X"]) == 0
    assert "no declara forma" in capsys.readouterr().out
    assert (tmp_path / "p.json").read_bytes() != before


def test_the_v1_integration_path_is_frozen_with_pinned_reference_values() -> None:
    """Los v7 se contrastan con ``snap=False``: esta ruta no puede cambiar ni un bit.

    Valores guardados por el código de T-029 E2 (antes de T-031): si la integración se reordena,
    los proyectos v7 dejarían de abrir (``section.properties_mismatch``) y hay que versionar.
    """
    reference = {
        "u": ((0.3, 0.1, 0.008, 0.012), 1.1293772630057337e-21),
        "t": ((0.2, 0.15, 0.01, 0.02), -6.101037146914849e-22),
    }
    for shape, (dims, iyz) in reference.items():
        names = ("h", "b", "tw", "tf")
        section = template_draft(
            shape, {n: u.Quantity.of(v, "m") for n, v in zip(names, dims, strict=True)}
        ).build(uid(1))
        old = freeform_properties(section.require_freeform(), snap=False)
        assert old.iyz.value == iyz
        assert old.iyz.method == METHOD_POLYGON_V1
        assert old.iy.value is not None
        assert old.iy.value == freeform_properties(section.require_freeform()).iy.value


def test_revising_a_v1_section_with_the_same_outline_creates_the_v2_revision() -> None:
    opened = open_project(REFERENCE_V7)
    model = opened.model
    section = next(s for s in model.snapshot().sections if s.name == "U300")
    assert section.properties.iyz.method == METHOD_POLYGON_V1
    revised = model.revise_freeform_section(section.id)
    assert revised.revision == section.revision + 1
    assert revised.properties.iyz.method == METHOD_POLYGON
    assert revised.properties.iyz.value == 0.0
    assert revised.require_freeform() == section.require_freeform()
    again = model.revise_freeform_section(section.id)
    assert again == revised  # ya es del método actual: sin cambios no hay revisión nueva
