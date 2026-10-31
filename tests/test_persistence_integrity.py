"""Integridad, rechazo, escritura segura, migraciones y límites (plan T-012, D5 a D7)."""

import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from persistence_support import deterministic_ids, rich_model
from veritas.core.diagnostics import DiagnosticCode, DiagnosticError
from veritas.core.geometry.model import StructuralModel
from veritas.core.presentation import PROFILE_SI, QuantityKind
from veritas.persistence import project as project_module
from veritas.persistence.canonical import canonical_bytes, sha256_hex
from veritas.persistence.errors import IntegrityError
from veritas.persistence.migrations import CURRENT_SCHEMA_VERSION, migrate
from veritas.persistence.project import open_project, save_project

Mutation = Callable[[dict[str, Any]], object]

NOT_A_NODE = "00000000-0000-4000-8000-0000000000ff"


@pytest.fixture
def saved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    deterministic_ids(monkeypatch)
    path = tmp_path / "p.json"
    save_project(path, rich_model())
    return path


def resign(data: dict[str, Any]) -> dict[str, Any]:
    body = {k: v for k, v in data.items() if k != "integrity"}
    data["integrity"] = {"algorithm": "sha256", "digest": sha256_hex(canonical_bytes(body))}
    return data


def edit(path: Path, mutation: Mutation, *, sign: bool = True) -> Path:
    data = json.loads(path.read_text(encoding="utf-8"))
    mutation(data)
    if sign:
        resign(data)
    target = path.with_name("edited.json")
    target.write_text(json.dumps(data), encoding="utf-8")
    return target


def mdl(data: dict[str, Any]) -> dict[str, Any]:
    return data["content"]["model"]  # type: ignore[no-any-return]


def expect(path: Path, code: DiagnosticCode, key: str | None = None) -> DiagnosticError:
    with pytest.raises(DiagnosticError) as info:
        open_project(path)
    assert info.value.code is code
    if key is not None:
        assert info.value.diagnostic.message_key == key
    return info.value


# --- Integridad --------------------------------------------------------------------------


def test_valid_file_opens(saved: Path) -> None:
    assert len(open_project(saved).model.nodes) == 4


def test_one_changed_byte_is_rejected_by_integrity(saved: Path) -> None:
    raw = bytearray(saved.read_bytes())
    index = raw.index(b'"N1"') + 2
    raw[index] = ord("X")
    saved.write_bytes(bytes(raw))
    with pytest.raises(IntegrityError):
        open_project(saved)


def test_changed_number_is_rejected_by_integrity(saved: Path) -> None:
    def tweak(data: dict[str, Any]) -> None:
        mdl(data)["nodes"][1]["position"][0] = 3.0001

    with pytest.raises(IntegrityError):
        open_project(edit(saved, tweak, sign=False))


def test_whitespace_reformatting_keeps_integrity(saved: Path) -> None:
    data = json.loads(saved.read_text(encoding="utf-8"))
    saved.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
    assert open_project(saved).model.model_revision == 21


def test_truncated_file_is_rejected(saved: Path) -> None:
    saved.write_bytes(saved.read_bytes()[:-200])
    expect(saved, DiagnosticCode.INPUT_INVALID_TYPE, "project.invalid_json")


def test_missing_integrity_is_rejected(saved: Path) -> None:
    def drop(data: dict[str, Any]) -> None:
        del data["integrity"]

    expect(
        edit(saved, drop, sign=False), DiagnosticCode.INPUT_INVALID_TYPE, "project.missing_fields"
    )


@pytest.mark.parametrize("payload", [b"", b"not json", b"[1, 2]", b"\xff\xfe\x00", b'{"a": NaN}'])
def test_invalid_json_payloads(tmp_path: Path, payload: bytes) -> None:
    path = tmp_path / "bad.json"
    path.write_bytes(payload)
    with pytest.raises(DiagnosticError) as info:
        open_project(path)
    assert info.value.code is DiagnosticCode.INPUT_INVALID_TYPE


def test_duplicate_json_key_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "dup.json"
    path.write_text('{"format": "a", "format": "b"}', encoding="utf-8")
    expect(path, DiagnosticCode.INPUT_INVALID_TYPE, "project.duplicate_key")


def test_other_format_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    path.write_text('{"format": "otro"}', encoding="utf-8")
    expect(path, DiagnosticCode.INPUT_INVALID_TYPE, "project.bad_format")


def test_missing_file_is_a_diagnostic(tmp_path: Path) -> None:
    expect(tmp_path / "no.json", DiagnosticCode.INPUT_INVALID_TYPE, "project.unreadable")


# --- Validación estricta (con integridad válida) -----------------------------------------


def test_unknown_field_is_rejected(saved: Path) -> None:
    def add(data: dict[str, Any]) -> None:
        mdl(data)["nodes"][0]["extra"] = 1

    expect(edit(saved, add), DiagnosticCode.INPUT_INVALID_TYPE, "project.unknown_fields")


def test_unknown_top_level_field_is_rejected(saved: Path) -> None:
    def add(data: dict[str, Any]) -> None:
        data["extra"] = 1

    expect(edit(saved, add), DiagnosticCode.INPUT_INVALID_TYPE, "project.unknown_fields")


def test_missing_field_is_rejected(saved: Path) -> None:
    def drop(data: dict[str, Any]) -> None:
        del mdl(data)["nodes"][0]["position"]

    expect(edit(saved, drop), DiagnosticCode.INPUT_INVALID_TYPE, "project.missing_fields")


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: mdl(d)["nodes"][0].__setitem__("revision", "1"),
        lambda d: mdl(d)["nodes"][0].__setitem__("revision", True),
        lambda d: mdl(d)["nodes"][0].__setitem__("label", 5),
        lambda d: mdl(d)["nodes"][0].__setitem__("position", [1, 2]),
        lambda d: mdl(d)["nodes"][0].__setitem__("position", [1, 2, "3"]),
        lambda d: mdl(d)["nodes"][0].__setitem__("id", "no-es-uuid"),
        lambda d: mdl(d)["nodes"][0].__setitem__("id", str(uuid4()).upper()),
        lambda d: mdl(d)["members"][0].__setitem__("material", [1]),
        lambda d: mdl(d)["supports"][0]["restraints"].pop(),
        lambda d: mdl(d)["loads"][0].__setitem__("type", "magia"),
        lambda d: mdl(d)["load_cases"][0].__setitem__("category", "OTRA"),
        lambda d: mdl(d).__setitem__("model_revision", -1),
        lambda d: mdl(d).__setitem__("nodes", {}),
    ],
)
def test_wrong_types_are_rejected(saved: Path, mutation: Mutation) -> None:
    with pytest.raises(DiagnosticError) as info:
        open_project(edit(saved, mutation))
    assert info.value.diagnostic.message_key.startswith("project.")


def test_non_finite_number_is_rejected(saved: Path) -> None:
    text = saved.read_text(encoding="utf-8").replace('"alpha": 0.0', '"alpha": 1e999', 1)
    saved.write_text(text, encoding="utf-8")
    with pytest.raises(DiagnosticError) as info:
        open_project(saved)
    assert info.value.code is DiagnosticCode.INPUT_NONFINITE


def test_duplicate_uuid_is_rejected(saved: Path) -> None:
    def dup(data: dict[str, Any]) -> None:
        nodes = mdl(data)["nodes"]
        nodes[1]["id"] = nodes[0]["id"]

    expect(edit(saved, dup), DiagnosticCode.REVISION_CONFLICT, "model.revision_conflict")


def test_orphan_node_reference_is_rejected(saved: Path) -> None:
    def orphan(data: dict[str, Any]) -> None:
        mdl(data)["members"][0]["node_j"] = NOT_A_NODE

    expect(edit(saved, orphan), DiagnosticCode.REFERENCE_NOT_FOUND, "model.reference_not_found")


def test_orphan_section_revision_is_rejected(saved: Path) -> None:
    def orphan(data: dict[str, Any]) -> None:
        mdl(data)["members"][0]["section"]["revision"] = 9

    expect(edit(saved, orphan), DiagnosticCode.REFERENCE_NOT_FOUND)


def test_orphan_load_target_is_rejected(saved: Path) -> None:
    def orphan(data: dict[str, Any]) -> None:
        nodal = next(x for x in mdl(data)["loads"] if x["type"] == "nodal")
        nodal["node_id"] = NOT_A_NODE

    expect(edit(saved, orphan), DiagnosticCode.REFERENCE_NOT_FOUND)


def test_two_supports_on_one_node_are_rejected(saved: Path) -> None:
    def twice(data: dict[str, Any]) -> None:
        supports = mdl(data)["supports"]
        supports[1]["node_id"] = supports[0]["node_id"]

    expect(edit(saved, twice), DiagnosticCode.REVISION_CONFLICT)


def test_inconsistent_section_properties_are_rejected(saved: Path) -> None:
    def doubled(data: dict[str, Any]) -> None:
        prop = mdl(data)["sections"][0]["properties"]["area"]
        prop["value"] = prop["value"] * 2

    expect(edit(saved, doubled), DiagnosticCode.INPUT_OUT_OF_RANGE, "section.properties_mismatch")


def test_excluding_every_member_from_self_weight_is_rejected(saved: Path) -> None:
    def exclude_all(data: dict[str, Any]) -> None:
        weight = next(x for x in mdl(data)["loads"] if x["type"] == "self_weight")
        weight["excluded_members"] = sorted(m["id"] for m in mdl(data)["members"])

    expect(
        edit(saved, exclude_all),
        DiagnosticCode.INPUT_OUT_OF_RANGE,
        "load.self_weight_all_excluded",
    )


def test_self_weight_in_a_variable_case_is_rejected(saved: Path) -> None:
    def to_variable(data: dict[str, Any]) -> None:
        weight = next(x for x in mdl(data)["loads"] if x["type"] == "self_weight")
        case = next(c for c in mdl(data)["load_cases"] if c["id"] == weight["case_id"])
        case["category"] = "VARIABLE"

    expect(edit(saved, to_variable), DiagnosticCode.INPUT_OUT_OF_RANGE)


def test_settlement_on_a_free_dof_is_rejected(saved: Path) -> None:
    def free_dof(data: dict[str, Any]) -> None:
        settlement = next(x for x in mdl(data)["loads"] if x["type"] == "settlement")
        support = next(s for s in mdl(data)["supports"] if s["id"] == settlement["support_id"])
        support["restraints"][2] = {"kind": "FREE", "stiffness": None}

    expect(
        edit(saved, free_dof), DiagnosticCode.INPUT_OUT_OF_RANGE, "load.settlement_dof_not_fixed"
    )


def test_member_with_degenerate_geometry_is_rejected(saved: Path) -> None:
    def same_position(data: dict[str, Any]) -> None:
        nodes = mdl(data)["nodes"]
        nodes[1]["position"] = list(nodes[0]["position"])

    with pytest.raises(DiagnosticError):
        open_project(edit(saved, same_position))


def test_stale_model_hash_is_rejected(saved: Path) -> None:
    def stale(data: dict[str, Any]) -> None:
        data["model_hash"] = "0" * 64

    with pytest.raises(IntegrityError):
        open_project(edit(saved, stale))


def test_builtin_profile_name_in_file_is_rejected(saved: Path) -> None:
    def builtin(data: dict[str, Any]) -> None:
        profile = {"name": "SI", "units": {}, "precision": {}}
        data["content"]["settings"]["user_profiles"].append(profile)

    with pytest.raises(DiagnosticError):
        open_project(edit(saved, builtin))


# --- Versiones y migraciones -------------------------------------------------------------


def test_newer_schema_version_is_rejected(saved: Path) -> None:
    def bump(data: dict[str, Any]) -> None:
        data["schema_version"] = CURRENT_SCHEMA_VERSION + 1

    error = expect(edit(saved, bump), DiagnosticCode.UNSUPPORTED_CONFIGURATION)
    assert error.diagnostic.message_key == "project.unsupported_version"


@pytest.mark.parametrize("version", [0, -1, "1", None, 1.5, True])
def test_invalid_schema_version_is_rejected(saved: Path, version: object) -> None:
    def put(data: dict[str, Any]) -> None:
        data["schema_version"] = version

    with pytest.raises(DiagnosticError):
        open_project(edit(saved, put))


def test_synthetic_migration_chain() -> None:
    def to_two(content: dict[str, object]) -> dict[str, object]:
        return {**content, "two": True}

    def to_three(content: dict[str, object]) -> dict[str, object]:
        return {**content, "three": True}

    migrated = migrate({"a": 1}, 1, target=3, registry={1: to_two, 2: to_three})
    assert migrated == {"a": 1, "two": True, "three": True}
    assert migrate({"a": 1}, 3, target=3, registry={}) == {"a": 1}


def test_migration_chain_with_a_gap_is_rejected() -> None:
    with pytest.raises(DiagnosticError) as info:
        migrate({}, 1, target=3, registry={2: lambda c: c})
    assert info.value.diagnostic.message_key == "project.migration_gap"


def test_version_newer_than_target_is_rejected() -> None:
    with pytest.raises(DiagnosticError) as info:
        migrate({}, 4, target=3, registry={})
    assert info.value.code is DiagnosticCode.UNSUPPORTED_CONFIGURATION


# --- Límites (NFR-006) -------------------------------------------------------------------


def test_file_above_size_limit_is_rejected(saved: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(project_module, "MAX_FILE_BYTES", 100)
    expect(saved, DiagnosticCode.INPUT_OUT_OF_RANGE, "project.too_large")


def test_excessive_nesting_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "deep.json"
    path.write_text('{"format": ' + "[" * 40 + "]" * 40 + "}", encoding="utf-8")
    expect(path, DiagnosticCode.INPUT_OUT_OF_RANGE, "project.too_deep")


def test_extreme_nesting_does_not_crash(tmp_path: Path) -> None:
    path = tmp_path / "deeper.json"
    path.write_text("[" * 200_000, encoding="utf-8")
    with pytest.raises(DiagnosticError):
        open_project(path)


def test_file_content_is_never_executed(tmp_path: Path) -> None:
    marker = tmp_path / "ejecutado"
    path = tmp_path / "evil.json"
    payload = {"format": f"__import__('pathlib').Path(r'{marker}').touch()"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DiagnosticError):
        open_project(path)
    assert not marker.exists()


# --- Escritura segura (NFR-003) ----------------------------------------------------------


def test_failure_before_replace_keeps_the_previous_file(
    saved: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = saved.read_bytes()

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("disco lleno")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(DiagnosticError) as info:
        save_project(saved, StructuralModel())
    assert info.value.diagnostic.message_key == "project.write_failed"
    monkeypatch.undo()
    assert saved.read_bytes() == before
    assert [p.name for p in saved.parent.iterdir() if p.name.endswith(".tmp")] == []
    assert open_project(saved).model.model_revision == 21


def test_bak_is_the_previous_version(saved: Path) -> None:
    first = saved.read_bytes()
    save_project(saved, StructuralModel())
    bak = saved.with_name(saved.name + ".bak")
    assert bak.read_bytes() == first
    assert open_project(saved).model.model_revision == 0
    assert open_project(bak).model.model_revision == 21


def test_first_save_creates_no_bak(tmp_path: Path) -> None:
    save_project(tmp_path / "n.json", StructuralModel())
    assert not (tmp_path / "n.json.bak").exists()


def test_duplicate_profile_names_are_rejected_on_save(tmp_path: Path) -> None:
    mine = PROFILE_SI.derive("Mío", {QuantityKind.LENGTH: "mm"})
    with pytest.raises(DiagnosticError):
        save_project(tmp_path / "d.json", StructuralModel(), [mine, mine])
    assert not (tmp_path / "d.json").exists()


def test_unknown_canon_version_is_rejected(saved: Path) -> None:
    def other(data: dict[str, Any]) -> None:
        data["canon_version"] = "veritas-canon/2"

    error = expect(edit(saved, other), DiagnosticCode.UNSUPPORTED_CONFIGURATION)
    assert error.diagnostic.message_key == "project.unsupported_canon"
