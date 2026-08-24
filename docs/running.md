# Levantar, probar y desplegar Finflow

Guía operativa. Para entender **qué** hace el sistema, ver
[overview.md](overview.md). Para probar la API a mano sin `curl`, hay una
colección de Postman/Bruno lista para importar en
[postman/](postman/README.md).

Todos los comandos se ejecutan dentro del DevContainer, en
`/workspaces/finflow_v2`. `just --list` es la fuente de verdad de las tareas
disponibles.

---

## Requisitos

### Obligatorios

| | |
|---|---|
| **DevContainer de VS Code** | El entorno canónico. Trae Python 3.13 y el AWS CLI ya instalados. |
| **Python 3.13** | Fijado en `pyproject.toml` (`>=3.13,<3.14`). |
| **uv** | El único gestor de dependencias. Nunca uses `pip`, `poetry`, `venv` ni `conda`. |
| **just** | Atajos del proyecto. |

`uv sync` se ejecuta solo al crear el contenedor; si añades dependencias,
`uv add <paquete>` (o `uv add --group dev <paquete>`).

### Opcionales — cada uno enciende una capacidad

| | Sin él |
|---|---|
| **Cuenta AWS + perfil** | Solo puedes correr en local contra el emulador. |
| **API key de Gemini** | Sin plan B: un correo que ninguna plantilla reconozca se conserva sin parsear, y los comercios se agrupan solo con las reglas deterministas. |
| **Cuenta de Gmail dedicada + App Password** (ver [email-forwarding.md](email-forwarding.md)) | Sin `ingest worker` real: se puede seguir probando el resto de la cadena con el webhook local (`POST /ingestion/bank-notifications`). |

Nada de esto rompe el arranque de la API — excepto la dirección de ingesta
misma, que es obligatoria: sin ella nadie podría registrarse, porque cada
cuenta nueva necesita una dirección de reenvío que derivar.

---

## Desarrollo local

Local corre contra **moto**, un emulador de AWS en proceso. No hay Docker
aparte, ni credenciales reales, ni cobros.

### 1. Configuración

```bash
cp .env.example .env
```

Genera el secreto que la app exige:

```bash
python3 -c "import secrets; print('IDENTITY_JWT_SECRET=' + secrets.token_hex(32))"
```

Pégalo en `.env`. Sin él la API **se niega a arrancar** — a propósito: firmar
tokens con un valor conocido es peor que no arrancar.

Pon también una dirección en `INGESTION_INGEST_MAILBOX_ADDRESS` — cualquier
dirección con forma de correo sirve para desarrollar (registro, login,
`GET/PATCH /identity/inbox`); solo hace falta que sea una cuenta real con su
App Password cuando quieras correr `just ingest-worker` de verdad. Ver
[email-forwarding.md](email-forwarding.md).

Si tienes key de Gemini, añádela también (`LLM_API_KEY=...`). Se obtiene en
[aistudio.google.com/apikey](https://aistudio.google.com/apikey).

### 2. Infraestructura

```bash
just aws-init
```

Arranca moto y crea todo: cinco tablas de DynamoDB, las colas con sus DLQ, el
bus de eventos y las reglas que lo conectan. Es idempotente, se puede repetir.

Las URLs que imprime ya están en `.env.example` — el id de cuenta de moto es
siempre `123456789012`, así que son predecibles y no hay que copiar nada.

Para ver qué existe realmente en cualquier momento: `just aws-status`.

### 3. Los cinco procesos

Cada uno en su terminal:

```bash
just dev               # API en http://localhost:8000 con recarga en caliente
just ingest-worker     # IMAP -> filtrado por remitente -> SQS
just parse-worker      # SQS -> parser determinista -> Gemini -> EventBridge
just merchant-worker   # EventBridge -> comercios canónicos
just financial-worker  # EventBridge -> filas del ledger y saldos
```

`merchant-worker` y `financial-worker` escuchan el mismo evento en colas
distintas y no dependen uno del otro: puedes correr solo el que te interese.
Cada worker avisa al arrancar si le falta configurar algo.

La API expone su documentación interactiva en `http://localhost:8000/docs`.

> En local se monta además `POST /ingestion/bank-notifications`, un webhook sin
> autenticar que acepta un correo entero directamente, sin pasar por ningún
> buzón. Es una costura de pruebas y **no existe fuera de `ENVIRONMENT=local`**
> — en producción nada llama a este webhook por HTTP; el `ingest worker` lee
> el buzón compartido directamente.

### 4. Probar el camino completo

Sin una cuenta de Gmail real configurada, el webhook local hace las veces del
`ingest worker`: reemplaza "el usuario reenvía y el worker lo recoge" por una
llamada directa, sin tocar IMAP.

**Crear una cuenta y guardar el token.** El registro ya asigna la dirección de
reenvío del usuario — no hace falta ningún paso adicional para eso:

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/identity/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"yo@example.com","password":"una frase larga de verdad"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

ADDRESS=$(curl -s http://localhost:8000/identity/inbox \
  -H "Authorization: Bearer $TOKEN" \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['address'])")
```

**Aprobar el remitente del banco.** Es una sola llamada a propósito: sin
remitentes aprobados, la dirección existe pero no acepta nada.

```bash
curl -X PATCH http://localhost:8000/identity/inbox \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"allowed_domains":["an.notificacionesbancolombia.com"]}'
```

**Simular la llegada de un correo reenviado**, directo al webhook local:

```bash
curl -X POST http://localhost:8000/ingestion/bank-notifications \
  -H 'Content-Type: application/json' \
  -d "{\"recipient\":\"$ADDRESS\",
       \"message_id\":\"<demo-1@example.com>\",
       \"sender\":\"alertasynotificaciones@an.notificacionesbancolombia.com\",
       \"subject\":\"Notificación\",
       \"raw_content\":\"Bancolombia: Compraste \$45.000 en EXITO CALI con tu T.Cred *1234, el 20/08/2026 a las 10:15\"}"
```

Responde `{"outcome":"accepted",...}`. A partir de ahí los workers hacen el
resto — corre `just parse-worker`, `just merchant-worker` y
`just financial-worker` en sus terminales para verlo avanzar.

**Ver el resultado:**

```bash
curl -s http://localhost:8000/merchants -H "Authorization: Bearer $TOKEN"
```

Eso muestra el comercio. **El saldo todavía no tiene endpoint**: Financial
escribe la cuenta, la fila del ledger y el saldo, pero el lado de lectura
—listado de cuentas, patrimonio neto— es el siguiente paso del proyecto, y con
él llegará también una tarea de inspección como las de abajo.

Por ahora lo que se ve es la salida del propio `financial-worker`. Esta es una
pasada real con la misma compra entregada dos veces:

```
INFO:...logging_event_publisher:domain_event | event_type='AccountOpened' …
INFO:...logging_event_publisher:domain_event | event_type='AccountFingerprintLinked' …
INFO:...logging_event_publisher:domain_event | event_type='TransactionRecorded' …
INFO:...logging_event_publisher:domain_event | event_type='TransactionAssigned' …
INFO:...logging_event_publisher:domain_event | event_type='AccountBalanceChanged' …
INFO:...sqs_worker:movement recorded | movement_id='5dc2b4eb…' outcome='applied' account_id='ef3bd2d3…'
INFO:...sqs_worker:movement recorded | movement_id='5dc2b4eb…' outcome='duplicate' account_id=None
```

Las dos entregas comparten `movement_id` —la identidad sale del contenido, no
del correo— y la segunda no aplicó nada. `outcome` es lo que hay que mirar:

| | |
|---|---|
| `applied` | Cayó en una cuenta y movió su saldo. |
| `unassigned` | La alerta no nombró una tarjeta que Financial pudiera usar. El movimiento se guarda igual, esperando a que alguien lo coloque. Es lo esperado, no un fallo. |
| `duplicate` | Ese movimiento ya estaba en el ledger. No se aplicó nada: ni fila, ni saldo — por eso `account_id` viene vacío. |

Los `domain_event` de arriba son la otra mitad de la historia: `AccountOpened`
solo aparece la primera vez que se ve una tarjeta, porque la cuenta se abre
sola. Deliberadamente **no se registra ni el monto, ni la contraparte, ni los
dígitos** — juntos son una línea del historial de gastos de alguien, y un log
no es sitio para eso.

### 5. Herramientas de inspección

```bash
just inspect     # bandejas, notificaciones y profundidad de la cola
just events      # eventos de integración que llegaron al bus (--follow para seguir)
just aws-status  # tablas, colas y buses que existen ahora mismo
```

`just events` es especialmente útil: publicar en un bus sin regla tiene éxito y
no entrega nada, así que es la única forma de comprobar que un evento salió de
verdad.

### 6. Antes de dar por terminado un cambio

```bash
just prepare   # formato + lint + tipos + tests
just fix       # arregla formato y lint automáticamente
```

---

## Producción

Misma imagen, mismo código; cambian el fichero de entorno y las credenciales.
`ENV_FILE=.env.production` es lo único que decide contra qué se corre, y
`AWS_ENDPOINT_URL` está **prohibido** con `ENVIRONMENT=production` — un
endpoint de emulador olvidado no puede desviar tráfico real.

### 1. Credenciales

```bash
cp .env.production.example .env.production
aws configure --profile finflow-production
```

`.env.production.example` está en git y es una plantilla; `.env.production` está
ignorado. **Ninguna clave se escribe en ninguno de los dos**: en local vienen
del perfil de `~/.aws`, y desplegado del rol de la instancia o la tarea (deja
`AWS_PROFILE` sin valor en ese caso).

Los secretos de la aplicación —`IDENTITY_JWT_SECRET`,
`INGESTION_INGEST_MAILBOX_APP_PASSWORD`, `LLM_API_KEY`— deberían venir de un
gestor de secretos, no del fichero.

### 2. Crear los recursos

```bash
aws sso login --profile finflow-production   # si usas SSO
just provision-prod
```

Imprime las URLs de las colas y termina con un bloque **`Put this in
.env.production:`**. Esas cuatro líneas hay que pegarlas:

```
INGESTION_PARSE_QUEUE_URL=...
INGESTION_INTEGRATION_EVENTS_QUEUE_URL=...
MERCHANT_EVENTS_QUEUE_URL=...
FINANCIAL_EVENTS_QUEUE_URL=...
```

Solo existen una vez creada la cola, porque la URL incluye el id de cuenta. Es
la razón de que cada cola tenga dos ajustes: el aprovisionamiento conoce el
nombre, la aplicación exige la URL completa.

Comprueba con `just aws-status .env.production`.

### 3. Arrancar

```bash
just run-prod              # uvicorn, sin recarga, puerto 8000
just ingest-worker-prod
just parse-worker-prod
just merchant-worker-prod
just financial-worker-prod
```

### 4. Configurar la cuenta de ingesta

Esto el código no puede hacerlo por ti — es un paso manual, una sola vez, y no
depende de un usuario en particular:

1. Crea una cuenta de Gmail dedicada a esto (no la tuya personal).
2. Actívale verificación en dos pasos.
3. Genera una **App Password** para ella (Cuenta de Google → Seguridad → Verificación en dos pasos → Contraseñas de aplicaciones).
4. Rellena en `.env.production`:
   ```
   INGESTION_INGEST_MAILBOX_ADDRESS=tu-cuenta-de-ingesta@gmail.com
   INGESTION_INGEST_MAILBOX_APP_PASSWORD=la-app-password-generada
   ```

Con la dirección vacía, la API se niega a arrancar — cada registro nuevo
necesita derivar una dirección de reenvío, así que no hay modo degradado
posible. Con la App Password vacía, la API arranca pero el `ingest worker`
se niega a arrancar él solo.

Paso a paso de cómo cada usuario conecta su banco a esa cuenta, en
[email-forwarding.md](email-forwarding.md).

---

## Cuando algo falla

El sistema está escrito para fallar ruidosamente en el arranque antes que
degradarse en silencio. Estos son los mensajes que verás y qué significan:

| Mensaje | Qué hacer |
|---|---|
| `IDENTITY_JWT_SECRET is not set` | Genera y pega el secreto. Todo token sería falsificable. |
| `INGESTION_INGEST_MAILBOX_ADDRESS is not set` | Sin ella nadie puede registrarse: cada cuenta nueva deriva su dirección de reenvío de esta. |
| `INGESTION_INGEST_MAILBOX_ADDRESS / ..._APP_PASSWORD are not set` (solo el `ingest worker`) | El worker no tiene qué buzón revisar. Configura la cuenta dedicada — ver arriba. |
| `INGESTION_PARSE_QUEUE_URL is not set` | Corre el aprovisionamiento y pega la URL. Si no, se aceptarían notificaciones que nunca se parsearían. |
| `MERCHANT_EVENTS_QUEUE_URL is not set` | Igual, para el worker de comercios. |
| `FINANCIAL_EVENTS_QUEUE_URL is not set` | Igual, para el worker de saldos: sin él ningún movimiento tocaría una cuenta. |
| `AWS_ENDPOINT_URL must be unset when ENVIRONMENT=production` | Estás usando el fichero de entorno equivocado. |
| `no model configured` (aviso, no error) | Falta `LLM_API_KEY`. Los workers siguen funcionando sin plan B. |

Un mensaje que no se puede parsear se borra de la cola; uno que quizá entienda
un despliegue más nuevo se deja, y acaba en la DLQ tras cinco intentos. Esa
distinción es deliberada: un payload que no cumple su esquema nunca va a
cumplirlo, pero una moneda o una versión que este despliegue no conoce puede
ser perfectamente legible para el siguiente, y borrarla destruiría un
movimiento real. Revisa las colas `*-dlq` si algo desaparece sin explicación.

---

## Referencia rápida de endpoints

| | |
|---|---|
| `POST /identity/register`, `POST /identity/login` | Cuenta y token — el registro ya asigna la dirección de reenvío |
| `GET /identity/me` | Quién soy |
| `GET /identity/inbox` | Mi dirección de reenvío y quién está aprobado |
| `PATCH /identity/inbox` | Reemplazar los remitentes aprobados |
| `GET /merchants` | Listado con búsqueda, filtros y contador de revisión |
| `GET /merchants/{id}` | Detalle con todos los alias |
| `PATCH /merchants/{id}` | Renombrar y/o recategorizar |
| `POST /merchants/{id}/confirm` | Aceptar la agrupación tal como está |
| `POST /merchants/{id}/aliases/move` · `/split` · `/merge` | Corregir agrupaciones |
| `GET /merchants/categories` | Vocabulario de categorías |
| `GET /health` | Sonda de salud |
