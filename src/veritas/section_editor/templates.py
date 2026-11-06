"""Plantillas U, L y T del croquis (EDT-001; TK-027; plan T-029 E2, E2-D2 = A).

Una plantilla solo GENERA el polígono desde sus medidas: la sección resultante es ``FREEFORM`` y
no recuerda de qué plantilla vino (se guarda el polígono, no las medidas). Sin acuerdos ni radios.
Las propiedades salen de la vía general del polígono; las fórmulas cerradas de U, L y T solo
existen en las pruebas, como referencia independiente. La J de estas formas es ``NO_DISPONIBLE``
(E2-D1 = A).

Origen de coordenadas: esquina inferior izquierda de la caja de la forma; y según b, z según h.

======  ===================  =====================================================================
Forma   Dimensiones          Significado
======  ===================  =====================================================================
U       ``h, b, tw, tf``     canto, ancho de ala, alma (a la izquierda) y alas (arriba y abajo)
L       ``h, b, t``          ala vertical (h), ala horizontal (b), espesor único
T       ``h, b, tw, tf``     canto total, ancho del ala (arriba), alma centrada y espesor del ala
======  ===================  =====================================================================
"""

from collections.abc import Mapping
from typing import Final

from veritas.core.diagnostics import Diagnostic, DiagnosticCode, InputValueError, Location
from veritas.core.units import LENGTH, Quantity
from veritas.core.validation import invalid_type, out_of_range, require_real
from veritas.section_editor.sketch import SketchContour, SketchDraft

TEMPLATE_DIMENSIONS: Final = {
    "u": ("h", "b", "tw", "tf"),
    "l": ("h", "b", "t"),
    "t": ("h", "b", "tw", "tf"),
}
"""Dimensiones obligatorias de cada plantilla, en orden canónico."""

type Polygon = list[tuple[float, float]]


def _rule(shape: str, rule: str, field: str) -> Diagnostic:
    return Diagnostic.create(
        DiagnosticCode.INPUT_OUT_OF_RANGE,
        "section.template_rule",
        {"shape": shape.upper(), "rule": rule},
        location=Location(field=field),
    )


def template_diagnostics(shape: str, values: Mapping[str, float]) -> tuple[Diagnostic, ...]:
    """Violaciones de las medidas de una plantilla (vacía si son válidas). ``values`` trae todas."""
    found = [
        out_of_range(name, values[name], "> 0", Location(field=name)).diagnostic
        for name in TEMPLATE_DIMENSIONS[shape]
        if values[name] <= 0.0
    ]
    if found:
        return tuple(found)
    h, b = values["h"], values["b"]
    if shape == "l":
        t = values["t"]
        if not (t < b and t < h):
            found.append(_rule(shape, "t < b y t < h", "t"))
    else:
        tw, tf = values["tw"], values["tf"]
        if not tw < b:
            found.append(_rule(shape, "tw < b", "tw"))
        limit = 2.0 * tf if shape == "u" else tf
        if not limit < h:
            found.append(_rule(shape, "2·tf < h" if shape == "u" else "tf < h", "tf"))
    return tuple(found)


def template_polygon(shape: str, values: Mapping[str, float]) -> Polygon:
    """Vértices (y, z) en m de la plantilla (medidas ya válidas)."""
    h, b = values["h"], values["b"]
    if shape == "l":
        t = values["t"]
        return [(0.0, 0.0), (b, 0.0), (b, t), (t, t), (t, h), (0.0, h)]
    tw, tf = values["tw"], values["tf"]
    if shape == "u":
        return [
            (0.0, 0.0),
            (b, 0.0),
            (b, tf),
            (tw, tf),
            (tw, h - tf),
            (b, h - tf),
            (b, h),
            (0.0, h),
        ]
    left, right = 0.5 * (b - tw), 0.5 * (b + tw)
    return [
        (left, 0.0),
        (right, 0.0),
        (right, h - tf),
        (b, h - tf),
        (b, h),
        (0.0, h),
        (0.0, h - tf),
        (left, h - tf),
    ]


def _names_diagnostic(shape: str, name: str, problem: str) -> Diagnostic:
    return Diagnostic.create(
        DiagnosticCode.INPUT_OUT_OF_RANGE,
        "editor.dimension_name",
        {
            "shape": shape.upper(),
            "name": name,
            "problem": problem,
            "expected": ", ".join(TEMPLATE_DIMENSIONS[shape]),
        },
        location=Location(field=name),
    )


def template_draft(shape: str, dimensions: Mapping[str, Quantity]) -> SketchDraft:
    """Borrador de una plantilla U, L o T; rechaza con el primer diagnóstico si no es válida."""
    if shape not in TEMPLATE_DIMENSIONS:
        raise invalid_type("shape", "u, l o t", shape)
    expected = TEMPLATE_DIMENSIONS[shape]
    for name in dimensions:
        if name not in expected:
            raise InputValueError(_names_diagnostic(shape, name, "desconocida"))
    for name in expected:
        if name not in dimensions:
            raise InputValueError(_names_diagnostic(shape, name, "falta"))
    values: dict[str, float] = {}
    for name in expected:
        quantity = dimensions[name]
        if not isinstance(quantity, Quantity):
            raise invalid_type(name, "Quantity", quantity, Location(field=name))
        values[name] = require_real(quantity.require(LENGTH).si_value, name)
    problems = template_diagnostics(shape, values)
    if problems:
        raise InputValueError(problems[0])
    points = tuple(
        (Quantity(y, LENGTH), Quantity(z, LENGTH)) for y, z in template_polygon(shape, values)
    )
    draft = SketchDraft(SketchContour(points))
    # Medidas casi degeneradas (p. ej. 2·tf = h − ε con ε bajo la tolerancia geométrica): el
    # polígono resultante no es válido y se rechaza aquí, no más tarde al construir.
    problems = draft.diagnostics()
    if problems:
        raise InputValueError(problems[0])
    return draft
