# Levantar y operar Finflow

Guía **operativa**: arrancar el sistema, crear su infraestructura, mirarla y
desplegarla.

Lo demás vive en otro sitio, a propósito:

| Para | Ver |
|---|---|
| Qué hace el sistema y por qué | [overview.md](overview.md) |
| Qué endpoints hay y en qué orden se llaman | [frontend-integration.md](frontend-integration.md) |
| Probar la API a mano, sin `curl` | [postman/](postman/README.md) |
| Cómo un usuario conecta su banco | [email-forwarding.md](email-forwarding.md) |

Todo se ejecuta dentro del DevContainer, en `/workspaces/finflow_v2`.
`just --list` es la fuente de verdad de las tareas disponibles.

---

## Empezar: dos comandos

```bash
cp .env.example .env       # y edita dos valores — ver "Configurar .env"
just up                    # emulador, recursos, datos demo y los procesos
```

`just up` hace por sí solo lo que antes eran cuatro comandos y cinco
terminales. La API queda en http://localhost:8000/docs.

El resto de esta guía explica cada paso y los otros dos entornos.

---

## Los tres entornos

Un solo código; lo único que cambia es **qué fichero de entorno se lee**. La
variable `ENV_FILE` lo decide, y cada recipe de `just` ya la fija por ti:

| Entorno | Fichero | AWS | Nombres de recursos | Recipes |
|---|---|---|---|---|
| **local** | `.env` | moto, en proceso | desnudos | `just dev`, `just seed`, workers sin sufijo |
| **development** | `.env.development` | cuenta real | prefijo `dev-` | `just run-dev`, `just deploy-dev` |
| **production** | `.env.production` | cuenta real | desnudos | `just run-prod`, `just deploy-prod` |

Los ficheros `.env` configuran lo que corre **desde tu máquina**. Lo que corre
**desplegado** son cinco funciones Lambda, y su configuración vive en
`infra/template.yaml` — ver [Desplegar en Lambda](#desplegar-en-lambda).

**Por qué tres y no dos.** DynamoDB on-demand cobra por petición, así que
probar contra producción para no pagar dos veces es exactamente lo que no hay
que hacer. `development` existe para eso: una cuenta de AWS de verdad, pero
con **todo prefijado `dev-`** para que no pueda tocar los datos de producción
—ni escribir en su tabla, ni *consumir* mensajes de su cola, que es peor
porque el mensaje se borra antes de que producción lo lea. El prefijo lo
aplica el código (`ENVIRONMENT=development`), no nueve variables a mano.

Puede compartir cuenta y perfil de AWS con producción sin riesgo: lo que
separa a los dos es el prefijo, no las credenciales.

**Los ficheros `.example`.** Cada `.env*` tiene su `.env*.example` **sí**
versionado en git; los `.env*` reales están ignorados. El `.example` es la
plantilla comentada: cópiala y rellena lo tuyo. Si no sabes qué significa una
variable, la respuesta está en el comentario de encima en el `.example`.

---

## Local

Local corre contra **moto**, un emulador de AWS en proceso. No hay Docker
aparte, ni credenciales reales, ni cobros.

### 1. Configurar `.env`

```bash
cp .env.example .env
```

Solo hay que tocar dos valores. El resto de la plantilla ya es correcto para
local — incluidas las URLs de las colas, porque el id de cuenta de moto es
siempre `123456789012` y por tanto son predecibles.

**`IDENTITY_JWT_SECRET`** — sin él la API **se niega a arrancar**, a
propósito: firmar tokens con un valor conocido es peor que no arrancar.

```bash
uv run python -c "import secrets; print(secrets.token_hex(32))"
```

**`INGESTION_INGEST_MAILBOX_ADDRESS`** — cualquier dirección con forma de
correo sirve para desarrollar. Es obligatoria porque cada registro deriva de
ella la dirección de reenvío del usuario, así que sin ella nadie podría
crearse una cuenta. Solo hace falta que sea una cuenta de Gmail real, con su
App Password, cuando quieras correr `just ingest-worker` de verdad.

> **El buzón es el único recurso que el prefijo `dev-` no separa.** El
> `ingest worker` busca los correos `UNSEEN` y los marca `\Seen` al
> procesarlos, así que un worker local o de dev apuntando al mismo buzón que
> producción **se come su correo**: producción ya no lo verá. Si vas a correr
> `just ingest-worker` fuera de producción, usa una cuenta de Gmail distinta.
> Para todo lo demás, deja `INGESTION_INGEST_MAILBOX_APP_PASSWORD` vacío: la
> API arranca igual y el worker se niega a arrancar, que es justo lo que
> quieres.

Opcionales:

- **`LLM_API_KEY`** ([aistudio.google.com/apikey](https://aistudio.google.com/apikey))
  — sin ella no hay plan B: un correo que ninguna plantilla reconozca se
  conserva sin parsear, y los comercios se agrupan solo con reglas
  deterministas.
- **`API_CORS_ORIGINS`** — ya trae los puertos de dev habituales
  (`http://localhost:5173,http://localhost:3000`). Si tu frontend escucha en
  otro puerto, añádelo. Vacío significa ninguno, que es lo correcto cuando el
  frontend se sirve desde el mismo origen. Ver
  [frontend-integration.md](frontend-integration.md#cors-qué-origen-puede-llamar).

### 2. Infraestructura

```bash
just aws-init
```

Arranca moto y crea todo: cinco tablas de DynamoDB, las colas con sus DLQ, el
bus de eventos y las reglas que lo conectan. Es idempotente, se puede repetir.

`just aws-down` lo para. `just aws-status` muestra qué existe ahora mismo.

### 3. Datos de prueba

moto guarda todo **en memoria**: cuando el emulador se para —`just aws-down`,
un reinicio del contenedor o de la máquina— desaparecen las tablas, las colas
y todos los registros. Nada de lo que crees en local se conserva.

En vez de hacer ese dato duradero, `just seed` lo hace barato de recrear:

```bash
just seed
```

Recorre la cadena entera —registra al usuario demo, aprueba el dominio del
banco, reenvía seis alertas de Bancolombia, drena los tres workers y declara
las cuentas que las adoptan— y deja algo que mirar. No necesita `just dev` ni
los workers levantados: monta la aplicación en su propio proceso. Repetirlo es
seguro: dos ejecuciones seguidas dejan los mismos saldos. Lo que deja:

| | |
|---|---|
| `demo@finflow.local` / `una frase larga de verdad` | La cuenta. El token sale impreso al final, listo para pegar en Postman. |
| 3 cuentas declaradas | Tarjeta de crédito, ahorros —con la débito enlazada como segundo instrumento— y efectivo. |
| 8 movimientos | Seis de alertas y dos a mano. Uno queda **sin asignar**: la nómina, porque esa alerta no nombra los últimos cuatro dígitos de ninguna cuenta. |
| 6 comercios | Todos en revisión, agrupados solo con reglas deterministas. |

Las cuentas se declaran **después** de que llegan las alertas, a propósito: la
adopción es retroactiva y así queda ejercitada.

El modelo queda **sin conectar** a propósito: las seis alertas son de las que
el parser determinista lee, así que sembrar no cuesta nada ni necesita red.
Por eso los workers avisan `no model configured`; aquí es lo esperado.
`--with-llm` lo deja puesto. `--email` / `--password` cambian la cuenta que
crea, por si quieres sembrar dos usuarios y comprobar que cada uno solo ve lo
suyo.

### 4. Los cinco procesos

Todos a la vez, en una sola terminal:

```bash
just up   # emulador + recursos + datos demo + los procesos
```

Cada línea va etiquetada con el servicio que la escribió, y un solo Ctrl+C
los baja todos. Tarda hasta 25s en salir a propósito: los workers terminan el
long poll que tengan en curso en vez de tirar el mensaje.

`just up` **no arranca `ingest-worker`**. El buzón de Gmail es el mismo en los
tres entornos y el poller marca como leído lo que lee, así que un run local
contra un emulador en memoria se comería el correo que development iba a
procesar — sin que nada lo reporte. En local la entrada es el webhook que la
API monta justo para eso. Si de verdad lo quieres: `just up --with-ingest`.

Dos stacks a la vez (local y development) necesitan puertos distintos:
`just up --api-port 8001`.

O cada uno en su terminal, si prefieres controlarlos por separado:

```bash
just dev               # API en http://localhost:8000, con recarga en caliente
just ingest-worker     # IMAP -> filtrado por remitente -> SQS
just parse-worker      # SQS -> parser determinista -> LLM -> EventBridge
just merchant-worker   # EventBridge -> comercios canónicos
just financial-worker  # EventBridge -> filas del ledger y saldos
```

`merchant-worker` y `financial-worker` escuchan el mismo evento en colas
distintas y no dependen uno del otro: puedes correr solo el que te interese.
Cada worker avisa al arrancar si le falta configurar algo.

La API expone su documentación interactiva en `http://localhost:8000/docs`.

> En local se monta además `POST /ingestion/bank-notifications`, un webhook sin
> autenticar que acepta un correo entero directamente, sin pasar por ningún
> buzón. Es una costura de pruebas y **no existe fuera de `ENVIRONMENT=local`**:
> en producción nada lo llama por HTTP; el `ingest worker` lee el buzón
> compartido directamente.
>
> Es lo que permite probar la cadena completa sin una cuenta de Gmail. El
> recorrido request a request está en
> [frontend-integration.md](frontend-integration.md#guion-de-prueba-de-punta-a-punta).

### 5. Mirar qué está pasando

```bash
just inspect     # bandejas, notificaciones y profundidad de la cola
just events      # eventos de integración que llegaron al bus (--follow para seguir)
just aws-status  # tablas, colas y buses que existen ahora mismo
```

`just events` es especialmente útil: publicar en un bus sin regla **tiene
éxito** y no entrega nada, así que es la única forma de comprobar que un
evento salió de verdad.

`just inspect` mira el despliegue entero, sin autenticación y desde la máquina
que lo corre. La vista equivalente para un usuario —solo su propio correo— es
`GET /ingestion/notifications`, y esa sí está montada en producción.

En el log del `financial-worker`, la línea que importa es `outcome`:

| | |
|---|---|
| `applied` | Cayó en una cuenta declarada y movió su saldo. |
| `unassigned` | Nadie ha declarado una cuenta para ese instrumento. El movimiento se guarda igual y se adopta en cuanto exista la cuenta. Es lo esperado, no un fallo. |
| `duplicate` | Ese movimiento ya estaba en el ledger: ni fila, ni saldo — por eso `account_id` viene vacío. |

Los `domain_event` del log deliberadamente **no registran ni el monto, ni la
contraparte, ni los dígitos**: juntos son una línea del historial de gastos de
alguien, y un log no es sitio para eso.

### 6. Antes de dar por terminado un cambio

```bash
just prepare   # formato + lint + tipos + tests
just fix       # arregla formato y lint automáticamente
```

---

## Development: AWS real, todo con prefijo `dev-`

`ENVIRONMENT=development` es todo el mecanismo — el prefijo se aplica en
código, no configurando nueve nombres a mano:

| Recurso | local / producción | development |
|---|---|---|
| Tablas | `bank_notifications`, `user_inboxes`, `users`, `merchants`, `financial` | `dev-` + cada una |
| Colas | `parse-notifications`, `merchant-events`, `financial-events`, `integration-events` | `dev-` + cada una |
| Bus | `finflow` | `dev-finflow` |

Las reglas de EventBridge no llevan prefijo: su nombre es único **por bus**, y
el bus ya está separado.

```bash
cp .env.development.example .env.development
# edita AWS_PROFILE, IDENTITY_JWT_SECRET e INGESTION_INGEST_MAILBOX_ADDRESS
just provision-dev          # crea los recursos dev-*, una sola vez
# pega en .env.development las cuatro URLs de cola que imprime
just up-dev                 # los cinco procesos, en una terminal
```

Aquí `ingest-worker` **sí** arranca: development es un entorno donde el correo
entra por donde entra en producción. No corras a la vez el poller de dos
entornos — se pelean por el mismo buzón.

Uno por uno, si lo prefieres: `just run-dev` y los cuatro `*-worker-dev`.

El prefijo separa tablas, colas y bus — **no el buzón**. Si vas a correr
`just ingest-worker-dev`, que sea contra una cuenta de Gmail distinta de la de
producción; si no, deja la App Password vacía y el worker no arrancará. Ver el
aviso en [Configurar `.env`](#1-configurar-env).

Development hereda **todas** las restricciones de producción menos una: puede
usar `AWS_ENDPOINT_URL`, que es lo que permite ensayar esta configuración
contra moto antes de que cueste nada. Lo demás sigue prohibido: `*` como
origen CORS se rechaza, y el webhook local **no** se monta. En dev los correos
entran por donde entran en producción — el `ingest-worker` leyendo el buzón
por IMAP.

---

## Producción

Misma imagen, mismo código; cambian el fichero de entorno y las credenciales.
`AWS_ENDPOINT_URL` está **prohibido** con `ENVIRONMENT=production`: un
endpoint de emulador olvidado no puede desviar tráfico real.

### 1. Credenciales y secretos

```bash
cp .env.production.example .env.production
aws configure --profile finflow-production
```

**Ninguna clave de AWS se escribe en el fichero**: en local vienen del perfil
de `~/.aws`, y desplegado del rol de la instancia o la tarea (deja
`AWS_PROFILE` sin valor en ese caso).

Los secretos de la aplicación —`IDENTITY_JWT_SECRET`,
`INGESTION_INGEST_MAILBOX_APP_PASSWORD`, `LLM_API_KEY`— van en **SSM Parameter
Store**, y el fichero solo guarda una referencia:

```bash
uv run python -c "import secrets; print(secrets.token_hex(32))"   # genera el valor
just secret-put /finflow/production/jwt-secret                    # y pégalo cuando lo pida
```

`secret-put` **pide el valor por prompt**, no lo acepta como argumento: una
línea de comandos es visible en `ps` para cualquier proceso de la máquina y
queda en el historial del shell. Termina imprimiendo la referencia que hay que
pegar:

```
IDENTITY_JWT_SECRET=ssm:/finflow/production/jwt-secret
```

Cualquier valor que **no** empiece por `ssm:` se toma como el secreto mismo,
que es lo que mantiene el desarrollo local con valores planos. La conversión
es por secreto, no global, así que `grep ssm: .env.production` responde cuáles
ya están fuera del fichero. Se leen una vez por proceso: rotar uno exige
reiniciar.

Los parámetros estándar son gratuitos (hasta diez mil) y se cifran con KMS —
por eso Parameter Store y no Secrets Manager, que cobra por secreto y mes a
cambio de una rotación automática que aquí nadie usa todavía.

### 2. Crear los recursos

```bash
aws sso login --profile finflow-production   # si usas SSO
just provision-prod
```

Termina con un bloque **`Put this in .env.production:`**. Esas cuatro líneas
hay que pegarlas:

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

### 3. Desplegar

El destino son **cinco funciones Lambda**, no una máquina encendida. Ver
[Desplegar en Lambda](#desplegar-en-lambda) más abajo para el detalle:

```bash
just deploy-prod
```

Los cinco procesos de siempre siguen existiendo y siguen sirviendo para correr
producción **desde tu máquina** —depurar contra datos reales, una migración
puntual, comprobar algo sin desplegar—:

```bash
just run-prod              # uvicorn, sin recarga, puerto 8000
just ingest-worker-prod
just parse-worker-prod
just merchant-worker-prod
just financial-worker-prod
```

No los corras a la vez que el despliegue: el `ingest-worker` local competiría
con la función por el mismo buzón, y los de cola por los mismos mensajes.

### 4. Configurar la cuenta de ingesta

Esto el código no puede hacerlo por ti — es un paso manual, una sola vez, y no
depende de un usuario en particular:

1. Crea una cuenta de Gmail dedicada a esto (no la tuya personal).
2. Actívale verificación en dos pasos.
3. Genera una **App Password** (Cuenta de Google → Seguridad → Verificación en dos pasos → Contraseñas de aplicaciones).
4. Rellena en `.env.production`:
   ```
   INGESTION_INGEST_MAILBOX_ADDRESS=tu-cuenta-de-ingesta@gmail.com
   INGESTION_INGEST_MAILBOX_APP_PASSWORD=ssm:/finflow/production/mailbox-app-password
   ```

Con la dirección vacía la API se niega a arrancar: cada registro nuevo deriva
de ella su dirección de reenvío, así que no hay modo degradado posible. Con la
App Password vacía la API arranca, pero el `ingest worker` se niega a arrancar
él solo.

Paso a paso de cómo cada usuario conecta su banco a esa cuenta, en
[email-forwarding.md](email-forwarding.md).

---

## Desplegar en Lambda

Los cinco procesos se despliegan como **cinco funciones Lambda**, desde una
sola imagen de contenedor. Nada de esto se hace a mano: `infra/template.yaml`
(AWS SAM) declara las funciones, sus disparadores, sus permisos y la URL de la
API.

### Quién crea qué

Hay **dos herramientas**, y la línea entre ellas no es arbitraria:

| | Lo crea | Por qué |
|---|---|---|
| Tablas, colas, DLQ, bus, reglas | `provisioning.py` (`just provision-prod`) | moto las emula, y **local las necesita** |
| Funciones, roles, disparadores, URL | SAM (`just deploy-prod`) | moto no ejecuta tu código; esto solo existe en AWS real |

De ahí el orden: **aprovisionar primero, desplegar después.** Las funciones se
enganchan a colas que tienen que existir ya.

### Requisitos

Docker y el [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html)
en la máquina que despliega, y los tres secretos ya puestos en Parameter
Store:

```bash
just secret-put /finflow/production/jwt-secret
just secret-put /finflow/production/mailbox-app-password
just secret-put /finflow/production/llm-api-key
```

### Desplegar

```bash
aws sso login --profile finflow-production
just provision-prod        # una vez, y cada vez que cambie una tabla o cola
just deploy-prod           # construye la imagen y despliega las cinco funciones
just deploy-outputs        # imprime la URL de la API
```

`just deploy-outputs` devuelve algo como
`https://abc123.lambda-url.us-east-1.on.aws/`. **Esa es la dirección de la
app**: HTTPS ya resuelto, sin dominio, sin certificado y sin balanceador. Es
la que va en el navegador del móvil y en la configuración del frontend.

Para dev es lo mismo con `just provision-dev` y `just deploy-dev`, contra el
stack `finflow-dev` y con todo prefijado `dev-`.

### Comprobar que el despliegue sirve

Que el `deploy` termine bien dice que CloudFormation creó cinco funciones, no
que la aplicación funcione. Eso lo responde `just smoke`, que habla **por HTTP
contra la URL desplegada** —lo único que ejerce el arranque en frío, el
adaptador, y que la API resolviera su secreto de firma en Parameter Store:

```bash
just smoke $(just deploy-outputs finflow-dev | grep ApiUrl | awk '{print $4}')
just smoke https://abc123.lambda-url.us-east-1.on.aws --with-pipeline
```

Tres profundidades, porque los entornos no pueden permitirse lo mismo:

| | Qué hace | Qué necesita |
|---|---|---|
| por defecto | Un usuario desechable por ejecución: registro, dos cuentas, un movimiento a mano, y las lecturas de la primera pantalla. | Solo la URL. **Ninguna credencial de AWS.** |
| `--with-pipeline` | Además reenvía una alerta y **espera** a que el movimiento aparezca. Es la comprobación de que las funciones worker están vivas. | Credenciales del entorno que nombre `ENV_FILE`. |
| `--read-only` | Salud y el guardián de autenticación. Nada escribe. | Solo la URL. |

`--with-pipeline` **no drena ninguna cola**, y esa es la diferencia con
`just verify`: contra un despliegue, quienes consumen son los *event source
mappings*, así que un segundo consumidor competiría con ellos por los mismos
mensajes y el resultado dependería de quién ganara. El script mete la alerta y
pregunta a la API hasta que el movimiento aparece.

Producción se comprueba con `just smoke-prod <url>`, que fuerza
`--read-only`: cada escritura deja un usuario que ningún endpoint puede
borrar. Y no depende de que te acuerdes: el script le pregunta a `/health`
contra qué despliegue está —`local`, `development` o `production`— y se niega
a escribir si la respuesta es producción, sin mirar el fichero de entorno que
cargó. `just smoke` lee `.env.development` sea cual sea la URL que le sigas
poniendo, así que un guardián basado en ese fichero no guarda nada. Si el
despliegue no dice qué es, tampoco escribe.

Sale con código 0 solo si pasó todo, que es lo que permite usarlo como puerta
en CI.

### Qué corre dónde

| Función | Qué la despierta | Notas |
|---|---|---|
| `ApiFunction` | Function URL (HTTPS) | El mismo `uvicorn` de `just run-prod`, detrás del Lambda Web Adapter. **Cero líneas de código propias.** |
| `IngestFunction` | Un temporizador (cada minuto) | Una pasada de IMAP por tick. Limitada a **una** ejecución simultánea: dos competirían por los mismos correos `UNSEEN`. |
| `ParseFunction` | La cola `parse-notifications` | Lotes de 3 |
| `MerchantFunction` | La cola `merchant-events` | Lotes de 3, y **una** ejecución simultánea |
| `FinancialFunction` | La cola `financial-events` | Lotes de 10, sin límite de concurrencia |

**Por qué esos números.** El timeout de una función no puede pasar del
`VisibilityTimeout` de su cola (120s): si lo pasara, AWS rechaza el enganche
al crearlo, y si se forzara, el mensaje volvería a hacerse visible a mitad de
proceso y se trabajaría dos veces a la vez. Con 120s de techo y un modelo que
puede tardar 30s por mensaje, el lote es lo que tiene que ceder — de ahí 3 en
parse y merchant, y 10 en financial, que no llama a ningún modelo.

**Por qué financial escala y merchant no.** El ledger escribe la fila con una
condición y mueve el saldo con un `ADD` atómico, las dos cosas en una sola
transacción: el mismo movimiento aplicado dos veces no hace nada y dos
movimientos sobre una cuenta no se pisan. Merchant no tiene esa garantía
probada, así que se serializa hasta que la tenga.

### Cómo vuelve un mensaje a la cola

Es lo único que cambia de fondo respecto a los workers. Con `poll_once`, un
mensaje **sobrevive por defecto** y el worker lo borra explícitamente al
terminar. En Lambda es al revés: **se borra por defecto**, y solo se conserva
lo que la respuesta nombra.

Esa inversión vive en un sitio,
[`shared/infrastructure/messaging/lambda_batch.py`](../src/personal_finance/shared/infrastructure/messaging/lambda_batch.py),
y depende de `FunctionResponseTypes: [ReportBatchItemFailures]` en la
plantilla: sin esa línea la respuesta se ignora y se borra el lote entero,
incluidos los mensajes que pedían volver.

### Registros

Cada función escribe a `/finflow/<stack>/<nombre>` con retención de 30 días
(7 en dev). Nombres propios y no los `/aws/lambda/...` que Lambda se crea sola:
declarar ese nombre es competir con ella y el stack pierde en el segundo
despliegue.

```bash
just deploy-logs finflow ApiFunction        # seguir uno en vivo
just deploy-logs finflow FinancialFunction
```

### Lo que no se puede desplegar

Dos cosas siguen siendo manuales, y ninguna es código: crear la cuenta de
Gmail con su App Password (paso 4 de arriba) y poner los valores de los tres
secretos en Parameter Store. La plantilla solo guarda sus **rutas**; los
valores nunca pasan por git ni por la imagen.

---

## DynamoDB: on-demand, no aprovisionado

Las cinco tablas corren en **`PAY_PER_REQUEST`** (on-demand) en los tres
entornos. Es el valor por defecto en código y en las tres plantillas.

**Por qué no la capa gratuita.** `PROVISIONED` regala 25 unidades de lectura y
25 de escritura, pero **compartidas en toda la cuenta**, y DynamoDB cobra un
índice secundario como si fuera una tabla: cinco tablas y dos índices a tres
unidades cada uno ya gastaban 21 de las 25. Cada índice nuevo obligaba a bajar
la capacidad otra vez — el esquema moldeado por una asignación en vez de por
los datos. Y tres unidades son unas tres lecturas de 4 KB por segundo, con
varios endpoints leyendo una partición entera: el uso normal quedaba a un
refresco de ser estrangulado.

**Qué cuesta.** Medido sobre un `just seed` completo: 67 escrituras y 52
lecturas. A diez correos reenviados al día, del orden de **un céntimo al mes**.
El trato es una factura inmedible a cambio de un techo que ya no condiciona el
diseño.

**El techo.** `INGESTION_DYNAMODB_MAX_READ_UNITS` / `..._MAX_WRITE_UNITS` (25)
limitan peticiones por segundo, por tabla y por índice, para que un bucle
descontrolado se estrangule en vez de facturar. Es un radio de explosión, no
un presupuesto: pegado al techo un mes entero costaría dinero de verdad. El
control de gasto es una alarma de facturación en la cuenta, que nada en este
repositorio puede crear.

**Cambiar de modo funciona.** Poner `INGESTION_DYNAMODB_BILLING_MODE=PROVISIONED`
en un fichero de entorno y volver a aprovisionar **mueve las tablas
existentes** — el aprovisionamiento reconcilia el modo en cada pasada, no solo
al crear. En ese caso mandan `INGESTION_DYNAMODB_READ_CAPACITY` /
`..._WRITE_CAPACITY`. Funciona en ambos sentidos, así que también hay que
cuidar el descuido: un fichero de entorno con el modo viejo devuelve tablas
vivas al modo viejo sin avisar.

### Copias de seguridad

El aprovisionamiento deja las cinco tablas con **point-in-time recovery**
(PITR): puedes devolver una tabla al estado exacto que tenía en cualquier
segundo de los últimos 35 días. Se aplica en los tres entornos; en local no
sirve de nada, pero el camino es el mismo que en producción.

Protege contra la forma normal en que se pierden datos —borrar la tabla
equivocada, un despliegue que corrompe saldos durante unas horas— y no tanto
contra un fallo de AWS, que es raro. Importa sobre todo por `financial`, que
contiene el ledger completo: los saldos se recalculan a partir de ella, así
que perderla es perder el historial, y los correos originales ya se
procesaron. Es también lo único de esta guía que **no se puede añadir
después**.

**Restaurar** crea siempre una tabla *nueva* — nunca sobrescribe la original,
que es lo que te deja comparar antes de decidir:

```bash
aws dynamodb restore-table-to-point-in-time \
  --profile finflow-production \
  --source-table-name financial \
  --target-table-name financial-restaurada \
  --restore-date-time 2026-09-03T14:32:00Z
```

Tarda de minutos a horas según el tamaño. Cuando esté lista y hayas comprobado
que trae lo que esperabas, apunta la app hacia ella con
`FINANCIAL_ACCOUNTS_TABLE=financial-restaurada` y reinicia — o renómbrala tú.
El coste es aproximadamente el de almacenar la tabla otra vez; a esta escala,
céntimos.

---

## Comprobar que los datos cuadran

```bash
just verify                    # contra el emulador
just verify .env.development   # contra la cuenta dev
```

Recorre el flujo entero con **dos usuarios** —registro, aprobación del
remitente, alertas, los tres workers, cuentas declaradas a posteriori,
movimientos manuales, renombrado de comercio— y después comprueba dos
familias de propiedades:

- **Integridad**: cada saldo es igual a los movimientos que tiene detrás; el
  patrimonio es activos menos pasivos; un movimiento asignado está en una
  cuenta que responde a su instrumento; los buckets del resumen suman su
  total.
- **Aislamiento**: ninguno de los dos llega a lo del otro, ni leyendo ni
  escribiendo — un id ajeno responde `404` y no `403`, porque un `403` ya
  confirmaría que existe.

Sale con código 1 si algo falla, así que sirve en CI. Se **niega a correr con
`ENVIRONMENT=production`**: registra dos usuarios con una contraseña escrita
en este repositorio. `--yes-really-production` lo fuerza; `--keep-llm` deja el
modelo conectado.

---

## Cuando algo falla

El sistema está escrito para fallar ruidosamente en el arranque antes que
degradarse en silencio:

| Mensaje | Qué hacer |
|---|---|
| `IDENTITY_JWT_SECRET is not set` | Genera y pega el secreto. Todo token sería falsificable. |
| `INGESTION_INGEST_MAILBOX_ADDRESS is not set` | Sin ella nadie puede registrarse: cada cuenta nueva deriva de ahí su dirección de reenvío. |
| `INGESTION_INGEST_MAILBOX_ADDRESS / ..._APP_PASSWORD are not set` (solo el `ingest worker`) | El worker no tiene qué buzón revisar. Configura la cuenta dedicada. |
| `INGESTION_PARSE_QUEUE_URL is not set` | Aprovisiona y pega la URL. Si no, se aceptarían notificaciones que nunca se parsearían. |
| `MERCHANT_EVENTS_QUEUE_URL is not set` | Igual, para el worker de comercios. |
| `FINANCIAL_EVENTS_QUEUE_URL is not set` | Igual, para el de saldos: sin él ningún movimiento tocaría una cuenta. |
| `Could not read the secret at '/...' from Parameter Store` | La referencia `ssm:` apunta a un parámetro que no existe o al que el rol no tiene acceso. Nunca se degrada a vacío. |
| `AccessDeniedException` al provisionar | Faltan permisos. Con PITR hacen falta `dynamodb:DescribeContinuousBackups` y `dynamodb:UpdateContinuousBackups` además de los de crear tablas. |
| `AWS_ENDPOINT_URL must be unset when ENVIRONMENT=production` | Estás usando el fichero de entorno equivocado. |
| `Missing .env… — copy it from ….example` | El recipe de `just` no encuentra su fichero de entorno. |
| `no model configured` (aviso, no error) | Falta `LLM_API_KEY`. Los workers siguen funcionando sin plan B. |
| `InvalidParameterValueException: ... maximum visibility timeout` (al desplegar) | El timeout de una función supera el `VisibilityTimeout` de su cola. Bájalo a 120s o menos. |
| `Resource of type 'AWS::Logs::LogGroup' ... already exists` | Un log group con ese nombre existe fuera del stack. Bórralo o cámbiale el nombre en la plantilla. |
| `ExpiredTokenException` en una función con horas de vida | Credenciales congeladas. `build_session` ya lo evita en Lambda; si reaparece, comprueba que no haya un `AWS_PROFILE` puesto en la plantilla. |
| La respuesta reporta fallos pero los mensajes desaparecen igual | Falta `FunctionResponseTypes: [ReportBatchItemFailures]` en el enganche de esa cola. |

**Si algo desaparece sin explicación, mira las colas `*-dlq`.** Cada cola de
trabajo lleva al lado una *dead-letter queue* con ese sufijo: un worker borra
el mensaje al terminar bien, no al recogerlo, así que uno que falla siempre
volvería para siempre tapando a los demás — tras cinco intentos SQS lo aparta
ahí. Es lo que convierte "desapareció dinero" en "hay 40 mensajes en
`parse-notifications-dlq`". `just inspect` muestra ese número; nadie lo vigila
todavía, no hay alarma.

Un mensaje que no cumple su esquema se borra: nunca va a cumplirlo. Uno que
quizá entienda un despliegue más nuevo —una moneda, una versión desconocida—
se deja y acaba en la DLQ, porque borrarlo destruiría un movimiento real.
