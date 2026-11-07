# ADR-014 — Edición del modelo: comandos atómicos, historial y borrado con dependientes

**Estado: ACCEPTED** (2026-11-06, con la aceptación de T-030 E1; plan [T-030](../../plans/T-030.md) aprobado con D1…D9 = A).

**Relación con otros ADR.**
- [ADR-006](ADR-006-proyecto-persistido-hash-canonico.md): el historial se apoya en `ModelSnapshot` y `StructuralModel.restore`, que son también el contrato de guardado y de hash; no cambia el esquema de proyecto.
- [ADR-008](ADR-008-visor-3d-derivado.md): la escena sigue derivándose del snapshot; la GUI no obtiene una vía de escritura propia.

## Contexto

F4 (modelador) necesita crear, mover, borrar y reasignar entidades con deshacer/rehacer (GEO-009, TK-011), sin que ninguna interfaz pueda dejar el modelo en un estado que el dominio no admita. El dominio no tenía `remove_node`, `remove_member` ni historial (TK-009), y las exclusiones del peso propio por barra (T-019) quedaban colgando al borrar una barra.

## Decisión (E1)

1. **`StructuralModel.remove_member` y `remove_node` rechazan si hay dependientes (D4-A).**
   - Barra: se rechaza con `model.remove_member_dependents` si tiene cargas de barra (uniforme, puntual, distribuida o momento distribuido). El peso propio **no** cuenta como dependiente: si la excluía, la exclusión se limpia (revisión n+1 del peso propio; cierra TK-009). Si tras borrarla todas las barras que quedan estuvieran excluidas, se rechaza con `model.remove_member_all_excluded` (nombra la barra y pide incluir alguna en el peso propio).
   - Nudo: se rechaza con `model.remove_node_dependents` si lo usan barras, un apoyo, cargas nodales o asientos de su apoyo.
   - Consultas: `member_dependents(id)` y `node_dependents(id)` (`NodeDependents`) para que una interfaz pueda informar y confirmar.
   - Ambos suman 1 a `model_revision` y no borran materiales ni secciones (las revisiones se conservan).
2. **`ModelEditor` (`core/geometry/editing.py`, sin Qt) es la única vía de edición de una interfaz (D3-A).** `execute(nombre, acción)` ejecuta un comando atómico con nombre; hay comandos con nombre para nudos, borrado y cascada.
3. **Deshacer/rehacer por instantáneas (D2-A).** Cada paso guarda el `ModelSnapshot` anterior y el posterior en una pila acotada (por defecto 100; el más antiguo se descarta). Deshacer y rehacer sustituyen el contenido con `StructuralModel.replace_content` (nuevo; igual que `restore` pero **en el sitio**): entidades, conectividad y **hash** idénticos. Un comando nuevo vacía la pila de rehacer. Un comando que no cambia nada no deja paso. Si la restauración falla, el paso sigue en su pila.
4. **Atomicidad e identidad.** Si un comando falla, el modelo vuelve al estado previo aunque el dominio hubiera mutado algo a medias. `editor.model` es **siempre el mismo objeto** (el contenido se sustituye en el sitio), de modo que una referencia como `Project.model` sigue vigente; las vistas de solo lectura obtenidas antes (`model.nodes`, `members`…) quedan obsoletas. El editor **no es reentrante**: una acción no puede llamar al editor (`editing.reentrant`).
5. **Transacciones.** `with editor.transaction(nombre)` agrupa varios comandos en un solo paso de deshacer, todo o nada; se pueden anidar (se unen a la exterior) y dentro de una no se puede deshacer ni rehacer. Un comando que falla dentro de una transacción, y una transacción interna que falla, restauran su propio punto de guardado aunque la exterior capture la excepción.
6. **Borrado en cascada (D4-A).** `remove_member_with_dependents`, `remove_node_with_dependents` y `remove_members` borran la barra o el nudo **con** sus dependientes como UN solo paso. Si el **estado final** no es válido (p. ej. el peso propio quedaría con todas las barras que sobreviven excluidas) no cambia nada. El resultado no depende del orden: se borran antes las barras excluidas del peso propio, y los ids repetidos se ignoran.
7. **Vigencia de resultados (D5-A).** Sin lógica nueva: cualquier edición cambia el hash y las corridas pasan a STALE por T-012; deshacer devuelve el hash anterior.

## Consecuencias y límites

- Memoria: una instantánea por paso (hasta 100 × dos); aceptable para modelos de este tamaño. Si crece, el cambio a comandos inversos (D2-B) es local al editor.
- No se guarda la historia de revisiones anteriores de nudos y barras (TK-011 sigue abierto en esa parte; D2-C).
- No hay copiar, dividir, trasladar, rotar ni reflejar (E6), grupos ni capas (D8), ventana (E2…), ni comando de CLI nuevo.
- El editor valida la regla forma/material al asignar (enmienda E2a); `StructuralModel.assign_*` no la aplica (TK-036 abierto).
- **Exclusiones del peso propio:** al borrar una barra se **limpian** (no cuentan como dependiente que bloquee); es la lectura de TK-009 («limpiar o rechazar») que implementa E1; **confirmada por el usuario el 2026-11-06**.
- `_MEMBER_LOAD_TYPES` es una lista mantenida a mano; una prueba comprueba que contiene todos los tipos de carga con `member_id`, de modo que un tipo nuevo no pase desapercibido.
- Un archivo no cambia: el historial no se persiste.

## Historial

- 2026-11-06: propuesto con E1 de T-030.
- 2026-11-06: ACCEPTED al aceptar el usuario T-030 E1.
- 2026-11-07: enmienda de E2a (R2-D2…D5, aprobada): (a) `generation` y `subscribe`/`unsubscribe`: el entero sube tras cada comando que cambia el modelo, deshacer, rehacer o fallo con restauración; los avisos se emiten después de terminar la operación, uno por paso (una transacción, uno al cerrar), no pueden mutar el editor (`editing.reentrant`) y, si uno lanza, se avisa a todos y se propaga la primera excepción tras confirmar la operación; (b) `preview_remove_members`/`preview_remove_node` devuelven un `CascadePreview` inmutable calculado con las mismas funciones del borrado sobre una copia, con `error` si el estado final no sería válido; (c) `add_member`, `set_member_orientation`, `assign_material` y `assign_section` (uno o varios ids, un paso); (d) `add_member`/`assign_*` aplican `compatibility_errors` (regla de `replace_sections`) antes de asignar. Sin cambio de esquema, hash ni `StructuralModel`.
- 2026-11-07: enmienda de E2a aceptada por el usuario.
