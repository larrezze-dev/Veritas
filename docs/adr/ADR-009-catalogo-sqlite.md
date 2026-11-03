# ADR-009 — Catálogo de perfiles en SQLite independiente

**Estado: ACCEPTED.** Redactado con E1 de T-022 y aprobado por el usuario el 2026-11-03 («E1 aceptado, ADR-009 aprobado, (b)»). Aprobarlo no acredita ningún dato de perfil ni valida el importador (E2).

**Fase:** F2 (primera porción). **Ticket:** T-022 ([plan](../../plans/T-022.md), D1–D11 = A, aprobado 2026-11-02).

**Relación con otros ADR.**
- [ADR-001](ADR-001-convenciones-dominio.md): UUID v4 y revisiones inmutables, diagnósticos con códigos estables (sin códigos nuevos), unidades SI internas.
- [ADR-006](ADR-006-proyecto-persistido-hash-canonico.md): el catálogo es un archivo **distinto** del proyecto. El snapshot de los perfiles usados dentro del proyecto (esquema v4) es de T-024 y tendrá su propio ADR.
- [ADR-007](ADR-007-configuracion-normativas.md): mismo patrón de entidad versionada con procedencia obligatoria.

## Contexto

La SRS exige catálogos de perfiles con procedencia, licencia de datos y auditoría (DAT-003, SEC-008…011) y la arquitectura prevé SQLite para catálogos. No hay datos de catálogo en el repositorio y no se inventan. `SectionRevision` solo admite hoy la familia rectangular, valida sus propiedades contra b·h y la persistencia asume b y h: asignar un perfil de catálogo a una barra cambia el núcleo y se difiere a T-024.

## Decisión

1. **Entidad propia.** `veritas.catalogs.model.CatalogProfile` (id UUID v4, revisión ≥ 1, `catalog_id`, `ProfileData`) es independiente de `SectionRevision`. `ProfileData` agrupa serie, designación, familia (`ProfileFamily`: I, U, L, T, SHS, RHS, CHS, RECT, ROUND, OTRO), dimensiones opcionales (m, > 0), `ProfileProperties` (área, Iy, Iz, J, masa lineal, cada una un `SectionProperty` SI con estado), `Provenance` (fuente, licencia de los datos, edición, método; todo obligatorio) y claves de aviso. `CatalogInfo` (nombre único sin distinguir mayúsculas, procedencia, `protected`) es la cabecera de cada catálogo.
2. **Estados de propiedad (aditivo).** `PropertyStatus` suma `IMPORTADO_SIN_VERIFICAR`, `CONTRASTADO` y `VERIFICADO` (SRS §5.2). Todo estado distinto de `NO_DISPONIBLE` exige un valor real finito. **Un perfil de catálogo solo admite `IMPORTADO_SIN_VERIFICAR` o `NO_DISPONIBLE`:** `CONTRASTADO` y `VERIFICADO` (reservados a F3A) y `CALCULADO` se rechazan en el modelo y al decodificar, de modo que ningún dato importado ni manipulado se presenta como verificado. Una propiedad no aportada es `NO_DISPONIBLE` con valor `None`, nunca 0; J no se deduce de Iy + Iz.
3. **Almacén.** `sqlite3` de la biblioteca estándar, solo en `catalogs/store.py` (prueba automática de aislamiento). Sin dependencias nuevas.
4. **Archivo reconocible y versionado.** `PRAGMA application_id` = `0x56434154` («VCAT») marca un catálogo de Veritas y `PRAGMA user_version` su esquema (hoy 2, ver la enmienda de E2). Abrir un archivo ajeno, uno sin la marca (incluido cualquier SQLite con `user_version` 0) o un archivo que no es SQLite se rechaza **sin escribir**. Una versión mayor que la soportada se rechaza. Una versión menor se migra con una cadena `N → N+1` en una única transacción; si falta un eslabón se rechaza antes de escribir, y si una migración falla el archivo queda idéntico. Las migraciones son un argumento (como en `persistence.migrations`), lo que permite probar la cadena con migraciones sintéticas. La migración real 1 → 2 llega con la enmienda de E2.
5. **Crear y abrir sin efectos laterales.** `create` rechaza un archivo existente, construye el esquema en un archivo temporal contiguo y lo publica al terminar (un fallo no deja un archivo a medias). `open` usa `mode=rw` (nunca crea) y no escribe si no hay migración.
6. **Transaccionalidad.** Toda escritura va en una única transacción `BEGIN IMMEDIATE … COMMIT` con la conexión en `isolation_level=None` (sin transacciones implícitas) y `BEGIN`/`COMMIT`/`ROLLBACK` explícitos; ante cualquier excepción, incluidas las que no son `Exception`, `ROLLBACK`. Diario por defecto (no WAL). Resultado probado: tras un fallo inyectado a mitad de lote, un lote rechazado o un reimport sin cambios, el archivo es idéntico byte a byte y no quedan archivos auxiliares.
7. **Identidad, revisiones y duplicados (D8).** Clave natural = (catálogo, serie, designación), con los textos sin espacios en los extremos y distinguiendo mayúsculas. Clave nueva = revisión 1 y `profile_id` nuevo. Contenido idéntico a la **última** revisión (comparación por documento canónico, de modo que −0,0 ≠ 0,0) = `UNCHANGED`, sin escribir. Contenido distinto = revisión n+1 con el mismo `profile_id`; las anteriores no se tocan (volver a un valor antiguo es otra revisión). Una clave repetida dentro de un lote se rechaza entera (`REVISION_CONFLICT`, `catalog.duplicate_key`); el importador de E2 rechazará ambas filas antes de llegar aquí.
8. **Protegido (D9).** Un catálogo protegido rechaza escribir perfiles (`UNSUPPORTED_CONFIGURATION`, `catalog.protected`). Protegerlo es irreversible; se edita mediante un clon (T-023). Sin borrar catálogos ni perfiles en T-022.
9. **Documento canónico y hash.** Cada revisión guarda su documento JSON canónico (`veritas-catalog-profile/1`: claves ordenadas, sin espacios, UTF-8, flotantes con su `repr` más corto exacto, NaN e infinitos prohibidos) como **fuente de verdad**, su SHA-256 y, aparte, columnas numéricas derivadas (área, Iy, Iz, J, masa) para las consultas de T-023. Los reales se guardan como texto en el documento y no dependen de la columna `REAL`: la reapertura devuelve los mismos `repr`. El hash se implementa localmente en `catalogs` y **no** importa `persistence` (que está por encima en la arquitectura). La decodificación es estricta (claves exactas, tipos, rangos y estados) y todo o nada.
10. **Verificación.** `CatalogStore.verify()` no modifica nada y devuelve todos los problemas (no solo el primero): cabecera, `integrity_check` y `foreign_key_check` de SQLite, hash de cada revisión, documento válido y canónico, coincidencia de las columnas derivadas con el documento, revisiones consecutivas desde 1 y coherencia identidad↔clave natural. Los problemas son `Diagnostic` con claves `catalog.verify_*` y código `INPUT_OUT_OF_RANGE`; el código de salida 3 de `veritas catalog verify` se declaró en E2.
11. **Diagnósticos.** Claves `catalog.*` en `messages_es`, con los códigos existentes: `REFERENCE_NOT_FOUND` (catálogo, perfil o archivo inexistente), `UNSUPPORTED_CONFIGURATION` (protegido, nombre ocupado, archivo existente o ajeno, versión o migración no soportada, error de la base de datos), `REVISION_CONFLICT` (clave repetida en el lote) y `INPUT_*`/`UNITS_INCOMPATIBLE` (contenido). `CatalogFileError` distingue los fallos del archivo (exit 2 en E2).
12. **Sin datos reales.** No se distribuye ninguna tabla de perfiles. Los fixtures son sintéticos; cualquier dato real (p. ej. PyPI `steel-sections`, licencia **no** verificada contra el repositorio original) se usa solo en local, sin commitear.

## Consecuencias y límites

- **Hallazgo resuelto (opción b, aprobada 2026-11-03).** Ampliar `PropertyStatus` ensanchaba lo que `SectionRevision` acepta: `core/sections/section.py` solo validaba positividad y +0.0 con estado `CALCULADO`. Cambio mínimo y aditivo: la validación se aplica a todo estado distinto de `NO_DISPONIBLE` (`section.py`, una condición). Los *golden* no cambian y las tres pruebas, antes `xfail(strict)`, son pruebas normales en `tests/test_catalog_layers.py`.
- El modelo no obliga a que A, Iy o Iz estén disponibles: el importador (E2) declara las columnas obligatorias y T-024 exigirá lo que necesite la FEM.
- Sin multiusuario ni concurrencia entre procesos más allá del bloqueo de SQLite (`timeout` de 5 s).
- Sin `read_only` ni apertura de solo lectura sin migrar (E2 lo añade si la CLI lo necesita).
- Los avisos de coherencia (D11) y el importador son de E2; el modelo solo transporta sus claves.

## Enmienda de E2 (2026-11-03): lotes de importación, esquema v2

Aprobada por el usuario en la consulta de inicio de E2 (opción b: «esquema v2 ahora»), para cumplir D6-A (el lote se guarda con su mapeo, el SHA-256 del archivo y la fecha). Cambia el contrato público de E1 solo de forma aditiva:

1. **Esquema v2.** `user_version` = 2. Tabla `import_batches` (id UUID, catálogo, fecha UTC `YYYY-MM-DDTHH:MM:SSZ`, nombre y formato del archivo, SHA-256 del archivo, documento canónico del mapeo y su SHA-256, filas leídas/aceptadas/rechazadas y perfiles creados/revisados/sin cambios) y columna `profile_revisions.batch_id` (nula en las revisiones anteriores). Migración 1 → 2 (`MIGRATIONS[1]`) en una transacción; `create` construye v1 y aplica las mismas migraciones, de modo que un archivo nuevo y uno migrado tienen el mismo esquema (prueba).
2. **API.** `CatalogStore.import_batch(catalog_id, items, source)` escribe los perfiles y el lote en la **misma** transacción y devuelve `BatchResult(batch, results)`; un fallo en cualquier punto no deja ni perfiles ni lote (prueba byte a byte). `put_profiles` no cambia. `list_batches` y `revision_batch` leen. El reloj es inyectable (`clock`) y debe devolver una fecha con zona horaria. `ImportSource` e `ImportBatch` (dominio, validación estricta) viven en `catalogs/model.py`.
3. **`verify`.** Comprueba cada lote (contenido válido, hash del mapeo, recuentos coherentes) y que las revisiones enlazadas sean las que el lote dice haber escrito y de su catálogo (`catalog.verify_batch`).
4. **Procedencia por perfil.** El SHA-256 del archivo no entra en la procedencia de cada perfil: reimportar un archivo con un valor cambiado revisaría las 100 filas contra D8. La procedencia (fuente, licencia, edición) viene de la cabecera del catálogo y el método, del mapeo.
5. **Consecuencias.** Reimportar sin cambios deja los perfiles intactos pero **registra un lote** (el archivo cambia: es una auditoría, no un fallo). Abrir un catálogo v1 lo migra (también con `list`, `show` o `verify`). El enlace revisión → lote es nulo en las revisiones anteriores a la migración. Un lote sin ninguna fila aceptada no se escribe.
6. **Sin cambios.** Entidad `CatalogProfile`, estados, documento canónico, hash de revisión y reglas de D8/D9 no cambian.

## Enmienda de T-023 E2 (2026-11-03): trazabilidad de los clones, esquema v3

Decidida por delegación del usuario («profesional y exacto») al aprobar el plan T-023 (D7 = B). Cambia el contrato de E2 solo de forma aditiva:

1. **Esquema v3.** `user_version` = 3. `catalogs.cloned_from` (clave foránea al catálogo de origen; nulo si no es un clon) y, en `profile_revisions`, `cloned_from_profile_id` y `cloned_from_revision` (la revisión exacta de la que se copió; nulos si no es una copia). Los dos enlaces de revisión **no** llevan clave foránea: el padre sería compuesto y `profile_id` solo no es único; los comprueba `verify`. Migración 2 → 3 en la cadena (`create` aplica las mismas); los archivos v1 y v2 se migran al abrirse y un v3 queda idéntico byte a byte.
2. **API.** `clone_catalog(origen, nombre, descripcion=None)` crea, en una sola transacción, un catálogo no protegido con la fuente, la licencia, la edición y (salvo que se indique otra) la descripción del origen, y copia la última revisión de cada perfil como revisión 1, con identificadores nuevos y el mismo contenido (la procedencia de cada perfil se conserva). Funciona también con un origen protegido y vacío; el origen no se modifica. `clone_source(catalogo)` y `revision_origin(perfil, revisión)` leen la trazabilidad; `CatalogInfo`, `CatalogProfile`, el documento canónico y su hash no cambian.
3. **`verify`.** Para cada revisión copiada: enlaces completos, solo en la revisión 1, a una revisión de origen que existe y es del catálogo `cloned_from` del clon, con el mismo contenido (`same_content`); y un catálogo no es clon de sí mismo (`catalog.verify_clone`). Las revisiones que nacen de reimportar o editar el clon no llevan enlace y es legítimo.
4. **Consecuencias.** Un clon de un clon enlaza con su origen inmediato. `show` y `ficha` dicen «clon de …» en vez de «anterior al registro de lotes». Los favoritos y recientes viven fuera del catálogo (archivo de usuario), por lo que un catálogo protegido sigue siendo de solo lectura.

## Historial

- 2026-11-02: redactado con E1 de T-022 (PROPUESTO).
- 2026-11-03: enmienda de E2 (esquema v2, lotes de importación) aprobada por el usuario en la consulta de inicio de E2.
- 2026-11-03: aprobado por el usuario (ACCEPTED). Hallazgo de `section.py` resuelto con la opción (b).
- 2026-11-03: enmienda de T-023 E2 (esquema v3, trazabilidad de los clones) decidida por delegación del usuario; pendiente de la aceptación de E2.
