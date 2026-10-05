# Despliegue automático

Cada push a **`master`** despliega **producción** y cada push a **`dev`**
despliega **desarrollo** — solo si antes pasa todo lo que prueba que lo viejo
sigue funcionando. Es `.github/workflows/pipeline.yml`, y lo que corre son las
mismas recetas de `just` que se corren a mano: el pipeline no tiene un camino
propio.

## Cuándo no corre

En `dev` no corren los flujos ni las pantallas en el navegador: un push ahí
pasa las validaciones y despliega desarrollo. Todo lo que va a producción sí
pasa por ellos, porque corren en cada push a `master`.


Un push que solo toca texto —cualquier `.md` y lo que hay en `docs/`— no corre
nada: ninguna prueba lo lee y nada lo despliega. La excepción es
`docs/openapi.json`, que es el contrato de la API y sí cuenta. Un push que mezcla
texto y código corre entero. *Run workflow* corre siempre.

## Qué corre, en orden

| Trabajo | Qué hace | Si falla |
|---|---|---|
| **Validaciones** | `just check-all` —formato, lint, tipos, las pruebas unitarias y de integración, el contrato de la API y el frontend con sus pruebas— y `just infra-check`, que la plantilla sea desplegable en los dos entornos | Nada llega a AWS |
| **Flujos y pantallas** (solo en `master`) | Levanta la pila local entera en el runner (moto, datos de prueba, API, workers y Vite) y corre `just verify` —dos usuarios de punta a punta, con integridad y aislamiento— y `just e2e`: cada suite del navegador de esa rama y `e2e-views`, que abre todas las pantallas en teléfono y escritorio | Nada llega a AWS; los logs quedan como artefacto |
| **Desplegar y comprobar** | Aprovisiona, despliega el backend, publica la web y corre el smoke contra lo desplegado: `smoke-prod` (solo lee) en producción, `smoke` en desarrollo | Vuelve sola a la versión anterior —backend y web— y la ejecución termina en rojo |

Todo lo de los dos primeros trabajos corre **antes** de tocar AWS y contra el
emulador, así que prueba el código nuevo. El smoke no puede: prueba la API que
está desplegada, y por eso va después del despliegue, con la reversión como red.

**Backend y web salen siempre juntos.** Una web publicada contra una API que
aún no existe rompe la pantalla (pasó el 2026-09-01).

**Nunca corren dos a la vez** en la misma rama, y nunca se cancelan a mitad: un
despliegue cortado entre el backend y la web los deja desacompasados.

## Cómo sabe a qué volver

Al terminar bien, el pipeline mueve la etiqueta `deployed/production` (o
`deployed/development`) al commit que acaba de desplegar. Si el siguiente
despliegue o su smoke fallan, saca esa etiqueta en un árbol aparte y corre **el
mismo script** de despliegue sobre él (`scripts/ci/deploy.sh <entorno>
../previous`). La reversión no es un camino distinto que solo se ejercita el
peor día.

La primera vez no hay etiqueta, así que no hay a qué volver: un fallo solo
deja la ejecución en rojo.

Aprovisionar no se revierte, a propósito: crea o ajusta tablas y colas, nunca
borra, y la versión anterior ignora lo que no conoce.

## Lo que hay que crear una vez

Todo en la cuenta `792884702854` y en el repositorio `lStiven/finflow`.

### 1. AWS: el proveedor OIDC de GitHub

Una sola vez por cuenta. Deja a GitHub pedir credenciales temporales, así que
**no se guarda ninguna llave de AWS en GitHub**.

```bash
aws iam create-open-id-connect-provider \
  --url https://token.actions.githubusercontent.com \
  --client-id-list sts.amazonaws.com
```

### 2. AWS: un rol por entorno

Desarrollo y producción viven en la misma cuenta (ver Trabas en
`PROGRESS.md`), así que lo único que separa un push a `dev` de producción es
que cada rol solo lo puede asumir **su** entorno de GitHub. Las políticas de
confianza ya están escritas: `infra/iam/github-oidc-trust-production.json` y
`...-development.json`.
El `sub` lleva los identificadores numéricos de la cuenta y del repo
(`lStiven@25795990/finflow@1342916879`), que es como GitHub lo emite para este
repo: si el repo se renombra o se recrea con el mismo nombre, el número cambia y
AWS deja de confiar en él. Cada una exige el entorno de GitHub **y** la rama (`master` o `dev`): la regla
de ramas del entorno vive en GitHub, y esta es la misma cerradura del lado de
AWS, por si aquella se cambia algún día.

```bash
aws iam create-role --role-name finflow-deploy-github-production \
  --assume-role-policy-document file://infra/iam/github-oidc-trust-production.json
aws iam create-role --role-name finflow-deploy-github-development \
  --assume-role-policy-document file://infra/iam/github-oidc-trust-development.json
```

A cada rol, **los mismos permisos que ya tiene el usuario con el que despliegas
ese entorno a mano**. `infra/iam/finflow-deploy-policy.json` es la referencia,
pero el 2026-09-15 se comprobó que no es un espejo de lo adjunto; copiar las
políticas del usuario real es lo que evita descubrir un permiso que falta a
mitad del primer despliegue.

### 3. GitHub: dos entornos

En *Settings → Environments*, `production` y `development`:

- **Deployment branches**: `production` solo desde `master`, `development`
  solo desde `dev`.
- **Variables**:
  - `AWS_ROLE_ARN`: el ARN del rol de ese entorno.
  - `CLOUDFLARE_ACCOUNT_ID`: el de tu cuenta de Cloudflare
    (`npx wrangler whoami` lo muestra).
- **Secrets**:
  - `ENV_FILE`: el contenido completo de `.env.production` o de
    `.env.development`, tal cual está en tu máquina, incluida la línea
    `AWS_PROFILE`. El pipeline crea un perfil con ese nombre.
  - `CLOUDFLARE_API_TOKEN`: un token con permiso *Cloudflare Pages: Edit*.

### 4. Recomendado: proteger `master`

*Settings → Branches*: exigir que pasen **Validaciones** y **Flujos y
pantallas** antes de mezclar a `master`. Así lo que llega a `master` —y por
tanto a producción— ya pasó por el pipeline en su rama.

## Probarlo sin esperar un push

*Actions → Validar y desplegar → Run workflow*, eligiendo la rama. Y en local,
lo mismo que corre el trabajo de flujos (sin `.env`, el script lo arma con
`scripts/ci/local-env.sh`: el ejemplo más las dos claves que deja vacías a
propósito y sin las que la pila no arranca —el secreto de los tokens, que se
genera en cada corrida, y una dirección de buzón que nadie lee—):

```bash
scripts/ci/local-stack.sh start
just verify && just e2e
scripts/ci/local-stack.sh stop
```

## Lo que sigue siendo a mano

- Registrar el webhook de Telegram y poner sus secretos en SSM, una vez por
  entorno (ver [alerts.md](alerts.md)).
- Borrar recursos que ya no se usan, como el índice viejo `by_user` de la tabla
  de notificaciones.
