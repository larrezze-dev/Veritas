"""Ejecuciones históricas inmutables, vigencia y esquema v2 (plan T-012, E2; D8 y D9)."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from persistence_support import m
from test_persistence_integrity import edit, expect
from veritas.cli import _analyze_demo_model
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticCode, DiagnosticError
from veritas.core.geometry.model import StructuralModel
from veritas.fem import analyze_planar_xz
from veritas.fem.config import AnalysisConfiguration
from veritas.fem.results.normalized import PlanarResults, normalize_planar_xz
from veritas.fem.results.run import AnalysisRun, RunStatus
from veritas.fem.spatial import analyze_spatial
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.results import SpatialAnalysisRun, SpatialResults, normalize_spatial
from veritas.persistence.canonical import model_hash
from veritas.persistence.codec import decode, encode
from veritas.persistence.errors import IntegrityError
from veritas.persistence.project import append_runs, open_project, save_project
from veritas.persistence.runs import (
    RunKind,
    RunRecord,
    Validity,
    decode_record,
    encode_record,
    make_record,
    validity,
)

pytestmark = pytest.mark.solver


def viga() -> tuple[StructuralModel, Any]:
    model, case_id, *_ = _analyze_demo_model("viga")
    return model, case_id


def planar_record(model: StructuralModel, case_id: Any) -> RunRecord:
    run = analyze_planar_xz(model, AnalysisConfiguration(case_id))
    assert run.status is RunStatus.SUCCEEDED
    return make_record(model, run, normalize_planar_xz(run, model))


def spatial_record(model: StructuralModel, case_id: Any) -> RunRecord:
    run = analyze_spatial(model, SpatialAnalysisConfiguration(case_id))
    assert run.status is RunStatus.SUCCEEDED
    return make_record(model, run, normalize_spatial(run, model))


# --- Registro, hash y vigencia -----------------------------------------------------------


def test_record_carries_the_model_hash_in_the_run() -> None:
    model, case_id = viga()
    record = spatial_record(model, case_id)
    assert record.model_hash == model_hash(model.snapshot())
    assert record.run.model_hash == record.model_hash
    assert record.kind is RunKind.SPATIAL
    assert record.usable


def test_a_run_without_hash_is_still_valid_in_memory() -> None:
    model, case_id = viga()
    run = analyze_spatial(model, SpatialAnalysisConfiguration(case_id))
    assert run.model_hash is None


def test_record_of_a_changed_model_is_rejected() -> None:
    model, case_id = viga()
    run = analyze_spatial(model, SpatialAnalysisConfiguration(case_id))
    node = next(iter(model.nodes.values()))
    model.move_node(node.id, m(0.1), m(0), m(0))
    with pytest.raises(DiagnosticError) as info:
        make_record(model, run)
    assert info.value.diagnostic.message_key == "project.run_model_changed"


def test_validity_current_stale_current() -> None:
    model, case_id = viga()
    record = spatial_record(model, case_id)
    assert validity(record, model) is Validity.CURRENT
    end = next(n for n in model.nodes.values() if n.label == "N2")
    model.move_node(end.id, m(2.5), m(0), m(0))
    assert validity(record, model) is Validity.STALE
    model.move_node(end.id, m(2), m(0), m(0))
    assert validity(record, model) is Validity.CURRENT


def test_a_label_change_does_not_make_a_run_stale() -> None:
    model, case_id = viga()
    record = spatial_record(model, case_id)
    node = next(iter(model.nodes.values()))
    model.relabel_node(node.id, "Otro")
    assert validity(record, model) is Validity.CURRENT


def test_unknown_canon_version_is_stale() -> None:
    model, case_id = viga()
    record = spatial_record(model, case_id)
    assert validity(replace(record, canon_version="veritas-canon/2"), model) is Validity.STALE


# --- Guardar y abrir ---------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["planar", "spatial"])
def test_saved_run_reopens_identical(tmp_path: Path, kind: str) -> None:
    model, case_id = viga()
    record = planar_record(model, case_id) if kind == "planar" else spatial_record(model, case_id)
    save_project(tmp_path / "p.json", model, runs=[record])
    opened = open_project(tmp_path / "p.json")
    assert len(opened.runs) == 1
    again = opened.runs[0]
    # Comparación exacta por ``repr`` (== iguala 0,0 y -0,0) de la procedencia, la salida bruta
    # y el resultado normalizado.
    # Los diccionarios (configuración, versiones) se guardan con las claves ordenadas: iguales
    # por valor, no por orden de inserción.
    assert again.run == record.run
    assert repr(again.run.raw) == repr(record.run.raw)
    assert repr(sorted(again.run.configuration.items())) == repr(
        sorted(record.run.configuration.items())
    )
    assert repr(again.results) == repr(record.results)
    assert (again.kind, again.model_hash, again.canon_version) == (
        record.kind,
        record.model_hash,
        record.canon_version,
    )
    assert isinstance(again.results, PlanarResults if kind == "planar" else SpatialResults)
    assert isinstance(again.run, AnalysisRun if kind == "planar" else SpatialAnalysisRun)
    assert validity(again, opened.model) is Validity.CURRENT


def test_failed_run_is_saved_as_failed_without_results(tmp_path: Path) -> None:
    model, case_id, *_ = _analyze_demo_model("mecanismo")
    run = analyze_spatial(model, SpatialAnalysisConfiguration(case_id))
    assert run.status is RunStatus.FAILED
    record = make_record(model, run)
    assert record.results is None
    assert not record.usable
    save_project(tmp_path / "p.json", model, runs=[record])
    again = open_project(tmp_path / "p.json").runs[0]
    assert again.run.status is RunStatus.FAILED
    assert again.results is None
    assert not again.usable
    assert [d.code for d in again.run.diagnostics] == [d.code for d in run.diagnostics]


def test_failed_run_cannot_carry_results() -> None:
    model, case_id, *_ = _analyze_demo_model("mecanismo")
    failed = analyze_spatial(model, SpatialAnalysisConfiguration(case_id))
    good_model, good_case = viga()
    good = spatial_record(good_model, good_case)
    with pytest.raises(DiagnosticError):
        RunRecord(
            RunKind.SPATIAL,
            replace(failed, model_hash=good.model_hash),
            good.results,
            good.model_hash,
        )


def test_results_of_another_run_are_rejected() -> None:
    model, case_id = viga()
    first, second = spatial_record(model, case_id), spatial_record(model, case_id)
    with pytest.raises(DiagnosticError):
        RunRecord(RunKind.SPATIAL, first.run, second.results, first.model_hash)


def test_hash_mismatch_between_run_and_record_is_rejected() -> None:
    model, case_id = viga()
    record = spatial_record(model, case_id)
    with pytest.raises(DiagnosticError):
        RunRecord(RunKind.SPATIAL, record.run, record.results, "0" * 64)


# --- Inmutabilidad -----------------------------------------------------------------------


def test_append_keeps_earlier_records_byte_identical(tmp_path: Path) -> None:
    model, case_id = viga()
    first = spatial_record(model, case_id)
    path = tmp_path / "p.json"
    save_project(path, model, runs=[first])
    before = json.dumps(encode_record(open_project(path).runs[0]), sort_keys=True)
    second = planar_record(model, case_id)
    project = append_runs(path, [second])
    assert [r.run_id for r in project.runs] == [first.run_id, second.run_id]
    reopened = open_project(path)
    assert json.dumps(encode_record(reopened.runs[0]), sort_keys=True) == before
    assert reopened.runs[1].run_id == second.run_id


def test_editing_the_model_keeps_the_records_unchanged(tmp_path: Path) -> None:
    model, case_id = viga()
    record = spatial_record(model, case_id)
    path = tmp_path / "p.json"
    save_project(path, model, runs=[record])
    project = open_project(path)
    before = json.dumps(encode_record(project.runs[0]), sort_keys=True)
    node = next(n for n in project.model.nodes.values() if n.label == "N2")
    project.model.move_node(node.id, m(3), m(0), m(0))
    save_project(path, project.model, project.user_profiles, runs=project.runs)
    after = open_project(path)
    assert json.dumps(encode_record(after.runs[0]), sort_keys=True) == before
    assert validity(after.runs[0], after.model) is Validity.STALE


def test_duplicate_run_id_is_rejected(tmp_path: Path) -> None:
    model, case_id = viga()
    record = spatial_record(model, case_id)
    path = tmp_path / "p.json"
    save_project(path, model, runs=[record])
    with pytest.raises(DiagnosticError) as info:
        append_runs(path, [record])
    assert info.value.code is DiagnosticCode.REVISION_CONFLICT
    assert len(open_project(path).runs) == 1


# --- Rechazo al abrir --------------------------------------------------------------------


@pytest.fixture
def saved_with_run(tmp_path: Path) -> Path:
    model, case_id = viga()
    path = tmp_path / "r.json"
    save_project(path, model, runs=[spatial_record(model, case_id)])
    return path


def runs_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    return data["content"]["runs"]  # type: ignore[no-any-return]


def test_altered_run_is_rejected_by_integrity(saved_with_run: Path) -> None:
    text = saved_with_run.read_text(encoding="utf-8").replace('"SUCCEEDED"', '"FAILED"', 1)
    saved_with_run.write_text(text, encoding="utf-8")
    with pytest.raises(IntegrityError):
        open_project(saved_with_run)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda d: runs_of(d)[0]["run"].__setitem__("extra", 1),
        lambda d: runs_of(d)[0]["run"].pop("status"),
        lambda d: runs_of(d)[0]["run"].__setitem__("status", "OTRO"),
        lambda d: runs_of(d)[0]["run"].__setitem__("run_id", "xyz"),
        lambda d: runs_of(d)[0]["run"].__setitem__("started_utc", "2026-01-01T00:00:00"),
        lambda d: runs_of(d)[0]["run"].__setitem__("duration_s", "1"),
        lambda d: runs_of(d)[0].__setitem__("kind", "otro"),
        lambda d: runs_of(d)[0].__setitem__("model_hash", "XYZ"),
        lambda d: runs_of(d)[0]["results"].__setitem__("nodes", 3),
        lambda d: runs_of(d).append(runs_of(d)[0]),
    ],
)
def test_malformed_runs_are_rejected(saved_with_run: Path, mutation: Any) -> None:
    with pytest.raises(DiagnosticError) as info:
        open_project(edit(saved_with_run, mutation))
    assert info.value.diagnostic.message_key.startswith(("project.", "model."))


def test_results_on_a_failed_record_are_rejected(saved_with_run: Path) -> None:
    def fail(data: dict[str, Any]) -> None:
        runs_of(data)[0]["run"]["status"] = "FAILED"

    with pytest.raises(DiagnosticError):
        open_project(edit(saved_with_run, fail))


def test_non_finite_value_in_a_run_is_rejected(saved_with_run: Path) -> None:
    text = saved_with_run.read_text(encoding="utf-8").replace(
        '"duration_s": ', '"duration_s": 1e999, "x": ', 1
    )
    saved_with_run.write_text(text, encoding="utf-8")
    with pytest.raises(DiagnosticError):
        open_project(saved_with_run)


# --- Migración v1 -> v2 ------------------------------------------------------------------


def test_schema_v1_file_opens_through_the_migration(tmp_path: Path) -> None:
    model, _ = viga()
    path = tmp_path / "v2.json"
    save_project(path, model)

    def downgrade(data: dict[str, Any]) -> None:
        data["schema_version"] = 1
        del data["content"]["runs"]
        del data["content"]["norms"]

    opened = open_project(edit(path, downgrade))
    assert opened.schema_version == 1  # versión del archivo
    assert opened.runs == ()
    assert repr(opened.model.snapshot()) == repr(model.snapshot())


def test_schema_v1_file_with_runs_is_rejected(saved_with_run: Path) -> None:
    def downgrade(data: dict[str, Any]) -> None:
        data["schema_version"] = 1  # un contenido v1 no tiene «runs»

    expect(
        edit(saved_with_run, downgrade), DiagnosticCode.INPUT_INVALID_TYPE, "project.unknown_fields"
    )


def test_newer_schema_is_still_rejected(saved_with_run: Path) -> None:
    def bump(data: dict[str, Any]) -> None:
        data["schema_version"] = 5

    expect(edit(saved_with_run, bump), DiagnosticCode.UNSUPPORTED_CONFIGURATION)


# --- Codec -------------------------------------------------------------------------------


def test_codec_primitives_and_unions() -> None:
    config: dict[str, str | float | int] = {"a": 1, "b": 1.0, "c": "x"}
    data = encode(config)
    back = decode(dict[str, str | float | int], data)
    assert back == config
    assert [type(v) for v in back.values()] == [int, float, str]
    ident = uuid4()
    assert decode(type(ident), encode(ident)) == ident
    assert str(decode(float, encode(-0.0))) == "-0.0"
    for bad in (float("nan"), float("inf")):
        with pytest.raises(DiagnosticError):
            encode(bad)
    with pytest.raises(DiagnosticError):
        decode(float, "1")
    with pytest.raises(DiagnosticError):
        decode(int, True)
    with pytest.raises(DiagnosticError):
        decode(int, 1.5)
    with pytest.raises(DiagnosticError):
        decode(tuple[float, float], [1.0])
    with pytest.raises(TypeError):
        encode(object())


def test_decode_record_requires_exact_fields() -> None:
    model, case_id = viga()
    data = encode_record(spatial_record(model, case_id))
    assert decode_record(data, "r").run_id is not None
    del data["canon_version"]
    with pytest.raises(DiagnosticError) as info:
        decode_record(data, "r")
    assert info.value.diagnostic.message_key == "project.missing_fields"


def test_units_are_si_in_the_saved_results() -> None:
    model, case_id = viga()
    record = spatial_record(model, case_id)
    free = next(n for n in record.results.nodes if n.displacement.components[2].value)  # type: ignore[union-attr]
    value = free.displacement.components[2].value
    assert value is not None
    assert u.Quantity.of(value, "m").si_value == value
