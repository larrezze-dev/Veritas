# Planes de tickets

Un archivo por ticket: `plans/<ID-TICKET>.md` (p. ej. `plans/T-001.md`).

Cada plan contiene:

1. **Estado de aprobación:** `PROPUESTO`, `APROBADO (fecha, referencia)` o `RECHAZADO`.
2. Alcance, archivos afectados, dependencias, pruebas y criterios de salida (ver `tasks/TEMPLATE.md` y `docs/MODULE_CONTRACT.md`).
3. **Revisiones pendientes:** cambios de alcance, contratos o dependencias posteriores a la aprobación, separados del plan aprobado. No se implementan hasta su confirmación.
4. **Historial:** entradas breves con fecha (propuesta, aprobación, revisiones, cierre).

No crear un plan nuevo para el mismo ticket: editar este documento y registrar el cambio en el historial.
