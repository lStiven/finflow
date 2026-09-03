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
| [deploy.md](deploy.md) | Cómo se despliega el backend y cómo se publica el frontend. |
| [email-forwarding.md](email-forwarding.md) | El contrato de la bandeja de entrada, en detalle. |
| [postman/](postman/README.md) | Los 36 requests listos para importar en Postman o Bruno. |
| `http://localhost:8000/docs` | OpenAPI en vivo. Es la fuente de verdad de esquemas y códigos. |
| [openapi.json](openapi.json) | El mismo contrato, exportado y versionado (`just openapi`). De aquí salen los tipos TypeScript del frontend. |
| [frontend/](../frontend/README.md) | La app que consume todo esto. Cómo levantarla y regenerar los tipos. |

---

## Antes de escribir la primera llamada

| | |
|---|---|
| **Base URL** | `http://localhost:8000` en local; contra un entorno desplegado, la `ApiUrl` del stack (`just deploy-outputs-dev` / `-prod`). No hay prefijo `/api` ni versión en la ruta. En el frontend es la única variable que lo decide: `VITE_API_BASE_URL`. |
| **Autenticación** | `Authorization: Bearer <token>`. JWT firmado (HS256), sin refresh y sin logout: cuando expira, se vuelve a hacer login. En local dura 24 h (`IDENTITY_ACCESS_TOKEN_TTL_MINUTES`). |
| **Dinero** | Siempre **string decimal** (`"158800"`, `"45000.50"`). Nunca number. Parséalo a decimal, no a `float`: un float pierde centavos. |
| **Tiempos** | Siempre **epoch en segundos** (int), UTC. Las alertas se interpretan en hora de Bogotá y se guardan convertidas. |
| **Aislamiento** | Todo se resuelve con el usuario del token. Ningún endpoint recibe un `user_id`. Lo ajeno responde **404**, nunca 403 — para que nadie pueda sondear qué existe. |
| **Asincronía** | El correo entra por un pipeline de colas. Lo que se ve en pantalla es el resultado de un proceso que corre por detrás, no de la petición del usuario. |
| **Idempotencia** | La entrega es *at-least-once*. Un mismo movimiento puede procesarse dos veces y el backend lo absorbe; la UI nunca debe reintentar un `POST` de movimiento manual "por si acaso" (ese sí duplica). |

### CORS: qué origen puede llamar

La API acepta llamadas desde los orígenes listados en `API_CORS_ORIGINS`, y
solo desde esos. El `.env` local ya trae los dos servidores de desarrollo
habituales:

```bash
API_CORS_ORIGINS=http://localhost:5173,http://localhost:3000
```

Si tu dev server escucha en otro puerto, añádelo a esa lista —separada por
comas— y reinicia `just dev`.

Lo que hay que saber para escribir el cliente:

- **Métodos permitidos: `GET`, `POST`, `PATCH`, `PUT`.** Son los que la API usa; el
  preflight `OPTIONS` lo responde el middleware.
- **Cabeceras permitidas: `Authorization` y `Content-Type`**, además de las que
  el navegador considera seguras por sí mismo. Si mandas una cabecera propia
  (`X-Trace-Id`, lo que sea), el preflight la rechaza hasta que se añada en el
  backend.
- **Las credenciales están desactivadas a propósito.** La autenticación es un
  bearer token que el cliente adjunta él mismo, no una cookie. No uses
  `credentials: "include"`: no hay cookie que mandar, y pedirla solo haría
  peligroso un origen mal configurado.
- **Vacío significa ningún origen cruzado.** Es lo correcto cuando el frontend
  se sirve desde el mismo origen que la API; no es un olvido.
- **`*` se rechaza al arrancar con `ENVIRONMENT=production`** y la aplicación no
  levanta. En local sí se admite, para trastear.
- **Escribe el origen exacto: `esquema://host[:puerto]`**, sin barra final y
  sin ruta. Es literalmente lo que el navegador manda en la cabecera `Origin`,
  y la comparación es exacta: `https://app.example.com/` no coincide con nada.
  La aplicación lo comprueba al arrancar y se niega a levantar con un valor mal
  escrito, en vez de dejarte depurando el navegador.
- Un origen no listado tiene **dos respuestas distintas** según el tipo de
  petición: una simple (un `GET` sin cabeceras especiales) recibe `200` sin
  cabeceras CORS y es el navegador quien la bloquea; un preflight recibe
  `400 Disallowed CORS origin` del propio middleware. Si ves ese 400, o
  "blocked by CORS policy" en la consola, lo primero que hay que mirar es
  `API_CORS_ORIGINS`.

---

## Cómo levantar el backend para integrarte

El detalle está en [running.md](running.md); aquí va lo mínimo.

```bash
just aws-init          # emulador AWS + tablas, colas y bus (una vez por sesión)
just seed              # usuario demo, alertas, cuentas y comercios ya poblados
just dev               # API en http://localhost:8000
```

> Esta guía describe **la API**, no el cliente. Ya existe un frontend en
> `frontend/` que la consume: se levanta con `just web` y sus tipos se generan
> desde `docs/openapi.json`, así que un endpoint que cambie aquí rompe su
> compilación. Cómo levantarlo y contra qué backend apunta está en
> [running.md → El frontend](running.md#el-frontend).

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

`GET /health` → `{"status": "ok", "environment": "production"}`. Sin
autenticación. Útil para el splash o para decidir si mostrar "no hay conexión
con el servidor". `environment` es `local`, `development` o `production`, y
está para que quien va a escribir sepa contra qué despliegue está: es lo que
usa `scripts/smoke.py` para negarse a registrar usuarios en producción. Un
frontend puede ignorarlo, o usarlo para pintar un aviso cuando no es
`production`.

### 1. Registro

Son **tres llamadas, no una**: la dirección tiene que demostrar que se lee
antes de que exista una cuenta detrás. Sin eso, cualquiera que pueda hacer un
`POST` llena el despliegue de cuentas que nadie puede recuperar.

```http
POST /identity/verification/request     → 202 { expires_in_minutes, code }
{ "email": "yo@example.com" }

POST /identity/verification/confirm     → 200 { verification_token, expires_at }
{ "email": "yo@example.com", "code": "123456" }

POST /identity/register                 → 201 { user_id, access_token, ... }
{
  "email": "yo@example.com",
  "password": "una frase larga de verdad",
  "verification_token": "<el de arriba>",
  "name": "Tu Nombre",
  "allowed_domains": ["an.notificacionesbancolombia.com"],
  "allowed_addresses": []
}
```

Reglas del paso del código:

- **`request` responde `202` siempre**, exista o no una cuenta con ese correo,
  y manda correo en los dos casos (uno con el código, otro diciendo "ya tienes
  cuenta"). No intentes deducir de la respuesta si el correo está registrado:
  el backend no lo dice a propósito, y la UI no debe inventarlo.
- **`code` en la respuesta solo viene en local.** Es `null` en cualquier
  despliegue real; ahí el código está únicamente en el buzón. Sirve para que
  un script termine el flujo sin correo, no para la pantalla.
- **El código son 6 dígitos y vive 15 minutos.** Cinco intentos: el sexto
  responde `429` y hay que pedir otro. Espacios y guiones se limpian solos, así
  que un pegado desde el correo funciona.
- **`confirm` responde igual (`400`, "That code is not valid") para código
  equivocado, correo sin desafío y correo mal formado.** Un código vencido es
  `410` y quiere decir "pide otro"; agotar los intentos es `429`.
- **`verification_token` vive 30 minutos y se gasta una sola vez.** Es lo que
  reserva la dirección: verificar no es registrar, y sin el token alguien que
  supiera que un correo acaba de verificarse podría ganarle la carrera a su
  dueño.
- **Límite por dirección: un mensaje por minuto, cinco por hora.** Pasado eso,
  `429` con cabecera `Retry-After` en segundos — muéstrala, es el único dato
  que da.

Reglas del registro:

- **La contraseña necesita 8 caracteres como mínimo.** No hay reglas de
  composición (mayúsculas, símbolos): son conocidas por empujar a la gente
  hacia patrones predecibles. Valida solo longitud, con el mismo mensaje que
  devuelve el backend.
- **Sin `verification_token` válido → `403`.** También si venció, si ya se
  gastó, o si es de otra dirección.
- **La contraseña se valida antes que el token**, así que un `422` por
  contraseña corta no quema el token: se puede reintentar con el mismo.
- **Email repetido → `409`.** Mensaje genérico a propósito — y para llegar
  hasta ahí hay que haber leído el código enviado a esa dirección, que es lo
  que evita que el `409` sirva para averiguar quién está registrado.
- **`name` es opcional.** La cuenta se identifica por el correo, así que se
  puede registrar sin nombre y ponerlo después con `PATCH /identity/me`.
  Máximo 80 caracteres; solo espacios se rechaza con `422`.
- **El registro ya asigna la dirección de reenvío.** No hay un segundo paso
  para "crear la bandeja"; no la pidas, no la construyas, no la dejes elegir.
- Los dos arrays de remitentes son **opcionales aquí**: se pueden mandar en el
  registro para ahorrar una llamada, o dejarlos vacíos y aprobarlos después.
  Vacíos significa *no acepta nada todavía*.

### 2. Sesión

```http
POST  /identity/login    → 200 { user_id, access_token, expires_at }
GET   /identity/me       → 200 { user_id, email, name }
PATCH /identity/me       → 200 { user_id, email, name }
{ "name": "Tu Nombre" }
```

- `POST /login` responde **`401` idéntico** para email desconocido, contraseña
  equivocada y email mal formado. La UI no debe intentar distinguirlos ("ese
  correo no existe" es justo lo que no se quiere filtrar).
- `expires_at` es epoch en segundos: úsalo para renovar antes de que caduque,
  no esperes al `401`.
- Un `401` en cualquier endpoint significa token ausente, inválido o vencido →
  volver al login. No hay refresh token ni endpoint de logout: cerrar sesión es
  borrar el token del cliente.
- **El token lleva `sub`, `email` y `name`** (los nombres estándar: se leen con
  cualquier decoder). Sirven para pintar al usuario apenas entra, sin una
  llamada extra — pero son una foto del momento en que se emitió: después de un
  `PATCH /identity/me` el `name` del token sigue siendo el viejo hasta el
  siguiente login. Para mostrar el actual, `GET /identity/me`.
- **`PATCH /identity/me` solo cambia el nombre.** El correo es la identidad de
  la cuenta y no hay forma de moverlo. A quién se edita sale del token: no
  recibe id, así que no hay manera de pedir la cuenta de otro.
- **Cambiar la contraseña invalida todos los tokens vigentes**, incluido el que
  hizo la llamada. Cada request autenticado compara la generación de
  credenciales que lleva el token contra la de la cuenta, así que una sesión
  abierta en otro dispositivo deja de servir en el acto. Ver el paso 2.b.

### 2.b Contraseña olvidada, restablecida y cambiada

```http
POST /identity/password/forgot   → 202, cuerpo vacío
{ "email": "yo@example.com" }

POST /identity/password/reset    → 204
{ "token": "<el del enlace del correo>", "new_password": "otra frase larga" }

POST /identity/password/change   → 200 { user_id, access_token, expires_at }   (auth)
{ "current_password": "...", "new_password": "..." }
```

- **`forgot` responde `202` exista o no la cuenta**, y manda correo en los dos
  casos. La pantalla tiene que decir lo mismo siempre ("si hay una cuenta con
  ese correo, te llegó un enlace"): decir "ese correo no existe" es exactamente
  lo que el backend se niega a decir. Mismo límite que el código: uno por
  minuto, cinco por hora, `429` con `Retry-After`.
- **El enlace apunta al frontend, no a la API.** Su forma es
  `IDENTITY_PASSWORD_RESET_URL` + `?token=…`; en producción,
  `https://finflow-apk.pages.dev/restablecer?token=…`. Esa página lee el token
  de la query y lo manda en el cuerpo de `POST /identity/password/reset`.
- **Vive 30 minutos y sirve una sola vez.** Pedir otro retira el anterior.
- **`reset` responde `204` sin token.** No inicia sesión: después hay que
  entrar con la contraseña nueva, que es también la prueba de que funcionó.
  Desconocido, vencido y ya usado son un solo `400` — nunca confirma que un
  token haya sido real. Contraseña corta es `422`, y **no** gasta el enlace.
- **`change` pide la contraseña actual** aunque la llamada ya venga
  autenticada: una sesión abierta en una máquina prestada no puede alcanzar
  para quedarse con la cuenta. Repetir la misma contraseña → `422`.
- **Contraseña actual equivocada → `403`, no `401`.** Deliberado, y el
  frontend depende de ello: el token está bien y la sesión no terminó, así que
  un cliente que trate el `401` como "te desconectaron" no debe ver uno aquí.
  Un `401` en esta ruta sigue significando lo de siempre: no hay token.
- **`change` devuelve un token nuevo, y hay que guardarlo.** El cambio invalida
  el que hizo la llamada; si el cliente no lo reemplaza, el siguiente request
  da `401` y parece un cierre de sesión.

#### Lo que el cliente HTTP tiene que hacer con estos códigos

Un detalle que muerde: el `401` es el código con el que casi todo cliente
cierra la sesión solo. Por eso ninguna de estas rutas lo devuelve por otra
razón que "no hay token": una contraseña actual equivocada es `403`, y un
`verification_token` gastado también. Si tu capa de red convierte cualquier
`401` en "volver al login", este contrato ya está pensado para eso.

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

#### `GET /ingestion/setup` — en qué paso va el usuario

La pantalla anterior tiene un problema: **dos de sus cuatro pasos terminan
donde el usuario no puede ver nada**. Google confirma la solicitud de reenvío
mandando un correo a una dirección que solo lee este despliegue, y la primera
alerta la recoge un worker. Sin esto la pantalla solo podría decir "ya te
avisaremos", que es la parte de todo onboarding que se siente rota aunque
funcione.

```http
GET /ingestion/setup
Authorization: Bearer <token>
```

```json
{
  "address": "finflowingest+b3a5e8f242c74822b1713f7d615df8e6@gmail.com",
  "steps": [
    { "key": "address_assigned",    "done": true,  "at": null },
    { "key": "senders_approved",    "done": true,  "at": null },
    { "key": "forwarding_confirmed","done": true,  "at": 1756400000 },
    { "key": "first_alert",         "done": false, "at": null }
  ],
  "current": "first_alert",
  "ready": false,
  "unapproved_senders": []
}
```

- **No hay endpoint para avanzar un paso, y no lo habrá.** El estado se
  deriva del registro del inbox en cada llamada, así que es el mismo en todos
  los navegadores y no puede desfasarse de lo que hizo el buzón. Un contador
  que escribiera el front estaría mal en cuanto la misma persona abriera otra
  pestaña.
- **`current`** es el paso al que apuntar, o `null` cuando no queda nada — así
  la UI nunca tiene que sacar "terminado" de un valor que por lo demás
  significa "haz esto".
- **`ready`** significa que los gastos están entrando solos **ahora mismo**.
  No es "todos los pasos en verde": quien reenvía cada alerta a mano está
  conectado y nunca va a tener una confirmación que mostrar. Lo que sí exige
  es que siga habiendo algún remitente aprobado — vaciar la lista lo devuelve
  a `false` aunque ya hubiera llegado una alerta.
- **`unapproved_senders`** son los remitentes cuyo correo llegó y se descartó
  por no estar aprobado. Es el fallo que desde una pantalla se ve idéntico al
  silencio, y la UI debería ofrecer aprobarlos de un clic (`PATCH
  /identity/inbox` con la lista actual **más** ese remitente). Llega vacío
  cuando `ready`.
- **Los sub-pasos que el backend no puede observar** — copiar la dirección,
  crear el filtro en Gmail — viven en el cliente y no deben bloquear nada: el
  paso verificable que hay detrás se pone en verde solo.
- **Es barato de sondear**: una lectura de un ítem mientras la respuesta
  todavía cambia. Un `refetchInterval` de unos segundos mientras la pantalla
  está abierta y el paso 3 o 4 sigue pendiente, y parar al llegar a `ready`.
  Como no hay push, esta es la única forma de que el checkmark aparezca solo
  mientras el usuario mira. El worker sondea el buzón cada 60 s por defecto,
  así que la confirmación puede tardar ese orden de tiempo en aparecer.
- **404** si la cuenta no tiene inbox. El registro siempre crea uno, así que
  significa que el registro se perdió, no que esté pendiente.

Las etiquetas de los cuatro pasos salen de `GET /ingestion/catalog`
(`setup_steps`), como el resto de vocabularios.

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
- **El usuario sí puede ver qué llegó**, y esa es la pantalla que salva la
  situación cuando algo no funciona. Va justo abajo.

#### `GET /ingestion/notifications`

Lo que llegó al alias del usuario, del más reciente al más antiguo. Es la única
superficie de lectura de Ingestion, y existe para distinguir tres fallos que
desde el cliente se ven idénticos —no aparece ningún movimiento nuevo—:

1. **No llegó nada.** La lista está vacía: la regla de reenvío del usuario no
   está haciendo lo que él cree.
2. **Llegó de un remitente que nadie aprobó.** `status: "ignored"`, con el
   remitente a la vista para poder aprobarlo con un `PATCH /identity/inbox`.
   Este es el caso más común y el más fácil de resolver desde la interfaz.
3. **Llegó y no se pudo leer.** `status: "pending_fallback"`, y
   `deferred_reason` dice cuál de las dos cosas pasó: `no_fallback_configured`
   (no hay modelo puesto) o `fallback_found_nothing` (lo leyó y se negó).

```http
GET /ingestion/notifications?limit=50&offset=0
GET /ingestion/notifications?status=ignored
```

Los filtros van **omitidos o con un valor válido**: `status=` vacío es un
`422`, no "sin filtro". Vale para cualquier parámetro de enum de esta API.

```json
{
  "notifications": [
    {
      "id": "8a448922-2641-52b9-bc7f-eecfcc82186c",
      "message_id": "<seed-transfer@finflow.local>",
      "sender": "alertasynotificaciones@an.notificacionesbancolombia.com",
      "subject": "Transferencia realizada",
      "status": "processed",
      "deferred_reason": null,
      "received_at": 1787403660
    }
  ],
  "total": 7,
  "counts": { "processed": 6, "ignored": 1 },
  "limit": 50,
  "offset": 0
}
```

Reglas:

- **Nunca devuelve el cuerpo del correo.** Una lista no lo necesita, y dos de
  los estados ya lo descartaron a propósito. Si la interfaz quiere enseñar "de
  qué era", el asunto y el remitente es todo lo que hay.
- **`counts` cuenta todos los estados del usuario**, filtre o no filtre la
  lista. Es el resumen de la pantalla ("6 procesados, 1 ignorado") y no se
  mueve al estrechar la lista, igual que el badge de comercios.
- **Orden fijo**, del más reciente al más antiguo. `limit` entre 1 y 200 (50 por
  defecto), `offset` desde 0, y `total` es el total **filtrado**.
- **El índice que hay detrás es eventualmente consistente**: un correo recibido
  hace un instante puede tardar un momento en aparecer. No hagas de esta lista
  la confirmación inmediata de un `POST`.
- Está montado **también en producción**, a diferencia del webhook de al lado.

Con esto, la pantalla de "conecta tu banco" se puede cerrar entera: dirección
para copiar, remitentes aprobados, y qué ha llegado de verdad.

### 5. Movimientos

```http
GET /financial/transactions?limit=50&offset=0
GET /financial/transactions?unassigned=true
GET /financial/transactions?account_id=<id>
GET /financial/transactions?origin=bank_alert|manual
GET /financial/transactions?search=EXITO
GET /financial/transactions?merchant_id=<id>
GET /financial/transactions?category=groceries
GET /financial/transactions?from=<epoch>&to=<epoch>
GET /financial/transactions?transfers=include|exclude|only
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
      "stated": null,
      "merchant": {
        "id": "6f2b1d0e-0c0a-4f2e-9a3b-6d5c4e3f2a1b",
        "display_name": "Netflix",
        "category": "subscriptions",
        "needs_review": false
      },
      "transfer": null
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
- **`merchant` es el comercio canónico detrás del texto del banco**, resuelto
  por el contexto Merchant. `TIENDAS ARA 123` y `ARA 900` traen el mismo
  `merchant.id`, que es justo lo que la búsqueda por texto no puede darte.
  `category` sale del mismo vocabulario que `GET /merchants/categories`.
- **`merchant: null` no es un error.** Puede ser que el worker de comercios
  todavía no haya procesado ese avistamiento (dura segundos: las dos colas se
  drenan por separado) o que sea un movimiento manual con un nombre que nadie
  ha visto nunca. El movimiento se lee igual de bien sin comercio.
- **La unión se hace al leer, no se guarda en el movimiento.** Renombrar un
  comercio, mover una grafía o fusionar dos se refleja de inmediato en todo el
  historial; no hay nada que reprocesar ni ninguna pantalla que refrescar dos
  veces.
- `merchant_id` y `category` filtran por la respuesta de Merchant, así que un
  movimiento sin comercio **no** cae en `category=uncategorized`: es
  desconocido, no "sin categorizar". Aparece en el bucket `key: null` del
  resumen.
- **Una `category` que no exista devuelve `422`**, no una lista vacía: en una
  pantalla de dinero, cero es una respuesta creíble y un error de teclado no
  puede parecerse a "no gastaste nada aquí". Un `merchant_id` inexistente sí
  devuelve una lista vacía — decir que no existe filtraría si es de otro.
- `from` **incluye** y `to` **excluye** (epoch en segundos), para que dos
  meses consecutivos nunca compartan un movimiento.

#### Traslados: un correo, dos movimientos

Pagar la tarjeta de crédito desde la cuenta del mismo banco llega en **un solo
correo** y son **dos movimientos**: sale plata de la cuenta y baja la deuda de
la tarjeta, por el mismo monto. Leerlo como uno solo está mal en las dos
direcciones —cargado a la cuenta, la deuda nunca baja; cargado a la tarjeta, un
movimiento saliente **sube** lo que se debe—, así que el backend escribe las dos
mitades y las marca.

```jsonc
// una de las dos filas
{
  "direction": "outgoing",
  "amount": "100000",
  "counterparty": "credit_card *1234",   // texto de máquina: no lo muestres
  "transfer": {
    "id": "c0f6ef69…",                   // el mismo en las dos mitades
    "role": "source",                    // source | destination
    "external": false,                   // el otro lado está en la app
    "counterpart_movement_id": "21e907…",
    "counterpart_instrument_kind": "credit_card",
    "counterpart_last_four": "1234"
  }
}
```

Y la otra forma que puede tener: una mitad sola, cuando la tarjeta se pagó
desde un banco, una billetera o efectivo que esta app no lleva.

```jsonc
{
  "direction": "incoming",
  "amount": "150000",
  "counterparty": "Nequi",               // texto del usuario: sí se muestra
  "origin": "manual",
  "transfer": {
    "id": "9b2c1a…",
    "role": "destination",
    "external": true,                    // no hay otra fila que buscar
    "counterpart_movement_id": null,
    "counterpart_instrument_kind": null,
    "counterpart_last_four": null
  }
}
```

Reglas:

1. **`transfer` no nulo significa que este movimiento no es gasto ni ingreso.**
   Nunca lo sumes a un total tuyo. Los totales del backend ya lo excluyen.
2. **`external` dice cuál de las dos formas tienes delante.** Léelo a él, no
   los tres `counterpart_*`: es la pregunta que la pantalla realmente hace, y
   los tres son `null` exactamente cuando es `true`. No sigas nunca un
   `counterpart_movement_id` sin comprobarlo antes.
3. **`counterparty` cambia de naturaleza con `external`.** En una mitad de un
   par es texto de máquina (`credit_card *1234`): forma parte de la identidad
   del movimiento y no puede reescribirse nunca, así que para la pantalla
   armas la frase con `role` + `counterpart_instrument_kind` +
   `counterpart_last_four` ("Pago a tu tarjeta ···· 1234"). En una mitad
   externa es lo que el usuario escribió ("Nequi", "efectivo") y se muestra
   tal cual — es lo único que nombra el otro lado.
4. **`role: "source"`** es la mitad de donde salió la plata; **`destination`**
   donde llegó — en una tarjeta, su deuda bajando.
5. **Las dos mitades de un par se enrutan por separado.** Si solo declaraste
   una de las dos cuentas, la otra queda `unassigned` y se adopta cuando
   declares la que falta. Es el mismo camino retroactivo de siempre. Una
   mitad externa no: nombra un instrumento de nadie, así que nunca la adopta
   una cuenta declarada después, y por eso su `account_id` es obligatorio
   desde el principio.
6. **`PATCH` del monto, la fecha o la contraparte de una mitad de un par
   responde `409`**: las dos dicen un mismo movimiento y corregir una sola
   dejaría dos saldos que no cuadran. Es un conflicto con el estado, no con la
   forma del cuerpo —por eso 409 y no 400—, y ninguna reescritura de la
   petición lo arregla. Sí se puede cambiar `account_id`, `detach` y la nota.
   **Una mitad externa sí se corrige entera**, porque no hay segunda fila con
   la que quedar desfasada: el `PATCH` responde 200 y mueve el saldo con él.
7. **`transfers`** controla qué se ve: `include` (por defecto en
   `/transactions`), `exclude` (por defecto en `/summary`) y `only`. Si una
   pantalla enseña una cifra del resumen y la lista detrás, pide `exclude` en
   las dos o los números no cuadrarán.
8. **`sort=amount` contesta "mis diez gastos más grandes del mes"** en una
   llamada, en vez de paginar y ordenar en el cliente. Exige `currency` (`400`
   sin ella) por lo mismo que `/summary?order=amount`. Para un reporte pide
   también `transfers=exclude`, o el pago de la tarjeta encabezará la lista.
9. **Un par completo solo lo produce hoy el parser determinista de
   Bancolombia.** El LLM tiene instrucción explícita de responder
   `understood=false` ante un correo así: elegir a ojo cuál de los dos
   instrumentos es el origen es exactamente cómo un saldo se mueve al revés.
   Una mitad externa no la produce ningún correo — la escribe el usuario, con
   `POST /financial/transactions/transfer`.

#### Registrar una mitad externa

```http
POST /financial/transactions/transfer
```

```jsonc
{
  "role": "destination",      // destination = la deuda de esa cuenta baja
  "amount": "150000",         // string, nunca número JSON
  "currency": "COP",
  "occurred_at": 1787500000,  // epoch en segundos
  "counterparty": "Nequi",    // cómo llama el usuario al otro lado
  "account_id": "…",          // OBLIGATORIO, a diferencia del alta manual
  "bank": "Bancolombia",
  "note": null
}
```

Responde `201` con el movimiento ya con su bloque `transfer` externo. Notas
para la pantalla:

- **No lleva `direction`.** El `role` la fija: `source` es plata saliendo,
  `destination` plata entrando, siempre. Es lo que impide registrar un pago
  que *sube* la deuda en vez de bajarla.
- **`account_id` es obligatorio** (`422` sin él, `404` si no es una cuenta del
  usuario). El movimiento afirma que un saldo se movió; sin cuenta no hay
  saldo que mover, y nada lo adoptaría después.
- **Es idempotente.** Dos peticiones idénticas (mismo usuario, cuenta, rol,
  monto, instante y contraparte) devuelven `201` con **el mismo movimiento**, y
  la deuda baja una sola vez. El id sale del contenido, como en las alertas, así
  que un doble clic o un reintento por red no duplican el pago. No necesitas
  botón deshabilitado ni token de idempotencia — aunque desactivarlo sigue
  siendo buena UX. Cambiar el monto, el instante, el rol o la contraparte sí es
  otro pago. Ojo: `bank` y `note` **no** entran en la identidad, así que
  reintentar con otra nota devuelve el movimiento original con la nota
  original.
- **Cuándo ofrecerlo**: pagar la tarjeta desde el *mismo* banco no lo necesita
  — ese correo llega solo y escribe las dos filas. Esto es para el otro caso.
- Los valores de `role` salen de `transfer_roles` en `GET /financial/catalog`,
  como cualquier otro vocabulario.

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

Un préstamo, una hipoteca y una inversión pueden además declarar **en qué
condiciones** están, y ahí Finflow calcula lo que ninguna alerta trae:

```http
PUT    /financial/accounts/{id}/loan          condiciones del crédito
PUT    /financial/accounts/{id}/investment    condiciones de la inversión
DELETE /financial/accounts/{id}/financing     dejar de calcular
GET    /financial/accounts/{id}/financing?periods=12   tabla + saldo real hoy
POST   /financial/accounts/{id}/accrue        registrar lo que cobró el corte
POST   /financial/accrue                      lo mismo, todas las cuentas
POST   /financial/accounts/{id}/value         cuánto vale hoy (renta variable)
```

Lo que hay que entender antes de dibujarlo:

* **`informational` es `true` en un préstamo y en una hipoteca**, y viene en
  cada `AccountResponse` y en cada opción de `account_kinds` del catálogo. Esas
  cuentas **no entran** en `net_worth`, ni en lo que la app reporta como
  deuda, ni en ningún total de `/summary`, `/trends` o `/history`. Su saldo se
  muestra en su propia tarjeta y en ningún agregado. La tarjeta de crédito
  **sí** entra: lo que debe es el gasto del mes sin pagar.
* Los cobros de cada corte (`POST .../accrue`) quedan como movimientos con
  `origin: "accrual"`. Salen en `/transactions` como cualquier otro y no
  suman en ningún total, por la regla de arriba.
* `POST .../accrue` se puede llamar cuantas veces se quiera: cada cobro se
  identifica por su cuenta y su periodo, así que el segundo intento lo rechaza
  el propio ledger. `skipped` dice cuántos.
* `GET .../financing` responde `409` cuando la cuenta no ha declarado
  condiciones: no es un error, es el formulario que falta. `periods_due`
  mayor que cero significa que hay cortes cerrados sin registrar y que
  `payoff` está corto por ellos.
* `POST .../value` **no es** `PUT .../balance`. El primero deja la diferencia
  como movimiento (la ganancia se ve); el segundo la esconde dentro del saldo
  inicial y el rendimiento sale siempre en cero.
* Las tasas van como **fracción** (`0.1956`, no `19.56`) y con su base
  (`effective_annual`, `nominal_annual`, `monthly`). No son intercambiables.

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
POST   /financial/transactions          { direction, amount, currency, occurred_at, counterparty, account_id?, bank?, note? }
PATCH  /financial/transactions/{id}     { amount?, currency?, occurred_at?, counterparty?, note?, account_id?, detach? }
DELETE /financial/transactions/{id}     -> { erased: [id], accounts: [AccountResponse] }
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
- **Borrar un movimiento le devuelve al saldo lo que se llevó.** `DELETE`
  borra la fila y deshace su efecto en el saldo, en la misma escritura: un
  gasto de 2.000 borrado son 2.000 que la cuenta vuelve a tener. **No es lo
  mismo que `detach`**, que solo lo saca de la cuenta y lo deja contando en
  los totales.
- **Lo que borrar hace depende del tipo de movimiento, y hay que decírselo al
  usuario antes de confirmar.** Son cuatro casos y ninguno se deduce del botón:
  sin cuenta no se mueve ningún saldo; en un activo vuelve la plata; en un
  pasivo **baja la deuda** si era un gasto y **sube** si era un pago (leer
  `direction` sin mirar `category` da la frase al revés); y un traslado no
  tocaba los totales del mes, así que borrarlo tampoco. El frontend lo resuelve
  en `frontend/src/lib/deletion.ts`, que está probado caso por caso.
- **Un traslado se borra entero.** Borrar cualquiera de las dos mitades borra
  la otra y ajusta los dos saldos — media dejaría una fila apuntando a un
  movimiento que ya no existe. Por eso `erased` es una **lista**: quita del
  caché todos los ids que trae, no solo el que pediste. Una pata pagada desde
  fuera de la app (`transfer.external`) no tiene segunda fila y va sola.
- **La respuesta trae las cuentas ya recalculadas** en `accounts`, para
  redibujar un saldo sin una segunda llamada. Viene vacía cuando el movimiento
  no estaba en ninguna cuenta, que es justo cuando ningún saldo cambió.
- **Borrar dos veces es `404`.** No es idempotente en el sentido de HTTP: la
  segunda vez no hay nada que borrar, y el saldo ya se movió una sola vez.
- **Borrar libera la identidad.** Como el id sale del contenido, un pago
  borrado por error se puede volver a registrar y cae en la misma fila.
- **Un movimiento manual sí puede traer `merchant`**, si su `counterparty`
  coincide con una grafía que ya conoce algún comercio del usuario: la unión
  es una búsqueda por huella, no requiere un avistamiento nuevo. Lo que **no**
  hace una entrada a mano es *crear* un comercio, así que un nombre que nunca
  llegó por correo se queda sin comercio para siempre. Merece la pena avisarlo
  al escribir: escribir el nombre tal como lo manda el banco es lo que hace
  que el gasto acabe en el mismo sitio.

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
- **Ojo con qué comercio devuelve cada acción**, porque no siempre es el del
  path: `move` devuelve el comercio **destino**, `split` el comercio **nuevo**
  que acaba de crear, y `merge`, `confirm` y `PATCH` sí devuelven el del path.
  Un cliente que meta la respuesta en la caché del comercio que abrió la
  pantalla se pintaría a sí mismo con los datos de otro; invalida la familia
  entera en vez de escribir una clave.
- `fingerprint` acepta tanto la huella que viene en la lista de alias como el
  texto crudo detrás de ella.
- **Quitarle a un comercio su último alias → `409`**, y eso vale tanto para
  `split` como para `move`: los dos lo dejarían sin ninguno, y un comercio sin
  alias no existe. Comprobado contra la API: ambos responden
  `'<grafía>' is the only way to reach this merchant`. La UI debería negarse
  antes de llamar —el frontend lo hace en `merchants/edits.ts::lastAliasBlocker`—
  y ofrecer las dos salidas que sí funcionan: renombrar ese comercio, o
  fusionarlo con el que debía ser.
- **Fusionar con uno mismo → `400`.** En un merge, el del path sobrevive.
- **`times_seen` cuenta apariciones del nombre, no dinero.** El gasto lo
  responde Financial: `GET /financial/summary?group_by=merchant` para el
  reparto, o `GET /financial/transactions?merchant_id=<id>` para los
  movimientos de un comercio.

### 9. Resumen de gasto

Lo que el cliente no debería calcular paginando: totales de un periodo y su
desglose. Un año de historial son miles de movimientos y `limit` llega a 200.

```http
GET /financial/summary?group_by=day|week|month|weekday|category|merchant|account
GET /financial/summary?from=<epoch>&to=<epoch>
GET /financial/summary?group_by=category&from=<epoch>&to=<epoch>
GET /financial/summary?timezone=America/Bogota

# Para reportes: la dona de ocho quesitos, y el delta contra el periodo anterior
GET /financial/summary?group_by=category&currency=COP&order=amount&top=8
GET /financial/summary?group_by=category&from=<epoch>&to=<epoch>&compare=true
```

Acepta **los mismos filtros que `/financial/transactions`** (`account_id`,
`unassigned`, `origin`, `search`, `merchant_id`, `category`, `from`, `to`,
`transfers`), y eso es a propósito: un bucket del resumen se abre repitiendo
la misma consulta contra `/transactions` con la `key` del bucket. La
excepción es `group_by=weekday`: no hay filtro para "todos los lunes", así
que esos buckets no se abren.

Con **una diferencia deliberada en el valor por defecto**: `transfers` es
`exclude` aquí y `include` en la lista. Un traslado entre cuentas propias no es
gasto ni ingreso, y contarlo reportaría el pago de una tarjeta como el mayor
gasto del mes y otra vez como ingreso en la tarjeta. Si una pantalla enseña una
cifra de aquí y la lista detrás, pide `transfers=exclude` en las dos. `only`
responde la pregunta contraria: cuánto moví entre mis propias cuentas.

`GET /financial/history` hace lo mismo sin preguntarlo: sus totales por mes
excluyen los traslados, pero el patrimonio de cada mes **sí** los replica —
porque el pago de la tarjeta movió los dos saldos de verdad.

```json
{
  "group_by": "category",
  "timezone": "America/Bogota",
  "totals": [
    {
      "currency": "COP",
      "incoming": "3200000",
      "outgoing": "845300",
      "net": "2354700",
      "movements": 27
    }
  ],
  "groups": [
    {
      "key": "groceries",
      "label": "groceries",
      "totals": [
        {
          "currency": "COP",
          "incoming": "0",
          "outgoing": "412000",
          "net": "-412000",
          "movements": 11
        }
      ],
      "movements": 11
    },
    {
      "key": null,
      "label": "Unattributed",
      "totals": [
        {
          "currency": "COP",
          "incoming": "0",
          "outgoing": "120000",
          "net": "-120000",
          "movements": 3
        }
      ],
      "movements": 3
    }
  ]
}
```

Reglas:

- **`totals` es el total del periodo filtrado**, ya sumado: no hace falta
  recorrer `groups` para pintar la cifra grande.
- **Nunca se suman dos monedas.** Tanto `totals` como cada bucket traen una
  entrada por moneda. Si el usuario tiene COP y USD, son dos cifras, no una
  convertida — no hay tasa de cambio en ninguna parte del backend.
- **`net = incoming - outgoing`**, con signo. Negativo es un mes que gastó más
  de lo que entró. `incoming` y `outgoing` siempre son positivos.
- **`key: null` es el bucket que la agrupación no pudo colocar**: un movimiento
  sin cuenta (`group_by=account`) o una contraparte que ningún comercio
  reclama —todavía, o nunca, si es un nombre que solo existe en una entrada
  manual— (`group_by=category|merchant`). Píntalo: sin él los buckets
  dejan de sumar `totals`. `label` trae `Unassigned` o `Unattributed`,
  pensados para traducirse en el cliente.
- **`label`**: para `merchant` es el nombre que el usuario le puso al comercio,
  para `account` el nombre de la cuenta, y para `month` y `category` es igual
  que la `key` (formatea `2026-08` y traduce `groceries` en el cliente, con el
  vocabulario de `GET /merchants/categories`).
- **Orden**: los tramos de tiempo (`day`, `week`, `month`) vienen del más
  reciente al más antiguo, `weekday` viene de lunes a domingo, y el resto
  viene de mayor a menor por número de movimientos. `order=amount` lo cambia a
  monto, pero **exige `currency`**: sin fijar una, el ranking estaría
  decidiendo que 100 USD es menos que 5 000 COP. Sin `currency` responde `400`.
- **`timezone` solo afecta a las agrupaciones de tiempo, y sí importa.** Una
  compra a las 8pm del 31 en Bogotá es el día 1 del mes siguiente en UTC. Por
  defecto `America/Bogota`; una zona que no exista devuelve `400`.
- **`from` incluye y `to` excluye**, igual que en la lista.
- Sin movimientos: `totals` y `groups` vacíos. Es una respuesta válida.

Y tres parámetros que existen para la pantalla de reportes:

- **`currency=COP|USD`** fija la respuesta a una moneda. Es lo que hace
  contestables a los otros dos: aquí nada convierte entre monedas.
- **`top=8` se queda con esos buckets y suma el resto en `others`.** `others`
  **no** viene dentro de `groups`, es un campo aparte, y su `key` es `null` a
  propósito: a diferencia de un bucket real no se puede abrir como la lista
  que hay detrás. `folded` dice cuántos buckets representa. `groups` + `others`
  siempre suma `totals`. Sobre un tramo de tiempo responde `400`: un rango se
  recorta con `from`/`to`, no plegando los días más viejos.
- **`compare=true`** corre además la ventana de igual duración inmediatamente
  anterior, y cada bucket trae `previous_totals`. Necesita `from` **y** `to`
  —sin una duración no hay ventana anterior— y devuelve `400` sin ellos. Tres
  detalles que importan al pintarlo:
  - `previous_totals: null` es "no se comparó"; `previous_totals: []` es
    "se comparó y no hubo nada". No los pintes igual: el primero no es una
    caída a cero.
  - **Una categoría que dejó de aparecer sigue en la respuesta**, con
    `totals: []` y su `previous_totals` lleno. Es justo lo que un reporte
    existe para enseñar; si la escondes, escondes la caída.
  - Sobre una agrupación de tiempo trae el total del periodo y **nada** por
    bucket (`previous_totals: null` en cada uno): `2026-08` contra `2026-07`
    son dos meses distintos, no un mes dos veces.
  - `previous_starts_at` / `previous_ends_at` dicen exactamente qué ventana
    se comparó, para rotularla.

### 10. Tendencias (la pantalla de reportes)

Lo que `/summary` no contesta: **dos dimensiones a la vez**. Restaurantes
contra transporte contra mercado, mes a mes — la gráfica apilada de un reporte.
Pedírselo a `/summary` un mes por llamada son doce viajes cuyos buckets pueden
venir ordenados distinto cada uno, así que ni siquiera se pueden apilar sin
reconciliarlos antes.

```http
GET /financial/trends?interval=month&dimension=category&periods=12
GET /financial/trends?interval=week&dimension=none&periods=8
GET /financial/trends?dimension=merchant&currency=COP&order=amount&series=8
GET /financial/trends?from=<epoch>&to=<epoch>&interval=day
```

```json
{
  "interval": "month",
  "dimension": "category",
  "timezone": "America/Bogota",
  "starts_at": 1780290000,
  "ends_at": 1788325634,
  "buckets": [
    { "key": "2026-07", "starts_at": 1782882000, "ends_at": 1785560400, "partial": false },
    { "key": "2026-08", "starts_at": 1785560400, "ends_at": 1788238800, "partial": false },
    { "key": "2026-09", "starts_at": 1788238800, "ends_at": 1788325634, "partial": true }
  ],
  "series": [
    {
      "key": "groceries",
      "label": "groceries",
      "points": [
        { "bucket": "2026-07", "totals": [] },
        { "bucket": "2026-08", "totals": [
            { "currency": "COP", "incoming": "0", "outgoing": "412000",
              "net": "-412000", "movements": 11 }
        ] },
        { "bucket": "2026-09", "totals": [] }
      ],
      "totals": [ { "currency": "COP", "incoming": "0", "outgoing": "412000",
                    "net": "-412000", "movements": 11 } ],
      "movements": 11
    }
  ],
  "others": null,
  "folded": 0,
  "totals": [ { "currency": "COP", "incoming": "4500000", "outgoing": "474300",
                "net": "4025700", "movements": 8 } ]
}
```

Las dos garantías por las que esto es un endpoint y no doce llamadas:

- **Los buckets son densos y los puntos van alineados por índice.** Cada serie
  trae exactamente un punto por bucket, en el mismo orden, incluidos los
  vacíos. Un mes en el que no pasó nada es un cero (`totals: []`), no un hueco:
  haz `zip(buckets, series[i].points)` y no rellenes nada nunca.
- **Las bandas se ordenan una sola vez sobre todo el rango**, así que todos los
  buckets apilan las mismas bandas en el mismo orden. Ese es el problema que
  hace inservible pedir doce resúmenes sueltos.

Reglas:

- **`dimension=none` es una sola banda, y no sobra**: cada punto ya trae
  `incoming` y `outgoing`, así que esa banda sola es la gráfica de flujo de
  caja (entradas contra salidas por periodo).
- **El rango** es `periods` intervalos hacia atrás desde ahora, o el `from`/`to`
  que pidas **ensanchado a los intervalos que toca**. Medio agosto al lado de
  septiembre entero reporta una caída que no ocurrió, así que el backend
  redondea hacia afuera antes de contar.
- **`partial: true` es el periodo que se está viviendo**, y su `ends_at` es
  *ahora*, no el fin del mes. No lo pintes como un periodo cerrado: es la
  comparación que siempre reporta una caída del 90% el día 3.
- **`series=8` se queda con esas bandas y pliega el resto en `others`**, que
  también viene denso. `key: null` en `others` y en la banda que la dimensión
  no pudo colocar (`Unattributed`, `Unassigned`).
- **Acepta los mismos filtros que `/transactions`**, así que cualquier banda se
  abre como la lista que hay detrás repitiendo la consulta con su `key`. Las de
  `key: null` no.
- **`transfers` es `exclude` por defecto**, como en `/summary`.
- **Un rango de más de 372 buckets responde `400`** en vez de contestar una
  gráfica que nadie puede leer. Pide un intervalo más ancho.

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
13. Nunca contar un traslado (`transfer` no nulo) como gasto ni como ingreso:
    la plata cambió de lado dentro de las cuentas del usuario y el patrimonio
    no se movió.
14. Nunca mostrar la mitad de un traslado como si fuera un movimiento suelto:
    lleva `counterpart_movement_id` justo para poder llegar a la otra.
15. Nunca sumar una cuenta con `informational: true` al patrimonio, a lo que
    el usuario debe ni a los gastos del mes. El backend ya las deja fuera; la
    interfaz tiene que decir *por qué*, o el saldo de la hipoteca en su
    tarjeta se lee como una contradicción con el "Debes" de arriba.
16. Nunca mandar una tasa como porcentaje (`19.56`). Va como fracción
    (`0.1956`) y con su base; el backend rechaza cualquier valor sobre 10
    justamente porque eso es lo que parece un porcentaje sin convertir.

---

## Vocabularios

**Pídelos a la API, no los copies.** Tres endpoints publican cada lista desde
el mismo enum contra el que el endpoint valida, así que no pueden desfasarse:

| Método | Ruta | Auth | Qué trae |
|---|---|---|---|
| GET | `/financial/catalog` | — | `account_kinds` (cada uno con su `category`), **`instrument_kinds`**, `currencies`, `movement_directions`, `transaction_origins`, `transaction_statuses`, `account_scopes`, `summary_groupings`, `transfer_views` |
| GET | `/ingestion/catalog` | — | `processing_statuses`, `ignored_reasons`, `deferred_reasons`, `instrument_kinds` (el mismo vocabulario, visto desde quien lo recibe) |
| GET | `/merchants/catalog` | — | `categories`, `sorts`, `statuses`, `alias_origins`, `counterparty_kinds` |

Los tres son públicos: describen la forma de la API, no los datos de nadie, y
la pantalla de registro los necesita antes de que exista una sesión. Cada
elemento trae `value` y `label`:

```jsonc
{
  "account_kinds": [
    { "value": "savings",     "label": "Savings",     "category": "asset" },
    { "value": "credit_card", "label": "Credit card", "category": "liability" }
  ]
}
```

`value` es lo que se envía y es estable — se persiste, así que no cambia
aunque se reordenen los miembros. `label` es una comodidad en inglés; si tu
interfaz está en español, construye tus propias etiquetas a partir de `value`.

`category` solo aparece en `account_kinds`, y es derivada: nadie declara una
hipoteca como activo. Sirve para agrupar el desplegable y para decir "dinero
que debes" junto a un saldo, sin duplicar la regla que lo decide.

Llámalos una vez al arrancar la app y cachéalos. Son estáticos.

### Instantánea de referencia

Para leer sin levantar el backend. La API manda; esto es una copia.

| Enum | Valores |
|---|---|
| `direction` | `outgoing`, `incoming` |
| `origin` (movimiento) | `bank_alert`, `manual` |
| `status` (movimiento) | `assigned`, `unassigned` |
| `kind` (cuenta) | `savings`, `checking`, `cash`, `investment`, `credit_card`, `loan`, `mortgage` |
| `category` (cuenta, derivada) | `asset`, `liability` |
| `currency` | `COP`, `USD` |
| `instrument_kind` | `credit_card`, `debit_card`, `savings_account`, `checking_account`, `account` — **no** es `kind` |
| `status` (comercio) | `automatic`, `confirmed` |
| `origin` (alias) | `seed`, `derived`, `suggested`, `manual` |
| `sort` (comercios) | `name`, `last_seen`, `times_seen` |
| `status` (notificación) | `received`, `queued`, `processing`, `processed`, `pending_fallback`, `failed`, `ignored` |
| `deferred_reason` | `no_fallback_configured`, `fallback_found_nothing` — solo junto a `pending_fallback`, y ambos son finales para ese intento |
| `category` (comercio) | `uncategorized`, `groceries`, `restaurants`, `transport`, `fuel`, `shopping`, `entertainment`, `subscriptions`, `utilities`, `health`, `education`, `travel`, `fees`, `transfers`, `income`, `other` |

### `instrument_kind` no es `kind`

Es la distinción que más cuesta caro equivocar, y ahora la API la hace cumplir.

- **`kind`** es lo que el dueño llama a la cuenta: `savings`, `credit_card`…
- **`instrument_kind`** es lo que el **banco** llama a la cosa por la que se
  movió el dinero, y son las únicas palabras que aparecen en una alerta:
  `account`, `debit_card`, `credit_card`, `savings_account`, `checking_account`.

Una cuenta de ahorros se declara con `kind: "savings"`, pero sus
transferencias llegan con `instrument_kind: "account"`. Declararla con
`"savings"` produce una clave que ninguna alerta puede igualar jamás.

Eso **antes se aceptaba en silencio**: la cuenta se creaba sin error y no
adoptaba nada nunca. Hoy `POST /financial/accounts` responde **422**. Ofrece
siempre un desplegable alimentado por `/financial/catalog` → `instrument_kinds`;
nunca un campo de texto.

### Una cuenta real tiene varios instrumentos

Esto es lo segundo que rompe la adopción, y no lo arregla ninguna validación.

Tu cuenta de ahorros manda alertas de **dos formas distintas**: como
`account` con los últimos cuatro de la **cuenta** cuando haces una
transferencia o un QR, y como `debit_card` con los últimos cuatro de la
**tarjeta** cuando compras. Son dos claves diferentes, y los últimos cuatro
tampoco coinciden entre sí.

Hay que enlazar las dos, o la mitad de sus movimientos espera para siempre:

```http
POST /financial/accounts/{id}/instruments
{ "bank": "bancolombia", "instrument_kind": "account",    "last_four": "5261" }

POST /financial/accounts/{id}/instruments
{ "bank": "bancolombia", "instrument_kind": "debit_card", "last_four": "0530" }
```

Enlazar es **retroactivo**: adopta al instante todo lo que ya estaba esperando
bajo esa clave. No hay que reenviar ningún correo.

Diséñalo como "esta cuenta, estas tarjetas", no como un solo campo.

### Cupo de una tarjeta de crédito

En una cuenta de pasivo el **saldo es la deuda**, no el disponible: gastar
`sube` el saldo. Es correcto, y el patrimonio ya resta los pasivos.

El cupo es un campo aparte, y son dos números distintos:

| Campo | Qué es |
|---|---|
| `opening_balance` | lo **gastado** a la fecha de declararla |
| `credit_limit` | el **cupo** total |
| `available` | derivado: `credit_limit − saldo` |

```jsonc
// POST /financial/accounts
{ "name": "Tarjeta", "kind": "credit_card",
  "opening_balance": "200000",   // ya gastado
  "credit_limit": "12000000" }   // cupo

// respuesta -> "balance": "200000", "available": "11800000"
```

Meter el cupo en `opening_balance` deja la tarjeta leyéndose como agotada el
día que se declara. `PUT /financial/accounts/{id}/credit-limit` lo cambia
después (los bancos los mueven); `null` lo borra.

`available` viene **con signo**: una tarjeta sobregirada reporta negativo en
vez de cero, porque es justo el caso que hay que mostrar. Y es `null`, no
`0`, cuando no hay cupo declarado — cero significaría "sin cupo disponible",
que es otra cosa. Los activos siempre lo traen en `null`, y pedirles un
`credit_limit` responde 422.

---

## Referencia de endpoints

| Método | Ruta | Auth | Para qué |
|---|---|---|---|
| GET | `/health` | — | Vivo o no. |
| POST | `/identity/verification/request` | — | Mandar un código de 6 dígitos al correo. `202` siempre. |
| POST | `/identity/verification/confirm` | — | Canjear el código por el token que gasta el registro. |
| POST | `/identity/register` | — | Crear cuenta (necesita `verification_token`); devuelve token y asigna la dirección de reenvío. |
| POST | `/identity/login` | — | Token. |
| POST | `/identity/password/forgot` | — | Mandar un enlace para elegir contraseña nueva. `202` siempre. |
| POST | `/identity/password/reset` | — | Gastar ese enlace. `204`, y corta todas las sesiones. |
| POST | `/identity/password/change` | ✔ | Cambiar la contraseña sabiendo la actual. Devuelve el token que reemplaza al vigente. |
| GET | `/identity/me` | ✔ | El id del usuario del token. |
| GET | `/identity/inbox` | ✔ | Dirección de reenvío + remitentes aprobados. |
| PATCH | `/identity/inbox` | ✔ | Reemplazar los remitentes aprobados. |
| GET | `/financial/catalog` | — | Vocabularios de Financial, para poblar formularios. |
| GET | `/ingestion/catalog` | — | Estados de una notificación, sus razones y los pasos del alta. |
| GET | `/merchants/catalog` | — | Vocabularios de Merchant. |
| GET | `/merchants/categories` | — | Solo categorías. Lo cubre `/merchants/catalog`; se mantiene por compatibilidad. |
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
| PUT | `/financial/accounts/{id}/credit-limit` | ✔ | Fijar, cambiar o borrar el cupo. Solo pasivos. |
| POST | `/financial/accounts/{id}/instruments` | ✔ | Enlazar otro instrumento (retroactivo). |
| POST | `/financial/accounts/{id}/close` | ✔ | Cerrar (no borra). |
| PUT | `/financial/accounts/{id}/loan` | ✔ | Condiciones del crédito: tasa, plazo, corte, cuota y seguros. Solo préstamo e hipoteca. |
| PUT | `/financial/accounts/{id}/investment` | ✔ | Condiciones de la inversión. Sin tasa = renta variable. Solo inversión. |
| DELETE | `/financial/accounts/{id}/financing` | ✔ | Dejar de calcular. Los cortes ya registrados se quedan. |
| GET | `/financial/accounts/{id}/financing` | ✔ | Tabla de amortización, saldo real de hoy y cortes sin registrar. `409` si no hay condiciones. |
| POST | `/financial/accounts/{id}/accrue` | ✔ | Registrar como movimientos lo que cobraron los cortes cerrados. Idempotente. |
| POST | `/financial/accrue` | ✔ | Lo mismo para todas las cuentas financiadas del usuario. |
| POST | `/financial/accounts/{id}/value` | ✔ | Cuánto vale hoy una inversión; la diferencia queda como movimiento. |
| GET | `/financial/net-worth` | ✔ | Patrimonio por moneda. |
| GET | `/financial/summary` | ✔ | Totales del periodo y desglose por día, semana, mes, día de la semana, categoría, comercio o cuenta. `top`, `order=amount` y `compare`. Excluye traslados salvo que pidas otra cosa. |
| GET | `/financial/trends` | ✔ | Serie temporal apilable: una banda por categoría, comercio o cuenta, con buckets densos. La gráfica de la pantalla de reportes. |
| GET | `/financial/transactions` | ✔ | Movimientos con su comercio, con filtros y paginación. Incluye las dos mitades de un traslado. |
| POST | `/financial/transactions` | ✔ | Registrar a mano. |
| GET | `/financial/transactions/{id}` | ✔ | Detalle. |
| POST | `/financial/transactions/transfer` | ✔ | Registrar a mano la mitad de un traslado cuya otra mitad no está en la app. |
| PATCH | `/financial/transactions/{id}` | ✔ | Corregir, mover de cuenta o desasignar. En una mitad de traslado, solo cuenta y nota. |
| DELETE | `/financial/transactions/{id}` | ✔ | Borrarlo y devolverle al saldo lo que se llevó. Un traslado se borra entero. |
| GET | `/ingestion/setup` | ✔ | En qué paso va conectando su banco, y si ya recibe gastos solo. |
| GET | `/ingestion/notifications` | ✔ | Qué llegó al alias del usuario y en qué estado quedó. |
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
| `400` | Moneda que la cuenta no tiene, valor imposible. Código de verificación inválido, enlace de restablecimiento inválido. | Mensaje del `detail`; es accionable. |
| `401` | Sin token, token inválido, vencido, **o emitido antes de un cambio de contraseña**. Trae `WWW-Authenticate: Bearer`. | Ir al login. |
| `403` | Registro sin `verification_token` usable (falta, venció, ya se gastó, o es de otra dirección), o la contraseña actual equivocada en `password/change`. | Volver al paso del código, o marcar el campo. **Nunca** cerrar la sesión: el token está bien. |
| `404` | No existe **o es de otro usuario**. | "No encontrado". Nunca "no tienes permiso". |
| `409` | El estado lo impide: cuenta duplicada, cuenta cerrada, movimiento ya asignado, último alias, mitad de un traslado que se intenta corregir sola. | Ofrecer la salida (abrir la existente, fusionar…). |
| `410` | El código de verificación venció. | "Pide uno nuevo", y volver a `verification/request`. |
| `422` | Esquema o regla del payload. `detail` es lista. | Marcar el campo; `loc` dice cuál. |
| `429` | Demasiado correo a esa dirección, o demasiados intentos con un código. Trae `Retry-After` en segundos, expuesto por CORS a propósito — es el único dato que da. | Mostrar la espera. No reintentar solo. |
| `502` | No se pudo entregar el correo al servidor de correo. | "Inténtalo en un momento". No es culpa del usuario. |

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
| **Tiempo real** | Sin websockets ni SSE. Polling o refresco manual. |
| **Refresh token / logout** | Sesión = token guardado en el cliente; al vencer, login otra vez. |
| **Borrado de cuentas** | No hay `DELETE` de cuentas: cerrar es lo que hay, y es deliberado — una cuenta cerrada sigue explicando su historia. Un **movimiento** suelto sí se borra (`DELETE /financial/transactions/{id}`). |
| **Paginación por cursor** | `limit`/`offset` solamente; `total` es el filtrado. |
| **Instrumentos legibles** | `AccountResponse.instruments` viene como la clave que se guarda —`11:bancolombia\|10:debit_card\|4:0530\|`, con la longitud delante de cada parte—, no como campos. Para enseñarla hay que decodificarla en el cliente (`frontend/src/accounts/instruments.ts`); si algún día publica `bank`/`instrument_kind`/`last_four`, ese módulo sobra. |

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

**7. Ver el comercio y el resumen.** Con `just merchant-worker` corriendo, el
movimiento ya trae su comercio canónico y el resumen sabe repartir el gasto.

```bash
curl -s "http://localhost:8000/financial/transactions" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

curl -s "http://localhost:8000/financial/summary?group_by=category" \
  -H "Authorization: Bearer $TOKEN" | python3 -m json.tool
```

Si `merchant` viene `null`, el worker de comercios todavía no ha drenado su
cola: espera unos segundos y repite. El movimiento se lee igual sin él.

**8. Comprobar el aislamiento entre usuarios.** Registra un segundo usuario,
pide su lista con su propio token y confirma que **no ve nada** del primero; y
que pedir por id una cuenta del primero responde `404`, no `403`.

```bash
OTHER=$(curl -s -X POST http://localhost:8000/identity/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"otro@example.com","password":"otra frase larga de verdad"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

curl -s http://localhost:8000/financial/accounts -H "Authorization: Bearer $OTHER"
```

**9. Probar los rechazos**, que son la mitad del contrato:

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
