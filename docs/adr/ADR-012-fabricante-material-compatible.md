# ADR-012 — Fabricante y materiales compatibles del perfil de catálogo (catálogo v4; proyecto v6 en E2)

**Estado: ACCEPTED** (2026-11-06, con la aceptación de E1 de T-028; plan [T-028](../../plans/T-028.md) aprobado con D1–D8 = A). La enmienda de E2 (snapshot, esquema de proyecto v6 y bloqueo) se añade al aceptarse E2. Aceptarlo no valida ningún dato de catálogo: «compatible» es una afirmación del archivo importado, no una comprobación normativa.

**Relación con otros ADR.**
- [ADR-009](ADR-009-catalogo-sqlite.md): enmienda el esquema del catálogo a **v4** (dos columnas derivadas); `content_json` sigue siendo la fuente de verdad.
- [ADR-010](ADR-010-snapshot-perfil-catalogo.md): el snapshot incorporará los dos campos en E2 (esquema de proyecto v6).
- [ADR-011](ADR-011-perfiles-parametricos-cortante-contrastado.md): sin cambios; los perfiles paramétricos no tienen fabricante.

## Contexto

SEC-005 pide buscar por fabricante y por material compatible (TK-023). El modelo de catálogo no tenía esos campos y la compatibilidad se decidía solo por forma (perfil metálico con madera bloqueado). El usuario decidió el 2026-11-06 que el material compatible es **bloqueante**.

## Decisión (E1)

1. **Campos opcionales en `ProfileData`:** `manufacturer: str | None` (texto recortado y no vacío) y `compatible_materials: tuple[str, ...]`, subconjunto de `MaterialFamily` (`STEEL`, `ALUMINIUM`, `TIMBER`), sin duplicados y en orden canónico de la familia. Vacío = «no declara».
2. **Serialización condicional.** El documento canónico incluye `manufacturer` y `compatible_materials` **solo si hay dato**. Los hashes de contenido de los perfiles existentes, `same_content` y la vigencia (`compare_with_catalog`) no cambian. El decodificador rechaza `manufacturer: null` y `compatible_materials: []` (no son canónicos).
3. **Añadir o cambiar los campos es una revisión nueva** del perfil (cambia el contenido).
4. **Esquema SQLite v4** (`_migration_3_to_4`): columnas nulables `manufacturer` y `compatible_materials` (lista separada por comas, orden canónico). `verify` las contrasta con `content_json`. La migración no reescribe contenido ni hashes.
5. **Importador.** Campos de texto `manufacturer` y `compatible_materials` (términos separados por `;` o `,`). Cada término es una familia (sin distinguir mayúsculas) o un alias del mapeo (`material_aliases`, solo en el documento del mapeo si existe, de modo que los `mapping_sha256` existentes no cambian). **Un término desconocido rechaza la fila** con `catalog.row_material_unknown`; no se ignora.
6. **Búsqueda (`catalog search`).** `--fabricante` (coincidencia exacta sin distinguir mayúsculas ni acentos), `--material` (el perfil lo declara compatible) y `--incluir-sin-declarar`. Un perfil que no declara materiales no sale con `--material` salvo con esa opción.
7. **Ficha y exportación.** La ficha muestra los campos solo si existen. El CSV nativo añade las columnas solo si algún perfil las declara (reimportable sin mapeo); el JSON ya los lleva por el documento. El clon los copia.

## Pendiente (E2)

Snapshot con ambos campos (opcionales), esquema de proyecto v6 con migración 5 → 6 que solo verifica, y `compatibility_errors` **bloqueante**: si el perfil declara materiales y el de la barra no figura, bloqueo; sin declarar, se mantiene la regla actual por forma; ambas reglas se acumulan (la forma metálica con TIMBER sigue bloqueada aunque se declare TIMBER).

## Consecuencias y límites

- Cambio aditivo: ningún catálogo ni proyecto existente cambia de contenido ni de hash.
- Un archivo v4 con campos comerciales no lo abre una versión anterior (rechaza `user_version` mayor).
- Sin datos reales en el repositorio (TK-019).

## Historial

- 2026-11-06: propuesto con E1 de T-028.
- 2026-11-06: ACCEPTED con la aceptación de E1.
