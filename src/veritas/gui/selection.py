"""Selección del visor (plan T-011 E2; múltiple en T-030 R2-D6).

La lógica vive en ``veritas.core.geometry.selection_logic`` (sin Qt, importable sin el extra
``gui``); este módulo la reexporta. La selección nunca es fuente de verdad: si el modelo cambia,
se vuelve a resolver contra la escena nueva.
"""

from veritas.core.geometry.selection_logic import (
    Selection,
    SelectionKind,
    SelectionSet,
    members_at_node,
    members_by_material,
    members_by_section,
)

__all__ = [
    "Selection",
    "SelectionKind",
    "SelectionSet",
    "members_at_node",
    "members_by_material",
    "members_by_section",
]
