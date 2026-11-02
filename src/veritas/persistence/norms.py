"""Normativas en el archivo de proyecto: esquema v3 (plan T-014, E2; D6 y D7; ADR-007).

``content.norms = {user, selected}``:

- ``user``: TODAS las revisiones de las configuraciones de usuario (nunca las protegidas, que
  viven en el código y se referencian por (id, revisión)).
- ``selected``: la revisión exacta elegida para el proyecto, o ``null``.

Las normativas no son parte del modelo analítico: no entran en el hash ``veritas-canon/1`` ni
caducan ejecuciones. La validación estricta al abrir reutiliza el codec genérico y
``NormRegistry.restore`` (todo o nada).
"""

from dataclasses import dataclass
from typing import Final

from veritas.core.references import RevisionRef
from veritas.design_codes.norms import NormConfiguration
from veritas.design_codes.registry import NormRegistry
from veritas.persistence.codec import decode, encode
from veritas.persistence.schema import Json, Reader

_FIELDS: Final = ("user", "selected")


@dataclass(frozen=True, slots=True)
class NormsState:
    """Normativas de usuario (todas las revisiones) y revisión seleccionada del proyecto."""

    user: tuple[NormConfiguration, ...] = ()
    selected: RevisionRef | None = None

    def registry(self) -> NormRegistry:
        """Registro validado con las protegidas del código y estas normativas (todo o nada)."""
        return NormRegistry.restore(self.user, self.selected)

    @classmethod
    def of(cls, registry: NormRegistry) -> "NormsState":
        """Estado a guardar de un registro."""
        return cls(registry.user_revisions(), registry.selected)


NO_NORMS: Final = NormsState()
"""Sin normativas de usuario ni selección (valor por defecto)."""


def encode_norms(state: NormsState) -> Json:
    """Estructura JSON de las normativas (valida antes de escribir)."""
    state.registry()
    return {
        "user": [encode(config, "user") for config in state.user],
        "selected": None if state.selected is None else encode(state.selected, "selected"),
    }


def decode_norms(data: object, path: str) -> NormsState:
    """Normativas validadas de forma estricta; ``DiagnosticError`` si algo no encaja."""
    reader = Reader(data, path, _FIELDS)
    user = tuple(
        decode(NormConfiguration, item, f"{reader.at('user')}[{i}]")
        for i, item in enumerate(reader.items("user"))
    )
    raw = reader.raw("selected")
    selected = None if raw is None else decode(RevisionRef, raw, reader.at("selected"))
    state = NormsState(user, selected)
    state.registry()
    return state
