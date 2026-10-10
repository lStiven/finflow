# Finflow — Plan de mejora integral UX/UI (resumen)

Versión 3.0 · actualizado el 2026-10-10 contra el código real.
El detalle, los criterios de aceptación y los prompts están en
[FINFLOW_PLAN_UX_V2.md](FINFLOW_PLAN_UX_V2.md).

## Objetivo

Que Finflow se entienda usándolo, no leyéndolo: menos texto permanente,
acciones claras y ayuda solo donde hace falta. Se conserva la identidad visual
y no se toca ninguna regla financiera.

## Dónde estamos

- **Hecho (2026-10-10):** la conexión con el banco (`/conectar`) ya es un
  asistente guiado con bienvenida, cuatro pasos, tutorial visual de Gmail,
  confirmación con evidencia real y una vista de estado para quien ya terminó.
  Probado con `just e2e-connect`. Sirve de referencia de estilo y de piezas
  reutilizables para el resto.
- **Ya existía y no hay que construirlo:** contrato OpenAPI con tipos
  generados, aprobaciones de remitentes por usuario aplicadas en Ingestion,
  estados de cada correo recibido, y el diagnóstico de recepción (correo
  descartado, sin movimiento, sin correos recientes).
- **Auditoría de pantallas (ARC-00) hecha el 2026-10-10:** varias mejoras ya
  existían (alta de cuenta por pasos, desglose y parciales en Reportes,
  sugerencias de tope). Detalle y horas corregidas en la sección 10 del
  documento largo.
- **Bancos y filtro desde el backend (ARC-02 + ARC-04) hechos el 2026-10-10:**
  `known_banks` en `/ingestion/catalog` y `gmail_filter` en `/ingestion/setup`.
  El frontend ya no guarda ningún catálogo de bancos.
- **Textos cortos y kit compartido (UX-01 + UX-02) hechos el 2026-10-10:** cada
  pantalla principal tiene título, una línea y su acción; la explicación vive en
  «Cómo funciona», que se abre sola la primera vez, como el asistente.
- **Transacciones y Presupuestos (UX-07 + UX-10) hechos el 2026-10-10:** «sin
  asignar» y la categoría se resuelven desde el movimiento; el primer tope es
  un número y un toque, y cada tope lleva a sus movimientos.
- **Fase F0–F2 cerrada y APROBADA el 2026-10-10** (sección 11 del documento
  largo): todo en verde y sin bloqueantes. Queda un desborde de Reportes a
  320 px que es anterior a la fase, ya corregido.
- **Cuentas (UX-08) hecho el 2026-10-10:** cada cuenta dice lo que le falta y
  lo resuelve ahí mismo, una sección a la vez, y las cifras explican de qué
  están hechas.
- **Facturas (UX-09) hecho el 2026-10-10:** acciones con nombre detrás de
  «Opciones», ningún error en silencio, y un cobro pagado lleva a su
  movimiento.
- **Resumen, Reportes y Comercios (UX-06, UX-11, UX-12) hechos el
  2026-10-10:** cada cifra, categoría, cuenta y columna abre lo que la forma
  —y la e2e comprueba que suma lo mismo—, y la cola de comercios se responde
  sin salir de la lista. F4 completa.
- **Guías y diagnóstico enlazado (UX-13 + UX-05) hechos el 2026-10-10:** una
  lista vacía dice por qué con evidencia y lleva al arreglo; Guías es una
  lista de tareas que el servidor marca como comprobadas. F5 completa. Queda
  F6 (UX-14, ayuda de avisos).

## Fases

| Fase | Qué | Prioridad |
| --- | --- | --- |
| F0 | Ajustes base: bancos conocidos y filtro de Gmail servidos por el backend | P0 |
| F1 | Textos más cortos y kit visual compartido (a partir del onboarding) | P0 |
| F2 | Transacciones y Presupuestos | P1 |
| F3 | Cuentas | P1 |
| F4 | Resumen, Facturas, Reportes y Comercios | P1 |
| F5 | Guías como centro de tareas y enlaces al diagnóstico | P1 |
| F6 | Avisos y métricas de experiencia, si se justifican | P2 |

**Descartado o diferido:** catálogo de bancos administrable en DynamoDB con
CRUD y auditoría, progreso de guías en DynamoDB, motor de tours atado al DOM.
Las razones están en el documento largo (sección 2).

## Esfuerzo estimado

Lista completa: **82–172 horas-persona**. Primera ola (F0 + F1 + F2):
**31–62 h**. Antes era 236–472 h y luego 125–250 h: bajó porque una parte ya
existía —la auditoría encontró más— y porque se eliminaron piezas
sobredimensionadas para una app de uso privado.

Este plan compite por tiempo con lo que `PROGRESS.md` marca como urgente
(tope al gasto del modelo de lenguaje, publicar `master` en producción).
Conviene intercalarlo, no ponerlo delante.

## Principios obligatorios

1. Reducir el texto visible al mínimo necesario.
2. Enseñar las funcionalidades mientras se usan.
3. No repetir ayudas completadas o descartadas.
4. Mostrar información adicional solo cuando aporte valor.
5. Mantener el diseño visual actual: fondo oscuro, magenta para la acción
   principal, cian informativo, verde para éxito.
6. Reutilizar componentes y servicios existentes.
7. No mostrar estados de éxito que el backend no pueda verificar.
8. No modificar reglas financieras durante cambios de UX.
9. Un ticket a la vez, con su prueba en navegador.
10. Permitir volver a consultar cualquier ayuda.
