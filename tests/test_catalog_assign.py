"""Asignación de perfiles de catálogo, sustitución masiva y dictámenes (T-024 E2; ADR-010).

Dominio (simetría TK-026, dictámenes SEC-013, ``replace_sections`` SEC-012), CLI de proyecto y
contraste analítico con OpenSees. Datos sintéticos: ningún perfil real.
"""

from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from catalog_support import assert_renders, make_data, make_properties, new_store, uid
from fem_support import Frame
from test_catalog_snapshot import CATALOG, profile
from veritas.catalogs import bridge
from veritas.catalogs.bridge import Freshness, compare_with_catalog, ensure_section
from veritas.catalogs.model import CatalogProfile, ProfileFamily
from veritas.catalogs.store import CatalogStore
from veritas.cli import main
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticCode, DiagnosticError
from veritas.core.geometry.model import StructuralModel
from veritas.core.geometry.replacement import ReplacementStatus
from veritas.core.materials.material import MaterialFamily
from veritas.core.sections.catalog import (
    BISYMMETRIC_SHAPES,
    SYMMETRY_METHOD,
    SYMMETRY_ORIGIN,
)
from veritas.core.sections.properties import PropertyStatus, SectionProperty
from veritas.core.sections.readiness import CodeCoverage, assess_section
from veritas.core.sections.section import SectionFamily, SectionRevision
from veritas.core.supports.support import fixed_restraints
from veritas.fem import analyze_planar_xz
from veritas.fem.results.run import RunStatus
from veritas.fem.spatial import analyze_spatial
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.persistence.project import open_project, save_project

# --- Perfil sintético con forma de IPE (valores de PRUEBA, sin valor comercial) --------------
E = 210e9
L, P = 2.0, 1000.0
A_SYN, IY_SYN, IZ_SYN, J_SYN = 2.85e-3, 1.94e-5, 1.42e-6, 6.98e-8


def ipe_like(torsion: float | None = J_SYN, family: ProfileFamily = ProfileFamily.I) -> Any:
    data = make_data("IPE-SINT", family=family)
    props = make_properties(area=A_SYN, iy=IY_SYN, iz=IZ_SYN, torsion=torsion)
    return CatalogProfile(
        id=uid(710), revision=1, catalog_id=CATALOG.id, data=replace(data, properties=props)
    )


def catalog_section(prof: CatalogProfile, **kwargs: Any) -> SectionRevision:
    return bridge.section_from_profile(prof, CATALOG, section_id=uid(810), **kwargs)


# --- Dictámenes (SEC-013) ---------------------------------------------------------------


def test_rectangular_section_is_fem_ready_but_code_is_never_verified() -> None:
    rect = StructuralModel().add_rectangular_section(
        b=u.Quantity.of(0.2, "m"), h=u.Quantity.of(0.4, "m")
    )
    ready = assess_section(rect)
    assert ready.fem_ready
    assert ready.fem_missing == ()
    assert ready.code_coverage is CodeCoverage.NO_COMPROBADO
    keys = [d.message_key for d in ready.diagnostics()]
    assert keys == ["section.code_not_verified"]
    for diagnostic in ready.diagnostics():
        assert_renders(diagnostic)


def test_fem_verdict_lists_every_missing_property() -> None:
    no_j = assess_section(catalog_section(ipe_like(torsion=None)))
    assert not no_j.fem_ready
    assert no_j.fem_missing == ("J",)
    web = assess_section(catalog_section(profile(family=ProfileFamily.U)))
    assert web.fem_missing == ("centroide y", "centroide z", "Iyz")
    keys = [d.message_key for d in web.diagnostics()]
    assert keys.count("section.fem_missing") == 3
    for diagnostic in web.diagnostics():
        assert_renders(diagnostic)


def test_complete_catalog_section_is_fem_ready_and_code_still_unchecked() -> None:
    ready = assess_section(catalog_section(ipe_like()))
    assert ready.fem_ready
    assert (
        ready.code_coverage is CodeCoverage.NO_COMPROBADO
    )  # los dos dictámenes son independientes


# --- Simetría (TK-026, D3 = C) -----------------------------------------------------------


def test_bisymmetric_shapes_match_the_declared_set() -> None:
    assert {"I", "SHS", "RHS", "CHS", "RECT", "ROUND"} == BISYMMETRIC_SHAPES


@pytest.mark.parametrize("shape", sorted(BISYMMETRIC_SHAPES))
def test_symmetry_is_derived_and_marked_as_such(shape: str) -> None:
    sec = catalog_section(ipe_like(family=ProfileFamily(shape)))
    for name in ("centroid_y", "centroid_z", "iyz"):
        prop = getattr(sec.properties, name)
        assert prop.value == 0.0
        assert prop.status is PropertyStatus.CALCULADO  # derivado, no importado
        assert (prop.origin, prop.method) == (SYMMETRY_ORIGIN, SYMMETRY_METHOD)
    assert sec.properties.iy.status is PropertyStatus.IMPORTADO_SIN_VERIFICAR


@pytest.mark.parametrize("shape", ["U", "L", "T", "OTRO"])
def test_non_bisymmetric_shapes_stay_unavailable(shape: str) -> None:
    sec = catalog_section(ipe_like(family=ProfileFamily(shape)))
    for name in ("centroid_y", "centroid_z", "iyz"):
        assert getattr(sec.properties, name).status is PropertyStatus.NO_DISPONIBLE
        assert getattr(sec.properties, name).value is None


def test_derivation_can_be_disabled() -> None:
    sec = catalog_section(ipe_like(), derive_symmetry=False)
    assert sec.properties.iyz.status is PropertyStatus.NO_DISPONIBLE


def test_symmetry_cannot_be_typed_in_by_hand_or_on_other_shapes() -> None:
    sec = catalog_section(ipe_like())
    fake = SectionProperty(0.0, "m⁴", "a mano", SYMMETRY_METHOD, PropertyStatus.CALCULADO)
    with pytest.raises(DiagnosticError) as info:
        replace(sec, properties=replace(sec.properties, iyz=fake))
    assert info.value.diagnostic.message_key == "section.catalog_not_derivable"
    nonzero = SectionProperty(
        1e-9, "m⁴", SYMMETRY_ORIGIN, SYMMETRY_METHOD, PropertyStatus.CALCULADO
    )
    with pytest.raises(DiagnosticError):
        replace(sec, properties=replace(sec.properties, iyz=nonzero))
    u_sec = catalog_section(ipe_like(family=ProfileFamily.U))
    derived = SectionProperty(0.0, "m⁴", SYMMETRY_ORIGIN, SYMMETRY_METHOD, PropertyStatus.CALCULADO)
    with pytest.raises(DiagnosticError) as info2:
        replace(u_sec, properties=replace(u_sec.properties, iyz=derived))
    assert info2.value.diagnostic.message_key == "section.catalog_not_derivable"


# --- Contraste analítico: voladizo con un perfil en I ------------------------------------


def steel_cantilever() -> tuple[Frame, Any]:
    frame = Frame()
    a, b = frame.node(0.0, 0.0), frame.node(L, 0.0)
    frame.member(a, b)
    frame.support(a, fixed_restraints())
    frame.nodal(b, fz=-P)
    return frame, b


def to_catalog(frame: Frame, prof: CatalogProfile) -> None:
    section = ensure_section(frame.model, bridge.snapshot_from_profile(prof, CATALOG))
    report = frame.model.replace_sections(list(frame.model.members), section.ref)
    assert report.applied


def test_planar_cantilever_with_catalog_profile_matches_pl3_over_3ei() -> None:
    frame, tip = steel_cantilever()
    to_catalog(frame, ipe_like())
    run = analyze_planar_xz(frame.model, frame.config())
    assert run.status is RunStatus.SUCCEEDED, run.diagnostics
    assert run.raw is not None
    result = next(n for n in run.raw.nodes if n.node_id == tip.id)
    assert result.displacement[1] == pytest.approx(-P * L**3 / (3 * E * IY_SYN), rel=1e-6)
    assert result.displacement[2] == pytest.approx(-P * L**2 / (2 * E * IY_SYN), rel=1e-6)


def test_spatial_cantilever_with_catalog_profile_matches_pl3_over_3ei() -> None:
    frame, tip = steel_cantilever()
    to_catalog(frame, ipe_like())
    run = analyze_spatial(frame.model, SpatialAnalysisConfiguration(frame.case_id))
    assert run.status is RunStatus.SUCCEEDED, run.diagnostics
    assert run.raw is not None
    result = next(n for n in run.raw.nodes if n.node_id == tip.id)
    assert result.displacement[2] == pytest.approx(-P * L**3 / (3 * E * IY_SYN), rel=1e-6)


def test_a_profile_without_j_is_rejected_by_both_adapters_never_filled_in() -> None:
    """J ausente no se deduce: el solver lo rechaza con diagnóstico (nunca un análisis «válido»)."""
    frame, _ = steel_cantilever()
    to_catalog(frame, ipe_like(torsion=None))
    with pytest.raises(DiagnosticError) as spatial:
        analyze_spatial(frame.model, SpatialAnalysisConfiguration(frame.case_id))
    assert spatial.value.diagnostic.message_key == "fem.section_property_unavailable"
    assert dict(spatial.value.diagnostic.params_dict())["property"] == "J"


def test_non_symmetric_profile_is_rejected_by_the_solver() -> None:
    frame, _ = steel_cantilever()
    to_catalog(frame, ipe_like(family=ProfileFamily.U))
    with pytest.raises(DiagnosticError) as info:
        analyze_planar_xz(frame.model, frame.config())
    assert info.value.diagnostic.message_key == "fem.section_property_unavailable"
    assert dict(info.value.diagnostic.params_dict())["property"] == "Iyz"


# --- replace_sections (SEC-012) ----------------------------------------------------------


def mixed_model() -> tuple[StructuralModel, dict[str, UUID], SectionRevision]:
    """Dos barras de acero (A, B) y una de madera (T), todas con una sección rectangular."""
    model = StructuralModel()
    nodes = [
        model.add_node(u.Quantity.of(x, "m"), u.Quantity.of(0, "m"), u.Quantity.of(0, "m"))
        for x in (0, 3, 6, 9)
    ]
    steel = model.add_material(
        name="Acero",
        family=MaterialFamily.STEEL,
        source="prueba",
        elastic_modulus=u.Quantity.of(210_000, "N/mm²"),
        shear_modulus=u.Quantity.of(81_000, "N/mm²"),
        density=u.Quantity.of(7850, "kg/m³"),
    )
    timber = model.add_material(
        name="Madera",
        family=MaterialFamily.TIMBER,
        source="prueba",
        elastic_modulus=u.Quantity.of(11_000, "N/mm²"),
        shear_modulus=u.Quantity.of(690, "N/mm²"),
        density=u.Quantity.of(420, "kg/m³"),
    )
    rect = model.add_rectangular_section(
        b=u.Quantity.of(0.1, "m"), h=u.Quantity.of(0.2, "m"), name="R 100x200"
    )
    ids = {}
    for label, (i, j), material in (
        ("A", (0, 1), steel),
        ("B", (1, 2), steel),
        ("T", (2, 3), timber),
    ):
        member = model.add_member(
            nodes[i].id, nodes[j].id, material=material.ref, section=rect.ref, label=label
        )
        ids[label] = member.id
    target = ensure_section(model, bridge.snapshot_from_profile(ipe_like(), CATALOG))
    return model, ids, target


def test_strict_replacement_applies_to_all_with_one_model_revision() -> None:
    model, ids, target = mixed_model()
    before = model.model_revision
    report = model.replace_sections([ids["A"], ids["B"]], target.ref)
    assert [m.status for m in report.members] == [ReplacementStatus.APPLIED] * 2
    assert report.model_revision_before == before
    assert report.model_revision_after == model.model_revision == before + 1  # una sola revisión
    assert report.changed
    assert model.member(ids["A"]).section == target.ref == model.member(ids["B"]).section
    assert model.member(ids["T"]).section != target.ref


def test_strict_mode_is_all_or_nothing_and_reports_every_bar() -> None:
    model, ids, target = mixed_model()
    before, snapshot = model.model_revision, model.snapshot()
    report = model.replace_sections([ids["A"], ids["T"], ids["B"]], target.ref)
    status = {m.label: m.status for m in report.members}
    assert status == {
        "A": ReplacementStatus.OMITTED,
        "T": ReplacementStatus.BLOCKED,
        "B": ReplacementStatus.OMITTED,
    }
    assert model.model_revision == before
    assert not report.changed
    assert model.snapshot() == snapshot  # no se tocó nada
    blocked = report.rejected
    assert len(blocked) == 3  # todas constan en el informe, no solo la incompatible
    timber = next(m for m in report.members if m.label == "T")
    assert [d.message_key for d in timber.blocking] == ["section.replace_incompatible_material"]
    assert_renders(timber.blocking[0])


def test_partial_mode_applies_only_to_compatible_and_lists_the_rest() -> None:
    model, ids, target = mixed_model()
    report = model.replace_sections([ids["A"], ids["T"], ids["B"]], target.ref, partial=True)
    status = {m.label: m.status for m in report.members}
    assert status == {
        "A": ReplacementStatus.APPLIED,
        "T": ReplacementStatus.BLOCKED,
        "B": ReplacementStatus.APPLIED,
    }
    assert [m.label for m in report.rejected] == ["T"]
    assert report.model_revision_after == report.model_revision_before + 1
    assert model.member(ids["T"]).section != target.ref


def test_replacement_is_reversible_and_keeps_history() -> None:
    model, ids, target = mixed_model()
    old = model.member(ids["A"]).section
    first_revision = model.member(ids["A"]).revision
    model.replace_sections([ids["A"]], target.ref)
    assert model.member(ids["A"]).revision == first_revision + 1
    back = model.replace_sections([ids["A"]], old)
    assert model.member(ids["A"]).section == old
    assert back.members[0].status is ReplacementStatus.APPLIED
    assert model.section(target.ref) == target  # la sección de catálogo sigue registrada


def test_unchanged_bars_are_reported_and_cost_no_revision() -> None:
    model, ids, target = mixed_model()
    model.replace_sections([ids["A"]], target.ref)
    revision = model.model_revision
    report = model.replace_sections([ids["A"]], target.ref)
    assert report.members[0].status is ReplacementStatus.UNCHANGED
    assert model.model_revision == revision
    assert not report.changed


def test_warnings_for_mass_change_and_missing_fem_property() -> None:
    model, ids, _ = mixed_model()
    no_j = ensure_section(model, bridge.snapshot_from_profile(ipe_like(torsion=None), CATALOG))
    report = model.replace_sections([ids["A"]], no_j.ref)
    keys = [d.message_key for d in report.members[0].warnings]
    assert "section.replace_not_fem_ready" in keys
    assert "section.replace_mass_change" in keys  # 0,1×0,2 m² frente a 2,85e-3 m²
    assert report.members[0].mass_change == pytest.approx(A_SYN / 0.02 - 1.0)
    for diagnostic in report.members[0].warnings:
        assert_renders(diagnostic)


def test_unknown_member_empty_selection_and_duplicates() -> None:
    model, ids, target = mixed_model()
    with pytest.raises(DiagnosticError):
        model.replace_sections([], target.ref)
    with pytest.raises(DiagnosticError):
        model.replace_sections([uid(999)], target.ref)
    report = model.replace_sections([ids["A"], ids["A"]], target.ref)
    assert len(report.members) == 1


def test_ensure_section_reuses_an_identical_snapshot() -> None:
    model, _, target = mixed_model()
    again = ensure_section(model, bridge.snapshot_from_profile(ipe_like(), CATALOG))
    assert again == target
    assert sum(1 for s in model.snapshot().sections if s.family is SectionFamily.CATALOGO) == 1
    newer = replace(ipe_like(), revision=2)
    other = ensure_section(model, bridge.snapshot_from_profile(newer, CATALOG))
    assert other.id != target.id


# --- Vigencia frente al catálogo (D8) ----------------------------------------------------


def test_freshness_states() -> None:
    prof = ipe_like()
    snap = bridge.snapshot_from_profile(prof, CATALOG)
    assert compare_with_catalog(snap, prof) is Freshness.CURRENT
    assert compare_with_catalog(snap, replace(prof, revision=2)) is Freshness.NEWER_REVISION
    other = replace(prof, data=make_data("IPE-SINT", family=ProfileFamily.U))
    assert compare_with_catalog(snap, other) is Freshness.CONTENT_DIFFERS
    assert compare_with_catalog(snap, None) is Freshness.MISSING
    assert compare_with_catalog(snap, replace(prof, id=uid(711))) is Freshness.MISSING


# --- CLI de proyecto ---------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    store = new_store(tmp_path, "cat.sqlite")
    info = store.create_catalog(name="CAT", source="SINTÉTICO", license="Sin valor", edition="2026")
    data = make_data("IPE-SINT", series="SINT-I")
    data = replace(
        data, properties=make_properties(area=A_SYN, iy=IY_SYN, iz=IZ_SYN, torsion=J_SYN)
    )
    channel = replace(make_data("UPN-SINT", series="SINT-U", family=ProfileFamily.U))
    store.put_profiles(info.id, [data, channel])
    store.close()
    model, _, _ = mixed_model()
    save_project(tmp_path / "p.json", model)
    return tmp_path


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, str, str]:
    code = main(["project", *argv])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


REF = "CAT/SINT-I/IPE-SINT"


def test_assign_profile_saves_a_snapshot_and_the_project_opens_without_the_catalog(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, err = run(capsys, "assign-profile", "p.json", "A", REF, "--db", "cat.sqlite")
    assert (code, err) == (0, "")
    assert "barra «A»: APLICADA" in out
    assert "FEM: APTA para FEM" in out
    assert "Norma: NO_COMPROBADO" in out  # los dos dictámenes aparecen por separado
    (workspace / "cat.sqlite").unlink()
    project = open_project(workspace / "p.json")
    member = next(m for m in project.model.members.values() if m.label == "A")
    section = project.model.section(member.section)
    assert section.family is SectionFamily.CATALOGO
    assert section.parameters.label == f"{REF}@1"  # type: ignore[union-attr]


def test_assign_all_reuses_one_section_and_replace_by_labels(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "replace-profile", "p.json", REF, "--barras", "A", "B",
                       "--db", "cat.sqlite")  # fmt: skip
    assert code == 0
    assert out.count("APLICADA") == 2
    project = open_project(workspace / "p.json")
    families = [project.model.section(m.section).family for m in project.model.members.values()]
    assert families.count(SectionFamily.CATALOGO) == 2
    assert len({m.section for m in project.model.members.values() if m.label in ("A", "B")}) == 1


def test_strict_rejection_exits_2_and_does_not_touch_the_file(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (workspace / "p.json").read_bytes()
    code, out, _ = run(capsys, "assign-profile", "p.json", "--todas", REF, "--db", "cat.sqlite")
    assert code == 2
    assert "barra «T»: BLOQUEADA" in out
    assert "barra «A»: OMITIDA" in out
    assert "Sustitución rechazada (modo estricto, todo o nada)" in out
    assert (workspace / "p.json").read_bytes() == before


def test_partial_mode_applies_the_compatible_and_always_lists_the_omitted(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "assign-profile", "p.json", "--todas", REF, "--db", "cat.sqlite",
                       "--parcial")  # fmt: skip
    assert code == 0
    assert "barra «A»: APLICADA" in out
    assert "barra «T»: BLOQUEADA" in out
    assert "bloqueo:" in out
    assert "Sustitución en modo parcial" in out
    project = open_project(workspace / "p.json")
    labels = {
        m.label: project.model.section(m.section).family for m in project.model.members.values()
    }
    assert labels == {
        "A": SectionFamily.CATALOGO,
        "B": SectionFamily.CATALOGO,
        "T": SectionFamily.RECTANGULAR,
    }


def test_partial_with_nothing_compatible_is_rejected(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (workspace / "p.json").read_bytes()
    code, out, _ = run(capsys, "assign-profile", "p.json", "T", REF, "--db", "cat.sqlite",
                       "--parcial")  # fmt: skip
    assert code == 2
    assert "ninguna barra es compatible" in out
    assert (workspace / "p.json").read_bytes() == before


def test_stored_runs_become_stale_after_a_replacement(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, out, _ = run(capsys, "assign-profile", "p.json", "A", REF, "--db", "cat.sqlite")
    assert code == 0
    assert "ejecuciones: 0 CURRENT, 0 STALE" in out


def test_sections_lists_both_verdicts_and_freshness(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "assign-profile", "p.json", "A", REF, "--db", "cat.sqlite")
    code, out, _ = run(capsys, "sections", "p.json")
    assert code == 0
    assert "CATALOGO" in out
    assert "RECTANGULAR" in out
    assert out.count("Norma: NO_COMPROBADO") == 3  # una por cada revisión de sección
    assert "Vigencia" not in out  # sin --db no hay comparación
    code, out, _ = run(capsys, "sections", "p.json", "--db", "cat.sqlite")
    assert "Vigencia: vigente" in out


def test_verify_reports_a_later_catalog_revision_without_changing_anything(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(capsys, "assign-profile", "p.json", "A", REF, "--db", "cat.sqlite")
    code, plain, _ = run(capsys, "verify", "p.json")
    assert code == 0
    assert "Vigencia" not in plain  # la salida sin --db no cambia
    code, out, _ = run(capsys, "verify", "p.json", "--db", "cat.sqlite")
    assert "vigente" in out
    with CatalogStore.open(workspace / "cat.sqlite") as store:
        info = store.catalog_by_name("CAT")
        latest = store.latest(info.id, "SINT-I", "IPE-SINT")
        changed = replace(
            latest.data, properties=make_properties(area=A_SYN * 1.1, iy=IY_SYN, iz=IZ_SYN)
        )
        store.put_profile(info.id, changed)
    before = (workspace / "p.json").read_bytes()
    code, out, _ = run(capsys, "verify", "p.json", "--db", "cat.sqlite")
    assert code == 0
    assert "revisión posterior" in out
    assert (workspace / "p.json").read_bytes() == before


@pytest.mark.parametrize(
    "argv",
    [
        ("assign-profile", "p.json", REF),  # falta la barra
        ("assign-profile", "p.json", "--todas", "A", REF),  # sobra la barra
        ("replace-profile", "p.json", REF),  # sin selección
        ("replace-profile", "p.json", REF, "--todas", "--barras", "A"),  # dos selecciones
    ],
)
def test_usage_errors_exit_2(
    workspace: Path, capsys: pytest.CaptureFixture[str], argv: tuple[str, ...]
) -> None:
    code, _, err = run(capsys, *argv, "--db", "cat.sqlite")
    assert code == 2
    assert err.startswith(DiagnosticCode.INPUT_OUT_OF_RANGE)


@pytest.mark.parametrize(
    "argv",
    [
        ("assign-profile", "p.json", "Z", REF),  # barra inexistente
        ("assign-profile", "p.json", "A", "CAT/SINT-I/NO-EXISTE"),
        ("assign-profile", "p.json", "A", "OTRO/SINT-I/IPE-SINT"),
        ("assign-profile", "p.json", "A", "CAT/SINT-I/IPE-SINT@9"),
        ("assign-profile", "p.json", "A", "mal-formada"),
    ],
)
def test_unknown_references_exit_2_and_write_nothing(
    workspace: Path, capsys: pytest.CaptureFixture[str], argv: tuple[str, ...]
) -> None:
    before = (workspace / "p.json").read_bytes()
    code, _, err = run(capsys, *argv, "--db", "cat.sqlite")
    assert code == 2
    assert err
    assert (workspace / "p.json").read_bytes() == before


def test_missing_catalog_file_exits_2(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, _, err = run(capsys, "assign-profile", "p.json", "A", REF, "--db", "no-existe.sqlite")
    assert code == 2
    assert err


def test_catalog_file_is_not_modified_by_an_assignment(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (workspace / "cat.sqlite").read_bytes()
    run(capsys, "assign-profile", "p.json", "A", REF, "--db", "cat.sqlite")
    assert (workspace / "cat.sqlite").read_bytes() == before
