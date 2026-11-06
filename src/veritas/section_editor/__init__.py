"""Editor de perfiles: borradores paramétricos con validación geométrica (T-025 E1).

``SectionDraft`` (EDT-004, EDT-012) y los constructores por forma. Importa solo ``veritas.core``.
Comparación con catálogo y CONTRASTADO (E2). F3B (T-029 E2): ``SketchDraft`` (croquis libre) y
las plantillas U, L y T.
"""

from veritas.section_editor.builders import (
    chs_draft,
    draft_from_values,
    i_draft,
    rhs_draft,
    shs_draft,
)
from veritas.section_editor.draft import SectionDraft
from veritas.section_editor.sketch import SketchContour, SketchDraft
from veritas.section_editor.templates import TEMPLATE_DIMENSIONS, template_draft

__all__ = [
    "TEMPLATE_DIMENSIONS",
    "SectionDraft",
    "SketchContour",
    "SketchDraft",
    "chs_draft",
    "draft_from_values",
    "i_draft",
    "rhs_draft",
    "shs_draft",
    "template_draft",
]
