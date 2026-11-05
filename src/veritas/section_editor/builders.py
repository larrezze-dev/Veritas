"""Constructores de borradores por forma y unidades (EDT-001, EDT-012; plan T-025 E1, D5/D11).

Cada constructor recibe las medidas como números en una unidad de longitud y devuelve un
``SectionDraft``: no valida la geometría (eso lo hace el borrador, con todos sus diagnósticos) ni
crea la revisión. Los radios opcionales (``r`` en la I, ``ro`` en RHS/SHS) valen 0 si se omiten.
Los nombres de dimensión son los de ``core.sections.parametric``.
"""

from collections.abc import Mapping

from veritas.core.sections.parametric import ParametricShape
from veritas.core.units import Quantity, get_unit
from veritas.section_editor.draft import SectionDraft


def draft_from_values(
    shape: ParametricShape, values: Mapping[str, float], unit: str = "m"
) -> SectionDraft:
    """Borrador desde medidas numéricas por nombre, todas en ``unit`` (unidad de longitud)."""
    length_unit = get_unit(unit)
    return SectionDraft.of(
        ParametricShape(shape), {n: Quantity.of(v, length_unit) for n, v in values.items()}
    )


def i_draft(  # noqa: PLR0913 (las cinco medidas de la I, por nombre)
    *, h: float, b: float, tw: float, tf: float, r: float | None = None, unit: str = "m"
) -> SectionDraft:
    """Perfil I: canto ``h``, ala ``b``, alma ``tw``, ala ``tf`` y acuerdo ``r`` opcional."""
    values = {"h": h, "b": b, "tw": tw, "tf": tf}
    if r is not None:
        values["r"] = r
    return draft_from_values(ParametricShape.I, values, unit)


def rhs_draft(
    *, h: float, b: float, t: float, ro: float | None = None, unit: str = "m"
) -> SectionDraft:
    """Tubo rectangular: ``h``, ``b``, espesor ``t`` y radio exterior ``ro`` opcional."""
    values = {"h": h, "b": b, "t": t}
    if ro is not None:
        values["ro"] = ro
    return draft_from_values(ParametricShape.RHS, values, unit)


def shs_draft(*, b: float, t: float, ro: float | None = None, unit: str = "m") -> SectionDraft:
    """Tubo cuadrado: un solo lado ``b`` (h = b), espesor ``t`` y radio exterior ``ro``."""
    values = {"b": b, "t": t}
    if ro is not None:
        values["ro"] = ro
    return draft_from_values(ParametricShape.SHS, values, unit)


def chs_draft(*, d: float, t: float, unit: str = "m") -> SectionDraft:
    """Tubo circular: diámetro exterior ``d`` y espesor ``t``."""
    return draft_from_values(ParametricShape.CHS, {"d": d, "t": t}, unit)
