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
| **Proyecto de Google Cloud** (cliente OAuth + tema de Pub/Sub) | Sin Gmail. Queda el proveedor simulado, suficiente para desarrollo. |

Nada de esto rompe el arranque: cada pieza ausente apaga su función y lo dice
en el log.

---

## Desarrollo local

Local corre contra **moto**, un emulador de AWS en proceso. No hay Docker
aparte, ni credenciales reales, ni cobros.

### 1. Configuración

```bash
cp .env.example .env
```

Genera los dos secretos que la app exige:

```bash
python3 -c "import secrets; print('IDENTITY_JWT_SECRET=' + secrets.token_hex(32))"
python3 -c "import secrets; print('INGESTION_OAUTH_STATE_SECRET=' + secrets.token_hex(32))"
```

Pega ambos en `.env`. Sin el primero la API **se niega a arrancar** — a
propósito: firmar tokens con un valor conocido es peor que no arrancar.

Si tienes key de Gemini, añádela también (`LLM_API_KEY=...`). Se obtiene en
[aistudio.google.com/apikey](https://aistudio.google.com/apikey).

### 2. Infraestructura

```bash
just aws-init
```

Arranca moto y crea todo: seis tablas de DynamoDB, las colas con sus DLQ, el
bus de eventos y las reglas que lo conectan. Es idempotente, se puede repetir.

Las URLs que imprime ya están en `.env.example` — el id de cuenta de moto es
siempre `123456789012`, así que son predecibles y no hay que copiar nada.

Para ver qué existe realmente en cualquier momento: `just aws-status`.

### 3. Los tres procesos

Cada uno en su terminal:

```bash
just dev              # API en http://localhost:8000 con recarga en caliente
just parse-worker     # SQS -> parser determinista -> Gemini -> EventBridge
just merchant-worker  # EventBridge -> comercios canónicos
```

Los workers avisan al arrancar si no hay modelo configurado. La API expone su
documentación interactiva en `http://localhost:8000/docs`.

> En local se monta además `POST /ingestion/bank-notifications`, un webhook sin
> autenticar que acepta un correo entero. Es una costura de pruebas y **no
> existe fuera de `ENVIRONMENT=local`**.

### 4. Probar el camino completo

Con el proveedor simulado se recorre la cadena entera sin OAuth ni Google.

**Crear una cuenta y guardar el token:**

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/identity/register \
  -H 'Content-Type: application/json' \
  -d '{"email":"yo@example.com","password":"una frase larga de verdad"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")
```

**Conectar un buzón simulado**, con la lista de remitentes aprobados. Es una
sola llamada a propósito: conectar sin filtro dejaría un buzón conectado del
que nunca llegaría nada.

```bash
curl -X POST http://localhost:8000/identity/mailboxes \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"address":"yo@gmail.test","provider":"simulated",
       "allowed_domains":["an.notificacionesbancolombia.com"]}'
```

**Dejar un correo en el buzón** (no se lee todavía — el buzón no es nuestro
hasta que el proveedor avise):

```bash
just mailbox-deliver --address yo@gmail.test \
  --sender alertasynotificaciones@an.notificacionesbancolombia.com
```

`--body` permite escribir la alerta a mano; sin él usa una compra de ejemplo.
Repetir `--message-id` sirve para ejercitar la deduplicación.

**Tocar el timbre**, como haría un proveedor real:

```bash
just mailbox-notify yo@gmail.test
```

Responde `{"fetched":1,"accepted":1,"duplicates":0}`. A partir de ahí los dos
workers hacen el resto.

**Ver el resultado:**

```bash
curl -s http://localhost:8000/merchants -H "Authorization: Bearer $TOKEN"
```

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
`INGESTION_OAUTH_STATE_SECRET`, `LLM_API_KEY`— deberían venir de un gestor de
secretos, no del fichero.

### 2. Crear los recursos

```bash
aws sso login --profile finflow-production   # si usas SSO
just provision-prod
```

Imprime las URLs de las colas y termina con un bloque **`Put this in
.env.production:`**. Esas tres líneas hay que pegarlas:

```
INGESTION_PARSE_QUEUE_URL=...
INGESTION_INTEGRATION_EVENTS_QUEUE_URL=...
MERCHANT_EVENTS_QUEUE_URL=...
```

Solo existen una vez creada la cola, porque la URL incluye el id de cuenta. Es
la razón de que cada cola tenga dos ajustes: el aprovisionamiento conoce el
nombre, la aplicación exige la URL completa.

Comprueba con `just aws-status .env.production`.

### 3. Arrancar

```bash
just run-prod              # uvicorn, sin recarga, puerto 8000
just parse-worker-prod
just merchant-worker-prod
```

### 4. Renovar suscripciones — no es opcional

Una suscripción de Gmail caduca a los 7 días **en silencio**: nada falla, los
correos simplemente dejan de llegar. Hay tres disparadores de renovación, y el
barrido programado es el suelo que cubre los buzones sin actividad:

```bash
just subscriptions-prod --watch
```

Déjalo corriendo como servicio, o programa `just subscriptions-prod` al menos
dos veces por ventana de suscripción (con Gmail, a diario sobra).

### 5. Conectar Gmail

Del lado de Google, y esto el código no puede hacerlo por ti:

1. **Cliente OAuth** de tipo *Aplicación web*, con la URI de redirección
   **exactamente** igual a `INGESTION_GMAIL_REDIRECT_URI`.
2. **Tema de Pub/Sub**, otorgando el rol *Publisher* a
   `gmail-api-push@system.gserviceaccount.com`.
3. **Suscripción push** de ese tema apuntando a
   `https://tu-dominio/ingestion/mailbox-events/gmail`.

Rellena los cuatro `INGESTION_GMAIL_*`. Con uno vacío el proveedor Gmail
sencillamente no existe, en lugar de existir a medias y fallar en la primera
notificación.

Del lado del usuario, el flujo son dos pantallas:

```
GET  /identity/mailboxes/gmail/authorize   -> devuelve la URL de consentimiento
GET  /identity/mailboxes/gmail/callback    -> Google redirige aquí
```

Contrato completo, paso a paso y con los códigos de error, en
[mailbox-connection.md](mailbox-connection.md).

> **App en modo Testing**: mientras la aplicación OAuth siga sin verificar —lo
> que permite a un despliegue pequeño saltarse la evaluación de seguridad de
> pago de Google— los *refresh tokens* caducan cada 7 días y cada usuario tiene
> que volver a autorizar. La API lo expone como `needs_attention: true` en
> `GET /identity/mailboxes`.

---

## Cuando algo falla

El sistema está escrito para fallar ruidosamente en el arranque antes que
degradarse en silencio. Estos son los mensajes que verás y qué significan:

| Mensaje | Qué hacer |
|---|---|
| `IDENTITY_JWT_SECRET is not set` | Genera y pega el secreto. Todo token sería falsificable. |
| `INGESTION_PARSE_QUEUE_URL is not set` | Corre el aprovisionamiento y pega la URL. Si no, se aceptarían notificaciones que nunca se parsearían. |
| `MERCHANT_EVENTS_QUEUE_URL is not set` | Igual, para el worker de comercios. |
| `AWS_ENDPOINT_URL must be unset when ENVIRONMENT=production` | Estás usando el fichero de entorno equivocado. |
| `no model configured` (aviso, no error) | Falta `LLM_API_KEY`. Los workers siguen funcionando sin plan B. |
| `501 No mailbox adapter for provider ...` | Una suscripción apunta a un proveedor sin adaptador. Falla a la vista en vez de tragar correos en silencio. |

Un mensaje que no se puede parsear se borra de la cola; uno que quizá entienda
un despliegue más nuevo se deja, y acaba en la DLQ tras cinco intentos. Revisa
las colas `*-dlq` si algo desaparece sin explicación.

---

## Referencia rápida de endpoints

| | |
|---|---|
| `POST /identity/register`, `POST /identity/login` | Cuenta y token |
| `GET /identity/me` | Quién soy |
| `POST /identity/mailboxes` | Conectar un buzón + remitentes aprobados |
| `GET /identity/mailboxes` | Buzones y cuáles necesitan atención |
| `DELETE /identity/mailboxes` | Desconectar (conserva la posición de lectura) |
| `POST /identity/mailboxes/refresh` | Renovar y sincronizar al abrir la app |
| `POST /identity/mailboxes/backfill` | Leer el mes en curso desde el día 1, una vez |
| `GET /identity/mailboxes/gmail/authorize` · `/callback` | Flujo OAuth |
| `POST /ingestion/mailbox-events/gmail` · `/simulated` | El timbre del proveedor |
| `GET /merchants` | Listado con búsqueda, filtros y contador de revisión |
| `GET /merchants/{id}` | Detalle con todos los alias |
| `PATCH /merchants/{id}` | Renombrar y/o recategorizar |
| `POST /merchants/{id}/confirm` | Aceptar la agrupación tal como está |
| `POST /merchants/{id}/aliases/move` · `/split` · `/merge` | Corregir agrupaciones |
| `GET /merchants/categories` | Vocabulario de categorías |
| `GET /health` | Sonda de salud |
