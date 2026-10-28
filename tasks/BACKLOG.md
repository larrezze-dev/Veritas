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
| T-010 | — | F1C | Liberaciones y resortes I/J, detección de mecanismo; incluye la «primera demostración solicitada» del ROADMAP (tres barras con materiales distintos y comparación con liberación de un extremo; P1 de T-009) | CON-003, CON-004, CON-009, CON-011, FEM-008 | T-009 | NO INICIADO |

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

## Tickets posteriores (por desglosar al llegar a su fase)

| ID | SRS backlog | Fase | Título | Requisitos SRS (aporte previsto) | Estado |
|---|---|---|---|---|---|
| T-011 | S3D-010 | F4 | Viewport mínimo PyVista | GEO-008 (parcial) | NO INICIADO |
| T-012 | S3D-011 | F4 | Persistencia de proyecto versionado, round-trip y codificación canónica del hash | DAT-001, DAT-004, NFR-007 | NO INICIADO |
| T-013 | S3D-012 | F8 | Informe básico | RPT-002, RPT-004, RPT-005 | NO INICIADO |
| T-014 | — | F7A | Editor manual de configuración de normativas (crear, editar, copiar, borrar) | Requisito de `CLAUDE.md`, sin ID SRS | NO INICIADO |
| T-015 | — | F5A (antes de los paneles) | Cargas de barra ampliadas: puntuales, parciales, trapezoidales y momentos distribuidos | LD-002 (completar el aporte parcial de T-006) | NO INICIADO |
| T-016 | — | F5A | Asientos impuestos en apoyos como acción de un caso de carga | GEO-010 / LD-002 (límite remitido por T-005 al ticket de cargas y no incluido en T-006) | NO INICIADO |

Las fases F2, F3A/B, F5A/B, F6, F7A/B y F8 se desglosarán en tickets cuando se acepte la fase anterior.

## Historial

- 2026-10-27: backlog inicial creado; todos los tickets NO INICIADO.
- 2026-10-27: registrados, a petición del usuario, los límites de T-006 sin planificación: T-015 (cargas de barra ampliadas), T-016 (asientos impuestos) y dos propuestas para F5A (peso específico γ, selección de barras del peso propio). No se registra la detección de cargas manuales que dupliquen el peso propio: no se puede decidir con certeza.
- 2026-10-28: T-009 amplía su alcance a T-FEM-001…004 (corrige la fila); propuesta P2 de T-008 resuelta en T-009 (D3-A).
- 2026-10-28: T-009 aceptado; P1 (primera demostración del ROADMAP) asignada a T-010; registradas, a petición del usuario, las propuestas de los límites de T-009 (adaptador 3D, Timoshenko, rodillo sin penalización, ampliación de la batería de referencias).
