# ADR-012 — Fabricante y materiales compatibles del perfil de catálogo (catálogo v4; proyecto v6 en E2)

**Estado: ACCEPTED** (2026-11-06, con la aceptación de E1 de T-028; plan [T-028](../../plans/T-028.md) aprobado con D1–D8 = A). La enmienda de E2 (snapshot, esquema de proyecto v6 y bloqueo) está IMPLEMENTADA y ACEPTADA (2026-11-06, con E2); incluye la aclaración de la clave de mensaje de R1, ya conforme por el usuario. Aceptarlo no valida ningún dato de catálogo: «compatible» es una afirmación del archivo importado, no una comprobación normativa.

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

## Proyecto y bloqueo (E2)

- `CatalogSnapshot` lleva `manufacturer` y `compatible_materials` (opcionales; mismo orden y normalización que el catálogo). Esquema de proyecto **v6**: las claves solo se escriben si hay dato, de modo que el hash de los modelos v1–v5 no cambia (fixture auténtico `tests/data/proyecto_v5_ref.json`). La migración 5 → 6 solo verifica: un archivo v5 con esas claves se rechaza; `[]` se rechaza en v6.
- `compatibility_errors` es **bloqueante** y acumula dos reglas: (R1) si el perfil declara materiales y la familia del material de la barra no figura, bloqueo `section.replace_material_not_declared` con la lista declarada; (R3) la regla de forma (perfil metálico con TIMBER) sigue vigente aunque se declare TIMBER. Sin declarar (vacío o snapshot anterior) no se infiere nada (R2/R5).
- Aclaración respecto al plan: R1 usa una clave de mensaje propia (no `section.replace_incompatible_material`) para poder mostrar la lista declarada sin alterar el texto de la regla de forma; ambas son bloqueos y se informan igual.
- `assign-profile`, `replace-profile` y `update-profile` (estricto o `--parcial`) aplican el bloqueo; el informe y `sections` muestran fabricante y materiales si constan; `update-profile` lista sus cambios.

## Consecuencias y límites

- Cambio aditivo: ningún catálogo ni proyecto existente cambia de contenido ni de hash.
- Un archivo v4 con campos comerciales no lo abre una versión anterior (rechaza `user_version` mayor).
- Sin datos reales en el repositorio (TK-019).

## Historial

- 2026-11-06: propuesto con E1 de T-028.
- 2026-11-06: ACCEPTED con la aceptación de E1.
