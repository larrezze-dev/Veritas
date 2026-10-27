# Contrato de entrega modular: demostrable por el usuario

Cada entrega debe poder instalarse/iniciarse y probarse **aisladamente** con un caso pequeño, sin esperar a que Veritas Structural Calculation 3D esté completo. En módulos sin GUI definitiva, entregar primero un demo CLI más una vista/tabla temporal que pueda ejecutar el usuario.

## Ficha obligatoria

- ID y nombre de módulo; IDs SRS cubiertos; dependencias y exclusiones.
- Entradas/salidas con tipos, unidades y validadores.
- Archivos añadidos/tocados; API pública; formato persistido.
- Demo reproducible (comando y datos de ejemplo); resultados esperados.
- Test unitario y prueba de integración; cómo comparar con referencia.
- Instrucciones **Windows PowerShell** exactas, no comandos Bash como única opción.
- Captura o ejemplo de salida cuando ayude, log sin secretos.
- Limitaciones conocidas, casos rechazados y advertencias de seguridad.
- Checklist de usuario en lenguaje no técnico y una pregunta «¿aceptas esta entrega?».

## Proceso por ticket

1. **Proponer:** alcance, módulo, dependencias, tests, cambios y riesgos. Guardar el plan en `plans/<ID-TICKET>.md` con estado `PROPUESTO`.
2. **Aprobar:** el usuario confirma antes de cambios relevantes; registrar en el plan `APROBADO` con fecha. Cambios posteriores de alcance, contratos o dependencias se anotan como revisión pendiente y esperan nueva confirmación.
3. **Implementar:** contrato tipado y prueba mínima primero; cambios localizados.
4. **Verificar:** pytest, lint, tipado y validación numérica; no esconder errores.
5. **Demostrar:** demo ejecutable, receta de prueba manual y resultados.
6. **Aceptar:** parar y esperar feedback. Si falla, corregir el mismo ticket.
7. **Cerrar:** actualizar changelog, [REQUIREMENTS_COVERAGE.md](REQUIREMENTS_COVERAGE.md), [ROADMAP.md](ROADMAP.md) si cierra fase, `tasks/BACKLOG.md` y handoff.

## Definición de terminado

Un módulo no está terminado por tener GUI bonita. Debe manejar caso correcto, caso inválido, guardar/reabrir si procede, presentar unidades, no contaminar módulos vecinos y permitir regresión automatizada. El usuario puede probarlo incluso si hay features futuras sin implementar: deben mostrarse como «pendiente» y no simular funcionamiento.

## Pruebas durante desarrollo

Para tareas de más de un día, entregar un estado intermedio seguro con demo y test al menos por cada contrato estable. No dejar un módulo roto esperando una fase posterior para detectar errores. El orquestador acuerda con el usuario los puntos de prueba sin interrumpir innecesariamente una operación atómica.
