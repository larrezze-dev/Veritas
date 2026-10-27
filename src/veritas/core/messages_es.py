"""Catálogo de mensajes de diagnóstico en español (cadenas externalizadas, NFR-009).

Las plantillas usan los parámetros del diagnóstico con ``str.format``. Un parámetro
ausente no rompe la presentación: se muestra la plantilla sin sustituir.
"""

from typing import Final

from veritas.core.diagnostics import Diagnostic

MESSAGES_ES: Final[dict[str, str]] = {
    "input.invalid_type": (
        "Tipo no admitido para {field}: se esperaba {expected} y se recibió {received}."
    ),
    "input.nonfinite": "Valor no finito para {field}: {value}.",
    "units.incompatible": "Dimensiones incompatibles: {left} frente a {right}.",
    "units.unrepresentable": "El resultado no es representable en doble precisión ({operation}).",
    "units.division_by_zero": "División por una magnitud nula ({operation}).",
    "units.unknown": "Unidad desconocida: «{symbol}».",
    "units.invalid_exponent": "Exponente no admitido: {exponent}; se requiere un entero.",
    "presentation.incomplete_profile": "El perfil «{profile}» no define unidad para: {missing}.",
    "presentation.unit_kind_mismatch": (
        "La unidad «{unit}» no corresponde a la magnitud {kind} en el perfil «{profile}»."
    ),
    "presentation.reserved_name": "El nombre «{profile}» está reservado a un perfil predefinido.",
    "presentation.invalid_name": "Nombre de perfil no válido: «{profile}».",
    "presentation.duplicate_kind": "El perfil «{profile}» define dos veces la misma magnitud.",
    "presentation.invalid_decimals": "Número de decimales no válido: {decimals}.",
    "geometry.same_node": "La barra no puede unir el nudo {node} consigo mismo.",
    "geometry.zero_length": (
        "Longitud de barra {length} m inferior al mínimo {minimum} m "
        "(nudos coincidentes o casi coincidentes)."
    ),
    "geometry.length_unrepresentable": (
        "La longitud de la barra no es representable en doble precisión."
    ),
    "geometry.reference_invalid": "Referencia de orientación nula o no válida.",
    "geometry.reference_parallel": (
        "La referencia de orientación es paralela al eje de la barra "
        "(proyección {projection} ≤ {tolerance})."
    ),
    "geometry.stored_reference_degenerate": (
        "Con la nueva geometría, la referencia guardada de la barra queda paralela a su eje "
        "(proyección {projection} ≤ {tolerance}). Asigne antes una orientación explícita."
    ),
    "geometry.alpha_out_of_range": "Giro alfa {alpha} rad fuera del rango |alfa| ≤ 2π.",
    "geometry.basis_not_orthonormal": (
        "La base local calculada no es ortonormal dextrógira "
        "(desviación {deviation}, determinante {determinant})."
    ),
    "model.reference_not_found": "No existe {kind} con identificador {entity_id}.",
}


QUANTITY_KIND_LABELS_ES: Final[dict[str, str]] = {
    "LENGTH": "Longitud",
    "AREA": "Área",
    "SECTION_MODULUS": "Módulo resistente",
    "SECOND_MOMENT": "Inercia / constante de torsión",
    "WARPING_CONSTANT": "Constante de alabeo",
    "MASS": "Masa",
    "DENSITY": "Densidad",
    "ACCELERATION": "Aceleración",
    "FORCE": "Fuerza",
    "FORCE_PER_LENGTH": "Fuerza por longitud",
    "TRANSLATIONAL_STIFFNESS": "Rigidez traslacional",
    "STRESS": "Tensión / módulo",
    "MOMENT": "Momento",
    "ANGLE": "Ángulo",
    "ROTATIONAL_STIFFNESS": "Rigidez rotacional",
    "DIMENSIONLESS": "Adimensional",
}


def render_message(diagnostic: Diagnostic) -> str:
    """Texto en español del diagnóstico; si falta la clave, se devuelve la propia clave."""
    template = MESSAGES_ES.get(diagnostic.message_key)
    if template is None:
        return diagnostic.message_key
    try:
        return template.format(**diagnostic.params_dict())
    except (KeyError, IndexError, ValueError):
        return template
