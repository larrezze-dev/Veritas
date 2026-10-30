# ADR-003 — Adaptador FEM espacial de seis GDL por nudo

**Estado: ACCEPTED** — aprobado por el usuario el 2026-10-28 («aprobado», junto con el plan T-018, D1–D10 = A). Revisión structural-reviewer incorporada. **Fase:** FEM-3D, antes de F2. **Ticket:** T-018 ([plan](../../plans/T-018.md)).
Depende de [ADR-001](ADR-001-convenciones-dominio.md) y [ADR-002](ADR-002-adaptador-plano-proceso-solver.md), ambos ACCEPTED. No los modifica. Aprobarlo no acredita implementación ni validación del solver.

## Contexto

ADR-002 limitó el primer adaptador al plano XZ. Su alternativa «3D ahora» se pospuso por dos motivos: faltaban G y J validados, y había que validar la torsión. Hoy:

- `MaterialRevision` guarda G como valor primario e independiente de E (T-004).
- La sección rectangular calcula J de Saint-Venant por serie, con método declarado (T-004). Nunca se deduce de Iy + Iz.
- Los resultados normalizados ya usan vectores de seis componentes en base de ADR-001 (T-008).
- `EndConnection` ya tiene seis estados por extremo en base local (T-003, T-010).
- El adaptador plano rechaza todo lo que no cabe en el plano: Rz, torsión, el preset «articulado» de extremo (Ry y Rz), traslaciones de extremo y apoyos fuera del plano.

Por tanto, la objeción de ADR-002 sobre «no inventar G ni J» queda resuelta para el acero y el aluminio con sección rectangular.

## Hechos comprobados (sondas, 2026-10-28)

Entorno: CPython 3.12.9, `openseespy` y `openseespywin` 3.8.0.0. Scripts fuera del repositorio; contraste con un ensamblaje NumPy independiente, usado solo en la sonda. Detalle y salidas en `plans/T-018.md`, «Hechos comprobados».

1. `geomTransf Linear` con vecxz = ez de ADR-001 da la misma base local de ADR-001 (`eleResponse` xaxis/yaxis/zaxis). Probado con barras +X, +Y, −X, vertical (fallback −X), α = π/2 y una oblicua con α = 0,37. Diferencia ≤ 1,1e-16.
2. `elasticBeamColumn` 3D (A, E, G, J, Iy, Iz): `localForce` = (pI, pJ) de ADR-001 §2, directamente en base local y sin cambiar signos. Tracción: pIx = −P, pJx = +P. Voladizo con −P en z: pIz = +P, pI_My = −PL, pJz = −P. Flechas PL³/(3EI), alargamiento PL/(EA) y giro TL/(GJ) con error ≤ 2,9e-15.
3. `-beamUniform` 3D: el orden es (Wy, Wz, Wx), en base local. Los valores coinciden a la vista con qL⁴/(8EI) y qL²/(2EA), pero el error no se ha calculado: es la puerta de E1.
4. Las opciones de elemento `-releasey 2` / `-releasez 2` (giro local liberado) son exactas en una barra oblicua (error ≤ 2,5e-15 frente a la condensación). Solo se ha probado con cargas nodales; con carga de barra es la puerta de E2. No existe una opción equivalente para la torsión.
5. Un resorte de torsión de extremo equivale exactamente a un J efectivo en serie: GJ_ef/L = 1/(L/GJ + 1/k_I + 1/k_J). Error ≤ 2,4e-15. Es exacto porque no existe un momento torsor distribuido.
   - El elemento admite J = 0 y A = 0, pero solo se ha comprobado que la corrida termina con código 0.
   - El axil **no** es equivalente en serie cuando hay carga axial distribuida (Wx, incluido el peso propio de barras no horizontales): la fuerza de empotramiento no depende de A. Lo detectó la revisión estructural.
6. Si un extremo usa un nudo auxiliar con un `zeroLength` en ejes locales, la penalización de las componentes rígidas (factor 1e8) da un error relativo de 2e-7 a 6e-7.
7. Rodillo inclinado (una traslación fija según una normal n arbitraria):
   - Con penalización: error 1,9e-7.
   - Exacto: `rigidLink beam` desde un nudo R situado en N − a·n (traslaciones fijas, giros libres) a un nudo C, más `equalDOF` C → N en las traslaciones. El giro de R alrededor de n queda desacoplado y lleva un resorte arbitrario. Con el handler **Lagrange**, el error es ≤ 2,4e-15 y n·u ≈ 1e-21, con BandGeneral, FullGeneral o UmfPack.
   - Con el handler **Transformation**, el mismo encadenado termina con `analyze = 0`, pero **el resultado es erróneo** (traslaciones nulas, error relativo 1,0). Cambiar el orden de declaración no lo corrige.
8. Alias de `equalDOF`: con Transformation, el aux y el maestro son iguales bit a bit; con Lagrange, |u_aux − u_m| ≈ 6e-20 con |u| ≈ 1e-5. Es decir, no es bit a bit: son unas 22·eps relativas.
9. La reacción del rodillo exacto sale en `nodeReaction(N)` y es paralela a n (|F⊥n| ≈ 8e-12 N frente a unos 5e3 N). Los nudos auxiliares de la cadena dan una reacción de 0,0.

## Decisión propuesta

1. **Alcance.** Estática lineal Euler–Bernoulli de barras rectas centroidales en el espacio, con `elasticBeamColumn` 3D, seis GDL por nudo y `geomTransf Linear` con vecxz = ez de ADR-001 (hecho 1). Propiedades: A, E, G, J, Iy e Iz.
   - Hipótesis declaradas: sin alabeo (Cw), sin deformación por cortante, sin excentricidades ni centro de esfuerzos cortantes distinto del centroide, y sin no linealidad.
   - Se rechaza con `UNSUPPORTED_CONFIGURATION` si:
     - J, Iy, Iz o A no están disponibles;
     - Iyz ≠ 0 (la flexión acoplada queda fuera);
     - la sección o el material no tienen un método declarado.
2. **Materiales.** Acero y aluminio con E ISOTROPIC y G primario de la revisión. La madera sigue rechazada, igual que en ADR-002 §2. G y E no se certifican como valores de diseño.
3. **Proceso y protocolo.** Se mantienen el proceso aislado por corrida, el mismo intérprete 3.12, JSON sin pickle y el worker sin `veritas` (ADR-002 §4-§5 y revisión DEC-011).
   - El protocolo es nuevo, `veritas.opensees.spatial` versión 1, con un **worker propio**.
   - El worker y el protocolo planos v2 no cambian.
   - El runner de subprocess se reutiliza sin cambios.
4. **Adaptador plano.** Se mantiene sin cambios, como adaptador aceptado y como **contraste cruzado**: cada referencia aceptada (T-FEM-001…004 y T-CON-REF-01…04), con las restricciones fuera del plano declaradas, debe dar en 3D lo mismo que en el plano. Su retirada, si llega, requerirá revisar ADR-002.
5. **Restricciones.** El handler y la representación de cada condición se eligen por exactitud y se declaran en la procedencia:
   - Fijaciones en base GLOBAL; ejes de una base inclinada que coinciden con un eje global; apoyos INCLINADOS con las tres traslaciones (o los tres giros) en el mismo estado: `fix`, exacto.
   - Rodillo inclinado (una traslación según una normal no global): el encadenado de `rigidLink` del hecho 7, exacto. Exige el handler Lagrange (decisión D3 del plan). La reacción se lee en el nudo (hecho 9).
   - Resortes de apoyo: `zeroLength` orientado en la base del apoyo, exacto.
   - Extremo con Ry o Rz liberados: opciones de elemento (hecho 4), exacto. Con carga de barra, sujeto a la puerta de E2.
   - Torsión de extremo, liberada o con resorte: J efectivo en serie (hecho 5), exacto.
   - Axil de extremo, liberado o con resorte: A efectivo en serie solo si la barra no tiene carga axial distribuida. Si la tiene, se rechaza.
   - Giros de flexión con resorte y traslaciones transversales de extremo (D5-A de T-017): nudo aux con `equalDOF` en las componentes globales exactas y `zeroLength` en ejes locales. La penalización se aplica solo a las componentes rígidas que no se pueden atar exactamente. Esto se declara con la hipótesis de penalización y una tolerancia calibrada.
   - Cada componente usa una sola representación, nunca dos a la vez.
   - Giros huérfanos: se tratan como subespacio. Solo se fijan si son de dimensión 3 o están alineados con ejes globales; si no, son un mecanismo.
   - Otros apoyos inclinados parciales: con penalización, como en el plano, o rechazados (decisión D4 del plan).
   - **Nunca** se encadenan restricciones multipunto con el handler Transformation (hecho 7). La salvaguarda y los controles posteriores al equilibrio lo detectarían, pero el traductor no debe generarlo.
6. **Salvaguarda y controles.**
   - Ensamblaje independiente en Python de la misma petición 3D, con 12×12 por barra, la misma formulación de las condiciones del punto 5 y LDLᵀ con pivotaje completo.
   - Modo de mecanismo, residuo y controles de extremo y de esfuerzos, en el sentido de T-017.
   - `tol_pivot` se recalibra con una batería 3D propia, que incluye los modos de torsión (p. ej., una viga biapoyada sin ningún Rx restringido) y los modos fuera del plano.
   - Los controles se evalúan con tolerancias exactas (c·eps) cuando no hay penalización y con una tolerancia de penalización declarada cuando la hay.
7. **Procedencia.** Se extiende al adaptador espacial la excepción incremental de ADR-002 §6: snapshots en memoria, `run_id` y versiones, sin hash canónico ni CURRENT/STALE hasta T-012.
   - El adaptador espacial tiene su propio identificador y versión, separado de los del plano.
   - FEM-005 y DAT-004 no quedan completos.
8. **Configuración.** La configuración de la corrida espacial es propia. `AnalysisConfiguration` no cambia: `tol_plane` y `tol_member_load_y` no tienen sentido en 3D. El límite de GDL libres se fija en el plan y se mide, porque la salvaguarda es O(n³) en Python puro.

## Alternativas

- **Sustituir el adaptador plano por el espacial.** Así habría un solo camino, pero cambia APIs y salidas aceptadas y pierde el contraste cruzado. Se descarta en este ADR.
- **Ampliar el worker y el protocolo planos.** Toca un contrato congelado (protocolo v2). Se descarta.
- **Penalización para todas las condiciones locales e inclinadas.** Es uniforme y sencilla, pero introduce errores de 1e-7 a 6e-7 donde existe una vía exacta (hechos 4, 5 y 7). Se descarta como opción general.
- **Matriz de rigidez propia, calculada en Python y pasada al solver.** No hay un elemento de matriz de usuario verificado en OpenSeesPy 3.8. Además, convertiría OpenSees en un simple resolvedor. Se descarta.
- **Handler Transformation en todas las corridas.** Conserva el alias bit a bit, pero obliga a usar penalización en el rodillo inclinado (hecho 7). Es la opción B de D3 en el plan.

## Consecuencias

- Hay dos adaptadores, con su worker y su protocolo cada uno. Más código que mantener, a cambio de un contraste cruzado automático.
- Una viga biapoyada con giros libres en ambos apoyos es un mecanismo de torsión en 3D: se rechaza como ANALYSIS_SINGULAR. El usuario debe restringir Rx en algún apoyo. Es correcto físicamente, pero cambia la experiencia respecto al plano, que restringe Uy, Rx y Rz implícitamente.
- Con Lagrange, el control de alias deja de ser bit a bit y pasa a ser una cota c·eps·|u|. El sistema es indefinido: la salvaguarda propia sigue decidiendo la singularidad antes del solver.
- No promete estabilidad espacial general, torsión no uniforme, pandeo, P-Delta ni aptitud normativa.

## Historial

- 2026-10-28: borrador PROPUESTO tras las sondas de T-018 (hechos 1-8).
- 2026-10-28: revisión structural-reviewer incorporada (axil en serie con Wx, huérfanos como subespacio, hechos 3-5 matizados, hecho 9). Pendiente de aprobación del usuario.
- 2026-10-28: el usuario aprueba el ADR con el plan T-018 («aprobado»). Estado: ACCEPTED. Implementación en T-018 (E1 y E2).
- 2026-10-30: enmendado por [ADR-004](ADR-004-penalizacion-iterada-extremos.md) (ACCEPTED): en los extremos de barra, la penalización simple con tolerancia calibrada (§5) se sustituye por la penalización iterada con contraste exacto; adaptador «3» y protocolo espacial v3 (enmienda de D10 del plan). Los apoyos D4 no cambian.
- 2026-10-30: sin cambio de decisión. T-016 (asientos impuestos, [plan](../../plans/T-016.md), D6-A) amplía el mensaje con `settlements` como versión nueva: protocolo espacial v4 y adaptador «4». Con asientos no corren penalizaciones ni cadenas (D4-bis-A), así que ADR-004 no cambia.
- 2026-10-31: sin cambio de decisión. T-015 (cargas de barra ampliadas, [plan](../../plans/T-015.md); [ADR-005](ADR-005-fuerzas-empotramiento-veritas.md) ACCEPTED) añade cargas puntuales, distribuidas y momentos de barra. Con ellas: (a) la regla «con carga de barra, sujeto a la puerta de E2» del hecho 4 y de §5 sigue valiendo solo para `-beamUniform`; las cargas no uniformes se rigen por ADR-005; (b) un Ux no rígido se rechaza con cualquier componente axial de carga nueva, y un Rx no rígido, con cualquier torsor de barra (clave `fem3d.end_torsion_with_torsional_load`, D8 de T-015); (c) protocolo espacial v5 y adaptador «5». La clave `fem3d.end_axial_with_axial_load` cubre también la carga puntual (T-015 E2.2-f; texto actual en `src/veritas/core/messages_es.py`).
