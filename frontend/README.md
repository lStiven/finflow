# frontend

La app web. Una SPA estática —Vite, React, TypeScript— servida desde Cloudflare
Pages y que habla con la API por HTTPS con un token bearer.

Por qué vive en este repositorio y no en uno propio, y por qué no es Next.js:
ver **Frontend** en [`docs/decisions.md`](../docs/decisions.md). Qué es Finflow
y cómo funciona: el [README del proyecto](../README.md).

## Levantarla

```bash
just web-install                  # una vez
cp .env.example .env.development  # apunta a http://localhost:8000
just up                           # emulador, datos demo, la API y los workers
just web                          # esta app, en http://localhost:5173
```

Los dos puertos llegan al navegador del host porque los dos procesos escuchan
en todas las interfaces: dentro de un DevContainer, un bind a loopback es
invisible para el puerto publicado de Docker, así que la página cargaría en el
5173 y cada llamada que hace fallaría —el fetch ocurre en tu navegador, no en el
contenedor—. `just up` antes necesitaba `--api-host 0.0.0.0` por eso y ahora lo
hace solo; el flag sigue aceptando `127.0.0.1` si quieres la API encerrada. La
razón completa está en
[docs/running.md](../docs/running.md#4-los-seis-procesos).

Entra con la cuenta sembrada: `demo@finflow.local` / `una frase larga de verdad`.

### Con qué backend habla

Con `VITE_API_BASE_URL` en `.env.development`, y con nada más. Es todo el
mecanismo: los tres entornos del backend son tres valores de esta variable —
`http://localhost:8000`, o la `ApiUrl` de `just deploy-outputs-dev` /
`just deploy-outputs-prod`.

Dos consecuencias que conviene saber antes de que cuesten una tarde:

- **Nada avisa cuando las dos mitades no coinciden.** La API local puede estar
  arriba en el 8000 mientras esta app le habla a AWS. El archivo está en
  `.gitignore`, así que un `git diff` tampoco lo muestra. La respuesta está en
  la pestaña **Network** del navegador: mira el dominio de la petición, no la
  barra de direcciones.
- **La cuenta sembrada solo existe en local.** `just seed` llena el emulador
  moto; en un entorno desplegado no hay nada que sembrar, así que
  `demo@finflow.local` recibe un 401. Regístrate una vez desde la app.

Todo lo que hay en estos archivos viaja dentro del bundle: nunca pongas un
secreto ahí. El origen que sirva esta app tiene que estar también en
`API_CORS_ORIGINS` del backend, o cada llamada muere en el preflight.

El panorama completo, para los tres entornos y las dos mitades:
[`docs/running.md`](../docs/running.md#el-frontend).

### Si el navegador no llega

**Abre `http://localhost:5173`, y solo esa.** Vite imprime una segunda URL,
«Network» —`http://172.17.x.x:5173`—, que es la dirección del contenedor en el
puente de Docker. Solo existe dentro de la red de Docker y desde el host no
contesta nada, así que falla con `ERR_CONNECTION_REFUSED` haya lo que haya
configurado. Es la causa más común de «el frontend no levanta», porque parece
la más específica de las dos. El servidor de desarrollo imprime una línea
debajo de las URLs diciéndolo.

Pasado eso, dos fallas reales se ven idénticas desde el navegador. Distínguelas
antes de cambiar nada, porque el arreglo de una no hace nada por la otra.

**Si la página dice *"Blocked request. This host is not allowed"***, el
servidor está arriba y contestando; lo que rechaza es el **nombre** que usaste.
Vite reconoce `localhost` y las IP sueltas y nada más, así que el túnel del
editor (`*.devtunnels.ms`), `host.docker.internal` y un teléfono que llega a la
máquina por nombre eran callejones sin salida. `server.allowedHosts` en
`vite.config.ts` apaga esa comprobación. No hace falta reconstruir nada: Vite
recarga su propia configuración.

**Si la conexión se rechaza en `localhost` mismo**, no hay nada escuchando en
el host. Eso son los puertos: un DevContainer no publica ninguno a nivel de
Docker por sí solo, así que `appPort` en `.devcontainer/devcontainer.json`
publica el 5173 y el 8000 como puertos reales de Docker en vez de depender del
túnel del editor. Solo surte efecto después de **Dev Containers: Rebuild
Container**, no de un reinicio. Compruébalo desde dentro del contenedor, en este
orden —la última línea es la que prueba que el host llega, porque
`host.docker.internal` *es* el host—:

```bash
docker ps --format '{{.Ports}}'                 # 0.0.0.0:5173->5173, 0.0.0.0:8000->8000
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5173/
curl -s -o /dev/null -w '%{http_code}\n' http://host.docker.internal:5173/
```

Importan los dos puertos. Con solo el 5173 publicado la página carga y cada
petición falla, porque el fetch corre en el navegador del host, no en el
contenedor.

### Abrirla desde un teléfono

Es el punto del producto, así que vale la pena decir la cadena entera. El
teléfono necesita una ruta hasta el contenedor (el túnel de puertos del editor
es la fácil), y `allowedHosts`, arriba, es lo que impide que Vite rechace el
nombre con el que llega. La API es la parte que se olvida: el fetch corre en el
teléfono, así que `VITE_API_BASE_URL` tiene que ser una dirección que el
*teléfono* pueda resolver —`localhost:8000` es el propio teléfono— y ese origen
tiene que estar en `API_CORS_ORIGINS` del backend, o cada llamada muere en el
preflight.

## El contrato

`src/api/schema.d.ts` es **generado**. No lo edites.

```bash
just web-types   # vuelve a exportar docs/openapi.json y regenera los tipos
```

Córrelo después de tocar cualquier router o modelo de respuesta en Python. El
sentido del arreglo es que una ruta, un parámetro o un campo de respuesta que
cambia en el backend haga que este proyecto deje de compilar, en vez de fallar
en el navegador de quien abra esa pantalla después. `just openapi-check` falla
cuando el contrato guardado se desvió del código.

[`docs/frontend-integration.md`](../docs/frontend-integration.md) es el
contrato de negocio que las pantallas tienen que respetar: qué significa «sin
asignar», por qué los pasivos son positivos, por qué dos monedas nunca se suman,
cómo se deshace un cobro de factura. Léelo antes de construir una pantalla, no
después.

## Comprobaciones

```bash
just web-check   # biome, tsc, la comprobación de obsoletos y las pruebas
just web-fix     # aplica lo que biome sabe arreglar
just web-audit   # npm audit + la comprobación de obsoletos, por su cuenta
just check-all   # las dos mitades del repositorio
```

`scripts/check-deprecated.mjs` hace fallar el build cuando este proyecto importa
un símbolo que sus propias dependencias marcan `@deprecated`. Entiende los
imports: un símbolo cuenta solo cuando lo importamos del paquete que lo depreca,
y `@types/*` se resuelve junto al paquete de runtime, que es donde se escondía
la deprecación que motivó el script (`FormEvent`). Lee el comentario al inicio
de ese archivo antes de extenderlo.

Las pruebas cubren las reglas en las que una pantalla no es de fiar por su
cuenta, no las pantallas. `lib/money.ts` y `lib/dates.ts` son el núcleo: que el
saldo positivo de un pasivo se lea como plata que se debe, que una tarjeta
saldada no diga «Debes», que ningún monto pase por un float, y que los meses se
agrupen en Bogotá y no en la zona del navegador. Lo mismo vale para lo que
aceptan los formularios de una cuenta (`accounts/`), lo que una corrección de
comercio no puede quitar (`merchants/`), lo que borrar un movimiento dice que
pasará (`lib/deletion.ts`), los dos roles de un traslado (`lib/transfers.ts`), y
las cifras de facturas, presupuestos y el disponible (`bills/`, `budgets/`,
`plan/`). Añade pruebas ahí antes de cambiar cualquiera de esos archivos.

### En un navegador de verdad

Una pantalla se comprueba mirándola. Con `just up` en una terminal y `just web`
en otra:

```bash
just shot        # Chromium sin cabeza sobre la app, PNGs en .screenshots/ (ignorado por git)
just e2e         # todas las suites, contra la pila real
just e2e-views   # cada pantalla, en teléfono y escritorio
```

Cada suite (`e2e-bills`, `e2e-budgets`, `e2e-transacciones`, …) conduce una
pantalla contra la pila real; las de dinero comprueban además que lo que la
pantalla enseña cuadra con lo que el servidor guardó.
`e2e-views` lee las pantallas de `src/routes`, así que una nueva queda cubierta
el día que llega, y falla ante un error de JavaScript o de consola, una
respuesta de la API de 400 o más (salvo los 404 que la app pide a propósito),
scroll lateral, o una página sin exactamente un `h1`.

## Mapa

```text
src/
├── api/          client.ts (auth, mapeo de errores), queries.ts (cada llamada), schema.d.ts
├── auth/         token.ts (sesión), AuthContext.tsx
├── lib/          money.ts, dates.ts — las reglas que no se pueden equivocar — y
│                 la corrección, el borrado, la exportación y los traslados
├── accounts/     el texto y la validación que necesitan los formularios de una cuenta
├── merchants/    lo mismo para comercios: categorías, origen de los alias, qué
│                 grafía no se le puede quitar a un comercio
├── transfers/    declarar después que un movimiento fue un traslado
├── bills/        el calendario de cobros, cómo se ve cada uno y lo que el detector propone
├── plan/         la tarjeta del disponible del mes
├── budgets/      el progreso de cada presupuesto y su resumen
├── reports/      la forma de las gráficas y el reporte por categoría
├── alerts/       Telegram, la campana y el aviso de lectura pausada
├── onboarding/   la guía para conectar el banco y el diagnóstico de la conexión
├── guides/       las guías dentro de la app
├── navigation/   los destinos, y cuáles caben en la barra del teléfono — lo que no
│                 cabe tiene que caer en la hoja, y una prueba lo exige
├── components/   primitivas en ui/, gráficas en charts/, el marco (AppShell) y Money
└── routes/       por archivos; routeTree.gen.ts lo genera el plugin de Vite
```

Dos archivos cargan con casi todo el riesgo. `lib/money.ts` nunca convierte un
decimal en string a número, y `describeBalance` es el único lugar que decide que
el saldo positivo de una tarjeta de crédito significa *debes eso*. Cambiar
cualquiera de los dos sin leer la guía de integración es como esta app empieza
a mentir.

## Tamaños

Finflow se abre desde un teléfono, así que el teléfono no es el caso degradado:
es el caso. Dos reglas sostienen el diseño, y las dos se rompieron antes de
escribirse:

- **Nada se desplaza de lado.** Una pista de grid mide al menos el min-content
  de su elemento, y un `truncate` nunca parte la línea, así que una lista de
  movimientos mantuvo abierto todo el dashboard a 440px. Los hijos de un grid
  llevan `min-w-0`.
- **Cada destino se alcanza en cualquier ancho.** Por debajo de `lg` la barra
  muestra tres y `MoreSheet` lleva el resto; por encima, el riel los muestra
  todos.

`scripts/shot.mjs` fotografía un ancho; comprobar un cambio contra varios es el
punto. 320, 360, 390, 430, 768, 1024 y 1440 son los que vale la pena mirar, más
un teléfono acostado.
