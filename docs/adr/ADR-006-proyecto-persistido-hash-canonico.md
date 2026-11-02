# ADR-006 — Proyecto persistido y hash canónico del modelo

**Estado: ACCEPTED.** Aprobado por el usuario el 2026-10-31 («Acepto E1 y apruebo ADR-006») en la puerta de E1 de T-012 (plan D10-A). Aprobarlo no acredita validación.

**Fase:** F4 (adelantado). **Ticket:** T-012 ([plan](../../plans/T-012.md), D1–D12).

**Relación con otros ADR.**
- [ADR-001](ADR-001-convenciones-dominio.md) §4: este ADR define la codificación canónica y el hash que ADR-001 dejó pendientes. La nota de «Ampliación T-012» en ADR-001, ADR-002 y ADR-003 se escribe al cierre de E2 (retirada de la excepción de procedencia sin hash).
- ADR-002, ADR-003: sin cambios en E1.

## Contexto

Hasta T-020 el modelo vive solo en memoria. DAT-001 (P0) exige guardar y abrir un proyecto con comprobación de integridad; DAT-004 exige ligar cada ejecución a un hash de versión del modelo; ADR-001 §4 exige definir y probar antes la codificación canónica (orden estable, SI, referencias resueltas, floats finitos, tratamiento de −0,0 y exclusión de datos de presentación).

## Decisión

1. **Contenedor.** Un único archivo JSON UTF-8: `{format: "veritas-project", schema_version, app_version, canon_version, content, model_hash, integrity}`. `content` = `{model, settings}`; `settings.user_profiles` guarda los perfiles de presentación de usuario (unidad y precisión por magnitud); los integrados no se guardan. Sin dependencias nuevas (`json`, `hashlib`, `os`).
2. **Contenido del modelo.** La revisión actual de nudos, barras, apoyos, casos y cargas (en orden de creación) y todas las revisiones de material y de sección, con etiquetas y revisiones, y `model_revision`. Los floats se escriben con su `repr` (exacto al releer), incluido −0,0.
3. **Restauración.** `StructuralModel.snapshot()` (vista de lectura) y `StructuralModel.restore(snapshot)` (constructor todo o nada que conserva UUID, revisiones y `model_revision`; valida duplicados, referencias, bases locales, un apoyo por nudo, peso propio y asientos). Son una ampliación pública del dominio; no cambian los métodos existentes.
4. **Codificación canónica `veritas-canon/1`.** JSON UTF-8 sin espacios, claves ordenadas, floats con su `repr` más corto exacto, NaN e infinitos rechazados, enums como cadenas, UUID en minúsculas con guiones, SHA-256 hexadecimal.
5. **Hash del modelo analítico.** SHA-256 de la codificación canónica de `{canon, model}` con −0,0 normalizado a 0,0 **solo en el hash**. Se **excluyen**: etiquetas; nombre y descripción de casos; `revision` de nudos, barras, apoyos, casos y cargas; `model_revision`; las revisiones de material y de sección que ninguna barra referencia. Se **incluyen**: id y contenido de cada entidad, y las referencias de barra a material y sección con su revisión. Las listas se ordenan por id. Consecuencia: el hash cambia si y solo si cambia el contenido analítico; no cambia por una etiqueta, un nombre de caso, el orden de inserción ni −0,0 frente a 0,0. La exclusión de la numeración de revisión es una interpretación de ADR-001 §4 («los cambios solo de presentación no alteran el modelo»).
6. **Integridad del archivo.** `integrity` = SHA-256 de la codificación canónica del resto de la envoltura (sin normalizar −0,0; cubre etiquetas y ajustes). Un reformateo de espacios no la altera; cualquier cambio de valor sí. `model_hash` se vuelve a calcular al abrir y debe coincidir (con el mismo `canon_version`).
7. **Validación al abrir (NFR-006).** Solo datos: nada del archivo se ejecuta. Campos exactos (ni desconocidos ni ausentes), tipos y rangos, claves JSON repetidas, `NaN`/`Infinity`, tamaño máximo 64 MiB y profundidad máxima 32. Orden: JSON → formato → integridad → versión y migración → validación estricta → restauración → hash. Todo o nada. Errores con **códigos existentes** (`INPUT_INVALID_TYPE`, `INPUT_OUT_OF_RANGE`, `INPUT_NONFINITE`, `REFERENCE_NOT_FOUND`, `REVISION_CONFLICT`, `UNSUPPORTED_CONFIGURATION`) y claves `project.*` nuevas en `messages_es`; la tabla de ADR-001 §5 no cambia. Una falla de integridad es `IntegrityError` (subclase de `InputValueError`) para que la frontera la distinga.
8. **Escritura segura.** Temporal en el mismo directorio, `fsync`, copia del archivo previo a `<archivo>.bak` y `os.replace`. Un fallo deja intacto el archivo anterior y no deja temporales.
9. **Migraciones (DAT-002).** `schema_version` entero; cadena `N → N+1` sobre `content`, aplicada tras verificar la integridad del archivo tal como se guardó y antes de validar. Versión mayor que la soportada: `UNSUPPORTED_CONFIGURATION`. El esquema inicial es la v1; no hay migraciones reales.
10. **Frontera CLI.** `veritas project demo|open|verify`: 0 correcto; 2 archivo inválido; 3 integridad no superada (mensaje en stderr). No cambia ninguna salida aceptada.
11. **Ejecuciones históricas (E2, implementada).** Esquema v2: `content.runs`, lista de registros (`persistence/runs.py`) con `kind` (plana o espacial), `model_hash`, `canon_version`, la corrida completa (procedencia, `request_json`, salida bruta, diagnósticos) y su resultado normalizado (`results`, nulo si no hay). Se serializan con un codec genérico estricto (`persistence/codec.py`, sin ejecutar nada). Solo se añaden (`append_runs`); un `run_id` repetido se rechaza. Una corrida FAILED se guarda como FAILED sin resultados y nunca como válida; los resultados de otra corrida o sobre una corrida no SUCCEEDED se rechazan. La vigencia (`validity`) es CURRENT si el hash guardado coincide con el del modelo actual y STALE si no (o si la `canon_version` no es la soportada); no se guarda. `AnalysisRun.model_hash` y `SpatialAnalysisRun.model_hash` son opcionales (`None` por defecto) y los fija la persistencia; adaptadores, protocolos y workers no cambian. Un archivo v1 se migra a v2 con `runs` vacío (migración `1 → 2`); un contenido v1 que ya traiga `runs` se rechaza. Se retira la excepción de procedencia de ADR-002 §6 y ADR-003 (hecho 7).
12. **Normativas (T-014 E2, ADR-007).** Esquema v3: `content.norms = {user, selected}` con todas las revisiones de las configuraciones de usuario y la revisión seleccionada. Migración `2 → 3` con `norms` vacío. No entran en el hash ni en `analytic_form` y no caducan ejecuciones; `canonical.py` no cambia. `save_project` y `append_runs` conservan las normativas; `update_norms` las guarda sin tocar el resto.

## Alternativas descartadas

ZIP con partes (D1-B) y SQLite (D1-C); hash incluyendo etiquetas o todas las revisiones (D4-B: caducaría resultados por un cambio de etiqueta); hash de la serialización del archivo (D4-C); escritura directa (D6-B); reconstruir con los `add_*` (D3-B: cambia los UUID); renombrar el perfil «N-mm» (D12-B: cambia salidas aceptadas).

## Consecuencias

- Cualquier cambio de los campos analíticos de una entidad, del orden de claves o de la representación de floats cambia el hash: exige un `canon_version` nuevo y una migración de los hashes guardados. El valor fijo de la batería (`tests/test_persistence_hash.py`) lo protege.
- Una sección recién construida puede dar un último ulp distinto de la constante de torsión entre plataformas (usa `math.exp`), y con él otro hash; una sección leída de un archivo no, porque sus propiedades se guardan y no se recalculan. En E2, una sección reconstruida podría marcarse STALE sin cambio real: la vigencia debe compararse siempre con el hash del modelo cargado del archivo. El valor fijo de la batería usa literales para esa constante.
- Una codificación canónica distinta de la soportada se rechaza al abrir (`UNSUPPORTED_CONFIGURATION`): no se acepta un hash sin poder verificarlo.
- El archivo es legible y diferenciable pero sin compresión: los archivos con muchas corridas (E2) serán grandes.
- El modelo solo conserva la revisión actual de nudos, barras, apoyos y cargas; la historia completa y el deshacer llegan con F4.
- El perfil de presentación «N-mm» (kg, kg/m³, m/s²) no es un sistema N-mm-t coherente; sigue sin renombrarse. Los perfiles de usuario derivados de él heredan esa observación.
- Aplicación experimental; no válida para justificar obras reales sin validación independiente.

## Historial

- 2026-10-31: redactado con E1 de T-012 (PROPUESTO).
- 2026-10-31: aprobado por el usuario (ACCEPTED).
- 2026-11-02: E2 implementada (esquema v2, ejecuciones históricas y vigencia, `model_hash` en las corridas); notas en ADR-001, ADR-002 y ADR-003.
- 2026-11-02: nota de T-014 E2 (esquema v3 con `norms`, migración 2 → 3).
