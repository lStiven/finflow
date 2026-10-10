# Finflow — Plan de mejora UX/UI y arquitectura frontend–backend

**Versión:** 3.0 (2026-10-10)
**Estado:** plan ejecutable, contrastado con el código del repositorio
**Cambios frente a 2.0:** el onboarding de correo ya está entregado; se
eliminaron las piezas que ya existían o que estaban sobredimensionadas para la
escala real de Finflow; los prompts se ajustaron para consumir menos contexto.

> **Regla principal:** el backend es la fuente de verdad de bancos,
> remitentes, aprobaciones, direcciones de reenvío, estados de procesamiento y
> finanzas. El frontend interpreta esos datos y los vuelve comprensibles. Las
> ayudas enseñan haciendo, no con párrafos.

## 0. Alcance y punto de partida

### Escala que manda en cada decisión

Finflow es un despliegue privado para su autor y unas pocas personas
(`CLAUDE.md`): AWS en capa gratuita, sin multi-tenencia. Toda propuesta se mide
contra eso. Una pieza pensada para un producto con equipos de operación —CRUD
administrativo, auditoría de catálogo, CMS— cuesta más de lo que devuelve aquí.

### Ya entregado: onboarding de conexión con el banco (antes UX-04)

Entregado el 2026-10-10 en `/conectar`, sin commit aún. Ya **no** hay trabajo
en paralelo ni archivos reservados. Este plan puede tocar esos archivos, con
una condición: `just e2e-connect` debe seguir pasando.

| Qué quedó | Dónde |
| --- | --- |
| Pantalla y orquestación (bienvenida, 4 pasos, estado de la conexión) | `frontend/src/routes/conectar.tsx` |
| Lógica pura con pruebas: pasos, bancos, evidencia, salud de la conexión | `frontend/src/onboarding/{steps,banks,activity,progress}.ts` |
| Componentes del flujo | `frontend/src/onboarding/*.tsx` |
| Diálogos y aviso globales | `frontend/src/components/{WelcomeDialog,ReadyDialog,OnboardingNudge}.tsx` |
| Prueba en navegador de punta a punta | `frontend/scripts/e2e-connect.mjs` + `scripts/e2e_connect_fixture.py` |

### Lo que ya existía y este plan daba por construir

| Pieza del plan 2.0 | Realidad en el código |
| --- | --- |
| ARC-01, contratos y cliente tipado | Existe: OpenAPI generado desde FastAPI (`docs/openapi.json`), tipos en `frontend/src/api/schema.d.ts` (`just web-types`), cliente `openapi-fetch`, consultas en `frontend/src/api/queries.ts`. |
| ARC-03, aprobaciones por usuario | Existe: `allowed_domains` y `allowed_addresses` por usuario, aplicadas en Ingestion (`AuthorizedSenderPolicy`). `PATCH /identity/inbox` reemplaza la lista completa. |
| UX-05, diagnóstico de recepción | Mayormente hecho en el estado de la conexión: correo descartado por remitente, correo sin movimiento, correo leyéndose, sin correos en 14 días, últimos correos con su resultado y "Es de mi banco". |
| UX-02, kit visual | La mitad existe en `frontend/src/onboarding/` (ver sección 6). |

### Qué incluye este plan

1. Quitar del frontend el único catálogo fijo que queda (bancos conocidos) y el
   armado del filtro de Gmail.
2. Textos más cortos y componentes compartidos promovidos desde el onboarding.
3. Mejoras de Resumen, Transacciones, Cuentas, Facturas, Presupuestos,
   Reportes, Comercios y Guías.
4. Accesibilidad, responsive y una prueba en navegador por ticket.

### Reglas de ejecución

- Un flujo vertical completo por ticket: API si hace falta → tipos → vista →
  pruebas unitarias → e2e en navegador.
- Revisar la implementación real antes de crear tablas, endpoints o
  componentes.
- No cambiar reglas financieras a través de la interfaz.
- No introducir bancos, dominios ni direcciones fijas en el frontend. Los
  enlaces a pantallas de Gmail (`frontend/src/onboarding/gmail.ts`) son
  navegación, no catálogo: se quedan en el frontend.
- Si un ticket cambia un endpoint: `just web-types` y actualizar la colección
  de Postman en el mismo cambio (`CLAUDE.md`).

### Relación con los otros planes

El plan de producto vive en los artefactos "De aquí a competir" y "Finflow
frente al mercado" (`.claude/rules/roadmap-artifacts.md`). Este documento es
solo el plan de ejecución de la mejora de experiencia. Si un ticket cierra algo
que el plan de construcción describe, se marca allí en el mismo turno.

Lo urgente de `PROGRESS.md` (tope al gasto del modelo de lenguaje, publicar
`master` en producción, disparador diario de los créditos) va antes o
intercalado: cuesta dinero o bloquea a usuarios reales.

---

## 1. Diagnóstico por pantalla

| Vista | Hallazgo | Experiencia esperada |
| --- | --- | --- |
| Resumen | Indicadores útiles que no explican cambios ni llevan a la causa. | Tarjetas con ayuda corta y navegación a movimientos filtrados. |
| Transacciones | Lista clara; el detalle usa lenguaje financiero y acciones separadas. | Acciones rápidas, explicación de «sin asignar», edición accesible. |
| Cuentas | Patrimonio, activos, deudas y vinculaciones exigen interpretación. | Explicación bajo demanda y alta guiada según el tipo de cuenta. |
| Facturas | Introducción extensa e iconos ambiguos. | Estados y acciones legibles; el impacto contable, solo cuando importa. |
| Presupuestos | Estado vacío poco motivador y explicación larga arriba. | Primer presupuesto guiado y acceso directo desde categorías con gasto. |
| Reportes | Gráficos informativos, exploración limitada. | Desglose navegable, periodos comparables y parciales señalados. |
| Comercios | Texto largo para explicar agrupación y categorías. | Bandeja de sugerencias revisables y acciones en contexto. |
| Guías | Biblioteca estática de lecturas. | Centro de tareas: continuar, qué quiero hacer, resolver un problema. |
| Conexión de correo | **Hecho.** | Referencia de estilo y de componentes para el resto. |

### Principios de diseño visual

Fondo oscuro, navegación lateral (barra inferior en móvil), magenta como acción
principal, cian informativo, verde para éxito o dinero que entra, ámbar para
atención. Tokens en `frontend/src/index.css`; no introducir paleta nueva.
Jerarquía clara, menos texto permanente, foco visible, objetivos táctiles de
44 px, animaciones discretas que respeten `prefers-reduced-motion`. La regla
global de movimiento reducido ya está en `index.css`.

---

## 2. Decisiones de arquitectura

### 2.1. Propiedad de los datos

| Elemento | Dueño | Implementación | Frontend |
| --- | --- | --- | --- |
| Bancos conocidos (con parser propio) | Backend | El registro de parsers en código (`ingestion/domain/parsing/registry.py`, `BANK_DOMAINS`), publicado por `GET /ingestion/catalog` | Leer y dibujar; borrar `KNOWN_BANKS` |
| Remitentes aprobados por usuario | Backend | Ya existe: inbox del usuario en DynamoDB, aplicado en Ingestion | Aprobar o quitar vía `PATCH /identity/inbox`; mostrar lo confirmado |
| Dirección de reenvío | Backend | Ya existe: alias derivado del `user_id`; no cambiarlo | Mostrar lo que da la API; nunca calcularlo |
| Texto del filtro de Gmail | Backend | Campo nuevo en `GET /ingestion/setup` | Copiar y mostrar; borrar `gmailFromFilter` |
| Verificación, recepción y procesamiento | Backend | Ya existe: `/ingestion/setup` y `/ingestion/notifications` | Mostrar sin inventar éxitos |
| Cuentas, movimientos, saldos, facturas, presupuestos | Backend | Dominios actuales | Presentación y captura de acciones |
| Progreso de ayudas ("ya lo vi", "lo descarté") | Navegador | `localStorage` por usuario, como `onboarding/progress.ts` | Lo verificable siempre sale del servidor |
| Textos, layout, animaciones, componentes | Frontend | Código versionado y tokens | Mantener en frontend |

### 2.2. Por qué no un catálogo de bancos administrable en DynamoDB

En Finflow, "banco conocido" significa **tiene un parser determinista**, y los
parsers son código Python. Dar de alta un banco en DynamoDB sin desplegar no le
da parser: solo pone un nombre en una tarjeta, y abre la puerta a que catálogo y
parsers se contradigan. Un banco sin parser ya funciona hoy como «Otro banco»:
la persona aprueba el remitente y el modelo de lenguaje interpreta la alerta.

Por eso la fuente de verdad del catálogo es el registro de parsers, y el
backend solo tiene que **publicarlo**. Se descartan el CRUD administrativo, la
auditoría de catálogo, el versionado y las bajas lógicas. Si algún día hay
bancos administrables sin parser (por ejemplo, para ofrecer tarjetas con logo),
se reabre como ticket propio.

### 2.3. Por qué el progreso de ayudas se queda en el navegador

Todo lo que el servidor puede comprobar —remitentes aprobados, confirmación de
Gmail, primer correo, movimientos— ya se sincroniza entre dispositivos porque
sale de la API. Lo que queda en el navegador son afirmaciones que nadie puede
verificar ("leí esto", "creé el filtro"). Guardarlas junto a hechos verificados
mezclaría las dos cosas. El costo, aceptado: en otro navegador se repite una
ayuda, nunca un paso real. Si eso llegara a molestar, se reabre como ticket P2.

### 2.4. Catálogo global y aprobaciones del usuario son cosas distintas

Que un banco aparezca como conocido **no aprueba sus correos para nadie**. Cada
persona aprueba los suyos y la ingesta lo aplica en el servidor. Las reglas ya
vigentes, que se mantienen:

- Una lista vacía no acepta nada.
- Un dominio se compara exacto: `bancolombia.com.co` no cubre subdominios.
- El frontend no deja aprobar como dominio un correo personal (`gmail.com`,
  `hotmail.com`…); una dirección exacta en esos dominios sí.
- "Es de mi banco" aprueba la dirección exacta, nunca el dominio.
- Riesgo residual conocido: un correo reenviado no trae SPF/DKIM útiles
  (`docs/email-forwarding.md`). La interfaz no lo resuelve y no debe aparentar
  que lo hace.

### 2.5. Contratos a crear (los únicos de este plan)

| Capacidad | Contrato | Consumidor |
| --- | --- | --- |
| Bancos conocidos | Campo `known_banks` en `GET /ingestion/catalog`: `id`, `name`, `domains`, derivados de `BANK_DOMAINS` | Paso "Elige tus bancos", nombres en el estado de la conexión |
| Filtro de Gmail | Campo `gmail_filter` en `GET /ingestion/setup`, armado desde los remitentes aprobados | Paso "Reenvía solo tus alertas", aviso de filtro desactualizado |

El resto de las capacidades del plan 2.0 (`/me/forwarding`,
`/me/sender-approvals`, `/me/guides`) ya existen con otro nombre o se
descartan. Cada consumidor nuevo usa el cliente y las consultas de
`queries.ts`; no se duplican tipos a mano.

### 2.6. Qué se queda en el frontend

Componentes, rutas, iconos, tokens, animaciones, textos de ayuda genéricos,
enlaces a pantallas de Gmail y el estado efímero de la interfaz. No se
construye una interfaz generada desde registros.

---

## 3. Backlog revisado

**Tamaño:** S, M, L. **Urgencia:** P0 base, P1 siguiente ola, P2 evolutiva.
**Horas:** esfuerzo humano estimado.

| ID | Iniciativa | Resultado | Tamaño | Horas | Urgencia | Estado |
| --- | --- | --- | --- | ---: | --- | --- |
| UX-04 | Onboarding de conexión | Asistente guiado y estado de la conexión | — | — | — | **Hecho** |
| ARC-00 | Auditoría de las demás pantallas | Inventario corto por pantalla, sin documentos extra | S | 3–6 | P0 | **Hecho** (sección 10) |
| ARC-01 | Contratos y cliente tipado | — | — | — | — | **Ya existía** |
| ARC-02 | Bancos conocidos desde el backend | `known_banks` en `/ingestion/catalog`; borrar `KNOWN_BANKS` | S | 3–6 | P0 | **Hecho** (2026-10-10) |
| ARC-03 | Aprobaciones por usuario | — | — | — | — | **Ya existía** |
| ARC-04 | Filtro de Gmail en el backend | `gmail_filter` en `/ingestion/setup`; borrar `gmailFromFilter` | S | 2–4 | P0 | **Hecho** (2026-10-10) |
| ARC-05 | Progreso de ayudas en DynamoDB | — | — | — | — | **Descartado** (2.3) |
| UX-01 | Textos cortos | Cabeceras breves, detalle bajo demanda, en todas las vistas | M | 6–12 | P0 | **Hecho** en las 8 pantallas principales (2026-10-10); las de detalle van con su ticket |
| UX-02 | Kit visual compartido | Promover las piezas del onboarding a `components/ui/` | S | 4–8 | P0 | **Hecho** la base (2026-10-10); el resto se promueve en su segundo uso |
| UX-03 | Motor de tours atado al DOM | — | — | — | — | **Descartado**: se rompe con cada cambio de maquetación; cada vista lleva su ayuda en contexto |
| UX-05 | Diagnóstico enlazado | Llevar el diagnóstico existente a Transacciones y Resumen | S | 3–6 | P1 | **Hecho** (2026-10-10) |
| UX-06 | Resumen accionable | Métricas con ayuda corta y navegación a desglose filtrado | M | 6–12 | P1 | **Hecho** (2026-10-10) |
| UX-07 | Transacciones y detalle | Acciones rápidas, «sin asignar», edición y traslados claros | M | 10–20 | P1 | **Hecho** (2026-10-10) |
| UX-08 | Cuentas e instrumentos | Tarjeta menos densa, vinculación, patrimonio y deuda explicados (el alta por tipo ya existe) | M | 10–20 | P1 | **Hecho** (2026-10-10) |
| UX-09 | Facturas | Previsto, pagado y vencido legibles; impacto contable bajo demanda | M | 8–16 | P1 | **Hecho** (2026-10-10) |
| UX-10 | Presupuestos | Estado vacío guiado (las sugerencias desde el gasto ya existen) | S | 6–12 | P1 | **Hecho** (2026-10-10) |
| UX-11 | Reportes | Columnas navegables (el resto del desglose y los parciales ya existen) | S | 4–8 | P1 | **Hecho** (2026-10-10) |
| UX-12 | Comercios y categorías | Cola de revisión y acciones en contexto; sugerir fusiones queda fuera (sin contrato) | M | 6–12 | P1 | **Hecho** (2026-10-10) |
| UX-13 | Centro de Guías | Continuar, qué quiero hacer, resolver un problema | M | 6–12 | P1 | **Hecho** (2026-10-10) |
| UX-14 | Ayuda de avisos | La conexión con Telegram ya existe (Perfil, `/guias/avisos`); solo acortar y guiar | S | 4–8 | P2 | Pendiente |
| UX-15 | Analítica de experiencia | — | — | — | — | **Diferido**: no hay uso suficiente para medir |
| QA-16 | Accesibilidad, móvil y regresión | Una e2e por ticket; `just e2e` completo antes de cerrar cada fase | — | 8–16 | Transversal | Continuo |

**Total pendiente: 82–172 horas-persona** (corregido por la auditoría, sección 10.3).
Primera ola (ARC-02/04, UX-01/02, UX-07, UX-10): **31–62 h**.

---

## 4. Criterios de aceptación

### ARC-02 y ARC-04 — Sin catálogo fijo en el frontend

**Cerrado el 2026-10-10.**

- [x] `frontend/src` no contiene dominios ni nombres de banco como catálogo; las
      tarjetas salen de `known_banks` (quedan nombres solo en comentarios,
      placeholders y textos de ejemplo).
- [x] `known_banks` se deriva de `BANK_DOMAINS`; agregar un parser con su
      dominio lo hace aparecer en la pantalla sin tocar el frontend. El nombre
      visible sale de `BANK_DISPLAY_NAMES` y, si falta, del nombre del parser.
- [x] Un banco aparece "elegido" solo si **todos** sus dominios están
      aprobados; con algunos, "incompleto".
- [x] El filtro mostrado es el `gmail_filter` del servidor, con el mismo formato
      que hoy (`@dominio OR dirección`), que es el verificado en Gmail.
- [x] Si `/ingestion/catalog` falla, la pantalla muestra un error recuperable,
      nunca una lista fija de respaldo (el loader de `/conectar` lo exige y cae
      en la pantalla de error con reintento).
- [x] Pruebas de backend del armado del filtro y del catálogo; `just e2e-connect`
      sigue pasando; Postman actualizado.

Lo que la implementación agregó y el plan no decía:

- `id` de un banco conocido es el `BANK_NAME` del parser, lo mismo que se
  escribe en el `bank` de cada movimiento: así el frontend nombra el banco de
  un movimiento sin un segundo campo (`parserName` desapareció).
- El setup devuelve también `gmail_filter_terms`, porque el aviso de «filtro
  desactualizado» compara términos, no el texto.
- Guardar remitentes no termina hasta que el setup vuelve con el filtro nuevo,
  y el inbox se lee con `ConsistentRead`: si no, se podía copiar a Gmail un
  filtro sin el banco recién elegido.
- `MovementCard` vive también en un diálogo global y no suspende: sin catálogo,
  muestra el banco como lo escribe el parser.

### UX-01 y UX-02 — Base visual

**Cerrado el 2026-10-10** para Resumen, Transacciones, Cuentas, Facturas,
Presupuestos, Reportes, Comercios y Guías.

- [x] Cabecera de una o dos líneas y una acción principal por pantalla.
- [x] Componentes compartidos con teclado, foco visible, lector de pantalla y
      móvil; sin librerías nuevas.
- [x] Ninguna ayuda reemplaza el estado real de una cuenta o integración.

Cómo quedó, para que el resto de tickets lo reutilice:

- **`components/PageHeader.tsx`**: título, una línea y la acción principal.
  El párrafo que antes iba debajo vive en **«Cómo funciona»**: tres o cuatro
  puntos con icono que entran escalonados, como en el asistente de conexión.
  Se abre solo al entrar hasta que se cierra —como la bienvenida del
  asistente, que espera a que la reconozcan— y, al cerrarlo («Entendido» o la
  X), se recuerda por cuenta en el navegador (`lib/help.ts`, mismo criterio que
  `onboarding/progress.ts`); el botón de la cabecera lo vuelve a abrir. No se
  abre solo donde otra cosa ya recibe la primera visita: Resumen (diálogo de
  bienvenida), Cuentas sin cuentas y Comercios sin comercios (su tarjeta de
  primer uso).
- **Promovidos a `components/ui/`**: `Notice` y `SuccessMark`. Los enlaces con
  aspecto de botón de las cabeceras usan `buttonClass`.
- **Quedan para su ticket**: las cabeceras de las pantallas de detalle
  (financiación de una cuenta, alta de cuenta, nuevo movimiento, detalle de un
  comercio) y los textos dentro de tarjetas (nota de la previsión en Facturas,
  aviso de crédito vigilado repetido en Cuentas).

### UX-05 — Diagnóstico enlazado

- [x] Desde Transacciones vacías o Resumen sin movimientos se llega al estado de
      la conexión, que ya distingue los casos con evidencia.
- [x] Lo que no se puede observar se dice como tal ("no podemos confirmarlo").

**Cerrado el 2026-10-10** (`just e2e-guias`): Transacciones vacía (sin
filtros) y los movimientos recientes del Resumen dicen el motivo con
evidencia —sin banco elegido, sin confirmar Gmail, confirmada sin correos,
correos descartados por su remitente (lo nombra), alertas ilegibles, días sin
nada— y llevan al paso que lo arregla (`onboarding/diagnosis.ts`). Del filtro
de Gmail dice que no se puede ver desde aquí. Sin motivo que dar, el texto de
siempre.

### UX-06 a UX-13

- **Resumen:** cada tarjeta lleva a su detalle real; las comparaciones dicen el
  periodo.
  **Cerrado el 2026-10-10** (`just e2e-resumen`): Patrimonio y Deuda llevan a
  Cuentas; cada categoría del anillo abre sus gastos del mes, y la e2e
  comprueba contra la API que esas filas suman lo que dibuja el anillo; cada
  cuenta abre sus movimientos; la comparación dice el mes «a esta altura»,
  porque se mide hasta el mismo día. Al enlazar las cuentas reapareció la
  lección de la rejilla (`grid-cols-1`) a 320 px, y se aplicó.
- **Transacciones:** origen automático o manual, «sin asignar», edición y
  borrado en lenguaje claro; un traslado nunca se presenta como ingreso o gasto.
  **Cerrado el 2026-10-10** (`just e2e-transacciones`): en el detalle,
  «sin asignar» es un aviso con la acción — asignar a una cuenta abierta de la
  misma moneda, que son las únicas que la API acepta — y, si vino de una
  alerta, el enlace para enlazar su tarjeta; la categoría se cambia ahí mismo
  y el aviso dice que es del comercio; la fila «Estado», que repetía «Cuenta»,
  se fue. La lista explica «sin asignar» al filtrar por él. El aviso de éxito
  espera a que el movimiento se relea, para no contradecir la fila de abajo.
- **Cuentas:** formulario según el tipo que soporta el backend; patrimonio =
  activos − pasivos; préstamos e hipotecas siguen fuera de los totales.
  **Cerrado el 2026-10-10** (`just e2e-cuentas`): cada tarjeta dice primero
  lo que le falta (`accounts/attention.ts`) — una cuenta que recibe alertas
  sin nada enlazado, o un crédito o inversión sin condiciones — con la acción
  ahí mismo; los tres paneles apilados son una fila de secciones, una abierta
  a la vez; el aviso de crédito vigilado sale una vez, en una línea; cada cifra
  de patrimonio dice de qué está hecha y «Debes» nombra los créditos que deja
  fuera (préstamos e hipotecas son siempre vigilados, así que «Debes» son las
  tarjetas); la barra del cupo se lee con lector de pantalla; la pestaña
  (`?ver=`) y el paso del alta (`?paso=`) van en la URL; crear una cuenta
  celebra con la marca del asistente; Financiación tiene cabecera corta y su
  «Cómo funciona». La e2e usa una persona recién registrada porque una cuenta
  no se puede borrar.
- **Facturas:** previsto, confirmado y vencido según las reglas reales;
  declarar no mueve saldos y la pantalla lo dice.
  **Cerrado el 2026-10-10** (`just e2e-bills`, ampliada): las cuatro acciones
  de cada factura, que eran iconos de 32 px cuyo significado vivía en un
  `title` que el teléfono no muestra, son palabras de 44 px detrás de
  «Opciones», con el nombre de la factura a la vista; pausar, borrar y cobrar
  sola ya no fallan en silencio, y todo error de la pantalla se anuncia; las
  acciones de cada cobro miden 44 px y uno pagado lleva a su movimiento; los
  cuatro párrafos largos quedan en una línea. Un cobro previsto sigue sin
  etiqueta, a propósito: es la decisión que defiende `bills/schedule.test.ts`
  («ponerle una a todo esconde las dos que importan»).
- **Presupuestos:** crear el primer tope en pocos pasos; categorías sugeridas
  salen de movimientos reales; un tope no bloquea nada.
  **Cerrado el 2026-10-10** (`just e2e-budgets`, ampliada): sin topes, la
  pantalla pide un solo número y pone uno sobre todo el mes; el mes va en la
  URL (`?mes=`); cada tope lleva a sus movimientos cuando la lista puede
  reproducir su cifra — una categoría y una cuenta como mucho —, y la e2e
  comprueba que esas filas suman lo gastado; quitar un tope muestra su error;
  las acciones miden 44 px.
- **Reportes:** los gráficos llevan a sus movimientos; periodos parciales
  señalados.
  **Cerrado el 2026-10-10** (`just e2e-reportes`): elegir una columna del
  flujo de caja o del gasto por categoría ofrece «Ver los movimientos de …»
  debajo, en dos pasos y no navegando al tocar, porque en el teléfono ese
  mismo toque es el que muestra las cifras. La e2e captura la serie tal como
  la recibió la página y comprueba que la lista suma la columna. «Balance»
  abre las dos direcciones. El día de la semana no enlaza: la lista no filtra
  por día.
- **Comercios:** nada se agrupa solo; las sugerencias se confirman.
  **Cerrado el 2026-10-10** (`just e2e-comercios`): cada comercio sin revisar
  ofrece las dos respuestas en palabras de 44 px, en la lista —«Está bien» y
  «Otra categoría», que cambia la categoría ahí mismo—, y un fallo se dice. El
  selector de la fila se nombra con el comercio: la pantalla ya tenía otro
  control «Categoría» (el filtro) y dos con un nombre son uno para un lector.
  La e2e registra una persona y le llegan dos alertas por el webhook local,
  porque revisar no se deshace y la cola del seed se agotaría.
- **Guías:** cada tarjeta lleva a una acción existente; la de conexión lleva a
  `/conectar`.
  **Cerrado el 2026-10-10** (`just e2e-guias`): tres bloques —«Para que
  Finflow trabaje solo», «¿Qué quieres hacer?», «Resolver un problema»— y las
  lecturas debajo. Lo pendiente lo responde el servidor (conexión, cuentas,
  presupuestos, Telegram) y lo hecho se rotula «Comprobado», la regla del
  asistente; cada tarea es un enlace a donde se hace, y la e2e abre cada uno.
  La tarjeta violeta que pintaba su icono en magenta desapareció con el
  rediseño.

---

## 5. Orden de ejecución

| Fase | Tickets | Qué se entrega | Control |
| --- | --- | --- | --- |
| F0 | ARC-00, ARC-02, ARC-04 | Inventario corto; bancos y filtro desde el backend | `just prepare`, `just web-check`, `just e2e-connect` |
| F1 | UX-01, UX-02 | Textos cortos y kit compartido | `just e2e-views` |
| F2 | UX-07, UX-10 | Transacciones y Presupuestos | e2e del ticket + `just e2e` |
| F3 | UX-08 | Cuentas | e2e del ticket + `just e2e` |
| F4 | UX-06, UX-09, UX-11, UX-12 | Resumen, Facturas, Reportes, Comercios | Uno por sesión |
| F5 | UX-05, UX-13 | Diagnóstico enlazado y centro de guías | `just e2e` |
| F6 | UX-14 | Ayuda de avisos | — |

### Criterio para iniciar un ticket

1. Su contrato API y el dueño de los datos están identificados.
2. Tiene criterios de aceptación y una prueba en navegador concreta.
3. Se conoce la acción del usuario que mejora.
4. Se puede entregar y revisar sola.

---

## 6. Estrategia de frontend

### Convenciones reales del repositorio (no reorganizar)

- `frontend/src/routes/` — pantallas (TanStack Router, rutas por archivo).
- `frontend/src/<feature>/` — lógica pura con su `*.test.ts` y, a veces,
  componentes del feature (`bills/`, `budgets/`, `merchants/`, `onboarding/`).
- `frontend/src/components/` y `components/ui/` — piezas compartidas.
- `frontend/src/api/queries.ts` — todas las lecturas y escrituras; tipos de
  `schema.d.ts`, que es generado (no se edita a mano).
- Pruebas: Vitest sobre lógica pura (sin DOM) y Playwright en
  `frontend/scripts/e2e-*.mjs`, cada una con su receta `just e2e-*`.

### Kit compartido: promover, no reconstruir (UX-02)

Ya construido en el onboarding y probado. Se mueve a `components/ui/` cuando
aparezca su segundo uso:

| Pieza | Archivo actual | Uso |
| --- | --- | --- |
| `Notice` | `onboarding/parts.tsx` | Aviso informativo, de atención o de éxito |
| `ExternalButton`, `buttonClass` | `onboarding/parts.tsx`, `components/ui/Button.tsx` | Enlaces con aspecto de botón |
| `StepHeading` | `onboarding/parts.tsx` | Título que recibe el foco al cambiar de paso |
| `SuccessMark`, `WaitingDot` | `onboarding/parts.tsx` | Éxito con animación; espera pasiva |
| `CopyField`, `useCopy` | `onboarding/CopyField.tsx` | Copiar con confirmación y respaldo manual |
| `GmailTutorial` | `onboarding/GmailTutorial.tsx` | Carrusel de pasos con teclado y gesto |
| `Stepper` | `onboarding/Stepper.tsx` | Progreso por pasos |
| `MovementCard` | `onboarding/MovementCard.tsx` | Un movimiento como tarjeta |
| `useMediaQuery`, `useNow` | `lib/` | Consultas de medios; tiempo que avanza |
| Animaciones | `index.css` | `step-in-*`, `pop`, `spotlight`, `burst`, `shimmer`, `draw-check` |

### Patrón de consumo de bancos tras ARC-02

```typescript
// Dentro de /conectar el loader ya lo trajo; useKnownBanks() suspende sobre él.
const banks = useKnownBanks(); // known_banks de /ingestion/catalog
const { senders, save } = useSenders(); // lo aprobado por esta persona
// "elegido" solo cuando el PATCH respondió y todos los dominios están en la lista
```

### Lecciones del onboarding que aplican a todas las vistas

- El paso o la pestaña visible va en la URL (`?paso=N`), así el botón atrás
  funciona y la pantalla no salta cuando el servidor responde.
- Una rejilla en móvil necesita `grid-cols-1` explícito; si no, un texto
  truncado la ensancha y la página se desborda.
- Nada aparece "aprobado" o "listo" antes de la respuesta del servidor.
- Separar lo que el servidor comprueba de lo que la persona dice, y rotularlo
  distinto ("Comprobado" frente a "Lo marcaste tú").

---

## 7. Prompts para Claude Code

**Para todos:** sin subagentes. Una revisión multiagente lee el diff completo
una vez por agente; en un cambio grande agota el límite de la sesión sin
terminar. Si un ticket necesita revisión, se hace directa sobre los archivos de
riesgo, y si algo se corta por límite, no se relanza igual.

### Prompt A — Auditoría corta (ARC-00), una sola vez

```text
Lee front_refactor/FINFLOW_PLAN_UX_V2.md (secciones 0, 1 y 3) y PROGRESS.md.
Ejecuta solo ARC-00. No escribas código ni abras subagentes.

Para Resumen, Transacciones, Cuentas, Facturas, Presupuestos, Reportes,
Comercios y Guías, lee solo el archivo de la ruta en frontend/src/routes y lo
que importe directamente. Para cada pantalla anota en una tabla:
- textos permanentes que sobran o pueden ir bajo demanda;
- la acción principal y si se ve;
- estados vacío / carga / error que falten;
- APIs que consume (de api/queries.ts);
- piezas del kit (sección 6) que podría usar.

Entrega: una sección nueva "Auditoría por pantalla" al final de
front_refactor/FINFLOW_PLAN_UX_V2.md, y corrige ahí las horas del backlog si
la auditoría lo justifica. Nada de documentos adicionales.
```

### Prompt B — ARC-02 + ARC-04 (bancos y filtro desde el backend)

```text
Implementa ARC-02 y ARC-04 de front_refactor/FINFLOW_PLAN_UX_V2.md
(secciones 2.2, 2.5 y 4). Sin subagentes.

Lee solo: ingestion/domain/parsing/registry.py, ingestion/presentation/http/
notifications.py (catálogo) y setup.py, frontend/src/onboarding/banks.ts,
frontend/src/lib/forwarding.ts y sus pruebas.

1. Backend: `known_banks` en GET /ingestion/catalog derivado de BANK_DOMAINS
   (id estable, nombre visible, dominios); `gmail_filter` en GET
   /ingestion/setup con el formato actual (`@dominio OR dirección`). Pruebas.
2. `just web-types`; actualizar docs/postman según docs/postman/README.md.
3. Frontend: crear ingestionCatalogQuery en api/queries.ts (hoy ninguna
   pantalla lee /ingestion/catalog) y leer gmail_filter de setupQuery; borrar
   KNOWN_BANKS y gmailFromFilter; error recuperable si el catálogo no
   responde.
4. Validar: just prepare, just web-check, just up + just web, just e2e-connect.
5. Actualizar PROGRESS.md y el estado del ticket en este plan. No hagas commit.
```

### Prompt C — Un ticket de UX (repetir por ticket)

```text
Ticket: UX-XX de front_refactor/FINFLOW_PLAN_UX_V2.md. Sin subagentes.
Lee la fila del ticket (sección 3), sus criterios (sección 4), la auditoría de
su pantalla y solo los archivos de esa pantalla.

Reglas: datos de negocio desde el backend; reutiliza el kit de la sección 6;
no cambies cálculos ni reglas financieras; no inventes estados; tema oscuro y
tokens actuales; sin librerías nuevas; nada de commits.

1. En dos líneas: la fricción actual y la acción que mejora.
2. Diseño: estados vacío/carga/error/éxito, móvil, teclado y lector.
3. Implementa solo ese ticket, con pruebas de la lógica nueva.
4. Escribe frontend/scripts/e2e-<pantalla>.mjs con su receta just, siguiendo
   e2e-connect.mjs: pantalla comparada con la API, sin desborde lateral, sin
   errores.
5. Valida: just web-check, la e2e del ticket y just e2e-views.
6. Actualiza el estado del ticket en este plan y PROGRESS.md.

Responde: qué cambió y por qué mejora, archivos, contratos usados, pruebas
ejecutadas con su resultado, y lo que queda abierto.
```

### Prompt D — Cierre de fase

```text
Revisa la fase F-N de front_refactor/FINFLOW_PLAN_UX_V2.md sin iniciar nada
nuevo y sin subagentes.
Corre just web-check, just prepare (si hubo Python) y just e2e completo.
Comprueba con grep que no hay catálogos de bancos ni dominios en frontend/src.
Clasifica lo que encuentres en BLOQUEANTE / IMPORTANTE / MEJORA, corrige solo
lo bloqueante dentro de la fase, y da veredicto APROBADA / NO APROBADA con la
salida real de las pruebas.
```

---

## 8. Definición de terminado

- [ ] El dato de negocio tiene una sola fuente de verdad y un contrato.
- [ ] No hay bancos, dominios ni direcciones fijas en el frontend.
- [ ] No hay aprobación implícita de remitentes ni acceso a datos de otra
      persona.
- [ ] La mejora reduce texto y deja una acción principal clara.
- [ ] Cubre vacío, carga, error, éxito y recuperación cuando aplica.
- [ ] Funciona con teclado, lector de pantalla, móvil (320–430 px) y movimiento
      reducido.
- [ ] Respeta las reglas financieras y no presenta una ayuda como un éxito
      bancario.
- [ ] Tiene su e2e en navegador; `just web-check` y `just e2e` pasan, con la
      salida real.
- [ ] `PROGRESS.md` y el estado del ticket en este plan están al día.

## 9. Por dónde empezar

1. ~~Prompt A: auditoría corta de las demás pantallas.~~ Hecho el 2026-10-10 (sección 10).
2. ~~Prompt B: ARC-02 + ARC-04, el único cambio de backend del plan.~~ Hecho el 2026-10-10.
3. ~~Prompt C con UX-01 y UX-02, aplicados primero a una pantalla real.~~ Hecho el 2026-10-10 en las ocho pantallas principales.
4. ~~Prompt C con UX-07 (Transacciones) y UX-10 (Presupuestos).~~ Hecho el 2026-10-10.
5. Prompt D para cerrar la fase.
6. ~~Seguir con Cuentas, después Resumen, Facturas, Reportes y Comercios, y
   cerrar con Guías y el diagnóstico enlazado.~~ Hecho el 2026-10-10. Queda
   F6 (UX-14).

---

## 10. Auditoría por pantalla (ARC-00, 2026-10-10)

Leída sobre el código, no sobre capturas: cada ruta de `frontend/src/routes`
y lo que importa directamente. **Cambia el plan en un punto central:** varias
mejoras que la sección 1 daba por construir ya existen (alta de cuenta por
pasos, desglose navegable y periodos parciales en Reportes, sugerencias de
tope desde el gasto real). Las horas de la sección 3 se corrigieron con esto.

### 10.1. Tabla

| Pantalla | Texto permanente que sobra o va bajo demanda | Acción principal | Estados que faltan | APIs (`api/queries.ts`) | Kit (sección 6) |
| --- | --- | --- | --- | --- | --- |
| **Resumen** `routes/index.tsx` | Subtítulo genérico («Aquí tienes un resumen…»); el párrafo de «sin cuentas» repite la explicación de Cuentas. | No hay una; la pantalla es de lectura. Ingresos y Gastos llevan a Transacciones filtradas; **Patrimonio, Deuda, el donut y las cuentas no llevan a nada.** | Vacío de movimientos sin salida a `/conectar` ni a «agregar a mano». Carga: sin indicador al navegar (ver 10.2). | `accountsQuery`, `summaryQuery` ×4, `categoriesQuery`, `transactionsQuery`, `planQuery`, `allowanceQuery`, `budgetsQuery` | `Notice` para el vacío; `MovementCard` no hace falta (la fila actual sirve). |
| **Transacciones** lista + detalle + nueva | Lista: casi nada. Detalle: el párrafo «no está en ninguna cuenta…» y la fila **Estado** repiten la fila **Cuenta** («Sin asignar» dos veces). Nueva: el recuadro del traslado (dos frases) y la ayuda de categoría (tres líneas) pueden ir bajo demanda. | Lista: «Agregar», visible. Detalle: «Corregir» y «Eliminar» se ven, pero **no hay forma de cambiar la categoría desde el movimiento**: la categoría es del comercio y no se enlaza a `/comercios/$merchantId`. «Sin asignar» no dice cómo resolverse (enlazar la tarjeta en Cuentas). | Vacío sin filtros: no enlaza a `/conectar` ni al diagnóstico. Error de mutaciones: cubierto (`role="alert"`). | `transactionsQuery`, `transactionQuery`, `accountsQuery`, `merchantsForFilterQuery`, `categoriesQuery`, `financialCatalogQuery`, `useEditTransaction`, `useDeleteTransaction`, `useUndoTransfer`, `useCreateTransaction`, `useCreateTransferLeg` | `Notice` (traslado, sin asignar); `buttonClass` para los enlaces con aspecto de botón que hoy repiten clases a mano. |
| **Cuentas** lista + nueva + financiación | Lista: el aviso de crédito vigilado aparece **dos veces** (en la tarjeta y dentro del panel de financiación); el párrafo de enlazar alertas es largo. Financiación: cabecera de tres líneas y «Intereses y seguros del mes» con un párrafo de cuatro. | «Nueva cuenta», visible. Cada tarjeta acumula tres paneles plegables (alertas, financiación, ajustes) más un enlace: la acción de la tarjeta no destaca. **El alta ya es un asistente por tipo** (Tipo → Datos → Confirmar) con pantalla de éxito que cuenta lo adoptado. | Pestañas y paso del alta en estado local, no en la URL (atrás no los deshace). Barra de cupo sin valor accesible. | `accountsQuery` (open/all/closed), `accountQuery`, `financingQuery`, `financialCatalogQuery`, `useLinkInstrument`, `useUnlinkInstrument`, `useRenameAccount`, `useRestateBalance`, `useSetCreditLimit`, `useCloseAccount`, `useReopenAccount`, `useCreateAccount`, `useAccrue`, `useRevalue`, `useSet{Loan,Investment}Terms`, `useClearFinancing` | `Stepper` (el alta tiene uno propio), `Notice`, `SuccessMark` en el éxito del alta. |
| **Facturas** `routes/facturas.tsx` | Cabecera de tres líneas; nota bajo la barra de previsión; el aviso de «cobrar sola» es necesario pero largo. | «Declarar una factura», visible. **Cuatro acciones solo con icono** (editar, cobrar sola, pausar, borrar) a 32 px, por debajo de los 44 px del plan; el significado vive en `title`, que en el teléfono no existe. | **Errores silenciosos:** pausar, borrar y armar «cobrar sola» no muestran el fallo. Los errores que sí se muestran no llevan `role="alert"`. Un cobro pagado no enlaza al movimiento que escribió. | `billsQuery`, `accountsQuery`, `categoriesQuery`, `recurringQuery`, `useSettleDueCharges`, `useSettleCharge`, `useLinkCharge`, `usePauseBill`, `useForgetBill`, `useSetBillAutopay`, `useDeclareBill` | `Notice` (aviso de cobrar sola, previsión), `buttonClass` para acciones con texto. |
| **Presupuestos** `routes/presupuestos.tsx` | Cabecera de tres líneas; nota al pie sobre topes solapados (puede ir junto al total, bajo demanda). | «Poner un tope», visible. **Las sugerencias desde categorías con gasto real ya existen** («Donde más se te va, y sin tope»). Un tope no lleva a sus movimientos. | Mes en estado local, no en la URL. Quitar un tope no muestra el error si falla. Barras `role="presentation"` (el texto de al lado lo compensa). Acciones a 32 px. | `budgetsQuery(month)`, `categoriesQuery`, `useDeclareBudget`, `useAmendBudget`, `useForgetBudget` | `Notice`; el vacío puede ofrecer el tope «todo el mes» con un toque. |
| **Reportes** `routes/reportes/index.tsx` | Pista larga en «En qué se va, periodo a periodo». | Filtros de periodo en la URL. **Ya lleva a los movimientos** desde Gastos, Ingresos, categorías, comercios y mayores gastos; **ya marca los periodos parciales** en las columnas. Falta: las columnas (flujo, pila, día de la semana) no son navegables. | Vacío bien resuelto (enlaza a `/conectar` y a agregar a mano). | `accountsQuery`, `summaryQuery` ×4, `trendQuery` ×2, `transactionsQuery`, `categoriesQuery` | Ninguna nueva. |
| **Comercios** lista + detalle | Cabecera con ejemplo de tres líneas; el banner de revisión repite la explicación cada vez. | «Ver los pendientes» y «Está bien» por fila. Detalle: editar, mover o separar alias, fusionar — completo. | «Está bien» falla en silencio (la fila se queda, sin mensaje). **No existe un contrato de sugerencias de fusión**: «sugerencias revisables» del plan solo puede ser la cola de revisión que ya hay. | `merchantsQuery`, `merchantQuery`, `merchantCatalogQuery`, `categoriesQuery`, `merchantsForFilterQuery`, `summaryQuery("merchant")`, `useConfirmMerchant`, `useEditMerchant`, `useMoveAlias`, `useSplitAlias`, `useMergeMerchants` | `Notice` para el banner. |
| **Guías** índice + 3 lecturas | Cada tarjeta lleva dos líneas de resumen; las lecturas son de 11 a 19 párrafos. | «Leer»: en la tarjeta de conexión es incorrecto (es un asistente, no una lectura). No hay guía ni tarea para Facturas ni Presupuestos. | La tarjeta violeta dibuja el icono en magenta (el componente solo distingue cian). | `useOnboarding` (nada más) | `Notice`, `Stepper` para «continuar donde quedé». |

### 10.2. Lo que se repite en todas

- **Carga:** el router no tiene `defaultPendingComponent`; al navegar se queda
  la pantalla anterior hasta que el loader responde, sin indicación. Una barra
  fina en `AppShell` leyendo el estado del router lo resuelve para todas.
  Error y «no existe» sí son globales (`RouteError`, `NotFoundScreen`).
- **Objetivos táctiles:** las acciones con icono de Facturas, Presupuestos y
  desenlazar en Cuentas miden 32 px o menos.
- **Errores de mutación sin mostrar:** Facturas (pausar, borrar, cobrar sola),
  Presupuestos (quitar), Comercios («Está bien»).
- **Estado en la URL:** Transacciones, Comercios y Reportes ya lo hacen;
  Cuentas (pestañas, paso del alta) y Presupuestos (mes) no.
- **Enlaces con aspecto de botón** escritos a mano en al menos cinco
  pantallas en lugar de `buttonClass`: el primer uso real del kit (UX-02).

### 10.3. Correcciones al backlog

| ID | Antes | Ahora | Por qué |
| --- | ---: | ---: | --- |
| ARC-00 | 3–6 | — | Hecho: esta sección. |
| UX-05 | 4–8 | 3–6 | Solo faltan enlaces desde el vacío de Resumen y Transacciones. |
| UX-06 | 10–20 | 6–12 | Ingresos y Gastos ya llevan a su detalle; faltan Patrimonio, Deuda, donut y cuentas. |
| UX-07 | 12–24 | 10–20 | Lista sólida; el trabajo está en el detalle (categoría, «sin asignar», redundancias). |
| UX-08 | 16–32 | 10–20 | El alta por tipo ya existe; queda la densidad de la tarjeta y la URL. |
| UX-09 | 10–20 | 8–16 | Iconos con texto, errores visibles, cabecera corta. |
| UX-10 | 10–20 | 6–12 | Las sugerencias desde el gasto ya existen; queda vacío con un toque, mes en URL, enlace a movimientos. |
| UX-11 | 14–28 | 4–8 | Desglose navegable y periodos parciales ya existen; quedan las columnas. |
| UX-12 | 10–20 | 6–12 | Sin sugerencias de fusión (no hay contrato): solo textos, errores y la cola existente. Sugerir fusiones queda como ticket propio, con backend en Merchant. |
| UX-13 | 8–16 | 6–12 | Corregir la tarjeta de conexión y sumar tareas, no reescribir las lecturas. |

**Total pendiente: 82–172 horas-persona** (antes 125–250).
**Primera ola** (ARC-02/04, UX-01/02, UX-07, UX-10): **31–62 h** (antes 45–90).

Las e2e de Facturas y Presupuestos ya existen (`just e2e-bills`,
`just e2e-budgets`): UX-09 y UX-10 las amplían en lugar de crear otra.

---

## 11. Cierre de fase F0–F2 (Prompt D, 2026-10-10)

**Veredicto: APROBADA.** Ningún hallazgo bloqueante dentro de la fase.

### 11.1. Lo que se corrió

| Control | Resultado |
| --- | --- |
| `just web-check` | 541 pruebas, Biome y `tsc` en verde |
| `just prepare` | 2502 pruebas de Python; pyright 0 errores (4 avisos de stubs en `scripts/check_template.py`, anteriores) |
| `just openapi-check` | contrato al día |
| `just e2e` | las ocho suites en verde, 21 pantallas a 390 y 1280 px |
| Búsqueda en `frontend/src` | ningún dominio ni lista de bancos fuera de las pruebas |
| Exploración en navegador (script fuera del repo) | 37 de 38 comprobaciones en verde; la que falla es el hallazgo 1 |

La exploración cubrió lo que ninguna e2e prueba:

- Con `/ingestion/catalog` caído, `/conectar` no dibuja bancos de respaldo:
  tras los dos reintentos muestra «Intentar de nuevo», y reintentar con el
  servidor de vuelta trae los bancos.
- Si asignar un movimiento o cambiar su categoría falla en el servidor, la
  pantalla dice por qué, no anuncia éxito y no cambia nada.
- «Cómo funciona» con teclado: el foco va al título al abrirlo y vuelve al
  botón al cerrarlo; la X mide 44×44.
- Con el almacenamiento de ayudas bloqueado, la pantalla carga y la ayuda se
  abre y se cierra sin errores.
- Las diez pantallas tocadas, a 320 y 768 px y con la ayuda abierta.
- Un traslado no ofrece «Asignar a»; el filtro «sin asignar» sin resultados no
  explica nada; «Ver movimientos» aparece en exactamente los topes cuya lista
  cuadra; el primer tope rechaza un monto inválido sin crear nada; Resumen,
  Cuentas vacía y Comercios vacío no abren la ayuda solos y Facturas sí.

### 11.2. Hallazgos

| # | Nivel | Qué | Dónde | Decisión |
| --- | --- | --- | --- | --- |
| 1 | IMPORTANTE (fuera de la fase) | A 320 px Reportes se desborda 7 px: un nombre largo en «Dónde más gastas» o «Gastos por categoría» ensancha la rejilla, que no lleva `grid-cols-1` (la lección de la sección 6). Existe desde que nació Reportes (2026-09-02); a 390 px no pasa. | `routes/reportes/index.tsx`, las dos `grid gap-4 lg:grid-cols-2` | **Corregido** el 2026-10-10 (`grid-cols-1` y `[&>*]:min-w-0`); a 320 px ya no desborda |
| 2 | MEJORA | `aria-controls` del botón «Cómo funciona» apunta a un id que no existe mientras el panel está cerrado | `components/PageHeader.tsx` | Anotado |
| 3 | MEJORA | El aviso «Listo, tu primer tope está puesto» sigue visible si se cambia de mes | `routes/presupuestos.tsx` | Anotado |
| 4 | MEJORA | Guardar remitentes ahora espera a que el setup vuelva con el filtro; en una cuenta aún sin terminar, eso incluye leer su historial de correos. No se midió lento, pero el «Guardando…» dura lo que dure esa lectura | `api/queries.ts`, `useUpdateInbox` | Anotado |
| 5 | MEJORA | `just e2e-budgets` registra una cuenta nueva en cada corrida, como ya hacía `e2e-connect`: el emulador local las acumula | `frontend/scripts/e2e-budgets.mjs` | Aceptado |
| 6 | MEJORA (documentación) | Este plan decía que la ayuda se abre «solo en la primera visita»; en realidad se abre hasta que se cierra | sección 4, UX-01 | Corregido en el texto |

