# ADR-002 — Primer adaptador plano y proceso solver independiente

**Estado: PROPUESTO** — pendiente de revalidación del entorno y aprobación del usuario. **Fase:** F1B (adaptador OpenSeesPy). **Ticket:** T-007 en `tasks/BACKLOG.md`.
Depende de [ADR-001](ADR-001-convenciones-dominio.md) (ACCEPTED el 2026-10-27). La aprobación no acreditaría implementación ni validación del solver. El contrato detallado y las pruebas se definirán en `plans/<ID-TICKET>.md` del ticket del adaptador.

## Problema y hechos a verificar al iniciar el ticket

Las siguientes observaciones proceden del análisis previo a este reinicio y **deben revalidarse** en el entorno real antes de aprobar el ADR, pues versiones y compatibilidades cambian:

- La versión de Python del dominio/app se fija en F0. Si fuera posterior a la soportada por OpenSeesPy (p. ej. 3.14), habría incompatibilidad con el solver.
- La [documentación del proveedor](https://openseespydoc.readthedocs.io/en/latest/) anunciaba OpenSeesPy 3.8.0.0 para Windows con requisito Python 3.12. [PyPI del paquete Windows](https://pypi.org/project/openseespywin/3.8.0.0/) publicaba un wheel x86-64 con metadato >=3.12 y clasificador 3.12; su etiqueta genérica `py3-none-win_amd64` no prueba compatibilidad binaria con versiones posteriores. Comprobar versiones vigentes, intérpretes disponibles (`py -0p`) e importación real del solver.
- [elasticBeamColumn 3D](https://openseespydoc.readthedocs.io/en/latest/src/elasticBeamColumn.html) necesita A, E, G, J, Iy e Iz. Si al llegar a F1B el dominio no dispone de G/Poisson ni de J validada, no es lícito inventar Poisson, deducir G sin hipótesis/datos ni sustituir J por Iy+Iz. Cw tampoco estaría disponible; este elemento no modela alabeo. E escalar no completa una ley constitutiva 3D.

## Decisión propuesta

1. Acotar el primer adaptador a estática lineal Euler–Bernoulli de barras rectas centroidales en plano global XZ, elemento `elasticBeamColumn` 2D, tres GDL activos por nudo. Usa A, E e inercia de flexión seleccionada de Iy/Iz mediante la base local de ADR-001. No modifica geometría 3D ni conexiones. FEM-002 espacial sigue pendiente. Es una reducción explícita de esta primera entrega, no un cambio del objetivo 3D del producto.
2. Admitir acero/aluminio con E declarado ISOTROPIC; rechazar madera con E solo LONGITUDINAL en esta entrega: falta contrato de dirección material y validación específica. No ampliar materiales. E no se certifica como propiedad de diseño.
3. Conservar la versión de Python y dependencias del dominio/app; si son incompatibles con OpenSeesPy, crear, solo tras autorización, un entorno solver separado (p. ej. `.venv-opensees312` con Python 3.12 x64 y las versiones de `openseespy`/`openseespywin` verificadas). Primer gate técnico: importación y smoke test real. Si falla, detener y revisar propuesta; no compilar ni migrar silenciosamente.
4. Un proceso nuevo por corrida, `subprocess` sin shell, intérprete configurado mediante ruta absoluta. Worker independiente en `fem/adapters/opensees_worker.py`, sintaxis compatible con el intérprete del solver, solo stdlib/OpenSeesPy; no importa `core` ni instala la aplicación en el entorno del solver. Traductor y runner permanecen en el entorno del dominio/app.
5. Intercambio privado y efímero validado de datos JSON por stdin y fichero temporal de respuesta controlado por el padre, logs stdout/stderr separados. Protocolo interno identificado, sin pickle/eval ni código procedente del modelo. No es formato persistido de proyecto ni contrato de interoperabilidad pública.
6. Procedencia en memoria mediante snapshots exactos de entrada/configuración, UUID de corrida y versiones observadas. No generar hash canónico, model_revision, CURRENT/STALE ni almacenamiento histórico; esas especificaciones siguen pendientes en ADR-001. Se solicita una excepción incremental expresa al contrato de hash de [ARCHITECTURE.md](../ARCHITECTURE.md) para esta demo, no su eliminación ni sustitución definitiva. El resultado solo describe esa ejecución experimental; FEM-005/DAT-004 no quedan completos.

## Alternativas

- **3D ahora:** exige ampliar material/sección y validar torsión. Cambia contratos y alcance; posponer hasta ticket específico aprobado.
- **Fijar todo el proyecto en la versión de Python del solver:** evita dos entornos; afecta al contrato Python y herramientas de F0. Valorar al decidir el entorno en F0, antes de que existan módulos aceptados.
- **Compilar OpenSeesPy para otra versión de Python:** toolchain, mantenimiento y validación nuevos; fuera de ticket.
- **Esperar soporte oficial de la versión del dominio:** evita dos entornos pero bloquea esta entrega; alternativa si se rechaza el proceso separado.

## Consecuencias y aprobación

Dos intérpretes implican instalación, empaquetado del worker y pruebas del protocolo. El aislamiento ya está previsto por arquitectura; la separación de versiones y el alcance plano requieren esta decisión explícita. No promete estabilidad espacial, cancelación interactiva ni aptitud normativa. El timeout terminará y recogerá el proceso; no se publica resultado parcial.

La aprobación solicitada abarca alcance plano, nuevos contratos `fem` del plan del ticket, proceso con dos versiones (si procede) y dependencias propuestas. La instalación de un intérprete adicional requiere autorización de entorno y receta concreta; ningún comando de instalación se ejecuta en planificación. La decisión comercial/licencia sigue en DEC-006; no se autoriza redistribución.

**Aprobación del usuario:** pendiente.

## Revisión propuesta (2026-10-27) — pendiente de aprobación

DEC-011 fija Python 3.12 para todo el proyecto. Comprobación en T-001 (venv temporal fuera del repo): `openseespy`/`openseespywin` 3.8.0.0 se importan en CPython 3.12.9 y un modelo mínimo termina con `analyze=0` (solo ejecución; sin validación de resultados). Propuesta de revisión:

- Punto 3: un único intérprete 3.12 para dominio, app y worker; sin entorno `.venv-opensees312` separado. OpenSeesPy sería dependencia del proyecto, añadida en T-007 con aprobación.
- Punto 4: se mantiene un proceso nuevo por corrida (aislamiento, FEM-009, NFR-003); el worker sigue sin importar `veritas.core`.
- Alternativas: «Fijar todo el proyecto en la versión de Python del solver» pasa a ser la opción adoptada por DEC-011.

Los puntos 1, 2, 5 y 6 no cambian. Esta revisión y el ADR completo se someten a aprobación antes de T-007.

## Historial

- 2026-10-27: reinicio del proyecto con Claude Code; ADR conservado como PROPUESTO. Hechos de entorno reclasificados como verificaciones pendientes.
- 2026-10-27: revisión propuesta tras DEC-011 (un solo intérprete 3.12); pendiente de aprobación.
