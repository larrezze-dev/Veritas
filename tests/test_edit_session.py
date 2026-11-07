"""T-030 E2b-1 (R2-D7, R2-D8): sesión de edición sin Qt: modificado, abrir/guardar sin estado a
medias, conservación de perfiles, ejecuciones y normativas, y avisos que no deshacen lo hecho."""

from pathlib import Path

import pytest

from gui_support import frame_model
from test_norms_persistence import populated, state_of
from test_persistence_runs import spatial_record, viga
from veritas.core import units as u
from veritas.core.presentation import PROFILE_SI, QuantityKind
from veritas.gui.session import EditSession
from veritas.persistence.canonical import model_hash
from veritas.persistence.codec import encode
from veritas.persistence.project import Project, open_project, save_project
from veritas.persistence.runs import Validity, validity

M = u.Quantity.of


def digest(session: EditSession) -> str:
    return model_hash(session.model.snapshot())


def saved_frame(path: Path, **kwargs: object) -> Project:
    model, _ = frame_model()
    return save_project(path, model, **kwargs)  # type: ignore[arg-type]


def test_new_session_is_empty_clean_and_has_no_path() -> None:
    session = EditSession()
    assert not session.dirty
    assert session.path is None
    assert len(session.model.snapshot().nodes) == 0


def test_dirty_follows_the_content_not_the_history() -> None:
    session = EditSession()
    assert session.attempt(lambda e: e.add_node(M(0, "m"), M(0, "m"), M(0, "m"))).ok
    assert session.dirty
    assert session.undo().ok
    assert not session.dirty  # deshacer hasta el estado guardado deja «limpio»
    assert session.redo().ok
    assert session.dirty


def test_a_rejected_command_is_reported_in_spanish_and_changes_nothing() -> None:
    session = EditSession()
    before = digest(session)
    result = session.attempt(lambda e: e.undo())
    assert not result.ok
    assert "deshacer" in result.message
    assert digest(session) == before
    assert not session.dirty


def test_non_domain_exceptions_are_not_swallowed() -> None:
    def boom(_: object) -> None:
        raise RuntimeError("fallo del programa")

    with pytest.raises(RuntimeError):
        EditSession().attempt(boom)


def test_save_without_path_is_rejected_and_save_as_sets_it(tmp_path: Path) -> None:
    session = EditSession()
    session.attempt(lambda e: e.add_node(M(1, "m"), M(2, "m"), M(3, "m"), label="A"))
    with pytest.raises(Exception, match="Guardar como") as info:
        session.save()
    assert info.value.diagnostic.message_key == "session.no_path"  # type: ignore[attr-defined]
    assert session.dirty
    target = tmp_path / "p.veritas"
    session.save_as(target)
    assert session.path == target
    assert not session.dirty
    assert open_project(target).model_hash == digest(session)


def test_save_reopen_keeps_entities_connectivity_and_hash(tmp_path: Path) -> None:
    path = tmp_path / "p.veritas"
    saved_frame(path)
    session = EditSession(open_project(path), path)
    node = next(iter(session.model.snapshot().nodes))
    assert session.attempt(lambda e: e.move_node(node.id, M(0.5, "m"), M(0, "m"), M(0, "m"))).ok
    assert session.dirty
    expected = digest(session)
    session.save()
    assert not session.dirty
    again = open_project(path)
    assert again.model_hash == expected
    assert encode(again.model.snapshot()) == encode(session.model.snapshot())


def test_a_failed_open_leaves_the_session_untouched(tmp_path: Path) -> None:
    path = tmp_path / "p.veritas"
    saved_frame(path)
    session = EditSession(open_project(path), path)
    session.attempt(lambda e: e.add_node(M(9, "m"), M(9, "m"), M(9, "m")))
    before, editor = digest(session), session.editor
    broken = tmp_path / "roto.veritas"
    broken.write_text("{ no es un proyecto", encoding="utf-8")
    with pytest.raises(Exception):  # noqa: B017, PT011 (cualquier diagnóstico de apertura)
        session.open(broken)
    with pytest.raises(Exception):  # noqa: B017, PT011
        session.open(tmp_path / "no_existe.veritas")
    assert session.editor is editor
    assert session.path == path
    assert digest(session) == before
    assert session.dirty


def test_a_failed_save_leaves_the_session_untouched(tmp_path: Path) -> None:
    session = EditSession()
    session.attempt(lambda e: e.add_node(M(1, "m"), M(1, "m"), M(1, "m")))
    with pytest.raises(Exception):  # noqa: B017, PT011 (directorio inexistente)
        session.save_as(tmp_path / "no" / "hay" / "p.veritas")
    assert session.path is None
    assert session.dirty


def test_open_replaces_the_editor_history_and_notifies(tmp_path: Path) -> None:
    path = tmp_path / "p.veritas"
    saved_frame(path)
    session = EditSession()
    calls: list[int] = []
    session.subscribe(lambda: calls.append(1))
    session.attempt(lambda e: e.add_node(M(1, "m"), M(1, "m"), M(1, "m")))
    assert len(calls) == 1
    session.open(path)
    assert len(calls) == 2
    assert not session.dirty
    assert not session.editor.can_undo  # el historial no cruza archivos
    session.new()
    assert len(calls) == 3
    assert session.path is None
    assert not session.dirty


def test_one_notification_per_step_and_none_from_the_old_editor(tmp_path: Path) -> None:
    path = tmp_path / "p.veritas"
    saved_frame(path)
    session = EditSession()
    old = session.editor
    session.open(path)
    calls: list[int] = []
    session.subscribe(lambda: calls.append(1))
    old.add_node(M(1, "m"), M(1, "m"), M(1, "m"))  # el editor sustituido ya no avisa
    assert calls == []
    with session.editor.transaction("varios"):
        session.editor.add_node(M(2, "m"), M(2, "m"), M(2, "m"))
        session.editor.add_node(M(3, "m"), M(3, "m"), M(3, "m"))
    assert calls == [1]


def test_a_listener_that_raises_does_not_undo_or_fail_the_edit() -> None:
    session = EditSession()
    seen: list[int] = []

    def broken() -> None:
        raise RuntimeError("aviso roto")

    session.subscribe(broken)
    session.subscribe(lambda: seen.append(1))
    result = session.attempt(lambda e: e.add_node(M(1, "m"), M(1, "m"), M(1, "m")))
    assert result.ok  # la orden se hizo; el aviso roto no la convierte en fallo
    assert seen == [1]  # los demás avisos se enteran
    assert session.dirty
    errors = session.take_listener_errors()
    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)
    assert session.take_listener_errors() == ()


def test_norms_and_user_profiles_survive_an_edit_and_save(tmp_path: Path) -> None:
    path = tmp_path / "p.veritas"
    norms = state_of(populated())
    profile = PROFILE_SI.derive("Mi perfil", {QuantityKind.LENGTH: "mm"})
    model, _ = frame_model()
    save_project(path, model, [profile], norms=norms)
    session = EditSession(open_project(path), path)
    session.attempt(lambda e: e.add_node(M(7, "m"), M(0, "m"), M(0, "m")))
    session.save()
    reopened = open_project(path)
    assert repr(reopened.norms) == repr(norms)
    assert [p.name for p in reopened.user_profiles] == ["Mi perfil"]


@pytest.mark.solver
def test_runs_are_kept_byte_for_byte_and_become_stale_after_editing(tmp_path: Path) -> None:
    model, case_id = viga()
    record = spatial_record(model, case_id)
    path = tmp_path / "p.veritas"
    save_project(path, model, runs=[record])
    session = EditSession(open_project(path), path)
    node = next(n for n in session.model.nodes.values() if n.label == "N2")
    session.attempt(lambda e: e.move_node(node.id, M(3, "m"), M(0, "m"), M(0, "m")))
    session.save()
    after = open_project(path)
    assert [r.run_id for r in after.runs] == [record.run_id]
    assert validity(after.runs[0], after.model) is Validity.STALE
    session.undo()  # volver al contenido del archivo sin guardar: el archivo conserva lo guardado
    assert session.dirty


def test_an_old_schema_file_is_flagged_until_it_is_saved_as_current(tmp_path: Path) -> None:
    """H10: guardar sobre un archivo de esquema antiguo avisa; ya guardado, deja de avisar."""
    source = Path(__file__).parent / "data" / "proyecto_v7_ref.json"
    target = tmp_path / "viejo.veritas"
    target.write_bytes(source.read_bytes())
    session = EditSession(open_project(target), target)
    assert session.upgrades_on_save
    session.save()
    assert not session.upgrades_on_save
    reopened = EditSession(open_project(target), target)
    assert not reopened.upgrades_on_save


def test_new_and_current_files_do_not_warn(tmp_path: Path) -> None:
    assert not EditSession().upgrades_on_save
    target = tmp_path / "a.veritas"
    saved_frame(target)
    assert not EditSession(open_project(target), target).upgrades_on_save
