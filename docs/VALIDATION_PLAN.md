# Plan de validación estructural y pruebas

**Estado del proyecto:** F1A en curso. Los casos marcados `SUPERADO` tienen tests automatizados y aceptación del usuario (T-003, 2026-10-27); el resto sigue `NO INICIADO`.

## Pruebas escalonadas

1. Geometría/unidades: nodos coincidentes, barra de longitud cero, eje local, transformaciones SI/presentación.
2. Perfiles: rectángulo, anillo, I, L y contorno con hueco; A, centroide e inercias mediante fórmulas independientes.
3. Materiales: datos dimensionales y ausencia de dos materiales por barra.
4. Conexiones: rígido I/J por defecto; release aislado; resortes rotacionales; offsets; mecanismo detectado.
5. FEM: viga biapoyada, voladizo, cercha, pórtico 2D y 3D, equilibrio de reacciones.
6. Cargas/paneles: equilibrio de fuerzas y momentos; peso propio sin duplicación; combinaciones.
7. Resultados: signos, estaciones, desplazamientos, envolventes y stale status por revisión.
8. CTE: cada fórmula contra documentación oficial y casos de referencia con edición identificada.
9. Persistencia/informes: round-trip, migraciones, pdf sin omitir advertencias.

## Fixtures iniciales

| Fixture | Hipótesis | Resultado de referencia | Fase prevista | Estado |
|---|---|---|---|---|
| rectangle_section | b, h | A=bh, segundo momento analítico según ejes (ADR-001: b en y, h en z) | F1A | NO INICIADO |
| ring_section | radio exterior R, interior r | A=π(R²−r²), I=π(R⁴−r⁴)/4 | F3A | NO INICIADO |
| self_weight_prismatic (propuesto) | Barra I=(0,0,0) m, J=(2,0,0) m; b=0,2 m, h=0,4 m, ρ=1000 kg/m³, g=10 m/s² sintética hacia −Z | qz=−800 N/m; resultante Fz=−1600 N (acción prescrita, no resultado de análisis) | F1B | NO INICIADO |
| beam_simple_center | L, P, E, I, biapoyada | R1=R2=P/2, Mmax=PL/4 | F1B | NO INICIADO |
| cantilever_uniform | viga Euler-Bernoulli, q uniforme | δ=qL⁴/(8EI) en extremo | F1B | NO INICIADO |
| release_rotation | misma viga con/sin liberación | respuesta esperada y momento liberado | F1C | NO INICIADO |
| spring_limits | rigidez pequeña/intermedia/muy grande | convergencia a límite articulado/rígido sin singularidades espurias | F1C | NO INICIADO |
| failed_mechanism | GDL suficientes liberados | cálculo rechazado, mensaje comprensible | F1C | NO INICIADO |
| panel_rect | área y presión conocidas | ΣF y ΣM conservados | F5A | NO INICIADO |

**Tolerancias:** fijarlas por caso y justificación numérica, no usar un epsilon global indiscriminado. En casos de malla/discretización, demostrar convergencia. Documentar convenciones de signos. Las referencias se derivan de fuentes independientes (fórmula analítica, cuerpo libre, bibliografía citada), nunca copiando la salida del propio código.

## Pruebas de aceptación SRS (sección 16)

| Test ID | Entrada | Expectativa | Fase prevista | Estado |
|---|---|---|---|---|
| T-GEO-001 | Barra entre nudos coincidentes | Rechazo explicativo | F1A | SUPERADO (T-003 aceptado 2026-10-27: `tests/test_geometry_model.py`) |
| T-CON-001 | Crear barra y examinar I/J | Ambos extremos rígidos por defecto | F1A | SUPERADO (T-003 aceptado 2026-10-27: `tests/test_geometry_model.py`) |
| T-CON-002 | Liberar giro solo en I | J y otras barras del nudo no cambian | F1C | NO INICIADO |
| T-CON-003 | Resorte rotacional con rigidez finita | Respuesta consistente entre límites liberado/rígido | F1C | NO INICIADO |
| T-CON-004 | Configuración que introduce mecanismo | Diagnóstico de inestabilidad; sin cálculo válido | F1C | NO INICIADO |
| T-CON-005 | Guardar/abrir uniones personalizadas | Coinciden seis estados, rigideces y offsets de I/J | F4 | NO INICIADO |
| T-MAT-001 | Intentar dos materiales en una barra | Invariante impide operación | F1A | SUPERADO (T-004 aceptado 2026-10-27: `tests/test_model_assignment.py`) |
| T-SEC-001 | Rectángulo b×h | Área y momentos analíticos correctos | F1A | SUPERADO (T-004 aceptado 2026-10-27: `tests/test_sections_rectangular.py`) |
| T-SEC-002 | Anillo con radios R,r | A=π(R²−r²), I=π(R⁴−r⁴)/4 | F3A | NO INICIADO |
| T-SEC-003 | Polígono con hueco | Área neta y centroide correctos | F3B | NO INICIADO |
| T-CAT-001 | CSV con unidades inválidas | Importación informada y sin corrupción | F2 | NO INICIADO |
| T-FEM-001 | Viga biapoyada, P central | Reacciones P/2, Mmax=PL/4 | F1B | NO INICIADO |
| T-FEM-002 | Voladizo con q | Flecha extremo qL⁴/(8EI) bajo hipótesis adecuadas | F1B | NO INICIADO |
| T-FEM-003 | Apoyo con resorte traslacional k (barra isostática) | Desplazamiento del apoyo δ = R/k; reacción R = −k·u en la base del apoyo (plan T-005, R5/O5) | F1B (T-009) | NO INICIADO |
| T-FEM-004 | Rodillo inclinado | Reacción normal al plano de rodadura (según ez′), nula en el plano; equilibrio global (plan T-005, R5) | F1B (T-009) | NO INICIADO |
| T-LD-001 | Panel uniforme | Equilibrio ΣF y ΣM | F5A | NO INICIADO |
| T-COM-001 | Dos casos lineales | Superposición correcta | F5A | NO INICIADO |
| T-COM-002 | Caso no lineal | Rechaza superposición inapropiada | F5A | NO INICIADO |
| T-RES-001 | Cambiar perfil tras cálculo | Resultados marcados obsoletos | F6 | NO INICIADO |
| T-NOR-001 | Falta propiedad necesaria | NO_COMPROBADO, nunca APROBADO | F7A | NO INICIADO |
| T-RPT-001 | Informe completo | Unidades, revisiones, método, avisos y datos | F8 | NO INICIADO |

La fase prevista es orientativa; el ticket concreto se asigna en `tasks/BACKLOG.md`.

## Verificación CTE

Origen prioritario oficial: https://www.codigotecnico.org/DocumentosCTE/SeguridadEstructural.html

Implementar primero DB-SE, DB-SE-AE, DB-SE-A y DB-SE-M, previa identificación de edición legal aplicable. CTE no posee DB específico de aluminio: no fabricar reglas “CTE-Al”. Los Eurocódigos se introducen solo después de su decisión de fase.

Cada chequeo normativo deberá incluir identificador de disposición, cláusula, variables, unidades, cálculo, validaciones de ámbito, ratio y estado PASS/FAIL/NOT_CHECKED/NOT_APPLICABLE/ERROR. Los resultados de un LLM (incluido Claude) no son referencias independientes.

## Pruebas de usuario

Cada ticket incorpora un README de demo, comandos PowerShell, datos pequeños, salida esperada y checklist de 5–10 minutos. Para GUI, indicar acciones precisas y señales de error. El usuario revisa y acepta antes del ticket siguiente.

## Antirregresiones

Toda corrección FEM/geométrica necesita test que reproduzca el error anterior. Las versiones de fixtures y referencias deben vivir en tests/ junto al código y quedar registradas en el ticket.

## Trazabilidad prevista de contratos ADR-001

[ADR-001](adr/ADR-001-convenciones-dominio.md) fue **aprobado** por el usuario el 2026-10-27. Esta matriz planifica dónde se implementará y probará cada contrato; no acredita implementación ni validación. La fase responsable es orientativa hasta que exista el backlog.

| Contrato ADR-001 | Fase responsable | Casos de test previstos | Estado |
|---|---|---|---|
| Ejes, base local, GDL y transformación (sección 1) | F1A (geometría de nudos/barras); F1B (adaptación FEM y transformación de respuesta) | +X, +Y, −X (ez arriba), vertical con fallback −X, giro de 90°, referencia explícita inválida, umbral 1e-8 por debajo/en/encima, referencia guardada degenerada, inversión I/J, coordenadas no finitas, longitud cero; verificar base derecha, sentido de Q y transformaciones inversas. | F1A SUPERADO (T-003 aceptado 2026-10-27: `tests/test_local_axes.py`); F1B NO INICIADO |
| Ejes centroidales e inercias de sección (sección 1, «Ejes de sección») | F1A (sección rectangular); F2/F3A (perfiles y propiedades posteriores); F1B (uso en FEM) | Rectángulo centrado con b en y y h en z; referencia independiente para centroide, A, Iy, Iz e Iyz; comprobar convención de signos y rechazo/no disponibilidad sin sustituir por cero. | NO INICIADO |
| Signos de cargas, reacciones, fuerzas de extremo y esfuerzos de sección (sección 2) | F1B (apoyos, cargas, respuestas de elemento y pruebas de referencia); F6 (presentación de resultados) | Derivar por cuerpos libres tracción axial y voladizo con carga −Z; comprobar fuerzas y momentos de reacción, relación de esfuerzos de extremo y sección, equilibrio y discontinuidad en carga concentrada distinguiendo s- y s+. | NO INICIADO |
| Identidad UUID, referencias y revisiones inmutables (sección 3 y tabla de sección 4) | F1A según entidad; **pendiente: ticket explícito de persistencia** (unicidad, transacciones, round-trip) | Copia crea ID nuevo; edición conserva ID y aumenta revisión; referencia antigua conserva contenido; conflicto mismo par con distinto contenido; rechazo no sobrescribe; etiqueta y tag de solver no sustituyen identidad. | NO INICIADO |
| Versiones, procedencia/hash y vigencia del resultado (sección 4) | F1B (procedencia de cada ejecución); F6 (consumo de resultados); **pendiente: ticket explícito de persistencia/codificación canónica del hash** | Revisión analítica cambia la procedencia; cambio solo de presentación no la cambia; ejecuciones repetidas reciben run ID distinto; cambio de configuración/versiones o snapshot marca resultado obsoleto; estabilidad de codificación y casos −0.0/float no finito cuando el contrato canónico esté especificado. | NO INICIADO |
| Diagnósticos y errores explícitos (sección 5) | F0 (fronteras de unidades/CLI); F1A (errores geométricos); F1B (configuración/fallo de análisis); persistencia: ticket pendiente | Tipo incorrecto/no finito, longitud cero/orientación inválida, referencia no encontrada, conflicto de revisión, configuración no soportada, singularidad/no convergencia/fallo; comprobar código estable, clave traducible, causa conservada y que no se publica resultado válido tras error. | NO INICIADO |

Los tests de esta matriz se implementarán junto con cada ticket responsable y usarán referencias independientes; no se añaden tests que solo comparen constantes copiadas del ADR. Una revisión algebraica por un agente (p. ej. structural-reviewer) no constituye test automatizado, resultado de solver ni auditoría externa.
