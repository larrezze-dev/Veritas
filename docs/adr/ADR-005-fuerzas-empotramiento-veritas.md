# ADR-005 — Cargas de barra no uniformes: fuerzas de empotramiento calculadas por Veritas

**Estado: ACCEPTED.** Aprobado por el usuario el 2026-10-30 («ADR-005 aprobada»), tras la aprobación del plan T-015 (D6-A, D7-A). Implementación en T-015 (E2.0–E2.3). Aprobarlo no acredita implementación ni validación.

**Fase:** F5A (adelantado). **Ticket:** T-015 ([plan](../../plans/T-015.md), D6 y D7).

**Relación con otros ADR.**
- [ADR-002](ADR-002-adaptador-plano-proceso-solver.md) y [ADR-003](ADR-003-adaptador-espacial-3d.md): no cambia ninguna decisión. Se amplía el mensaje del worker con versión nueva, según su regla: protocolo plano v4 y espacial v5, adaptadores «5».
- ADR-003, hecho 4 («liberaciones exactas; con carga de barra, sujeto a la puerta de E2»): vale solo para la carga uniforme en toda la luz (`-beamUniform`, validada en T-018 E2). Para las cargas no uniformes queda superado por el hecho 2 del plan T-015. ADR-003 recibe una nota de historial, sin cambio de decisión.
- [ADR-004](ADR-004-penalizacion-iterada-extremos.md): no cambia. Las cargas nuevas son cargas nodales del patrón base y entran en el vector de cargas del contraste exacto.
- [ADR-001](ADR-001-convenciones-dominio.md): no cambia. Los esfuerzos publicados siguen sus ejes y signos.

## Contexto

T-015 añade cargas de barra puntuales (fuerza y par), distribuidas parciales y trapezoidales, y momentos distribuidos. Las sondas con OpenSeesPy 3.8.0 (plan T-015, hechos 1–4) muestran lo siguiente:

- **Barras biempotradas sin liberaciones:** los tipos de carga de OpenSees dan resultados correctos.
- **Barras con `-release*`:** se ignoran las liberaciones en las cargas parciales y trapezoidales (2D) y en las puntuales y parciales (3D). El resultado es erróneo y no da ningún error.
- **Trapezoidal 3D:** no se resuelve; los argumentos finales se ignoran.
- **Momentos distribuidos 3D:** no existen. Algunos tipos de carga desconocidos se aceptan en silencio y no aplican nada.

Usar esos tipos de carga daría resultados falsos presentados como válidos, lo que prohíbe `CLAUDE.md`.

## Decisión propuesta

1. **Fuente única de p0.** Un módulo puro, `veritas.fem.member_loads`, sin OpenSeesPy y sin importar el worker, calcula el vector local de fuerzas de empotramiento p0 de cada carga nueva:
   - funciones de forma de Hermite (flexión) y lineales (axil y torsión), exactas para una barra prismática de Euler–Bernoulli;
   - Gauss–Legendre de 3 puntos en las distribuidas, exacta en esos grados;
   - evaluación directa en las puntuales;
   - convenio localForce = k·T·u + p0.
2. **Condensación.** En las barras con `-release*`, p0 se condensa con la misma k que usa la salvaguarda (`condense_beam`). Con el código 3, los cortantes condensados son las reacciones de la barra biapoyada. La carga `-beamUniform` existente conserva su camino actual sin cambio de código.
3. **Aplicación.**
   - El traductor envía −Tᵀ·p0_c como carga nodal en los nudos del elemento (el aux si existe; hecho 7 del plan), en el campo nuevo `member_end_loads`, separado de `nodal_loads`.
   - El worker solo aplica `ops.load` en el patrón base y no calcula nada.
4. **Esfuerzo publicado.** El esfuerzo de extremo publicado es localForce del worker + p0_c, sumado en el proceso padre antes de normalizar. **Este es el cambio de contrato:** hasta ahora el esfuerzo publicado era solo el `localForce` del worker.
5. **Controles.**
   - Los controles de barra comparan localForce del worker con k·T·u + p0 de las cargas `-beamUniform`.
   - El residuo, el equilibrio, la salvaguarda, los controles de aux, la suma de reacciones y la iteración de Uzawa leen también `member_end_loads` (regla de lectura de D7 del plan).
6. **Versiones.** Protocolo plano v4 y espacial v5, adaptadores «5». Se rechazan las versiones anteriores. Las corridas sin cargas nuevas son idénticas bit a bit a las de los adaptadores «4», comprobado con los workers congelados en `tests/data/`.

## Alternativas

- **Tipos de carga de OpenSees donde son correctos, y rechazo en el resto.** Excluiría las barras con liberaciones, las trapezoidales 3D y los momentos 3D, y dependería de errores no documentados de 3.8.
- **Subdividir la barra en los puntos de carga.** Cambia la topología, los GDL y la salvaguarda, y no es exacta para trapezoidales ni momentos. Se usa como referencia de validación (R-SUB), no como formulación.
- **Mover también la carga uniforme a este camino.** Rompería la identidad de las corridas aceptadas sin aportar nada.

## Consecuencias

- **El solver ya no calcula de forma independiente el efecto local de las cargas nuevas.** La formulación de p0 la validan solo referencias independientes: R-FIX, R-PROP, R-CANT, R-SUB y R-OPS del plan T-015.
- **Contraste plano/espacial.** Como los dos adaptadores comparten `member_loads`, el contraste D1 no valida p0.
- **Control en producción.** El único control de p0 en producción es el equilibrio de barra: valida los totales, no el reparto entre extremos.
- **Formulación.** Las fuerzas de empotramiento dependen de Euler–Bernoulli prismático; un elemento Timoshenko necesitará otras.
- **Versiones.** Las corridas guardadas con adaptador «4» desajustan la petición al normalizar; es el comportamiento versionado correcto.
- **Dependencias.** Ninguna nueva.

## Historial

- 2026-10-30: redactado como PROPOSED tras la aprobación del plan T-015 (D6-A, D7-A).
- 2026-10-30: ACCEPTED por el usuario («ADR-005 aprobada»).
