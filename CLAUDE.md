# Veritas Structural Calculation 3D — instrucciones raíz para Claude Code

Este repositorio implementa Veritas Structural Calculation 3D, aplicación experimental de estructuras de barras y nudos 3D con Python, PySide6, PyVista y OpenSeesPy.

## Autoridad y lectura

1. Leer primero docs/DECISIONS.md, docs/ARCHITECTURE.md, docs/ROADMAP.md y el ticket activo.
2. Consultar la SRS v1.1.0 y la propuesta v1.1.0 únicamente en las secciones relacionadas con el ticket. No cargar todos los documentos en cada turno.
3. En conflicto: instrucción explícita actual del usuario > SRS/decisiones aprobadas > contratos modulares > propuestas generales.
4. No cambiar decisiones arquitectónicas ni alcance sin un ADR y confirmación del usuario.

## Principios no negociables

- Una barra tiene exactamente un material y una sección. No existen miembros multimaterial.
- Ambos extremos de una barra son rígidos por defecto; las uniones analíticas I/J son independientes y configurables por seis GDL.
- El núcleo de dominio jamás importa PySide6, PyVista ni OpenSeesPy. OpenSeesPy solo se usa tras un adaptador.
- Geometría de perfil, propiedades calculadas, material, acciones, normativas y resultados son entidades separadas y versionadas.
- Un análisis fallido, singular o una comprobación incompleta nunca se muestra como válido/aprobado.
- Primera normativa: CTE España DB-SE, DB-SE-AE, DB-SE-A y DB-SE-M. Aluminio FEM sí, verificación normativa NO_COMPROBADA hasta norma validada. EN/SIA posteriormente.
- Caso no esté implementado en la planificación, añade un editor manual de configuración de normativas (crear, editar, copiar, borrar)
- No realizar diseño de detalle de uniones.
- Esta aplicación es experimental, no apta para justificar obras reales sin validación independiente.

## Forma de trabajo

- Una tarea = un módulo pequeño o una porción vertical probada; no abrir múltiples frentes sin autorización.
- Antes de editar: describir alcance, archivos afectados, dependencias, pruebas y criterios de salida.
- Guardar el plan de cada ticket en `plans/<ID-TICKET>.md`, incluido su estado de aprobación. Mantener allí este plan y los siguientes; ajustar o editar el mismo documento con historial breve de cambios. Los cambios de alcance, contratos o dependencias sujetos a aprobación deben distinguirse del plan ya aprobado y esperar confirmación antes de implementarse.
- Preferir tests de fórmulas/equilibrio antes de GUI. No inventar resultados analíticos o normativos.
- Código tipado Python, unidades SI internas, convenciones de ejes/ signos declaradas, errores explícitos.
- No sobrescribir cambios ajenos; revisar estado git y hacer cambios reversibles.
- No introducir dependencias nuevas, mover/eliminar datos o modificar contratos públicos sin aprobación.
- Al terminar: pruebas ejecutadas, resultados, límites conocidos, archivos, instrucciones de prueba manual y pregunta de aceptación al usuario.
- Detenerse ante la puerta de validación de usuario. No avanzar a la siguiente entrega sin aceptación.

## Agentes y uso de modelos

Leer docs/AGENT_POLICY.md. Orquestador principal Astra cuando disponible; delegar extracción/documentación a Luna, implementación acotada a Sol y revisión de decisiones matemáticas críticas a Astra. Delegación solo cuando reduce trabajo y los agentes no escriben archivos compartidos en paralelo.

## Contexto y sesiones

Leer docs/CONTEXT_POLICY.md. Revisar uso de contexto con /context cuando sea posible al inicio, después de hitos y antes de tareas extensas. A ~55–60 % preparar checkpoint; a ~65–70 % notificar y recomendar nueva sesión antes de iniciar trabajo largo; no esperar al 80 %. Nunca afirmar porcentajes si la interfaz no los muestra. No provocar compaction para ocultar falta de handoff.

## Documentos operativos

- docs/ARCHITECTURE.md: dependencias y contratos.
- docs/ROADMAP.md: fases y puertas de aprobación.
- docs/MODULE_CONTRACT.md: interfaz de cada entrega probada por usuario.
- docs/VALIDATION_PLAN.md: pruebas estructurales.
- docs/AGENT_POLICY.md: distribución económica de modelos.
- docs/CONTEXT_POLICY.md: continuidad de sesiones.
- tasks/BACKLOG.md: tickets iniciales.
- tasks/TEMPLATE.md: definición de tareas.
- plans/: planes de tickets, aprobación y revisiones.
- handoffs/TEMPLATE.md: checkpoint para sesiones nuevas.

## Plan de Implementación

Primer paso: evaluar en detalle el proyecto utilizando el MCP rea para determinar su complejidad. Luego elaborar un detallado plan de implementación y guardarlo en el archivo .planning.md - este plan de implementación debe de ser elaborado de forma que te sirva de guía durante todo el proceso de desarrollo. Detallar todas las funcionalidades e GUI a clonar en una secuencia lógica y dentro de lo posible secuencial.

## Control de tareas

Elabora una lista desglosada de tareas atómicas basada en el plan de implementación. Crea el archivo .tasks.md como referencia para el control de tareas. Cada vez que realices total o parcialmente una tarea, o altere tareas ya realizadas, actualiza el estado de la tarea en el archivo .tasks.md. En la columna "Comentarios" del archivo .tasks.md, anota que has realizado la tarea y cualquier comentario relevante.

Caso una tarea no pueda ser realizada debido a dependencia de features aun no desarrolladas o cualquier otro motivo, en el archivo .ticktets.md crea un "Ticket" asociando la tarea pendiente a su tarea complementaria futura, de forma que una tarea pendiente pueda ser finalizada en el momento correcto.

Serás el único responsable de marcar una tarea como realizada, pendiente o en marcha. Las tareas pendientes se marcarán con "[ ]", las tareas finalizadas con "[x]" y las tareas en marcha con "[o]"

Para que el usuario pueda realizar un seguimiento proactivo del desarrollo del proyecto crea un informe en .tasks.html con un dashboard indicativo del avance del desarrollo desde de diversas perspectivas. Actualizalo al finalizar cada tarea atómica.