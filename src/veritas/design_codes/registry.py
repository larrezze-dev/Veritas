"""Registro de configuraciones de normativas: crear, editar, copiar, borrar (ADR-007; D4).

Reglas:

- **Crear:** revisión 1, ``USER``, id nuevo.
- **Editar:** revisión n+1 del mismo id; las revisiones anteriores no se tocan. Un contenido
  idéntico no crea revisión. ``id``, ``kind``, ``system`` y ``derived_from`` no cambian.
- **Copiar:** id nuevo, revisión 1, ``USER``, ``derived_from`` = la revisión exacta de
  origen (informativa). Se pueden copiar las protegidas.
- **Borrar:** retira la entidad con todas sus revisiones; el id no se recicla. Borrar el
  origen de una copia no invalida la copia.
- **Protegidas:** editar o borrar es ``UNSUPPORTED_CONFIGURATION`` con clave
  ``norm.protected``. Borrar la configuración seleccionada del proyecto (``select``) es
  ``UNSUPPORTED_CONFIGURATION`` con clave ``norm.in_use``; el gancho opcional
  ``selected_guard`` añade otras razones de uso.
- **Seleccionar:** una revisión exacta, protegida o de usuario; no afecta al modelo analítico.
- **Restaurar:** ``restore`` reconstruye el registro desde un proyecto, todo o nada.

Toda operación valida primero y modifica después: un rechazo no deja rastro.
"""

from collections.abc import Callable, Iterable
from dataclasses import replace
from uuid import UUID, uuid4

from veritas.core.diagnostics import (
    Diagnostic,
    DiagnosticCode,
    InputValueError,
    Location,
)
from veritas.core.references import RevisionRef, require_ref
from veritas.core.validation import invalid_type
from veritas.design_codes.builtin import protected_configurations
from veritas.design_codes.norms import (
    NormConfiguration,
    NormKind,
    NormParameter,
    NormSystem,
)

_MAX_ID_ATTEMPTS = 1000


def _config_error(message_key: str, params: dict[str, str], entity_id: UUID) -> InputValueError:
    return InputValueError(
        Diagnostic.create(
            DiagnosticCode.UNSUPPORTED_CONFIGURATION,
            message_key,
            params,
            location=Location(entity_id=entity_id),
        )
    )


class NormRegistry:
    """Todas las revisiones de las configuraciones (protegidas y de usuario)."""

    def __init__(
        self,
        *,
        id_factory: Callable[[], UUID] = uuid4,
        selected_guard: Callable[[UUID], bool] | None = None,
    ) -> None:
        self._id_factory = id_factory
        self._selected_guard = selected_guard
        self._selected: RevisionRef | None = None
        self._revisions: dict[UUID, list[NormConfiguration]] = {}
        self._retired: set[UUID] = set()
        for config in protected_configurations():
            self._revisions[config.id] = [config]

    # --- Lectura -------------------------------------------------------------------------

    def __contains__(self, ref: object) -> bool:
        if not isinstance(ref, RevisionRef):
            return False
        return ref.revision <= len(self._revisions.get(ref.entity_id, ()))

    def get(self, ref: RevisionRef, location: Location | None = None) -> NormConfiguration:
        """Revisión exacta o ``REFERENCE_NOT_FOUND``."""
        require_ref(ref, "ref", location)
        if ref not in self:
            raise self._not_found(str(ref), location)
        return self._revisions[ref.entity_id][ref.revision - 1]

    def latest(self, entity_id: UUID) -> NormConfiguration:
        """Última revisión de una configuración o ``REFERENCE_NOT_FOUND``."""
        return self._history(entity_id)[-1]

    def history(self, entity_id: UUID) -> tuple[NormConfiguration, ...]:
        """Todas las revisiones de una configuración, de la 1 a la última."""
        return tuple(self._history(entity_id))

    def all_configurations(self) -> tuple[NormConfiguration, ...]:
        """Última revisión de cada configuración: protegidas primero, luego las de usuario."""
        return tuple(history[-1] for history in self._revisions.values())

    def user_configurations(self) -> tuple[NormConfiguration, ...]:
        """Última revisión de cada configuración de usuario, en orden de creación."""
        return tuple(c for c in self.all_configurations() if c.kind is NormKind.USER)

    def user_revisions(self) -> tuple[NormConfiguration, ...]:
        """Todas las revisiones de las configuraciones de usuario (por id, de la 1 a la última)."""
        return tuple(
            revision
            for history in self._revisions.values()
            for revision in history
            if revision.kind is NormKind.USER
        )

    @property
    def selected(self) -> RevisionRef | None:
        """Revisión exacta seleccionada para el proyecto, o ``None``."""
        return self._selected

    def is_protected(self, entity_id: UUID) -> bool:
        """``True`` si la configuración es protegida (no editable ni borrable)."""
        return self.latest(entity_id).kind is NormKind.PROTECTED

    # --- Operaciones ---------------------------------------------------------------------

    def create(  # noqa: PLR0913 (campos de NormConfiguration, por nombre)
        self,
        *,
        name: str,
        system: NormSystem,
        jurisdiction: str,
        edition: str,
        documents: tuple[str, ...] = (),
        description: str = "",
        parameters: tuple[NormParameter, ...] = (),
    ) -> NormConfiguration:
        """Crea una configuración de usuario en revisión 1."""
        config = NormConfiguration(
            id=self._new_id(),
            revision=1,
            name=name,
            kind=NormKind.USER,
            system=system,
            jurisdiction=jurisdiction,
            edition=edition,
            documents=documents,
            description=description,
            derived_from=None,
            parameters=parameters,
        )
        self._revisions[config.id] = [config]
        return config

    def edit(  # noqa: PLR0913 (campos editables de NormConfiguration, por nombre)
        self,
        entity_id: UUID,
        *,
        name: str | None = None,
        jurisdiction: str | None = None,
        edition: str | None = None,
        documents: tuple[str, ...] | None = None,
        description: str | None = None,
        parameters: tuple[NormParameter, ...] | None = None,
    ) -> NormConfiguration:
        """Revisión n+1 con los campos dados (``None`` = sin cambio); idéntico = sin revisión."""
        history = self._history(entity_id)
        current = history[-1]
        self._reject_protected(current)
        candidate = NormConfiguration(
            id=current.id,
            revision=current.revision + 1,
            name=current.name if name is None else name,
            kind=current.kind,
            system=current.system,
            jurisdiction=current.jurisdiction if jurisdiction is None else jurisdiction,
            edition=current.edition if edition is None else edition,
            documents=current.documents if documents is None else documents,
            description=current.description if description is None else description,
            derived_from=current.derived_from,
            parameters=current.parameters if parameters is None else parameters,
        )
        # Contenido exacto por ``repr``: ``0.0 == -0.0`` no debe ocultar un cambio de signo.
        if repr(replace(candidate, revision=current.revision)) == repr(current):
            return current
        history.append(candidate)
        return candidate

    def copy(self, source: RevisionRef, *, name: str) -> NormConfiguration:
        """Copia de una revisión exacta (también protegida) como configuración de usuario."""
        origin = self.get(require_ref(source, "source"))
        config = NormConfiguration(
            id=self._new_id(),
            revision=1,
            name=name,
            kind=NormKind.USER,
            system=origin.system,
            jurisdiction=origin.jurisdiction,
            edition=origin.edition,
            documents=origin.documents,
            description=origin.description,
            derived_from=origin.ref,
            parameters=origin.parameters,
        )
        self._revisions[config.id] = [config]
        return config

    def select(self, ref: RevisionRef | None) -> None:
        """Selecciona la revisión exacta (protegida o de usuario) o limpia la selección.

        Solo identifica qué normativa usará el proyecto: no entra en el hash del modelo ni
        caduca ejecuciones (ADR-007).
        """
        if ref is not None:
            self.get(ref)
        self._selected = ref

    @classmethod
    def restore(
        cls,
        user_revisions: Iterable[NormConfiguration],
        selected: RevisionRef | None = None,
        *,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> "NormRegistry":
        """Registro con las protegidas del código y las revisiones de usuario dadas.

        Todo o nada. Rechaza una protegida o un id reservado en la lista de usuario
        (``UNSUPPORTED_CONFIGURATION``), revisiones repetidas, con huecos o que no empiezan
        en 1 (``REVISION_CONFLICT``) y una selección de un id desconocido
        (``UNSUPPORTED_CONFIGURATION``: puede ser una protegida de otra versión) o de una
        revisión inexistente (``REFERENCE_NOT_FOUND``).
        """
        registry = cls(id_factory=id_factory)
        reserved = set(registry._revisions)
        for config in user_revisions:
            if not isinstance(config, NormConfiguration):
                raise invalid_type("norms", "NormConfiguration", config)
            params = {"name": config.name, "entity_id": str(config.id)}
            if config.kind is not NormKind.USER:
                raise _config_error("norm.protected_in_project", params, config.id)
            if config.id in reserved:
                raise _config_error("norm.reserved_id", params, config.id)
            history = registry._revisions.setdefault(config.id, [])
            if config.revision != len(history) + 1:
                raise InputValueError(
                    Diagnostic.create(
                        DiagnosticCode.REVISION_CONFLICT,
                        "norm.revision_sequence",
                        {
                            "entity_id": str(config.id),
                            "revision": config.revision,
                            "expected": len(history) + 1,
                        },
                        location=Location(entity_id=config.id, revision=config.revision),
                    )
                )
            history.append(config)
        if selected is not None:
            require_ref(selected, "selected")
            if selected.entity_id not in registry._revisions:
                unknown = {"entity_id": str(selected.entity_id)}
                raise _config_error("norm.selected_unknown", unknown, selected.entity_id)
        registry.select(selected)
        return registry

    def delete(self, entity_id: UUID) -> int:
        """Retira la configuración con todas sus revisiones; devuelve cuántas retiró."""
        history = self._history(entity_id)
        self._reject_protected(history[-1])
        selected_here = self._selected is not None and self._selected.entity_id == entity_id
        if selected_here or (self._selected_guard is not None and self._selected_guard(entity_id)):
            raise _config_error(
                "norm.in_use", {"name": history[-1].name, "entity_id": str(entity_id)}, entity_id
            )
        del self._revisions[entity_id]
        self._retired.add(entity_id)
        return len(history)

    # --- Internos ------------------------------------------------------------------------

    def _history(self, entity_id: object) -> list[NormConfiguration]:
        if not isinstance(entity_id, UUID):
            raise invalid_type("entity_id", "UUID", entity_id)
        try:
            return self._revisions[entity_id]
        except KeyError:
            raise self._not_found(str(entity_id)) from None

    def _new_id(self) -> UUID:
        for _ in range(_MAX_ID_ATTEMPTS):
            candidate = self._id_factory()
            if candidate not in self._revisions and candidate not in self._retired:
                return candidate
        raise RuntimeError("No se pudo obtener un identificador de normativa no usado")

    @staticmethod
    def _reject_protected(config: NormConfiguration) -> None:
        if config.kind is NormKind.PROTECTED:
            raise _config_error(
                "norm.protected", {"name": config.name, "entity_id": str(config.id)}, config.id
            )

    @staticmethod
    def _not_found(entity_id: str, location: Location | None = None) -> InputValueError:
        return InputValueError(
            Diagnostic.create(
                DiagnosticCode.REFERENCE_NOT_FOUND,
                "model.reference_not_found",
                {"kind": "norm", "entity_id": entity_id},
                location=location,
            )
        )
