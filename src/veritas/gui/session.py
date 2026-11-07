"""Sesión de edición sin Qt (plan T-030 E2b-1; R2-D7, R2-D8).

``EditSession`` es lo que la ventana editable posee: el proyecto abierto (perfiles de usuario,
ejecuciones ``runs`` y normativas ``norms`` que se conservan tal cual al guardar), su
``ModelEditor``, la ruta del archivo y el indicador «modificado».

- ``dirty`` compara el ``ModelSnapshot`` vigente (incluidas revisiones) con el del último
  guardado/apertura: deshacer hasta el estado guardado lo deja «limpio»; revertir a mano una
  edición (A→B→A) NO, porque sube la revisión (conservador).
- ``open`` y ``save`` no dejan estado a medias: si fallan, la sesión sigue exactamente como estaba.
- ``subscribe`` avisa tras cada edición, deshacer, rehacer, apertura, guardado o proyecto nuevo (el
  editor se sustituye al abrir; los avisos de la sesión sobreviven). Un aviso que lanza NO propaga
  su excepción al comando ya hecho: queda en ``take_listener_errors``.
- ``attempt`` ejecuta una orden y devuelve ``Attempt`` con el diagnóstico en español si el dominio
  la rechaza; una excepción que no es de dominio se propaga (es un fallo del programa).
"""

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from veritas.core.diagnostics import Diagnostic, DiagnosticCode, DiagnosticError, InputValueError
from veritas.core.geometry.editing import ModelEditor
from veritas.core.geometry.model import StructuralModel
from veritas.core.geometry.snapshot import ModelSnapshot
from veritas.core.messages_es import render_message
from veritas.core.presentation import PresentationProfile
from veritas.persistence.migrations import CURRENT_SCHEMA_VERSION
from veritas.persistence.norms import NO_NORMS, NormsState
from veritas.persistence.project import Project, open_project, save_project
from veritas.persistence.runs import RunRecord


@dataclass(frozen=True, slots=True)
class Attempt[T]:
    """Resultado de una orden: ``value`` si salió bien o ``message`` (español) si se rechazó."""

    ok: bool
    value: T | None = None
    message: str = ""
    diagnostic: Diagnostic | None = None


class EditSession:
    """Proyecto en edición: modelo, editor, ruta, indicador «modificado» y avisos."""

    def __init__(
        self,
        project: Project | None = None,
        path: str | Path | None = None,
        *,
        model: StructuralModel | None = None,
    ) -> None:
        self._listeners: list[Callable[[], None]] = []
        self._listener_errors: list[Exception] = []
        self._editor = ModelEditor(StructuralModel())
        self._profiles: tuple[PresentationProfile, ...] = ()
        self._runs: tuple[RunRecord, ...] = ()
        self._norms: NormsState = NO_NORMS
        self._path: Path | None = None
        self._file_version: int | None = None
        self._saved: ModelSnapshot = self._editor.model.snapshot()
        self._load(project, None if path is None else Path(path), model)

    # --- Estado --------------------------------------------------------------------------

    def _load(
        self, project: Project | None, path: Path | None, model: StructuralModel | None = None
    ) -> None:
        previous = self._editor
        if project is not None:
            model = project.model
        elif model is None:
            model = StructuralModel()
        editor = ModelEditor(model)
        editor.subscribe(self._forward)
        self._editor = editor
        self._profiles = () if project is None else project.user_profiles
        self._runs = () if project is None else project.runs
        self._norms = NO_NORMS if project is None else project.norms
        self._path = path
        # R9: versión declarada por el archivo, ya leída al abrirlo (sin releerlo).
        self._file_version = None if project is None or path is None else project.schema_version
        self._saved = model.snapshot()
        with suppress(DiagnosticError):  # el editor inicial vacío nunca estuvo suscrito
            previous.unsubscribe(self._forward)  # el anterior queda inerte

    @property
    def editor(self) -> ModelEditor:
        return self._editor

    @property
    def model(self) -> StructuralModel:
        return self._editor.model

    @property
    def path(self) -> Path | None:
        return self._path

    @property
    def dirty(self) -> bool:
        """El contenido difiere del último guardado o apertura."""
        return self.model.snapshot() != self._saved

    @property
    def upgrades_on_save(self) -> bool:
        """Guardar reescribiría con el esquema vigente un archivo más antiguo (H10)."""
        return self._file_version is not None and self._file_version < CURRENT_SCHEMA_VERSION

    @property
    def runs(self) -> tuple[RunRecord, ...]:
        return self._runs

    @property
    def norms(self) -> NormsState:
        return self._norms

    @property
    def user_profiles(self) -> tuple[PresentationProfile, ...]:
        return self._profiles

    # --- Avisos --------------------------------------------------------------------------

    def subscribe(self, callback: Callable[[], None]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def unsubscribe(self, callback: Callable[[], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def take_listener_errors(self) -> tuple[Exception, ...]:
        """Excepciones de avisos desde la última consulta (se vacía la lista)."""
        errors, self._listener_errors = tuple(self._listener_errors), []
        return errors

    def _forward(self) -> None:
        for callback in tuple(self._listeners):
            try:
                callback()
            except Exception as exc:
                self._listener_errors.append(exc)

    # --- Órdenes -------------------------------------------------------------------------

    def attempt[T](self, action: Callable[[ModelEditor], T]) -> Attempt[T]:
        """Ejecuta ``action(editor)``; un rechazo de dominio se devuelve como ``Attempt``."""
        try:
            value = action(self._editor)
        except DiagnosticError as error:
            return Attempt(False, None, render_message(error.diagnostic), error.diagnostic)
        return Attempt(True, value)

    def undo(self) -> Attempt[str]:
        return self.attempt(lambda editor: editor.undo())

    def redo(self) -> Attempt[str]:
        return self.attempt(lambda editor: editor.redo())

    # --- Archivo -------------------------------------------------------------------------

    def new(self) -> None:
        """Sustituye la sesión por un proyecto vacío sin archivo."""
        self._load(None, None)
        self._forward()

    def open(self, path: str | Path) -> None:
        """Abre ``path``; si falla (archivo, esquema o integridad), la sesión no cambia."""
        target = Path(path)
        project = open_project(target)
        self._load(project, target)
        self._forward()

    def save(self) -> None:
        """Guarda en la ruta actual; sin ruta, ``session.no_path``."""
        if self._path is None:
            raise InputValueError(
                Diagnostic.create(DiagnosticCode.UNSUPPORTED_CONFIGURATION, "session.no_path", {})
            )
        self._write(self._path)

    def save_as(self, path: str | Path) -> None:
        """Guarda en un archivo nuevo y pasa a ser la ruta de la sesión."""
        self._write(Path(path))

    def _write(self, target: Path) -> None:
        saved = save_project(target, self.model, self._profiles, runs=self._runs, norms=self._norms)
        self._path = target
        self._file_version = CURRENT_SCHEMA_VERSION
        self._saved = self.model.snapshot()
        self._profiles, self._runs, self._norms = saved.user_profiles, saved.runs, saved.norms
        self._forward()
