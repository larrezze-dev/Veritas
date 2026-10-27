# Backlog de tickets

**Estado del proyecto:** inicio desde cero con Claude Code (2026-10-27). Ningún ticket iniciado.

Fuente: backlog sugerido por la SRS v1.1.0 (sección 18, S3D-001…012) y fases de `docs/ROADMAP.md`. Cada ticket se detalla con `tasks/TEMPLATE.md` y su plan en `plans/<ID-TICKET>.md` antes de implementarse. Los IDs SRS indican el aporte previsto; ningún ticket cierra un requisito sin evidencia (ver `docs/REQUIREMENTS_COVERAGE.md`).

Regla de avance: un ticket a la vez; no iniciar el siguiente sin la aceptación expresa del usuario del anterior.

## Tickets iniciales (F0–F1C)

| ID | SRS backlog | Fase | Título | Requisitos SRS (aporte previsto) | Dependencias | Estado |
|---|---|---|---|---|---|---|
| T-001 | S3D-001 | F0 | Repositorio, entorno Python, test runner, lint/tipado, CI y subagentes `.claude/agents/` | NFR-001, NFR-008 | — | ACEPTADO — 2026-10-27, aceptación expresa del usuario ([plan](../plans/T-001.md)) |
| T-002 | S3D-002 | F0 | Sistema de unidades SI y conversión de presentación | NFR-005 | T-001 | NO INICIADO |
| T-003 | S3D-003 | F1A | Node/Member, base local y orientación; extremos I/J rígidos por defecto | GEO-001, GEO-002, GEO-004, CON-001, CON-002 | T-002, ADR-001 | NO INICIADO |
| T-004 | S3D-004 | F1A | Revisiones de material y sección rectangular; un material y una sección por barra | MAT-001, MAT-002, SEC-001, SEC-002, SEC-003, GEO-003 | T-003 | NO INICIADO |
| T-005 | S3D-005 | F1B | Apoyos por GDL global | GEO-010 (parcial) | T-004 | NO INICIADO |
| T-006 | S3D-006 | F1B | Casos de carga, cargas nodales y peso propio | LD-001, LD-002, LD-003 | T-005 | NO INICIADO |
| T-007 | S3D-007 | F1B | Adaptador OpenSeesPy en proceso aislado | FEM-001, FEM-003, FEM-005, FEM-008, FEM-010, NFR-003 | T-006, ADR-002 (aprobación pendiente) | NO INICIADO |
| T-008 | S3D-008 | F1B | Resultados normalizados: desplazamientos, reacciones y esfuerzos de extremo | FEM-004, RES-001 | T-007 | NO INICIADO |
| T-009 | S3D-009 | F1B | Pruebas analíticas de referencia (viga biapoyada, voladizo) y primera demostración | T-FEM-001, T-FEM-002 | T-008 | NO INICIADO |
| T-010 | — | F1C | Liberaciones y resortes I/J, detección de mecanismo | CON-003, CON-004, CON-009, CON-011, FEM-008 | T-009 | NO INICIADO |

## Tickets posteriores (por desglosar al llegar a su fase)

| ID | SRS backlog | Fase | Título | Requisitos SRS (aporte previsto) | Estado |
|---|---|---|---|---|---|
| T-011 | S3D-010 | F4 | Viewport mínimo PyVista | GEO-008 (parcial) | NO INICIADO |
| T-012 | S3D-011 | F4 | Persistencia de proyecto versionado, round-trip y codificación canónica del hash | DAT-001, DAT-004, NFR-007 | NO INICIADO |
| T-013 | S3D-012 | F8 | Informe básico | RPT-002, RPT-004, RPT-005 | NO INICIADO |
| T-014 | — | F7A | Editor manual de configuración de normativas (crear, editar, copiar, borrar) | Requisito de `CLAUDE.md`, sin ID SRS | NO INICIADO |

Las fases F2, F3A/B, F5A/B, F6, F7A/B y F8 se desglosarán en tickets cuando se acepte la fase anterior.

## Historial

- 2026-10-27: backlog inicial creado; todos los tickets NO INICIADO.
