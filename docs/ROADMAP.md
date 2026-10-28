# Roadmap guiado por puertas de aceptación

**Estado del proyecto:** inicio desde cero con Claude Code. Ninguna fase iniciada.

El Product Owner puede probar cada fase y detener el desarrollo sin invalidar los módulos ya aprobados.

| Fase | Unidad demostrable | Gate del usuario | Estado |
|---|---|---|---|
| F0 | Estructura proyecto, entorno, test runner, sistema de unidades, configuración, subagentes de Claude Code y plantillas de trabajo | Ejecución de test de ejemplo en Windows | ACEPTADA — 2026-10-27, aceptación expresa del usuario (T-001 y T-002) |
| F1A | Node/Member, material, sección rectangular, conexión rígida I/J | CLI o pequeña vista de barras y propiedades | ACEPTADA — 2026-10-27, aceptación expresa del usuario (T-003 y T-004) |
| F1B | Apoyos y cargas nodales/barras, adaptador OpenSeesPy | Viga de referencia, reacciones/desplazamientos verificables | ACEPTADA 2026-10-28 (T-005 a T-009 aceptados; puerta superada con `veritas analyze referencias`, T-FEM-001…004; la «primera demostración solicitada» pasa a T-010) |
| F1C | Cambio de liberación I/J, resorte rotacional y errores por mecanismo | Usuario compara tres uniones y verifica mensajes | ACEPTADA 2026-10-28 (T-010 aceptado: «Acepto T-010 (y con ello F1C)»; puerta superada con `veritas analyze uniones`, `analyze demo --caso tres-materiales` y T-CON-REF-01…04) |
| F2 | Catálogo SQLite, importador y tabla buscable | Importación 100 perfiles, filtros y asignación | NO INICIADA |
| F3A | Editor paramétrico de perfiles y propiedades básicas | Rectángulo/I/tubo con medidas y A/I verificadas | NO INICIADA |
| F3B | Croquis libre, huecos, validación y versiones | Sección con hueco y error por autointersección | NO INICIADA |
| F4 | Modelador 3D, inspector, snapping, undo/redo, guardado | Montar pórtico sin código y editar uniones | NO INICIADA |
| F5A | Casos, combinaciones manuales y panel simple | Reparto y equilibrio visibles | NO INICIADA |
| F5B | Generadores CTE DB-SE-AE viento/nieve con referencias | Casos de cálculo verificados y editables | NO INICIADA |
| F6 | Diagramas, deformadas, filtros, envolventes y tablas | Análisis tipo RSTAB sobre modelo de muestra | NO INICIADA |
| F7A | DB-SE/DB-SE-AE y chequeos DB-SE-A de acero; editor manual de configuración de normativas (crear, editar, copiar, borrar)¹ | Ratio y cláusula de acero reproducibles; crear/copiar/borrar una configuración normativa sin afectar a las protegidas | NO INICIADA |
| F7B | DB-SE-M madera y ampliaciones verificadas | Comprobaciones madera trazables; aluminio NO_COMPROBADO | NO INICIADA |
| F8 | Informes, packaging Windows, QA y recuperación | PDF completo y test end-to-end | NO INICIADA |

¹ Requisito procedente de `CLAUDE.md` (principios no negociables); no tiene ID en la SRS v1.1.0. Ticket T-014 en `tasks/BACKLOG.md`; no se modifica la SRS sin aprobación.

**F1C+ (T-017, antes de F2; [plan](../plans/T-017.md)):** endurecimiento de uniones y mecanismos. E1 (controles de la solución tras el equilibrio y contraste de `veritas analyze uniones`) ACEPTADA por el usuario el 2026-10-28 (commit 8ebbbb6). E2 (modo de mecanismo determinista) ACEPTADA el 2026-10-28. T-017 ACEPTADO. No altera el estado aceptado de F1C.

**Tickets abiertos para planificar (decisión del usuario, 2026-10-28):** se adelantan, antes de F2, los que solo dependen de F0–F1C. Orden: T-018 (adaptador 3D, ADR-003), T-016, T-015, T-019, T-020, T-012, T-014 y T-011. Aplazados: T-013 y Timoshenko. Detalle en `tasks/BACKLOG.md`. Las puertas de fase no cambian. T-018: plan y [ADR-003](adr/ADR-003-adaptador-espacial-3d.md) aprobados el 2026-10-28 ([plan](../plans/T-018.md)); E1 pendiente.

Estados admitidos: `NO INICIADA`, `EN CURSO`, `EN VALIDACIÓN` (entregada, esperando aceptación del usuario), `ACEPTADA` (con fecha y referencia a la aceptación expresa) y `RECHAZADA` (en corrección dentro de la misma fase).

## Prioridades internas

P0: dominio/solver mínimo, uniones rígidas por defecto, unidades y tests. P1: editor, catálogos, conexiones avanzadas, cargas/paneles, filtros, normativa CTE e informes. P2: Eurocódigos, SIA, perfiles avanzados, importaciones complejas.

## Arranque del proyecto con Claude Code (F0)

1. Leer `CLAUDE.md`, [DECISIONS.md](DECISIONS.md), [ARCHITECTURE.md](ARCHITECTURE.md) y este documento.
2. Backlog inicial en `tasks/BACKLOG.md`, plantillas `tasks/TEMPLATE.md` y `handoffs/TEMPLATE.md` y carpeta `plans/` creados el 2026-10-27. Backlog sugerido por la SRS (sección 18): repositorio/CI, sistema de unidades, Node/Member, materiales y sección rectangular, apoyos, cargas, adaptador OpenSeesPy, resultados, pruebas analíticas, viewport mínimo, persistencia e informe básico. Los IDs de ticket se asignan en el backlog, no en estos documentos.
3. [ADR-001](adr/ADR-001-convenciones-dominio.md) aprobado el 2026-10-27. [ADR-002](adr/ADR-002-adaptador-plano-proceso-solver.md) aprobado el 2026-10-27, con la revisión DEC-011; se implementa en T-007.
4. Subagentes del proyecto definidos en `.claude/agents/` (T-001) según [AGENT_POLICY.md](AGENT_POLICY.md).
5. Primer ticket: plan en `plans/<ID-TICKET>.md`, aprobación del usuario, implementación, demo y aceptación según [MODULE_CONTRACT.md](MODULE_CONTRACT.md).

## Regla de aprobación

No declarar «fase concluida» sin: pruebas automatizadas, demo en Windows, lista de requisitos cerrados, limitaciones y aceptación expresa del usuario. Tras un módulo rechazado se trabaja en correcciones del mismo módulo. Al aceptar una fase, actualizar su estado aquí, en [REQUIREMENTS_COVERAGE.md](REQUIREMENTS_COVERAGE.md) y en el backlog.

## Primera demostración solicitada

Tres barras con materiales distintos, una sección por barra, conexiones rígidas I/J y una carga nodal; ejecutar análisis y mostrar desplazamientos y reacciones, incluso mediante UI mínima o CLI reproducible. Luego comparar con liberaciones de un extremo.
