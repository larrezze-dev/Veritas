# ADR-013 — Secciones de croquis libre: contornos poligonales con huecos (proyecto v7)

**Estado: ACCEPTED** (2026-11-06, con la aceptación de E1 de T-029; plan [T-029](../../plans/T-029.md) aprobado con D1–D8 = A).

**Relación con otros ADR.**
- [ADR-006](ADR-006-proyecto-persistido-hash-canonico.md): enmienda el esquema de proyecto a **v7** (familia `FREEFORM`).
- [ADR-011](ADR-011-perfiles-parametricos-cortante-contrastado.md): mismo patrón (fórmulas y validación en `core/sections/`; la revisión recalcula y rechaza propiedades manipuladas).
- [ADR-001](ADR-001-convenciones-dominio.md): ejes del perfil (y según b, z según h; Iy = ∫z², Iz = ∫y², Iyz = ∫yz).

## Contexto

EDT-002/004/005 piden un croquis libre con huecos, validado y con propiedades generales (centroide ≠ 0, Iyz ≠ 0, ejes principales). Hasta T-025 solo existían familias cerradas doblemente simétricas.

## Decisión (E1)

1. **Familia `FREEFORM`** con `FreeformParameters(outer, holes)`: vértices (y, z) en m; sin arcos (D3-A); una región (D2-A).
2. **Representación única.** Exterior antihorario, huecos horarios, cada contorno empieza en su vértice menor (y, luego z), huecos ordenados; el contorno se cierra implícitamente. Misma lista de vértices (salvo orientación, inicio y orden de huecos) ⇒ mismo contenido y mismo hash. No se eliminan vértices colineales redundantes: el mismo contorno con un vértice de más es otro contenido (las propiedades coinciden). Un contorno con el primer vértice repetido al final se rechaza (`sketch_duplicate_vertex`): el cierre es siempre implícito, de modo que «contorno abierto» solo existe en el borrador de E2 (`sketch_open_contour`, hoy no emitida).
3. **Validez (EDT-004), en `core`.** Tolerancia `1e-9 · diagonal` de la caja del exterior; ≥ 3 vértices finitos; vértices consecutivos no coincidentes; sin autointersección (distancia entre aristas no contiguas ≤ tol) ni pico (retroceso o solape en aristas contiguas); área no nula; cada hueco estrictamente dentro del exterior, sin tocarlo y sin tocar, solapar ni contener a otro hueco. **Ante la duda se rechaza** (D7-A). Se valida la lista dada y también su forma canónica, de modo que lo guardado siempre se reabre. Los diagnósticos se acumulan en `sketch_diagnostics`; la revisión rechaza con el primero.
4. **Propiedades.** A, centroide, Iy, Iz, Iyz por fórmulas exactas de polígono (Green) con suma `fsum` y origen interno trasladado a la media del exterior; método `polygon-green/1`, estado `CALCULADO`. El centroide y los productos de inercia están en el sistema de definición (el croquis no se recentra).
5. **Ejes principales derivados, no guardados** (`principal_axes`), con la convención de ADR-001 (la inercia de un eje es la integral del cuadrado de la coordenada perpendicular): `angle` es el del eje respecto al cual la inercia es I_max, medido de +y hacia +z en (−π/2, π/2]; el perpendicular tiene I_min. Exige tensor admisible (Iy, Iz > 0, Iyz² ≤ Iy·Iz).
6. **J no disponible** (D6-A): `NO_DISPONIBLE` con motivo y método `sin-solucion-exacta-validada/1`; sin área de cortante (`shear` ausente y rechazado si se aporta). Consecuencia: `readiness` declara la sección **no apta para FEM** por J. Esa declaración es un aviso: ninguna ruta de análisis la consulta. El adaptador espacial exige J disponible e Iyz = 0; el plano no exige J. Las propiedades son centroidales y el origen de ejes del miembro es el centroide (ADR-001), de modo que el centroide ≠ 0 del croquis no introduce error mecánico. Un control de «aptitud» en los adaptadores queda fuera de T-029 (ticket).
7. **Antimanipulación** (revisión estructural, H1). Invariantes independientes del método: J siempre `NO_DISPONIBLE`; estados solo `CALCULADO` o `CONTRASTADO`; Iyz² ≤ Iy·Iz. Además cada propiedad cuyo método sea el actual se contrasta **una a una** con el recálculo (tolerancia relativa 1e-12): un método de otra versión (O3) exime solo a esa propiedad. La exigencia «centroide e Iyz = +0.0» no se aplica a esta familia. Cuando E2 admita J de pared delgada en U/L/T, el invariante de J se enmendará aquí.
8. **Persistencia v7.** `parameters = {"outer": [[y, z], …], "holes": [[[y, z], …], …]}` con precisión completa; la migración 6 → 7 solo verifica (rechaza `FREEFORM` en un archivo v6). Hash de v1–v6 intacto.

## Decisión (E2, revisión R1 del plan T-029)

1. **Borrador (`SketchDraft`).** Contornos como polilíneas con unidad y bandera `closed`; acumula todos los diagnósticos, incluido `section.sketch_open_contour` (contorno sin cerrar). Solo un borrador válido produce una `SectionRevision` (EDT-012). El cierre sigue siendo implícito.
2. **Plantillas U, L y T (E2-D2 = A).** Solo generan el polígono: la sección es `FREEFORM` y no recuerda la plantilla (se guarda el polígono, no las medidas). Sin radios ni acuerdos. Medidas: U `h, b, tw, tf`; L `h, b, t`; T `h, b, tw, tf`. Cierra TK-027. Las fórmulas cerradas existen solo en las pruebas, como referencia independiente por Steiner; el polígono de una I se contrasta además con la fórmula cerrada de la I paramétrica.
3. **J sigue `NO_DISPONIBLE` (E2-D1 = A).** No se enmienda el invariante de E1: U, L y T tampoco tienen J hasta que exista un método con referencia externa (TK-028, ΣL·t³/3 aplazado).
4. **CLI (E2-D3 = A).** `section sketch --exterior "y,z;…" [--hueco "y,z;…"]… [--unidad]`, `section u|l|t --dim …`, `project add-sketch` y `project add-section … u|l|t`. Los vértices se escriben como texto; un archivo de vértices queda para un ticket. La salida muestra centroide, Iyz y ejes principales y declara que no hay J ni cortante; ya no dice «origen en el centroide».
5. **Comparación y contraste.** `section sketch --ref/--db` y `add-sketch --contrastar` siguen las reglas de `add-section`; A, Iy e Iz pueden pasar a `CONTRASTADO`, J no es comparable. Para `FREEFORM` no se emite el aviso «la forma difiere».
6. **Revisiones.** `StructuralModel.add_freeform_section` y `revise_freeform_section`: cambiar el croquis o el nombre crea una revisión nueva; las barras conservan la anterior; sin cambios devuelve la actual. Una revisión nueva vuelve a `CALCULADO`.

## Consecuencias y límites

- Cambio aditivo: ningún proyecto existente cambia de contenido ni de hash.
- Sin arcos (ticket), sin clipping general ni booleanas más allá de hueco interior y unión por arista completa (E2), sin multi-región, sin J general, Cw ni módulos plásticos (TK-028), sin GUI (F4).
- Un archivo v7 con `FREEFORM` no lo abre una versión anterior.
- **Sin forma declarada (TK-032).** Una `FREEFORM` (también una U, L o T de plantilla) no lleva etiqueta de forma: el bloqueo forma/material de `compatibility_errors` (metálico con madera) y los avisos de forma y de acuerdos del contraste no se le aplican. La U de catálogo sí se bloquea con madera; la misma U dibujada no. Hasta decidir TK-032 es responsabilidad del usuario no asignar un croquis metálico a una barra de madera.
- **Iyz de ruido (TK-033).** En secciones simétricas el Iyz calculado es del orden del redondeo (p. ej. 1e-21), no 0 exacto: el adaptador plano lo rechaza como flexión acoplada. Falla del lado seguro; el informe de la CLI no define ángulos con ese ruido y declara los ejes indeterminados si Iy ≈ Iz.
- **Medidas casi degeneradas de una plantilla** (p. ej. 2·tf = h − ε con ε bajo `1e-9 · diagonal`) las rechaza el propio `template_draft`, con el diagnóstico de la geometría (campo `outer`).
- **Revisión por contenido.** `revise_freeform_section` compara el polígono canónico en m de forma exacta: el mismo polígono escrito en otra unidad puede diferir en ulps y crear una revisión nueva.
- La tolerancia es relativa a la figura: una figura con vértices a separaciones < 1e-9·diagonal se rechaza.

## Historial

- 2026-11-06: propuesto con E1 de T-029.
- 2026-11-06: ACCEPTED con la aceptación de E1.
- 2026-11-06: enmienda de E2 (borrador, plantillas U/L/T, CLI, comparación y revisiones); sin cambio de esquema.
