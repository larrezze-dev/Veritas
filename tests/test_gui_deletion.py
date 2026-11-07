"""T-030 E2b-2: borrado de la selección con vista previa idéntica al borrado real (sin Qt)."""

from uuid import uuid4

import pytest

from gui_support import frame_model
from veritas.core.geometry.editing import ModelEditor
from veritas.core.geometry.selection_logic import Selection, SelectionKind, SelectionSet
from veritas.core.loads.load_case import LoadCategory
from veritas.gui.deletion import STEP_NAME, apply_deletion, describe, plan_deletion
from veritas.persistence.canonical import model_hash


def node(i: object) -> Selection:
    return Selection(SelectionKind.NODE, i)  # type: ignore[arg-type]


def member(i: object) -> Selection:
    return Selection(SelectionKind.MEMBER, i)  # type: ignore[arg-type]


def test_preview_does_not_touch_the_model_and_matches_the_real_deletion() -> None:
    model, ids = frame_model()
    editor = ModelEditor(model)
    selection = SelectionSet.of([node(ids["n2"]), member(ids["p2"])])
    before = model_hash(model.snapshot())
    plan = plan_deletion(model, selection)
    assert model_hash(model.snapshot()) == before
    assert plan.ok
    assert ids["n2"] in plan.nodes
    assert set(plan.members) == {ids["p1"], ids["d"], ids["p2"]}  # n2 arrastra P1 y D
    gone_before = (set(model.nodes), set(model.members), set(model.loads), set(model.supports))
    apply_deletion(editor, selection)
    assert set(plan.nodes) == gone_before[0] - set(model.nodes)
    assert set(plan.members) == gone_before[1] - set(model.members)
    assert set(plan.loads) == gone_before[2] - set(model.loads)
    assert set(plan.supports) == gone_before[3] - set(model.supports)


def test_the_whole_selection_is_one_undo_step() -> None:
    model, ids = frame_model()
    editor = ModelEditor(model)
    before = model_hash(model.snapshot())
    apply_deletion(editor, SelectionSet.of([member(ids["p1"]), member(ids["p2"]), node(ids["n3"])]))
    assert editor.undo_names == (STEP_NAME,)
    editor.undo()
    assert model_hash(model.snapshot()) == before
    editor.redo()
    assert ids["p1"] not in model.members
    assert ids["n3"] not in model.nodes


def test_a_node_whose_members_are_also_selected_is_deleted_once() -> None:
    model, ids = frame_model()
    selection = SelectionSet.of([member(ids["d"]), node(ids["n2"]), node(ids["n3"])])
    plan = plan_deletion(model, selection)
    assert plan.ok
    assert {ids["n2"], ids["n3"]} <= set(plan.nodes)
    apply_deletion(ModelEditor(model), selection)
    assert ids["n2"] not in model.nodes


def test_unknown_ids_are_ignored_and_empty_selection_is_empty_plan() -> None:
    model, _ = frame_model()
    plan = plan_deletion(model, SelectionSet.of([node(uuid4())]))
    assert plan.empty
    assert describe(model, plan) == "No hay nada que borrar."
    assert plan_deletion(model, SelectionSet()).empty


def test_a_rejected_deletion_is_reported_and_leaves_the_model_alone() -> None:
    model, ids = frame_model()
    # Dejar todas las barras restantes excluidas del peso propio hace inválido el borrado.
    case = model.add_load_case("Q", LoadCategory.PERMANENT)
    model.set_self_weight(case.id, excluded_members=[ids["p1"], ids["p2"]])
    before = model_hash(model.snapshot())
    plan = plan_deletion(model, SelectionSet.of([member(ids["d"])]))
    assert not plan.ok
    assert plan.error is not None
    assert model_hash(model.snapshot()) == before
    editor = ModelEditor(model)
    with pytest.raises(Exception):  # noqa: B017, PT011 (diagnóstico de dominio)
        apply_deletion(editor, SelectionSet.of([member(ids["d"])]))
    assert model_hash(model.snapshot()) == before
    assert not editor.can_undo


def test_describe_names_nodes_members_supports_and_loads() -> None:
    model, ids = frame_model()
    text = describe(model, plan_deletion(model, SelectionSet.of([node(ids["n1"])])))
    assert "N1" in text
    assert "P1" in text
    assert "Apoyos: 1" in text
