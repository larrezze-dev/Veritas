# Gestión preventiva de ventana de contexto (Claude Code)

## Objetivo

Minimizar la compresión automática (auto-compact) y preservar estado verificable de cada módulo. La compresión puede perder trazabilidad técnica; preferir nuevas sesiones planificadas y handoffs en Markdown.

## Fuente de medida

Usar la métrica real que exponga Claude Code: `/context` muestra el uso de la ventana de contexto en la sesión; `/status` muestra modelo, cuenta y configuración. Un agente **no debe afirmar** que ve porcentajes de ventana si la interfaz o herramienta no los expone. No inventar estimaciones precisas a partir de longitud de conversación.

## Umbrales operativos (orientativos, no configuraciones mágicas)

| Nivel | Acción |
|---|---|
| Menos de 50 % | Trabajar normalmente con contexto acotado |
| 50–60 % | Crear/actualizar handoff tras hito, reducir lecturas y logs |
| 60–65 % | Evaluar cuánto contexto requiere el próximo ticket; no iniciar tarea grande |
| 65–70 % | Avisar al usuario y recomendar cerrar sesión al concluir operación atómica |
| 70–75 % | Escribir handoff completo y abrir sesión nueva (`/clear` o nueva sesión) |
| Más de 75 % | No iniciar trabajo nuevo; preparar continuidad inmediata |

**Regla:** no esperar a 80 %. El criterio es anticipatorio: incluso al 45 %, si el siguiente ticket necesita gran análisis o muchos archivos, iniciar sesión fresca tras checkpoint. Los límites no pueden imponerse por Markdown al motor: son una política que el orquestador debe ejecutar.

## Cadencia de monitoreo

- Comprobar al comenzar la sesión, tras completar cada ticket y antes de cargar SRS grande, logs de tests, catálogos o traza FEM.
- En una operación prolongada, revisar en cada hito técnico o aproximadamente cada 10–15 acciones importantes si existe métrica accesible.
- Mantener `CLAUDE.md` corto; leer únicamente documentos necesarios (secciones concretas de la SRS, no el documento completo); limitar resultados de terminal y citar rutas.
- Delegar exploraciones amplias a subagentes: su lectura no consume el contexto de la sesión principal. Reciben contexto mínimo y devuelven resúmenes con enlaces a evidencias.

## Handoff obligatorio

Guardar en `handoffs/SESSION-AAAA-MM-DD-NNN.md`, a partir de `handoffs/TEMPLATE.md` (la plantilla se crea en F0), con:

1. Objetivo y ticket actual.
2. Decisiones aprobadas y cambios ADR.
3. Estado implementado (rutas y hashes/commit cuando proceda).
4. Pruebas realizadas, comandos y resultados; fallos pendientes.
5. Lo que **no** debe modificarse.
6. Próxima acción mínima y cómo verificarla.
7. Datos para usuario: qué probar, cómo y qué aceptación falta.

El orquestador debe comprobar que el handoff se ha guardado y explicar al usuario cómo empezar la siguiente sesión: “Lee CLAUDE.md y handoffs/SESSION-... y continúa desde el checkpoint; no rehagas los módulos aprobados”. `CLAUDE.md` se carga automáticamente en cada sesión; el handoff debe indicarse explícitamente.

## Qué hacer sin telemetría

Si la superficie de Claude Code en uso no muestra el porcentaje de contexto: notificar la limitación; usar cierres por ticket, sesiones breves, checkpoints tras hitos y apertura preventiva antes de trabajos largos. Nunca depender de un porcentaje inventado.

## Compaction

No llamar `/compact` de manera rutinaria solo para prolongar la misma sesión. Si Claude Code compacta automáticamente, conservar el handoff externo y reconstruir las referencias de estado a partir de él. Si no es viable abrir sesión nueva, proponer `/compact` solo con consentimiento del usuario y tras guardar el handoff.
