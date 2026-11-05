# ADR-010 — Snapshot de perfil de catálogo en el proyecto (esquema v4)

**Estado: ACCEPTED.** Redactado con E1 de T-024 ([plan](../../plans/T-024.md), D1–D9 aprobados el 2026-11-05) y aceptado por el usuario el 2026-11-05 junto con E1. Aceptarlo no acredita ningún dato de perfil ni habilita el cálculo de perfiles de catálogo (ver «Límites»).

**Relación con otros ADR.**
- [ADR-009](ADR-009-catalogo-sqlite.md): el perfil de catálogo es una entidad propia; aquí se define su copia dentro del proyecto. Se reutilizan sus estados de propiedad (`IMPORTADO_SIN_VERIFICAR`, `NO_DISPONIBLE`).
- [ADR-006](ADR-006-proyecto-persistido-hash-canonico.md): el hash canónico del modelo ya incluye las secciones usadas; el snapshot entra por la misma codificación.
- [ADR-001](ADR-001-convenciones-dominio.md): UUID v4, revisiones inmutables, SI interno y diagnósticos con clave estable (sin códigos nuevos).

## Contexto

Un perfil de catálogo no puede representarse hoy como `SectionRevision` (solo rectangular, validada contra b·h) y el proyecto no debe depender del archivo SQLite para abrirse, calcularse ni auditarse (DAT-003).

## Decisión

1. **Una sola entidad versionada (D2-A).** `SectionFamily.CATALOGO` y `SectionRevision.parameters: RectangularParameters | CatalogSnapshot`. La barra sigue refiriendo una única revisión de sección (GEO-003). Familia y parámetros deben coincidir (`section.family_mismatch`).
2. **`CatalogSnapshot` en `core/sections/catalog.py`**, inmutable y sin dependencias de `catalogs`, `persistence` ni `sqlite3`: catálogo (id, nombre, fuente, licencia, edición); perfil (id, revisión, serie, designación, forma de un conjunto cerrado `PROFILE_SHAPES`); `source_sha256`; dimensiones (m, > 0, únicas, ordenadas); procedencia del perfil; propiedades copiadas (área, Iy, Iz, J, masa lineal) con su unidad SI, origen, método y estado; claves de aviso.
3. **Sin datos derivados en E1 (D3 = C).** Una propiedad ausente es `NO_DISPONIBLE` con valor `None`, nunca 0; J no se deduce de Iy + Iz. Las propiedades de la `SectionRevision` de catálogo son idénticas a las del snapshot (A, Iy, Iz, J) y el centroide e `Iyz` deben ser `NO_DISPONIBLE` (`section.catalog_not_derivable`). E2 podrá declarar `Iyz = 0` y centroide 0 en familias bisimétricas, marcados como derivados de la simetría, solo tras contrastarlos con un voladizo analítico de IPE y con un ADR o enmienda (TK-026).
4. **Estados.** Solo `IMPORTADO_SIN_VERIFICAR` y `NO_DISPONIBLE`; `CALCULADO`, `CONTRASTADO` y `VERIFICADO` se rechazan en el snapshot y al decodificar.
5. **Puente en `catalogs/bridge.py`.** `snapshot_from_profile(perfil, catálogo)`, `section_from_snapshot` y `section_from_profile`. Es el único módulo que une `catalogs` con `core.sections`; no abre el archivo SQLite. `source_sha256` es el `profile_sha256` de la revisión copiada: `core` solo valida su formato y permite a E2 avisar de «desactualizado» comparando contra el catálogo si se aporta.
6. **Esquema v4 (D7-A).** `sections[].parameters` se decodifica según `family` (discriminador existente): `{b, h}` para RECTANGULAR y el objeto del snapshot (campos exactos: `catalog`, `profile`, `dimensions`, `provenance`, `area`, `iy`, `iz`, `torsion_constant`, `linear_mass`, `warnings`) para CATALOGO. Sin campos de más ni de menos. Los números se guardan con su `repr` (exactos, −0,0 incluido).
7. **Migración 3 → 4 (`_v3_to_v4`).** El contenido v3 no cambia de forma; solo se comprueba que toda sección sea RECTANGULAR (otra familia en un v3 se rechaza, nunca se reinterpreta). Un archivo v1–v3 sigue abriéndose; guardar siempre escribe v4.
8. **Integridad (D8-A).** El hash canónico del modelo cubre el snapshot completo de cada sección usada. `open_project` rechaza (a) un archivo alterado sin refirmar (integridad del archivo), (b) uno refirmado con un snapshot cambiado (hash del modelo distinto del guardado) y (c) uno refirmado y con hash recalculado pero incoherente (propiedades distintas del snapshot, estado o forma inválidos). No requiere el catálogo.
9. **Diagnósticos.** Claves `section.catalog_*`, `section.family_mismatch`, `section.not_rectangular` y `catalog.profile_not_in_catalog` en `messages_es`, con códigos existentes. `require_rectangular()` da un error explícito a quien necesita b y h (`revise_rectangular_section`, `veritas section rect`).

## Consecuencias y límites

- Cambio aditivo del contrato público de `SectionRevision` (aprobado en D2).
- **Ningún perfil de catálogo es calculable todavía:** los adaptadores plano y espacial rechazan `Iyz` y centroide `NO_DISPONIBLE` (`fem.section_property_unavailable`), que es el comportamiento correcto («un análisis incompleto nunca es válido»). TK-026 lo resuelve en E2 para las familias bisimétricas.
- E1 no expone CLI de asignación, sustitución masiva ni dictámenes FEM/norma (E2) ni GUI (TK-024).
- `RectangularParameters` y los proyectos rectangulares no cambian de contenido; su hash analítico es idéntico entre v3 y v4.
- Los datos de catálogo siguen siendo sintéticos en los tests (TK-019, NFR-012).

## Enmienda de E2 (2026-11-05): simetría, dictámenes y sustitución

Cambios aditivos aprobados en el plan T-024 (D3 = C, D4-A, D5-A, D6-A, D8-A); aceptados con E2 el 2026-11-05.

1. **TK-026: datos derivados por simetría.** `BISYMMETRIC_SHAPES` = I, SHS, RHS, CHS, RECT, ROUND. Para ellas el puente fija centroide = 0 e `Iyz` = +0.0 con estado `CALCULADO`, origen «simetría de la familia» y método «deducido de la doble simetría de la forma declarada (no importado)»; `SectionRevision` solo acepta esos valores con ese origen y método y solo en esas formas (cualquier otro valor, origen o forma se rechaza). U, L, T y OTRO siguen `NO_DISPONIBLE`. Condición declarada: los ejes y/z del perfil son los de simetría (convención del catálogo). `derive_symmetry=False` mantiene el comportamiento de E1. Contraste: un voladizo con un perfil en I sintético reproduce P·L³/(3·E·Iy) en los adaptadores plano y espacial (`tests/test_catalog_assign.py`).
2. **Dictámenes (SEC-013).** `assess_section` devuelve `fem_ready` (A, Iy, Iz, J, centroide e Iyz disponibles, con un diagnóstico por ausente) y `code_coverage` = `NO_COMPROBADO` con motivo; nunca un único «apta».
3. **Sustitución masiva (SEC-012).** `StructuralModel.replace_sections` con informe por barra (APLICADA, SIN_CAMBIO, OMITIDA, BLOQUEADA). Una sola revisión de modelo y reversible. Estricto por defecto; `partial=True` aplica a las compatibles y el informe lista siempre el resto. **Compatibilidad (decisión de detalle):** el catálogo no trae el material compatible (TK-023), por lo que solo se bloquea un perfil metálico (I, U, L, T, SHS, RHS, CHS) con material de madera; se avisa si la masa lineal cambia más de un 20 % o si la sección nueva no es apta para FEM.
4. **CLI.** `veritas project assign-profile`, `replace-profile`, `sections` y `verify --db` (`cli_profiles.py`). El catálogo se abre solo para leer el perfil o comparar su vigencia; el proyecto guarda el snapshot y una sola sección por perfil y revisión (`ensure_section`). `Freshness` (VIGENTE, REVISION_POSTERIOR, CONTENIDO_DISTINTO, AUSENTE) es informativo y no modifica nada.
5. **Límites.** Un perfil sin J no se calcula en 3D (nunca se deduce); un perfil U/L/T tampoco en plano (`Iyz`). «I» se declara bisimétrica por familia: un perfil I asimétrico mal etiquetado en el catálogo no se detecta (el contraste con el fabricante es de F3A, TK-021).

## Historial

- 2026-11-05: propuesto con E1 de T-024.
- 2026-11-05: ACCEPTED con la aceptación de E1.
- 2026-11-05: enmienda de E2 (simetría, dictámenes, sustitución y CLI), ACEPTADA.
