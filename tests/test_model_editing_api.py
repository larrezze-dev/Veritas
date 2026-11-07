"""T-030 E2a (R2-D2…D5): avisos y clave de cambio, comandos de barra, vista previa de cascada y
regla forma/material en el editor. Todo en el dominio, sin Qt."""

import random
from uuid import UUID

import pytest

from fem_support import Frame
from veritas.core import units as u
from veritas.core.diagnostics import DiagnosticError
from veritas.core.geometry.editing import CascadePreview, ModelEditor
from veritas.core.geometry.model import StructuralModel
from veritas.core.materials.material import MaterialFamily
from veritas.core.sections.parametric import ParametricShape
from veritas.core.supports.support import fixed_restraints, pinned_restraints
from veritas.persistence.canonical import model_hash

M = u.Quantity.of


def digest(model: StructuralModel) -> str:
    return model_hash(model.snapshot())


def key_of(error: DiagnosticError) -> str:
    return error.diagnostic.message_key


class Rig:
    """Pórtico de 3 nudos y 2 barras con todas las dependencias, dentro de un editor, más una
    segunda sección (rectangular) y un material de madera para probar asignaciones."""

    def __init__(self) -> None:
        self.frame = Frame()
        f = self.frame
        self.a, self.b, self.c = f.node(0.0, 0.0), f.node(3.0, 0.0), f.node(6.0, 0.0)
        self.ab, self.bc = f.member(self.a, self.b), f.member(self.b, self.c)
        f.support(self.a, fixed_restraints())
        f.support(self.c, pinned_restraints())
        model = f.model
        model.add_member_uniform_load(f.case_id, self.ab.id, qz=M(-1000.0, "N/m"))
        model.add_nodal_load(f.case_id, self.b.id, fz=M(-500.0, "N"))
        model.add_support_settlement(f.case_id, self.a.id, uz=M(-0.01, "m"))
        model.set_self_weight(f.case_id, excluded_members=[self.bc.id])
        self.section2 = model.add_rectangular_section(b=M(0.1, "m"), h=M(0.3, "m"))
        self.timber = model.add_material(
            name="Madera",
            family=MaterialFamily.TIMBER,
            source="prueba",
            elastic_modulus=M(11e9, "Pa"),
            shear_modulus=M(0.69e9, "Pa"),
            density=M(420, "kg/m³"),
        )
        self.chs = model.add_parametric_section(
            ParametricShape.CHS, {"d": M(0.1, "m"), "t": M(0.005, "m")}
        )
        self.editor = ModelEditor(model)
        self.calls = 0
        self.editor.subscribe(self._tick)

    def _tick(self) -> None:
        self.calls += 1

    @property
    def model(self) -> StructuralModel:
        return self.editor.model


# --- R2-D2: generación y avisos ----------------------------------------------------------


def test_generation_rises_once_per_command_undo_and_redo_with_one_notice_each() -> None:
    r = Rig()
    assert (r.editor.generation, r.calls) == (0, 0)
    r.editor.relabel_node(r.a.id, "A1")
    assert (r.editor.generation, r.calls) == (1, 1)
    r.editor.undo()
    assert (r.editor.generation, r.calls) == (2, 2)
    r.editor.redo()
    assert (r.editor.generation, r.calls) == (3, 3)


def test_generation_keeps_rising_when_undo_then_a_new_edit_repeats_the_revision() -> None:
    r = Rig()
    seen = [r.editor.generation]
    r.editor.relabel_node(r.a.id, "X")
    seen.append(r.editor.generation)
    r.editor.undo()
    seen.append(r.editor.generation)
    r.editor.relabel_node(r.a.id, "Y")  # misma revisión de nudo que antes, otro contenido
    seen.append(r.editor.generation)
    assert seen == sorted(set(seen))


def test_a_noop_command_neither_notifies_nor_bumps_the_generation() -> None:
    r = Rig()
    r.editor.relabel_node(r.a.id, r.a.label)
    assert (r.editor.generation, r.calls) == (0, 0)


def test_a_transaction_emits_a_single_notice_when_it_closes() -> None:
    r = Rig()
    with r.editor.transaction("Tres cambios"):
        r.editor.relabel_node(r.a.id, "A")
        r.editor.relabel_node(r.b.id, "B")
        assert r.calls == 0
        with r.editor.transaction("Anidada"):
            r.editor.relabel_node(r.c.id, "C")
        assert r.calls == 0
    assert (r.editor.generation, r.calls) == (1, 1)


def test_a_failed_command_restores_and_notifies_once_with_the_original_error() -> None:
    r = Rig()
    before = digest(r.model)
    with pytest.raises(DiagnosticError):
        r.editor.remove_member(r.ab.id)  # tiene carga de barra
    assert digest(r.model) == before
    assert (r.editor.generation, r.calls) == (1, 1)


def test_a_failed_transaction_notifies_once_and_a_failed_inner_one_does_not() -> None:
    r = Rig()

    def failing() -> None:
        with r.editor.transaction("Falla"):
            r.editor.relabel_node(r.a.id, "A")
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        failing()
    assert (r.editor.generation, r.calls) == (1, 1)
    with r.editor.transaction("Externa"):
        with pytest.raises(DiagnosticError), r.editor.transaction("Interna"):
            r.editor.remove_member(r.ab.id)
        assert r.calls == 1
    assert r.calls == 1  # la externa no cambió nada neto


def test_notices_never_run_inside_an_action_and_cannot_mutate_the_editor() -> None:
    r = Rig()
    inside: list[bool] = []
    mutation: list[str] = []

    def during_action(model: StructuralModel) -> None:
        inside.append(r.calls > 0)
        model.relabel_node(r.a.id, "Z")

    r.editor.execute("Acción", during_action)
    assert inside == [False]
    assert r.calls == 1

    def hostile() -> None:
        try:
            r.editor.relabel_node(r.b.id, "no")
        except DiagnosticError as error:
            mutation.append(key_of(error))
        assert not r.editor.can_undo  # no se puede deshacer desde un aviso

    r.editor.subscribe(hostile)
    r.editor.relabel_node(r.c.id, "C")
    assert mutation == ["editing.reentrant"]
    assert r.model.node(r.b.id).label != "no"
    assert r.editor.can_undo


def test_every_subscriber_is_notified_and_the_first_error_propagates_after_commit() -> None:
    r = Rig()
    log: list[str] = []

    def bad() -> None:
        log.append("bad")
        raise ValueError("aviso")

    r.editor.subscribe(bad)
    r.editor.subscribe(lambda: log.append("good"))
    with pytest.raises(ValueError, match="aviso"):
        r.editor.relabel_node(r.a.id, "A")
    assert log == ["bad", "good"]
    assert r.model.node(r.a.id).label == "A"  # la operación no se revierte
    assert r.editor.can_undo


def test_unsubscribe_and_invalid_callbacks() -> None:
    r = Rig()
    r.editor.unsubscribe(r._tick)
    r.editor.relabel_node(r.a.id, "A")
    assert r.calls == 0
    with pytest.raises(DiagnosticError) as error:
        r.editor.unsubscribe(r._tick)
    assert key_of(error.value) == "editing.not_subscribed"
    with pytest.raises(DiagnosticError) as error:
        r.editor.subscribe(3)  # type: ignore[arg-type]
    assert key_of(error.value) == "editing.invalid_callback"
    r.editor.subscribe(r._tick)
    r.editor.subscribe(r._tick)  # idempotente
    r.editor.relabel_node(r.a.id, "B")
    assert r.calls == 1


# --- R2-D3: comandos con nombre de barra -------------------------------------------------


def test_add_member_orient_and_assign_commands_undo_and_redo_with_identical_hash() -> None:
    r = Rig()
    f = r.frame
    d = r.editor.add_node(M(6.0, "m"), M(0.0, "m"), M(3.0, "m"))
    start = digest(r.model)
    member = r.editor.add_member(
        r.c.id, d.id, material=f.material.ref, section=r.section2.ref, label="CD"
    )
    r.editor.set_member_orientation(member.id, alpha=M(0.5, "rad"))
    r.editor.assign_section([member.id, r.ab.id], f.section.ref)
    r.editor.assign_material(member.id, f.material.ref)  # sin cambio
    after = digest(r.model)
    assert r.editor.undo_names[1:] == ("Crear barra", "Orientar barra", "Asignar sección")
    for _ in range(3):
        r.editor.undo()
    assert member.id not in r.model.members
    assert digest(r.model) == start
    for _ in range(3):
        r.editor.redo()
    assert digest(r.model) == after
    assert after != start


def test_assign_section_to_several_members_is_one_step_and_atomic() -> None:
    r = Rig()
    f = r.frame
    r.editor.assign_section((r.ab.id, r.bc.id, r.ab.id), r.section2.ref)  # id repetido
    assert r.editor.undo_names == ("Asignar sección",)
    assert {m.section for m in r.model.members.values()} == {r.section2.ref}
    before = digest(r.model)
    with pytest.raises(DiagnosticError):  # una barra existe y otra no: nada cambia
        r.editor.assign_section((r.ab.id, UUID(int=1)), f.section.ref)
    assert digest(r.model) == before
    with pytest.raises(DiagnosticError) as error:
        r.editor.assign_section([], f.section.ref)
    assert key_of(error.value) == "model.empty_selection"


# --- R2-D5: regla forma/material ---------------------------------------------------------


def test_metal_shape_in_timber_is_rejected_by_every_editor_command_without_a_step() -> None:
    r = Rig()
    f = r.frame
    # Una barra de madera con sección rectangular es válida.
    timber_bar = r.editor.add_member(
        r.a.id, r.c.id, material=r.timber.ref, section=f.section.ref, label="M"
    )
    steps = r.editor.undo_names
    before, generation = digest(r.model), r.editor.generation
    with pytest.raises(DiagnosticError) as error:
        r.editor.assign_section(timber_bar.id, r.chs.ref)  # CHS metálica en madera
    assert key_of(error.value) == "section.replace_incompatible_material"
    with pytest.raises(DiagnosticError) as error:
        r.editor.add_member(r.a.id, r.b.id, material=r.timber.ref, section=r.chs.ref)
    assert key_of(error.value) == "section.replace_incompatible_material"
    r.editor.assign_section(r.ab.id, r.chs.ref)  # CHS en acero: bien
    with pytest.raises(DiagnosticError) as error:
        r.editor.assign_material([r.bc.id, r.ab.id], r.timber.ref)  # ab (CHS) rechaza todo
    assert key_of(error.value) == "section.replace_incompatible_material"
    assert r.model.member(r.bc.id).material == f.material.ref  # atómico
    assert r.editor.undo_names == (*steps, "Asignar sección")
    assert digest(r.model) != before  # el único cambio es la asignación válida
    assert r.editor.generation > generation


# --- R2-D4: vista previa == borrado real -------------------------------------------------


def clear_exclusions(r: Rig) -> None:
    r.editor.execute("Sin exclusiones", lambda m: m.set_self_weight(r.frame.case_id))


def _removed(before: StructuralModel, after: StructuralModel) -> dict[str, set[UUID]]:
    def ids(model: StructuralModel) -> dict[str, set[UUID]]:
        return {
            "members": set(model.members),
            "loads": {
                load.id for load in model.loads.values() if load.id != _self_weight_id(model)
            },
            "supports": set(model.supports),
        }

    b, a = ids(before), ids(after)
    return {k: b[k] - a[k] for k in b}


def _self_weight_id(model: StructuralModel) -> UUID | None:
    return None if model.self_weight is None else model.self_weight.id


def test_preview_of_node_removal_matches_the_real_cascade_and_does_not_mutate() -> None:
    r = Rig()
    before_hash, generation = digest(r.model), r.editor.generation
    preview = r.editor.preview_remove_node(r.b.id)
    assert isinstance(preview, CascadePreview)
    assert digest(r.model) == before_hash
    assert r.editor.generation == generation
    assert r.calls == 0
    assert preview.ok
    assert set(preview.members) == {r.ab.id, r.bc.id}
    assert len(preview.member_loads) == 1
    assert len(preview.nodal_loads) == 1
    assert preview.support is None
    assert preview.settlements == ()
    assert preview.cleaned_exclusions == (r.bc.id,)
    snapshot = StructuralModel.restore(r.model.snapshot())
    r.editor.remove_node_with_dependents(r.b.id)
    removed = _removed(snapshot, r.model)
    assert removed["members"] == set(preview.members)
    assert removed["loads"] == set(preview.member_loads) | set(preview.nodal_loads)
    assert r.model.self_weight is not None
    assert r.model.self_weight.excluded_members == frozenset()


def test_preview_of_a_supported_node_lists_support_and_settlements() -> None:
    r = Rig()
    r.editor.execute("Sin exclusiones", lambda m: m.set_self_weight(r.frame.case_id))
    preview = r.editor.preview_remove_node(r.a.id)
    assert preview.support is not None
    assert len(preview.settlements) == 1
    assert preview.members == (r.ab.id,)
    assert len(preview.member_loads) == 1
    snapshot = StructuralModel.restore(r.model.snapshot())
    r.editor.remove_node_with_dependents(r.a.id)
    removed = _removed(snapshot, r.model)
    assert removed["supports"] == {preview.support}
    assert removed["loads"] == set(preview.member_loads) | set(preview.settlements)


def test_preview_reports_the_error_of_an_invalid_final_state_and_matches_the_rejection() -> None:
    r = Rig()
    # Quitar la barra no excluida (ab) dejaría solo bc, excluida del peso propio.
    preview = r.editor.preview_remove_members([r.ab.id])
    assert not preview.ok
    assert preview.error is not None
    assert preview.error.message_key == "model.remove_member_all_excluded"
    before = digest(r.model)
    with pytest.raises(DiagnosticError) as error:
        r.editor.remove_members([r.ab.id])
    assert key_of(error.value) == preview.error.message_key
    assert digest(r.model) == before
    # Quitar las dos es válido y la vista previa lo sabe.
    both = r.editor.preview_remove_members([r.bc.id, r.ab.id])
    assert both.ok
    assert both.members == (r.bc.id, r.ab.id)
    r.editor.remove_members([r.bc.id, r.ab.id])
    assert not r.model.members


def test_preview_errors_for_unknown_ids() -> None:
    r = Rig()
    with pytest.raises(DiagnosticError):
        r.editor.preview_remove_node(UUID(int=7))
    with pytest.raises(DiagnosticError):
        r.editor.preview_remove_members([UUID(int=7)])


# --- Criterio de salida 1: sesión larga --------------------------------------------------


def test_a_session_of_30_operations_undoes_and_redoes_with_identical_hash_and_notices() -> None:
    r = Rig()
    f = r.frame
    rng = random.Random(30)
    start = digest(r.model)
    nodes = [r.a.id, r.b.id, r.c.id]
    members = [r.ab.id, r.bc.id]
    for index in range(30):
        kind = index % 5
        if kind == 0:
            nodes.append(
                r.editor.add_node(
                    M(rng.random() * 10, "m"), M(0.0, "m"), M(rng.random() * 10, "m")
                ).id
            )
        elif kind == 1:
            i, j = rng.sample(nodes, 2)
            members.append(
                r.editor.add_member(i, j, material=f.material.ref, section=f.section.ref).id
            )
        elif kind == 2:
            r.editor.set_member_orientation(rng.choice(members), alpha=M(index / 10, "rad"))
        elif kind == 3:
            r.editor.assign_section(
                rng.sample(members, 2), r.section2.ref if index % 2 else f.section.ref
            )
        else:
            r.editor.relabel_node(rng.choice(nodes), f"N{index}")
    final = digest(r.model)
    assert r.editor.generation == r.calls
    before_undo = r.calls
    undone = 0
    while r.editor.can_undo:
        r.editor.undo()
        undone += 1
    assert r.calls - before_undo == undone
    assert digest(r.model) == start
    while r.editor.can_redo:
        r.editor.redo()
    assert digest(r.model) == final
    assert r.calls - before_undo == 2 * undone


# --- Revisión de arquitectura de E2a -----------------------------------------------------


def test_preview_lists_equal_the_real_removal_for_every_single_member_and_node() -> None:
    for kind in ("member", "node"):
        for target in ("a", "b", "c", "ab", "bc"):
            r = Rig()
            clear_exclusions(r)
            ids = {"a": r.a.id, "b": r.b.id, "c": r.c.id, "ab": r.ab.id, "bc": r.bc.id}
            if (kind == "member") != (target in ("ab", "bc")):
                continue
            before = StructuralModel.restore(r.model.snapshot())
            if kind == "member":
                preview = r.editor.preview_remove_members([ids[target]])
                r.editor.remove_member_with_dependents(ids[target])
            else:
                preview = r.editor.preview_remove_node(ids[target])
                r.editor.remove_node_with_dependents(ids[target])
            removed = _removed(before, r.model)
            assert preview.ok
            assert removed["members"] == set(preview.members)
            assert removed["loads"] == (
                set(preview.member_loads) | set(preview.nodal_loads) | set(preview.settlements)
            )
            assert removed["supports"] == ({preview.support} if preview.support else set())


def test_preview_cleaned_exclusions_and_member_loads_match_a_member_removal() -> None:
    r = Rig()
    preview = r.editor.preview_remove_members([r.bc.id, r.ab.id])
    assert preview.cleaned_exclusions == (r.bc.id,)
    assert len(preview.member_loads) == 1
    r.editor.remove_members([r.bc.id, r.ab.id])
    assert r.model.self_weight is not None
    assert r.model.self_weight.excluded_members == frozenset()


def test_a_failing_subscriber_during_a_failed_command_keeps_the_original_error() -> None:
    r = Rig()

    def bad() -> None:
        raise ValueError("aviso")

    r.editor.subscribe(bad)
    with pytest.raises(DiagnosticError) as error:
        r.editor.remove_member(r.ab.id)
    assert key_of(error.value) == "model.remove_member_dependents"


def test_undo_redo_and_transaction_from_a_subscriber_are_rejected() -> None:
    r = Rig()
    keys: list[str] = []

    def hostile() -> None:
        def open_transaction() -> None:
            with r.editor.transaction("x"):
                pass

        for call in (r.editor.undo, r.editor.redo, open_transaction):
            try:
                call()
            except DiagnosticError as error:
                keys.append(key_of(error))

    r.editor.subscribe(hostile)
    r.editor.relabel_node(r.a.id, "A")
    assert keys == ["editing.reentrant"] * 3
    assert r.editor.undo_names == ("Renombrar nudo",)


def test_failing_subscriber_on_undo_leaves_consistent_stacks() -> None:
    r = Rig()
    r.editor.relabel_node(r.a.id, "A")

    def bad() -> None:
        raise ValueError("aviso")

    r.editor.subscribe(bad)
    with pytest.raises(ValueError, match="aviso"):
        r.editor.undo()
    assert r.editor.redo_names == ("Renombrar nudo",)
    assert r.editor.undo_names == ()
    assert r.model.node(r.a.id).label == ""


def test_a_failed_noop_transaction_still_notifies_once() -> None:
    r = Rig()

    def failing() -> None:
        with r.editor.transaction("Nada"):
            raise RuntimeError("boom")

    with pytest.raises(RuntimeError):
        failing()
    assert (r.editor.generation, r.calls) == (1, 1)
    assert r.editor.undo_names == ()


def test_set_member_orientation_with_reference_and_unknown_node_leave_no_step() -> None:
    r = Rig()
    r.editor.set_member_orientation(r.ab.id, reference=(0.0, 1.0, 0.0))
    assert r.editor.undo_names == ("Orientar barra",)
    steps = r.editor.undo_names
    with pytest.raises(DiagnosticError):
        r.editor.add_member(
            r.a.id, UUID(int=3), material=r.frame.material.ref, section=r.frame.section.ref
        )
    assert r.editor.undo_names == steps
