# Requisitos y dependencias

Todo lo que Finflow necesita para correr, en una sola página: qué instalar,
con qué versión, para qué sirve y dónde se declara. Después, cómo levantarlo en
tu máquina y cómo dejarlo en un servidor.

> [!NOTE]
> Las **versiones exactas de las librerías** no se repiten aquí: viven en
> `pyproject.toml` + `uv.lock` (Python) y `frontend/package.json` +
> `frontend/package-lock.json` (frontend). Esta página nombra cada una y dice
> para qué sirve; los archivos de bloqueo son la fuente de verdad.

## 1. Herramientas del sistema

| Herramienta | Versión | Para qué | Cuándo hace falta | Dónde se fija |
| :--- | :--- | :--- | :--- | :--- |
| **Python** | `>=3.13,<3.14` (solo 3.13) | Backend y workers | Siempre | `pyproject.toml` (`requires-python`) |
| **uv** | 0.11.32 en el DevContainer; 0.9 dentro de la imagen de Lambda | Único gestor de entorno y dependencias de Python | Siempre | `.devcontainer/Dockerfile`, `Dockerfile` |
| **just** | 1.58.0 | Atajos de todo el proyecto (`justfile`) | Siempre | `.devcontainer/Dockerfile` |
| **Node.js + npm** | 24 (`>=24 <25`) | Frontend: servidor de desarrollo, compilación, pruebas | Para tocar o publicar el frontend | `frontend/package.json` (`engines`) |
| **AWS CLI** v2 | la última | Perfiles, SSM, CloudFormation, consultas | Contra AWS real (development, producción) | feature del DevContainer |
| **AWS SAM CLI** | la última | `sam build` / `sam deploy` | Para desplegar el backend | `uv tool install aws-sam-cli` |
| **Docker** | cualquiera reciente, con daemon accesible | `sam build` construye una imagen de contenedor | Para desplegar el backend | feature `docker-outside-of-docker` |
| **Git** | cualquiera | Control de versiones | Siempre | — |
| **d2** | la última | Regenerar los diagramas de `docs/assets/` | Solo al editar diagramas; no viene en el DevContainer | [docs/README.md](README.md#los-diagramas) |
| **curl, jq, wget, unzip, build-essential** | los de Debian 12 | Scripts, recetas del `justfile` y compilar dependencias nativas (bcrypt) | Siempre | `.devcontainer/Dockerfile` |
| **Chromium** (headless) | el que baje Playwright | `just shot` y las pruebas e2e en navegador | Solo desarrollo | `npm --prefix frontend exec -- playwright install chromium` |

El **DevContainer** (`.devcontainer/`) deja todo lo anterior instalado: es el
entorno de desarrollo canónico. Si no lo usas, instala cada herramienta a mano
con las versiones de la tabla (Debian 12 es la referencia).

## 2. Dependencias de Python

Declaradas en `pyproject.toml`; instaladas con `uv sync` (todo) o
`uv sync --no-dev` (solo ejecución, lo que lleva la imagen de Lambda).

**De ejecución**

| Paquete | Para qué |
| :--- | :--- |
| `fastapi[standard]` | API HTTP (incluye `uvicorn` y el CLI `fastapi`) |
| `pydantic`, `pydantic-settings` | Validación en los bordes, salida estructurada del modelo y lectura de la configuración |
| `boto3` | SQS, EventBridge, DynamoDB, SSM |
| `bcrypt` | Hash de contraseñas |
| `pyjwt` | Tokens de acceso (JWT) |
| `google-genai` | Cliente de Gemini, el plan B de la extracción |
| `xlsxwriter` | Exportar movimientos a Excel |
| `pyright` | Declarado también aquí; solo sirve para comprobar tipos, no se usa al correr |

**De desarrollo** (grupo `dev`)

| Paquete | Para qué |
| :--- | :--- |
| `pytest` | Pruebas |
| `moto[dynamodb,events,secretsmanager,server,sqs]` | AWS emulado en local (`moto_server`) |
| `httpx2` | Cliente HTTP de pruebas y scripts |
| `ruff` | Formato y lint |
| `pyright==1.1.414` | Tipos estrictos |
| `boto3-stubs[...]` | Tipos de boto3 |
| `cfn-lint`, `aws-sam-translator` | Validar `infra/template.yaml` |
| `pillow` | Utilidades de imágenes de los scripts |
| `poethepoet`, `pre-commit` | Tareas; `pre-commit` está declarado pero el repositorio no trae configuración de ganchos |

## 3. Dependencias del frontend

Declaradas en `frontend/package.json`; instaladas con `npm ci` (`just web-install`
usa `npm install`).

**De ejecución:** `react` 19, `react-dom`, `@tanstack/react-router`,
`@tanstack/react-query`, `openapi-fetch`, `tailwind-merge`, `clsx`,
`lucide-react`, `sonner`.

**De desarrollo:** `vite` 7, `typescript` 5.9, `tailwindcss` 4 con
`@tailwindcss/vite`, `@vitejs/plugin-react`, `@tanstack/router-plugin`,
`@tanstack/react-query-devtools`, `openapi-typescript` (genera los tipos desde
`docs/openapi.json`), `vitest`, `@biomejs/biome`, `playwright`, `wrangler`
(publica en Cloudflare Pages) y los tipos de `node` y `react`.

## 4. Cuentas y servicios externos

| Servicio | Para qué | Local | Development / producción |
| :--- | :--- | :---: | :---: |
| **AWS** (Lambda, SQS, EventBridge, DynamoDB, SSM, CloudWatch, SNS, ECR, CloudFormation) | Todo el backend | No (moto) | **Sí** |
| **Gmail de ingesta**, con App Password | Buzón compartido que lee el worker de ingesta (IMAP) | No | **Sí**, una por entorno |
| **Gmail/SMTP de envío** | Códigos de verificación y enlaces de recuperación | No (se escriben en el log) | **Sí** |
| **Google Gemini** (`LLM_API_KEY`) | Extracción cuando ninguna plantilla sirve | No obligatorio | **Sí** |
| **Telegram** (bot y webhook) | Avisos al teléfono | Opcional | Opcional |
| **Cloudflare Pages** | Publicar el frontend | No | **Sí** |
| **GitHub Actions** (OIDC hacia AWS) | Publicación automática | No | Opcional ([ci.md](ci.md)) |

## 5. Configuración (variables de entorno)

Un archivo por entorno, **nunca versionado**; cada uno tiene su plantilla:

| Archivo | Entorno | Plantilla |
| :--- | :--- | :--- |
| `.env` | local (moto) | `.env.example` |
| `.env.development` | development, AWS real con prefijo `dev-` | `.env.development.example` |
| `.env.production` | producción | `.env.production.example` |
| `frontend/.env.development` | URL de la API para `just web` (local o development) | `frontend/.env.example` |
| `frontend/.env.production` | URL de la API dentro del bundle publicado (lo genera `just web-publish`) | `frontend/.env.production.example` |

Las variables que **hay que rellenar** (el resto trae valor por defecto en la
plantilla):

| Variable | Qué es | Obligatoria |
| :--- | :--- | :--- |
| `IDENTITY_JWT_SECRET` | Secreto que firma los tokens. Largo y aleatorio: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. **La app no arranca sin él.** | Siempre |
| `INGESTION_INGEST_MAILBOX_ADDRESS` | Gmail de ingesta. **También en local**: cada registro deriva de ella la dirección de reenvío, y sin ella nadie puede crear cuenta. En local basta cualquier dirección con forma de correo. | Siempre |
| `INGESTION_INGEST_MAILBOX_APP_PASSWORD` | App Password de esa Gmail | development, producción |
| `IDENTITY_MAIL_FROM_ADDRESS` / `_APP_PASSWORD` | Cuenta que envía códigos y enlaces | development, producción |
| `IDENTITY_PASSWORD_RESET_URL` | Dirección del frontend a la que apunta el enlace de recuperación | development, producción |
| `LLM_API_KEY` | Clave de Gemini. Vacía es válida (los correos sin plantilla no se interpretan); el parámetro SSM del stack debe existir igual. | Recomendada en development y producción |
| `ALERTS_TELEGRAM_WEBHOOK_SECRET` | Lo único que autentica el webhook de Telegram; vacío dejaría el endpoint abierto. La plantilla local trae un valor de ejemplo. | **Siempre**, también como secreto SSM en lo desplegado |
| `ALERTS_TELEGRAM_BOT_TOKEN` | Token del bot de avisos | Solo si usas Telegram |
| `AWS_PROFILE` | Perfil de AWS (`finflow-dev`, `finflow-production`) | development, producción |
| `API_CORS_ORIGINS` | Orígenes del frontend que la API acepta | Siempre |
| `*_QUEUE_URL` | URL de cada cola; las imprime `just provision-*` | development, producción |

En producción los secretos no van en el archivo: viven en SSM Parameter Store
(`just secret-put /finflow/<entorno>/…`, ver [deploy.md](deploy.md#4-los-secretos-en-parameter-store)).

## 6. Levantarlo en tu máquina

### Con el DevContainer (recomendado)

Requisitos: **VS Code** con la extensión *Dev Containers* y **Docker**. Abrir la
carpeta y aceptar «Reopen in Container»; el contenedor corre
`uv sync`, `npm ci` y baja Chromium solo. Publica los puertos **8000** (API) y
**5173** (frontend): si alguno está ocupado en tu máquina, el contenedor no
arranca.

```bash
cp .env.example .env       # y rellena IDENTITY_JWT_SECRET e INGESTION_INGEST_MAILBOX_ADDRESS
cp frontend/.env.example frontend/.env.development   # sin él, el frontend sale en blanco
just up                    # moto + recursos + datos de prueba + API + workers
just web                   # en otra terminal: el frontend
```

| | |
| :--- | :--- |
| API y Swagger | http://localhost:8000/docs |
| Frontend | http://localhost:5173 |
| Usuario de prueba | `demo@finflow.local` · `una frase larga de verdad` |

`Ctrl+C` apaga el stack; para apagar solo el emulador, `just aws-down`.

### Sin DevContainer

1. Instala Python 3.13, `uv`, `just`, Node 24 y `build-essential` (tabla 1).
2. `uv sync` y `npm --prefix frontend ci`.
3. `cp .env.example .env` y rellena `IDENTITY_JWT_SECRET` e `INGESTION_INGEST_MAILBOX_ADDRESS`; `cp frontend/.env.example frontend/.env.development`.
4. `just up` y `just web`, igual que arriba.

Los códigos de verificación se escriben en el log de la API, y el buzón de
ingesta no se lee: un correo de banco se simula con
`POST /ingestion/bank-notifications`. Más detalle y la comprobación de datos en
[running.md](running.md#local).

### Comprobar que todo está bien

```bash
just prepare       # formato, lint, tipos y pruebas de Python
just web-check     # Biome, tipos, API deprecada y pruebas del frontend
just check-all     # las dos, más que el contrato OpenAPI esté al día
just e2e           # recorridos en navegador (necesita el stack levantado)
```

## 7. Dejarlo en un servidor

Finflow **no pide un servidor propio**: el backend corre como siete funciones
Lambda (una imagen de contenedor, `Dockerfile` + `infra/template.yaml`) y el
frontend como archivos estáticos en Cloudflare Pages. No hay nada que mantener
encendido. La guía completa, paso a paso, es [deploy.md](deploy.md); este es el
resumen de lo que debes tener antes.

### Camino principal: AWS + Cloudflare

Una vez por máquina o cuenta:

1. **AWS CLI, SAM CLI y Docker** funcionando (`aws --version`, `sam --version`,
   `docker info`).
2. **Un perfil de AWS por entorno** (`finflow-dev`, `finflow-production`) y la
   política `FinflowDeploy` en el usuario que despliega
   (`infra/iam/finflow-deploy-policy.json`).
3. **Los secretos en SSM**: `just secret-put /finflow/<entorno>/…`.
4. **La Gmail de ingesta** con verificación en dos pasos y App Password.
5. **Cloudflare**: `cd frontend && npx wrangler login`.
6. **Los archivos `.env.development` / `.env.production`** rellenados (sección 5).

Cada despliegue:

```bash
just deploy-prod        # aprovisiona tablas y colas, construye y despliega; pide confirmar el changeset
just web-publish        # genera frontend/.env.production desde la ApiUrl del stack y publica
just smoke-prod <ApiUrl>
```

> [!IMPORTANT]
> **Backend y web salen juntos.** Una web nueva contra una API vieja (o al
> revés) rompe pantallas. Un push a `dev` despliega development y un merge a
> `master` despliega producción solos: [ci.md](ci.md).

### Camino alterno: un servidor propio (solo para depurar)

Es lo que [running.md](running.md#3-correr-producción-desde-tu-máquina)
describe como depuración, no un destino de producción soportado. **Sigue
necesitando AWS** (SQS, DynamoDB, EventBridge, SSM): lo único que cambia es
dónde corren los procesos. Requisitos: Linux con Python 3.13, `uv`, `just`,
credenciales de AWS (perfil o rol de instancia) y salida a Internet por los
puertos 993 (IMAP), 465 (SMTP) y 443.

```bash
uv sync
cp .env.production.example .env.production      # rellena (sección 5)
just provision-prod                             # crea tablas y colas; imprime las cinco *_QUEUE_URL
# pega esas URLs en .env.production
just run-prod                                   # API en el puerto 8000
just ingest-worker-prod                         # un proceso por cada uno:
just parse-worker-prod
just merchant-worker-prod
just financial-worker-prod
just alerts-worker-prod
```

Son **seis procesos de larga duración**: supervísalos con `systemd` o similar.
El frontend se construye con `just web-build` (necesita `frontend/.env.production`
apuntando a tu API) y se sirve `frontend/dist/` con cualquier servidor estático;
ese origen debe estar en `API_CORS_ORIGINS`. `uv run` sincroniza también el grupo
`dev`; para un servidor sin él, exporta `UV_NO_DEV=1`.

> [!WARNING]
> **No actives `API_TRUST_PROXY_HEADERS=true` detrás de un proxy genérico
> (nginx, Caddy) sin más.** Ese modo lee primero la cabecera
> `x-amzn-request-context`, que en Lambda pone el adaptador pero que un cliente
> podría enviar él mismo, y así elegir su IP y saltarse los límites de registro,
> códigos y webhook. Déjalo en `false` (límites por IP del socket) o haz que el
> proxy elimine esa cabecera y fije `X-Forwarded-For`.

> [!CAUTION]
> **No los corras a la vez que el despliegue en Lambda**: el `ingest-worker`
> competiría con la función por el buzón, y los de cola por los mismos
> mensajes.

## 8. Cuando algo no arranca

| Síntoma | Causa más probable |
| :--- | :--- |
| `uv sync` falla por la versión de Python | No es 3.13: `uv python install 3.13` |
| La app no arranca y menciona un secreto | Falta `IDENTITY_JWT_SECRET` |
| `just up` dice que un puerto está ocupado | Otro proceso en 5000 (moto) u 8000 (API): `just aws-down` o ciérralo |
| El frontend carga y toda llamada falla | La API no está en el 8000, o su origen no está en `API_CORS_ORIGINS` |
| `sam build` muere con `docker-credential-…` | Usa `just deploy-*`, que ya lo esquiva ([deploy.md](deploy.md#1-docker-y-el-sam-cli)) |
| Chromium no abre en `just shot` | Faltan librerías del sistema o el navegador: `npm --prefix frontend exec -- playwright install chromium` |

El resto, en [running.md](running.md#cuando-algo-falla).
