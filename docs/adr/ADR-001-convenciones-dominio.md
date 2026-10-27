# ADR-001 — Ejes, signos, identidad, revisiones y errores

**Estado:** ACCEPTED. **Fecha:** 2026-10-27. **Fase:** F0 (contrato previo al primer cálculo, F1A).
**Aprobación del usuario:** expresa, 2026-10-27 («ADR-001: aprobado»). Este documento establece el contrato de diseño aprobado; no acredita implementación ni validación FEM. Las implementaciones se realizan en los tickets de `tasks/BACKLOG.md`.

## Contexto y alcance

La arquitectura exige declarar ejes y signos antes del primer cálculo. El proyecto se inicia desde cero: el entorno y el sistema de unidades SI se establecerán en F0. Faltan convenciones comunes para que dominio, solver, GUI y reportes no interpreten de forma distinta los mismos datos.

Este ADR fija contratos aprobados; no implementa Node/Member, matrices de rigidez, solver, liberaciones, persistencia ni comprobaciones normativas. No altera DEC-001…010 ni el sistema de unidades que se defina en F0. Referencias internas: ARCHITECTURE.md; SRS GEO-001/004, CON-003/006, SEC-001/011, FEM-004/005/010, RES-007, DAT-001/004 y NFR-007. Todos siguen sin iniciar: un ADR no satisface su implementación.

## 1. Sistemas de coordenadas y grados de libertad

- Global XYZ cartesiano dextrógiro, Z hacia arriba. La dirección de gravedad será -Z; el valor de g y el peso propio se fijarán en el ticket de cargas, no aquí.
- Local xyz dextrógiro. Barra dirigida I -> J, estación s en [0,L], x longitudinal. I/J son explícitos y no se ordenan por coordenadas ni identificador.
- Orden común de seis GDL: (Ux, Uy, Uz, Rx, Ry, Rz). Traslaciones en m, rotaciones en rad, positivas según regla de la mano derecha. Vector conjugado: (Fx, Fy, Fz, Mx, My, Mz), N y N*m.
- Los seis estados GDL de una conexión I/J se expresan en la base LOCAL de la barra. Desplazamientos nodales, apoyos, cargas nodales y reacciones usan base GLOBAL por defecto; una base distinta debe declararse y estar soportada. Compartir orden de componentes no significa compartir base.
- El sistema global/local se indica en cada carga, apoyo o resultado. No inferirlo por la pantalla. Rigidez de conexión: traslacional N/m, rotacional N*m/rad; su incorporación al registro de unidades se decide en el ticket de unidades (F0).
- Base inicial no deformada para el primer análisis lineal. Rotaciones finitas y bases corrotacionales requieren contrato específico posterior.

### Construcción propuesta de la base local

Para extremos pI, pJ y longitud L = ||pJ-pI||:

1. ex = (pJ-pI)/L. Rechazar longitud nula o no representable y coordenadas no finitas. El umbral físico para nudos casi coincidentes pertenece al ticket de geometría (F1A); no usar el umbral angular como longitud mínima.
2. Seleccionar una referencia r de dirección local +y y normalizarla ANTES de comprobar la proyección: rhat=r/||r||. Rechazar r cero/no finita. Por defecto r = global +Y; si ||rhat-(rhat·ex)ex|| <= 1e-8, usar global +Z y normalizarla. El umbral es angular/adimensional, para evitar amplificar errores al dividir por una proyección casi nula.
3. w = rhat - (rhat·ex)ex; ey0 = w/||w||; ez0 = ex × ey0. Referencia explícita casi paralela se rechaza usando el mismo umbral, sin sustitución silenciosa.
4. Giro alfa alrededor de +ex: ey = cos(alfa)ey0 + sin(alfa)ez0; ez = -sin(alfa)ey0 + cos(alfa)ez0. Se almacena alfa en rad.
5. Conservar referencia efectiva y alfa en la revisión de barra. Al editar extremos, recalcular con esa referencia; si degenera, pedir orientación explícita. No activar otro fallback silenciosamente ni modificar una revisión histórica.

El fallback es una ayuda de creación, no una promesa de orientación continua para todas las direcciones. Cambiar I/J explícitamente requiere nueva revisión y reinterpretación controlada de orientación, cargas y extremos; queda prohibido intercambiarlos silenciosamente. El ticket de geometría (F1A) deberá rechazar la operación si aún no puede conservar correctamente esos datos.

Q contiene ex, ey, ez como filas. Para un vector libre: v_local = Q v_global y v_global = Q^T v_local. Comprobar QQ^T=I y det(Q)=+1. Fuerzas y momentos usan la misma rotación solo si están referidos al mismo punto; con offsets se necesita además el transporte del momento y una formulación validada posterior.

### Ejes de sección

Plano transversal yz con origen en el CENTROIDE de la sección para las propiedades Iy/Iz/Iyz. Para un rectángulo centrado, b en y y h en z: A=bh, Iy=∫z²dA=bh³/12, Iz=∫y²dA=hb³/12; Iyz=∫yz dA, con este signo explícito (cero para el rectángulo centrado). El tensor geométrico usa -Iyz fuera de la diagonal. Ejes no centroidales deben etiquetarse y requieren transporte de inercias; no reutilizar estas fórmulas sin ese ajuste. El tratamiento de flexión acoplada de secciones no simétricas queda para su ticket. J no se deduce de Iy+Iz. Los ejes principales no sustituyen silenciosamente a y/z. A se expresa en m2, Iy/Iz/Iyz/J en m4; una propiedad no disponible no se representa como cero.

## 2. Signos de cargas, reacciones y esfuerzos

- Fuerzas nodales positivas en +X/+Y/+Z, momentos positivos según mano derecha. Toda carga local lleva base y extremo/estación explícitos.
- Reacción: acción del apoyo/terreno SOBRE la estructura, en global por defecto. Mostrar el sistema de referencia.
- Esfuerzos de sección q(s)=(N,Vy,Vz,T,My,Mz): acción de la porción hacia J sobre la porción hacia I en el corte de normal +ex. N positivo significa tracción. Vy/Vz positivos siguen +ey/+ez; T/My/Mz siguen mano derecha sobre +ex/+ey/+ez.
- Fuerzas de extremo pI/pJ: acciones del nudo SOBRE la barra, componentes en la misma base local. Los momentos de pI están referidos a I y los de pJ a J, cada uno al punto de su corte correspondiente; no trasladados ambos a un único origen. Son objetos distintos de q(s).
- Sin offsets ni acciones concentradas adicionales entre el punto extremo y el corte: q(0+)=-pI, q(L-)=pJ, tanto para fuerzas como para momentos. Con cargas distribuidas los esfuerzos varían a lo largo de la barra; no interpolar esfuerzos por defecto ni omitir las cargas al reconstruirlos.
- Una carga concentrada interior puede producir salto. El resultado debe distinguir s- y s+; nunca promediar el salto para fabricar continuidad.
- La convención de signos es propia de Veritas Structural Calculation 3D; no se presume idéntica a un vector devuelto por OpenSeesPy. El ticket del adaptador OpenSeesPy (F1B) deberá documentar cada respuesta del elemento y verificar su transformación con casos independientes antes de normalizarla.

### Ejemplos de referencia que deberán guiar los tests

Para barra horizontal I=(0,0,0), J=(L,0,0), alfa=0: ex=X, ey=Y, ez=Z.

| Caso | Resultado de referencia |
|---|---|
| Tracción axial P>0 aplicada a J en +X, I fijo | RIx=-P; pIx=-P, pJx=P; N(s)=+P |
| Voladizo con fuerza -P Z en J | RIz=+P, RIMy=-PL; Vz(s)=-P; My(s)=P(L-s) |
| Barra vertical I=(0,0,0), J=(0,0,L), alfa=0 | ex=Z, ey=Y, ez=-X |
| Barra en +Y, referencia por defecto | fallback +Z: ex=Y, ey=Z, ez=X |
| Barra en +X, giro alfa=pi/2 | ex=X, ey=Z, ez=-Y |

Los dos primeros casos se derivan del equilibrio de cada cuerpo libre, no de un resultado de solver. Hipótesis: estática lineal, base constante inicial, sin offsets y sin acciones concentradas adicionales aplicadas directamente al extremo de la barra (las cargas del ejemplo son nodales). Como valores de inspección: L=2 m, P=1000 N; el voladizo tiene RIz=1000 N, RIMy=-2000 N*m, Vz=-1000 N y My(0)=2000 N*m, My(L)=0. No se simulan desplazamientos ni se acredita una formulación de viga con esta tabla.

Casos de frontera para implementación futura: referencia justo por debajo/en/por encima de 1e-8; referencia guardada que degenera tras editar extremos; coordenadas no finitas; longitud cero. Revertir I/J con alfa=0 y referencia +Y conserva ey y cambia signos de ex y ez, pero también cambia asociaciones de extremos y componentes locales; no es una operación automática de preservación de datos.

## 3. Identidad y referencias

- ID de entidad: UUID v4 generado con biblioteca estándar, inmutable, representado como texto canónico minúsculo con guiones al persistir. Etiqueta visible/número de fila es independiente y editable.
- IDs no se reciclan tras borrar. Copiar una entidad crea otro ID; una revisión de la misma entidad conserva el ID lógico.
- Una referencia versionada es (entity_id, revision). revision es entero positivo desde 1; bool no es una revisión. Un mismo par identifica contenido inmutable.
- MaterialRevision y SectionRevision se referencian por su par exacto, no por «la última». Editar catálogo no altera barras existentes; reasignar a nueva revisión es operación explícita.
- Dos entradas con el mismo par y distinto contenido son conflicto, no sobrescritura. ID de solver es un entero interno de cada corrida; el adaptador conserva un mapa UUID/revisión/extremo -> tags. Tags no sustituyen identidad de dominio.
- Objeto valor como Quantity no tiene identidad/revisión propia. Los objetos persistidos relevantes se versionan por la entidad que los contiene. No agregar ID a cada vector o número.

## 4. Revisiones, formatos y resultados

| Concepto | Propuesta |
|---|---|
| entity_revision | Entero positivo creciente dentro de cada identidad; contenido previo inmutable |
| model_revision | Entero creciente por transacción válida que cambia el modelo analítico; operación rechazada no incrementa |
| schema_version | Entero independiente para formato persistido; incompatibilidad requiere migración probada |
| Versión de aplicación/adaptador/solver | Campos separados, no equivalentes a revisión de modelo |
| analysis_run_id | UUID independiente por ejecución, incluso al repetir idéntica entrada |

Deshacer/restaurar produce una nueva revisión del modelo, aunque pueda referenciar revisiones antiguas inmutables; no hace retroceder el contador. Cambios solo de presentación no alteran model_revision. Cambios de geometría, material/sección, apoyos, conexiones o cargas invalidan resultados actuales; configuración de análisis nueva también exige nueva coincidencia de procedencia.

Resultado asociado a una clave con hash del snapshot analítico (incluidas referencias y contenido necesario), configuración y versiones de adaptador/solver. Se propone SHA-256, pero la codificación canónica y su versión deben definirse y probarse en el contrato del adaptador/persistencia antes de generar hashes de producción: orden estable, SI, referencias resueltas, floats finitos, tratamiento de -0.0 y exclusión explícita de datos de presentación. Este ADR no implementa ni simula ese hash.

Separar estado de ejecución (PENDING/RUNNING/SUCCEEDED/FAILED/CANCELLED) de vigencia (CURRENT/STALE). Un resultado antiguo puede conservar éxito histórico y ser STALE para el modelo actual. Éxito de análisis no significa cumplimiento normativo. Estados de comprobación futuros: PASS/FAIL/NOT_CHECKED/NOT_APPLICABLE/ERROR, con traducciones UI como NO_COMPROBADA.

## 5. Errores y avisos

Contrato futuro de diagnóstico: code estable (ASCII), message_key traducible, parámetros tipados, severidad ERROR/WARNING, ubicación opcional (entity_id, revision, campo, extremo o estación) y causa técnica separada. La lógica utiliza code, nunca el texto traducido.

| Código inicial propuesto | Uso |
|---|---|
| INPUT_INVALID_TYPE | Tipo de entrada incorrecto |
| INPUT_NONFINITE | NaN/infinito |
| UNITS_INCOMPATIBLE | Dimensiones incompatibles |
| UNITS_UNREPRESENTABLE | Overflow/underflow total |
| GEOMETRY_ZERO_LENGTH | Extremos coincidentes |
| GEOMETRY_INVALID_ORIENTATION | Referencia cero/casi paralela o base inválida |
| REFERENCE_NOT_FOUND | Referencia no resoluble |
| REVISION_CONFLICT | Mismo par ID/revisión con contenido distinto o actualización obsoleta |
| UNSUPPORTED_CONFIGURATION | Configuración no implementada/validada |
| ANALYSIS_SINGULAR | Singularidad confirmada por diagnóstico suficiente |
| ANALYSIS_NONCONVERGENCE | Falta de convergencia reportada |
| ANALYSIS_FAILED | Fallo de solver sin diagnóstico específico fiable |

No etiquetar todo fallo como «mecanismo». ERROR bloquea aceptación de operación o publicación de resultado como válido; WARNING no convierte lo no soportado en soportado. No inventar resultados cero como sustituto. En un fallo de corrida se conserva el modelo y el diagnóstico.

Cada frontera pública (API, CLI) declarará en su ticket las excepciones y códigos de salida que emite; no inferir códigos analizando mensajes de excepciones. Cuando se implemente una frontera concreta deberá recibir categorías explícitas y probarlas; cualquier cambio incompatible de una API ya aceptada necesitará aprobación aparte. Aquí se reservan los nombres y significados, no se afirma que ya se emitan.

## Alternativas y consecuencias

- Z arriba facilita lectura de gravedad y modelos de edificación. Y arriba también es posible, pero obliga a otra convención coherente: alternativa descartada al aprobar este ADR.
- Proyectar +Y conserva xyz=XYZ para barras en +X. Fallback explícito evita división casi por cero; puede introducir discontinuidad de orientación al crear barras, por eso se conserva la referencia efectiva y se permite orientación explícita.
- UUID separa identidad de numeración y etiquetas; requiere mapas de solver. Numeración secuencial como identidad global dificulta importación/fusión y no se propone.
- Revisiones inmutables aumentan almacenamiento pero hacen reproducibles referencias y resultados. Evitan mutación silenciosa de catálogos ya usados.
- No normalizar signos del solver por intuición: la adaptación y las pruebas específicas son trabajo obligatorio futuro.

## Puerta de aprobación y seguimiento

Los cinco grupos anteriores están aprobados como contrato de diseño. No se realizó una revisión crítica por structural-reviewer antes de la aprobación; se recomienda hacerla al planificar el ticket de geometría (T-003) y registrar cualquier precisión como revisión de este ADR con nueva aprobación. Esa revisión es documental, no constituye validación de un solver ni auditoría profesional externa. La trazabilidad prevista hacia tests está en [VALIDATION_PLAN.md](../VALIDATION_PLAN.md). Las implementaciones seguirán en sus tickets, con demo/tests de su API real. No cerrar GEO/FEM/DAT ni declarar F0 completa solo por aceptar este ADR.

Implementación y validación numérica del producto: NO_IMPLEMENTADA.

## Historial

- 2026-10-27: reinicio del proyecto con Claude Code; ADR conservado como PROPUESTO, pendiente de revisión y aprobación del usuario.
- 2026-10-27: usuario aprueba ADR-001; estado ACCEPTED. Sin implementación ni validación numérica.
