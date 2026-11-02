# ADR-007 — Configuración de normativas

**Estado: ACCEPTED.** Redactado con E1 de T-014 (2026-11-02) y aprobado por el usuario el 2026-11-02 («Acepto E1 y apruebo ADR-007») en la puerta de E1. Aprobarlo no acredita validación normativa ni implementa comprobaciones.

**Fase:** F7A (adelantado, solo capa de datos). **Ticket:** T-014 ([plan](../../plans/T-014.md), D1–D10 = A, aprobado 2026-11-02).

**Relación con otros ADR.**
- [ADR-001](ADR-001-convenciones-dominio.md): reutiliza `RevisionRef` (id, revisión exactos), `core.units` y los diagnósticos con la tabla de códigos de §5 (sin códigos nuevos).
- [ADR-006](ADR-006-proyecto-persistido-hash-canonico.md): el formato persistido (esquema v3) y la selección de normativa del proyecto llegan en E2; una nota breve en ADR-006 se escribe al cierre de E2. En E1 no se toca la persistencia.

## Contexto

`CLAUDE.md` exige un editor manual de configuración de normativas (crear, editar, copiar, borrar) para los casos no planificados. No hay texto normativo verificado en `docs/references/` (TK-007) y DEC-002 fija la edición consolidada del CTE antes de codificar fórmulas (F7A), de modo que la aplicación no puede traer coeficientes sin inventarlos. NOR-004 y NOR-010 exigen que ninguna comprobación incompleta se muestre como aprobada y que el aluminio quede `NO_COMPROBADA`.

## Decisión

1. **Entidad.** `NormConfiguration` es una revisión inmutable con `id` (UUID v4), `revision` (≥ 1), `name`, `kind` (`PROTECTED` | `USER`), `system` (`CTE` | `EN` | `SIA` | `OTHER`), `jurisdiction`, `edition` (texto; «NO FIJADA» si no se conoce), `documents` (tupla de textos únicos), `description`, `derived_from` (`RevisionRef` opcional, solo informativa, nunca la propia entidad) y `parameters`. Vive en `veritas.design_codes.norms`; dominio puro.
2. **Parámetros.** `NormParameter` = `key` (`[a-z0-9_.]+`, única en la configuración), `value` (SI, finito; el −0,0 se conserva), `dimension`, `unit` (símbolo del registro de `core.units`, de la misma dimensión; se guarda el símbolo canónico), `source` (cláusula o documento, **obligatoria**) y `note`. No hay catálogo cerrado de claves: nada de contenido normativo en el código.
3. **Protegida.** `veritas.design_codes.builtin.CTE_PROTECTED`: «CTE España (DB-SE, DB-SE-AE, DB-SE-A, DB-SE-M)», UUID `5e0f6c1a-3b7d-4c52-9a41-7d2e8b6f0c13`, revisión 1, edición «NO FIJADA», **sin parámetros**. Es una plantilla de identificación; F7A añadirá una revisión protegida nueva cuando fije la edición con referencias verificadas.
4. **Operaciones.** `NormRegistry` (`veritas.design_codes.registry`):
   - *crear*: revisión 1, `USER`, id nuevo;
   - *editar*: revisión n+1 del mismo id; el contenido idéntico (comparado por `repr`, de modo que −0,0 ≠ 0,0) no crea revisión; `id`, `kind`, `system` y `derived_from` no cambian; las revisiones anteriores no se modifican;
   - *copiar*: id nuevo, revisión 1, `USER`, `derived_from` = revisión exacta de origen; admite copiar protegidas;
   - *borrar*: retira todas las revisiones; el id no se recicla; borrar el origen de una copia no invalida la copia.
   Cada operación valida antes de modificar: un rechazo no deja rastro.
5. **Protección.** Editar o borrar una protegida es `UNSUPPORTED_CONFIGURATION` con clave `norm.protected`. Borrar la configuración seleccionada del proyecto es `UNSUPPORTED_CONFIGURATION` con clave `norm.in_use`: el registro consulta el gancho `selected_guard(id) -> bool`; la selección y su persistencia son de E2. Lectura de una revisión inexistente o borrada: `REFERENCE_NOT_FOUND`.
6. **Cobertura.** `coverage(config, family)` devuelve siempre `Coverage.NO_COMPROBADO` mientras no exista un plugin de comprobación registrado para (sistema, edición, familia); hoy no hay ninguno. La cobertura **no es un campo** de la configuración, no se guarda y no es editable: un archivo editado a mano no puede declarar «aprobado», y el aluminio queda `NO_COMPROBADO` por construcción.
7. **Independencia del análisis.** Las configuraciones no forman parte del modelo analítico: no entran en `analytic_form` ni en el hash `veritas-canon/1`, y su cambio no caduca ejecuciones (se comprueba en E2 con la persistencia).
8. **Diagnósticos.** Códigos existentes con claves `norm.*` en `messages_es`: `norm.protected`, `norm.in_use`, `norm.duplicate_parameter`, `norm.duplicate_document`, `norm.invalid_key`, `norm.self_derived`; `units.incompatible` y `model.reference_not_found` se reutilizan.
9. **Pendiente (E2, plan T-014 D6–D8).** Esquema de proyecto v3 (`content.norms = {user, selected}`) con migración 2 → 3, selección opcional (id, revisión), y grupo `veritas norm` (list, show, new, copy, set, delete `--confirmar`, use). Fuera de alcance: interfaz gráfica, plugins y comprobaciones (F7A/F7B), biblioteca global, importación/exportación.

## Alternativas descartadas

Diccionario libre clave-valor (D2-B: sin unidades ni fuente); catálogo cerrado de coeficientes CTE (D2-C) y protegida con coeficientes «de memoria» (D3-B): inventarían contenido normativo; editar en sitio (D4-B) y borrado lógico (D4-C); campo de cobertura en la configuración (D5-B): el usuario podría afirmar una cobertura que el motor no tiene.

## Consecuencias

- Hasta F7A las configuraciones son datos: nada las consume; los parámetros que el usuario añada son responsabilidad suya, con la fuente que declare.
- La protegida no trae edición fijada ni coeficientes; copiarla es el punto de partida del usuario.
- `NormRegistry` guarda las revisiones en memoria; sin persistencia no sobreviven a la sesión (E2).
- Borrar es irreversible dentro del registro; la copia `.bak` del guardado de proyecto (E2) es la única red.
- Aplicación experimental; no válida para justificar obras reales sin validación independiente.

## Historial

- 2026-11-02: redactado con E1 de T-014 (PROPUESTO).
- 2026-11-02: aprobado por el usuario (ACCEPTED) en la puerta de E1.
