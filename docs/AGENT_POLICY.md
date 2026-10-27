# Política de agentes, costes y revisión (Claude Code)

## Objetivo

Delegar de manera proporcional a dificultad y riesgo. Preferir una sola ejecución bien especificada a muchos agentes que releen el mismo contexto. Los agentes no son una excusa para saltar validación independiente de ingeniería.

## Niveles y modelos

Los nombres Astra, Sol y Luna son alias de rol usados en `CLAUDE.md`; cada uno se asigna a un modelo de Claude mediante el campo `model` del subagente (`opus`, `sonnet`, `haiku`) o, en la sesión principal, mediante `/model`.

| Nivel | Alias | Modelo Claude | Agentes | Tareas permitidas |
|---|---|---|---|---|
| L0 mecánico | Luna | `haiku` (Claude Haiku) | explorer, docs-maintainer | Inventarios, búsqueda, limpieza documental, formato, matrices de requisitos, resúmenes; sin decisión técnica crítica |
| L1 desarrollo | Sol | `sonnet` (Claude Sonnet) | implementer, test-engineer | Código acotado, integración, tests, diagnóstico reproducible, refactor con contrato aprobado |
| L2 crítico | Astra | `opus` (Claude Opus) | orquestador (sesión principal), structural-reviewer, architecture-reviewer | Plan, arquitectura, formulación FEM, interpretación CTE, revisión de algoritmos geométricos y seguridad |

**Fallback:** comprobar el modelo activo con `/model` o `/status`. Si Opus no está disponible para la sesión principal, orquestar con Sonnet y elevar el rigor de revisión; no asignar una tarea crítica a Haiku por ahorrar tokens. Si Sonnet no está disponible, detener las tareas de código que dependan de él y pedir al usuario una alternativa permitida. No afirmar una tarifa exacta ni garantía de menor coste sin medir el consumo real.

## Subagentes de proyecto

Definidos en T-001 como archivos `.claude/agents/<nombre>.md`. Frontmatter: `name` y `description` obligatorios; `tools` (lista separada por comas) y `model` (`haiku`, `sonnet`, `opus` o `inherit`) opcionales. Por convención, `name` en minúsculas con guiones. Las claves no reconocidas se ignoran sin error: revisar erratas con `/agents`.

| Agente | Alias / modelo | Herramientas sugeridas | Responsabilidad |
|---|---|---|---|
| (sesión principal) | Astra / `opus` | Todas | Descompone tickets, delega, fusiona evidencias, mantiene contratos, pide aceptación |
| explorer | Luna / `haiku` | Read, Grep, Glob | Mapea archivos y APIs; solo lectura |
| docs-maintainer | Luna / `haiku` | Read, Grep, Glob, Edit, Write | Requisitos, changelog, guías, matrices; no fórmulas estructurales |
| implementer | Sol / `sonnet` | Read, Grep, Glob, Edit, Write, Bash/PowerShell | Un módulo y archivos exclusivos por ticket |
| test-engineer | Sol / `sonnet` | Read, Grep, Glob, Edit, Write, Bash/PowerShell | Pruebas, fixtures, comparaciones y reproducibilidad |
| architecture-reviewer | Astra / `opus` | Read, Grep, Glob | Revisa contratos, capas, migraciones y riesgos sistémicos; solo lectura |
| structural-reviewer | Astra / `opus` | Read, Grep, Glob | Audita FEM, convenciones, propiedades de perfiles, equilibrio y aplicación CTE; solo lectura y exige referencia |

También pueden usarse los agentes integrados de Claude Code cuando encajen (p. ej. `Explore` para búsquedas amplias de solo lectura o `Plan` para diseño), respetando las mismas reglas de nivel.

## Flujo de delegación

1. El orquestador determina si la tarea es divisible y define dueños de archivos.
2. Delegar una investigación de lectura a Luna, implementación localizada a Sol o revisión estructural a Astra.
3. No abrir dos agentes con edición sobre el mismo archivo ni tareas fuertemente dependientes en paralelo. Para trabajo paralelo con edición, valorar aislamiento en worktree.
4. Entregar a cada agente **extracto de SRS + contrato + archivos concretos**, no todo el historial ni un volcado del proyecto. El subagente solo conoce lo que recibe en su prompt.
5. Solicitar evidencia: archivos, cambios, tests, salidas y dudas; no respuestas vagas.
6. El orquestador revisa el diff y pruebas antes de la demo al usuario. El informe del subagente no lo ve el usuario: el orquestador debe trasladar lo relevante.
7. La validación técnica externa al LLM sigue siendo obligatoria para cálculos estructurales.

## Economía de tokens

- Máximo sugerido de 4 subagentes concurrentes; usar menos cuando compartan archivos.
- No delegar tareas que tardan menos en resolverse directamente.
- Luna no reescribe código FEM complejo ni interpreta normativa.
- Sol no modifica reglas CTE por inferencia; debe tener fórmula y prueba explícitas.
- Astra solo cuando haya impacto estructural, normativo, diseño de contratos o coordinación compleja.
- Revisar el consumo real (p. ej. `/cost` o `/status` cuando la interfaz lo muestre); ajustar asignación con resultados, no supuestos.

## Capacidad de subagentes y permisos

En Claude Code, el campo `tools` del subagente restringe efectivamente las herramientas disponibles: un revisor con solo Read/Grep/Glob no puede editar archivos. Si se omite `tools`, el subagente hereda todas las herramientas de la sesión principal. Los subagentes heredan además el modo de permisos y los servidores MCP de la sesión. El orquestador debe comprobar la configuración real de cada agente y no confiar en instrucciones de texto para garantizar solo lectura.
