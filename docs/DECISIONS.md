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
| [ADR-004](adr/ADR-004-penalizacion-iterada-extremos.md) | Penalización iterada (Uzawa, factor de extremo 1e1) en los extremos de barra con aux penalizado del worker espacial, contraste exacto obligatorio con la K reducida (nunca publicado), protocolo espacial v3 y adaptador «3»; enmienda ADR-003 §5 | ACCEPTED — aprobado por el usuario el 2026-10-30 («Aprobar; implementar en sesión nueva»), puerta E2-c de T-018; implementada en T-018 (E2.4-impl y E2.5, aceptadas el 2026-10-30) |
| [ADR-005](adr/ADR-005-fuerzas-empotramiento-veritas.md) | Cargas de barra no uniformes: fuerzas de empotramiento calculadas por Veritas (Euler–Bernoulli), aplicadas como cargas nodales equivalentes (`member_end_loads`); esfuerzo publicado = localForce + p0_c; protocolos plano v4 y espacial v5, adaptadores «5» | ACCEPTED — aprobado por el usuario el 2026-10-30 («ADR-005 aprobada»); implementación en T-015 (E2.0–E2.3 aceptadas; E2.4 de cierre pendiente de aceptación) |

**Aprobaciones de implementación de T-018 (E1 y E2)**, registradas en el [plan T-018](../plans/T-018.md) (sección de cada desviación o pieza y su Historial). No son decisiones de producto ni crean ADR; el ADR-003 no cambia. ADR-004 es la única enmienda de ADR de E2 (enmienda ADR-003 §5).
- DEV-1 (aprobada 2026-10-28): precisión de 1e-7 en la batería con L/h > 1000; el resto sigue en 1e-9.
- PROP-1 (aprobada 2026-10-28): complemento alineado en D4; `fix` en dos ejes globales cuando la tercera fila es un eje global.
- Cota de `tol_axis` ≤ 1e-6 (aprobada 2026-10-28).
- PROP-2 (aprobada 2026-10-28): término a priori eps·`penalty_factor`·‖θ‖∞ en el estimador de penalización.
- DEV-2 (aprobada 2026-10-28): escala del alias de la cadena, c_alias·eps·máx(‖u_tras‖∞, a·‖θ‖∞).
- `inertia_axis` = "3D" en el análisis espacial (aprobada 2026-10-29).
- Nueva ayuda del grupo `analyze` (aprobada 2026-10-29).
- Calibración de penalización (`penalty_factor` = 1e8, `tol_penalty` = 1e-6, alpha = D), dada por buena el 2026-10-28.

**Aprobaciones de E2 de T-018** (plan «Etapa E2», aprobado 2026-10-29: E2-a, E2-b, E2-d, E2-e y E2-f según la propuesta; E2-c como puerta):
- E2-a (criterio exacto del aux por terna) y E2-b (protocolo v2, adaptador «2»), con sus concreciones de contrato interno (marcas `axial_released`/`torsion_released`, `orphan_fixes` y `end_springs`) y la cota de registro de 1e-4 para el aux oblicuo (E2.1, 2026-10-29).
- E2.2 (2026-10-29): `TOL_AXIAL_LOAD` = 1e-9 relativa para Wx = 0 (sin campo nuevo en la configuración); `fem3d.end_spring_near_rigid` con `NEAR_RIGID_RATIO` = 1e8; dependencia lineal de huérfanos con `tol_axis`; guarda de orquestación E2.2-d, retirada en E2.5.
- E2.3 (2026-10-30): nombre local de las coordenadas del aux; diagnóstico de huérfanos con `fem3d.mechanism_orphan_rotation` y `fem3d.orphan_rotation_oblique`; contraste de la K reducida con el worker en tests. E2-g (celosía o trípode biarticulado sin giros fijos = mecanismo, documentado) con la redacción corregida (M1).
- E2-c, puerta de penalización (2026-10-30): opción **U**, penalización iterada con contraste exacto, formalizada en [ADR-004](adr/ADR-004-penalizacion-iterada-extremos.md). La aprobación previa de la opción C quedó en suspenso y fue sustituida.
- E2.4-impl (2026-10-30): factor de extremo 1e1; `end_penalty_*` solo en el snapshot; `end_penalties` aparte en la petición; regla S_s = 0; F con normas euclídeas y L_máx global; `ANALYSIS_NONCONVERGENCE` con WARNING para `fem3d.end_penalty_iterations`; `protocol.py` importa los valores por defecto de `config.py`.
- E2.5 (2026-10-30): escalas del contraste (modelo mixto D4 más extremo penalizado, con `tol_penalty` sumada); `solve_factored` como adición a `fem/adapters/singularity.py`; claves `fem3d.end_penalty_reference_unreliable`, `fem3d.end_link_check_failed` y `fem3d.singular_member_end`; tres constantes nuevas de procedencia; cotas de normalización aproximadas en los aux. Corrección de redacción de ADR-004 (`nodeReaction` resta las cargas, m2).
- E2.6 (2026-10-30): `uniones` espacial en la dirección (2, −3, 6)/7 con α = 0,37 (opción A); apoyos del mecanismo en la base local de la barra (opción A); salida 0 si la variante Mecanismo da ANALYSIS_SINGULAR (opción A); texto de `fem3d.hypothesis_penalty`. Desviaciones menores aceptadas tras confirmar que no afectan al cálculo: resorte con k = 1·EI/L (como `analyze uniones`), hipótesis D4 en la variante Mecanismo y o2 parcial.
- E2.7 (2026-10-30): `veritas info` declara el análisis espacial (E2.7-a); la aceptación de E2 cierra T-018 (E2.7-b).
- Revisión de código de E2 (2026-10-30): D-1, documentar el tipo `_Factorization`; D-2, `uniones` exige modo concluyente para salir con 0; D-3, `fem3d.end_penalty_iterations` como WARNING informativo, porque `Severity` no tiene INFO; m2, `end_axial_load_tolerance` en la procedencia; m7, no se aplica y se documenta; m8, la salida de `uniones` muestra también las hipótesis de «Liberada».

**Aprobaciones de implementación de T-016** (asientos impuestos), registradas en el [plan T-016](../plans/T-016.md) (D1–D11, D4-bis, desglose de E2, detalle de E2.3 e Historial). Sin ADR nuevo (D6-A): son ampliaciones versionadas según las reglas de ADR-002 y ADR-003; ADR-004 no cambia porque su penalización y su contraste no corren con asientos.
- Plan (2026-10-30, «todo aprobado»): D1–D11 y D4-bis = A. Asiento como acción del caso (`SupportSettlement`), solo en GDL FIXED de apoyos de base global en FEM, sin penalizaciones ni cadenas en la misma corrida (D4-bis-A), control de asiento (D7) y `veritas analyze asientos` (D11-A).
- Desglose de E2 (2026-10-30): E2-a…E2-d = A (workers congelados en `tests/data/` para la identidad bit a bit sin asientos; tests de E1 convertidos y clave `fem.settlement_pending` retirada; una puerta por pieza; contraste de reacciones con aux), C1 y C2.
- E2.1 (aceptada 2026-10-30): protocolo plano v3, adaptador plano «4»; M1, el plano conserva el orden de controles de T-017 tras el asiento; M2, el rechazo por penalización solo afecta a apoyos penalizados.
- E2.2 (aceptada 2026-10-30): protocolo espacial v4, adaptador espacial «4», cota del control `CHAIN_ALIAS_FACTOR`·eps·s; M1, el espacial evalúa asiento → equilibrio → `solution_checks`; M3, rechazo de D4-bis-A también en el protocolo y el worker espaciales.
- E2.3 (aceptada 2026-10-30): E2.3-a…E2.3-e = A (nota |u − valor|/s de la obs. 5; T-ASI-09 en los dos adaptadores con fórmulas cerradas y filas «A + Q»; formato de `analyze referencias` sin `--caso`; golden `asientos.txt`; revisión estructural acotada a 09, 10 y la transcripción); P1, rechazo de D4-bis-A también en el protocolo y el worker planos; escala de momentos EA·δ en T-ASI-06 para el contraste T-ASI-10.
- E2 y T-016 (aceptados 2026-10-30, «todo aceptado»): M1, el protocolo plano v3 rechaza nudos repetidos en `fixes` (como el espacial), sin cambiar su número de versión; M2, los workers no cambian; la nota de obs. 5 y el golden `asientos.txt` no cambian. **T-016 ACEPTADO**.

**Aprobación del plan T-015** (cargas de barra ampliadas), registrada en el [plan T-015](../plans/T-015.md): plan aprobado el 2026-10-30 («aprobado, adelante»), D1–D12 = A. ADR-005 ACCEPTED el 2026-10-30. E1 aceptada el 2026-10-30 («Todo aceptado»): E1-a, se reutilizan `fem.load_type_unsupported` y `fem3d.load_type_unsupported` en lugar de una clave temporal; E1-b, magnitud de presentación `MOMENT_PER_LENGTH` con N·m/m, kN·m/m y N·mm/mm (cambian `units profiles` y `units list`); E1-c, filas de la CLI para las cargas nuevas.
- E2.0 (aceptada 2026-10-30, «todo aceptado»): E2.0-a, `condense_beam(…, *, length=None)` como argumento nuevo; E2.0-b, si M_I + M_J = 0 no hay corrección; E2.0-c, `ValueError` como defensa en `member_loads`; E2.0-d, R-OPS en subproceso; E2.0-e, tolerancia 1e-12 sobre la mayor de dos escalas.
- E2.1 (aceptada 2026-10-30, «Aceptado»): E2.1-a, claves `fem.member_point_load_out_of_plane` y `fem.member_moment_out_of_plane`; E2.1-b, el p0 del padre se recupera de la petición y no viaja en el protocolo; E2.1-c, el test de rechazo plano de E1 pasa a aceptación; E2.1-d, límite heredado `fem.no_free_dofs`; E2.1-e, hallazgo previo a T-015 (normalización plana con apoyo penalizado), abierto como T-021.
- E2.2 (aceptada 2026-10-30, «Acepto todo lo que sugieres»): E2.2-a, D8 carga a carga con `TOL_AXIAL_LOAD` y clave `fem3d.end_torsion_with_torsional_load`; E2.2-b, regla de huérfanos con momento con `tol_axis`·‖m‖; E2.2-c, p0_c recuperado con índices de `-release*` anulados; E2.2-d, `force_scale` cuenta las cargas nuevas; E2.2-e, el test de rechazo espacial de E1 pasa a aceptación; E2.2-f, texto de `fem3d.end_axial_with_axial_load` actualizado (ya cubre la carga puntual, sin cambio de clave).
- E2.3 (aceptada 2026-10-31, «E2.3 (con E2.3-a..e) aceptado»): E2.3-a, barra auxiliar descargada en T-CB-01 y 02, y variantes a/b/c y a/b; E2.3-b, R-SUB contrasta con la barra subdividida; E2.3-c, T-CB-07 sin flexión 3D (cubierta por E2.2); E2.3-d, nueva línea «Cargas» de `info`; E2.3-e, golden solo para el comando nuevo.
- E2.4 (cierre documental, 2026-10-31): en curso; pendiente de aceptación del usuario, que cierra T-015.

Los ADR complementan DEC-001…010 sin sustituirlas. Aprobar un ADR no declara implementadas sus capacidades ni valida un solver. Al aprobar o rechazar un ADR, actualizar su estado aquí y en el propio ADR con fecha y referencia a la aprobación expresa del usuario.

No desarrollar verificación normativa de aluminio bajo etiqueta «CTE»: no existe un DB-SE específico de aluminio equivalente a acero/madera. El análisis FEM seguirá disponible; mostrar NO_COMPROBADO hasta seleccionar/validar normativa.

Si una solicitud modifica una decisión aprobada, registrar ADR en `docs/adr/` antes de aplicarla, con impacto técnico y aceptación del Product Owner. Nomenclatura: `ADR-NNN-titulo-corto.md`, con secciones de estado, contexto, decisión, alternativas, consecuencias e historial.
