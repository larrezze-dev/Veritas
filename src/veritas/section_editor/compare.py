"""Comparación del editor con un perfil de catálogo (EDT-011; plan T-025 E2, D12-B).

La lógica (informe con diferencia con signo, tolerancia y promoción a CONTRASTADO) vive en
``veritas.core.sections.contrast`` porque ``StructuralModel.register_contrast`` la usa; aquí se
reexporta y se añade el puente de dimensiones de un ``CatalogSnapshot`` a un borrador. Importa
solo ``veritas.core``. Un perfil sin una dimensión del editor no se completa: el borrador resultante
lo dirá en sus diagnósticos (nunca se inventa una medida).
"""

from veritas.core.sections.catalog import CatalogSnapshot
from veritas.core.sections.contrast import (
    COMPARED_PROPERTIES,
    DEFAULT_TOLERANCE,
    ComparisonReport,
    ContrastVerdict,
    PropertyComparison,
    compare_with_snapshot,
    contrasted_properties,
)
from veritas.core.sections.parametric import SHAPE_DIMENSIONS, ParametricShape
from veritas.core.units import LENGTH, Quantity
from veritas.section_editor.draft import SectionDraft


def draft_from_snapshot(snapshot: CatalogSnapshot) -> SectionDraft:
    """Borrador con las dimensiones del perfil de catálogo que el editor reconoce (en m).

    Solo el nombre de dimensión coincidente con la forma paramétrica; el resto se ignora. Para
    formas que el editor no tiene (U, L, T, …) lanza ``ValueError``.
    """
    try:
        shape = ParametricShape(snapshot.shape)
    except ValueError as error:
        raise ValueError(f"el editor no tiene la forma {snapshot.shape}") from error
    expected = SHAPE_DIMENSIONS[shape]
    return SectionDraft.of(
        shape,
        {d.name: Quantity(d.value, LENGTH) for d in snapshot.dimensions if d.name in expected},
    )


__all__ = [
    "COMPARED_PROPERTIES",
    "DEFAULT_TOLERANCE",
    "ComparisonReport",
    "ContrastVerdict",
    "PropertyComparison",
    "compare_with_snapshot",
    "contrasted_properties",
    "draft_from_snapshot",
]
