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

## Consecuencias y límites

- Cambio aditivo: ningún proyecto existente cambia de contenido ni de hash.
- Sin arcos (ticket), sin clipping general ni booleanas más allá de hueco interior y unión por arista completa (E2), sin multi-región, sin J general, Cw ni módulos plásticos (TK-028), sin GUI (F4).
- Un archivo v7 con `FREEFORM` no lo abre una versión anterior.
- La tolerancia es relativa a la figura: una figura con vértices a separaciones < 1e-9·diagonal se rechaza.

## Historial

- 2026-11-06: propuesto con E1 de T-029.
- 2026-11-06: ACCEPTED con la aceptación de E1.
