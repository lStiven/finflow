# Guía de integración para el frontend

Qué hay que integrar, en qué orden, y qué reglas de negocio no se pueden
romper desde la interfaz. Cubre el recorrido completo —crear el usuario,
conectar el banco, recibir correos, declarar cuentas, revisar comercios— y
cómo probarlo simulando un ambiente real sin depender de que llegue un correo
de verdad.

Complementos, no sustitutos de esta guía:

| | |
|---|---|
| [overview.md](overview.md) | Qué hace el sistema y por qué está partido así. Léelo primero si nunca lo has visto. |
| [running.md](running.md) | Cómo levantar el backend, los workers y el emulador. |
| [email-forwarding.md](email-forwarding.md) | El contrato de la bandeja de entrada, en detalle. |
| [postman/](postman/README.md) | Los 26 requests listos para importar en Postman o Bruno. |
| `http://localhost:8000/docs` | OpenAPI en vivo. Es la fuente de verdad de esquemas y códigos. |

---

## Antes de escribir la primera llamada

| | |
|---|---|
| **Base URL** | `http://localhost:8000` en desarrollo. No hay prefijo `/api` ni versión en la ruta. |
| **Autenticación** | `Authorization: Bearer <token>`. JWT firmado (HS256), sin refresh y sin logout: cuando expira, se vuelve a hacer login. En local dura 24 h (`IDENTITY_ACCESS_TOKEN_TTL_MINUTES`). |
| **Dinero** | Siempre **string decimal** (`"158800"`, `"45000.50"`). Nunca number. Parséalo a decimal, no a `float`: un float pierde centavos. |
| **Tiempos** | Siempre **epoch en segundos** (int), UTC. Las alertas se interpretan en hora de Bogotá y se guardan convertidas. |
| **Aislamiento** | Todo se resuelve con el usuario del token. Ningún endpoint recibe un `user_id`. Lo ajeno responde **404**, nunca 403 — para que nadie pueda sondear qué existe. |
| **Asincronía** | El correo entra por un pipeline de colas. Lo que se ve en pantalla es el resultado de un proceso que corre por detrás, no de la petición del usuario. |
| **Idempotencia** | La entrega es *at-least-once*. Un mismo movimiento puede procesarse dos veces y el backend lo absorbe; la UI nunca debe reintentar un `POST` de movimiento manual "por si acaso" (ese sí duplica). |

### ⚠️ CORS todavía no está configurado

La aplicación **no monta middleware de CORS**. Un frontend servido desde otro
origen (`http://localhost:5173`, por ejemplo) recibirá el bloqueo del
navegador en cada llamada, incluido el preflight `OPTIONS`.

Mientras eso no se resuelva en el backend hay dos salidas:

- **Proxy en el dev server** (Vite/Next): redirige `/api/*` a
  `http://localhost:8000`. Mismo origen, sin CORS de por medio. Es lo que
  recomiendo para empezar hoy.
- Añadir `CORSMiddleware` a la API con la lista de orígenes permitidos. Es un
  cambio de backend, pequeño, y hay que hacerlo antes de desplegar el
  frontend en un dominio propio.

---

## Cómo levantar el backend para integrarte

El detalle está en [running.md](running.md); aquí va lo mínimo.

```bash
just aws-init          # emulador AWS + tablas, colas y bus (una vez por sesión)
just seed              # usuario demo, alertas, cuentas y comercios ya poblados
just dev               # API en http://localhost:8000
```

`just seed` deja una cuenta con datos reales que ya recorrieron el pipeline
completo: `demo@finflow.local` / `una frase larga de verdad`, tres cuentas,
ocho movimientos (uno **sin asignar** a propósito) y seis comercios pendientes
de revisión. El token sale impreso al final. Es reproducible: se puede volver
a correr las veces que haga falta y deja exactamente los mismos saldos.

Para ver moverse el pipeline mientras integras, cada worker en su terminal:

```bash
just parse-worker      # cola -> parser -> evento
just merchant-worker   # evento -> comercios
just financial-worker  # evento -> ledger y saldos
```

`just ingest-worker` (el que lee el buzón por IMAP) solo hace falta cuando
quieras probar con correos reales. Sin él, el webhook local
`POST /ingestion/bank-notifications` hace de banco: acepta un correo entero
como si lo hubieran reenviado.

> Ese webhook **solo existe con `ENVIRONMENT=local`**. No está autenticado y en
> producción no está montado: el `ingest worker` lee el buzón en proceso. El
> frontend no debe llamarlo nunca — es una costura de pruebas, no una ruta del
> producto.

---

## El recorrido completo, pantalla por pantalla

### 0. Salud

`GET /health` → `{"status": "ok"}`. Sin autenticación. Útil para el splash o
para decidir si mostrar "no hay conexión con el servidor".

### 1. Registro

```http
POST /identity/register
{
  "email": "yo@example.com",
  "password": "una frase larga de verdad",
  "allowed_domains": ["an.notificacionesbancolombia.com"],
  "allowed_addresses": []
}
```

→ `201` con `{ user_id, access_token, token_type, expires_at }`.

Reglas:

- **La contraseña necesita 8 caracteres como mínimo.** No hay reglas de
  composición (mayúsculas, símbolos): son conocidas por empujar a la gente
  hacia patrones predecibles. Valida solo longitud, con el mismo mensaje que
  devuelve el backend.
- **Email repetido → `409`.** Mensaje genérico a propósito.
- **El registro ya asigna la dirección de reenvío.** No hay un segundo paso
  para "crear la bandeja"; no la pidas, no la construyas, no la dejes elegir.
- Los dos arrays de remitentes son **opcionales aquí**: se pueden mandar en el
  registro para ahorrar una llamada, o dejarlos vacíos y aprobarlos después.
  Vacíos significa *no acepta nada todavía*.

### 2. Sesión

```http
POST /identity/login    → 200 { user_id, access_token, expires_at }
GET  /identity/me       → 200 { user_id }
```

- `POST /login` responde **`401` idéntico** para email desconocido, contraseña
  equivocada y email mal formado. La UI no debe intentar distinguirlos ("ese
  correo no existe" es justo lo que no se quiere filtrar).
- `expires_at` es epoch en segundos: úsalo para renovar antes de que caduque,
  no esperes al `401`.
- Un `401` en cualquier endpoint significa token ausente, inválido o vencido →
  volver al login. No hay refresh token ni endpoint de logout: cerrar sesión es
  borrar el token del cliente.

### 3. Conectar el banco

Es la pantalla que más fácil se implementa mal, y la que decide si el usuario
recibe algo o no.

```http
GET   /identity/inbox
PATCH /identity/inbox   { "allowed_domains": [...], "allowed_addresses": [...] }
```

```json
{
  "address": "finflowingest+b3a5e8f242c74822b1713f7d615df8e6@gmail.com",
  "allowed_domains": ["an.notificacionesbancolombia.com"],
  "allowed_addresses": []
}
```

Reglas que la UI tiene que respetar:

1. **La dirección se muestra, no se elige ni se edita.** Sale del id del
   usuario. Lo que sí conviene es un botón de copiar bien visible: el usuario
   tiene que pegarla en la configuración de su correo.
2. **`PATCH` reemplaza, no mezcla.** Lo que mandes es la lista completa. Para
   añadir un remitente hay que enviar los que ya estaban **más** el nuevo: lee
   primero, concatena, envía.
3. **Lista vacía = no acepta nada.** Nunca es "acepta todo". Si el usuario
   borra todos sus remitentes, la pantalla debe decir claramente que dejará de
   recibir movimientos.
4. **Solo sirve el reenvío automático del cliente de correo.** El botón
   "Reenviar" manual de Gmail reescribe el `From` con la dirección del propio
   usuario, y ese correo se rechaza por remitente no aprobado — correctamente.
   La ayuda en pantalla debe llevar a crear un **filtro** con reenvío
   automático. El paso a paso está en [email-forwarding.md](email-forwarding.md).
5. **Un dominio cubre más que una dirección.** Los bancos rotan la parte local
   de sus remitentes (`alertas@`, `notificaciones@`) mucho más que el dominio.
   Sugiere aprobar el dominio.

### 4. Qué pasa cuando llega un correo

Esto no lo dispara el frontend, pero determina qué puede prometer la interfaz.

```
banco -> correo del usuario -> reenvío automático -> alias del usuario
      -> ingest worker (IMAP, sondea cada 60 s)
      -> filtro de remitente -> deduplicación -> cola SQS
      -> parse worker: plantilla determinista, si no, Gemini
      -> evento TransactionExtracted
      -> merchant worker (comercio canónico)  +  financial worker (ledger y saldo)
```

Lo que implica para la UI:

- **Latencia de segundos a un par de minutos** entre que el banco envía y que
  el movimiento aparece. No hay push ni websockets: refresca al abrir la
  pantalla y ofrece "pull to refresh". Un spinner infinito esperando un
  movimiento concreto es la forma equivocada de modelarlo.
- **Un correo puede no producir ningún movimiento** y eso no es un fallo: un
  remitente no aprobado se ignora, una plantilla desconocida sin modelo
  configurado se conserva sin extraer, una autorización (compra "aprobada",
  todavía no cobrada) se descarta a propósito para no contar el gasto dos
  veces.
- **El frontend no puede ver los correos.** Hoy no hay ningún endpoint que
  liste notificaciones ni sus estados; eso solo se ve por CLI (`just inspect`).
  Ver [Lo que el backend todavía no expone](#lo-que-el-backend-todavía-no-expone).

### 5. Movimientos

```http
GET /financial/transactions?limit=50&offset=0
GET /financial/transactions?unassigned=true
GET /financial/transactions?account_id=<id>
GET /financial/transactions?origin=bank_alert|manual
GET /financial/transactions?search=EXITO
GET /financial/transactions/{transaction_id}
```

```json
{
  "transactions": [
    {
      "id": "c48f5bac931041c6a4d6355eab87e1d0",
      "direction": "outgoing",
      "amount": "89900",
      "currency": "COP",
      "occurred_at": 1787493600,
      "counterparty": "NETFLIX",
      "bank": "",
      "origin": "manual",
      "status": "assigned",
      "account_id": "a854326b-3cf9-4378-9679-fce7de56d95d",
      "note": "cobro automatico, sin correo",
      "stated": null
    }
  ],
  "total": 8,
  "limit": 2,
  "offset": 0
}
```

Reglas:

- **Orden fijo: del más reciente al más antiguo**, por `occurred_at`. No hay
  parámetro de ordenación.
- **Paginación por `limit`/`offset`**; `limit` entre 1 y 200, 50 por defecto.
  `total` es el total **filtrado**, no el global.
- `search` filtra por `counterparty` únicamente (subcadena, sin distinguir
  mayúsculas). No busca en la nota ni en el banco.
- **`status: "unassigned"` no es un error ni un estado a medias.** Es un
  movimiento registrado que todavía no pertenece a ninguna cuenta, porque
  nadie declaró esa tarjeta o porque la alerta no nombró un instrumento
  utilizable (el caso típico: una nómina que dice "en tu cuenta de AHORROS"
  sin dígitos). Alguien que solo quiere ver qué entra y qué sale puede vivir
  así para siempre. Muéstralo como una bandeja de "movimientos sin cuenta",
  con la acción de declarar la cuenta al lado — no como una advertencia roja.
- `bank` viene vacío en los movimientos manuales; en los de alerta llega
  normalizado en minúsculas (`"bancolombia"`).

### 6. Cuentas

```http
GET   /financial/accounts?scope=open|closed|all
POST  /financial/accounts
GET   /financial/accounts/{id}
PATCH /financial/accounts/{id}                 { "name": "..." }
POST  /financial/accounts/{id}/instruments     { "bank", "instrument_kind", "last_four" }
POST  /financial/accounts/{id}/close
GET   /financial/net-worth
```

Declarar una cuenta:

```json
{
  "name": "Tarjeta Bancolombia",
  "kind": "credit_card",
  "currency": "COP",
  "opening_balance": null,
  "bank": "Bancolombia",
  "instrument_kind": "credit_card",
  "last_four": "1234"
}
```

Reglas:

1. **Las cuentas las declara el usuario; el sistema nunca las crea solo.** Una
   alerta de una tarjeta desconocida no abre una cuenta: se queda sin asignar.
2. **Declararla es retroactivo.** En cuanto existe, adopta los movimientos que
   estaban esperando y recalcula el saldo con ellos. La respuesta trae
   `movements_applied` ya actualizado: es un número perfecto para el mensaje de
   éxito ("adoptamos 3 movimientos que estaban esperando").
3. **`instrument_kind` y `last_four` van juntos o no van.** Uno solo se rechaza
   con `422`: emparejar por uno de los dos fusionaría dos cuentas reales. Y un
   instrumento exige `bank` — "termina en 1234" sin banco no identifica nada.
4. **El emparejamiento es (banco, tipo de instrumento, últimos cuatro).** Una
   misma cuenta de ahorros puede recibir alertas bajo dos nombres distintos —
   la cuenta (`account`) y la tarjeta débito que tira de ella (`debit_card`).
   Para eso está `POST /instruments`: enlaza el segundo nombre, y también
   adopta retroactivamente. Repetir un enlace que ya existe no hace nada (no
   falla).
5. **Cuenta duplicada → `409`** ("An account already answers to that bank and
   card"). Ofrece abrir la existente, no reintentar.
6. **El saldo viene firmado, y el signo depende de la categoría.** `category`
   es `asset` o `liability` y **no se elige**: se deriva de `kind`. En una
   tarjeta de crédito, `"balance": "158800"` significa *debes 158.800*. Un
   activo puede quedar en negativo legítimamente cuando no se declaró saldo
   inicial: es un histórico incompleto, no un error.
7. **`opening_balance` es opcional y solo tiene sentido al abrir.** Sin él, la
   cuenta arranca en cero y el saldo es "lo que ha pasado desde que la
   declaraste", no lo que hay en el banco.
8. **El patrimonio neto es un array, uno por moneda.** Nunca sumes monedas: no
   hay tipo de cambio guardado en ninguna parte, y inventarlo corrompe el
   número. Si hay COP y USD, se muestran dos cifras, no una.
9. **Cerrar no borra.** La cuenta deja de aceptar movimientos y conserva su
   historia y su saldo (un crédito pagado que cierra en cero es justo lo que
   hay que ver). Un movimiento nuevo para una cuenta cerrada se queda **sin
   asignar**, no se pierde. Cerrar dos veces no falla.
10. **No hay borrado de cuentas.** No ofrezcas "eliminar".

`GET /financial/accounts` devuelve la lista **ordenada por nombre** y, con
ella, `net_worth`: la pantalla principal se resuelve con **una sola llamada**.

Cuidado con una diferencia sutil entre las dos formas de pedir el patrimonio:
el `net_worth` que viene en `/financial/accounts` se calcula **sobre el `scope`
pedido** —por defecto, solo las cuentas abiertas—, mientras que
`GET /financial/net-worth` siempre las incluye todas, cerradas incluidas. Si la
app enseña la cifra en dos sitios con criterios distintos, el usuario verá dos
números que no cuadran.

### 7. Registrar y corregir a mano

```http
POST  /financial/transactions          { direction, amount, currency, occurred_at, counterparty, account_id?, bank?, note? }
PATCH /financial/transactions/{id}     { amount?, currency?, occurred_at?, counterparty?, note?, account_id?, detach? }
```

Reglas:

- **Lo manual es para lo que el banco no anuncia**: efectivo, un cobro
  automático sin correo, una transferencia que no generó alerta.
- **`account_id` es opcional.** Sin él, el movimiento nace sin asignar, igual
  que una alerta huérfana.
- **Un movimiento manual con cuenta se rechaza si la cuenta no puede tomarlo**
  — cerrada (`409`) o en otra moneda (`400`) — en vez de guardarlo en otro
  sitio. Es una acción que el usuario acaba de pedir: decirle que no se pudo
  es más útil que colocarla en un lugar que no eligió.
- **Un movimiento manual no es idempotente.** Su identidad es aleatoria: dos
  `POST` iguales son dos gastos. Deshabilita el botón mientras la petición está
  en vuelo y no reintentes automáticamente.
- **Corregir una alerta conserva lo que dijo el banco.** La primera corrección
  de monto, fecha o contraparte guarda el original en `stated`; a partir de ahí
  la respuesta trae las dos versiones. Muéstralo ("el banco decía 45.000") en
  lugar de esconderlo: es la única forma de saber después si se equivocó el
  parser o el banco. Una nota sola **no** crea `stated`: no es una afirmación
  sobre el movimiento.
- **Cambiar el monto obliga a mandar la moneda** (`422` si no).
- **`account_id` y `detach` son excluyentes** (`422` si van los dos). `detach`
  deja el movimiento sin cuenta; `account_id` lo mueve a otra, y los saldos de
  las dos cuentas implicadas se recalculan desde el ledger.
- **Un `PATCH` vacío se rechaza** (`422`, "Nothing to change").
- **La identidad nunca cambia al corregir**, así que una alerta reentregada
  después de la corrección sigue siendo el mismo movimiento y no aparece dos
  veces.
- **No hay borrado de movimientos.**

### 8. Comercios

```http
GET   /merchants/categories                    (sin autenticación)
GET   /merchants?search=&category=&needs_review=&sort=&limit=&offset=
GET   /merchants/{id}
PATCH /merchants/{id}                          { display_name?, category? }
POST  /merchants/{id}/confirm
POST  /merchants/{id}/aliases/move             { fingerprint, target_merchant_id }
POST  /merchants/{id}/aliases/split            { fingerprint, display_name?, category? }
POST  /merchants/{id}/merge                    { absorbed_merchant_id }
```

Modelo: un **comercio** tiene N **alias** (las distintas formas en que su
nombre llega en las alertas). Cada alias dice de dónde salió: `seed` (el
primero, el que creó el comercio), `derived` (mismo nombre módulo ruido),
`suggested` (una conjetura del sistema) o `manual` (lo puso el usuario).

Reglas:

- **`needs_review` es la cola de trabajo del usuario.** El listado trae también
  `needs_review` como total global, pensado para el badge: no se mueve cuando
  el usuario escribe en el buscador.
- **Renombrar o recategorizar cuenta como revisar**: el comercio sale de la
  cola y pasa a `status: "confirmed"`. `POST /confirm` es la versión "está bien
  así", conjeturas incluidas.
- **Las categorías se leen del backend** (`GET /merchants/categories`, con
  `value` y `label`), nunca se escriben a mano en el frontend: un desplegable
  hardcodeado se desincroniza el día que se añada una.
- **Las correcciones del usuario son permanentes.** Mover un alias a otro
  comercio hace que esa grafía se resuelva por coincidencia exacta desde
  entonces; ninguna regla vuelve a decidir por él. Conviene que la UI lo diga.
- `fingerprint` acepta tanto la huella que viene en la lista de alias como el
  texto crudo detrás de ella.
- **Sacar el último alias de un comercio → `409`**: un comercio sin alias no
  existe. Ofrece "fusionar" en su lugar.
- **Fusionar con uno mismo → `400`.** En un merge, el del path sobrevive.
- **`times_seen` cuenta apariciones del nombre, no dinero.** El gasto lo
  responde Financial, y hoy **no hay forma fiable de unir un movimiento con su
  comercio** desde el frontend (ver los gaps abajo).

---

## Reglas de negocio que el frontend no puede romper

Resumen para revisar contra la interfaz cuando esté hecha:

1. Nunca inventar una cuenta ni sugerir que el sistema la creó: las declara el
   usuario, siempre.
2. Nunca tratar "sin asignar" como error. Es un estado normal y completo.
3. Nunca sumar patrimonio de monedas distintas.
4. Nunca mostrar un saldo de pasivo con el signo cambiado: positivo es lo que
   se debe.
5. Nunca parsear dinero como `float`.
6. Nunca mandar `PATCH /identity/inbox` con una lista parcial creyendo que
   añade.
7. Nunca presentar la lista de remitentes vacía como "acepta todo".
8. Nunca reintentar automáticamente un `POST` de movimiento manual.
9. Nunca ocultar `stated` cuando existe.
10. Nunca llamar al webhook de ingesta desde la app.
11. Nunca hardcodear el vocabulario de categorías.
12. Nunca esperar consistencia inmediata después de un correo.

---

## Vocabularios

Todos son strings estables: se persisten, así que no cambian de valor aunque se
reordenen.

| Enum | Valores |
|---|---|
| `direction` | `outgoing`, `incoming` |
| `origin` (movimiento) | `bank_alert`, `manual` |
| `status` (movimiento) | `assigned`, `unassigned` |
| `kind` (cuenta) | `savings`, `checking`, `cash`, `investment`, `credit_card`, `loan`, `mortgage` |
| `category` (cuenta, derivada) | `asset`, `liability` |
| `currency` | `COP`, `USD` |
| `instrument_kind` | `credit_card`, `debit_card`, `savings_account`, `checking_account`, `account` |
| `status` (comercio) | `automatic`, `confirmed` |
| `origin` (alias) | `seed`, `derived`, `suggested`, `manual` |
| `sort` (comercios) | `name`, `last_seen`, `times_seen` |
| `category` (comercio) | `uncategorized`, `groceries`, `restaurants`, `transport`, `fuel`, `shopping`, `entertainment`, `subscriptions`, `utilities`, `health`, `education`, `travel`, `fees`, `transfers`, `income`, `other` |

`instrument_kind` viene del parser, no de un desplegable: una compra con
tarjeta de crédito llega como `credit_card`, un QR o una transferencia como
`account`. Es lo que hay que usar al declarar la cuenta para que la adopte.

---

## Referencia de endpoints

| Método | Ruta | Auth | Para qué |
|---|---|---|---|
| GET | `/health` | — | Vivo o no. |
| POST | `/identity/register` | — | Crear cuenta; devuelve token y asigna la dirección de reenvío. |
| POST | `/identity/login` | — | Token. |
| GET | `/identity/me` | ✔ | El id del usuario del token. |
| GET | `/identity/inbox` | ✔ | Dirección de reenvío + remitentes aprobados. |
| PATCH | `/identity/inbox` | ✔ | Reemplazar los remitentes aprobados. |
| GET | `/merchants/categories` | — | Vocabulario de categorías. |
| GET | `/merchants` | ✔ | Listar/buscar/filtrar comercios. |
| GET | `/merchants/{id}` | ✔ | Detalle con sus alias. |
| PATCH | `/merchants/{id}` | ✔ | Renombrar y/o recategorizar (marca revisado). |
| POST | `/merchants/{id}/confirm` | ✔ | Aceptar tal cual. |
| POST | `/merchants/{id}/aliases/move` | ✔ | Mover una grafía a otro comercio. |
| POST | `/merchants/{id}/aliases/split` | ✔ | Sacar una grafía a comercio propio. |
| POST | `/merchants/{id}/merge` | ✔ | Fusionar dos comercios. |
| GET | `/financial/accounts` | ✔ | Cuentas + patrimonio, en una llamada. |
| POST | `/financial/accounts` | ✔ | Declarar cuenta (adopta lo que esperaba). |
| GET | `/financial/accounts/{id}` | ✔ | Detalle. |
| PATCH | `/financial/accounts/{id}` | ✔ | Renombrar. |
| POST | `/financial/accounts/{id}/instruments` | ✔ | Enlazar otro instrumento. |
| POST | `/financial/accounts/{id}/close` | ✔ | Cerrar (no borra). |
| GET | `/financial/net-worth` | ✔ | Patrimonio por moneda. |
| GET | `/financial/transactions` | ✔ | Movimientos, con filtros y paginación. |
| POST | `/financial/transactions` | ✔ | Registrar a mano. |
| GET | `/financial/transactions/{id}` | ✔ | Detalle. |
| PATCH | `/financial/transactions/{id}` | ✔ | Corregir, mover de cuenta o desasignar. |
| POST | `/ingestion/bank-notifications` | — | **Solo local.** Simular un correo. No es una ruta del producto. |

---

## Errores

Hay **dos formas** de cuerpo de error, y el frontend tiene que manejar las dos:

```jsonc
// Validación de esquema (422) — FastAPI: detail es una lista
{ "detail": [ { "type": "value_error", "loc": ["body"], "msg": "Value error, ...", "input": {...} } ] }

// Reglas de negocio y todo lo demás — detail es un string
{ "detail": "An account already answers to that bank and card" }
```

| Código | Cuándo | Qué hacer en pantalla |
|---|---|---|
| `400` | Moneda que la cuenta no tiene, valor imposible. | Mensaje del `detail`; es accionable. |
| `401` | Sin token, token inválido o vencido. Trae `WWW-Authenticate: Bearer`. | Ir al login. |
| `404` | No existe **o es de otro usuario**. | "No encontrado". Nunca "no tienes permiso". |
| `409` | El estado lo impide: cuenta duplicada, cuenta cerrada, movimiento ya asignado, último alias. | Ofrecer la salida (abrir la existente, fusionar…). |
| `422` | Esquema o regla del payload. `detail` es lista. | Marcar el campo; `loc` dice cuál. |

Un `202` del webhook local **no** significa que se aceptó el correo: siempre
responde 202, incluso para una dirección desconocida, para que nadie pueda
sondear qué direcciones existen. El campo `outcome` (`accepted`, `duplicate`,
`unknown_recipient`) es el que informa.

---

## Lo que el backend todavía no expone

Importante para planear pantallas: esto **no se puede construir hoy** sin
tocar el backend.

| Falta | Consecuencia para el frontend |
|---|---|
| **CORS** | Bloqueante desde un origen distinto. Proxy en el dev server mientras tanto. |
| **Listado de correos/notificaciones** | No se puede mostrar "recibimos 3 correos, 1 sin leer" ni el estado de la ingesta. La pantalla de "conectar el banco" no puede confirmar que llegó nada; solo se puede inferir por movimientos nuevos. |
| **Relación movimiento ↔ comercio** | Un movimiento trae `counterparty` en crudo, no el `merchant_id`. No hay forma fiable de agrupar gasto por comercio o por categoría desde el cliente: la normalización que hace Merchant no está expuesta. Buscar por texto es aproximado, no equivalente. |
| **Agregados** | No hay "gasto del mes", "por categoría", "por cuenta". Se puede calcular en el cliente paginando movimientos, con el límite de 200 por página, o pedir el endpoint. |
| **Tiempo real** | Sin websockets ni SSE. Polling o refresco manual. |
| **Refresh token / logout** | Sesión = token guardado en el cliente; al vencer, login otra vez. |
| **Borrado** | No hay `DELETE` de cuentas ni de movimientos. Cerrar y corregir es lo que hay. |
| **Paginación por cursor** | `limit`/`offset` solamente; `total` es el filtrado. |

---

## Guion de prueba de punta a punta

Simula el ambiente real completo sin depender de que llegue un correo. Con la
infraestructura arriba (`just aws-init`) y `just dev` corriendo.

### Opción rápida

```bash
just seed
```

Deja todo poblado y verificable. Sirve para desarrollar contra datos estables.

### Opción manual, paso a paso

Es lo mismo que hace el seed, request a request. Vale la pena una vez para ver
dónde se detiene cada pieza.

**1. Usuario y token.**

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/identity/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"prueba@example.com","password":"una frase larga de verdad"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

**2. Su dirección de reenvío.**

```bash
ADDRESS=$(curl -s http://localhost:8000/identity/inbox \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['address'])")
echo "$ADDRESS"
```

**3. Aprobar el remitente del banco.** Sin esto no se acepta nada.

```bash
curl -X PATCH http://localhost:8000/identity/inbox \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"allowed_domains":["an.notificacionesbancolombia.com"]}'
```

**4. Simular el correo del banco** (esto es lo que en producción hace el
`ingest worker` leyendo el buzón).

```bash
curl -X POST http://localhost:8000/ingestion/bank-notifications \
  -H 'Content-Type: application/json' \
  -d "{\"recipient\":\"$ADDRESS\",
       \"message_id\":\"<prueba-1@example.com>\",
       \"sender\":\"alertasynotificaciones@an.notificacionesbancolombia.com\",
       \"subject\":\"Notificación\",
       \"raw_content\":\"Bancolombia: Compraste \$45.000 en EXITO CALI con tu T.Cred *1234, el 20/08/2026 a las 10:15\"}"
```

Con `just parse-worker`, `just merchant-worker` y `just financial-worker`
corriendo, en segundos hay comercio y movimiento.

**5. Ver el movimiento esperando cuenta.**

```bash
curl -s "http://localhost:8000/financial/transactions?unassigned=true" \
  -H "Authorization: Bearer $TOKEN"
```

**6. Declarar la cuenta y ver la adopción retroactiva.**

```bash
curl -X POST http://localhost:8000/financial/accounts \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Tarjeta Bancolombia","kind":"credit_card","currency":"COP",
       "bank":"Bancolombia","instrument_kind":"credit_card","last_four":"1234"}'
```

La respuesta trae `movements_applied: 1` y el saldo ya movido.

**7. Comprobar el aislamiento entre usuarios.** Registra un segundo usuario,
pide su lista con su propio token y confirma que **no ve nada** del primero; y
que pedir por id una cuenta del primero responde `404`, no `403`.

```bash
OTHER=$(curl -s -X POST http://localhost:8000/identity/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"otro@example.com","password":"otra frase larga de verdad"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s http://localhost:8000/financial/accounts -H "Authorization: Bearer $OTHER"
```

**8. Probar los rechazos**, que son la mitad del contrato:

```bash
# Remitente no aprobado: el correo se registra pero nunca se parsea.
# Cuenta duplicada -> 409.
# Instrumento a medias (last_four sin instrument_kind) -> 422.
# PATCH de movimiento vacío -> 422.
# Token ausente -> 401.
```

### Con correos reales

Cuando quieras cerrar el círculo entero, configura la cuenta de ingesta y el
reenvío automático siguiendo [email-forwarding.md](email-forwarding.md), corre
`just ingest-worker` y reenvía una alerta de verdad desde tu banco. El resto
del recorrido es idéntico: el webhook del paso 4 es lo único que se sustituye.
