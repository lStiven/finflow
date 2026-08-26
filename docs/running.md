# Levantar, probar y desplegar Finflow

Guía operativa. Para entender **qué** hace el sistema, ver
[overview.md](overview.md). Para probar la API a mano sin `curl`, hay una
colección de Postman/Bruno lista para importar en
[postman/](postman/README.md). Para integrar un cliente contra este backend
—qué llamar, en qué orden y con qué reglas— está
[frontend-integration.md](frontend-integration.md).

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

`API_CORS_ORIGINS` ya trae los puertos de dev habituales
(`http://localhost:5173,http://localhost:3000`): son los orígenes desde los que
un navegador puede llamar a esta API. Si tu frontend escucha en otro puerto,
añádelo a esa lista. Vacío significa ninguno, que es lo correcto cuando el
frontend se sirve desde el mismo origen. Ver
[frontend-integration.md](frontend-integration.md#cors-qué-origen-puede-llamar).

### 2. Infraestructura

```bash
just aws-init
```

Arranca moto y crea todo: cinco tablas de DynamoDB, las colas con sus DLQ, el
bus de eventos y las reglas que lo conectan. Es idempotente, se puede repetir.

Cada tabla queda con **point-in-time recovery** activado (ver
[Copias de seguridad](#copias-de-seguridad)); en local no sirve de nada, pero
el camino es el mismo que en producción, que es el punto.

Las URLs que imprime ya están en `.env.example` — el id de cuenta de moto es
siempre `123456789012`, así que son predecibles y no hay que copiar nada.

Para ver qué existe realmente en cualquier momento: `just aws-status`.

### 3. Datos de prueba

moto guarda todo en memoria: cuando el emulador se para —`just aws-down`, un
reinicio del contenedor, un reinicio de la máquina— desaparecen las tablas, las
colas y todos los registros. **Nada de lo que crees en local se conserva.**

En vez de hacer ese dato duradero, `just seed` lo hace barato de recrear:

```bash
just seed
```

Recorre la cadena entera —registra al usuario demo, aprueba el dominio del
banco, reenvía seis alertas de Bancolombia, drena los tres workers y declara
las cuentas que las adoptan— y deja una cuenta en la que hay algo que mirar. No
necesita `just dev` ni los workers levantados: monta la aplicación en su propio
proceso.

Repetirlo es seguro. Las alertas llevan un `message_id` fijo y las deduplica la
ingesta; las cuentas y los movimientos manuales se consultan antes de
escribirlos, porque un movimiento manual tiene identidad aleatoria y si no se
duplicaría en cada pasada. Dos ejecuciones seguidas dejan los mismos saldos.

El modelo queda **sin conectar** a propósito —`--with-llm` lo deja puesto—:
las seis alertas son de las que el parser determinista lee, así que sembrar no
cuesta nada ni necesita red. Por eso los workers avisan `no model configured`
al arrancar; aquí es lo esperado.

Lo que deja sembrado:

| | |
|---|---|
| `demo@finflow.local` / `una frase larga de verdad` | La cuenta. El token sale impreso al final, listo para pegar en Postman. |
| 3 cuentas declaradas | Tarjeta de crédito, ahorros —con la tarjeta débito enlazada como segundo instrumento— y efectivo. |
| 8 movimientos | Seis venidos de alertas y dos a mano. Uno queda **sin asignar**: la nómina, porque esa alerta no nombra los últimos cuatro dígitos de ninguna cuenta. |
| 6 comercios | Todos en revisión, agrupados solo con las reglas deterministas. |

Las cuentas se declaran **después** de que llegan las alertas, a propósito: la
adopción es retroactiva y así queda ejercitada.

`--email` y `--password` cambian la cuenta que crea, por si quieres sembrar dos
usuarios y comprobar que cada uno solo ve lo suyo.

### 4. Los cinco procesos

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

### 5. Probar el camino completo

Sin una cuenta de Gmail real configurada, el webhook local hace las veces del
`ingest worker`: reemplaza "el usuario reenvía y el worker lo recoge" por una
llamada directa, sin tocar IMAP.

Esto es, request a request, lo mismo que hace `just seed`. Vale la pena hacerlo
a mano una vez para ver dónde se detiene cada pieza; después, sembrar.

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

Eso muestra el comercio. Para el dinero, el movimiento está registrado pero
**sin asignar**: nadie ha declarado todavía una cuenta para esa tarjeta, y eso
es correcto — Finflow funciona así para quien solo quiere ver qué entra y qué
sale.

```bash
curl -s "http://localhost:8000/financial/transactions?unassigned=true" \
  -H "Authorization: Bearer $TOKEN"
```

**Declara la cuenta y adopta lo que ya llegó.** Es retroactivo: el saldo se
recalcula con las alertas que estaban esperando.

```bash
curl -X POST http://localhost:8000/financial/accounts \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"name":"Tarjeta Bancolombia","kind":"credit_card","currency":"COP",
       "bank":"Bancolombia","instrument_kind":"credit_card","last_four":"1234"}'

curl -s http://localhost:8000/financial/accounts -H "Authorization: Bearer $TOKEN"
curl -s http://localhost:8000/financial/net-worth -H "Authorization: Bearer $TOKEN"
```

**Registra a mano lo que el banco no anuncia** —un pago automático, efectivo—
y corrígelo si hace falta:

```bash
curl -X POST http://localhost:8000/financial/transactions \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"direction":"outgoing","amount":"89900","currency":"COP",
       "occurred_at":1787500000,"counterparty":"NETFLIX","note":"cobro automatico"}'
```

La colección de [Postman](postman/README.md) trae los once requests de la
carpeta *Financial* con sus descripciones.

Y esta es la salida del propio `financial-worker` en una pasada real, con la
misma compra entregada dos veces:

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
| `applied` | Cayó en una cuenta declarada y movió su saldo. |
| `unassigned` | Nadie ha declarado una cuenta para esa tarjeta, o la alerta no nombró una que Financial pudiera usar. El movimiento se guarda igual y se adopta en cuanto exista la cuenta. Es lo esperado, no un fallo. |
| `duplicate` | Ese movimiento ya estaba en el ledger. No se aplicó nada: ni fila, ni saldo — por eso `account_id` viene vacío. |

Los `domain_event` de arriba son la otra mitad de la historia. Deliberadamente
**no se registra ni el monto, ni la contraparte, ni los dígitos** — juntos son una línea del historial de gastos de alguien, y un log
no es sitio para eso.

### 6. Herramientas de inspección

```bash
just inspect     # bandejas, notificaciones y profundidad de la cola
just events      # eventos de integración que llegaron al bus (--follow para seguir)
just aws-status  # tablas, colas y buses que existen ahora mismo
```

`just events` es especialmente útil: publicar en un bus sin regla tiene éxito y
no entrega nada, así que es la única forma de comprobar que un evento salió de
verdad.

`just inspect` mira el despliegue entero, sin autenticación y desde la máquina
que lo corre. La vista equivalente para un usuario —solo su propio correo— es
`GET /ingestion/notifications`, y esa sí está montada en producción.

### 7. Antes de dar por terminado un cambio

```bash
just prepare   # formato + lint + tipos + tests
just fix       # arregla formato y lint automáticamente
```

---

## Desarrollo contra AWS real

Un tercer entorno, entre el emulador y producción: **una cuenta de AWS de
verdad, con todos los recursos prefijados `dev-`**. Existe porque on-demand
cobra por petición, y probar contra producción para no pagar dos veces es
exactamente lo que no hay que hacer.

`ENVIRONMENT=development` es todo el mecanismo. El prefijo se aplica **en
código**, no configurando nueve nombres a mano:

| Recurso | local / producción | development |
|---|---|---|
| Tablas | `bank_notifications`, `user_inboxes`, `users`, `merchants`, `financial` | `dev-` + cada una |
| Colas | `parse-notifications`, `merchant-events`, `financial-events`, `integration-events` | `dev-` + cada una |
| Bus | `finflow` | `dev-finflow` |

Que sea central es el punto: ocho nombres bien y uno olvidado es el caso que
cuesta datos reales, y nada lo reportaría. Las reglas de EventBridge no llevan
prefijo porque su nombre es único **por bus**, y el bus ya está separado.

Producción conserva los nombres desnudos que ya creó: activar este entorno no
toca nada de lo que hay.

```bash
cp .env.development.example .env.development
# edita AWS_PROFILE y IDENTITY_JWT_SECRET
just provision-dev          # crea los recursos dev-*
# pega las cuatro URLs de cola que imprime en .env.development
just run-dev                # la API contra dev
just ingest-worker-dev      # y sus workers, todos con sufijo -dev
```

El webhook local (`POST /ingestion/bank-notifications`) **no** se monta aquí:
solo existe con `ENVIRONMENT=local`. En dev los correos entran por donde
entran en producción — el `ingest-worker` leyendo el buzón por IMAP.

### Comprobar que los datos cuadran

```bash
just verify                    # contra el emulador
just verify .env.development   # contra la cuenta dev
```

Recorre el flujo entero con **dos usuarios**: registra, aprueba el remitente
del banco, reenvía sus alertas, drena los tres workers, declara las cuentas
(que adoptan retroactivamente lo que ya había llegado), coloca a mano el pago
de nómina —su plantilla no trae dígitos, así que nada puede emparejarlo solo—,
registra un movimiento manual y renombra un comercio. Después comprueba dos
familias de propiedades:

- **Integridad**: cada saldo es igual a los movimientos que tiene detrás; el
  patrimonio es activos menos pasivos; un movimiento asignado está en una
  cuenta que responde a su instrumento; los buckets del resumen suman su
  total.
- **Aislamiento**: ninguno de los dos usuarios llega a lo del otro — ni
  listando, ni buscando, ni pidiendo un id conocido, que responde `404` y no
  `403`. También comprueba que no pueda **escribir** sobre lo ajeno.

Sale con código 1 si algo falla, así que sirve en CI. Se **niega a correr con
`ENVIRONMENT=production`**: registra dos usuarios con una contraseña que está
escrita en este repositorio.

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
`INGESTION_INGEST_MAILBOX_APP_PASSWORD`, `LLM_API_KEY`— no van en el fichero:
van en **SSM Parameter Store**, y el fichero solo guarda una referencia.

```bash
just secret-put /finflow/production/jwt-secret "$(python3 -c 'import secrets;print(secrets.token_hex(32))')"
```

Imprime la línea que hay que pegar en `.env.production`:

```
IDENTITY_JWT_SECRET=ssm:/finflow/production/jwt-secret
```

Cualquier valor que **no** empiece por `ssm:` se toma como el secreto mismo,
que es lo que mantiene el desarrollo local con valores planos. La conversión es
por secreto, no global, así que `grep ssm: .env.production` responde cuáles ya
están fuera del fichero. Se leen una vez por proceso: rotar uno exige
reiniciar.

Los parámetros estándar son gratuitos (hasta diez mil) y se cifran con KMS —
por eso Parameter Store y no Secrets Manager, que cobra por secreto y mes a
cambio de una rotación automática que aquí nadie usa todavía.

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

### Copias de seguridad

`just provision-prod` deja las cinco tablas con **point-in-time recovery**
(PITR) activado. Significa que AWS registra cada cambio de forma continua y
puedes devolver una tabla al estado exacto que tenía en cualquier segundo de
los últimos 35 días.

No protege sobre todo contra un fallo de AWS, que es raro. Protege contra la
forma normal en que se pierden datos: borrar la tabla equivocada creyendo que
era la de pruebas, un despliegue con un bug que corrompe saldos durante unas
horas, un script de limpieza apuntando al entorno que no era.

Importa especialmente por la tabla `financial`, que contiene el ledger
completo — cada movimiento de cada usuario desde el primer día. Los saldos se
recalculan a partir de ella, así que perderla no es perder un saldo: es perder
el historial, y no hay forma de reconstruirlo porque los correos originales ya
se procesaron.

Es también lo único de esta guía que **no se puede añadir después**. Una alarma
que faltaba se configura el día que se echa en falta; una tabla que nunca tuvo
copias, no.

**Restaurar** crea siempre una tabla *nueva* — nunca sobrescribe la original,
que es lo que te deja comparar antes de decidir:

```bash
aws dynamodb restore-table-to-point-in-time \
  --profile finflow-production \
  --source-table-name financial \
  --target-table-name financial-restaurada \
  --restore-date-time 2026-09-03T14:32:00Z
```

Tarda del orden de minutos a horas según el tamaño. Cuando la tabla nueva esté
lista y hayas comprobado que trae lo que esperabas, apunta la app hacia ella
con `FINANCIAL_ACCOUNTS_TABLE=financial-restaurada` y reinicia — o renómbrala
tú. El coste es aproximadamente el de almacenar la tabla otra vez; a esta
escala, céntimos.

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
| `Could not read the secret at '/...' from Parameter Store` | La referencia `ssm:` apunta a un parámetro que no existe o al que el rol no tiene acceso. Nunca se degrada a vacío: un secreto de firma ausente tiene que parar el arranque. |
| `AccessDeniedException` al provisionar | Al rol le faltan permisos. Con PITR hacen falta `dynamodb:DescribeContinuousBackups` y `dynamodb:UpdateContinuousBackups` además de los de crear tablas. |
| `AWS_ENDPOINT_URL must be unset when ENVIRONMENT=production` | Estás usando el fichero de entorno equivocado. |
| `no model configured` (aviso, no error) | Falta `LLM_API_KEY`. Los workers siguen funcionando sin plan B. |

**La DLQ** (*dead-letter queue*) es la segunda cola que acompaña a cada cola de
trabajo, con el sufijo `-dlq`. Un worker no borra un mensaje al recogerlo sino
al terminar bien, así que uno que falla siempre reaparecería para siempre,
tapando a los demás. Tras cinco intentos SQS lo aparta ahí. No arregla nada por
sí sola: es lo que convierte "desapareció dinero sin explicación" en "hay 40
mensajes en `parse-notifications-dlq`, algo cambió". Hoy nadie vigila ese
número — `just inspect` lo muestra, pero no hay alarma.

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
| `GET /ingestion/notifications` | Qué correos llegaron y en qué estado quedaron |
| `GET /merchants` | Listado con búsqueda, filtros y contador de revisión |
| `GET /merchants/{id}` | Detalle con todos los alias |
| `PATCH /merchants/{id}` | Renombrar y/o recategorizar |
| `POST /merchants/{id}/confirm` | Aceptar la agrupación tal como está |
| `POST /merchants/{id}/aliases/move` · `/split` · `/merge` | Corregir agrupaciones |
| `GET /merchants/categories` | Vocabulario de categorías |
| `GET /financial/accounts` · `POST` | Listar cuentas con patrimonio neto · declarar una |
| `GET /financial/accounts/{id}` · `PATCH` | Detalle · renombrar |
| `POST /financial/accounts/{id}/instruments` | Enlazar otra tarjeta o número de cuenta |
| `POST /financial/accounts/{id}/close` | Cerrar, conservando historial y saldo |
| `GET /financial/net-worth` | Activos − pasivos, por moneda |
| `GET /financial/transactions` · `POST` | Movimientos con filtros · registrar uno a mano |
| `GET /financial/transactions/{id}` · `PATCH` | Detalle · corregir, mover o desasignar |
| `GET /health` | Sonda de salud |
