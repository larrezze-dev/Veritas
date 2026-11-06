"""T-026 (TK-025): ``veritas project update-profile`` — vista previa y aplicación explícita."""

from dataclasses import replace
from pathlib import Path

import pytest

from catalog_support import make_data, make_properties, new_store
from test_catalog_assign import A_SYN, IY_SYN, IZ_SYN, J_SYN, REF, mixed_model, run
from veritas.catalogs.model import ProfileFamily
from veritas.catalogs.store import CatalogStore
from veritas.core.sections.section import SectionFamily
from veritas.persistence.project import open_project, save_project

DB = ("--db", "cat.sqlite")


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.chdir(tmp_path)
    store = new_store(tmp_path, "cat.sqlite")
    info = store.create_catalog(name="CAT", source="SINTÉTICO", license="Sin valor", edition="2026")
    data = make_data("IPE-SINT", series="SINT-I")
    data = replace(
        data, properties=make_properties(area=A_SYN, iy=IY_SYN, iz=IZ_SYN, torsion=J_SYN)
    )
    store.put_profiles(
        info.id, [data, make_data("UPN-SINT", series="SINT-U", family=ProfileFamily.U)]
    )
    store.close()
    save_project(tmp_path / "p.json", mixed_model()[0])
    return tmp_path


def new_revision(ws: Path, factor: float = 1.1) -> None:
    with CatalogStore.open(ws / "cat.sqlite") as store:
        info = store.catalog_by_name("CAT")
        latest = store.latest(info.id, "SINT-I", "IPE-SINT")
        changed = replace(
            latest.data,
            properties=make_properties(area=A_SYN * factor, iy=IY_SYN, iz=IZ_SYN, torsion=J_SYN),
        )
        store.put_profile(info.id, changed)


def assign(capsys: pytest.CaptureFixture[str], *labels: str) -> None:
    code, _, _ = run(capsys, "replace-profile", "p.json", REF, "--barras", *labels, *DB)
    assert code == 0


def sections_of(ws: Path) -> dict[str, str]:
    project = open_project(ws / "p.json")
    return {
        m.label: project.model.section(m.section).parameters.label  # type: ignore[union-attr]
        for m in project.model.members.values()
        if project.model.section(m.section).family is SectionFamily.CATALOGO
    }


def test_preview_shows_the_changes_and_writes_nothing(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assign(capsys, "A", "B")
    new_revision(workspace)
    before = (workspace / "p.json").read_bytes()
    code, out, err = run(capsys, "update-profile", "p.json", "--todas", *DB)
    assert (code, err) == (0, "")
    assert "VISTA PREVIA" in out
    assert "@1 → CAT/SINT-I/IPE-SINT@2" in out
    assert "revisión posterior" in out
    assert f"A: {A_SYN:.6g} m² → {A_SYN * 1.1:.6g} m² (+10.00%)" in out
    assert "Iy:" not in out  # lo que no cambia no se lista
    assert "barras: A, B" in out
    assert "el archivo no se ha modificado" in out
    assert (workspace / "p.json").read_bytes() == before


def test_apply_updates_the_bars_in_one_new_revision_and_is_reversible(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assign(capsys, "A", "B")
    new_revision(workspace)
    code, out, _ = run(capsys, "update-profile", "p.json", "A", "B", "--aplicar", *DB)
    assert code == 0
    assert "Proyecto guardado" in out
    assert "FEM:" in out
    assert "Norma: NO_COMPROBADO" in out
    assert sections_of(workspace) == {
        "A": "CAT/SINT-I/IPE-SINT@2",
        "B": "CAT/SINT-I/IPE-SINT@2",
    }
    project = open_project(workspace / "p.json")
    assert len({m.section for m in project.model.members.values() if m.label in ("A", "B")}) == 1
    code, out, _ = run(capsys, "update-profile", "p.json", "--todas", "--aplicar", *DB)
    assert code == 0
    assert "ningún perfil necesita actualización" in out
    code, _, _ = run(capsys, "replace-profile", "p.json", f"{REF}@1", "--barras", "A", *DB)
    assert code == 0
    assert sections_of(workspace)["A"] == "CAT/SINT-I/IPE-SINT@1"  # reversible


def test_updating_one_bar_leaves_the_others_on_the_old_revision(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assign(capsys, "A", "B")
    new_revision(workspace)
    code, _, _ = run(capsys, "update-profile", "p.json", "A", "--aplicar", *DB)
    assert code == 0
    assert sections_of(workspace) == {
        "A": "CAT/SINT-I/IPE-SINT@2",
        "B": "CAT/SINT-I/IPE-SINT@1",
    }


def test_current_absent_and_non_catalog_bars_are_listed_and_untouched(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assign(capsys, "A")
    before = (workspace / "p.json").read_bytes()
    code, out, _ = run(capsys, "update-profile", "p.json", "--todas", "--aplicar", *DB)
    assert code == 0
    assert "barra «A»: CAT/SINT-I/IPE-SINT@1 vigente" in out
    assert "barra «T»: sin perfil de catálogo" in out
    assert (workspace / "p.json").read_bytes() == before
    other = workspace / "other.sqlite"
    with CatalogStore.create(other) as store:
        store.create_catalog(name="CAT", source="S", license="L", edition="1")
    code, out, _ = run(capsys, "update-profile", "p.json", "A", "--aplicar", "--db", "other.sqlite")
    assert code == 0
    assert "no actualizable" in out
    assert (workspace / "p.json").read_bytes() == before


def test_non_catalog_bars_in_the_selection_are_skipped_not_blocked(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assign(capsys, "A")
    new_revision(workspace)
    before = (workspace / "p.json").read_bytes()
    code, out, _ = run(capsys, "update-profile", "p.json", "A", "T", "--aplicar", *DB)
    assert code == 0
    assert "barra «T»: sin perfil de catálogo" in out
    assert (workspace / "p.json").read_bytes() != before
    assert sections_of(workspace) == {"A": "CAT/SINT-I/IPE-SINT@2"}


def test_usage_errors_exit_2(workspace: Path, capsys: pytest.CaptureFixture[str]) -> None:
    for argv in (
        ("update-profile", "p.json", *DB),
        ("update-profile", "p.json", "--todas", "A", *DB),
        ("update-profile", "p.json", "ZZ", *DB),
    ):
        code, out, err = run(capsys, *argv)
        assert code == 2, out + err


def test_stored_runs_report_is_present_on_apply(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assign(capsys, "A")
    new_revision(workspace)
    code, out, _ = run(capsys, "update-profile", "p.json", "A", "--aplicar", *DB)
    assert code == 0
    assert "ejecuciones: 0 CURRENT, 0 STALE" in out
    assert "model_revision" in out


# --- T-028 E2: la revisión nueva declara materiales ------------------------------------------


def new_declared_revision(ws: Path, *materials: str, manufacturer: str | None = None) -> None:
    with CatalogStore.open(ws / "cat.sqlite") as store:
        info = store.catalog_by_name("CAT")
        latest = store.latest(info.id, "SINT-I", "IPE-SINT")
        store.put_profile(
            info.id,
            replace(latest.data, manufacturer=manufacturer, compatible_materials=materials),
        )


def test_update_lists_the_manufacturer_and_materials_that_change(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assign(capsys, "A")
    new_declared_revision(workspace, "STEEL", "ALUMINIUM", manufacturer="Acme")
    code, out, _ = run(capsys, "update-profile", "p.json", "--todas", *DB)
    assert code == 0
    assert "fabricante: sin declarar → Acme" in out
    assert "materiales compatibles: sin declarar → STEEL, ALUMINIUM" in out


def test_update_is_blocked_when_the_new_revision_excludes_the_bar_material(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assign(capsys, "A", "B")
    new_declared_revision(workspace, "ALUMINIUM")
    before = (workspace / "p.json").read_bytes()
    for extra in ((), ("--parcial",)):
        code, out, _ = run(capsys, "update-profile", "p.json", "A", "B", "--aplicar", *extra, *DB)
        assert code == 2
        assert "ALUMINIUM" in out
        assert "BLOQUEADA" in out
        assert (workspace / "p.json").read_bytes() == before  # nada aplicable: no se escribe
    assert sections_of(workspace) == {
        "A": "CAT/SINT-I/IPE-SINT@1",
        "B": "CAT/SINT-I/IPE-SINT@1",
    }
