# ADR-008 — Visor 3D derivado del dominio y extra opcional `gui`

**Estado: ACCEPTED.** Redactado con E1 de T-011 y aprobado por el usuario el 2026-11-02 («Acepto E1 y apruebo ADR-008») en la puerta de E1. Aprobar este ADR no acredita ningún cálculo ni normativa.

**Fase:** F4 (adelantado, solo lectura). **Ticket:** T-011 ([plan](../../plans/T-011.md), D1–D8 aprobados 2026-11-02; D2 y versiones confirmadas expresamente).

**Relación con otros ADR.** [ADR-001](ADR-001-convenciones-dominio.md): ejes globales y base local sin transformar. [ADR-006](ADR-006-proyecto-persistido-hash-canonico.md): el visor abre proyectos con `persistence`.

## Contexto

SRS S3D-010 pide un viewport mínimo; DEC-001 fija PySide6 + PyVista/VTK. La propuesta exige que «la geometría persistida será independiente de la escena PyVista». Ninguna dependencia GUI existía en `pyproject.toml`.

## Decisión

1. **Escena derivada.** `veritas.gui.scene.build_scene(ModelSnapshot) -> Scene` produce datos planos (nudos, segmentos, apoyos, ejes locales, límites). Es una proyección de solo lectura: se reconstruye entera tras cada cambio; nunca es fuente de verdad. No importa PySide6, PyVista ni NumPy y se prueba en toda la suite.
2. **Sin recálculo geométrico en la GUI.** Los ejes locales salen de `recompute_local_axes` con la orientación guardada; las coordenadas son las del dominio (m, base global).
3. **Extra opcional `gui`.** `[project.optional-dependencies] gui` con `PySide6==6.12.0`, `pyvista==0.49.2` y `pyvistaqt==0.13.2` (transitivas fijadas en `uv.lock`: `vtk==9.7.1`, `qtpy`, `numpy`, `matplotlib`, etc.). `uv sync --locked` sin extras sigue instalando solo lo anterior; se instala con `uv sync --locked --extra gui`.
4. **Importación diferida.** Qt y PyVista se importan solo dentro de la función que abre la ventana (E2); `veritas` y la CLI funcionan sin el extra.
5. **Aislamiento.** `core` y los demás dominios siguen sin importar GUI (`test_layer_isolation`).
6. **CI.** Los tests de Qt/PyVista (E2) usan `QT_QPA_PLATFORM=offscreen`, se omiten si falta el extra y el extra se instala solo en windows.

## Consecuencias

- Las dependencias GUI son pesadas (VTK, Qt): por eso son opcionales.
- Licencias: PySide6 LGPL-3/GPL; VTK y PyVista BSD/MIT. La licencia del producto (DEC-006) sigue pendiente; no se resuelve aquí.
- La prueba visual real es manual: la CI no ve pantalla.

## Historial

- 2026-11-02: redactado con E1 de T-011 (PROPUESTO).
- 2026-11-02: aprobado por el usuario (ACCEPTED) en la puerta de E1.
- 2026-11-02: E2 implementada (ventana, visor, tablas y `veritas gui`); sin cambios en la decisión. Concreción: la selección por clic se resuelve por proximidad en pantalla (`gui/picking.py`, sin Qt) y no con el picker de VTK; con Qt offscreen VTK no proyecta píxeles, así que la prueba de clic se ejecuta solo en local (ver plan T-011, E2-b y E2-f).
