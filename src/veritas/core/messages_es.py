"""Catálogo de mensajes de diagnóstico en español (cadenas externalizadas, NFR-009).

Las plantillas usan los parámetros del diagnóstico con ``str.format``. Un parámetro
ausente no rompe la presentación: se muestra la plantilla sin sustituir.
"""

from collections.abc import Mapping
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
    "presentation.invalid_precision": (
        "Precisión no válida ({mode}, {digits} dígitos): el mínimo es {lowest}."
    ),
    "presentation.invalid_digits_option": (
        "Opción --cifras no válida: «{item}». Use MAGNITUD=N[,MAGNITUD=N…] con N de 1 a 15 y "
        "una magnitud de: {kinds}."
    ),
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
    "project.invalid_json": "El archivo de proyecto no es JSON válido: {reason}.",
    "project.duplicate_key": "Clave repetida en el archivo de proyecto: {key}.",
    "project.nonfinite_literal": "El archivo de proyecto contiene un valor no finito: {value}.",
    "project.too_large": "El archivo de proyecto ocupa {size} bytes y el máximo es {limit}.",
    "project.too_deep": "El archivo de proyecto supera la profundidad máxima de {limit} niveles.",
    "project.unreadable": "No se puede leer el archivo de proyecto: {reason}.",
    "project.write_failed": (
        "No se pudo guardar el proyecto ({reason}); el archivo anterior no se ha modificado."
    ),
    "project.bad_format": "No es un archivo de proyecto Veritas (formato «{found}»).",
    "project.invalid_field": "Campo {path}: se esperaba {expected}.",
    "project.unknown_fields": "Campos desconocidos en {path}: {fields}.",
    "project.missing_fields": "Faltan campos en {path}: {fields}.",
    "project.invalid_value": "Valor no válido en {path}: {reason}.",
    "project.nonfinite": "Valor no finito en {path}.",
    "project.integrity": (
        "El archivo está alterado o incompleto: resumen guardado {expected}, calculado {actual}."
    ),
    "project.unsupported_version": (
        "La versión de esquema {found} es posterior a la soportada ({supported}); "
        "abra el archivo con una versión más reciente de la aplicación."
    ),
    "project.unsupported_canon": (
        "La codificación canónica «{found}» no está soportada (soportada: «{supported}»); "
        "el hash del modelo no se puede verificar."
    ),
    "project.duplicate_run": "La ejecución {run_id} ya está guardada en el proyecto.",
    "project.run_model_changed": (
        "El modelo cambió desde la corrida (revisión {run_revision} frente a {model_revision}): "
        "no se puede ligar a su hash."
    ),
    "project.assign_usage": (
        "Uso: assign-profile ARCHIVO ETIQUETA REFERENCIA, o con --todas: ARCHIVO REFERENCIA."
    ),
    "project.replace_usage": "Indique exactamente una selección: --barras E1 E2… o --todas.",
    "project.update_usage": "Indique exactamente una selección: ETIQUETA… o --todas.",
    "project.case_required": "Indique el caso de carga con --carga. Casos del proyecto: {cases}.",
    "project.invalid_version": "Versión de esquema no válida: {found}.",
    "project.migration_gap": "No hay migración registrada de la versión {step} a la {next}.",
    "model.family_change": (
        "Una revisión no puede cambiar la familia de {kind} ({current} -> {requested}); "
        "cree una entidad nueva."
    ),
    "section.family_mismatch": (
        "Los parámetros ({found}) no corresponden a la familia de sección {family}."
    ),
    "section.not_rectangular": (
        "La sección es de la familia {family}: no tiene parámetros b y h de un rectángulo."
    ),
    "section.catalog_invalid_name": "Nombre de dimensión de perfil no válido: «{name}».",
    "section.catalog_invalid_shape": "Forma de perfil de catálogo no admitida: «{shape}».",
    "section.catalog_invalid_hash": (
        "El hash de origen del perfil de catálogo debe ser un SHA-256 hexadecimal en minúsculas."
    ),
    "section.catalog_duplicate_dimension": "Dimensión de perfil repetida: «{name}».",
    "section.catalog_invalid_warning": "Clave de aviso de perfil no válida o repetida: «{key}».",
    "section.catalog_invalid_status": (
        "Estado {status} no admitido en {field}: un perfil de catálogo solo puede ser "
        "IMPORTADO_SIN_VERIFICAR o NO_DISPONIBLE."
    ),
    "section.catalog_invalid_unit": "Unidad {unit} no válida para {field} de un perfil.",
    "section.catalog_properties_mismatch": (
        "La propiedad {field} de la sección no coincide con la copiada del perfil de catálogo."
    ),
    "section.catalog_not_derivable": (
        "La propiedad {field} de un perfil de catálogo debe ser NO_DISPONIBLE: el catálogo no la "
        "aporta y no se deduce."
    ),
    "editor.dimension_name": (
        "Dimensión «{name}» de la forma {shape}: {problem} (dimensiones de la forma: {expected})."
    ),
    "editor.compare_tolerance": (
        "Tolerancia {value} no válida: debe ser un número entre 0 y 1 (0,01 = 1 %)."
    ),
    "editor.compare_catalog_section": (
        "La sección es de la familia {family}: ya es un perfil de catálogo, no hay nada que "
        "contrastar."
    ),
    "editor.compare_shape_differs": (
        "La forma del perfil de catálogo no coincide con la de la sección: la comparación no es "
        "significativa."
    ),
    "editor.compare_fillet_differs": (
        "Solo uno de los dos lados lleva radios de acuerdo: es la causa probable de la diferencia."
    ),
    "editor.cli_dimension": (
        "Dimensión «{text}» no válida: use nombre=valor, por ejemplo h=300 (en la unidad de "
        "--unidad)."
    ),
    "editor.cli_shape": ("Forma «{shape}» no válida para el editor: use rect, i, rhs, shs o chs."),
    "editor.cli_contrast_usage": "--contrastar y --db se indican juntos (el catálogo se lee solo).",
    "editor.cli_rect_dimensions": "El rectángulo se define con --dim b=… h=… y nada más.",
    "editor.cli_not_comparable": (
        "No se registra el contraste: ninguna propiedad comparable está dentro de la tolerancia."
    ),
    "editor.cli_section_not_found": (
        "No hay ninguna sección «{name}» en el proyecto (nombre exacto de «project sections»)."
    ),
    "project.assign_section_usage": (
        "Use «assign-section ARCHIVO ETIQUETA SECCIÓN» o «assign-section ARCHIVO --todas SECCIÓN»."
    ),
    "section.not_parametric": (
        "La sección es de la familia {family}: no tiene parámetros de un perfil paramétrico."
    ),
    "section.geometry_dimensions": (
        "Dimensiones no válidas para la forma {shape}: se esperaban {expected} y se recibió "
        "{found}."
    ),
    "section.geometry_flange_too_thick": (
        "El espesor de ala tf = {tf:g} m no deja alma: se exige 2·tf < h = {h:g} m."
    ),
    "section.geometry_web_too_thick": (
        "El espesor de alma tw = {tw:g} m no deja vuelo de ala: se exige tw < b = {b:g} m."
    ),
    "section.geometry_wall_too_thick": (
        "El espesor t = {t:g} m cierra el hueco: se exige 2·t < {side:g} m (lado menor)."
    ),
    "section.geometry_wall_too_thick_circle": (
        "El espesor t = {t:g} m cierra el hueco: se exige t < d/2 con d = {d:g} m."
    ),
    "section.geometry_radius_too_large": (
        "El radio {field} = {value:g} m es demasiado grande: se exige como máximo {max:g} m."
    ),
    "section.shear_not_derivable": (
        "Un perfil de catálogo no lleva áreas de cortante: el catálogo no las aporta y no se "
        "deducen."
    ),
    "section.shear_invalid_status": (
        "Estado no admitido en {field}: el área de cortante solo puede ser CALCULADO o "
        "NO_DISPONIBLE."
    ),
    "section.shear_mismatch": (
        "Las áreas de cortante de la sección no coinciden con las calculadas a partir de sus "
        "parámetros."
    ),
    "section.fem_missing": "No apta para FEM: falta la propiedad {property}.",
    "section.code_not_verified": (
        "Cobertura normativa NO COMPROBADA: no existe una verificación normativa validada."
    ),
    "section.replace_incompatible_material": (
        "Barra «{member}»: un perfil de forma {shape} (metálico) no es compatible con un "
        "material de la familia {family}."
    ),
    "section.replace_mass_change": "Barra «{member}»: la masa lineal cambia {change}.",
    "section.replace_mass_unknown": (
        "Barra «{member}»: no se puede comparar la masa lineal (falta el área de una sección)."
    ),
    "section.replace_not_fem_ready": (
        "Barra «{member}»: la sección nueva no es apta para FEM (falta {missing})."
    ),
    "model.empty_selection": "No se ha seleccionado ninguna barra.",
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
    "connection.spring_nonpositive": (
        "Rigidez del resorte de extremo en {dof} no admitida: {value} (se exige > 0 y normal; "
        "use RELEASED en su lugar)."
    ),
    "connection.semi_rigid_dof": (
        "El preset semirrígido pone el resorte en un giro (Rx, Ry, Rz), no en {dof}."
    ),
    "connection.duplicate_dof": "El GDL de extremo {dof} se define más de una vez.",
    "connection.unknown_dof": (
        "GDL de extremo desconocido: «{dof}» (se admiten Ux, Uy, Uz, Rx, Ry, Rz)."
    ),
    "load.all_zero": "Una carga debe tener alguna componente no nula ({fields}).",
    "load.position_out_of_range": (
        "Posición {field} = {value} fuera de rango ({condition}); una carga en un extremo de la "
        "barra se introduce como carga nodal."
    ),
    "load.segment_too_short": (
        "Tramo de carga [{start}; {end}] demasiado corto: ξb − ξa debe superar {minimum}."
    ),
    "load.self_weight_duplicate": (
        "El modelo ya tiene un peso propio (caso {case_id}); solo se admite uno. "
        "Retírelo antes de definirlo en otro caso."
    ),
    "load.self_weight_category": (
        "El peso propio solo puede estar en un caso permanente; el caso «{case}» sería {category}."
    ),
    "load.self_weight_missing": "El modelo no tiene peso propio definido.",
    "load.self_weight_mode_gravity": (
        "En el modo {mode} el peso propio no usa la gravedad g: no se debe indicar."
    ),
    "load.self_weight_all_excluded": (
        "El peso propio excluye las {count} barras del modelo; una definición sin barras no "
        "se admite."
    ),
    "load.self_weight_member_excluded": (
        "La barra {member} está excluida del peso propio; no se deriva su peso."
    ),
    "material.specific_weight_missing": (
        "El material «{material}» de la barra {member} no tiene peso específico γ y el peso "
        "propio está en modo de peso específico."
    ),
    "load.settlement_dof_not_fixed": (
        "El asiento exige el GDL {dof} del apoyo FIXED, pero está en estado {state}."
    ),
    "load.settlement_duplicate": (
        "El caso «{case}» ya tiene un asiento en este apoyo; solo se admite uno por caso y "
        "apoyo. Retírelo antes de añadir otro."
    ),
    "load.settlement_support_conflict": (
        "El cambio del apoyo ({detail}) dejaría incoherente el asiento del caso «{case}»; "
        "retire antes el asiento."
    ),
    "fem.no_members": "El modelo no tiene barras: no hay nada que analizar.",
    "fem.node_out_of_plane": (
        "El nudo {node} está fuera del plano de análisis XZ "
        "(Y = {y} m; se exige |Y| ≤ {tolerance} m)."
    ),
    "fem.isolated_node": "El nudo {node} no pertenece a ninguna barra.",
    "fem.member_end_translation_unsupported": (
        "El extremo {end} de la barra {member} tiene {dof} en estado {state}: el análisis plano "
        "solo admite liberación o resorte en el giro de flexión del plano, no en traslaciones."
    ),
    "fem.member_end_out_of_plane_unsupported": (
        "El extremo {end} de la barra {member} tiene {dof} en estado {state}: es un GDL fuera "
        "del plano XZ y el análisis plano no puede representarlo. Libere solo el giro del plano."
    ),
    "fem.end_spring_near_rigid": (
        "El resorte del extremo {end} de la barra {member} es casi rígido (k·L/(E·I) = {ratio} "
        "> {limit}); degrada el condicionamiento. Considere el estado RIGID."
    ),
    "fem.orphan_rotation_restrained": (
        "En el nudo {node} todas las barras tienen el giro liberado y ningún apoyo lo restringe: "
        "ese giro no tiene rigidez ni carga y se restringe para el cálculo. No es una reacción "
        "y el giro del nudo no se publica."
    ),
    "fem.mechanism_orphan_rotation": (
        "Mecanismo en el nudo o nudos {node}: todas las barras tienen el giro liberado, ningún "
        "apoyo lo restringe y el caso aplica un momento en ese nudo. No se calcula."
    ),
    "fem.mechanism_member_end": (
        "Rigidez singular (pivote escalado {pivot} ≤ {tolerance}): mecanismo en el que "
        "participa el giro del extremo {end} de la barra {member}. Los GDL que participan se "
        "registran con el modo de mecanismo; si el modo no es concluyente, la ubicación es "
        "orientativa (un GDL del mecanismo). No se calcula."
    ),
    "fem.mechanism_mode_inconclusive": (
        "El modo de mecanismo no es concluyente (residuo {residual} > {limit} o participación "
        "dentro de la banda de guarda): la ubicación es la del pivote, orientativa."
    ),
    "fem.material_family_unsupported": (
        "La barra {member} usa material {family}; el análisis plano solo admite acero y aluminio "
        "con E isótropo (ADR-002 §2)."
    ),
    "fem.member_axis_not_plane": (
        "Los ejes locales de la barra {member} no permiten flexión en el plano XZ "
        "(ex·Y = {ex_y}, ey·Y = {ey_y}, ez·Y = {ez_y}; tolerancia {tolerance})."
    ),
    "fem.section_property_unavailable": (
        "La propiedad {property} de la sección no está disponible; no se sustituye por cero."
    ),
    "fem.section_coupled_bending": (
        "La sección de la barra {member} tiene Iyz = {iyz}; la flexión acoplada no está soportada."
    ),
    "fem.support_basis_not_plane": (
        "La base inclinada del apoyo no tiene ey′ normal al plano XZ "
        "(ex′·Y = {ex_y}, ez′·Y = {ez_y}; tolerancia {tolerance})."
    ),
    "fem.support_spring_out_of_plane": (
        "Resorte de apoyo en {dof}, fuera del plano XZ: "
        "no se puede representar en el análisis plano."
    ),
    "fem.load_out_of_plane": (
        "Carga nodal fuera del plano XZ (Fy = {fy} N, Mx = {mx} N·m, Mz = {mz} N·m)."
    ),
    "fem.member_load_out_of_plane": (
        "Carga de barra con componente global Y = {qy} N/m (límite {limit} N/m)."
    ),
    "fem.member_point_load_out_of_plane": (
        "Carga puntual de barra fuera del plano XZ (Fy = {fy} N, límite {force_limit} N; "
        "Mx = {mx} N·m, Mz = {mz} N·m, límite {moment_limit} N·m)."
    ),
    "fem.member_moment_out_of_plane": (
        "Momento distribuido de barra fuera del plano XZ (mx = {mx} N·m/m, mz = {mz} N·m/m; "
        "límite {limit} N·m/m)."
    ),
    "fem.settlement_out_of_plane": (
        "Asiento fuera del plano XZ (Uy = {uy} m, Rx = {rx} rad, Rz = {rz} rad); el análisis "
        "plano solo admite Ux, Uz y Ry."
    ),
    "fem.settlement_inclined_unsupported": (
        "Asiento en un apoyo de base inclinada: el análisis solo lo admite en apoyos de base "
        "global; no se ignora."
    ),
    "fem.settlement_with_penalty_unsupported": (
        "El caso tiene asientos y la petición usa penalizaciones o cadenas (apoyos inclinados, "
        "extremos penalizados); esta combinación aún no se admite y no se ignora."
    ),
    "fem.settlement_mismatch": (
        "El desplazamiento calculado en el GDL asentado {component} del nudo {node} "
        "({value}) no coincide con el prescrito ({prescribed}); diferencia {difference} "
        "> cota {bound}."
    ),
    "fem.load_type_unsupported": (
        "Carga de tipo {type} que el análisis plano no admite; no se ignora."
    ),
    "fem.empty_case": "El caso «{case}» no tiene cargas.",
    "fem.too_many_dofs": (
        "El modelo tiene {count} GDL libres; la comprobación de singularidad admite como máximo "
        "{limit} en esta versión."
    ),
    "fem.no_free_dofs": (
        "Todos los GDL del modelo plano están restringidos (ningún GDL libre): el análisis "
        "plano no resuelve esta estructura en esta versión. No se calcula."
    ),
    "fem.singular": (
        "Rigidez singular o extremadamente mal condicionada (pivote escalado {pivot} ≤ "
        "{tolerance}, componente {component}): posible mecanismo o apoyos insuficientes. "
        "No se calcula."
    ),
    "fem.invalid_stiffness": "Matriz de rigidez con diagonal negativa o no finita (error interno).",
    "fem.solver_failed": "El solver falló (código {code}). No hay resultados.",
    "fem.timeout": (
        "El análisis superó el tiempo máximo de {timeout} s y se detuvo. No hay resultados."
    ),
    "fem.worker_crashed": (
        "El proceso de cálculo terminó sin respuesta válida (código {returncode}). "
        "No hay resultados."
    ),
    "fem.protocol_error": "Respuesta del proceso de cálculo no válida. No hay resultados.",
    "fem.nonfinite_result": "El solver devolvió valores no finitos. No hay resultados.",
    "fem.equilibrium_failed": (
        "Control de equilibrio no superado (residuo de fuerzas {force}, de momentos {moment}; "
        "tolerancia {tolerance}). No hay resultados."
    ),
    "fem.member_end_postcondition_failed": (
        "Control de la solución no superado en el extremo {end} de la barra {member}: "
        "{control} (valor {value}, cota {bound}). No hay resultados."
    ),
    "fem.member_force_check_failed": (
        "Control de esfuerzos no superado en la barra {member}: componente {component} de "
        "localForce frente a k·T·u − F_eq (residuo {value}, cota {bound}). No hay resultados."
    ),
    "fem.residual_failed": (
        "Residuo K·u − P no superado en {subject}, coordenada {component} de la base del "
        "solver (residuo {value}, cota {bound}). No hay resultados."
    ),
    "fem.subject_node": "el nudo {node}",
    "fem.subject_member_end": "el giro del extremo {end} de la barra {member}",
    "fem.out_of_plane_implicit_restraint": (
        "Ningún apoyo restringe {dof}: el análisis plano lo restringe implícitamente en los "
        "{nodes} nudos. El modelo 3D podría ser un mecanismo fuera del plano; el resultado solo "
        "vale para el modelo plano."
    ),
    "fem.hypothesis_planar": ("Análisis estático lineal en el plano XZ: x₁ = X, x₂ = Z, θ = −Ry."),
    "fem.hypothesis_euler_bernoulli": (
        "Barras Euler–Bernoulli 2D (sin deformación por cortante); se usan A, E e Iy o Iz."
    ),
    "fem.hypothesis_implicit_out_of_plane": (
        "Uy, Rx y Rz quedan restringidos implícitamente en todos los nudos; sus reacciones no se "
        "calculan."
    ),
    "fem.hypothesis_penalty": (
        "Al menos un apoyo inclinado rígido se representa por penalización (error relativo del "
        "orden de 1e-8 en la dirección restringida)."
    ),
    "fem.hypothesis_member_end_links": (
        "Extremos de barra con giro liberado o resorte: nudo auxiliar con las traslaciones del "
        "nudo (equalDOF) y giro propio; resorte rotacional lineal, elástico y bilateral."
    ),
    "fem.hypothesis_orphan_rotation": (
        "Giros de nudo sin rigidez ni carga (todas las barras liberadas) restringidos para el "
        "cálculo; no son reacciones."
    ),
    "fem.hypothesis_normalized": (
        "Resultados normalizados a ADR-001 (ejes globales y locales de Veritas); formulación "
        "plana contrastada solo con T-FEM-001…004 (T-009) y T-CON-REF-01…04 (T-010); este "
        "resultado no está validado; aplicación experimental."
    ),
    "fem.hypothesis_support_projection": (
        "Reacción en base del apoyo inclinado: Q_apoyo·R con Y, Mx y Mz tomados como 0 (no "
        "calculados); su efecto en Ux′/Uz′ es ≤ tol_axis·|R fuera del plano|."
    ),
    "fem.results_unavailable": (
        "No hay resultados que normalizar: la corrida no es SUCCEEDED (estado {status})."
    ),
    "fem.results_model_mismatch": (
        "El modelo no corresponde a la corrida analizada ({reason}). Vuelve a analizar."
    ),
    "fem.results_axes_inconsistent": (
        "Ejes de barra incoherentes con el plano al normalizar (componente {component} = "
        "{value}, límite {limit}; error interno). No hay resultados."
    ),
    "fem.results_equilibrium": (
        "Control de equilibrio de los resultados normalizados no superado ({where}: residuo de "
        "fuerza {force}, de momento {moment}; error interno). No hay resultados."
    ),
    "fem.results_unsupported_load": (
        "Carga {load} de un tipo que la normalización no admite (error interno)."
    ),
    "fem3d.end_axial_with_axial_load": (
        "La barra {member} tiene Ux de extremo liberado o con resorte y carga axial de barra "
        "en el caso (componente axial {wx}: N/m si es distribuida, N si es puntual; incluido "
        "el peso propio de una barra inclinada); el análisis espacial no admite esa "
        "combinación."
    ),
    "fem3d.end_torsion_with_torsional_load": (
        "La barra {member} tiene Rx de extremo liberado o con resorte y par torsor de barra "
        "en el caso (componente x {mx}: N·m/m si es distribuido, N·m si es puntual); el "
        "análisis espacial no admite esa combinación (T-015, D8)."
    ),
    "fem3d.end_spring_near_rigid": (
        "El resorte {dof} del extremo {end} de la barra {member} es casi rígido (cociente "
        "{ratio} > {limit}); puede empeorar el condicionamiento: considere declararlo rígido."
    ),
    "fem3d.orphan_rotation_restrained": (
        "El nudo {node} no tiene rigidez de giro en {rotations} (todas las barras liberadas y "
        "sin apoyo de giro); esos giros se fijan sin reacción."
    ),
    "fem3d.mechanism_orphan_rotation": (
        "Mecanismo en el nudo o nudos {node}: un giro no tiene rigidez (barras liberadas y sin "
        "apoyo de giro) y el caso aplica un momento sobre él. No se calcula."
    ),
    "fem3d.orphan_rotation_oblique": (
        "Mecanismo en el nudo o nudos {node}: un giro sin rigidez en una dirección oblicua a "
        "los ejes globales (barras liberadas y sin apoyo de giro); no se fija. Los GDL del "
        "modo figuran en la procedencia. No se calcula."
    ),
    "fem3d.hypothesis_end_releases": (
        "Giros de flexión liberados en los extremos representados por la condensación del "
        "propio elemento de barra (exacta, también con cargas de barra)."
    ),
    "fem3d.hypothesis_end_series": (
        "Torsión y axil de extremo liberados o con resorte representados con J y A efectivos "
        "en serie (exacto: torsor y axil constantes en la barra)."
    ),
    "fem3d.hypothesis_end_links": (
        "Extremos con resorte o traslación liberada representados con nudos auxiliares que no "
        "forman parte de los resultados."
    ),
    "fem3d.hypothesis_orphan_rotation": (
        "Giros sin rigidez en algún nudo fijados en ejes globales, sin reacción (ver avisos)."
    ),
    "fem3d.load_type_unsupported": (
        "Carga de tipo {type} que el análisis espacial no admite; no se ignora."
    ),
    "fem3d.hypothesis_spatial": (
        "Análisis estático lineal espacial: seis GDL por nudo (Ux, Uy, Uz, Rx, Ry, Rz) en la "
        "base global de ADR-001."
    ),
    "fem3d.hypothesis_euler_bernoulli": (
        "Barras Euler–Bernoulli 3D sin deformación por cortante; torsión uniforme de "
        "Saint-Venant sin alabeo; se usan A, Iy, Iz, J, E y G; Iyz = 0."
    ),
    "fem3d.hypothesis_lagrange": (
        "Restricciones por multiplicadores de Lagrange con factores escalados a la mayor "
        "rigidez diagonal de las barras."
    ),
    "fem3d.hypothesis_inclined_chain": (
        "Traslaciones fijas según una normal no global representadas de forma exacta, sin "
        "penalización, con nudos auxiliares que no forman parte de los resultados."
    ),
    "fem3d.hypothesis_penalty": (
        "Al menos un giro fijo parcial en base oblicua de un apoyo se representa por "
        "penalización; el resultado lleva la tolerancia de penalización."
    ),
    "fem3d.hypothesis_normalized": (
        "Resultados normalizados a ADR-001 (ejes globales y locales de Veritas) con seis "
        "componentes calculadas; formulación espacial contrastada solo con su batería "
        "declarada (T-018); este resultado no está validado; aplicación experimental."
    ),
    "fem3d.chain_postcondition_failed": (
        "Control de la cadena del apoyo inclinado no superado en el nudo {node} ({control}: "
        "desviación {value}, cota {bound}). No hay resultados."
    ),
    "fem3d.penalty_estimate_failed": (
        "La restricción representada por penalización en el nudo {node} se incumple más de "
        "lo admitido (estimado {value}, cota {bound}; incluye el redondeo "
        "eps·penalty_factor, que crece con un factor de penalización alto). No hay resultados."
    ),
    "fem3d.hypothesis_end_penalty_iterated": (
        "Al menos una componente rígida de un extremo oblicuo se representa por penalización "
        "iterada (ADR-004): se itera hasta que el paso relativo de la restricción baja de la "
        "tolerancia; el resultado lleva la tolerancia de penalización."
    ),
    "fem3d.end_penalty_iterations": (
        "Informativo: la penalización iterada de los extremos terminó en {iterations} "
        "iteraciones (motivo: {stop}; paso relativo final {step})."
    ),
    "fem3d.subject_member": "la barra {member}",
    "fem3d.end_penalty_contrast_failed": (
        "El contraste exacto de la penalización iterada de los extremos no se supera "
        "({control}, componente {component}: desviación {value}, cota {bound}) en {subject}. "
        "No hay resultados."
    ),
    "fem3d.end_penalty_reference_unreliable": (
        "La solución de referencia del contraste de la penalización de extremo no es fiable "
        "(corrección del refinamiento {value}, cota {bound}). No hay resultados."
    ),
    "fem3d.end_link_check_failed": (
        "Control del enlace del extremo {end} de la barra {member} no superado ({control}: "
        "desviación {value}, cota {bound}). No hay resultados."
    ),
    "fem3d.singular_member_end": (
        "Rigidez singular o extremadamente mal condicionada (pivote escalado {pivot} ≤ "
        "{tolerance}) en el extremo {end} de la barra {member}, componente local {component}: "
        "posible mecanismo en la unión de extremo. No se calcula."
    ),
    "fem3d.end_penalty_not_converged": (
        "La penalización iterada de los extremos no converge (iteraciones {iterations}, "
        "motivo {stop}, paso relativo final {step}, cota {bound}). No hay resultados."
    ),
    "fem3d.solver_stderr": (
        "El solver escribió avisos durante una corrida terminada: «{text}». Los controles de "
        "la solución deciden si es válida."
    ),
    "benchmark.hypothesis_formulation": (
        "Euler-Bernoulli 2D sin deformación por cortante, geomTransf Linear, estática lineal, "
        "EI y EA constantes y cargas consistentes: las fórmulas son exactas en nudos y extremos."
    ),
    "benchmark.hypothesis_data": (
        "Acero E = {modulus_gpa:g} GPa, R {b_mm:g}×{h_mm:g} (Iy = b·h³/12, A = b·h)."
    ),
    "benchmark.title_simply_supported": "Biapoyada con P central",
    "benchmark.title_cantilever": "Voladizo con carga uniforme",
    "benchmark.title_spring_global": "Resorte traslacional en base global",
    "benchmark.title_spring_inclined": "Resorte traslacional en base inclinada",
    "benchmark.title_inclined_roller": "Rodillo inclinado",
    "benchmark.description_simply_supported": (
        "Biapoyada de {span:g} m en +X (B1 = N1→N2, B2 = N2→N3), P = {load_kn:g} kN hacia −Z "
        "como carga nodal en N2, articulación en N1. Rodillo vertical (Uz) en N3."
    ),
    "benchmark.description_cantilever": (
        "Voladizo de {span:g} m en +X en dos barras (nudo interior a = L/2), empotrado en N1, "
        "q = −{load_kn_m:g} kN/m en Z sobre B1 y B2."
    ),
    "benchmark.description_spring_global": (
        "Biapoyada de {span:g} m en +X (B1 = N1→N2, B2 = N2→N3), P = {load_kn:g} kN hacia −Z "
        "como carga nodal en N2, articulación en N1. Resorte k = {stiffness:g} N/m en Uz de N3 "
        "(isostática)."
    ),
    "benchmark.description_spring_inclined": (
        "Biapoyada de {span:g} m en +X (B1 = N1→N2, B2 = N2→N3), P = {load_kn:g} kN hacia −Z "
        "como carga nodal en N2, articulación en N1. Resorte k = {stiffness:g} N/m en Uz′ de "
        "N3, base a {angle:g}° (ex′ = (cos, 0, sin), ez′ = (−sin, 0, cos)); Ux′ libre."
    ),
    "benchmark.description_inclined_roller": (
        "Biapoyada de {span:g} m en +X (B1 = N1→N2, B2 = N2→N3), P = {load_kn:g} kN hacia −Z "
        "como carga nodal en N2, articulación en N1. Rodillo rígido sobre un plano a "
        "{angle:g}° en N3 (Uz′ fijo por penalización, Ux′ libre)."
    ),
    "benchmark.note_in_plane_not_applicable": (
        "R_x′ en N3: no aplicable (GDL libre); la reacción no tiene componente en el plano de "
        "rodadura."
    ),
    "benchmark.title_propped_released": "Empotrada-deslizadera con extremo I liberado",
    "benchmark.title_propped_spring": "Empotrada-deslizadera con resorte en el extremo I",
    "benchmark.title_cantilever_spring": "Voladizo con resorte en el extremo I",
    "benchmark.title_internal_hinge": "Rótula interior (extremo J liberado)",
    "benchmark.title_truss": "Cercha triangular articulada",
    "benchmark.description_propped_released": (
        "Barra de {span} m, empotramiento en N1 y deslizadera Uz en N2, carga {load_kn_m} kN/m "
        "hacia −Z; giro Ry del extremo I liberado (equivale a biapoyada). T-CON-002/003."
    ),
    "benchmark.description_propped_spring": (
        "Barra de {span} m, empotramiento en N1 y deslizadera Uz en N2, carga {load_kn_m} kN/m "
        "hacia −Z; resorte rotacional en el extremo I con k·L/(EI) = {ratio}: "
        "M_I = (wL²/8)/(1 + 3EI/(kL)). T-CON-003."
    ),
    "benchmark.description_cantilever_spring": (
        "Voladizo de {span} m empotrado en N1 con resorte rotacional en el extremo I "
        "(k·L/(EI) = {ratio}) y P = {load_kn} kN hacia −Z en N2: δ = PL³/(3EI) + PL²/k."
    ),
    "benchmark.description_internal_hinge": (
        "Dos barras de {span} m; empotramiento en N1, rótula en N2 (extremo J de B1 liberado), "
        "deslizadera Uz en N3; P = {load_kn} kN hacia −Z en N2. Isostática."
    ),
    "benchmark.description_truss": (
        "Cercha N1–N2–N3 de {span} m de luz y {rise} m de flecha, todos los extremos con el "
        "giro liberado; articulación en N1, rodillo en N2 y P = {load_kn} kN hacia −Z en N3. "
        "Axiles por equilibrio de nudos; flecha por trabajos virtuales."
    ),
    "benchmark.note_orphan_rotations": (
        "Giros de nudo sin rigidez restringidos para el cálculo en {nodes} nudos (D3-A del plan "
        "T-010); su giro se publica como no aplicable."
    ),
    "benchmark.note_in_plane_status": "R_x′ en N3: estado {status} (se esperaba no aplicable).",
    "benchmark.hypothesis_spatial_formulation": (
        "Euler-Bernoulli 3D sin deformación por cortante, torsión uniforme de Saint-Venant sin "
        "alabeo, estática lineal, secciones constantes y cargas consistentes: las fórmulas son "
        "exactas en nudos y extremos."
    ),
    "benchmark.hypothesis_spatial_data": (
        "Acero E = {modulus_gpa:g} GPa, G = {shear_gpa:g} GPa, R {b_mm:g}×{h_mm:g}; A, Iy, Iz "
        "y J (= {torsion:.5g} m⁴, serie de Saint-Venant) de las propiedades de la sección."
    ),
    "benchmark.title_spatial_cantilever_y": "Voladizo espacial en +Y",
    "benchmark.title_spatial_cantilever_oblique": "Voladizo espacial oblicuo",
    "benchmark.title_bracket_vertical": "Ménsula acodada con carga vertical",
    "benchmark.title_bracket_horizontal": "Ménsula acodada con carga horizontal",
    "benchmark.title_portal_in_plane": "Pórtico biempotrado, carga en su plano",
    "benchmark.title_portal_out_of_plane": "Pórtico biempotrado, carga fuera de su plano",
    "benchmark.title_oblique_self_weight": "Voladizo oblicuo con peso propio",
    "benchmark.title_oblique_member_loads": "Voladizo oblicuo con cargas de barra local y global",
    "benchmark.description_spatial_cantilever_y": (
        "Voladizo de {length:g} m de O a J en +Y, empotrado en O. En J, el equivalente global "
        "de la carga local N = {n_kn:g} kN, Py = {py_kn:g} kN, Pz = {pz_kn:g} kN y "
        "T = {t_knm:g} kN·m."
    ),
    "benchmark.description_spatial_cantilever_oblique": (
        "Voladizo de {length:g} m de O al punto J ({x:g}, {y:g}, {z:g}) m, con alfa = {alpha:g} "
        "rad y empotrado en O. En J, el equivalente global de la carga local N = {n_kn:g} kN, "
        "Py = {py_kn:g} kN, Pz = {pz_kn:g} kN y T = {t_knm:g} kN·m."
    ),
    "benchmark.description_bracket_vertical": (
        "Ménsula acodada empotrada en O: B1 = O→A en +X ({l1:g} m) y B2 = A→C en +Y "
        "({l2:g} m). Carga P = {load_kn:g} kN hacia −Z en C (flexión y torsión)."
    ),
    "benchmark.description_bracket_horizontal": (
        "Ménsula acodada empotrada en O: B1 = O→A en +X ({l1:g} m) y B2 = A→C en +Y "
        "({l2:g} m). Carga H = {load_kn:g} kN en +X en C (Iz, A y el signo de ey = −X en B2)."
    ),
    "benchmark.description_portal_in_plane": (
        "Pórtico en XZ de {span:g} m de luz y {height:g} m de altura, con bases empotradas. "
        "H/2 en +X en cada nudo de dintel (H = {load_kn:g} kN), con deformación axil."
    ),
    "benchmark.description_portal_out_of_plane": (
        "Pórtico en XZ de {span:g} m de luz y {height:g} m de altura, con bases empotradas. "
        "P = {load_kn:g} kN en +Y solo en A1: flexión débil de los pilares acoplada con la "
        "torsión del dintel."
    ),
    "benchmark.description_oblique_self_weight": (
        "Voladizo oblicuo de {length:g} m con alfa = {alpha:g} rad, empotrado en O, bajo su peso "
        "propio (ρ·A·g del modelo)."
    ),
    "benchmark.description_oblique_member_loads": (
        "Voladizo oblicuo de {length:g} m con alfa = {alpha:g} rad, empotrado en O. Carga "
        "uniforme LOCAL ({local_kn_m}) kN/m y GLOBAL ({global_kn_m}) kN/m, con Wx ≠ 0."
    ),
    "benchmark.title_tripod_general": "Trípode espacial articulado, carga general",
    "benchmark.title_tripod_vertical": "Trípode espacial articulado, carga vertical",
    "benchmark.description_tripod": (
        "Vértice V ({apex}) m y bases empotradas A ({a}), B ({b}) y C ({c}) m; barras de "
        "base a vértice con Ry y Rz liberados en ambos extremos "
        "(preset articulado). Carga ({load_kn}) kN en V. Axiles de Σ N·e = P y desplazamiento "
        "de e·u = N·L/(EA); giro de V nulo."
    ),
    "benchmark.note_cross_planar": (
        "Contraste espacial-plano: modelo plano con Uy, Rx y Rz fijos en todos los nudos. "
        "Fórmulas sobre el 3D con 1e-9, y 3D frente a plano con {tolerance:g}; fuera del plano, "
        "frente a 0."
    ),
    "benchmark.hypothesis_settlement": (
        "Asientos impuestos (plan T-016): Euler-Bernoulli sin deformación por cortante y "
        "torsión de Saint-Venant, estática lineal, secciones constantes; dos elementos por vano. "
        "Fórmulas planas con EI = E·b·h³/12 y EA = E·b·h; espaciales con Iz y J de la sección."
    ),
    "benchmark.hypothesis_settlement_values": (
        "δ = {delta_mm:g} mm, θ = {theta:g} rad y δ axial = {axial_mm:g} mm, valores "
        "prescritos con signo en base global (ADR-001; δ < 0 es un descenso). La reacción es "
        "la acción del apoyo que los impone."
    ),
    "benchmark.title_settlement_fixed_fixed": "Biempotrada con asiento vertical",
    "benchmark.title_settlement_propped": "Empotrada-articulada con asiento vertical",
    "benchmark.title_settlement_simply_supported": "Biapoyada con asiento vertical (isostática)",
    "benchmark.title_settlement_rotation": "Biempotrada con giro impuesto",
    "benchmark.title_settlement_two_span": "Continua de dos vanos con asiento del apoyo central",
    "benchmark.title_settlement_axial": "Biempotrada con desplazamiento axial impuesto",
    "benchmark.title_settlement_torsion": "Biempotrada con giro de torsión impuesto (3D)",
    "benchmark.title_settlement_released": "Biempotrada con asiento en Uy y Rz liberado en J (3D)",
    "benchmark.title_settlement_spring_end": (
        "Biempotrada con asiento en Uy y resorte de giro en J (3D)"
    ),
    "benchmark.title_settlement_superposition": "Superposición de asiento y carga uniforme",
    "benchmark.description_settlement_fixed_fixed": (
        "Viga de {span:g} m en +X (B1 = N1→N2, B2 = N2→N3), empotrada en N1 y N3. Asiento "
        "Uz = {delta_mm:g} mm en N3."
    ),
    "benchmark.description_settlement_propped": (
        "Viga de {span:g} m en +X (B1 = N1→N2, B2 = N2→N3), empotrada en N1 y articulada en "
        "N3. Asiento Uz = {delta_mm:g} mm en N3."
    ),
    "benchmark.description_settlement_simply_supported": (
        "Viga de {span:g} m en +X (B1 = N1→N2, B2 = N2→N3), articulación en N1 y rodillo "
        "vertical (Uz) en N3. Asiento Uz = {delta_mm:g} mm en N3: giro de sólido rígido, sin "
        "esfuerzos ni reacciones (escalas declaradas 3EI|δ|/L³ y 3EI|δ|/L²)."
    ),
    "benchmark.description_settlement_rotation": (
        "Viga de {span:g} m en +X (B1 = N1→N2, B2 = N2→N3), empotrada en N1 y N3. Giro "
        "impuesto Ry = {theta:g} rad en N1."
    ),
    "benchmark.description_settlement_two_span": (
        "Continua de dos vanos de {span:g} m en +X (B1…B4, nudos N1…N5), articulación en N1 y "
        "rodillos verticales en N3 y N5. Asiento Uz = {delta_mm:g} mm en el apoyo central N3."
    ),
    "benchmark.description_settlement_axial": (
        "Viga de {span:g} m en +X (B1 = N1→N2, B2 = N2→N3), empotrada en N1 y N3. "
        "Desplazamiento impuesto Ux = +{delta_mm:g} mm en N3 (tracción)."
    ),
    "benchmark.description_settlement_torsion": (
        "Viga de {span:g} m en +X (B1 = N1→N2, B2 = N2→N3), empotrada en N1 y N3. Giro "
        "impuesto Rx = {theta:g} rad en N3; T = GJθ/L."
    ),
    "benchmark.description_settlement_released": (
        "Viga de {span:g} m en +X (B1 = N1→N2, B2 = N2→N3), empotrada en N1 y N3, con Rz "
        "liberado en el extremo J de B2. Asiento Uy = {delta_mm:g} mm en N3 (flexión en Iz)."
    ),
    "benchmark.description_settlement_spring_end": (
        "Viga de {span:g} m en +X (B1 = N1→N2, B2 = N2→N3), empotrada en N1 y N3, con resorte "
        "de giro Rz k = EIz/L = {stiffness:.6g} N·m/rad en el extremo J de B2 (nudo auxiliar). "
        "Asiento Uy = {delta_mm:g} mm en N3; referencia por condensación del giro del extremo."
    ),
    "benchmark.description_settlement_superposition": (
        "Viga de {span:g} m en +X (B1 = N1→N2, B2 = N2→N3), empotrada en N1 y N3. Tres "
        "corridas: A, asiento Uz = {delta_mm:g} mm en N3; Q, q = −{load_kn_m:g} kN/m en Z "
        "sobre B1 y B2; A+Q, las dos acciones en un mismo caso (no es una combinación)."
    ),
    "benchmark.note_settlement_alias": (
        "Control de asiento, {component} en {node}: |u − valor|/s = {ratio:.1e}, cota "
        "{bound:.1e} (s de los nudos de dominio: la razón es ≥ la evaluada por el control)."
    ),
    "benchmark.note_settlement_superposition": (
        "Filas «A + Q»: la corrida A+Q frente a la suma de las corridas A y Q en cada "
        "componente calculada; las fórmulas cerradas son la referencia independiente."
    ),
    "benchmark.hypothesis_peso_propio": (
        "Peso propio (plan T-019): Euler-Bernoulli, estática lineal, sección rectangular "
        "constante, dos elementos por vano; carga uniforme hacia −Z con q = γ·A o q = ρ·A·g. "
        "Referencias por cuerpo libre con EI = E·b·h³/12; γ y g son valores sintéticos, sin "
        "tabla normativa."
    ),
    "benchmark.hypothesis_peso_propio_values": (
        "γ = {gamma_kn_m3:g} kN/m³ (q = {q_gamma:g} N/m), g = {gravity:g} m/s² con ρ = 7850 "
        "kg/m³ (q = {q_density:g} N/m); las barras excluidas no se derivan ni cargan."
    ),
    "benchmark.title_peso_propio_gamma_fixed": "Biempotrada con peso propio por γ·A",
    "benchmark.title_peso_propio_density_fixed": (
        "Biempotrada con peso propio por ρ·A·g (modo por defecto)"
    ),
    "benchmark.title_peso_propio_excluded": "Voladizo con una barra excluida del peso propio",
    "benchmark.description_peso_propio_gamma_fixed": (
        "Viga de {span:g} m en +X (B1 = N1→N2, B2 = N2→N3), empotrada en N1 y N3, A = "
        "{area:g} m². Peso propio en modo γ con γ = {gamma_kn_m3:g} kN/m³: q = {q:g} N/m "
        "en B1 y B2."
    ),
    "benchmark.description_peso_propio_density_fixed": (
        "Misma viga y mismo modelo que la variante a, sin γ: peso propio por ρ·A·g con g = "
        "{gravity:g} m/s²: q = {q:g} N/m en B1 y B2."
    ),
    "benchmark.description_peso_propio_excluded": (
        "Voladizo de {span:g} m en +X (B1 = N1→N2, B2 = N2→N3), empotrado en N1, A = {area:g} "
        "m². Peso propio en modo γ con γ = {gamma_kn_m3:g} kN/m³ (q = {q:g} N/m) y B2 "
        "excluida: solo B1 pesa y B2 es un tramo sin peso que gira como sólido rígido."
    ),
    "benchmark.hypothesis_cargas_barra": (
        "Cargas de barra ampliadas (plan T-015): Euler-Bernoulli prismático, estática lineal; "
        "las fuerzas de empotramiento de las cargas nuevas las calcula Veritas y se aplican "
        "como cargas nodales equivalentes (ADR-005). Fórmulas planas con EI = E·b·h³/12 y "
        "EA = E·b·h; espaciales con J de la sección. Signos: carga hacia −Z da R_My < 0 en I."
    ),
    "benchmark.hypothesis_cargas_barra_values": (
        "Cargas de referencia: P = {load_kn:g} kN, w = {distributed_kn_m:g} kN/m, F axial = "
        "{axial_kn:g} kN y m = {moment_kn_m:g} kN·m/m. La reacción es la acción del apoyo."
    ),
    "benchmark.title_cargas_barra_fixed_point": "Biempotrada con carga puntual (fuerza X y Z)",
    "benchmark.title_cargas_barra_fixed_triangular": "Biempotrada con carga triangular",
    "benchmark.title_cargas_barra_cantilever_moment": "Voladizo con momento distribuido uniforme",
    "benchmark.title_cargas_barra_propped_point": (
        "Empotrada-articulada con carga puntual (extremo I liberado)"
    ),
    "benchmark.title_cargas_barra_triangular_root": (
        "Voladizo con triangular de máximo en el empotramiento"
    ),
    "benchmark.title_cargas_barra_triangular_tip": "Voladizo con triangular de máximo en la punta",
    "benchmark.title_cargas_barra_cantilever_point": "Voladizo con carga puntual interior",
    "benchmark.title_cargas_barra_torque_distributed": (
        "Voladizo con torsor distribuido uniforme (3D)"
    ),
    "benchmark.title_cargas_barra_torque_point": "Voladizo con par de torsión puntual (3D)",
    "benchmark.title_cargas_barra_subdivision_planar": (
        "Barra oblicua con extremo I liberado frente a la barra subdividida"
    ),
    "benchmark.title_cargas_barra_subdivision_spatial": (
        "Barra oblicua 3D con Rz liberado en J frente a la barra subdividida"
    ),
    "benchmark.description_cargas_barra_fixed_point": (
        "Viga de {span:g} m en +X (B1 = N1→N2, empotrada en N1 y N2; B2 = N2→N3 sin carga). "
        "Puntual en ξ = {xi:g}: Fz = −{load_kn:g} kN y Fx = +{axial_kn:g} kN."
    ),
    "benchmark.description_cargas_barra_fixed_triangular": (
        "Viga de {span:g} m en +X (B1 = N1→N2, empotrada en N1 y N2; B2 = N2→N3 sin carga). "
        "Carga triangular de 0 en I a −{load_kn_m:g} kN/m en Z en J."
    ),
    "benchmark.description_cargas_barra_cantilever_moment": (
        "Voladizo de {span:g} m en +X, empotrado en N1. Momento distribuido my = "
        "{moment_kn_m:g} kN·m/m en toda la barra."
    ),
    "benchmark.description_cargas_barra_propped_point": (
        "Viga de {span:g} m en +X, empotrada en N1 y N2, con Ry liberado en el extremo I de B1. "
        "Puntual Fz = −{load_kn:g} kN en ξ = {xi:g}."
    ),
    "benchmark.description_cargas_barra_triangular_root": (
        "Voladizo de {span:g} m en +X, empotrado en N1. Carga triangular de −{load_kn_m:g} kN/m "
        "en Z en I a 0 en J."
    ),
    "benchmark.description_cargas_barra_triangular_tip": (
        "Voladizo de {span:g} m en +X, empotrado en N1. Carga triangular de 0 en I a "
        "−{load_kn_m:g} kN/m en Z en J."
    ),
    "benchmark.description_cargas_barra_cantilever_point": (
        "Voladizo de {span:g} m en +X, empotrado en N1. Puntual Fz = −{load_kn:g} kN en ξ = {xi:g}."
    ),
    "benchmark.description_cargas_barra_torque_distributed": (
        "Voladizo de {span:g} m en +X, empotrado en N1. Torsor distribuido mx = "
        "{moment_kn_m:g} kN·m/m en toda la barra."
    ),
    "benchmark.description_cargas_barra_torque_point": (
        "Voladizo de {span:g} m en +X, empotrado en N1. Par de torsión Mx = {torque_kn_m:g} "
        "kN·m en ξ = {xi:g}."
    ),
    "benchmark.description_cargas_barra_subdivision_planar": (
        "Barra A→B oblicua de 5 m en el plano XZ, con A y C empotrados y la barra B→C. "
        "Puntual (fuerza) en ξ = 0,3, par en ξ = 0,6 y parcial uniforme en [0,2; 0,7], frente "
        "a la misma barra subdividida con cargas nodales y uniformes por tramo."
    ),
    "benchmark.description_cargas_barra_subdivision_spatial": (
        "Barra A→B oblicua en el espacio, con A y C empotrados, Rz liberado en J y la barra "
        "B→C. Puntual de seis componentes en ξ = 0,3 y parcial uniforme en [0,5; 0,8] (base "
        "local), frente a la misma barra subdividida con carga nodal y uniforme por tramo."
    ),
    "benchmark.note_cargas_barra_subdivision": (
        "Filas «subdividida»: la corrida con las cargas nuevas frente a la barra subdividida en "
        "los puntos de carga, que solo usa tipos ya aceptados; son la referencia independiente "
        "de las fuerzas de empotramiento."
    ),
    "section.property_unavailable": "La propiedad {property} de la sección no está disponible.",
    "section.property_unrepresentable": (
        "La propiedad {property} no es representable como valor positivo en doble precisión "
        "(resultado {value})."
    ),
    "norm.protected": (
        "La configuración de normativa «{name}» es protegida: no se edita ni se borra; "
        "cópiela para modificarla."
    ),
    "norm.in_use": (
        "La configuración de normativa «{name}» es la seleccionada del proyecto: "
        "seleccione otra antes de borrarla."
    ),
    "norm.duplicate_parameter": "Parámetro de normativa repetido: {key}.",
    "norm.duplicate_document": "Documento de normativa repetido: {document}.",
    "norm.invalid_key": (
        "Clave de parámetro no válida: «{key}» (se admiten minúsculas ASCII, dígitos, «_» y «.»)."
    ),
    "norm.protected_in_project": (
        "La configuración «{name}» es protegida y no puede guardarse como de usuario."
    ),
    "norm.reserved_id": "El identificador {entity_id} («{name}») está reservado a una protegida.",
    "norm.revision_sequence": (
        "Revisiones de normativa no consecutivas en {entity_id}: revisión {revision}, "
        "se esperaba la {expected}."
    ),
    "norm.selected_unknown": (
        "La normativa seleccionada {entity_id} no existe en el proyecto ni entre las "
        "protegidas de esta versión."
    ),
    "norm.cli_param_format": "Parámetro no válido «{spec}»: se espera CLAVE=VALOR (número).",
    "norm.cli_param_source": (
        "Cada --param exige su --fuente (cláusula o documento): {params} parámetro(s) y "
        "{sources} fuente(s)."
    ),
    "norm.cli_param_missing": "No existe el parámetro «{key}» que se quiere quitar.",
    "norm.cli_confirm_required": (
        "Borrar «{name}» no se puede deshacer: repita el comando con --confirmar."
    ),
    "norm.self_derived": "La configuración {entity_id} no puede derivar de sí misma.",
    "catalog.invalid_dimension_name": (
        "Nombre de dimensión de perfil no válido: «{name}» (minúscula ASCII, dígitos y «_»)."
    ),
    "catalog.duplicate_dimension": "Dimensión de perfil repetida: {name}.",
    "catalog.invalid_status": (
        "Estado {status} no admitido en la propiedad {field} de un perfil de catálogo: solo "
        "IMPORTADO_SIN_VERIFICAR o NO_DISPONIBLE."
    ),
    "catalog.invalid_warning": "Clave de aviso de perfil no válida o repetida: «{key}».",
    "catalog.invalid_document": (
        "Documento de perfil no válido en {path} (faltan: {missing}; sobran: {extra})."
    ),
    "catalog.database_error": "Error de la base de datos del catálogo.",
    "catalog.file_exists": "Ya existe el archivo {path}: no se sobrescribe.",
    "catalog.file_missing": "No existe el archivo de catálogo {path}.",
    "catalog.unreadable": "El archivo {path} no se puede leer como base de datos SQLite.",
    "catalog.not_a_catalog": "El archivo {path} no es un catálogo de Veritas: no se modifica.",
    "catalog.unsupported_version": (
        "Versión de catálogo {found} no soportada (esta versión admite hasta la {supported})."
    ),
    "catalog.migration_gap": "No hay migración de catálogo de la versión {step} a la {next}.",
    "catalog.store_closed": "El catálogo {path} está cerrado.",
    "catalog.name_taken": "Ya existe un catálogo llamado «{name}».",
    "catalog.protected": (
        "El catálogo «{catalog}» está protegido: no admite importar perfiles; se edita "
        "mediante un clon."
    ),
    "catalog.duplicate_key": (
        "Clave de perfil repetida en el lote: serie «{series}», designación «{designation}»."
    ),
    "catalog.not_found": "No existe el catálogo «{catalog}».",
    "catalog.profile_not_in_catalog": "El perfil «{profile}» no pertenece al catálogo «{catalog}».",
    "catalog.profile_not_found": "No existe el perfil «{profile}».",
    "catalog.verify_integrity": "El archivo de catálogo falla la comprobación de SQLite: {detail}.",
    "catalog.verify_orphan": "Fila huérfana en {table}: no existe su registro en {parent}.",
    "catalog.verify_header": "Cabecera del catálogo inesperada ({field}).",
    "catalog.verify_identity": (
        "La identidad del perfil es incoherente en la revisión {revision} "
        "(un mismo perfil con varias claves, o una clave con varios perfiles)."
    ),
    "catalog.verify_revision_sequence": ("Revisiones del perfil no consecutivas desde 1: {found}."),
    "catalog.verify_hash": "El hash de contenido de la revisión {revision} no coincide.",
    "catalog.verify_document": (
        "El documento de la revisión {revision} no es válido o no está en forma canónica."
    ),
    "catalog.verify_columns": (
        "Las columnas de consulta de la revisión {revision} no coinciden con su documento: "
        "{fields}."
    ),
    "catalog.mapping_json": (
        "El archivo de mapeo no es un JSON válido (sin claves repetidas ni NaN/Infinity)."
    ),
    "catalog.mapping_structure": "Mapeo no válido en {path}: se esperaba {expected}.",
    "catalog.mapping_format": "Formato de mapeo «{found}» no soportado; se esperaba {expected}.",
    "catalog.mapping_field_unknown": (
        "La columna «{column}» se asigna al campo desconocido «{field}». Campos: {known}."
    ),
    "catalog.mapping_field_repeated": (
        "Dos entradas alimentan el mismo campo {field}: «{first}» y «{second}»."
    ),
    "catalog.mapping_unit": (
        "Unidad no válida «{unit}» en la columna «{column}» (campo {field}): {expected}."
    ),
    "catalog.mapping_family": "Familia no válida «{value}» en {path}. Familias: {known}.",
    "catalog.mapping_required": "El mapeo no asigna ninguna columna al campo obligatorio {field}.",
    "catalog.mapping_column_unknown": (
        "Columna «{column}» desconocida en el formato nativo (sin mapeo). Campos: {known}."
    ),
    "catalog.mapping_column_missing": (
        "El archivo no tiene la columna «{column}» que el mapeo asigna a un campo."
    ),
    "catalog.import_duplicate_column": "El archivo repite la columna «{column}» en su cabecera.",
    "catalog.import_options": "Opción {option} no válida: «{value}» ({allowed}).",
    "catalog.import_format": (
        "No se reconoce el formato de {path}: use la extensión .csv o .json, o indique --formato."
    ),
    "catalog.import_missing": "No existe el archivo de origen {path}.",
    "catalog.import_unreadable": "No se puede leer el archivo de origen {path}.",
    "catalog.import_encoding": "El archivo {path} no está codificado en UTF-8.",
    "catalog.import_csv": "CSV mal formado en {path} (línea {line}).",
    "catalog.import_json": ("JSON mal formado en {path} (sin claves repetidas ni NaN/Infinity)."),
    "catalog.import_structure": "El JSON de {path} debe ser una lista de objetos.",
    "catalog.import_empty": "El archivo {path} no tiene filas de datos.",
    "catalog.row_width": "La fila tiene {found} celda(s) y la cabecera {expected}.",
    "catalog.row_value_type": "Valor de tipo no admitido en {field}: se esperaba texto o número.",
    "catalog.row_required": "Falta el valor obligatorio de {field}.",
    "catalog.row_not_number": (
        "{field}: «{value}» no es un número válido (sin nan/inf, guiones bajos ni separadores "
        "de miles)."
    ),
    "catalog.row_not_positive": "{field}: «{value}» debe ser mayor que 0.",
    "catalog.row_unrepresentable": "{field}: «{value}» no es representable en SI.",
    "catalog.row_family_unknown": "Familia «{value}» desconocida. Familias: {known}.",
    "catalog.row_duplicate_key": (
        "Clave repetida en el archivo: serie «{series}», designación «{designation}» (primera "
        "aparición en la línea {first_line}); se rechazan todas las filas con esa clave."
    ),
    "catalog.row_invalid": "Fila no válida: {detail}",
    "catalog.warn_mass_area": (
        "Aviso: la masa lineal difiere de A·ρ (acero, 7850 kg/m³) en más de ±2 %."
    ),
    "catalog.warn_area_dimensions": (
        "Aviso: A difiere en más de ±1 % del área de la I calculada con h, b, tw y tf (y con "
        "r si se da; sin r no cuenta los acuerdos) o las dimensiones son incoherentes."
    ),
    "catalog.warn_iy_less_than_iz": (
        "Aviso: Iy < Iz en un perfil I (posible intercambio de ejes)."
    ),
    "catalog.cli_option": "La opción {option} exige {requires}.",
    "catalog.invalid_batch": "Lote de importación no válido o incoherente ({field}).",
    "catalog.verify_batch": "El lote de importación {batch} es incoherente ({field}).",
    "catalog.search_range": "Rango no válido en {option}: «{value}» ({reason}).",
    "catalog.search_order": "Orden no válido: «{value}» ({allowed}).",
    "catalog.reference_invalid": (
        "Referencia de perfil no válida: «{value}» "
        "(se espera CATÁLOGO/SERIE/DESIGNACIÓN, con @REVISIÓN opcional)."
    ),
    "catalog.compare_count": (
        "Se comparan de {minimum} a {maximum} perfiles; se recibieron {count}."
    ),
    "catalog.verify_clone": "La trazabilidad de clonación de {item} es incoherente ({field}).",
    "catalog.user_file": (
        "El archivo de usuario {path} no es válido ({reason}); no se ha modificado."
    ),
    "catalog.output_exists": "Ya existe {path}: use --forzar para sobrescribirlo.",
    "catalog.output_error": "No se pudo escribir {path}.",
    "catalog.compare_repeated": "El perfil «{profile}» aparece más de una vez en la comparación.",
}


QUANTITY_KIND_LABELS_ES: Final[dict[str, str]] = {
    "LENGTH": "Longitud",
    "DISPLACEMENT": "Desplazamiento",
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
    "MOMENT_PER_LENGTH": "Momento por longitud",
    "SPECIFIC_WEIGHT": "Peso específico",
    "ANGLE": "Ángulo",
    "ROTATIONAL_STIFFNESS": "Rigidez rotacional",
    "DIMENSIONLESS": "Adimensional",
}


def render_text(key: str, params: Mapping[str, object] | None = None) -> str:
    """Texto en español de una clave con parámetros; si falta la clave, la propia clave."""
    template = MESSAGES_ES.get(key)
    if template is None:
        return key
    try:
        return template.format(**(params or {}))
    except (KeyError, IndexError, ValueError):
        return template


def render_message(diagnostic: Diagnostic) -> str:
    """Texto en español del diagnóstico; si falta la clave, se devuelve la propia clave."""
    return render_text(diagnostic.message_key, diagnostic.params_dict())
