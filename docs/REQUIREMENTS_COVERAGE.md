# Cobertura de requisitos SRS

Matriz de trazabilidad de la SRS v1.1.0 ([references/Structural3D_SRS_v1.1.0.md](references/Structural3D_SRS_v1.1.0.md)) hacia tickets, evidencias y aceptación del usuario.

**Estado del proyecto:** F0 y F1A aceptadas (T-003 y T-004 aceptados 2026-10-27). Los aportes con implementación y aceptación figuran como `PARCIAL` con su límite. Los requisitos afectados por ADR-001 (aprobado el 2026-10-27) figuran como `PARCIAL / DOCUMENTAL`; el resto, `NO INICIADO`.

## Reglas de uso (Claude Code)

- La matriz registra aportes por ticket; no declara satisfecho un requisito global por la existencia de una configuración, una demo o un ADR.
- Actualizar únicamente al cerrar un ticket (paso 7 de [MODULE_CONTRACT.md](MODULE_CONTRACT.md)), citando el ID de ticket de `tasks/BACKLOG.md`, la ruta de evidencia (tests, demo, `plans/<ID-TICKET>.md`) y la fecha de aceptación expresa del usuario.
- Estados admitidos:
  - `NO INICIADO`: sin trabajo asociado.
  - `EN CURSO`: ticket con plan aprobado en implementación.
  - `PARCIAL`: aporte verificado que no cubre todo el requisito; indicar el límite.
  - `PARCIAL / DOCUMENTAL`: solo contrato o ADR aprobado, sin implementación.
  - `CUBIERTO`: requisito completo, con tests, demo y aceptación del usuario.
- No marcar `CUBIERTO` sin evidencia reproducible. No atribuir pruebas manuales o ejecuciones de CI que no se hayan realizado.
- Las filas se extraen literalmente de la SRS; no editar el texto del requisito aquí. Si la SRS cambia, regenerar la matriz y registrar el cambio.

## Matriz

| ID SRS | Prioridad / categoría | Requisito SRS | Aporte de tickets | Estado y evidencia |
|---|---|---|---|---|
| GEO-001 | P0 | Crear nudos con ID estable y coordenadas XYZ en SI. | T-003 (`plans/T-003.md`): `StructuralModel.add_node` (UUID v4, revisiones, coordenadas en m); `tests/test_geometry_model.py` | PARCIAL — T-003 aceptado 2026-10-27; modelo solo en memoria, sin persistencia ni GUI |
| GEO-002 | P0 | Crear barras entre dos nudos distintos; rechazar longitudes degeneradas. | T-003 (`plans/T-003.md`): `add_member` rechaza mismo nudo y L < 1 µm (`GEOMETRY_ZERO_LENGTH`); `tests/test_geometry_model.py`, `tests/test_local_axes.py` | PARCIAL — T-003 aceptado 2026-10-27; sin GUI |
| GEO-003 | P0 | Asignar exactamente una revisión de material y de sección a cada barra. | T-004 (`plans/T-004.md`): `Member.material`/`Member.section` obligatorios como `RevisionRef` (id, revisión); `assign_material`/`assign_section`; `tests/test_model_assignment.py` | PARCIAL — T-004 aceptado 2026-10-27; modelo solo en memoria; sin sustitución masiva (SEC-012) |
| GEO-004 | P0 | Calcular ejes locales ortonormales y permitir orientar una barra alrededor de su eje longitudinal. | T-003 (`plans/T-003.md`): base local ADR-001 §1 (A1-a), giro alfa, referencia explícita, postcondición QQᵀ = I; CLI `veritas geometry axes`; `tests/test_local_axes.py`, `tests/test_geometry_cli.py` | PARCIAL — T-003 aceptado 2026-10-27; sin inversión I/J ni visualización 3D |
| GEO-005 | P1 | Selección simple, múltiple, por ventana y por atributos. | — | NO INICIADO |
| GEO-006 | P1 | Crear, dividir, copiar, trasladar, rotar, reflejar y replicar elementos. | — | NO INICIADO |
| GEO-007 | P1 | Captura geométrica, rejilla 3D, niveles, planos, cotas, etiquetas y capas. | — | NO INICIADO |
| GEO-008 | P1 | Edición gráfica y tabular sincronizada sobre el mismo dominio. | — | NO INICIADO |
| GEO-009 | P1 | Deshacer/rehacer operaciones atómicas y mantener integridad del modelo. | — | NO INICIADO |
| GEO-010 | P1 | Apoyos por GDL, articulaciones/liberaciones, resortes y excentricidades soportadas. | T-005 (`plans/T-005.md`): `veritas.core.supports` (`Support`, `DofRestraint` FREE/FIXED/SPRING, `SupportBasis` GLOBAL/INCLINADA, presets empotramiento/articulación/rodillo); `StructuralModel.set_support/relabel_support/remove_support`; CLI `veritas support dofs|demo`; `tests/test_supports.py`, `test_model_supports.py`, `test_support_cli.py` | PARCIAL — T-005 aceptado 2026-10-27; sin liberaciones/resortes de extremo I/J (T-010), excentricidades, resortes acoplados/unilaterales, asientos impuestos ni efecto en análisis (T-007) |
| GEO-011 | P1 | Detectar barras duplicadas, nudos coincidentes y posibles mecanismos. | — | NO INICIADO |
| GEO-012 | P1 | Filtrar, ocultar, aislar y colorear barras por grupo, material y sección. | — | NO INICIADO |
| GEO-013 | P2 | Intercambio geométrico DXF con pérdidas y convenciones declaradas. | — | NO INICIADO |
| CON-001 | P0 | Rigidez predeterminada de los dos extremos I/J de cada barra nueva. | T-003 (`plans/T-003.md`): `EndConnection.rigid()` en I y J para los seis GDL; `tests/test_geometry_model.py` | PARCIAL — T-003 aceptado 2026-10-27; sin persistencia ni GUI |
| CON-002 | P0 | Configuraciones de los extremos I y J independientes y persistentes. | T-003 (`plans/T-003.md`): `end_i`/`end_j` independientes en `Member` | PARCIAL — T-003 aceptado 2026-10-27; solo estado RIGID, sin persistencia |
| CON-003 | P1 | GDL locales Ux, Uy, Uz, Rx, Ry, Rz con estados rígido, liberado o resorte, cuando sean compatibles con el tipo de elemento. | ADR-001 (contrato de convenciones, aprobado 2026-10-27) | PARCIAL / DOCUMENTAL — sin implementación ni tests |
| CON-004 | P1 | Rigidez traslacional N/m y rotacional Nm/rad, con conversión de unidades y validación. | — | NO INICIADO |
| CON-005 | P1 | Presets rígido, articulado, semirrígido y personalizado, con definición precisa por GDL. | — | NO INICIADO |
| CON-006 | P1 | Offsets/excentricidades I/J y referencias de ejes consistentes. | ADR-001 (contrato de convenciones, aprobado 2026-10-27) | PARCIAL / DOCUMENTAL — sin implementación ni tests |
| CON-007 | P1 | Presets de unión reutilizables y versionados. | — | NO INICIADO |
| CON-008 | P1 | Seleccionar el extremo en vista 3D y editarlo en inspector, tabla o asignación múltiple. | — | NO INICIADO |
| CON-009 | P1 | Detectar mecanismos, grados de libertad huérfanos y combinaciones incompatibles. | — | NO INICIADO |
| CON-010 | P1 | Incluir parámetros de extremos en informes y tablas. | — | NO INICIADO |
| CON-011 | P1 | Traducir configuraciones soportadas a OpenSeesPy; rechazar explícitamente las no soportadas. | — | NO INICIADO |
| CON-012 | P1 | Historial, invalidación de resultados y pruebas de regresión para uniones. | — | NO INICIADO |
| MAT-001 | P0 | Acero, aluminio y madera; parámetros E, G o Poisson según formulación, densidad y propiedades de resistencia diferenciadas. | T-004: `MaterialRevision` (STEEL/ALUMINIUM/TIMBER; E y G independientes, ν opcional, ρ); `tests/test_materials.py` | PARCIAL — T-004 aceptado 2026-10-27; sin resistencias (F7A/F7B) ni material definido solo por E y ν |
| MAT-002 | P0 | ID, nombre, familia, fuente, edición normativa, unidades y versión. | T-004: UUID v4, revisión, nombre, familia, fuente, `standard_edition`, SI declarado; `tests/test_materials.py` | PARCIAL — T-004 aceptado 2026-10-27; sin catálogo ni persistencia (MAT-003, T-012) |
| MAT-003 | P1 | Catálogo normalizado y materiales personalizados; clonar registros protegidos antes de editar. | — | NO INICIADO |
| MAT-004 | P1 | Validaciones dimensionales, propiedades físicamente admisibles y avisos de datos incompletos. | — | NO INICIADO |
| MAT-005 | P1 | Reasignar material invalida análisis dependientes; no borra históricos. | — | NO INICIADO |
| MAT-006 | P2 | Importación/exportación de datos de materiales con trazabilidad. | — | NO INICIADO |
| SEC-001 | P0 | Sección con ID y revisión inmutables, familia y parámetros geométricos. | ADR-001; T-004: `SectionRevision` (RECTANGULAR, b, h), `revise_rectangular_section`; `tests/test_sections_rectangular.py` | PARCIAL — T-004 aceptado 2026-10-27; solo rectángulo macizo |
| SEC-002 | P0 | Separar propiedades geométricas del material; masa lineal derivada de densidad y área. | T-004: sección sin material; `linear_mass` = ρ·A (kg/m); `tests/test_model_assignment.py` | PARCIAL — T-004 aceptado 2026-10-27; solo rectángulo macizo |
| SEC-003 | P0 | Área, centroide, Iy, Iz e Iyz, sistemas de referencia y unidades explícitas. | ADR-001; T-004: A, yc, zc, Iy, Iz, Iyz con unidad, origen, método y estado; J por serie de Saint-Venant; CLI `veritas section rect`; `tests/test_sections_rectangular.py` | PARCIAL — T-004 aceptado 2026-10-27; solo rectángulo; signo de Iyz pendiente de sección no simétrica |
| SEC-004 | P1 | Radios de giro, módulos resistentes, J, Cw y otras propiedades cuando exista método validado. | — | NO INICIADO |
| SEC-005 | P1 | Buscar por serie, nombre, dimensiones, A, inercias, masa, fabricante y material compatible. | — | NO INICIADO |
| SEC-006 | P1 | Ficha con croquis acotado y comparación de perfiles. | — | NO INICIADO |
| SEC-007 | P1 | Catálogos de solo lectura, clones editables, favoritos y perfiles recientes. | — | NO INICIADO |
| SEC-008 | P1 | Importar CSV/JSON con asignación de columnas/unidades, vista previa y validación por fila. | — | NO INICIADO |
| SEC-009 | P1 | Exportar perfiles del usuario y mantener origen, licencia de datos, norma, revisión y método de cálculo. | — | NO INICIADO |
| SEC-010 | P1 | Detectar duplicados e incoherencias entre datos publicados y propiedades calculadas. | — | NO INICIADO |
| SEC-011 | P1 | Cambiar geometría genera nueva revisión; proyectos existentes conservan referencia previa. | ADR-001 (contrato de convenciones, aprobado 2026-10-27) | PARCIAL / DOCUMENTAL — sin implementación ni tests |
| SEC-012 | P1 | Sustitución masiva de perfiles en barras con validación de compatibilidad. | — | NO INICIADO |
| SEC-013 | P1 | Separar sección apta para FEM de sección cubierta por una norma de diseño. | — | NO INICIADO |
| SEC-014 | P2 | Catálogos adicionales de proveedores mediante adaptadores importables. | — | NO INICIADO |
| EDT-001 | P0 | Editor paramétrico de rectángulo, círculo, anillo, I, U, L, RHS, SHS y CHS. | — | NO INICIADO |
| EDT-002 | P1 | Editor 2D con líneas, arcos, polígonos cerrados, huecos, cotas y snapping. | — | NO INICIADO |
| EDT-003 | P1 | Operaciones geométricas unión/sustracción de regiones homogéneas. | — | NO INICIADO |
| EDT-004 | P0 | Rechazar autointersecciones, contornos abiertos, espesores negativos y huecos inválidos. | — | NO INICIADO |
| EDT-005 | P0 | Calcular área, centroide, inercias Iy/Iz/Iyz y ejes principales. | — | NO INICIADO |
| EDT-006 | P1 | Dibujar centroide, ejes, contornos, cotas y tabla de propiedades. | — | NO INICIADO |
| EDT-007 | P1 | Tratar arcos analíticamente o con discretización de error controlado. | — | NO INICIADO |
| EDT-008 | P1 | Calcular J/módulos plásticos solo con algoritmo específico y contrastado, indicando no soportado cuando proceda. | — | NO INICIADO |
| EDT-009 | P2 | Cw, propiedades efectivas y secciones abiertas delgadas mediante métodos especializados verificados. | — | NO INICIADO |
| EDT-010 | P1 | Guardar nuevo perfil, clon o revisión, con historial y bloqueo de revisiones en uso. | — | NO INICIADO |
| EDT-011 | P1 | Comparar propiedades calculadas con valores de catálogo mostrando diferencia relativa. | — | NO INICIADO |
| EDT-012 | P1 | Permitir borradores, pero impedir asignar perfiles con geometría inválida. | — | NO INICIADO |
| EDT-013 | P1 | Exportar dibujo SVG y definición JSON del perfil. | — | NO INICIADO |
| LD-001 | P0 | Casos de carga identificados por ID, categoría y descripción. | T-006 (`plans/T-006.md`): `veritas.core.loads.LoadCase` (UUID v4, revisión, nombre, `LoadCategory` PERMANENT/VARIABLE/ACCIDENTAL, descripción); `StructuralModel.add/revise/remove_load_case`; `tests/test_loads.py`, `test_model_loads.py`, `test_self_weight.py`, `test_load_resultant.py`, `test_load_cli.py` | PARCIAL — T-006 aceptado 2026-10-27; sin subtipos, ψ, edición normativa del caso ni combinaciones (F5A); nombre no único |
| LD-002 | P0 | Fuerzas/momentos nodales y cargas puntuales/distribuidas de barras según tipos soportados. | T-006: `NodalLoad` (F y M en base global), `MemberUniformLoad` (q uniforme en toda la barra, base GLOBAL o LOCAL, por metro de longitud real); `case_resultant` (ΣF/ΣM de acciones prescritas); CLI `veritas load demo`; `tests/test_loads.py`, `test_model_loads.py`, `test_self_weight.py`, `test_load_resultant.py`, `test_load_cli.py` | PARCIAL — T-006 aceptado 2026-10-27; sin cargas puntuales/parciales/trapezoidales en barra, momentos distribuidos, cargas proyectadas, base inclinada ni efecto en análisis (T-007) |
| LD-003 | P0 | Peso propio desde geometría, área, densidad y gravedad, sin duplicidad. | T-006: `SelfWeight` (q = ρ·A·g por metro de longitud real hacia −Z, g por defecto 9,80665 m/s², derivado bajo demanda; uno por modelo y solo en caso permanente); `StructuralModel.set_self_weight/self_weight_load`; CLI `veritas load selfweight`; fixture `self_weight_prismatic`; `tests/test_loads.py`, `test_model_loads.py`, `test_self_weight.py`, `test_load_resultant.py`, `test_load_cli.py` | PARCIAL — T-006 aceptado 2026-10-27; no detecta cargas manuales que dupliquen el peso propio; sin selección de barras; ρ·g puede no coincidir con pesos específicos tabulados de DB-SE-AE (por verificar) |
| LD-004 | P1 | Paneles de contorno geométrico sin rigidez FEM, con barras receptoras configurables. | — | NO INICIADO |
| LD-005 | P1 | Reparto tributario o direccional con método explícito y verificación ΣF/ΣM. | — | NO INICIADO |
| LD-006 | P1 | Visualizar panel, áreas tributarias, flechas y cargas equivalentes transferidas. | — | NO INICIADO |
| LD-007 | P1 | Asistentes viento/nieve con parámetros geográficos/geométricos y normativa/anexo versionados. | — | NO INICIADO |
| LD-008 | P1 | Imperfecciones globales y locales mediante geometría o acciones equivalentes, sin duplicación de efectos. | — | NO INICIADO |
| LD-009 | P1 | Acciones térmicas cuando el elemento y la formulación FEM estén validados. | — | NO INICIADO |
| LD-010 | P1 | Mantener historial de parámetros generadores, sobreescrituras y resultados. | — | NO INICIADO |
| COM-001 | P0 | Combinaciones explícitas como suma de casos y factores definidos. | — | NO INICIADO |
| COM-002 | P1 | Combinaciones automáticas ELU/ELS por plugin normativo y anexo nacional. | — | NO INICIADO |
| COM-003 | P1 | Acciones incompatibles, simultaneidad, principal y coeficientes correspondientes. | — | NO INICIADO |
| COM-004 | P1 | Exponer fórmula expandida y edición normativa de cada combinación. | — | NO INICIADO |
| COM-005 | P1 | Superponer solo resultados de análisis matemáticamente lineales compatibles. | — | NO INICIADO |
| COM-006 | P1 | En análisis no lineal resolver combinaciones con ejecución propia cuando corresponda. | — | NO INICIADO |
| COM-007 | P1 | Envolventes máximas/mínimas con barra, estación y combinación gobernantes. | — | NO INICIADO |
| FEM-001 | P0 | Adaptador determinista de dominio a OpenSeesPy, sin dependencia en el núcleo. | T-007 (`plans/T-007.md`): `veritas.fem.adapters.planar_xz.translate_planar_xz` (función pura, tags por UUID, JSON idéntico byte a byte), worker `opensees_worker.py` (solo stdlib + OpenSeesPy); `tests/test_fem_translator.py`, `test_layer_isolation.py` | PARCIAL — T-007 aceptado 2026-10-27; solo plano XZ (ADR-002 §1) |
| FEM-002 | P0 | Barras espaciales con seis GDL por nudo para marcos, y formulaciones de cercha compatibles. | — | NO INICIADO |
| FEM-003 | P0 | Definir restricciones, elementos, transformaciones, cargas, solución y tolerancias. | T-007 (`plans/T-007.md`): `elasticBeamColumn` 2D + `geomTransf Linear`, `fix` exacto, resortes `zeroLength`, penalización solo para una traslación oblicua rígida, `-beamUniform`, análisis lineal estático; tolerancias en `AnalysisConfiguration`; gate G1–G7 en `tests/test_fem_solver.py` | PARCIAL — T-007 aceptado 2026-10-27; sin 3D, liberaciones, offsets ni no linealidad |
| FEM-004 | P0 | Recuperar desplazamientos, giros, reacciones y esfuerzos extremos con signos/unidades declarados. | ADR-001 (contrato de convenciones, aprobado 2026-10-27) | PARCIAL / DOCUMENTAL — sin implementación ni tests |
| FEM-005 | P0 | Iniciar cada corrida con estado FEM limpio y resultados ligados a revisión exacta. | ADR-001; T-007 (`plans/T-007.md`): proceso nuevo y `wipe()` por corrida; `AnalysisRun` con `run_id`, `model_revision` leído del dominio, snapshot de configuración y petición JSON; `tests/test_fem_runner.py`, `test_fem_solver.py` | PARCIAL — T-007 aceptado 2026-10-27; sin hash canónico ni CURRENT/STALE (excepción ADR-002 §6) |
| FEM-006 | P1 | Segundo orden P-Delta con registro de convergencia. | — | NO INICIADO |
| FEM-007 | P1 | Análisis modal básico: frecuencias, periodos, modos. | — | NO INICIADO |
| FEM-008 | P1 | Detectar singularidades, mecanismos, ausencia de convergencia y fallos, evitando resultados aparentemente válidos. | T-007 (`plans/T-007.md`): salvaguarda D3 (ensamblaje independiente, Jacobi, LDLᵀ con pivotaje completo, `tol_pivot` calibrado), fallo de solver, timeout, respuesta inválida, no finitos y control de equilibrio → FAILED sin resultados; `tests/test_fem_singularity.py`, `test_fem_runner.py` | PARCIAL — T-007 aceptado 2026-10-27; garantía condicionada a la batería; sin localización de mecanismos (T-010); ≤ 300 GDL libres |
| FEM-009 | P1 | Ejecución aislada y cancelable sin corromper proyecto. | — | NO INICIADO |
| FEM-010 | P1 | Registrar versión, parámetros, avisos, duración y estado. | ADR-001; T-007 (`plans/T-007.md`): `AnalysisRun` (versiones veritas/adaptador/protocolo/Python/openseespy/openseespywin, configuración, hipótesis, avisos, pivote mínimo, inicio UTC, duración, estado, stdout/stderr separados); CLI `veritas analyze demo` | PARCIAL — T-007 aceptado 2026-10-27; solo en memoria (sin persistencia, T-012) |
| FEM-011 | P2 | Estabilidad/no linealidad avanzada con matriz explícita de soporte y validación. | — | NO INICIADO |
| RES-001 | P0 | Tablas de desplazamientos, giros, reacciones y esfuerzos con unidades. | — | NO INICIADO |
| RES-002 | P1 | Diagramas N, Vy, Vz, torsión, My y Mz por estaciones. | — | NO INICIADO |
| RES-003 | P1 | Modelo deformado/indeformado y escala de deformación independiente. | — | NO INICIADO |
| RES-004 | P1 | Filtros por caso, combinación, material, sección, familia, grupo, nivel y rango. | — | NO INICIADO |
| RES-005 | P1 | Mapas de color por magnitud o aprovechamiento y envolventes máximas/mínimas. | — | NO INICIADO |
| RES-006 | P1 | Selección sincronizada entre viewport 3D y tablas. | — | NO INICIADO |
| RES-007 | P1 | Mostrar signo, sistema de ejes, estación y combinación gobernante. | ADR-001 (contrato de convenciones, aprobado 2026-10-27) | PARCIAL / DOCUMENTAL — sin implementación ni tests |
| RES-008 | P1 | Guardar vistas/cámaras y exportarlas a informes. | — | NO INICIADO |
| RES-009 | P1 | Señalar resultados obsoletos tras edición del modelo. | — | NO INICIADO |
| NOR-001 | P1 | Plugins por código, jurisdicción, edición/consolidación, material, familia y comprobación soportada. | — | NO INICIADO |
| NOR-002 | P1 | Primero CTE DB-SE, DB-SE-AE, DB-SE-A y DB-SE-M, con trazabilidad por cláusula y edición. | — | NO INICIADO |
| NOR-003 | P1 | Cada verificación mostrará cláusula, fórmula, parámetros, valores intermedios, unidades y ratio. | — | NO INICIADO |
| NOR-004 | P1 | Estados APROBADO, INCUMPLIDO, NO_APLICABLE, NO_COMPROBADO y ERROR. | — | NO INICIADO |
| NOR-005 | P1 | No aprobar si faltan propiedades de sección, material o parámetros de estabilidad. | — | NO INICIADO |
| NOR-006 | P1 | Arriostramientos y longitudes de referencia definidos y trazables. | — | NO INICIADO |
| NOR-007 | P1 | Prohibir mezclar resultados/comprobaciones de versiones normativas incompatibles sin identificación. | — | NO INICIADO |
| NOR-008 | P1 | Test independiente contra ejemplos publicados para cada cláusula implementada. | — | NO INICIADO |
| NOR-009 | P2 | Eurocódigos EN 1990/1991/1993/1995/1999 y anexos nacionales como paquetes posteriores; otros sistemas, incluido SIA, separados. | — | NO INICIADO |
| NOR-010 | P1 | Para aluminio bajo perfil CTE, indicar cobertura normativa NO_COMPROBADA mientras no se apruebe referencia de cálculo aplicable. | — | NO INICIADO |
| NOR-011 | P1 | Validar ámbito de aplicación del DB seleccionado frente a la tipología estructural declarada. | — | NO INICIADO |
| DAT-001 | P0 | Guardar y abrir archivo de proyecto autocontenido/versionado, con comprobación de integridad. | ADR-001 (contrato de convenciones, aprobado 2026-10-27) | PARCIAL / DOCUMENTAL — sin implementación ni tests |
| DAT-002 | P1 | Migraciones controladas, copias y restauración ante fallo. | — | NO INICIADO |
| DAT-003 | P1 | SQLite de catálogos independiente y snapshot de perfiles usados en cada proyecto. | — | NO INICIADO |
| DAT-004 | P1 | Mantener ejecuciones históricas inmutables con hash de versión del modelo. | ADR-001 (contrato de convenciones, aprobado 2026-10-27) | PARCIAL / DOCUMENTAL — sin implementación ni tests |
| DAT-005 | P1 | CSV/JSON para tablas; DXF sujeto a contrato de intercambio específico. | — | NO INICIADO |
| RPT-001 | P1 | Editor de plantillas: capítulos activables, orden, selección y filtros. | — | NO INICIADO |
| RPT-002 | P1 | Incluir geometría, materiales, perfiles, cargas, combinaciones, análisis y comprobaciones. | — | NO INICIADO |
| RPT-003 | P1 | HTML/PDF con tablas paginadas, diagramas, vistas 3D, unidades y referencias. | — | NO INICIADO |
| RPT-004 | P1 | Informar de fallos, avisos, cobertura normativa parcial y obsolescencia de resultados. | — | NO INICIADO |
| RPT-005 | P1 | Registrar versión del proyecto, solver, norma, catálogo y fecha del cálculo. | — | NO INICIADO |
| RPT-006 | P2 | Plantillas corporativas, comparación de revisiones y formatos adicionales. | — | NO INICIADO |
| NFR-001 | Plataforma | Instalación y ejecución verificadas en Windows 11 x64. | T-001: `uv sync` y CLI `veritas` en Windows 11 x64 (10.0.26300), CPython 3.12.9 | PARCIAL — verificado localmente; T-001 aceptado 2026-10-27; no cubre la aplicación futura ni el instalador |
| NFR-002 | Offline | Modelado y cálculo sin Internet tras instalación y carga local de datos. | — | NO INICIADO |
| NFR-003 | Robustez | El fallo del solver no destruye el modelo ni genera éxito ficticio. | T-007 (`plans/T-007.md`): worker en proceso aislado; todo FAILED con `raw = None` (invariante de `AnalysisRun`); `model_revision` sin cambio tras cada corrida; `tests/test_fem_runner.py` (workers simulados: timeout, caída, respuesta corrupta, no finitos, desequilibrio) | PARCIAL — T-007 aceptado 2026-10-27; sin cancelación interactiva (FEM-009) |
| NFR-004 | Interfaz | Fluidez de trabajo medida sobre modelo de referencia de 1.000 barras; umbrales cuantitativos tras benchmark. | — | NO INICIADO |
| NFR-005 | Precisión | Doble precisión cuando corresponda; tolerancias y unidades documentadas. | T-002: `Quantity` en doble precisión con valor SI y dimensión; registro cerrado de 42 unidades contrastado con fracciones exactas; rechazo de no finitos, desbordamiento y subnormales; tolerancias justificadas por caso | PARCIAL — T-002 aceptado 2026-10-27; sin magnitudes de carga superficial ni desplazamiento, sin temperatura; no cubre cálculos posteriores |
| NFR-006 | Seguridad | Importaciones validadas; no ejecutar código contenido en un proyecto. | — | NO INICIADO |
| NFR-007 | Auditoría | Logs de análisis, errores y versiones; historial de cambios esenciales. | ADR-001 (contrato de convenciones, aprobado 2026-10-27) | PARCIAL / DOCUMENTAL — sin implementación ni tests |
| NFR-008 | Calidad | Tests unitarios, integración, referencia y regresión; tipado, lint y CI. | T-001: pytest (22 tests), ruff, mypy estricto, test de aislamiento de capas y workflow CI | PARCIAL — CI en GitHub (repo público larrezze-dev/Veritas) en verde: run 40545484065 (2026-10-27, windows-latest, CPython 3.12.10, 22 tests); windows-latest ≠ Windows 11; sin tests estructurales todavía |
| NFR-009 | Localización | Cadenas externalizadas; español inicial, arquitectura preparada para alemán e inglés. | — | NO INICIADO |
| NFR-010 | Accesibilidad | Diálogos/tabla navegables por teclado y escalado DPI. | — | NO INICIADO |
| NFR-011 | Recuperación | Autoguardado y recuperación explícita sin sobrescritura oculta. | — | NO INICIADO |
| NFR-012 | Licencias | Auditoría de dependencias, catálogos y normas antes de redistribución. | — | NO INICIADO |

## Pruebas de aceptación SRS (sección 16)

Las pruebas `T-XXX-NNN` de la sección 16 de la SRS se trazan en [VALIDATION_PLAN.md](VALIDATION_PLAN.md). Estado inicial: todas `NO INICIADO`.
