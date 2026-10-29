# Decisiones aprobadas — Veritas Structural Calculation 3D

Fecha de base: 2026-10-24. Fuente: decisiones del Product Owner incorporadas en SRS v1.1.0 (nombre de trabajo en la SRS: Structural3D).

| ID | Decisión | Estado |
|---|---|---|
| DEC-001 | Windows local, PySide6 + PyVista/VTK + Python + OpenSeesPy | Aprobada |
| DEC-002 | España: CTE Seguridad Estructural prioritario (DB-SE, DB-SE-AE, DB-SE-A, DB-SE-M) | Aprobada |
| DEC-003 | Español primero, internacionalización preparada | Aprobada |
| DEC-004 | Catálogos europeos de acero primero, ampliación a aluminio/madera | Aprobada |
| DEC-005 | Editor A/centroide/inercias primero, J/Cw solo por método validado | Aprobada |
| DEC-006 | Licencia del producto se decide tras auditoría | Proceso aprobado; licencia pendiente |
| DEC-007 | CSV/JSON primero, DXF después | Aprobada |
| DEC-008 | Hardware objetivo y rendimiento se fijan por benchmark | Proceso aprobado; cifras pendientes |
| DEC-009 | Uniones rígidas por defecto I/J; liberaciones y resortes editables en seis GDL | Aprobada |
| DEC-010 | Un material por barra; sin detalles constructivos de uniones | Aprobada |
| DEC-011 | Python 3.12 x64 para todo el proyecto (`requires-python ==3.12.*`), por compatibilidad con OpenSeesPy 3.8.0.0 en Windows; entorno con uv y `uv.lock`; código en `src/veritas/` | Aprobada 2026-10-27 (plan T-001). Revisar cuando OpenSeesPy soporte versiones posteriores o NumPy/SciPy abandonen 3.12 |

## ADR

| ADR | Contrato | Estado |
|---|---|---|
| [ADR-001](adr/ADR-001-convenciones-dominio.md) | Ejes y signos; UUID y referencias versionadas; revisiones y procedencia de resultados; diagnósticos estables | ACCEPTED — aprobado por el usuario el 2026-10-27; implementación por tickets posteriores (T-003 y siguientes) |
| [ADR-002](adr/ADR-002-adaptador-plano-proceso-solver.md) | Primer adaptador FEM limitado al plano XZ, worker OpenSeesPy en proceso aislado por corrida con el mismo intérprete 3.12 (revisión DEC-011) y procedencia en memoria sin hash canónico (excepción incremental) | ACCEPTED — aprobado por el usuario el 2026-10-27, incluida la revisión DEC-011; implementación en T-007 |
| [ADR-003](adr/ADR-003-adaptador-espacial-3d.md) | Adaptador FEM espacial de seis GDL por nudo junto al plano (contraste cruzado), protocolo y worker propios, restricciones exactas donde se pueda (Lagrange para el rodillo inclinado), configuración espacial y extensión de la excepción de procedencia de ADR-002 | ACCEPTED — aprobado por el usuario el 2026-10-28 («aprobado», con el plan T-018, D1–D10 = A); implementación en T-018 |

**Aprobaciones de implementación de T-018 (E1)**, registradas en el [plan T-018](../plans/T-018.md) (sección de cada desviación o propuesta y su Historial). No son decisiones de producto ni crean ADR; el ADR-003 no cambia.
- DEV-1 (aprobada 2026-10-28): precisión de 1e-7 en la batería con L/h > 1000; el resto sigue en 1e-9.
- PROP-1 (aprobada 2026-10-28): complemento alineado en D4; `fix` en dos ejes globales cuando la tercera fila es un eje global.
- Cota de `tol_axis` ≤ 1e-6 (aprobada 2026-10-28).
- PROP-2 (aprobada 2026-10-28): término a priori eps·`penalty_factor`·‖θ‖∞ en el estimador de penalización.
- DEV-2 (aprobada 2026-10-28): escala del alias de la cadena, c_alias·eps·máx(‖u_tras‖∞, a·‖θ‖∞).
- `inertia_axis` = "3D" en el análisis espacial (aprobada 2026-10-29).
- Nueva ayuda del grupo `analyze` (aprobada 2026-10-29).
- Calibración de penalización (`penalty_factor` = 1e8, `tol_penalty` = 1e-6, alpha = D), dada por buena el 2026-10-28.

Los ADR complementan DEC-001…010 sin sustituirlas. Aprobar un ADR no declara implementadas sus capacidades ni valida un solver. Al aprobar o rechazar un ADR, actualizar su estado aquí y en el propio ADR con fecha y referencia a la aprobación expresa del usuario.

No desarrollar verificación normativa de aluminio bajo etiqueta «CTE»: no existe un DB-SE específico de aluminio equivalente a acero/madera. El análisis FEM seguirá disponible; mostrar NO_COMPROBADO hasta seleccionar/validar normativa.

Si una solicitud modifica una decisión aprobada, registrar ADR en `docs/adr/` antes de aplicarla, con impacto técnico y aceptación del Product Owner. Nomenclatura: `ADR-NNN-titulo-corto.md`, con secciones de estado, contexto, decisión, alternativas, consecuencias e historial.
