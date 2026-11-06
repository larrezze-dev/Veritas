# Salida aceptada de `veritas analyze` (golden)

Salida normalizada de `analyze demo` (cinco casos), `analyze uniones` y `analyze referencias`, tomada con la CLI aceptada antes de añadir `analyze espacial` (plan T-018, pieza 7). La normalización está en `tests/cli_golden.py`. `tests/test_analyze_spatial_cli.py` comprueba que esa salida no cambia.

Se compara el texto, las etiquetas (incluidos los dígitos de los identificadores, como `T-FEM-001` o `plan T-009`), las unidades, la estructura y el código de salida; los números se enmascaran.

Solo se regeneran cuando el usuario acepta un cambio de esa salida o de la normalización, y siempre con la CLI que se acaba de aceptar. Si el árbol de trabajo tiene cambios en `cli.py`, se generan desde un worktree de esa versión (`git worktree add`), nunca con `git stash`:

```powershell
uv run python tests/cli_golden.py
```

Historial: 2026-10-29, regenerados con el `cli.py` de 5d69bb8 en un worktree, tras dejar de enmascarar los dígitos de los identificadores (D-d de la revisión de E1). Reaplicando el patrón anterior se obtienen exactamente los archivos previos.

Historial: 2026-10-30, `asientos.txt` nuevo (plan T-016, E2.3-d), generado solo para `analyze asientos` con la CLI de E2.3 (`cli_golden.capture`); los demás archivos no se regeneraron. Su contenido inicial forma parte de la aceptación de E2.3.

Historial: 2026-10-31, `cargas_barra.txt` nuevo (plan T-015, E2.3), generado solo para `analyze cargas-barra` con la CLI de E2.3 (`cli_golden.capture`); los demás archivos no se regeneraron. Su contenido inicial forma parte de la aceptación de E2.3.

Historial: 2026-10-31, `peso_propio.txt` nuevo (plan T-019, E2), generado solo para `analyze peso-propio` con la CLI de E2 (`cli_golden.capture`); los demás archivos no se regeneraron. Su contenido inicial forma parte de la aceptación de E2.

Historial: 2026-10-31, `uniones.txt` regenerado solo para `analyze uniones` con la CLI de T-020 E2.1 (`cli_golden.capture`): añade el bloque «Salto de extremo J de B2» (6 líneas, declarado en el plan T-020, D6-A); los demás archivos no se regeneraron. Su contenido forma parte de la aceptación de E2.1.

Historial: 2026-11-03, `catalogo_cli.txt` nuevo (plan T-022, E2): sesión completa de `veritas catalog` (crear, simular, importar, reimportar, `--estricto`, listar, mostrar, proteger, verificar y fallos con su código de salida) en un directorio temporal. Lo genera `tests/catalog_cli_golden.py` (`uv run python tests/catalog_cli_golden.py`), no `cli_golden.py`: enmascara solo los UUID, los SHA-256 (completos y de 12 caracteres) y la fecha UTC, porque los recuentos forman parte de lo aceptado. Los demás archivos no se regeneraron. Su contenido inicial forma parte de la aceptación de E2.

Historial: 2026-11-03, `catalogo_consulta.txt` nuevo (plan T-023, E1): sesión de `veritas catalog search`, `ficha` y `comparar` (búsquedas, orden, ausentes, revisiones, comparación entre catálogos y fallos con su código de salida). Lo genera `tests/catalog_cli_golden.py` (`uv run python tests/catalog_cli_golden.py consulta` regenera solo este archivo); `catalogo_cli.txt` no se regeneró y se comprobó idéntico. Su contenido inicial forma parte de la aceptación de E1.

Historial: 2026-11-03, `catalogo_cli.txt` regenerado con la CLI de T-023 E2: cambian **solo 3 líneas** (`esquema v2` → `esquema v3`: dos de `create` y una de `verify`), con el esquema v3 aprobado por el usuario en la consulta de inicio de E2 (`git diff` de esas líneas en el informe de la puerta). `catalogo_gestion.txt` nuevo (plan T-023, E2): sesión de `clone`, `favorito`, `exportar` y `verify` de clones con sus códigos de salida; lo genera `uv run python tests/catalog_cli_golden.py gestion`. `catalogo_consulta.txt` no se regeneró. Su contenido inicial forma parte de la aceptación de E2.

Historial: 2026-11-05, `proyecto_perfiles.txt` nuevo (plan T-024, E2): sesión de `veritas project assign-profile`, `replace-profile`, `sections` y `verify --db` (estricto rechazado, parcial, sin cambio, dictámenes, vigencia y fallos con su código de salida). Lo genera `uv run python tests/project_profiles_golden.py`; enmascara solo UUID y SHA-256. Los demás archivos no se regeneraron. Su contenido inicial forma parte de la aceptación de E2.

Historial: 2026-11-06, `catalogo_cli.txt` y `catalogo_gestion.txt` actualizados por T-028 E1: cambian **solo las líneas** `esquema v3` → `esquema v4` (esquema de catálogo v4 aprobado en el plan T-028, D7).
