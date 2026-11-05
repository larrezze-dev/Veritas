"""Editor paramétrico, E2 (T-025, D12-B, D14-A): comparación con el catálogo, estado CONTRASTADO,
CLI y puerta de F3A. Datos sintéticos: ningún perfil real."""

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from catalog_cli_golden import DATA_DIR, MAPPING, SYNTHETIC_ARGS
from test_catalog_snapshot import CATALOG, profile
from veritas.catalogs import bridge
from veritas.catalogs.model import ProfileFamily
from veritas.cli import main
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticError
from veritas.core.geometry.model import StructuralModel
from veritas.core.materials.material import MaterialFamily
from veritas.core.sections.catalog import CatalogSnapshot
from veritas.core.sections.contrast import ContrastVerdict, compare_with_snapshot
from veritas.core.sections.parametric import ParametricShape
from veritas.core.sections.properties import PropertyStatus, SectionProperty
from veritas.core.sections.section import SectionRevision
from veritas.persistence.project import open_project, save_project
from veritas.section_editor.compare import draft_from_snapshot

DB = "c.sqlite"
REF = "Sint/SINTETICO-I/SINT-100x50-4-6"
I_DIMS = ["--dim", "h=100", "b=50", "tw=4", "tf=6", "--unidad", "mm"]
PROPS = ("area", "iy", "iz", "torsion_constant")


def _imported(value: float | None, unit: str) -> SectionProperty:
    if value is None:
        return SectionProperty(None, unit, "S", "no aportado", PropertyStatus.NO_DISPONIBLE)
    return SectionProperty(value, unit, "S", "importado", PropertyStatus.IMPORTADO_SIN_VERIFICAR)


def _chs_model() -> tuple[StructuralModel, SectionRevision]:
    model = StructuralModel()
    section = model.add_parametric_section(
        ParametricShape.CHS,
        {"d": u.Quantity.of(100, "mm"), "t": u.Quantity.of(5, "mm")},
        name="CHS 100x5",
    )
    return model, section


def _snapshot(section: SectionRevision, **scale: float) -> CatalogSnapshot:
    """Snapshot sintético con las propiedades del editor multiplicadas por ``scale``."""
    base = bridge.snapshot_from_profile(profile(family=ProfileFamily.CHS), CATALOG)
    values: dict[str, Any] = {}
    for name, unit in (("area", "m²"), ("iy", "m⁴"), ("iz", "m⁴"), ("torsion_constant", "m⁴")):
        value = getattr(section.properties, name).value
        values[name] = _imported(None if value is None else value * scale.get(name, 1.0), unit)
    return replace(base, **values)


# --- Comparación (EDT-011) ---------------------------------------------------------------


def test_difference_is_signed_and_relative_to_the_catalog() -> None:
    _, section = _chs_model()
    report = compare_with_snapshot(section, _snapshot(section, area=1.0 / 1.005, iy=1.02), 0.01)
    area, iy = report.item("area"), report.item("iy")
    assert area.relative_difference == pytest.approx(0.005)  # Veritas por encima del catálogo
    assert area.verdict is ContrastVerdict.WITHIN
    assert iy.relative_difference == pytest.approx(1.0 / 1.02 - 1.0)  # signo negativo
    assert iy.verdict is ContrastVerdict.OUTSIDE
    assert report.all_within is False
    assert report.within == ("area", "iz", "torsion_constant")


def test_missing_property_is_never_filled_on_either_side() -> None:
    _, section = _chs_model()
    snapshot = replace(_snapshot(section), torsion_constant=_imported(None, "m⁴"))
    j = compare_with_snapshot(section, snapshot).item("torsion_constant")
    assert (j.verdict, j.catalog, j.relative_difference) == (
        ContrastVerdict.NOT_COMPARABLE,
        None,
        None,
    )
    # La J de la I del editor es NO_DISPONIBLE: no se rellena con la del catálogo.
    model = StructuralModel()
    dims = {"h": 100, "b": 50, "tw": 4, "tf": 6}
    ibeam = model.add_parametric_section(
        ParametricShape.I, {n: u.Quantity.of(v, "mm") for n, v in dims.items()}
    )
    full = replace(_snapshot(section), torsion_constant=_imported(4.0e-8, "m⁴"))
    j2 = compare_with_snapshot(ibeam, full).item("torsion_constant")
    assert j2.verdict is ContrastVerdict.NOT_COMPARABLE
    assert (j2.computed, j2.catalog) == (None, 4.0e-8)


@pytest.mark.parametrize("tolerance", [0.0, -0.1, 1.0, float("nan"), True])
def test_invalid_tolerance_is_rejected(tolerance: float) -> None:
    _, section = _chs_model()
    with pytest.raises(DiagnosticError):
        compare_with_snapshot(section, _snapshot(section), tolerance)


def test_a_catalog_section_cannot_be_compared() -> None:
    _, section = _chs_model()
    catalog_section = bridge.section_from_snapshot(
        _snapshot(section), section_id=section.id, revision=1
    )
    with pytest.raises(DiagnosticError):
        compare_with_snapshot(catalog_section, _snapshot(section))


def test_draft_from_snapshot_keeps_only_editor_dimensions() -> None:
    _, section = _chs_model()
    snapshot = bridge.snapshot_from_profile(profile(), CATALOG)  # I con solo h y b
    draft = draft_from_snapshot(snapshot)
    assert {n for n, _ in draft.dimensions} == {"h", "b"}
    assert not draft.valid  # faltan tw y tf: nunca se inventan
    with pytest.raises(ValueError, match="forma"):
        draft_from_snapshot(replace(_snapshot(section), shape="U"))


# --- register_contrast (TK-021) ----------------------------------------------------------


def test_contrast_inside_tolerance_creates_a_new_revision_with_the_veritas_value() -> None:
    model, section = _chs_model()
    snapshot = _snapshot(section, area=1.004)
    contrasted, report = model.register_contrast(section.id, snapshot, tolerance=0.01)
    assert contrasted.revision == section.revision + 1
    assert contrasted.parameters == section.parameters
    for name in PROPS:
        new, old = getattr(contrasted.properties, name), getattr(section.properties, name)
        assert new.status is PropertyStatus.CONTRASTADO
        assert (new.value, new.unit, new.method) == (old.value, old.unit, old.method)  # Veritas
    origin = contrasted.properties.area.origin
    assert snapshot.source_sha256 in origin
    assert snapshot.label in origin
    assert "tolerancia" in origin
    assert contrasted.properties.centroid_y.status is PropertyStatus.CALCULADO
    statuses = {getattr(contrasted.properties, n).status for n in (*PROPS, "iyz")}
    assert PropertyStatus.VERIFICADO not in statuses
    assert report.all_within
    # Las revisiones anteriores no cambian (inmutables) y el contraste repetido no duplica.
    assert model.section(section.ref) == section
    again, _ = model.register_contrast(section.id, snapshot, tolerance=0.01)
    assert again == contrasted
    assert len(model.snapshot().sections) == 2


def test_contrast_outside_tolerance_promotes_nothing() -> None:
    model, section = _chs_model()
    snapshot = _snapshot(section, area=1.2, iy=1.2, iz=1.2, torsion_constant=1.2)
    result, report = model.register_contrast(section.id, snapshot, tolerance=0.01)
    assert result == section
    assert report.within == ()
    assert len(model.snapshot().sections) == 1


def test_partial_contrast_promotes_only_the_properties_within_tolerance() -> None:
    model, section = _chs_model()
    result, _ = model.register_contrast(section.id, _snapshot(section, iy=1.5), tolerance=0.01)
    statuses = {n: getattr(result.properties, n).status for n in PROPS}
    assert statuses["iy"] is PropertyStatus.CALCULADO
    assert {statuses[n] for n in ("area", "iz", "torsion_constant")} == {PropertyStatus.CONTRASTADO}


def test_tampered_contrasted_value_is_rejected() -> None:
    model, section = _chs_model()
    result, _ = model.register_contrast(section.id, _snapshot(section), tolerance=0.01)
    area = result.properties.area
    assert area.value is not None
    forged = replace(result.properties, area=replace(area, value=area.value * 1.001))
    with pytest.raises(DiagnosticError):
        replace(result, properties=forged)


def test_contrasted_section_round_trips_and_keeps_the_hash(tmp_path: Path) -> None:
    model, section = _chs_model()
    model.register_contrast(section.id, _snapshot(section), tolerance=0.01)
    path = tmp_path / "p.json"
    saved = save_project(path, model)
    reopened = open_project(path)
    assert reopened.model_hash == saved.model_hash
    statuses = {s.revision: s.properties.area.status for s in reopened.model.snapshot().sections}
    assert statuses == {1: PropertyStatus.CALCULADO, 2: PropertyStatus.CONTRASTADO}


# --- CLI y puerta de F3A -----------------------------------------------------------------


@pytest.fixture
def gate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "lote.csv").write_bytes((DATA_DIR / "catalog_synthetic_100.csv").read_bytes())
    (tmp_path / MAPPING).write_bytes((DATA_DIR / MAPPING).read_bytes())
    create = ["catalog", "create", "--db", DB, "--nombre", "Sint", "--fuente", "SINTÉTICO"]
    assert main([*create, "--licencia", "Sin valor comercial", "--edicion", "2026"]) == 0
    imported = ["catalog", "import", "--db", DB, "--catalogo", "Sint", "lote.csv", *SYNTHETIC_ARGS]
    assert main(imported) == 0
    assert main(["project", "demo", "--out", "p.json", "--caso", "viga"]) == 0
    return tmp_path


def test_section_commands_show_state_method_and_the_reason_of_unavailable(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["section", "i", *I_DIMS]) == 0
    out = capsys.readouterr().out
    assert "i-closed-form/1" in out
    assert "CALCULADO" in out
    assert "sin-solucion-exacta-validada/1" in out  # J de la I
    assert "NO_DISPONIBLE" in out
    assert "flujo horizontal" in out
    assert main(["section", "chs", "--dim", "d=100", "t=5", "--unidad", "mm"]) == 0
    assert "annulus-torsion-exact/1" in capsys.readouterr().out
    assert main(["section", "shs", "--dim", "b=100", "t=5", "--unidad", "mm"]) == 0
    assert main(["section", "rhs", "--dim", "h=120", "b=80", "t=5", "--unidad", "mm"]) == 0


def test_section_rect_output_is_unchanged(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["section", "rect", "--b", "0.2", "--h", "0.4"]) == 0
    assert "Área de cortante" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["section", "i", "--dim", "h=100", "b=50", "tw=4"],  # falta tf
        ["section", "i", "--dim", "h=100", "b=50", "tw=4", "tf=60", "--unidad", "mm"],  # 2tf > h
        ["section", "chs", "--dim", "d=100", "t"],  # sin «=»
        ["section", "chs", "--dim", "d=100", "t=x"],
        ["section", "chs", "--dim", "d=100", "t=5", "t=6"],
        ["section", "chs", "--dim", "d=100", "q=5"],
    ],
)
def test_invalid_input_exits_2_with_a_diagnostic(
    argv: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(argv) == 2
    assert capsys.readouterr().err


def test_compare_reports_signed_differences_and_changes_nothing(
    gate: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (gate / DB).read_bytes()
    capsys.readouterr()
    assert main(["section", "comparar", "i", *I_DIMS, "--ref", REF, "--db", DB]) == 0
    out = capsys.readouterr().out
    assert "DENTRO" in out
    assert "NO_COMPARABLE" in out
    assert "no se rellena" in out
    assert "Solo informe" in out
    assert (gate / DB).read_bytes() == before
    far = ["--dim", "h=100", "b=50", "tw=4", "tf=7", "--unidad", "mm"]
    assert main(["section", "comparar", "i", *far, "--ref", REF, "--db", DB]) == 0
    assert "FUERA" in capsys.readouterr().out
    assert main(["section", "comparar", "i", *I_DIMS, "--ref", REF, "--db", DB, "--tol", "x"]) == 2


def test_f3a_gate_end_to_end(gate: Path, capsys: pytest.CaptureFixture[str]) -> None:
    add = ["project", "add-section", "p.json", "i", *I_DIMS, "--nombre", "I100"]
    # --contrastar y --db van juntos.
    assert main([*add, "--contrastar", REF]) == 2
    capsys.readouterr()

    # Fuera de tolerancia: código 2 y el archivo NO cambia.
    before = (gate / "p.json").read_bytes()
    far = ["--dim", "h=100", "b=50", "tw=4", "tf=7", "--unidad", "mm", "--nombre", "I7"]
    assert (
        main(["project", "add-section", "p.json", "i", *far, "--contrastar", REF, "--db", DB]) == 2
    )
    assert "Nada escrito" in capsys.readouterr().out
    assert (gate / "p.json").read_bytes() == before

    # Dentro de tolerancia: CONTRASTADO con el valor de Veritas, guardado.
    assert main([*add, "--contrastar", REF, "--db", DB]) == 0
    out = capsys.readouterr().out
    assert "CONTRASTADO registrado en A, Iy, Iz" in out
    assert "VERIFICADO" not in (gate / "p.json").read_text(encoding="utf-8")

    # Asignación estricta y listado con los dos dictámenes, el cortante y el estado.
    assert main(["project", "assign-section", "p.json", "B1", "I100"]) == 0
    capsys.readouterr()
    assert main(["project", "sections", "p.json"]) == 0
    listing = capsys.readouterr().out
    assert "CONTRASTADO" in listing
    assert "Avz" in listing
    assert "FEM:" in listing
    assert "Norma:" in listing

    # El proyecto se reabre sin catálogo: estado, valor y hash intactos.
    (gate / DB).unlink()
    assert main(["project", "verify", "p.json"]) == 0
    capsys.readouterr()
    reopened = open_project(gate / "p.json")
    member = next(iter(reopened.model.members.values()))
    section = reopened.model.section(member.section)
    assert section.properties.area.status is PropertyStatus.CONTRASTADO
    assert section.properties.torsion_constant.status is PropertyStatus.NO_DISPONIBLE
    assert section.shear is not None
    assert section.shear.shear_y.value is None


def test_assign_section_modes_and_errors(gate: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["project", "add-section", "p.json", "i", *I_DIMS, "--nombre", "I100"]) == 0
    assert main(["project", "assign-section", "p.json", "--todas", "I100"]) == 0
    capsys.readouterr()
    # Misma asignación otra vez: sin cambios, el archivo no se toca.
    before = (gate / "p.json").read_bytes()
    assert main(["project", "assign-section", "p.json", "--todas", "I100"]) == 0
    assert "Sin cambios" in capsys.readouterr().out
    assert (gate / "p.json").read_bytes() == before
    # Sección inexistente, barra inexistente y uso incorrecto: código 2.
    assert main(["project", "assign-section", "p.json", "B1", "NOEXISTE"]) == 2
    assert main(["project", "assign-section", "p.json", "B9", "I100"]) == 2
    assert main(["project", "assign-section", "p.json", "--todas", "B1", "I100"]) == 2


def test_assign_section_blocks_a_timber_bar_strictly(
    gate: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = open_project(gate / "p.json")
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
        specific_weight=u.Quantity.of(4.2, "kN/m³"),
    )
    member = next(iter(model.members.values()))
    model.assign_material(member.id, timber.ref)
    save_project(gate / "p.json", model)
    assert main(["project", "add-section", "p.json", "i", *I_DIMS, "--nombre", "I100"]) == 0
    before = (gate / "p.json").read_bytes()
    capsys.readouterr()
    assert main(["project", "assign-section", "p.json", "B1", "I100"]) == 2
    assert "rechazada" in capsys.readouterr().out
    assert (gate / "p.json").read_bytes() == before
