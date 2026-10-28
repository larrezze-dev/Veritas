# Backlog de tickets

**Estado del proyecto:** inicio desde cero con Claude Code (2026-10-27). Ningún ticket iniciado.

Fuente: backlog sugerido por la SRS v1.1.0 (sección 18, S3D-001…012) y fases de `docs/ROADMAP.md`. Cada ticket se detalla con `tasks/TEMPLATE.md` y su plan en `plans/<ID-TICKET>.md` antes de implementarse. Los IDs SRS indican el aporte previsto; ningún ticket cierra un requisito sin evidencia (ver `docs/REQUIREMENTS_COVERAGE.md`).

Regla de avance: un ticket a la vez; no iniciar el siguiente sin la aceptación expresa del usuario del anterior.

## Tickets iniciales (F0–F1C)

| ID | SRS backlog | Fase | Título | Requisitos SRS (aporte previsto) | Dependencias | Estado |
|---|---|---|---|---|---|---|
| T-001 | S3D-001 | F0 | Repositorio, entorno Python, test runner, lint/tipado, CI y subagentes `.claude/agents/` | NFR-001, NFR-008 | — | ACEPTADO — 2026-10-27, aceptación expresa del usuario ([plan](../plans/T-001.md)) |
| T-002 | S3D-002 | F0 | Sistema de unidades SI y conversión de presentación | NFR-005 | T-001 | ACEPTADO — 2026-10-27, aceptación expresa del usuario ([plan](../plans/T-002.md)) |
| T-003 | S3D-003 | F1A | Node/Member, base local y orientación; extremos I/J rígidos por defecto | GEO-001, GEO-002, GEO-004, CON-001, CON-002 | T-002, ADR-001 | ACEPTADO (2026-10-27) |
| T-004 | S3D-004 | F1A | Revisiones de material y sección rectangular; un material y una sección por barra | MAT-001, MAT-002, SEC-001, SEC-002, SEC-003, GEO-003 | T-003 | ACEPTADO (2026-10-27) |
| T-005 | S3D-005 | F1B | Apoyos por GDL: rígidos, resortes, rodillos y base inclinada | GEO-010 (parcial) | T-004 | ACEPTADO (2026-10-27) |
| T-006 | S3D-006 | F1B | Casos de carga, cargas nodales y peso propio | LD-001, LD-002, LD-003 | T-005 | ACEPTADO (2026-10-27) ([plan](../plans/T-006.md)) |
| T-007 | S3D-007 | F1B | Adaptador OpenSeesPy en proceso aislado | FEM-001, FEM-003, FEM-005, FEM-008, FEM-010, NFR-003 | T-006, ADR-002 (aceptado 2026-10-27) | ACEPTADO (2026-10-27) ([plan](../plans/T-007.md)) |
| T-008 | S3D-008 | F1B | Resultados normalizados: desplazamientos, reacciones y esfuerzos de extremo | FEM-004, RES-001 | T-007 | ACEPTADO (2026-10-28) |
| T-009 | S3D-009 | F1B | Pruebas analíticas de referencia (biapoyada, voladizo, resorte, rodillo inclinado) y primera demostración | T-FEM-001 a T-FEM-004 (corregido en el plan T-009) | T-008 | ACEPTADO (2026-10-28, «aceptado, P1 a T-010») |
| T-010 | — | F1C | Liberaciones y resortes I/J, detección de mecanismo; incluye la «primera demostración solicitada» del ROADMAP (tres barras con materiales distintos y comparación con liberación de un extremo; P1 de T-009) | CON-003, CON-004, CON-005, CON-009, CON-011, CON-012, FEM-008 | T-009 | ACEPTADO (2026-10-28, «Acepto T-010 (y con ello F1C), confirmo R2 y R3») ([plan](../plans/T-010.md)) |

## Propuestas pendientes asignadas a tickets

Origen: revisión structural-reviewer de T-002 (ver `plans/T-002.md`). El usuario decidió (2026-10-27) evaluarlas en el plan de cada ticket; requieren aprobación allí.

| Ticket | Propuesta |
|---|---|
| T-003 | ~~Documentar en ADR-001 §5 que la división por magnitud nula usa `UNITS_UNREPRESENTABLE` (clave `units.division_by_zero`)~~ — resuelto en T-003 (P1, aceptado 2026-10-27) |
| T-004 | ~~Unidad kg/m (masa lineal de perfiles)~~ — incorporada en T-004 (D6; aceptado 2026-10-27) |
| ~~T-006~~ → F5A / ticket de γ | Magnitud `SURFACE_LOAD` (kN/m², separada de tensión/módulo) → F5A (paneles, LD-004/005); unidad kN/m³ (peso específico, DB-SE-AE) → ticket que introduzca γ o paneles. Aplazadas en T-006 (D7-A, aprobado 2026-10-27) |
| T-008 | Magnitud `DISPLACEMENT` (flechas en mm con perfil kN-m) |
| ~~T-009 / T-010~~ | ~~Estructura sin GDL libres~~ — resuelto en T-009 (D3-A): rechazo previo con `fem.no_free_dofs` |
| Por crear (FEM-002, con ADR) | Adaptador FEM 3D (seis GDL por nudo). ADR-002 limitó el primer adaptador al plano XZ; hace falta un ADR nuevo. Reutiliza ADR-001 y los vectores de 6 componentes de T-008. Origen: límites de T-009 |
| Por crear (tras la propiedad de área de cortante) | Elemento Timoshenko (deformación por cortante). Necesita el área de cortante Av de la sección, que hoy no se calcula (afecta a T-004) y requiere referencias propias. Origen: límites de T-009 |
| Por crear (con ADR) | Rodillo inclinado rígido sin penalización: restricciones multipunto (constraint handler Transformation). Error actual del orden de 1e-8. Origen: límites de T-009 |
| T-010 y siguientes de cada capacidad | Ampliar la batería de referencias analíticas: cercha y pórtico (SRS, puerta de F1: «viga/cercha/pórtico de referencia»), y casos propios para cada capacidad nueva (liberaciones, 3D, P-Delta). Una batería no equivale a una validación general. Origen: límites de T-009 |
| T-012 | Perfil «N-mm» no coherente N-mm-t: renombrar o documentar al persistir perfiles de usuario |
| Modal (por crear) | Unidades Hz/s y rigidez rotacional distribuida |
| F5A (ticket por crear) | Peso específico γ (kN/m³) como propiedad del material, para reproducir los pesos específicos tabulados de DB-SE-AE (por verificar). Cambia la API aceptada de `MaterialRevision` (T-004): requiere plan y aprobación. Origen: límite de T-006 (D4-C) |
| F5A (ticket por crear) | Selección de barras del peso propio (grupos, exclusiones). Hoy se aplica a todas las barras. Origen: límite de T-006 |

### Límites de T-010 y su subsanación (registrados a petición del usuario, 2026-10-28)

Cada línea requiere plan y aprobación propios antes de implementarse; ninguna está en curso.

| Ticket destino | Límite de T-010 | Subsanación propuesta |
|---|---|---|
| T-017 (nuevo, F1C+) | P1: ninguna postcondición comprueba el alias del aux ni la condición de extremo (revisión del código de E2, hallazgo 2) | Controles tras el cálculo: \|u(aux) − u(maestro)\| ≤ cota, \|M\| ≤ cota en extremo liberado, \|M − k·Δθ\| ≤ cota con resorte, `nodeReaction(aux)[2]` ≈ 0; fallo explícito si no se cumplen |
| T-017 | La localización del mecanismo es un único GDL y puede variar entre corridas (tags por orden de UUID) | Modo de mecanismo (espacio nulo con la misma LDLᵀ de la salvaguarda, D3-A del plan T-017) con la lista de nudos/extremos que participan, independiente del orden de tags; mensaje determinista |
| T-017 | La tabla de `veritas analyze uniones` no se contrasta automáticamente | Contraste con tolerancia de las columnas rígida y liberada (fórmulas ya impresas) y código 3 si no coincide, como `analyze referencias` |
| T-017 | Control de residuo K·u − P (O7 de T-007; resortes de rigidez extrema) | Residuo independiente con la K de la salvaguarda; complementa el aviso `fem.end_spring_near_rigid` |
| Adaptador 3D (por crear, con ADR) | Solo plano XZ: solo se traduce el giro de flexión del plano; el preset «articulado» (Ry y Rz) y la torsión se rechazan | Adaptador de seis GDL por nudo con liberaciones por GDL local; reutiliza `EndConnection`, el nudo auxiliar y la batería T-CON-REF |
| Adaptador 3D (T-017, D5-A) | Traslaciones de extremo liberadas o con resorte rechazadas (D2-C no elegida) | Restricción en ejes locales de la barra (`equalDOF` no basta en barras inclinadas): transformación local o elemento con condensación estática verificada, con referencias propias |
| F6 (resultados) | El giro propio del extremo de barra (aux) no se publica | Publicarlo normalizado a ADR-001 (θ = −Ry: invertir signo; revisión de E2, observación 6) como giro relativo extremo-nudo |
| F6 (resultados) | Precisión de presentación limitada por el perfil (flechas de centésimas de mm con 3 decimales) | Cifras significativas configurables por magnitud en los perfiles de presentación |
| T-012 (persistencia) | Uniones sin guardar/abrir (T-CON-005, CON-002 «persistentes») | Round-trip de los seis estados y rigideces por extremo |
| F4 / CON-007 | Sin biblioteca de presets versionada ni edición gráfica (CON-007, CON-008) | Presets de unión como entidades versionadas; inspector de extremo en la GUI |

## Tickets posteriores (por desglosar al llegar a su fase)

| ID | SRS backlog | Fase | Título | Requisitos SRS (aporte previsto) | Estado |
|---|---|---|---|---|---|
| T-011 | S3D-010 | F4 | Viewport mínimo PyVista | GEO-008 (parcial) | ABIERTO — orden 8; dependencias nuevas (PySide6, PyVista) a aprobar en su plan |
| T-012 | S3D-011 | F4 | Persistencia de proyecto versionado, round-trip y codificación canónica del hash | DAT-001, DAT-004, NFR-007 | ABIERTO — orden 6 (tras las entidades nuevas de T-015, T-016 y T-019) |
| T-013 | S3D-012 | F8 | Informe básico | RPT-002, RPT-004, RPT-005 | APLAZADO — necesita combinaciones, comprobaciones y versiones de catálogo (F2, F5A, F7A) |
| T-014 | — | F7A | Editor manual de configuración de normativas (crear, editar, copiar, borrar) | Requisito de `CLAUDE.md`, sin ID SRS | ABIERTO — orden 7; solo capa de datos (configuraciones protegidas y de usuario), el contenido normativo lo fija F7A |
| T-015 | — | F5A (antes de los paneles) | Cargas de barra ampliadas: puntuales, parciales, trapezoidales y momentos distribuidos | LD-002 (completar el aporte parcial de T-006) | ABIERTO — orden 3 |
| T-016 | — | F5A | Asientos impuestos en apoyos como acción de un caso de carga | GEO-010 / LD-002 (límite remitido por T-005 al ticket de cargas y no incluido en T-006) | ABIERTO — orden 2 |
| T-017 | — | F1C+ (antes de F2) | Endurecimiento de uniones y mecanismos: postcondiciones del aux (P1), modo de mecanismo determinista, contraste automático de `analyze uniones`, residuo K·u − P | CON-009, FEM-008 (límites de T-010) | ACEPTADO 2026-10-28 (E1 8ebbbb6; E2) ([plan](../plans/T-017.md)) |

| T-018 | — | FEM-3D (antes de F2) | Adaptador FEM 3D (seis GDL por nudo), con ADR-003. Absorbe: liberaciones por GDL local (Rz, torsión, preset «articulado»), traslaciones de extremo (D5-A de T-017), rodillo inclinado sin penalización y referencias de cercha y pórtico. El ADR decide si el adaptador plano se mantiene | FEM-002 (y GEO-010, CON-009 en 3D) | ABIERTO — orden 1 |
| T-019 | — | F5A (adelantado) | Peso específico γ del material y selección de barras del peso propio (cambia la API aceptada de `MaterialRevision`) | LD-003 | ABIERTO — orden 4 |
| T-020 | — | F6 (adelantado) | Resultados pendientes: giro propio del extremo de barra normalizado a ADR-001 y cifras significativas configurables por magnitud | RES-001 (parcial) | ABIERTO — orden 5 |

Las fases F2, F3A/B, F5A/B, F6, F7A/B y F8 se desglosarán en tickets cuando se acepte la fase anterior.

### Orden de planificación (decisión del usuario, 2026-10-28)

«ABIERTO» significa registrado para planificar; ningún ticket se implementa sin plan aprobado en `plans/`. Se planifican y entregan de uno en uno, en este orden: T-018 (3D, ADR-003) → T-016 → T-015 → T-019 → T-020 → T-012 → T-014 → T-011. Aplazados: T-013 y el elemento Timoshenko (necesita el área de cortante de F3A).

## Historial

- 2026-10-27: backlog inicial creado; todos los tickets NO INICIADO.
- 2026-10-27: registrados, a petición del usuario, los límites de T-006 sin planificación: T-015 (cargas de barra ampliadas), T-016 (asientos impuestos) y dos propuestas para F5A (peso específico γ, selección de barras del peso propio). No se registra la detección de cargas manuales que dupliquen el peso propio: no se puede decidir con certeza.
- 2026-10-28: T-009 amplía su alcance a T-FEM-001…004 (corrige la fila); propuesta P2 de T-008 resuelta en T-009 (D3-A).
- 2026-10-28: T-009 aceptado; P1 (primera demostración del ROADMAP) asignada a T-010; registradas, a petición del usuario, las propuestas de los límites de T-009 (adaptador 3D, Timoshenko, rodillo sin penalización, ampliación de la batería de referencias).
- 2026-10-28: T-010 aceptado (F1C ACEPTADA); R2 y R3 confirmados. Registrados, a petición del usuario, P1 y la planificación de la subsanación de los límites de T-010; nuevo ticket T-017 (endurecimiento de uniones y mecanismos).
- 2026-10-28: T-017 elegido por el usuario como siguiente ticket; plan aprobado (D1–D7 = A, sin O1). D5-A: las traslaciones de extremo liberadas pasan al adaptador 3D.
- 2026-10-28: T-017 E1 aceptada por el usuario (commit 8ebbbb6). E2 (modo de mecanismo) aceptada el mismo día; T-017 ACEPTADO.
- 2026-10-28: a petición del usuario («abre todos los tickets pendientes que puedan resolverse con lo desarrollado»), se abren T-011, T-012, T-014, T-015 y T-016 y se crean T-018 (adaptador 3D con ADR-003), T-019 (γ y selección del peso propio) y T-020 (resultados pendientes). Orden «3D primero». T-013 y Timoshenko aplazados. Las filas de propuestas y límites asignadas al adaptador 3D, a F5A (γ, peso propio) y a F6 (giro del aux, precisión) pasan a T-018, T-019 y T-020.
