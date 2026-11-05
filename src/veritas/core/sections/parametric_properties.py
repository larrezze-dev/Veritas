"""Propiedades CALCULADO de un perfil paramétrico (T-025 E1, D6/D7).

Centroide e ``Iyz`` son +0.0 por la doble simetría de todas las formas; A, Iy e Iz salen de las
fórmulas cerradas de ``shapes_i`` y ``shapes_hollow``. J solo existe donde hay solución exacta
(CHS); en la I y en el RHS/SHS es ``NO_DISPONIBLE`` con el motivo en ``origin`` (DEC-005).
"""

from typing import Final

from veritas.core.diagnostics import Location
from veritas.core.sections.parametric import ParametricParameters, ParametricShape
from veritas.core.sections.properties import PropertyStatus, SectionProperties, SectionProperty
from veritas.core.sections.shapes_hollow import (
    METHOD_CHS,
    METHOD_CHS_TORSION,
    METHOD_RHS,
    METHOD_SHS,
    chs_area_inertia,
    rhs_area_inertias,
)
from veritas.core.sections.shapes_i import METHOD_I, i_area_inertias
from veritas.core.validation import checked_positive

METHOD_TORSION_UNAVAILABLE: Final = "sin-solucion-exacta-validada/1"
_TORSION_MOTIVE: Final = (
    "sin solución exacta validada de la constante de torsión de Saint-Venant para esta forma "
    "(DEC-005; TK-028)"
)
_METHOD_OF: Final = {
    ParametricShape.I: METHOD_I,
    ParametricShape.RHS: METHOD_RHS,
    ParametricShape.SHS: METHOD_SHS,
    ParametricShape.CHS: METHOD_CHS,
}


def _no_torsion() -> SectionProperty:
    return SectionProperty(
        None, "m⁴", _TORSION_MOTIVE, METHOD_TORSION_UNAVAILABLE, PropertyStatus.NO_DISPONIBLE
    )


def parametric_properties(
    parameters: ParametricParameters, location: Location | None = None
) -> SectionProperties:
    """Propiedades de un perfil paramétrico (``parameters`` ya validado)."""
    shape, v = parameters.shape, parameters.as_dict()
    method = _METHOD_OF[shape]
    origin = f"calculado a partir de los parámetros de esta revisión (forma {shape})"

    def calc(value: float, unit: str, how: str = method) -> SectionProperty:
        return SectionProperty(value, unit, origin, how, PropertyStatus.CALCULADO)

    torsion: SectionProperty
    if shape is ParametricShape.I:
        area, iy, iz = i_area_inertias(v["h"], v["b"], v["tw"], v["tf"], v["r"])
        torsion = _no_torsion()
    elif shape is ParametricShape.CHS:
        area, iy = chs_area_inertia(v["d"], v["t"])
        iz = iy
        torsion = calc(checked_positive(2.0 * iy, "J", location), "m⁴", METHOD_CHS_TORSION)
    else:
        b = v["b"]
        h = b if shape is ParametricShape.SHS else v["h"]
        area, iy, iz = rhs_area_inertias(h, b, v["t"], v["ro"])
        torsion = _no_torsion()
    return SectionProperties(
        area=calc(checked_positive(area, "A", location), "m²"),
        centroid_y=calc(0.0, "m"),
        centroid_z=calc(0.0, "m"),
        iy=calc(checked_positive(iy, "Iy", location), "m⁴"),
        iz=calc(checked_positive(iz, "Iz", location), "m⁴"),
        iyz=calc(0.0, "m⁴"),
        torsion_constant=torsion,
    )
