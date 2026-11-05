# ADR-011 — Perfiles paramétricos, área de cortante energética y estado CONTRASTADO (esquema v5)

**Estado: ACCEPTED** (2026-11-05, con E1 de T-025). La enmienda E2 (estado `CONTRASTADO`, comparación y CLI) fue aceptada el 2026-11-05. Aceptarlo no valida ningún resultado estructural ni habilita justificar obras reales.

**Relación con otros ADR.**
- [ADR-001](ADR-001-convenciones-dominio.md): ejes y signos (b según y, h según z; Iy = ∫z²dA), UUID, revisiones inmutables, SI interno y diagnósticos con clave estable.
- [ADR-006](ADR-006-proyecto-persistido-hash-canonico.md): este ADR **enmienda** el esquema de proyecto a **v5** y respeta el hash canónico `veritas-canon/1` sin cambiarlo (ver «Esquema v5 y estabilidad del hash»).
- [ADR-009](ADR-009-catalogo-sqlite.md) y [ADR-010](ADR-010-snapshot-perfil-catalogo.md): el perfil de catálogo y su snapshot no se tocan; los estados de sus propiedades siguen siendo `IMPORTADO_SIN_VERIFICAR`/`NO_DISPONIBLE`.
- DEC-005: A, centroide e inercias primero; J y Cw solo por método validado.

## Contexto

F3A exige definir un perfil por sus medidas (I, tubo rectangular y cuadrado, tubo circular), obtener sus propiedades con estado, origen y método, rechazar la geometría inválida antes de asignarla y versionarla. Hasta ahora solo existía el rectángulo macizo y el perfil de catálogo copiado. Además, la sección no tenía área de cortante y el hash del modelo depende de la codificación de cada propiedad, de modo que añadirla a todas las secciones habría roto todos los proyectos v1–v4.

## Decisión (E1: dominio, fórmulas y esquema)

1. **Una familia nueva, una entidad versionada.** `SectionFamily.PARAMETRICA` y `SectionRevision.parameters: RectangularParameters | ParametricParameters | CatalogSnapshot`. El rectángulo sigue siendo `RECTANGULAR` (no se duplica). Familia y parámetros deben coincidir (`section.family_mismatch`). Las fórmulas viven en `core/sections/` (un módulo por familia); el editor (`section_editor/`) importa solo `core`.
2. **Formas y dimensiones (m), con orden canónico.** Convención de ejes de ADR-001; todas las formas son doblemente simétricas con el origen en el centroide.

   | Forma | Dimensiones | Notas |
   |---|---|---|
   | `I` (alas paralelas, bisimétrica) | `h, b, tw, tf, r` | `r` = acuerdo alma–ala (cuatro iguales), opcional |
   | `RHS` | `h, b, t, ro` | espesor uniforme; `ro` radio exterior, opcional |
   | `SHS` | `b, t, ro` | **una sola dimensión de lado** `b` (h = b); no se compara h = b en coma flotante |
   | `CHS` | `d, t` | diámetro exterior y espesor |

   Nombres como los comprobados del catálogo (`h`, `b`, `tw`, `tf`, `r`); `t`, `d`, `ro` y el uso de `b` en la SHS son **convención nueva** de este ADR. Los radios omitidos se **guardan siempre como +0.0** (−0,0 se normaliza): una geometría tiene una única representación, por lo que contenido, `revise_*` («si no cambia, devuelve la actual») y hash coinciden. El radio interior del tubo **no es independiente ni se guarda**: `ri = max(ro − t, 0)` (esquinas concéntricas, espesor uniforme; hallazgo G1 de la revisión estructural).
3. **Reglas de validez (EDT-004), en `core`.** `geometry_diagnostics` devuelve todas las violaciones; `ParametricParameters` rechaza con la primera (también al abrir un archivo manipulado) y `SectionDraft` las acumula. Valores finitos, dimensiones estrictamente positivas y, necesarias y suficientes para un contorno simple: I → `2·tf < h`, `tw < b`, `0 ≤ r ≤ min((b − tw)/2, (h − 2·tf)/2)`; RHS/SHS → `2t < min(h, b)`, `0 ≤ ro ≤ min(h, b)/2`; CHS → `t < d/2`. Claves `section.geometry_*`, con ubicación (campo). Autointersecciones y contornos abiertos de croquis libre quedan para F3B.
4. **Fórmulas cerradas y métodos versionados.** `i-closed-form/1`, `rhs-closed-form/1`, `shs-closed-form/1`, `chs-closed-form/1` y, solo para J de la CHS, `annulus-torsion-exact/1`. Se evita la resta de cajas: suma de partes y factorizaciones exactas (`A_CHS = π·t·(d − t)`, `A_RHS = 2t(b + h − 2t)`, `b·h³ − bi·hi³ = 2t·[b(h² + h·hi + hi²) + hi³]`, `R⁴ − r⁴ = (R − r)(R + r)(R² + r²)`). Los acuerdos usan la región «cuadrado menos cuarto de círculo» (`a = 1 − π/4`, `c = (10 − 3π)/(3(4 − π))`, `k = 1 − 5π/16`; `core/sections/fillet.py`) sumada directamente en los ejes de la sección. Centroide e `Iyz` son +0.0 `CALCULADO` por simetría. En SHS y CHS (Iy = Iz) los ejes principales son indeterminados: cualquier eje centroidal lo es; en I y RHS, y/z ya son principales.
5. **Constante de torsión (D7-A).** `J` solo existe donde hay solución exacta: CHS (`J = Ip = 2·I`, sección anular). En la I y en el RHS/SHS es `NO_DISPONIBLE`, `value = None`, con el motivo «sin solución exacta validada… (DEC-005; TK-028)» en `origin`. **Consecuencia visible:** una I o un RHS/SHS del editor no es «apta para FEM» (falta J); el adaptador espacial la rechaza con `fem.section_property_unavailable` y el plano la admite (no usa J). Nunca se completa J.
6. **Área de cortante energética (D8-A), con nombre propio.** `shear_area_energy_y/z` (grupo `ShearAreas`), propiedad de la sección independiente de ν (SEC-002). **No es** el área resistente a cortante de DB-SE-A ni de EC3 y no es comparable con el `Av` de un catálogo: `design_codes` no puede usarla como tal. Definición (corte recto de Jourawski, energía complementaria): con cortes rectos `z = cte`, `Q(z) = ∫_{z' > z} z'·w(z') dz'` y `w(z)` la suma de las cuerdas de material, `Avz = Iy² / ∫ Q(z)²/w(z) dz` (análogo con `y = cte` e `Iz` para `Avy`); el rectángulo da exactamente 5/6·A. Se cumple `∫Q dz = Iy` y `0 < Av < A`. La definición se escribe en el docstring de `shear.py`, en `method` (`shear-energy-straight-cut/1`) y en `origin`, y la cuadratura de las pruebas usa **la misma**.
   **Alcance (decidido por el usuario el 2026-11-05):** rectángulo, `Avy` y `Avz`; RHS/SHS de aristas vivas, `Avz` y `Avy` (misma fórmula con `b` y `h` intercambiados); CHS de todo espesor, `Avy = Avz` por la forma cerrada del corte recto (propiedad energética del corte recto, no la de pared delgada A/2); I de aristas vivas, solo `Avz`. `NO_DISPONIBLE` con motivo, nunca un valor burdo ni 0: `Avy` de la I y cualquier radio de acuerdo > 0.
7. **Esquema v5 y estabilidad del hash (D9-B, D10-C).** `CURRENT_SCHEMA_VERSION = 5`.
   - `SectionRevision.shear: ShearAreas | None = None` es **opcional y último campo**; `None` = «nunca calculada» (no 0). El codificador escribe la clave `shear` **solo si no es `None`**; por tanto una sección v1–v4 tiene el mismo contenido y el mismo hash.
   - `build_rectangular_section(…, with_shear=False)`: por defecto no se añade el cortante al rectángulo (D10-C); el editor y la CLI de E2 lo activarán. `revise_rectangular_section` conserva el grupo si la revisión anterior lo tenía.
   - El decodificador despacha por `family` con **rama explícita** para `PARAMETRICA` (no cae al `b, h`); `Reader` admite campos opcionales (`shear`); `parameters` de una I es `{shape, dimensions: [{name, value}…]}` en orden canónico.
   - **Migración 4 → 5 (`_v4_to_v5`) solo verifica:** un v4 solo puede tener secciones RECTANGULAR o CATALOGO y sin `shear`; lo ajeno se rechaza, no se reinterpreta. Un v1–v4 se abre; guardar escribe v5.
   - Prueba de salida: un proyecto v4 **auténtico** (`tests/data/proyecto_v4_ref.json`, guardado con el código de T-024 antes de este cambio: rectángulo, perfil de catálogo y una corrida espacial) se abre bajo v5 con `model_hash` idéntico (`9d68bdcd…7551`) y su corrida sigue `CURRENT`; `test_pinned_hash_value` no cambia.
8. **Antimanipulación.** Si todos los métodos de las propiedades son los vigentes de la forma, `SectionRevision` recalcula y exige coincidencia (tolerancia relativa 1e-12, también del motivo de las `NO_DISPONIBLE`); un método de otra versión se acepta tal como se guardó (O3). El grupo `shear` se valida igual solo si está presente, solo admite `CALCULADO` o `NO_DISPONIBLE`, y un perfil de catálogo no puede llevarlo (`section.shear_not_derivable`).
9. **Borrador.** `section_editor.SectionDraft` (objeto puro, no registrado, no persistido): forma y dimensiones con unidad; `diagnostics()` acumula las violaciones de EDT-004 (nombres, unidades, valores y reglas de `core`); `build` solo produce una `SectionRevision` si el borrador es válido (EDT-012). Cambiar medidas = `StructuralModel.revise_parametric_section` (revisión máx + 1); las barras conservan la anterior. La forma no cambia dentro de una revisión.
10. **Compatibilidad de material.** Los perfiles paramétricos son metálicos: la regla de `replace_sections` que bloquea un perfil metálico con madera se aplica también a ellos.

## Alternativas descartadas

- Codificar siempre el cortante en `SectionProperties` (D9-A): cambia el hash de todas las secciones; rechazado. Función al vuelo (D9-C): no versionada ni trazable.
- Coeficiente de Cowper dependiente de ν (D8-B): mete el material en la sección; el área del alma (D8-C): sin energía equivalente.
- J aproximada para la I y el RHS (D7-B) o algoritmo numérico general de Prandtl (D7-C): fuera de alcance (TK-028).
- `ri` independiente de `ro` (G1): el contorno interior podría cortar al exterior; se exigiría un espesor mínimo de esquina.

## Validación (E1)

Referencias independientes en Python puro (sin dependencias nuevas): cuadratura de acordes de Gauss–Legendre sobre el contorno («caja menos huecos»; con acuerdos y arcos con `z = m − h·cos φ`) para A, Iy, Iz, Iyz y para la integral energética del cortante, sin llamar al código de fórmulas; racionales exactos (`fractions`) para las formas de aristas vivas con t/h hasta 1e-6; `decimal` a 60 dígitos para la CHS fina; fórmulas de la SRS (anillo); invariancias (escala λ², λ⁴; intercambio b↔h; continuidad en `ro = t`; monotonía con el radio; límites a rectángulo y círculo; `0 < Av < A`; `∫Q dz = Iy`; RHS de aristas vivas ≡ I con `tw = 2t`, `tf = t`). Concordancia medida ~1e-15 con la cuadratura. Voladizo con I, RHS y CHS del editor frente a `P·L³/(3·E·Iy)` con la inercia de la referencia. No se usa ningún valor de prontuario.

## Consecuencias y límites

- Cambio aditivo del contrato público de `SectionRevision` y del esquema (v5). Sin dependencias nuevas.
- La I y el RHS/SHS del editor no se analizan en 3D hasta TK-028; en plano sí.
- El área de cortante energética no es la de Cowper ni una cota normativa; hoy no la consume ningún elemento (TK-013, TK-029).
- Fuera de E1: círculo macizo (ROUND), U, L, T y ejes principales (TK-027); J de I y RHS (TK-028); cortante con acuerdos y `Avy` de la I (TK-029); guardar perfil del editor en un catálogo de usuario y SVG/JSON (TK-030); GUI (TK-031).
- Aplicación experimental, no apta para justificar obras reales sin validación independiente.

## Enmienda E2: comparación con catálogo y estado CONTRASTADO (D12-B, D14-A; ACEPTADA 2026-11-05)

- `core/sections/contrast.py`: `compare_with_snapshot(sección, snapshot, tolerancia)` informa, por A, Iy, Iz y J, el valor de Veritas, el del catálogo, la diferencia relativa CON SIGNO `(Veritas − catálogo)/catálogo` y un dictamen `DENTRO`/`FUERA`/`NO_COMPARABLE`. Una propiedad ausente en cualquiera de los lados (J de la I y del RHS/SHS en Veritas; J no aportada por el catálogo) es `NO_COMPARABLE` y nunca se rellena. Tolerancia por defecto 1 % (parámetro de software, no normativo; editable con `--tol`; rango abierto (0, 1)).
- `StructuralModel.register_contrast(section_id, snapshot, tolerance)`: las propiedades dentro de tolerancia pasan de `CALCULADO` a `CONTRASTADO` en una NUEVA revisión (max + 1) con la misma geometría, el valor, la unidad y el método calculados por Veritas (nunca el valor del catálogo). `origin` cita catálogo, perfil, revisión, `source_sha256` completo, diferencia relativa y tolerancia. Fuera de tolerancia, o sin propiedades promovibles, no se registra nada. Repetir el contraste es idempotente.
- Contrato de `SectionRevision` (cambio aditivo): el recálculo antimanipulación acepta una propiedad `CONTRASTADO` si su valor coincide con el recálculo (1e-12 relativo), ignorando solo estado y origen. Un valor distinto se rechaza (`section.properties_mismatch`). El esquema v5 no cambia: `status` ya admitía el valor.
- `VERIFICADO` no lo emite ningún código: queda reservado a validación externa del usuario. `CONTRASTADO` es una afirmación registrada, no una prueba criptográfica; solo `source_sha256` permite comprobarla contra el catálogo. Los estados `IMPORTADO_SIN_VERIFICAR` del catálogo y su snapshot no se tocan (ADR-009/010 intactos).
- CLI (`cli_section_editor.py`, sin GUI): `section i|rhs|shs|chs --dim`, `section comparar`, `project add-section [--contrastar REF --db]`, `project assign-section [--parcial]` (reutiliza `replace_sections`, D5 de T-024); `project sections` muestra estado de A, Iy, Iz, J y cortante. `section rect` conserva su salida; `add-section rect` activa el cortante del rectángulo (D10-C). Códigos: 0 ejecutado; 2 diagnóstico o contraste sin propiedades dentro de tolerancia (archivo sin cambios).
- El catálogo se lee solo por `cli_profiles.read_snapshot` (frontera de la CLI); `section_editor` y `core` no importan `catalogs`.

## Decisiones del usuario sobre E1 (2026-11-05)

1. `Avy` de RHS/SHS: **habilitado** (implementado; invariancia b↔h probada).
2. CHS: **opción B**, forma cerrada incluida en `shear.chs_shear` (fórmula en el docstring; contrastada con la cuadratura de acordes; en pared muy fina limita la cuadratura).
3. Nombres de dimensión `b`, `t`, `d`, `ro`: **confirmados**.
4. `replacement.compatibility_errors` bloquea perfiles paramétricos con madera: **se mantiene**.

## Historial

- 2026-11-05: propuesto con E1 de T-025 (dominio, fórmulas, esquema v5 y migración 4 → 5). Aceptado por el usuario con E1.
- 2026-11-05: decisiones 1–4 del usuario aplicadas (Avy RHS/SHS, cortante CHS); suite 3871 pasadas.
- 2026-11-05: enmienda E2 redactada con la implementación de E2 (D12-B, D14-A); aceptada por el usuario con E2 (2026-11-05).
