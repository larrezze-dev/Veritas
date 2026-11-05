"""Normativas en el proyecto: esquema v3, migración 2→3, selección e independencia del análisis
(plan T-014, E2; D6 y D7)."""

import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from persistence_support import deterministic_ids, rich_model
from test_persistence_integrity import edit, expect
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticCode, DiagnosticError
from veritas.core.geometry.model import StructuralModel
from veritas.core.references import RevisionRef
from veritas.design_codes.builtin import CTE_PROTECTED, CTE_PROTECTED_ID
from veritas.design_codes.norms import NormConfiguration, NormKind, NormParameter, NormSystem
from veritas.design_codes.registry import NormRegistry
from veritas.persistence.canonical import model_hash
from veritas.persistence.migrations import CURRENT_SCHEMA_VERSION
from veritas.persistence.norms import NormsState
from veritas.persistence.project import open_project, save_project, update_norms

pytestmark = pytest.mark.usefixtures("ids")


@pytest.fixture
def ids(monkeypatch: pytest.MonkeyPatch) -> None:
    deterministic_ids(monkeypatch)


def make_registry() -> NormRegistry:
    counter = iter(range(1000, 2000))
    return NormRegistry(id_factory=lambda: UUID(int=next(counter) | (4 << 76) | (0b10 << 62)))


def param(key: str = "k1", value: float = 1.5) -> NormParameter:
    return NormParameter(
        key=key, value=value, dimension=u.STRESS, unit="MPa", source="Fuente ñ 1", note="nota"
    )


def populated() -> NormRegistry:
    reg = make_registry()
    a = reg.create(
        name="Ñandú «A»",
        system=NormSystem.EN,
        jurisdiction="Prueba",
        edition="2020",
        documents=("Doc A", "Doc B"),
        description="desc ✓",
        parameters=(param(), param("k2", -0.0)),
    )
    reg.edit(a.id, parameters=(param(), param("k2", 2.0), param("k3", 1e-300)))
    reg.edit(a.id, name="Ñandú v3")
    reg.copy(CTE_PROTECTED.ref, name="Mi CTE")
    return reg


def state_of(reg: NormRegistry) -> NormsState:
    return NormsState(reg.user_revisions(), reg.selected)


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "p.json"


# --- Registro: selección y restauración (D7) ----------------------------------------------


def test_select_protected_and_user_revisions_and_clear() -> None:
    reg = populated()
    user = reg.user_configurations()[0]
    assert reg.selected is None
    reg.select(CTE_PROTECTED.ref)
    assert reg.selected == CTE_PROTECTED.ref
    reg.select(RevisionRef(user.id, 1))
    assert reg.selected == RevisionRef(user.id, 1)
    reg.select(None)
    assert reg.selected is None


def test_select_rejects_missing_revision_and_type() -> None:
    reg = populated()
    with pytest.raises(DiagnosticError) as info:
        reg.select(RevisionRef(CTE_PROTECTED_ID, 2))
    assert info.value.code is DiagnosticCode.REFERENCE_NOT_FOUND
    with pytest.raises(DiagnosticError) as info:
        reg.select(CTE_PROTECTED_ID)  # type: ignore[arg-type]
    assert info.value.code is DiagnosticCode.INPUT_INVALID_TYPE
    assert reg.selected is None


def test_deleting_the_selected_configuration_is_rejected_by_the_registry() -> None:
    reg = populated()
    user = reg.user_configurations()[0]
    reg.select(RevisionRef(user.id, 2))
    with pytest.raises(DiagnosticError) as info:
        reg.delete(user.id)
    assert info.value.diagnostic.message_key == "norm.in_use"
    reg.select(CTE_PROTECTED.ref)
    assert reg.delete(user.id) == 3


def test_restore_rebuilds_the_same_registry() -> None:
    reg = populated()
    reg.select(RevisionRef(reg.user_configurations()[0].id, 2))
    again = NormRegistry.restore(reg.user_revisions(), reg.selected)
    assert repr(again.user_revisions()) == repr(reg.user_revisions())
    assert again.selected == reg.selected
    assert again.all_configurations() == reg.all_configurations()


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        ("protected_kind", DiagnosticCode.UNSUPPORTED_CONFIGURATION),
        ("protected_id", DiagnosticCode.UNSUPPORTED_CONFIGURATION),
        ("duplicate", DiagnosticCode.REVISION_CONFLICT),
        ("gap", DiagnosticCode.REVISION_CONFLICT),
        ("not_from_one", DiagnosticCode.REVISION_CONFLICT),
    ],
)
def test_restore_rejects_inconsistent_revisions(mutate: str, code: DiagnosticCode) -> None:
    first, second, third, _ = populated().user_revisions()
    cases: dict[str, list[NormConfiguration]] = {
        "protected_kind": [replace(first, kind=NormKind.PROTECTED)],
        "protected_id": [replace(first, id=CTE_PROTECTED_ID)],
        "duplicate": [first, first],
        "gap": [first, third],
        "not_from_one": [second],
    }
    with pytest.raises(DiagnosticError) as info:
        NormRegistry.restore(tuple(cases[mutate]))
    assert info.value.code is code


def test_restore_rejects_selected_unknown_or_missing_revision() -> None:
    reg = populated()
    first = reg.user_configurations()[0]
    ghost = UUID(int=5 | (4 << 76) | (0b10 << 62))
    with pytest.raises(DiagnosticError) as info:
        NormRegistry.restore(reg.user_revisions(), RevisionRef(ghost, 1))
    assert info.value.code is DiagnosticCode.UNSUPPORTED_CONFIGURATION
    with pytest.raises(DiagnosticError) as info:
        NormRegistry.restore(reg.user_revisions(), RevisionRef(first.id, 9))
    assert info.value.code is DiagnosticCode.REFERENCE_NOT_FOUND
    with pytest.raises(DiagnosticError) as info:
        NormRegistry.restore(reg.user_revisions(), RevisionRef(CTE_PROTECTED_ID, 2))
    assert info.value.code is DiagnosticCode.REFERENCE_NOT_FOUND


# --- Round-trip (D6) ----------------------------------------------------------------------


def test_round_trip_is_exact_with_all_revisions(path: Path) -> None:
    reg = populated()
    reg.select(RevisionRef(reg.user_configurations()[0].id, 2))
    saved = save_project(path, rich_model(), norms=state_of(reg))
    opened = open_project(path)
    assert opened.schema_version == CURRENT_SCHEMA_VERSION == 4
    assert repr(opened.norms.user) == repr(reg.user_revisions())
    assert opened.norms.selected == reg.selected
    assert saved.norms == opened.norms
    zero = opened.norms.user[0].parameters[1].value
    assert math.copysign(1.0, zero) == -1.0
    assert len(opened.norms.user) == 4  # tres revisiones de A y la copia
    assert opened.norms.user[3].derived_from == CTE_PROTECTED.ref


def test_default_project_has_empty_norms(path: Path) -> None:
    save_project(path, rich_model())
    opened = open_project(path)
    assert opened.norms == NormsState()
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["content"]["norms"] == {"selected": None, "user": []}


def test_protected_configurations_are_not_stored_in_the_file(path: Path) -> None:
    reg = make_registry()
    reg.select(CTE_PROTECTED.ref)
    save_project(path, rich_model(), norms=state_of(reg))
    text = path.read_text(encoding="utf-8")
    assert "CTE España" not in text
    assert open_project(path).norms.selected == CTE_PROTECTED.ref


def test_save_rejects_protected_in_user_list(path: Path) -> None:
    with pytest.raises(DiagnosticError) as info:
        save_project(path, rich_model(), norms=NormsState((CTE_PROTECTED,), None))
    assert info.value.code is DiagnosticCode.UNSUPPORTED_CONFIGURATION
    assert not path.exists()


def test_update_norms_keeps_model_profiles_and_runs(path: Path) -> None:
    model = rich_model()
    save_project(path, model)
    reg = populated()
    after = update_norms(path, state_of(reg))
    assert after.norms == state_of(reg)
    opened = open_project(path)
    assert repr(opened.model.snapshot()) == repr(model.snapshot())
    assert opened.model_hash == model_hash(model.snapshot())
    assert (path.with_name(path.name + ".bak")).exists()


# --- Validación al abrir ------------------------------------------------------------------


@pytest.fixture
def saved(path: Path) -> Path:
    reg = populated()
    reg.select(RevisionRef(reg.user_configurations()[0].id, 2))
    save_project(path, rich_model(), norms=state_of(reg))
    return path


def norms_of(data: dict[str, Any]) -> dict[str, Any]:
    return data["content"]["norms"]  # type: ignore[no-any-return]


def test_unknown_and_missing_norms_fields_are_rejected(saved: Path) -> None:
    expect(
        edit(saved, lambda d: norms_of(d).update(extra=1)),
        DiagnosticCode.INPUT_INVALID_TYPE,
        "project.unknown_fields",
    )
    expect(
        edit(saved, lambda d: norms_of(d).pop("selected")),
        DiagnosticCode.INPUT_INVALID_TYPE,
        "project.missing_fields",
    )


def test_unknown_configuration_field_is_rejected(saved: Path) -> None:
    expect(
        edit(saved, lambda d: norms_of(d)["user"][0].update(coverage="APROBADO")),
        DiagnosticCode.INPUT_INVALID_TYPE,
        "project.unknown_fields",
    )


def test_wrong_types_are_rejected(saved: Path) -> None:
    expect(
        edit(saved, lambda d: norms_of(d)["user"][0].update(name=3)),
        DiagnosticCode.INPUT_INVALID_TYPE,
    )
    expect(
        edit(saved, lambda d: norms_of(d)["user"][0]["parameters"][0].update(value="1")),
        DiagnosticCode.INPUT_INVALID_TYPE,
    )
    expect(edit(saved, lambda d: d["content"].update(norms=[])), DiagnosticCode.INPUT_INVALID_TYPE)


def test_invalid_domain_values_are_rejected(saved: Path) -> None:
    expect(
        edit(saved, lambda d: norms_of(d)["user"][0]["parameters"][0].update(source="")),
        DiagnosticCode.INPUT_INVALID_TYPE,
    )
    expect(
        edit(saved, lambda d: norms_of(d)["user"][0]["parameters"][0].update(unit="kN")),
        DiagnosticCode.UNITS_INCOMPATIBLE,
    )
    expect(
        edit(saved, lambda d: norms_of(d)["user"][0].update(kind="PROTECTED")),
        DiagnosticCode.UNSUPPORTED_CONFIGURATION,
    )


def test_duplicate_uuid_and_revision_pairs_are_rejected(saved: Path) -> None:
    def duplicate(d: dict[str, Any]) -> None:
        users = norms_of(d)["user"]
        users.append(json.loads(json.dumps(users[0])))

    expect(edit(saved, duplicate), DiagnosticCode.REVISION_CONFLICT)

    def protected_id(d: dict[str, Any]) -> None:
        norms_of(d)["user"][0]["id"] = str(CTE_PROTECTED_ID)

    expect(edit(saved, protected_id), DiagnosticCode.UNSUPPORTED_CONFIGURATION)


def test_selected_orphan_and_unknown_are_rejected(saved: Path) -> None:
    ghost = "00000000-0000-4000-8000-0000000000aa"
    expect(
        edit(saved, lambda d: norms_of(d).update(selected={"entity_id": ghost, "revision": 1})),
        DiagnosticCode.UNSUPPORTED_CONFIGURATION,
    )
    expect(
        edit(
            saved,
            lambda d: norms_of(d).update(
                selected={"entity_id": str(CTE_PROTECTED_ID), "revision": 2}
            ),
        ),
        DiagnosticCode.REFERENCE_NOT_FOUND,
    )
    expect(
        edit(saved, lambda d: norms_of(d).update(selected="x")), DiagnosticCode.INPUT_INVALID_TYPE
    )


def test_changed_byte_in_norms_breaks_integrity(saved: Path) -> None:
    raw = bytearray(saved.read_bytes())
    index = raw.index("Ñandú".encode()) + 2
    raw[index] ^= 0x01
    saved.write_bytes(bytes(raw))
    with pytest.raises(DiagnosticError):
        open_project(saved)


# --- Migración 2 → 3 y versiones ----------------------------------------------------------


def v2_file(path: Path, model: StructuralModel) -> Path:
    save_project(path, model)

    def downgrade(d: dict[str, Any]) -> None:
        d["schema_version"] = 2
        del d["content"]["norms"]

    return edit(path, downgrade)


def test_v2_file_migrates_with_empty_norms(path: Path) -> None:
    model = rich_model()
    opened = open_project(v2_file(path, model))
    assert opened.schema_version == 2  # versión del archivo
    assert opened.norms == NormsState()
    assert repr(opened.model.snapshot()) == repr(model.snapshot())


def test_v2_file_that_already_has_norms_is_rejected(path: Path) -> None:
    save_project(path, rich_model())
    expect(
        edit(path, lambda d: d.update(schema_version=2)),
        DiagnosticCode.INPUT_INVALID_TYPE,
        "project.unknown_fields",
    )


def test_v1_file_migrates_through_both_steps(path: Path) -> None:
    save_project(path, rich_model())

    def downgrade(d: dict[str, Any]) -> None:
        d["schema_version"] = 1
        del d["content"]["norms"]
        del d["content"]["runs"]

    opened = open_project(edit(path, downgrade))
    assert opened.runs == ()
    assert opened.norms == NormsState()


def test_version_five_is_rejected(path: Path) -> None:
    save_project(path, rich_model())
    expect(
        edit(path, lambda d: d.update(schema_version=5)),
        DiagnosticCode.UNSUPPORTED_CONFIGURATION,
    )


# --- Independencia del análisis (D6, D7) ---------------------------------------------------


def test_norm_changes_never_change_the_model_hash(path: Path) -> None:
    model = rich_model()
    base = model_hash(model.snapshot())
    reg = make_registry()
    first = save_project(path, model, norms=state_of(reg))
    assert first.model_hash == base
    created = reg.create(name="N", system=NormSystem.OTHER, jurisdiction="x", edition="y")
    reg.select(created.ref)
    assert update_norms(path, state_of(reg)).model_hash == base
    reg.edit(created.id, parameters=(param(),))
    reg.select(CTE_PROTECTED.ref)
    reg.delete(created.id)
    assert update_norms(path, state_of(reg)).model_hash == base
    assert open_project(path).model_hash == base


def test_canonical_module_does_not_know_norms() -> None:
    from veritas.persistence import canonical  # noqa: PLC0415

    source = Path(canonical.__file__).read_text(encoding="utf-8")
    assert "design_codes" not in source
    assert "norms" not in source.lower()


@pytest.mark.solver
def test_saved_run_stays_current_after_norm_changes(path: Path) -> None:
    from veritas.cli import _analyze_demo_model  # noqa: PLC0415
    from veritas.fem import analyze_planar_xz  # noqa: PLC0415
    from veritas.fem.config import AnalysisConfiguration  # noqa: PLC0415
    from veritas.fem.results.normalized import normalize_planar_xz  # noqa: PLC0415
    from veritas.persistence.runs import Validity, make_record, validity  # noqa: PLC0415

    model, case_id, *_ = _analyze_demo_model("viga")
    run = analyze_planar_xz(model, AnalysisConfiguration(case_id))
    record = make_record(model, run, normalize_planar_xz(run, model))
    save_project(path, model, runs=[record])
    reg = populated()
    reg.select(CTE_PROTECTED.ref)
    update_norms(path, state_of(reg))
    opened = open_project(path)
    assert [validity(r, opened.model) for r in opened.runs] == [Validity.CURRENT]
