# ADR-004 — Penalización iterada en los extremos de barra del worker espacial

**Estado: ACCEPTED.** Aprobado por el usuario el 2026-10-30 («Aprobar; implementar en sesión nueva»), tras elegir la opción U («U: iterada + contraste») en la puerta E2-c del plan T-018. Incorpora las revisiones de structural-reviewer y architecture-reviewer (2026-10-30: sin bloqueantes, Mayores incorporados). Implementación en T-018 (E2.4-impl y E2.5).

**Fase:** FEM-3D, etapa E2. **Ticket:** T-018 ([plan](../../plans/T-018.md), «Pieza E2.4»).

**Relación con otros ADR.**
- Enmienda [ADR-003](ADR-003-adaptador-espacial-3d.md):
  - §5: en los extremos de barra, la «tolerancia calibrada» de la penalización simple se sustituye por la penalización iterada con contraste;
  - D10 del plan: el adaptador pasa a «3» y el protocolo a v3.
- Los apoyos con penalización de ADR-003 (D4) no cambian.
- No modifica ADR-001 ni ADR-002.
- Aprobarlo no acredita implementación ni validación.

## Contexto

En E2 (D5-A, E2-a), un extremo de barra no rígido cuya terna no es coordenada global se modela con un nudo aux. Las componentes rígidas de esa terna se atan al nudo maestro con `zeroLength` de penalización en ejes locales: Uy/Uz no rígidos y resortes Ry/Rz en barras oblicuas.

OpenSees no ofrece una restricción lineal general entre GDL en ejes no globales: `equalDOF` y `equalDOF_Mixed` trabajan en ejes globales, y `rigidLink` ata los tres giros.

Medidas de la puerta E2-c (plan T-018, hechos 19-24), frente a la solución exacta de la K reducida (mpmath, 50 dígitos):

- **Penalización simple, factor 1e8.**
  - Error de hasta 8,8e-2 en traslaciones (∝ eps·f·λ²) y de 7,6e-6 en giros.
  - Ningún factor ni ninguna escala cumplen `tol_penalty` = 1e-6 con margen.
  - Ningún estimador heurístico lo acota.
- **Penalización iterada (Uzawa).** Sonda con el `_build` del worker sin cambios: rejilla de λ de 5 a 5000, con uno y dos extremos y con cadena, más 40-80 casos aleatorios de giro.

  | Factor | Error máximo | Iteraciones |
  |---|---|---|
  | 1e1 | 6,2e-9 | ≤ 7 (mediana 5) |
  | 1e2 | 1,6e-7 (λ = 5000) | ≤ 5 |

  - Con el factor 1e1, el error máximo de 6,2e-9 es de λ = 5000; con λ ≤ 500 es ≤ 3,8e-11.
  - El paso de la iteración se estanca en el suelo de redondeo; el criterio de §3 para todos los casos.
- **`nodeReaction`** (hecho 24, sonda): es el desequilibrio F_int − P del nudo, es decir, **resta** las cargas aplicadas. Una pareja de cargas ±L en el maestro y en el aux no aparece en las reacciones; el multiplicador del `equalDOF` sí (hecho 17).

## Decisión propuesta

1. **Algoritmo (worker).** Solo si la petición incluye penalizaciones de extremo, el worker resuelve por iteración de Uzawa sobre ellas.
   - Cada resorte s de penalización de extremo tiene una dirección global b_s, enviada por el padre, una rigidez k_s y g_s, que es la `basicDeformation` del `zeroLength`. Con el nudo 1 como maestro y el nudo 2 como aux, g_s = b_s·(u_aux − u_N).
   - En la iteración i, con los multiplicadores λ_s⁽ⁱ⁾ (λ⁽¹⁾ = 0, que es la penalización simple):
     - se aplican cargas −λ_s⁽ⁱ⁾·b_s en el aux y +λ_s⁽ⁱ⁾·b_s en el maestro, como fuerzas o momentos según la terna;
     - se resuelve;
     - se calcula λ_s⁽ⁱ⁺¹⁾ = λ_s⁽ⁱ⁾ + k_s·g_s⁽ⁱ⁾.
   - Cada iteración reconstruye el modelo (`_build` + `wipe`), con un único patrón `Constant` corrector. Es la variante que se midió.
   - El punto fijo (g = 0) es el sistema KKT de la restricción exacta, sea cual sea k. La contracción por iteración es ρ ≤ ~1/(1 + f/1,9) con las escalas de E2-a (revisión estructural).
   - Los apoyos D4 **no** iteran.
2. **Factor de extremo.** `end_penalty_factor` = **1e1**, sobre las escalas de E2-a:
   - giros: Σ máx(4EI/L, GJ/L);
   - traslaciones: Σ máx(EA/L, 12EI/L³).

   `penalty_factor` (1e8) rige solo los apoyos D4.
3. **Parada (worker; todos los parámetros los envía el padre).**
   - Por resorte, el paso relativo es p_s = |k_s·g_s⁽ⁱ⁾| / S_s. S_s es una escala enviada por el padre:
     - la escala de fuerza del caso, F = máx(|cargas nodales de fuerza|, Σ|w|·L, |momentos nodales|/L_máx), para traslaciones;
     - F·L_máx, para giros.

     S_s no depende de λ, así que el caso λ ≈ 0 (enlace sin fuerza o carga nula) para sin falsos FAILED.
   - Se calcula P⁽ⁱ⁾ = máx_s p_s.
   - **Convergencia:** P⁽ⁱ⁾ ≤ `end_penalty_tolerance` (1e-10).
   - **Estancamiento aceptado:** i ≥ 3, P⁽ⁱ⁾ ≤ `end_penalty_stagnation` (que el padre fija en `tol_penalty`), y P⁽ⁱ⁾ no mejora al menos 10× frente al mejor P anterior.
   - **No convergencia:** se agota `end_penalty_max_iterations` (20). La corrida es FAILED con `fem3d.end_penalty_not_converged` y nunca OK.
   - El worker devuelve el número de iteraciones y el motivo (`tolerance`, `stagnation`, `max_iterations` o `none`).
   - El padre **recomprueba** la convergencia con los k_s·g_s y S_s devueltos (defensa en profundidad).
4. **Contraste exacto obligatorio (E2.5).** Va en la misma pieza que el equilibrio con aux y la retirada de la guarda E2.2-d. Toda corrida con penalización de extremo compara la respuesta con la solución de la K reducida que ya factoriza la salvaguarda (sustitución, O(n²)).
   - **Qué se compara:**
     - los desplazamientos de dominio y de los aux, relativos a ‖u‖∞ y ‖θ‖∞ del modelo;
     - las fuerzas de barra, relativas a máx(‖f_barra‖∞, escala de fuerza del caso), con un suelo para no dar FAILED espurios en barras descargadas.
   - **Fiabilidad de la referencia:** un residuo relativo ‖Tᵀ·F − K_red·q‖, o un paso de refinamiento iterativo. Si la referencia no es fiable, la corrida es FAILED.
   - **Resultado:** si la diferencia supera `tol_penalty`, la corrida es FAILED con `fem3d.end_penalty_contrast_failed`.
   - **Qué se publica:** se publican los resultados de OpenSees. La solución de la salvaguarda solo comprueba y nunca se publica.
   - **Límites declarados:**
     - el contraste no valida la traducción, porque worker y salvaguarda leen la misma petición;
     - los apoyos D4 entran en la referencia como exactos, así que su error calibrado se suma al de los extremos.
5. **Protocolo espacial v3 y adaptador «3».** v2 deja de aceptarse: padre y worker cambian juntos (precedente E2-b).
   - **Petición:**
     - `end_penalty_tolerance`, `end_penalty_stagnation` y `end_penalty_max_iterations`;
     - por cada resorte de penalización de extremo, su dirección global b_s y su escala S_s.
   - **Respuesta:**
     - `end_penalty_iterations`, `end_penalty_stop` (motivo) y `end_penalty_converged`;
     - por cada resorte de extremo, `basicForce`, `basicDeformation` y el multiplicador λ_s de la última resolución.
     - Sin penalización de extremo: 0 iteraciones, motivo `none`, convergida = true y λ = 0.
   - Un test de paridad de `PROTOCOL_VERSION` compara el padre con el worker.
6. **Fuerza del enlace y reacciones (hecho 24).**
   - Escalar publicado del enlace: λ_s + k_s·g_s, con el convenio de `basicForce` (positivo con g > 0). Su acción sobre el aux es −(λ_s + k_s·g_s)·b_s.
   - La reacción publicada sigue siendo nodeReaction(N) + Σ nodeReaction(aux) (hecho 17). Las cargas correctoras **no** se suman a mano: `nodeReaction` ya las resta (desequilibrio F_int − P) y la pareja se anula en N + aux.
   - El equilibrio de cada aux por separado (E2.5) incluye la acción del enlace.
7. **Configuración** (`SpatialAnalysisConfiguration`). Este es el único contrato aceptado de E1 que cambia.
   - Campos nuevos:
     - `end_penalty_factor` = 1e1 (> 0);
     - `end_penalty_tolerance` = 1e-10 (0 < tol < `tol_penalty`);
     - `end_penalty_max_iterations` = 20 (entero ≥ 1, validado como `max_free_dofs`).
   - Van en `snapshot()` y en su lista, y **no** en `provenance_constants`.
8. **Registro y textos.**
   - Las iteraciones y el motivo de parada se registran como diagnóstico informativo `fem3d.end_penalty_iterations`. No cambia ninguna API pública de resultados.
   - La hipótesis tiene una clave propia, `fem3d.hypothesis_end_penalty_iterated`. `HYPOTHESIS_PENALTY` sigue igual para D4, y las salidas aceptadas de E1 no cambian.

## Consecuencias

- Se conserva todo E2-a, incluidas las uniones semirrígidas Ry/Rz en barras oblicuas, con una precisión del orden de la de los casos exactos.
- Una corrida con penalización de extremo cuesta hasta 20 × (`_build` + factorización); lo medido es ≤ 7. Las corridas sin aux penalizado siguen por el camino actual, con una sola resolución.
- Entre plataformas, el número de iteraciones y el motivo de parada pueden diferir, porque el estancamiento depende del redondeo (hecho 21). Los tests afirman «OK ⇒ error ≤ tol» y no un número de iteraciones.
- Las cifras de este ADR salen de la sonda. Se vuelven a medir con el criterio implementado en E2.4-impl.
- Pruebas exigidas, por pieza:
  - **E2.4-impl:**
    - protocolo v3 estricto y paridad de versión;
    - configuración y snapshot/normalización (`test_fem3d_normalize`);
    - paridad de b_s con `spring_direction`;
    - hecho 22, con el criterio implementado, en desplazamientos y fuerzas de barra;
    - λ ≈ 0 y carga nula → convergida;
    - un caso que pare por estancamiento;
    - no convergencia (`max_iterations` = 1, con un enlace con fuerza significativa) → `converged` = false, y FAILED en la orquestación con la guarda E2.2-d sustituida por monkeypatch (precedente E2.3);
    - patrón corrector único y `wipe` en cada iteración;
    - igualdad numérica de las respuestas sin penalización de extremo frente a fixtures guardadas antes del cambio.
  - **E2.5:**
    - contraste con un worker manipulado → FAILED;
    - suelo de las fuerzas de barra;
    - fiabilidad de la referencia;
    - modelo mixto con D4 y extremos penalizados;
    - reacciones y equilibrio de N y de cada aux con las cargas correctoras;
    - Linux en la CI.

## Alternativas descartadas

- **A** (factor de extremo con una sola resolución), **B** (estimador heurístico) y mantener 1e8: no cumplen E2-c ni R5 (hechos 19-20).
- **C** (rechazar las ternas oblicuas penalizadas): es segura, pero pierde Ry en vigas oblicuas en planta y en barras con α ≠ k·90° (revisión de E2.4, M1).
- **D sola** (contraste exacto con penalización simple): da FAILED en casi toda traslación oblicua realista.
- **Publicar la solución de la salvaguarda:** cambiaría el solver declarado (ADR-003). No se propone.
- **Rodillo relativo** (revisión de E2.4, M2): exacto en traslaciones según el razonamiento, sin verificar, y no cubre los giros. Queda como posible optimización.

## Historial

- 2026-10-30: redactado (PROPOSED) tras la elección de la opción U por el usuario.
- 2026-10-30: incorporadas las revisiones de structural-reviewer (M1-M4, m1-m3, o1-o3) y architecture-reviewer (M1-M6, m1-m7). Hechos 23 (factor 1e1 y criterio de parada) y 24 (`nodeReaction`) medidos en sonda.
- 2026-10-30: el usuario aprueba el ADR y el detalle de E2.4-impl («Aprobar; implementar en sesión nueva»). Estado: ACCEPTED.
- 2026-10-30 (E2.5, revisión de structural-reviewer m2; aprobado por el usuario): corrección de redacción en Contexto y §6. `nodeReaction` no «excluye» las cargas aplicadas: devuelve el desequilibrio F_int − P, que las resta. La conclusión no cambia (las correctoras no se suman a mano) y la decisión tampoco.
