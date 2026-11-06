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

## Enmienda T-031 (TK-032 y TK-033; esquema v8)

1. **Forma declarada opcional (TK-032, D1-A).** `FreeformParameters.shape` es una de `PROFILE_SHAPES` o `None` («sin declarar»). Las plantillas U, L y T la fijan solas (E2-D2 solo se ajusta en eso: la plantilla deja una **clasificación**, no las medidas); `section sketch` y `project add-sketch` aceptan `--forma`. Rige la misma regla que en catálogo: con forma metálica (`METAL_SHAPES`) y un material de madera la asignación se **bloquea** (`section.replace_incompatible_material`); sin forma y con madera solo **avisa** (`section.replace_freeform_shape_undeclared`), porque un croquis arbitrario puede ser un perfil de madera. El contraste vuelve a avisar de forma (`editor.compare_shape_differs`, o `editor.compare_shape_undeclared` si no hay etiqueta) y de acuerdos.
2. **Esquema v8.** La clave `shape` se escribe solo si hay forma declarada: el contenido y el hash de v1–v7 no cambian. La migración 7 → 8 solo verifica (un v7 con `shape` se rechaza). Fixture auténtico: `tests/data/proyecto_v7_ref.json`.
3. **Iyz de ruido (TK-033, D2-A).** Método `polygon-green/2`: el Iyz con |Iyz| ≤ `IYZ_NOISE_RELATIVE`·√(Iy·Iz), con `IYZ_NOISE_RELATIVE = 1e-12`, se fija a +0.0. Es redondeo de la integración (comprobado: U 300×100×8×12 mm da 1,1e-21 frente a un √(Iy·Iz) del orden de 1e-5), no acoplamiento. **Efecto declarado:** un acoplamiento real menor que 1e-12 relativo se trata como cero; con ese tamaño la flexión esviada cambia en κ_débil/κ_fuerte = Iyz/Iz = ρ·√(Iy/Iz) con ρ = 1e-12 (la flecha en el plano, solo en segundo orden, ρ²; la componente fuera del plano, de primer orden, ~4e-12 en una U 300×100 y ~1e-10 en una chapa con b/t = 100), muy por debajo de las demás tolerancias del proyecto (contraste 1 %, geometría 1e-9). Un Iyz mayor (L, o un cizallamiento de 1e-6) se conserva.
4. **Compatibilidad del método.** Los proyectos v7 con `polygon-green/1` abren sin cambios: su Iyz de ruido se conserva tal cual (no se reinterpreta al abrir) y `SectionRevision` sigue contrastando cada propiedad `/1` con la recomputación **sin** ajuste (`freeform_properties(..., snap=False)`), de modo que la antimanipulación no se debilita. Una propiedad con `/2` se contrasta con el ajuste.
5. **Sin cambio de solver.** `planar_xz` sigue exigiendo `Iyz == 0.0`; el adaptador espacial sigue rechazando por J no disponible. TK-033 se cierra en el origen del ruido, no en el contrato FEM.

## Consecuencias y límites

- Cambio aditivo: ningún proyecto existente cambia de contenido ni de hash.
- Sin arcos (ticket), sin clipping general ni booleanas más allá de hueco interior y unión por arista completa (E2), sin multi-región, sin J general, Cw ni módulos plásticos (TK-028), sin GUI (F4).
- Un archivo v7 con `FREEFORM` no lo abre una versión anterior.
- **Sin forma declarada** (croquis dibujado a mano sin `--forma`): con madera solo hay aviso, no bloqueo; es responsabilidad del usuario declarar la forma de un croquis metálico (T-031; antes de T-031 ninguna `FREEFORM` se comprobaba).
- **Iyz de ruido:** los proyectos v7 guardados con `polygon-green/1` conservan su Iyz de ruido y el adaptador plano los sigue rechazando; se corrigen con `revise_freeform_section` (que crea una revisión nueva con `/2` aunque el contorno no cambie) o volviendo a dibujar la sección.
- **Figura simétrica lejos del origen.** Una figura simétrica dibujada con coordenadas muy grandes (p. ej. 50 m, desde un futuro import) deja de ser exactamente simétrica en `float` (asimetría ≈ ε·desplazamiento/L): ese Iyz es de la geometría guardada, no ruido, puede superar la cota y el adaptador plano lo rechaza (del lado seguro). Se evita recentrando el croquis.
- **Forma declarada engañosa.** La etiqueta es una declaración del usuario: una U dibujada con `--forma OTRO`, `RECT` o `ROUND` no se bloquea con madera ni avisa (el aviso solo cubre «sin declarar»). La regla del catálogo no se debilita (allí la forma viene del snapshot). Tampoco se puede devolver una etiqueta a «sin declarar» con `revise_freeform_section` (`shape=None` conserva).
- **Congelación del método `/1`.** Los proyectos v7 se contrastan con `freeform_properties(..., snap=False)`: la ruta de integración (`_contour_moments`, `polygon_geometry`) debe seguir idéntica bit a bit; `tests/test_freeform_shape_iyz.py` fija valores de referencia de tres formas y un cambio de esa ruta exige una versión nueva del método.
- **Medidas casi degeneradas de una plantilla** (p. ej. 2·tf = h − ε con ε bajo `1e-9 · diagonal`) las rechaza el propio `template_draft`, con el diagnóstico de la geometría (campo `outer`).
- **Revisión por contenido.** `revise_freeform_section` compara el polígono canónico en m de forma exacta: el mismo polígono escrito en otra unidad puede diferir en ulps y crear una revisión nueva.
- La tolerancia es relativa a la figura: una figura con vértices a separaciones < 1e-9·diagonal se rechaza.

## Historial

- 2026-11-06: propuesto con E1 de T-029.
- 2026-11-06: ACCEPTED con la aceptación de E1.
- 2026-11-06: enmienda de E2 (borrador, plantillas U/L/T, CLI, comparación y revisiones); sin cambio de esquema.
- 2026-11-06: enmienda de T-031 (forma declarada opcional, esquema v8 e Iyz de ruido con `polygon-green/2`), aceptada con T-031.
