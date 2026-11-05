"""Editor de perfiles: borradores paramétricos con validación geométrica (T-025 E1).

``SectionDraft`` (EDT-004, EDT-012) y los constructores por forma. Importa solo ``veritas.core``.
Pendiente (E2/F3B): comparación con catálogo, croquis y booleanas.
"""

from veritas.section_editor.builders import (
    chs_draft,
    draft_from_values,
    i_draft,
    rhs_draft,
    shs_draft,
)
from veritas.section_editor.draft import SectionDraft

__all__ = ["SectionDraft", "chs_draft", "draft_from_values", "i_draft", "rhs_draft", "shs_draft"]
