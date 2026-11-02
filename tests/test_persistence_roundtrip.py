"""Round-trip del proyecto: guardar y abrir deja el modelo idéntico (plan T-012, E1)."""

from dataclasses import replace
from pathlib import Path

import pytest

from persistence_support import deterministic_ids, rich_model
from veritas.core import units as u
from veritas.core.connections.end import Dof, DofState, MemberEnd
from veritas.core.geometry.model import StructuralModel
from veritas.core.presentation import (
    PROFILE_SI,
    Precision,
    PrecisionMode,
    QuantityKind,
)
from veritas.fem.spatial.config import SpatialAnalysisConfiguration
from veritas.fem.spatial.translator import translate_spatial
from veritas.persistence.project import open_project, save_project


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> StructuralModel:
    deterministic_ids(monkeypatch)
    return rich_model()


def test_round_trip_is_identical_by_repr(model: StructuralModel, tmp_path: Path) -> None:
    saved = save_project(tmp_path / "p.json", model)
    opened = open_project(tmp_path / "p.json")
    # ``==`` iguala 0,0 y -0,0: la comparación exacta es por ``repr``.
    assert repr(opened.model.snapshot()) == repr(model.snapshot())
    assert opened.model.model_revision == model.model_revision
    assert opened.model_hash == saved.model_hash
    assert opened.schema_version == 2


def test_negative_zero_is_preserved_in_the_file(model: StructuralModel, tmp_path: Path) -> None:
    save_project(tmp_path / "p.json", model)
    opened = open_project(tmp_path / "p.json")
    z_values = [str(n.position.z) for n in opened.model.nodes.values()]
    assert "-0.0" in z_values
    nodal = [load for load in opened.model.loads.values() if hasattr(load, "force")]
    assert any(str(c) == "-0.0" for load in nodal for c in load.force.components())


def test_members_keep_their_older_material_and_section_revisions(
    model: StructuralModel, tmp_path: Path
) -> None:
    save_project(tmp_path / "p.json", model)
    opened = open_project(tmp_path / "p.json").model
    refs = sorted((m.material.revision, m.section.revision) for m in opened.members.values())
    assert refs == sorted((m.material.revision, m.section.revision) for m in model.members.values())
    assert (1, 1) in refs
    assert (2, 2) in refs
    for material_id in {m.material.entity_id for m in model.members.values()}:
        assert opened.material_history(material_id) == model.material_history(material_id)
    for section_id in {m.section.entity_id for m in model.members.values()}:
        assert opened.section_history(section_id) == model.section_history(section_id)


def test_end_connections_survive_t_con_005(model: StructuralModel, tmp_path: Path) -> None:
    save_project(tmp_path / "p.json", model)
    opened = open_project(tmp_path / "p.json").model
    for member in model.members.values():
        other = opened.member(member.id)
        assert other.end_i == member.end_i
        assert other.end_j == member.end_j
    b1 = next(m for m in opened.members.values() if m.label == "B1")
    assert b1.end_j.state(Dof.RY) is DofState.SPRING
    assert b1.end_j.stiffness_of(Dof.RY) == u.Quantity(2.5e6, Dof.RY.stiffness_dimension)
    b2 = next(m for m in opened.members.values() if m.label == "B2")
    assert b2.end_i.state(Dof.RY) is DofState.RELEASED
    assert b2.end_i.state(Dof.UX) is DofState.SPRING
    assert opened.member(b2.id).end_j.is_rigid
    assert MemberEnd.I.value == "I"


def test_labels_and_identities_are_kept(model: StructuralModel, tmp_path: Path) -> None:
    save_project(tmp_path / "p.json", model)
    opened = open_project(tmp_path / "p.json").model
    assert {n.id: (n.revision, n.label) for n in opened.nodes.values()} == {
        n.id: (n.revision, n.label) for n in model.nodes.values()
    }
    assert any(n.revision == 2 and n.label == "Nudo 2" for n in opened.nodes.values())
    assert opened.self_weight == model.self_weight
    assert list(opened.loads) == list(model.loads)  # orden de creación


def test_spatial_request_is_bit_identical_after_round_trip(
    model: StructuralModel, tmp_path: Path
) -> None:
    save_project(tmp_path / "p.json", model)
    opened = open_project(tmp_path / "p.json").model
    q_case = next(c for c in model.load_cases.values() if c.name == "Q")
    config = SpatialAnalysisConfiguration(q_case.id)
    before = translate_spatial(model, config)
    after = translate_spatial(opened, config)
    assert repr(after.request) == repr(before.request)
    assert after.hypotheses == before.hypotheses


def test_empty_model_round_trips(tmp_path: Path) -> None:
    save_project(tmp_path / "e.json", StructuralModel())
    opened = open_project(tmp_path / "e.json")
    assert opened.model.snapshot() == StructuralModel().snapshot()
    assert opened.model.model_revision == 0


def test_user_profiles_round_trip_and_builtins_are_not_saved(
    model: StructuralModel, tmp_path: Path
) -> None:
    mine = PROFILE_SI.derive(
        "Mi perfil",
        {QuantityKind.LENGTH: "mm", QuantityKind.FORCE: "kN"},
        {QuantityKind.FORCE: Precision(PrecisionMode.SIGNIFICANT, 5)},
    )
    saved = save_project(tmp_path / "p.json", model, [PROFILE_SI, mine])
    assert saved.user_profiles == (mine,)
    opened = open_project(tmp_path / "p.json")
    assert opened.user_profiles == (mine,)
    assert opened.user_profiles[0].precision_for(QuantityKind.FORCE) == Precision(
        PrecisionMode.SIGNIFICANT, 5
    )
    assert not opened.user_profiles[0].builtin


def test_saving_twice_gives_the_same_file(model: StructuralModel, tmp_path: Path) -> None:
    save_project(tmp_path / "a.json", model, app_version="x")
    save_project(tmp_path / "b.json", model, app_version="x")
    assert (tmp_path / "a.json").read_bytes() == (tmp_path / "b.json").read_bytes()


def test_snapshot_restore_without_files(model: StructuralModel) -> None:
    clone = StructuralModel.restore(model.snapshot())
    assert repr(clone.snapshot()) == repr(model.snapshot())
    # El clon es independiente: editarlo no cambia el original.
    node = next(iter(clone.nodes.values()))
    clone.move_node(node.id, u.Quantity.of(9, "m"), u.Quantity.of(9, "m"), u.Quantity.of(9, "m"))
    assert model.node(node.id).position != clone.node(node.id).position
    assert replace(model.snapshot()) == model.snapshot()
