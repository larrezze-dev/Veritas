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
    "load.self_weight_duplicate": (
        "El modelo ya tiene un peso propio (caso {case_id}); solo se admite uno. "
        "Retírelo antes de definirlo en otro caso."
    ),
    "load.self_weight_category": (
        "El peso propio solo puede estar en un caso permanente; el caso «{case}» sería {category}."
    ),
    "load.self_weight_missing": "El modelo no tiene peso propio definido.",
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
    "fem3d.member_end_not_rigid": (
        "El extremo {end} de la barra {member} no es rígido ({dof}: {state}); el análisis "
        "espacial solo admite por ahora extremos rígidos."
    ),
    "fem3d.end_axial_with_axial_load": (
        "La barra {member} tiene Ux de extremo liberado o con resorte y carga axial distribuida "
        "en el caso (Wx = {wx} N/m, incluido el peso propio de una barra inclinada); el "
        "análisis espacial no admite esa combinación."
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
        "Al menos un giro fijo parcial en base oblicua o una componente rígida de un extremo "
        "oblicuo se representa por penalización; el resultado lleva la tolerancia de "
        "penalización."
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
    "benchmark.note_cross_planar": (
        "Contraste espacial-plano: modelo plano con Uy, Rx y Rz fijos en todos los nudos. "
        "Fórmulas sobre el 3D con 1e-9, y 3D frente a plano con {tolerance:g}; fuera del plano, "
        "frente a 0."
    ),
    "section.property_unavailable": "La propiedad {property} de la sección no está disponible.",
    "section.property_unrepresentable": (
        "La propiedad {property} no es representable como valor positivo en doble precisión "
        "(resultado {value})."
    ),
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
