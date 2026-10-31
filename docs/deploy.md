# Desplegar Finflow

Guía de **despliegue**: dejar el sistema corriendo en AWS y el frontend
publicado. Para arrancarlo desde tu máquina —local, o contra una cuenta real—
ver [running.md](running.md).

Un despliegue completo son **tres cosas**, y cada una tiene su herramienta:

| Qué | Con qué | Por qué así |
|---|---|---|
| Tablas, colas, DLQ, bus y reglas | `just provision-dev` / `provision-prod` | Es el plano de datos, y **local también lo necesita**: moto lo emula con este mismo código |
| Las siete funciones Lambda, sus disparadores, sus roles y la URL de la API | `just deploy-dev` / `deploy-prod` (AWS SAM) | moto no ejecuta tu código; esto solo existe en AWS de verdad |
| El bundle del frontend | `just web-publish-dev` / `web-publish` | Es estático: no hay servidor que mantener |

De ahí **el orden, que no es negociable**: aprovisionar, desplegar, publicar.
Las funciones se enganchan a colas que ya tienen que existir, y el bundle se
construye contra una URL de API que ya tiene que responder.

| Quiero | Ir a |
|---|---|
| Desplegar el backend a development | [Backend → development](#backend--development) |
| Desplegar el backend a producción | [Backend → producción](#backend--producción) |
| Publicar el frontend | [Frontend → Cloudflare Pages](#frontend--cloudflare-pages) |
| Saber si lo desplegado funciona | [Comprobar que sirve](#comprobar-que-sirve) |

---

## Antes de la primera vez

Seis cosas, una sola vez por máquina o por cuenta. Ninguna se repite en cada
despliegue.

### 1. Docker y el SAM CLI

```bash
docker info      # el DevContainer usa el daemon del host, no levanta uno propio
sam --version    # lo instala postCreateCommand con `uv tool install aws-sam-cli`
```

**`sam build` y el ayudante de credenciales del DevContainer.** Si alguna vez
lo ves morir así:

```
Error: Credentials store docker-credential-dev-containers-<uuid> exited with "".
... StoreError
```

no es Docker ni AWS. La extensión Dev Containers escribe
`credsStore: dev-containers-<uuid>` en `~/.docker/config.json` para reenviar
al host los logins de registro que tengas allí. Ese ayudante implementa `get`,
`store` y `erase`, pero **no `list`** — y `list` es justo lo que el SDK de
Docker llama antes de construir una imagen, para juntar las cabeceras de
autenticación. El CLI de Docker no pasa por ahí, y por eso `docker build` a
mano funciona y `sam build` no.

`just deploy-dev` y `just deploy-prod` ya lo esquivan: construyen con
`DOCKER_CONFIG` apuntando a `.aws-sam/docker-config`, una configuración sin
ayudante. No se pierde nada, porque las dos imágenes base son públicas. Si
llamas a `sam build` a mano, pásale lo mismo:

```bash
DOCKER_CONFIG=.aws-sam/docker-config sam build --config-env production \
    --config-file infra/samconfig.toml --template infra/template.yaml
```

**«No changes to deploy» no es un fallo.** La imagen del backend lleva
`pyproject.toml`, `uv.lock` y `src/personal_finance`, y nada más: una rama que
solo toca el frontend, la documentación o el `justfile` construye una imagen
idéntica a la que ya está desplegada. Los dos entornos llevan
`fail_on_empty_changeset = false` en `infra/samconfig.toml` para que eso
termine en éxito y no en un error que parece una avería. Si necesitas
comprobar si de verdad cambiaría algo, compara el id de la imagen construida
con la etiqueta que corre la función:

```bash
docker images --format '{{.Repository}} {{.ID}}' | grep function
aws lambda get-function --function-name <nombre> --profile finflow-production \
    --query 'Code.ImageUri' --output text
```

El id va dentro de la etiqueta (`apifunction-<id>-api`). Si coinciden, no hay
despliegue que hacer: lo que cambió no viaja en la imagen.

### 2. Los perfiles de AWS

```bash
aws configure --profile finflow-dev
aws configure --profile finflow-production
```

`~/.aws` está montado en un volumen Docker con nombre (`finflow-v2-aws`), así
que **sobrevive a un rebuild** del contenedor. Es un volumen y no un bind
mount del `~/.aws` del host a propósito: el contenedor no ve ninguna otra
credencial tuya. Para empezar de cero, `docker volume rm finflow-v2-aws` y
reconstruye.

Si la cuenta está detrás de IAM Identity Center, usa `aws configure sso`: lo
que queda guardado es un token que caduca, no una clave permanente.

### 3. Los permisos del usuario que despliega

Desplegar necesita mucho más que correr la app: `sam deploy` crea el stack,
sube la imagen y crea los cinco roles de ejecución. Un usuario de plano de
datos falla con `AccessDenied` a mitad y deja el stack en `ROLLBACK`.

`infra/iam/finflow-deploy-policy.json` es la política mínima, acotada por
prefijo: los stacks `finflow` y `finflow-dev`, los roles y funciones
`finflow-*`, los logs `/finflow/*` y los parámetros `/finflow/*`.
`iam:PassRole` está condicionado a `lambda.amazonaws.com`, así que el usuario
no puede prestarle un rol a ningún otro servicio.

Adjúntala desde una identidad que sí tenga permisos de IAM:

```bash
aws iam create-policy --policy-name FinflowDeploy \
  --policy-document file://infra/iam/finflow-deploy-policy.json
aws iam attach-user-policy --user-name <tu-usuario> \
  --policy-arn arn:aws:iam::<cuenta>:policy/FinflowDeploy
```

Si ya está adjunta y el archivo cambió, `create-policy` falla con
`EntityAlreadyExists`: lo que toca es una versión nueva, por defecto. IAM sólo
guarda cinco, así que borra la vieja si se llena.

```bash
aws iam create-policy-version \
  --policy-arn arn:aws:iam::<cuenta>:policy/FinflowDeploy \
  --policy-document file://infra/iam/finflow-deploy-policy.json \
  --set-as-default
```

**Este archivo es la política de referencia, no un espejo de la cuenta.** Se
comprobó el 2026-09-15 que los dos usuarios tienen permisos que no están aquí
(`lambda:ListFunctions`, por ejemplo) y que producción no tiene alguno que sí
está. Editarlo no cambia nada por sí solo, y ninguno de los dos usuarios puede
leer IAM ni sobre sí mismo: lo que hay adjunto se mira en la consola, con una
identidad administradora.

Un permiso que falta no se ve hasta que CloudFormation llama a esa API a mitad
del despliegue, y entonces el stack entra en `ROLLBACK` con el resto de
funciones canceladas. Pasó con `lambda:PutFunctionConcurrency`: las funciones
que ya tenían `ReservedConcurrentExecutions` no volvían a pedirlo, así que el
hueco sólo salió al crear una función nueva que sí lo usaba.

Dos sitios quedan en `*` y no por pereza: los repositorios de ECR los nombra
el *companion stack* de SAM con un hash impredecible, y los *event source
mappings* de SQS se identifican por UUID. No hay prefijo al que agarrarse.

### 4. Los secretos, en Parameter Store

Uno por entorno. El valor se pide por prompt, nunca como argumento: una línea
de comandos es visible en `ps` y queda en el historial del shell.

```bash
uv run python -c "import secrets; print(secrets.token_hex(32))"   # para el JWT

just secret-put /finflow/production/jwt-secret
just secret-put /finflow/production/mailbox-app-password
just secret-put /finflow/production/mail-app-password
just secret-put /finflow/production/llm-api-key
just secret-put /finflow/production/telegram-bot-token
just secret-put /finflow/production/telegram-webhook-secret
```

La receta es `secret-put` **seguida de la ruta**; `just /finflow/...` a secas
no es un comando y `just` responde que no conoce esa receta.

Los dos últimos son de los avisos y los explica
[alerts.md](alerts.md#4-desplegarlo). El del webhook lo inventas tú:

```bash
uv run python -c "import secrets; print(secrets.token_hex(32))"
```

`mail-app-password` es de dónde **salen** los correos que manda Identity —
códigos de verificación y enlaces para restablecer la contraseña. Puede ser
exactamente la misma App Password que `mailbox-app-password`: es la misma
casilla, leída por IMAP y escrita por SMTP. Están separadas porque son dos
permisos distintos (solo la función de la API resuelve esta) y porque nada
obliga a que la cuenta que envía sea la que recibe.

Para development, la misma orden con `/finflow/development/...`: **el fichero
de entorno se deduce de la ruta**, así que no hay un segundo argumento que
equivocar. Una ruta que contradiga el fichero se rechaza antes de pedirte el
valor — pegar una referencia de development en `.env.production` haría que
producción firmara con el secreto de dev.

Cada comando termina imprimiendo la línea que hay que pegar en el `.env` del
entorno:

```
IDENTITY_JWT_SECRET=ssm:/finflow/production/jwt-secret
```

**Rotar uno es el mismo comando** (`Overwrite=True`), pero no se propaga solo:
el valor se lee una vez por proceso, así que hay que volver a desplegar el
stack o reiniciar el proceso local.

Qué ruta lee cada stack está en `infra/samconfig.toml`
(`JwtSecretParameter`, `MailboxPasswordParameter`, `MailAppPasswordParameter`,
`LlmApiKeyParameter`, `TelegramBotTokenParameter`,
`TelegramWebhookSecretParameter`, y `LlmDailyCallsParameter`, que no es un
secreto: ver [El tope diario del modelo](#el-tope-diario-del-modelo)).

> **Un override olvidado ahí no falla: cruza los entornos.** Cada uno de esos
> parámetros se declara en `infra/template.yaml` con un *default* que apunta a
> `/finflow/production/`, así que un stack al que se le olvide el override no
> se queda sin secreto — lee el del otro entorno. Pasó el 14 de septiembre con
> los tres de Telegram: `finflow-dev` arrancó pidiendo
> `/finflow/production/telegram-bot-token`, no existía, y **la API entera se
> quedó sin arrancar**. Si hubiera existido, dev habría estado mandando avisos
> con el bot de producción y nada habría fallado.
>
> Al añadir un parámetro nuevo de tipo secreto, añádelo a los **dos** bloques
> `parameter_overrides` en el mismo cambio.

Además de la ruta del secreto, Identity necesita dos valores que **no** son
secretos y viven en `parameter_overrides` del mismo fichero:

| Parámetro | Qué es |
|---|---|
| `MailFromAddress` | La cuenta desde la que sale el correo. Tiene que coincidir con la App Password de `MailAppPasswordParameter`. |
| `PasswordResetUrl` | La página del frontend que recibe el enlace; la API le añade `?token=…`. Tiene que ser `https`: el token viaja en la query. |

**Los tres son obligatorios**: sin ellos la API no arranca. Es deliberado — un
despliegue que no puede mandar un código es uno donde nadie puede registrarse
ni recuperar su cuenta, y eso tiene que fallar al desplegar y no en la cara del
primer usuario que lo intente.

**Los de Telegram no lo son**, y la diferencia importa: un despliegue sin bot
sirve todo lo demás con normalidad y responde `503` solo en `/alerts/*`. Los
avisos se suman a la app; no la sostienen. El worker de avisos sí se niega a
arrancar sin token, porque su único trabajo es mandar.

> Hoy `finflow-dev` y `finflow-production` resuelven a la **misma cuenta de
> AWS**, con usuarios IAM distintos. Compruébalo antes de fiarte de la
> separación:
> `aws sts get-caller-identity --profile finflow-production --query '[Account,Arn]' --output text`

Comprueba la pareja antes de desplegar, que es cuando sale barato:

```bash
just mail-check "" .env.production
```

Un `535` ahí significa que la App Password y `MailFromAddress` son de cuentas
distintas — el único error que la plantilla no puede detectar y que solo
aparece cuando alguien intenta registrarse.

#### El tope diario del modelo

Cuántos correos puede leer el modelo por persona y por día. No es un secreto,
pero vive en Parameter Store por otra razón: la función de parseo lo relee
cada cinco minutos, así que **cambiarlo no exige desplegar**. Cómo cuenta está
en [overview.md → El tope del modelo](overview.md#el-tope-del-modelo).

**Créalo antes del primer despliegue que lo incluye**, una vez por entorno.
Esto vale también para un stack que ya existía antes de que el tope llegara:

```bash
just llm-limit-put development 20
just llm-limit-put production 20
```

Sin él el despliegue termina en verde y `smoke` pasa, porque ninguno de los
dos llama al modelo. El fallo aparece con el primer correo que ninguna
plantilla reconoce: la función no puede leer el límite, el mensaje se
reintenta y acaba en `parse-notifications-dlq`, con
`Could not read the parameter at '/finflow/<entorno>/llm-daily-calls-per-user'`
en el log. Crear el parámetro arregla los correos que lleguen después. Los que
ya están en la DLQ no vuelven solos.

Cambiarlo es la misma orden con otro número, y se aplica en menos de cinco
minutos. `0` apaga el modelo para todos sin tocar las plantillas. La receta
solo acepta un número entero, y escribe un `String` estándar, cuyas lecturas no
se cobran. Para comprobar qué valor tiene:

```bash
aws ssm get-parameter --name /finflow/production/llm-daily-calls-per-user \
    --profile finflow-production --query Parameter.Value --output text
```

`LlmDailyCallsParameter` tiene la misma trampa que los secretos: su *default*
en la plantilla apunta a `/finflow/production/`, así que va en los **dos**
bloques de `parameter_overrides`, y ya está en los dos.

### 5. La cuenta de correo de ingesta

Manual, y **una por entorno**: una cuenta de Gmail dedicada, verificación en
dos pasos, y una App Password. Development y producción tienen la suya, y
por qué importa está en [Cada stack tiene su propio
buzón](#cada-stack-tiene-su-propio-buzón). Los pasos están en
[running.md → Configurar la cuenta de ingesta](running.md#4-configurar-la-cuenta-de-ingesta).

### 6. Cloudflare, para el frontend

```bash
cd frontend && npx wrangler login
```

---

## Backend → development

```bash
cp .env.development.example .env.development   # solo la primera vez
aws sso login --profile finflow-dev            # si usas SSO

just provision-dev          # tablas, colas, DLQ, bus y reglas, todo `dev-`
# pega en .env.development las cuatro URLs de cola que imprime

just deploy-dev             # construye la imagen y despliega las siete funciones
just deploy-outputs-dev     # imprime la ApiUrl y lo demás que publica el stack
just smoke <ApiUrl>         # comprueba que responde de verdad
```

`provision-dev` solo hace falta cuando cambia una tabla o una cola, y
repetirlo no hace daño: crea o ajusta, nunca borra. El pipeline lo corre en
cada despliegue. `deploy-dev`, cada vez que cambie el código.

**Desde los strikes del modelo (2026-10-31) cambió el aprovisionamiento**, así
que a mano hay que correrlo antes de desplegar:

- La tabla `users` tiene un índice nuevo, `by_user_id`. Alerts lo usa para saber
  a qué correo escribir.
- La regla de `alerts-events` también deja pasar `ModelReadingSuspended`.

Si se despliega sin aprovisionar, todo sigue funcionando hasta el primer
strike: entonces el aviso no encuentra el índice, se reintenta y termina en
`alerts-events-dlq`.

**Antes del primer despliegue con el tope del modelo**, una sola vez:
`just llm-limit-put development 20`. Sin ese parámetro el despliegue sale
bien y los correos que necesitan el modelo terminan en la DLQ. Ver
[El tope diario del modelo](#el-tope-diario-del-modelo).

Las cuatro URLs de cola no se pueden escribir por adelantado: incluyen el id
de cuenta, así que solo existen una vez creada la cola.

## Backend → producción

Idéntico, con los recipes `-prod` y una comprobación más floja al final:

```bash
cp .env.production.example .env.production     # solo la primera vez
aws sso login --profile finflow-production

just provision-prod
# pega en .env.production las cuatro URLs de cola que imprime
just llm-limit-put production 20   # solo la primera vez: ver "El tope diario del modelo"

just deploy-prod
just deploy-outputs-prod
just smoke-prod <ApiUrl>    # solo lectura: ver "Comprobar que sirve"
```

La `ApiUrl` es algo como `https://abc123.lambda-url.us-east-1.on.aws/`. **Esa
es la dirección de la app**: HTTPS ya resuelto, sin dominio, sin certificado y
sin balanceador. Es la que consume el frontend.

### Cada stack tiene su propio buzón

El prefijo `dev-` separa tablas, colas y bus. **No separa un buzón de Gmail**:
eso lo hace tener uno por entorno, y es lo que permite que los dos stacks
poll-een a la vez (`IngestScheduleState=ENABLED` en ambos).

La razón de la separación: el lector busca los correos `UNSEEN` y marca
`\Seen` lo que se lleva, así que dos pollers sobre **un mismo** buzón no
duplican un mensaje, **se lo disputan** — cada correo cae en el que preguntó
primero y es invisible para el otro. Con buzones distintos no hay carrera.
Antes de volver a apuntar dos stacks a una sola cuenta, apaga uno.

Dos valores tienen que concordar en cada entorno, y solo IMAP se entera si no
lo hacen: la dirección en `IngestMailboxAddress` y la App Password guardada en
la ruta de `MailboxPasswordParameter`. Son una credencial partida en dos
sitios.

```bash
grep -n "IngestMailboxAddress\|IngestScheduleState" infra/samconfig.toml
```

**Cambiar la cuenta de correo de un entorno son dos cosas, no una**: la App
Password nueva con `just secret-put`, y la dirección en `IngestMailboxAddress`
—de donde sale la parte anterior al `+` de la dirección de reenvío de cada
usuario— más el despliegue. Hacerlo cuando ya hay usuarios registrados
invalida la dirección que cada uno pegó en Gmail.

---

## Frontend → Cloudflare Pages

El bundle no vive en esta cuenta de AWS: S3 más CloudFront se construyó y se
retiró el 2026-09-01 porque la cuenta no puede crear una distribución hasta
que AWS Support la verifique. Ver [decisions.md → Deployment](decisions.md).

```bash
just web-publish-dev        # contra el stack finflow-dev
just web-publish            # contra producción
```

Cada uno hace las tres cosas que tienen que ir juntas: lee la `ApiUrl` del
stack, **genera** `frontend/.env.production` con ella, construye y sube
`dist/`. No escribas ese fichero a mano: es justo el valor que se queda viejo
y produce una app que carga bien y llama a la URL de ayer.

**Un despliegue que cambia el contrato arrastra una publicación**, en el
mismo rato y no más tarde: el bundle publicado sigue mandando el cuerpo de
ayer. Cuando `POST /identity/register` empezó a exigir `verification_token`,
la API de development lo pidió a las 21:03 y el sitio publicado siguió
mandando email y contraseña a secas — registrarse desde el navegador
respondía 422 aunque las dos mitades estuvieran bien. Despliega y publica
seguido, o el registro queda roto en medio.

**Y después, el paso que no hace el comando** — solo hace falta la primera
vez, y cada vez que cambie el dominio:

1. Copia el origen que imprime el despliegue, en la línea `published at`.
2. Añádelo a `CorsOrigins` en `infra/samconfig.toml`, en el entorno que
   corresponda.
3. Vuelve a desplegar el backend (`just deploy-prod` / `just deploy-dev`).

**No deduzcas ese origen del nombre del proyecto.** Los subdominios
`*.pages.dev` son únicos en todo Cloudflare, así que un nombre ya tomado por
otra cuenta recibe un sufijo al crearse: el proyecto `finflow-dev` se publica
en `finflow-dev-2tc.pages.dev`, y `finflow-dev.pages.dev` es el sitio de un
desconocido. Por eso la receta lee el origen de lo que acaba de publicar en
vez de construirlo.

La primera ejecución crea el proyecto y pregunta por su **rama de
producción**. La respuesta tiene que coincidir con `pages_branch` en el
`justfile` (`master`): si no, cada subida queda archivada como *preview*, en
un subdominio distinto por despliegue que jamás podrá estar en la lista de
CORS.

Sin ese segundo despliegue **la app carga, se dibuja entera y ninguna llamada
funciona**: el navegador las bloquea en el preflight. La receta avisa al
terminar si no encuentra el origen en `samconfig.toml`.

La lista de orígenes es de **coincidencia exacta**: un dominio propio es una
entrada más, y las URL de *preview* de Pages —un subdominio distinto por
despliegue— no pueden estar en ella. Por eso las recetas publican siempre en la
rama de producción del proyecto y nunca como preview.

Dos ficheros en `frontend/public/` sostienen lo que hacía CloudFront, y Vite
los copia tal cual a la raíz de `dist/`:

- **`_headers`** — HSTS, `nosniff`, `X-Frame-Options: DENY`, referrer-policy, y
  `immutable` para `/assets/*`. `index.html` queda fuera: Pages lo revalida en
  cada petición, y eso es lo que hace visible un despliegue nuevo sin comprar
  una invalidación.
- **`_redirects`** — `/* /index.html 200`. Sin él, `/cuentas` recargado en el
  navegador es un 404: la ruta existe en el bundle, no en la subida.

---

## Comprobar que sirve

Que el despliegue termine bien dice que CloudFormation creó siete funciones,
no que la aplicación funcione. Eso lo responde `just smoke`, que habla **por
HTTP contra la URL desplegada** — lo único que ejerce el arranque en frío, el
adaptador, y que la API resolviera su secreto de firma en Parameter Store.

| | Qué hace | Qué necesita |
|---|---|---|
| `just smoke <url>` | Un usuario desechable por ejecución: registro, dos cuentas, un movimiento a mano y las lecturas de la primera pantalla. | Solo la URL. **Ninguna credencial de AWS.** |
| `just smoke <url> --with-pipeline` | Además reenvía una alerta y **espera** a que el movimiento aparezca: la comprobación de que las funciones worker están vivas. | Credenciales del entorno. |
| `just smoke-prod <url>` | Salud y el guardián de autenticación. No escribe nada. | Solo la URL. |

Producción se comprueba en modo lectura porque cada escritura dejaría un
usuario que ningún endpoint puede borrar. No depende de que te acuerdes: el
script le pregunta a `/health` contra qué despliegue está y se niega a
escribir si la respuesta es `production`, sin mirar el fichero de entorno que
cargó.

`--with-pipeline` **no drena ninguna cola**, y esa es la diferencia con `just
verify`: contra un despliegue quienes consumen son los *event source
mappings*, así que un segundo consumidor competiría con ellos por los mismos
mensajes. El script mete la alerta y pregunta a la API hasta que el movimiento
aparece.

Sale con código 0 solo si pasó todo, así que sirve como puerta en CI.

---

## Después del primer despliegue

Cuatro cosas que solo hacen falta una vez, y que no avisan si faltan.

**Registra el webhook de Telegram.** Los avisos no funcionan hasta que Telegram
sepa a dónde entregar, y eso es una orden aparte del despliegue: la URL solo
cambia cuando cambia el Function URL. Una vez por entorno:

```bash
just deploy-outputs-dev                       # de aquí sale ApiUrl
just telegram-webhook-dev https://<ApiUrl>/alerts/telegram/webhook
just telegram-webhook-info-dev                # qué cree Telegram, y el último error
```

Sin esto, vincular un canal parece funcionar —el enlace se abre, el bot
responde con su mensaje por defecto— y no pasa nada más. Todo lo demás de la
app sigue igual. Está entero en [alerts.md](alerts.md).

**Confirma la suscripción de las alarmas.** La plantilla crea un tema SNS y
cuatro alarmas, una por DLQ. Tras el primer despliegue con `AlertEmail` puesto
en `infra/samconfig.toml`, AWS manda un correo de confirmación: hasta que
pulses ese enlace no llega ningún aviso, y la alarma parecerá funcionar. Es el
paso que más se olvida.

Las alarmas usan `Maximum` y no `Average`: un mensaje que entra y se drena
dentro del mismo periodo se promediaría hasta casi cero y no reportaría nada.
Y nombran sus colas en vez de referenciarlas —la plantilla no las crea—, así
que renombrar una cola en `provisioning.py` deja aquí una alarma vigilando un
nombre que ya no existe, en `INSUFFICIENT_DATA`, con aspecto de sana.

**Cierra el grifo de ECR.** Cada despliegue sube la imagen una vez por
función —siete copias de ~250 MB— a repositorios que SAM crea y nunca poda. Es
la única línea de esta factura que crece sola:

```bash
just ecr-prune-dev 3     # deja las 3 imágenes más recientes por repositorio
just ecr-prune-prod 3
```

La política se adjunta al repositorio, no a las imágenes de hoy, así que se
aplica también a los despliegues futuros y repetirlo es inocuo.

**Mira los logs cuando haga falta.** Cada función escribe en
`/finflow/<stack>/<nombre>`, con 30 días de retención (7 en dev):

```bash
just deploy-logs-prod ApiFunction
just deploy-logs-dev FinancialFunction
```

---

## Referencia: qué corre dónde

| Función | Qué la despierta | Notas |
|---|---|---|
| `ApiFunction` | Function URL (HTTPS) | El mismo `uvicorn` de `just run-prod`, detrás del Lambda Web Adapter. **Cero líneas de código propias.** |
| `IngestFunction` | Un temporizador | Una pasada de IMAP por tick. Limitada a **una** ejecución simultánea: dos competirían por los mismos correos `UNSEEN`. |
| `ParseFunction` | La cola `parse-notifications` | Lotes de 3. Cada correo que va al modelo gasta del [tope diario](overview.md#el-tope-del-modelo) de su dueño |
| `MerchantFunction` | La cola `merchant-events` | Lotes de 3, y **una** ejecución simultánea |
| `FinancialFunction` | La cola `financial-events` | Lotes de 10, sin límite de concurrencia |
| `AlertsFunction` | La cola `alerts-events` | Lotes de 5, y **una** ejecución simultánea. Ver [alerts.md](alerts.md#por-qué-una-sola-ejecución-a-la-vez). También avisa cuando el modelo se suspende para alguien, y para eso manda correo: usa `MailFromAddress` y `MailAppPasswordParameter`, y no arranca sin ellos |
| `WeeklySummaryFunction` | Un temporizador: los lunes a las 08:00 de Bogotá | El resumen de la semana anterior, por Telegram y a la campana. **Una** ejecución simultánea; correrlo dos veces no manda nada dos veces. Ver [alerts.md](alerts.md#el-resumen-del-lunes) |

**Por qué esos números.** El timeout de una función no puede pasar del
`VisibilityTimeout` de su cola (120s): si lo pasara, AWS rechaza el enganche al
crearlo, y si se forzara, el mensaje se haría visible a mitad de proceso y se
trabajaría dos veces a la vez. Con 120s de techo y un modelo que puede tardar
30s por mensaje, el lote es lo que cede.

**Por qué financial escala y merchant no.** El ledger escribe la fila con una
condición y mueve el saldo con un `ADD` atómico, las dos cosas en una sola
transacción. Merchant no tiene esa garantía probada, así que se serializa
hasta que la tenga.

**Cómo vuelve un mensaje a la cola.** Es lo único que cambia de fondo respecto
a los workers locales. Con `poll_once` un mensaje **sobrevive por defecto** y
el worker lo borra al terminar; en Lambda es al revés, **se borra por
defecto** y solo se conserva lo que la respuesta nombra. Esa inversión vive en
[`shared/infrastructure/messaging/lambda_batch.py`](../src/personal_finance/shared/infrastructure/messaging/lambda_batch.py)
y depende de `FunctionResponseTypes: [ReportBatchItemFailures]` en la
plantilla: sin esa línea la respuesta se ignora y se borra el lote entero.

## Referencia: los parámetros del stack

Los dieciocho de `infra/template.yaml`. Cada entorno los fija en su bloque
`parameter_overrides` de `infra/samconfig.toml`, y cambiar cualquiera es un
despliegue, salvo el *valor* del tope del modelo, que vive en SSM.

| Parámetro | development / production | Qué es |
|---|---|---|
| `EnvironmentName` | `development` / `production` | Llega a la app como `ENVIRONMENT`. De ahí deriva la app los nombres de sus recursos, y es lo que `/health` responde y lo que impide a `smoke` escribir en producción. |
| `ResourcePrefix` | `dev-` / `""` | Solo para los ARN de los permisos. Tiene que coincidir con el prefijo que la app deriva de `EnvironmentName`; si no, cada función pide permiso sobre tablas que no son las suyas. En producción va entre comillas: SAM rechaza un valor vacío sin ellas. |
| `IngestMailboxAddress` | una cuenta de Gmail por entorno | El buzón de ingesta. Ver [Cada stack tiene su propio buzón](#cada-stack-tiene-su-propio-buzón). |
| `IngestScheduleExpression` | `rate(5 minutes)` / `rate(1 minute)` | Cada cuánto se revisa el buzón. Es lo que más gasta del free tier de Lambda: casi todo son pasadas que no encuentran nada. |
| `IngestScheduleState` | `ENABLED` / `ENABLED` | Apagar la ingesta sin desmontar nada. |
| `JwtSecretParameter`, `MailboxPasswordParameter`, `MailAppPasswordParameter`, `LlmApiKeyParameter`, `TelegramBotTokenParameter`, `TelegramWebhookSecretParameter` | `/finflow/<entorno>/…` | Rutas de los secretos. Ver [Los secretos](#4-los-secretos-en-parameter-store). |
| `LlmDailyCallsParameter` | `/finflow/<entorno>/llm-daily-calls-per-user` | Ruta del tope del modelo. Ver [El tope diario del modelo](#el-tope-diario-del-modelo). |
| `MailFromAddress`, `PasswordResetUrl` | | El correo de Identity. Ver [Los secretos](#4-los-secretos-en-parameter-store). `MailFromAddress` y `MailAppPasswordParameter` también los usa `AlertsFunction`, para el aviso de suspensión del modelo. |
| `CorsOrigins` | el origen de Pages de cada uno, y `localhost:5173` | Coincidencia exacta. Ver [Frontend → Cloudflare Pages](#frontend--cloudflare-pages). |
| `TelegramBotUsername` | un bot por entorno | Ver [alerts.md](alerts.md#4-desplegarlo). |
| `AlertEmail` | | A quién avisan las alarmas de las DLQ. Vacío despliega, pero nadie se entera. |
| `LogRetentionDays` | `7` / `30` | Días de logs. Explícito porque el valor por defecto de AWS es no borrarlos nunca. |

Los seis `…Parameter` de secretos y `LlmDailyCallsParameter` tienen un
*default* que apunta a `/finflow/production/`: un override olvidado hace que
development lea lo de producción, sin error.

## Lo que no se puede desplegar

Tres cosas siguen siendo manuales, y ninguna es código: crear la cuenta de
Gmail con su App Password, poner los valores de los seis secretos en
Parameter Store, y crear allí el tope del modelo. La plantilla solo guarda sus
**rutas**; los valores nunca pasan por git ni por la imagen.

Los errores que aparecen al desplegar están en
[running.md → Cuando algo falla](running.md#cuando-algo-falla).
