# Arquitectura y límites de módulos

**Estado:** contrato de diseño para el inicio del proyecto. No existe código implementado; las carpetas y contratos descritos son objetivos de los tickets.

## Tecnologías

Python tipado; dominio independiente; Pydantic para datos/validación de frontera; SQLAlchemy+SQLite para catálogos; PySide6 para UI; PyVista/VTK para visor; OpenSeesPy adaptado; NumPy/SciPy para cálculo auxiliar; pytest para validación; Jinja2/WeasyPrint para informes, sujetos a verificación de dependencias y licencias. La versión de Python y cada dependencia se fijan y aprueban en el ticket de entorno (F0); no introducir dependencias sin aprobación.

## Capas

1. **core:** Node, Member, MaterialRevision, SectionRevision, MemberEndConnection, Support, LoadCase, LoadCombination, AnalysisConfiguration. Sin dependencias de GUI/FEM.
2. **catalogs / section_editor:** perfil paramétrico y croquis, validador geométrico, propiedades, revisión y procedencia; separados del material.
3. **connections:** configuración obligatoria I y J, seis estados GDL, presets, offset, unidades, invalidación.
4. **loads:** fuerzas, peso propio, paneles de carga sin rigidez, asistentes y combinaciones; transferencia equilibrada.
5. **fem:** traductor determinista, proceso aislado, resultados normalizados, fallos explícitos.
6. **design_codes:** CTE por paquetes DB y edición; no depende de GUI; EN/SIA adicionales. Incluye editor manual de configuración de normativas (crear, editar, copiar, borrar) para casos no planificados.
7. **gui:** comandos sobre dominio y vista 3D, inspector/tabla sincronizados.
8. **reports:** consume snapshots, no ejecuta solver.
9. **persistence:** proyecto versionado, catálogos independientes, migraciones y resultados históricos.

## Contratos obligatorios

- Las entidades poseen ID inmutable, versión/revisión y propiedades dimensionales coherentes.
- Una barra referencia exactamente una SectionRevision y una MaterialRevision.
- Todo Member tiene EndConnection I/J; se inicializan rígidas por defecto.
- Una unión liberada y una semirrígida se traducen mediante formulación FEM soportada; no improvisar release universal OpenSeesPy.
- Resultados asociados al hash de la revisión del modelo, configuración y versión del adaptador.
- Excepción incremental **aprobada** en [ADR-002](adr/ADR-002-adaptador-plano-proceso-solver.md) (2026-10-27, con la revisión DEC-011): el primer adaptador se limita al plano XZ y conserva la procedencia mediante snapshots/configuración exactos en memoria, run_id y versiones, sin hash canónico ni CURRENT/STALE hasta especificar ese contrato. El worker OpenSeesPy corre en un proceso nuevo por corrida con el mismo intérprete 3.12 del proyecto y no importa `veritas.core`. No sustituye el objetivo espacial del producto.
- Todo cambio de perfil, material, conexión o carga invalida resultados afectados.
- Las combinaciones no lineales se calculan por ejecución, no por suma indiscriminada de esfuerzos.
- Para catálogo: no confundir Iy+Iz con J; propiedad avanzada no disponible queda explícita.
- CTE DB-SE/AE/A/M con edición y campo de aplicación identificados; aluminio sin chequeo todavía.
- Mantener lógica de cálculo fuera de widgets, reportes y controladores.

## Carpetas de código previstas

Paquete `veritas` bajo `src/` (DEC-011): `src/veritas/` con `cli.py` como punto de entrada y un subpaquete por capa: core/ (geometry/, materials/, sections/, connections/, supports/, loads/), catalogs/, section_editor/, connections/, loads/, fem/ (adapters/, runners/, results/), design_codes/ (cte/), gui/ (modeler/, inspector/, results/), reports/, persistence/. Tests en `tests/`. Los subpaquetes de segundo nivel se crean en sus tickets. `fem/` existe desde T-007: `config.py`, `adapters/` (traductor plano XZ, protocolo, salvaguarda de singularidad y worker OpenSeesPy, único módulo que importa `openseespy`), `runners/` (proceso aislado) y `results/` (registro de corrida y salida bruta; desde T-008, `normalized.py`: resultados normalizados a ADR-001 con estado por componente) y, desde T-009, `benchmarks.py`: casos de referencia analítica T-FEM-001…004 (y T-CON-REF-01…04 desde T-010) y su contraste con tolerancia, usado por `veritas analyze referencias`; importa `core` y `fem`, nunca la CLI, y el worker no lo importa; sus textos son claves `benchmark.*` de `messages_es`). Desde T-010 (protocolo interno v2, adaptador 2) un extremo de barra con el giro del plano liberado o con resorte se traduce con un nudo auxiliar (`equalDOF` en u₁, u₂ y `zeroLength` rotacional relativo); la salvaguarda ensambla el mismo modelo y el giro huérfano se restringe con aviso. Desde T-017 E1 (adaptador 3, protocolo v2 sin cambios), `fem/postconditions.py` contrasta en el proceso padre, tras el equilibrio, cada corrida con la formulación de la salvaguarda: alias exacto u₁, u₂ del aux, localForce ≈ k·T·u − F_eq en todas las barras, extremo liberado y resorte de extremo, y residuo Tᵀ(K·u − F) por coordenada reducida; un control no superado da FAILED sin resultados. El alias exacto depende del handler `Transformation` que fija el worker del protocolo v2: cambiar el handler exige una versión nueva de protocolo y revisar ese control. Desde T-017 E2, con singularidad, la salvaguarda (`fem/adapters/singularity.py`) calcula el modo de mecanismo (espacio nulo con la misma factorización que decide `singular`, participación por GDL) y lo publica en `PivotSummary.nullity/mechanism`, sin cambiar `core.diagnostics`; si no es concluyente, vuelve al pivote con el aviso `fem.mechanism_mode_inconclusive`. Dependencia de ejecución: `openseespy==3.8.0.0` (aprobada en T-007).

Carpetas de trabajo de Claude Code (no son código de producto): `.claude/agents/` (subagentes del proyecto), `tasks/` (backlog y plantilla de ticket), `plans/` (plan y estado de aprobación por ticket), `handoffs/` (checkpoints de sesión).

## Convenciones

Unidades SI internas; presentación configurable. [ADR-001](adr/ADR-001-convenciones-dominio.md), **aprobado** por el usuario el 2026-10-27, define los contratos de ejes, signos, identidad, revisiones y errores antes del primer cálculo. La UI nunca será el almacenamiento primario. Los contratos persistidos se versionan; cambios incompatibles requieren migración y prueba round-trip.

Contenido de ADR-001 (aplicar exactamente la construcción y los límites del ADR):

- Global XYZ derecho, Z arriba; local x de I a J, orientación transversal y giro explícitos. GDL de conexiones locales; datos nodales globales por defecto.
- Esfuerzos seccionales sobre cara +x distintos de acciones nodales sobre extremos; N positivo a tracción. Normalizar respuestas del solver solo tras ensayos específicos.
- IDs UUID v4 inmutables; referencias (ID, revisión positiva) y contenido histórico inmutable. Etiquetas visibles y tags de solver son independientes.
- Revisiones de entidad/modelo, esquema y versiones de aplicación/adaptador/solver son conceptos separados. Vigencia CURRENT/STALE no equivale al estado de ejecución ni al cumplimiento normativo.
- Diagnósticos con códigos estables, mensajes traducibles y ubicación. Cada frontera pública (CLI, API) declara en su ticket sus excepciones y códigos de salida; cambiarlos después requiere aprobación.

Estas reglas son contratos de diseño: geometría, entidades, adaptador, persistencia y diagnósticos estructurados se implementarán en sus tickets. La codificación canónica del hash requiere contrato y pruebas antes de su uso en producción.

## Controles de integración

- Unitarias de núcleo y secciones sin OpenSeesPy ni ventana Qt.
- Tests de adaptador FEM de extremo a extremo.
- Tests GUI con fixtures pequeños y runs reproducibles.
- No fusionar nuevas formulaciones estructurales sin caso de referencia independiente.
- Comprobación automática de que `core` no importa PySide6, PyVista ni OpenSeesPy.
