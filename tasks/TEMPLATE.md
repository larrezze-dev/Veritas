# <ID-TICKET> — <Título corto>

**Fase:** <F0…F8> · **Prioridad:** <P0/P1/P2> · **Estado:** NO INICIADO
**Plan:** `plans/<ID-TICKET>.md` · **Dependencias:** <tickets / ADR que deben estar aceptados>

Estados admitidos: `NO INICIADO`, `PLAN PROPUESTO`, `PLAN APROBADO`, `EN CURSO`, `EN VALIDACIÓN` (entregado, esperando aceptación), `ACEPTADO` (fecha y referencia), `RECHAZADO` (en corrección en este mismo ticket).

## Objetivo

<Qué unidad demostrable entrega este ticket, en una o dos frases.>

## Requisitos SRS cubiertos

| ID SRS | Aporte previsto | Límite (lo que NO cubre) |
|---|---|---|
| <GEO-001> | <…> | <…> |

## Alcance

- Incluido: <…>
- Excluido: <…>

## Contratos y decisiones aplicables

- ADR / DEC: <ADR-001 §…, DEC-…>
- Convenciones de ejes, signos y unidades: <referencia>
- API pública y formato persistido afectados: <ninguno / descripción>

## Archivos previstos

| Archivo | Acción | Responsable (agente) |
|---|---|---|
| <ruta> | crear / modificar | <implementer / test-engineer / orquestador> |

## Pruebas

- Unitarias: <casos positivos y negativos>
- Integración: <…>
- Referencia independiente: <fórmula, cuerpo libre o bibliografía; tolerancia justificada>
- Regresión: suite completa, lint, formato y tipado.

## Demo y prueba manual (Windows PowerShell)

```powershell
# Comandos exactos
```

Resultado esperado: <…>
Casos de error a probar: <…>

## Criterios de salida

- [ ] Tests nuevos y regresión en verde; lint, formato y tipado correctos.
- [ ] Demo reproducible en Windows con salida esperada.
- [ ] Casos inválidos rechazados con diagnóstico explícito.
- [ ] `docs/REQUIREMENTS_COVERAGE.md` y `tasks/BACKLOG.md` actualizados.
- [ ] Limitaciones conocidas documentadas.
- [ ] Aceptación expresa del usuario registrada.

## Checklist del usuario (5–10 minutos)

1. <Paso en lenguaje no técnico>

**¿Aceptas esta entrega?**

## Evidencia y cierre

- Comandos ejecutados y resultados: <…>
- Limitaciones conocidas: <…>
- Aceptación: <fecha y referencia>
