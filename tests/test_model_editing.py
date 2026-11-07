"""T-030 E1: borrado con dependientes (TK-009, D4-A) y edición con deshacer/rehacer (GEO-009,
D2-A, D3-A; ADR-014). Todo en el dominio, sin ventana."""

from collections.abc import Callable
from dataclasses import dataclass, fields
from pathlib import Path
from typing import get_args
from uuid import UUID

import pytest

from fem_support import Frame
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticError
from veritas.core.geometry.editing import ModelEditor
from veritas.core.geometry.entities import Member, Node
from veritas.core.geometry.model import _MEMBER_LOAD_TYPES, StructuralModel
from veritas.core.loads.loads import Load
from veritas.core.supports.support import fixed_restraints, pinned_restraints
from veritas.persistence.canonical import model_hash
from veritas.persistence.project import open_project, save_project

M = u.Quantity.of


def key_of(error: DiagnosticError) -> str:
    return error.diagnostic.message_key


@dataclass
class Scene:
    """Pórtico con todas las dependencias posibles: cargas de barra, nodales, apoyo, asiento y
    exclusiones del peso propio."""

    frame: Frame
    a: Node
    b: Node
    c: Node
    ab: Member
    bc: Member
    load_ab: UUID
    load_nodal: UUID
    settlement: UUID

    @property
    def model(self) -> StructuralModel:
        return self.frame.model


def scene() -> Scene:
    frame = Frame()
    a, b, c = frame.node(0.0, 0.0), frame.node(3.0, 0.0), frame.node(6.0, 0.0)
    ab, bc = frame.member(a, b), frame.member(b, c)
    frame.support(a, fixed_restraints())
    frame.support(c, pinned_restraints())
    model = frame.model
    load_ab = model.add_member_uniform_load(frame.case_id, ab.id, qz=M(-1000.0, "N/m")).id
    load_nodal = model.add_nodal_load(frame.case_id, b.id, fz=M(-500.0, "N")).id
    settlement = model.add_support_settlement(frame.case_id, a.id, uz=M(-0.01, "m")).id
    model.set_self_weight(frame.case_id, excluded_members=[bc.id])
    return Scene(frame, a, b, c, ab, bc, load_ab, load_nodal, settlement)


# --- remove_member / remove_node en el dominio (D4-A) -----------------------------------


def test_removing_a_member_with_loads_is_rejected_listing_them() -> None:
    s = scene()
    with pytest.raises(DiagnosticError) as error:
        s.model.remove_member(s.ab.id)
    assert key_of(error.value) == "model.remove_member_dependents"
    assert str(s.load_ab) in str(error.value.diagnostic.params_dict()["loads"])
    assert s.ab.id in s.model.members  # nada cambió


def test_removing_a_member_without_loads_cleans_the_self_weight_exclusion() -> None:
    s = scene()
    model = s.model
    weight = model.self_weight
    assert weight is not None
    assert weight.excluded_members == {s.bc.id}
    # bc está excluida del peso propio y ab no: al borrar bc sobrevive ab, que no está excluida.
    revision = model.model_revision
    removed = model.remove_member(s.bc.id)
    assert removed.id == s.bc.id
    assert s.bc.id not in model.members
    assert model.model_revision == revision + 1
    cleaned = model.self_weight
    assert cleaned is not None
    assert cleaned.excluded_members == frozenset()
    assert cleaned.revision == weight.revision + 1


def test_removing_the_only_non_excluded_member_would_leave_all_excluded_and_is_rejected() -> None:
    s = scene()
    for load_id in s.model.member_dependents(s.ab.id):
        s.model.remove_load(load_id)
    before = s.model.snapshot()
    with pytest.raises(DiagnosticError) as excluded:
        s.model.remove_member(s.ab.id)  # quedaría solo bc, que está excluida del peso propio
    assert key_of(excluded.value) == "model.remove_member_all_excluded"
    assert s.model.snapshot() == before


def test_removing_a_node_with_dependents_is_rejected_with_the_counts() -> None:
    s = scene()
    with pytest.raises(DiagnosticError) as error:
        s.model.remove_node(s.a.id)
    assert key_of(error.value) == "model.remove_node_dependents"
    params = error.value.diagnostic.params_dict()
    assert (params["members"], params["supports"], params["settlements"]) == (1, 1, 1)
    deps = s.model.node_dependents(s.b.id)
    assert deps.members == (s.ab.id, s.bc.id)
    assert deps.loads == (s.load_nodal,)
    assert deps.support is None
    assert bool(deps)


def test_removing_a_free_node_works_and_a_missing_one_is_a_diagnostic() -> None:
    s = scene()
    free = s.model.add_node(M(9, "m"), M(0, "m"), M(0, "m"))
    assert not s.model.node_dependents(free.id)
    s.model.remove_node(free.id)
    assert free.id not in s.model.nodes
    with pytest.raises(DiagnosticError):
        s.model.remove_node(free.id)
    with pytest.raises(DiagnosticError):
        s.model.remove_member(free.id)


def test_a_model_after_any_removal_still_restores_without_dangling_references() -> None:
    s = scene()
    editor = ModelEditor(s.model)
    editor.execute("Incluir bc", lambda m: m.set_self_weight(s.frame.case_id))
    editor.remove_member_with_dependents(s.ab.id)
    # restore comprueba todas las referencias: no debe lanzar.
    StructuralModel.restore(editor.model.snapshot())


# --- Cascada: un solo paso de deshacer ---------------------------------------------------


def test_cascade_member_removal_is_one_atomic_step() -> None:
    s = scene()
    editor = ModelEditor(s.model)
    before = model_hash(editor.model.snapshot())
    before_loads = len(editor.model.loads)
    with pytest.raises(DiagnosticError):
        editor.remove_member_with_dependents(s.ab.id)  # quedaría solo bc, toda excluida
    assert model_hash(editor.model.snapshot()) == before  # el fallo no dejó nada a medias
    assert len(editor.model.loads) == before_loads
    assert list(editor.undo_names) == []
    # Con bc incluida en el peso propio sí se puede: la cascada borra su carga y la barra.
    editor.execute("Incluir bc", lambda m: m.set_self_weight(s.frame.case_id))
    editor.remove_member_with_dependents(s.ab.id)
    assert s.ab.id not in editor.model.members
    assert s.load_ab not in editor.model.loads
    assert editor.undo_names == ("Incluir bc", "Borrar barra con dependientes")
    editor.undo()
    assert s.ab.id in editor.model.members
    assert s.load_ab in editor.model.loads


def test_cascade_node_removal_removes_members_loads_support_and_settlements() -> None:
    s = scene()
    editor = ModelEditor(s.model)
    editor.execute("Incluir bc", lambda m: m.set_self_weight(s.frame.case_id))
    hash_before = model_hash(editor.model.snapshot())
    editor.remove_node_with_dependents(s.a.id)
    model = editor.model
    assert s.a.id not in model.nodes
    assert s.ab.id not in model.members
    assert s.load_ab not in model.loads
    assert s.settlement not in model.loads
    assert s.bc.id in model.members  # no depende de a
    StructuralModel.restore(model.snapshot())  # sin referencias colgantes
    assert editor.undo_names == ("Incluir bc", "Borrar nudo con dependientes")
    editor.undo()
    assert model_hash(editor.model.snapshot()) == hash_before


def test_cascade_node_removal_that_cannot_complete_changes_nothing() -> None:
    s = scene()
    editor = ModelEditor(s.model)
    snapshot = editor.model.snapshot()
    with pytest.raises(DiagnosticError) as error:  # borrar a deja bc, que está excluida
        editor.remove_node_with_dependents(s.a.id)
    assert key_of(error.value) == "model.remove_member_all_excluded"
    assert editor.model.snapshot() == snapshot
    assert not editor.can_undo


def test_cascade_result_does_not_depend_on_the_order_of_the_members() -> None:
    """Revisión de arquitectura H1: el estado final válido (sin barras) no depende del orden."""
    s = scene()
    editor = ModelEditor(s.model)
    editor.remove_node_with_dependents(s.b.id)  # barras ab y bc, bc excluida del peso propio
    assert not editor.model.members
    assert s.b.id not in editor.model.nodes
    StructuralModel.restore(editor.model.snapshot())
    editor.undo()
    assert {s.ab.id, s.bc.id} == set(editor.model.members)
    first = scene()
    second = scene()
    editor_a, editor_b = ModelEditor(first.model), ModelEditor(second.model)
    removed_a = editor_a.remove_members([first.ab.id, first.bc.id])
    removed_b = editor_b.remove_members([second.bc.id, second.ab.id, second.bc.id])  # repetido
    assert len(removed_a) == len(removed_b) == 2
    assert [m.label for m in removed_a] == [m.label for m in removed_b]  # mismo orden de borrado
    assert not editor_a.model.members
    assert not editor_b.model.members
    assert len(editor_a.undo_names) == len(editor_b.undo_names) == 1


# --- Editor: historial, atomicidad y transacciones ---------------------------------------


def test_undo_and_redo_restore_entities_connectivity_and_hash() -> None:
    editor = ModelEditor()
    base = model_hash(editor.model.snapshot())
    a = editor.add_node(M(0, "m"), M(0, "m"), M(0, "m"), label="A")
    b = editor.add_node(M(4, "m"), M(0, "m"), M(0, "m"), label="B")
    editor.move_node(b.id, M(5, "m"), M(0, "m"), M(0, "m"))
    editor.relabel_node(a.id, "A1")
    hashes = [model_hash(editor.model.snapshot())]
    assert editor.undo_names == ("Crear nudo", "Crear nudo", "Mover nudo", "Renombrar nudo")
    assert editor.undo() == "Renombrar nudo"
    assert editor.model.node(a.id).label == "A"
    assert editor.undo() == "Mover nudo"
    assert editor.model.node(b.id).position.x == 4.0
    editor.undo()
    editor.undo()
    assert model_hash(editor.model.snapshot()) == base
    assert not editor.model.nodes
    assert not editor.can_undo
    assert editor.can_redo
    assert editor.redo_names == ("Crear nudo", "Crear nudo", "Mover nudo", "Renombrar nudo")
    for _ in range(4):
        editor.redo()
    assert model_hash(editor.model.snapshot()) == hashes[0]
    assert editor.model.node(a.id).label == "A1"
    assert not editor.can_redo


def test_a_new_command_clears_the_redo_stack() -> None:
    editor = ModelEditor()
    editor.add_node(M(0, "m"), M(0, "m"), M(0, "m"))
    editor.undo()
    assert editor.can_redo
    editor.add_node(M(1, "m"), M(0, "m"), M(0, "m"))
    assert not editor.can_redo
    with pytest.raises(DiagnosticError) as error:
        editor.redo()
    assert key_of(error.value) == "editing.nothing_to_redo"


def test_a_command_that_changes_nothing_leaves_no_step() -> None:
    s = scene()
    editor = ModelEditor(s.model)
    editor.execute("Reasignar la misma sección", lambda m: m.assign_section(s.ab.id, s.ab.section))
    editor.relabel_node(s.a.id, s.a.label)
    assert editor.undo_names == ()
    assert not editor.can_undo
    with pytest.raises(DiagnosticError) as error:
        editor.undo()
    assert key_of(error.value) == "editing.nothing_to_undo"


def test_a_failing_command_restores_the_model_even_if_it_mutated_something() -> None:
    editor = ModelEditor()
    editor.add_node(M(0, "m"), M(0, "m"), M(0, "m"))
    snapshot = editor.model.snapshot()

    def half_done(model: StructuralModel) -> None:
        model.add_node(M(1, "m"), M(0, "m"), M(0, "m"))  # muta...
        raise RuntimeError("falla a medias")  # ...y luego falla

    with pytest.raises(RuntimeError):
        editor.execute("Comando que falla", half_done)
    assert editor.model.snapshot() == snapshot
    assert editor.undo_names == ("Crear nudo",)  # el fallido no es un paso


def test_a_transaction_is_one_step_and_all_or_nothing() -> None:
    editor = ModelEditor()
    base = model_hash(editor.model.snapshot())
    with editor.transaction("Crear dos nudos"):
        editor.add_node(M(0, "m"), M(0, "m"), M(0, "m"))
        editor.add_node(M(2, "m"), M(0, "m"), M(0, "m"))
    assert editor.undo_names == ("Crear dos nudos",)
    assert len(editor.model.nodes) == 2
    editor.undo()
    assert model_hash(editor.model.snapshot()) == base
    editor.redo()
    assert len(editor.model.nodes) == 2

    snapshot = editor.model.snapshot()

    def failing_transaction() -> None:
        with editor.transaction("Falla"):
            editor.add_node(M(9, "m"), M(0, "m"), M(0, "m"))
            raise RuntimeError("falla en medio")

    with pytest.raises(RuntimeError):
        failing_transaction()
    assert editor.model.snapshot() == snapshot
    assert editor.undo_names == ("Crear dos nudos",)


def test_a_failed_inner_command_inside_a_transaction_does_not_poison_it() -> None:
    editor = ModelEditor()
    with editor.transaction("Mixta"):
        a = editor.add_node(M(0, "m"), M(0, "m"), M(0, "m"))
        with pytest.raises(DiagnosticError):
            editor.execute("Mover a un id inexistente", lambda m: m.remove_node(UUID(int=7)))
        editor.add_node(M(1, "m"), M(0, "m"), M(0, "m"))
    assert len(editor.model.nodes) == 2
    assert a.id in editor.model.nodes
    assert editor.undo_names == ("Mixta",)


def test_nested_transactions_join_the_outer_one_and_block_undo() -> None:
    editor = ModelEditor()
    with editor.transaction("Exterior"):
        editor.add_node(M(0, "m"), M(0, "m"), M(0, "m"))
        with editor.transaction("Interior"):
            editor.add_node(M(1, "m"), M(0, "m"), M(0, "m"))
        assert not editor.can_undo
        with pytest.raises(DiagnosticError) as error:
            editor.undo()
        assert key_of(error.value) == "editing.in_transaction"
    assert editor.undo_names == ("Exterior",)


def test_history_is_bounded_and_drops_the_oldest_step() -> None:
    editor = ModelEditor(limit=3)
    for i in range(5):
        editor.add_node(M(i, "m"), M(0, "m"), M(0, "m"), label=f"N{i}")
    assert len(editor.undo_names) == 3
    for _ in range(3):
        editor.undo()
    assert not editor.can_undo
    assert sorted(n.label for n in editor.model.nodes.values()) == ["N0", "N1"]
    for bad in (0, -1, True, 1.5):
        with pytest.raises(DiagnosticError):
            ModelEditor(limit=bad)  # type: ignore[arg-type]


def test_command_names_are_validated() -> None:
    editor = ModelEditor()
    for bad in ("", "   ", None):
        with pytest.raises(DiagnosticError):
            editor.execute(bad, lambda m: m.model_revision)  # type: ignore[arg-type]
        with pytest.raises(DiagnosticError), editor.transaction(bad):  # type: ignore[arg-type]
            pass


# --- Sesión larga y persistencia (criterios de salida) -----------------------------------


def long_session(editor: ModelEditor) -> None:
    """20 operaciones mezcladas sobre un pórtico: crear, mover, borrar y reasignar."""
    frame = Frame()
    editor.execute(
        "Material y sección",
        lambda m: (m.register_material(frame.material), m.register_section(frame.section)),
    )
    nodes = [
        editor.add_node(M(float(i), "m"), M(0, "m"), M(0, "m"), label=f"N{i}") for i in range(5)
    ]

    def create_member(i: int) -> Member:
        return editor.execute(
            "Crear barra",
            lambda m: m.add_member(
                nodes[i].id,
                nodes[i + 1].id,
                material=frame.material.ref,
                section=frame.section.ref,
                label=f"B{i}",
            ),
        )

    members = [create_member(i) for i in range(4)]
    editor.execute("Apoyo", lambda m: m.set_support(nodes[0].id, fixed_restraints()))
    case = editor.execute("Caso", lambda m: m.add_load_case("Q", frame.case.category))
    editor.execute("Carga nodal", lambda m: m.add_nodal_load(case.id, nodes[4].id, fz=M(-100, "N")))
    editor.execute(
        "Carga de barra",
        lambda m: m.add_member_uniform_load(case.id, members[1].id, qz=M(-50, "N/m")),
    )
    editor.move_node(nodes[2].id, M(2.5, "m"), M(0, "m"), M(0, "m"))
    editor.relabel_node(nodes[3].id, "Cumbrera")
    editor.remove_member_with_dependents(members[1].id)
    editor.remove_member(members[2].id)
    editor.execute("Quitar apoyo", lambda m: m.remove_support(nodes[0].id))
    editor.execute("Reasignar", lambda m: m.assign_section(members[0].id, members[0].section))
    editor.add_node(M(9, "m"), M(9, "m"), M(9, "m"), label="Suelto")


def test_a_session_of_mixed_operations_undoes_to_the_initial_state_and_redoes_to_the_end(
    tmp_path: Path,
) -> None:
    editor = ModelEditor()
    initial = model_hash(editor.model.snapshot())
    long_session(editor)
    steps = len(editor.undo_names)
    assert steps >= 15
    final = model_hash(editor.model.snapshot())
    final_snapshot = editor.model.snapshot()
    assert final != initial
    for _ in range(steps):
        editor.undo()
    assert model_hash(editor.model.snapshot()) == initial
    assert editor.model.snapshot().nodes == ()
    for _ in range(steps):
        editor.redo()
    assert model_hash(editor.model.snapshot()) == final
    assert editor.model.snapshot() == final_snapshot


def test_edit_undo_save_and_reopen_gives_the_same_entities_and_hash(tmp_path: Path) -> None:
    editor = ModelEditor()
    long_session(editor)
    editor.undo()
    editor.undo()
    editor.redo()
    snapshot = editor.model.snapshot()
    path = tmp_path / "p.json"
    saved = save_project(path, editor.model)
    opened = open_project(path)
    assert opened.model_hash == saved.model_hash == model_hash(snapshot)
    assert opened.model.snapshot().nodes == snapshot.nodes
    assert opened.model.snapshot().members == snapshot.members
    assert opened.model.snapshot().supports == snapshot.supports
    assert opened.model.snapshot().loads == snapshot.loads
    # Se puede seguir editando el modelo abierto con un editor nuevo.
    reopened = ModelEditor(opened.model)
    reopened.add_node(M(1, "m"), M(1, "m"), M(1, "m"))
    assert reopened.can_undo
    reopened.undo()
    assert model_hash(reopened.model.snapshot()) == saved.model_hash


def test_a_member_between_coincident_nodes_is_rejected_with_an_explanation() -> None:
    """T-GEO-001 del SRS: barra entre nudos coincidentes ⇒ rechazo explicativo, sin paso."""
    frame = Frame()
    editor = ModelEditor(frame.model)
    a = editor.add_node(M(0, "m"), M(0, "m"), M(0, "m"))
    b = editor.add_node(M(0, "m"), M(0, "m"), M(0, "m"))
    count = len(editor.undo_names)
    with pytest.raises(DiagnosticError):
        editor.execute(
            "Crear barra",
            lambda m: m.add_member(
                a.id, b.id, material=frame.material.ref, section=frame.section.ref
            ),
        )
    assert len(editor.undo_names) == count
    assert not editor.model.members


# --- Identidad del modelo, no reentrancia y atomicidad fina (revisión de arquitectura) ----


def test_the_model_object_is_always_the_same_so_references_stay_valid(tmp_path: Path) -> None:
    editor = ModelEditor(scene().model)
    model = editor.model
    editor.add_node(M(1, "m"), M(1, "m"), M(1, "m"))
    with pytest.raises(DiagnosticError):
        editor.remove_node(UUID(int=9))  # falla: el modelo se restaura en el sitio
    assert editor.model is model
    editor.undo()
    assert editor.model is model
    editor.redo()
    assert editor.model is model
    # Un Project abierto sigue vigente: guardar project.model incluye el último nodo.
    path = tmp_path / "p.json"
    save_project(path, model)
    project = open_project(path)
    bound = ModelEditor(project.model)
    bound.add_node(M(2, "m"), M(2, "m"), M(2, "m"), label="NUEVO")
    with pytest.raises(DiagnosticError):
        bound.remove_member(UUID(int=11))
    bound.add_node(M(3, "m"), M(3, "m"), M(3, "m"), label="OTRO")
    save_project(tmp_path / "q.json", project.model)
    labels = {n.label for n in open_project(tmp_path / "q.json").model.nodes.values()}
    assert {"NUEVO", "OTRO"} <= labels


def test_the_editor_is_not_reentrant() -> None:
    editor = ModelEditor()
    before = editor.model.snapshot()

    def inner_call(model: StructuralModel) -> None:
        editor.add_node(M(0, "m"), M(0, "m"), M(0, "m"))

    with pytest.raises(DiagnosticError) as error:
        editor.execute("Anidado", inner_call)
    assert key_of(error.value) == "editing.reentrant"
    assert editor.model.snapshot() == before
    assert editor.undo_names == ()
    for call in (editor.undo, editor.redo):

        def via(model: StructuralModel, call: Callable[[], str] = call) -> None:
            call()

        with pytest.raises(DiagnosticError) as inside:
            editor.execute("Deshacer dentro", via)
        assert key_of(inside.value) == "editing.reentrant"


def test_a_failed_inner_transaction_restores_its_own_savepoint_even_if_caught() -> None:
    editor = ModelEditor()
    with editor.transaction("Exterior"):
        editor.add_node(M(0, "m"), M(0, "m"), M(0, "m"), label="A")
        try:
            with editor.transaction("Interior"):
                editor.add_node(M(1, "m"), M(0, "m"), M(0, "m"), label="B")
                raise ValueError("falla la interior")
        except ValueError:
            pass
        editor.add_node(M(2, "m"), M(0, "m"), M(0, "m"), label="C")
    assert sorted(n.label for n in editor.model.nodes.values()) == ["A", "C"]  # sin B
    assert editor.undo_names == ("Exterior",)


def test_a_failing_restore_keeps_the_step_on_its_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    editor = ModelEditor()
    editor.add_node(M(0, "m"), M(0, "m"), M(0, "m"))
    real = editor.model.replace_content

    def broken(snapshot: object) -> None:
        raise MemoryError("sin memoria")

    monkeypatch.setattr(editor.model, "replace_content", broken)
    with pytest.raises(MemoryError):
        editor.undo()
    assert editor.undo_names == ("Crear nudo",)  # el paso no se perdió
    monkeypatch.setattr(editor.model, "replace_content", real)
    assert editor.undo() == "Crear nudo"
    assert editor.redo_names == ("Crear nudo",)
    monkeypatch.setattr(editor.model, "replace_content", broken)
    with pytest.raises(MemoryError):
        editor.redo()
    assert editor.redo_names == ("Crear nudo",)


def test_every_member_load_type_is_known_to_the_dependents_check() -> None:
    """Si se añade un tipo de carga de barra y no a ``_MEMBER_LOAD_TYPES``, ``remove_member`` no
    lo vería y ``restore`` rechazaría el resultado (revisión de arquitectura H6)."""
    with_member = {
        t for t in get_args(Load.__value__) if "member_id" in {f.name for f in fields(t)}
    }
    assert with_member == set(_MEMBER_LOAD_TYPES)
