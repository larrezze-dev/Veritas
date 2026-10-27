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
    "input.out_of_range": (
        "Valor de {field} fuera del rango admisible: {value} (se exige {condition})."
    ),
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
    "model.revision_conflict": (
        "Conflicto de revisión en {kind} {entity_id}, revisión {revision}: {reason}."
    ),
    "model.family_change": (
        "Una revisión no puede cambiar la familia de {kind} ({current} -> {requested}); "
        "cree una entidad nueva."
    ),
    "section.properties_mismatch": (
        "Las propiedades de la sección no coinciden con las calculadas a partir de sus parámetros."
    ),
    "support.invalid_states": (
        "Estados de apoyo no válidos: se esperaban seis DofRestraint en el orden "
        "Ux, Uy, Uz, Rx, Ry, Rz y se recibió {received}."
    ),
    "support.no_restraint": (
        "Un apoyo debe restringir al menos un GDL (FIXED o SPRING); para quitarlo, retírelo."
    ),
    "support.spring_nonpositive": (
        "Rigidez de resorte no admitida: {value} (se exige > 0 y normal; use FREE en su lugar)."
    ),
    "support.basis_direction_invalid": "Dirección {field} de la base de apoyo nula o subnormal.",
    "support.basis_reference_parallel": (
        "La referencia z_ref es casi paralela a x_dir (proyección {projection} <= {tolerance})."
    ),
    "support.roller_axis": "El eje de un rodillo debe ser una traslación (Ux, Uy, Uz): {axis}.",
    "support.duplicate_dof": "El GDL {dof} se define más de una vez.",
    "support.unknown_dof": "GDL desconocido: «{dof}» (se admiten Ux, Uy, Uz, Rx, Ry, Rz).",
    "support.unsupported_basis": (
        "Base de apoyo no soportada: «{basis}» (se admiten global e inclinada)."
    ),
    "load.all_zero": "Una carga debe tener alguna componente no nula ({fields}).",
    "load.self_weight_duplicate": (
        "El modelo ya tiene un peso propio (caso {case_id}); solo se admite uno. "
        "Retírelo antes de definirlo en otro caso."
    ),
    "load.self_weight_category": (
        "El peso propio solo puede estar en un caso permanente; el caso «{case}» sería {category}."
    ),
    "load.self_weight_missing": "El modelo no tiene peso propio definido.",
    "section.property_unavailable": "La propiedad {property} de la sección no está disponible.",
    "section.property_unrepresentable": (
        "La propiedad {property} no es representable como valor positivo en doble precisión "
        "(resultado {value})."
    ),
}


QUANTITY_KIND_LABELS_ES: Final[dict[str, str]] = {
    "LENGTH": "Longitud",
    "AREA": "Área",
    "SECTION_MODULUS": "Módulo resistente",
    "SECOND_MOMENT": "Inercia / constante de torsión",
    "WARPING_CONSTANT": "Constante de alabeo",
    "MASS": "Masa",
    "DENSITY": "Densidad",
    "LINEAR_MASS": "Masa lineal",
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
