# Avisos por Telegram

Qué hace, cómo se configura y qué falla cuando no funciona.

La app manda un mensaje al teléfono de su dueño cada vez que se mueve plata.
Es lo que la convierte en algo que no hay que acordarse de abrir. El contexto
que lo hace se llama `alerts`, y es el quinto.

> **Ojo con el nombre.** `alerts` es el contexto de los avisos al usuario.
> `just alerts-dev` / `alerts-prod` son otra cosa completamente distinta:
> quién recibe las alarmas de CloudWatch. Las recetas de los avisos llevan
> `alerts-worker` y `telegram-*`.

---

## Qué recibe el usuario

Un mensaje de tres o cuatro líneas, en texto plano, segundos después de que el
movimiento entra:

```
Gasto $84.300
COMPRA EN *PAYU*COL
Bancolombia · 13/09 04:46 p. m.
Sin cuenta asignada
```

**Dice lo que dice el banco, no lo que dice la app.** La contraparte es el
texto crudo del correo —`COMPRA EN *PAYU*COL`, no `PayU`— y no hay total del
mes. Las dos cosas son datos de otros contextos: el nombre bonito es de
Merchant y el acumulado es de Financial. Leerlos ataría cada aviso a que dos
cosas más estén arriba justo en el momento en que se mueve la plata. El porqué
completo está en [decisions.md](decisions.md).

Entra todo lo que el ledger registra, venga del correo del banco o escrito a
mano, y tanto gastos como ingresos. **No entran los devengos**: los intereses y
seguros que la propia aplicación calcula llegan de a cuatro cuando alguien abre
la pantalla del crédito y pulsa *Actualizar*, y avisar de eso mientras su dueño
lo está mirando en pantalla es lo que hace que se apaguen los avisos de verdad.

Cada canal se apaga por separado y admite un **monto mínimo**: por debajo no se
manda nada. Es contra el ruido, no contra el dinero — un café de $3.000 no debe
gastar la atención que necesita un cargo de $400.000.

### La campana

Todo aviso que se mandó, o que se habría mandado, a Telegram queda también
en la app durante 30 días, **tenga o no el usuario un canal vinculado**
(`GET /alerts/inbox`). La pantalla lo consulta cada pocos segundos mientras
está visible y enseña lo nuevo como una notificación flotante: es lo más
parecido a tiempo real que tiene un despliegue sin sockets. Ocultar un aviso
(`DELETE /alerts/inbox/{id}`) es para siempre y no borra el mensaje de
Telegram.

### El resumen del lunes

Los lunes a las 08:00 de Bogotá, `WeeklySummaryFunction` le manda a cada
usuario lo que gastó la semana anterior, de lunes a domingo y por moneda,
comparado con lo normal de sus propias semanas anteriores, y la categoría que
más subió. La primera semana no hay con qué comparar, y el mensaje lo dice en
vez de comparar contra cero. Va a la campana y a cada canal que no lo haya
apagado: es el tipo `weekly_summary`, encendido por defecto, y se apaga con
el mismo `PATCH /alerts/channels/{id}` que los movimientos.

Correrlo dos veces la misma semana no manda nada dos veces, y un usuario con
datos raros no detiene a los demás. Solo conoce a quien ya tuvo algún
movimiento: Alerts arma su propia lista con lo que oye, sin preguntarle a otro
contexto quién existe. A mano, para probarlo o para reenviar una semana:

```bash
just weekly-summary              # la semana anterior a hoy
just weekly-summary 2026-09-21   # la semana que contiene ese día
```

Una semana que todavía no terminó se rechaza: su resumen ocuparía el lugar del
de la semana completa.

### Cuando el modelo deja de leer sus correos

Si alguien acumula strikes (ver [overview.md → Los strikes](overview.md#los-strikes)),
Ingestion publica `ModelReadingSuspended` y este contexto se lo dice por tres
lados:

- **La campana**, con una entrada `model_paused`.
- **Cada Telegram vinculado, aunque haya apagado los avisos.** No es un aviso
  que alguien pidió: es algo que le pasó a su cuenta.
- **Su correo**, con el texto aprobado en
  [model-strikes-copy.md](model-strikes-copy.md). Es el único mensaje de este
  contexto que no va por Telegram: quien nunca lo vinculó también tiene que
  enterarse. La dirección la da Identity a través de su caso de uso publicado,
  y el correo sale de la misma casilla que manda los códigos de verificación
  (`MailFromAddress` y `MailAppPasswordParameter`).

Como con los movimientos, se pregunta antes de mandar y se anota después:
recibir el mismo evento dos veces no avisa dos veces. Si el servidor de correo o
Telegram están caídos, el mensaje vuelve a la cola y el reintento solo completa
lo que faltó. En local no hay servidor de correo: el worker escribe el correo en
el log.

---

## Cómo se vincula

Un toque, sin códigos y sin buscar identificadores:

1. En **Perfil → Avisos por Telegram**, pulsar *Conectar Telegram*.
2. Se abre el bot con un enlace `t.me/<bot>?start=<token>`.
3. Pulsar **Empezar**. El bot responde «listo, te aviso por aquí».
4. La pantalla lo detecta sola y deja de preguntar.

El token del enlace **sirve una sola vez y vence en 15 minutos**. Se entrega en
la respuesta de `POST /alerts/channels` y en ningún otro sitio: no se guarda —
solo su hash— y `GET /alerts/channels` no lo devuelve. Si se pierde, se pide
otro; pedir otro retira el anterior, así que una cuenta nunca tiene dos enlaces
vivos.

**Un chat de Telegram pertenece a una sola cuenta de Finflow.** Si intentas
vincular uno que ya está en otra cuenta, el bot te lo dice ahí mismo. Dos
personas recibiendo sus movimientos en la misma conversación es una fuga que
ninguna de las dos aceptó.

La dirección nunca se enseña entera: la pantalla muestra los últimos cuatro
caracteres (`…6789`), que es todo lo que su dueño necesita para reconocerla.

---

## Configurarlo

### 1. Crear el bot

Con [@BotFather](https://t.me/BotFather) en Telegram: `/newbot`, un nombre, un
usuario terminado en `bot`. Devuelve un token con la forma
`1234567890:AA...`.

**Uno por entorno.** Development y producción hablan con personas reales, y un
solo bot no podría decir de cuál de los dos viene un aviso.

### 2. Inventar el secreto del webhook

No lo da Telegram: lo eliges tú, y es lo único que autentica el webhook.

```bash
uv run python -c "import secrets; print(secrets.token_hex(32))"
```

### 3. En local

Los dos van a `.env`, en texto:

```dotenv
ALERTS_TELEGRAM_BOT_TOKEN=1234567890:AA...
ALERTS_TELEGRAM_BOT_USERNAME=mi_bot
ALERTS_TELEGRAM_WEBHOOK_SECRET=el-que-acabas-de-generar
```

Sin token, el worker **escribe el mensaje en el log en vez de mandarlo**. Es
útil: ves el texto exacto sin gastar un bot. Esa rama se decide por
`ENVIRONMENT=local` y por nada más — un interruptor propio sería un interruptor
que alguien puede activar en producción.

El **secreto del webhook sí hace falta siempre**, también en local. Un valor
esperado vacío haría que la comparación en tiempo constante aceptara una
cabecera vacía, que es abrir la puerta en vez de entornarla. Cuando falta, el
endpoint rechaza a todo el mundo.

### 4. Desplegarlo

```bash
just secret-put /finflow/development/telegram-bot-token
just secret-put /finflow/development/telegram-webhook-secret
```

La receta es `secret-put` **seguida de la ruta**. `just /finflow/...` a secas no
es un comando.

Y el usuario del bot, que no es secreto, va en `parameter_overrides` de
`infra/samconfig.toml` junto a las rutas de los dos anteriores:

```toml
"TelegramBotUsername=mi_bot_dev",
"TelegramBotTokenParameter=/finflow/development/telegram-bot-token",
"TelegramWebhookSecretParameter=/finflow/development/telegram-webhook-secret",
```

> **Estos tres overrides no son opcionales.** `infra/template.yaml` los declara
> con un default que apunta a `/finflow/production/`, así que olvidarlos no deja
> el stack sin secreto: lo hace leer el del otro entorno. Ver
> [deploy.md](deploy.md#4-los-secretos-en-parameter-store).

### 5. Registrar el webhook

Una vez por entorno, después de desplegar. Telegram no sabe a dónde entregar
hasta que se lo dices, y la URL solo cambia cuando cambia el Function URL:

```bash
just deploy-outputs-dev     # de aquí sale ApiUrl
just telegram-webhook-dev https://<ApiUrl>/alerts/telegram/webhook
just telegram-webhook-info-dev
```

No es parte del despliegue a propósito: dos stacks registrándose contra un
mismo bot se pelearían, y el que perdiera dejaría de recibir updates en
silencio.

---

## Probarlo en local

Telegram no alcanza `localhost` y `setWebhook` exige un nombre público con
https, así que la mitad *de entrada* es la única que no se puede ejercer tal
cual. Se suplanta — que es justo para lo que sirve la cabecera secreta — y todo
lo demás sigue siendo real:

```bash
just aws-init                  # emulador, tablas, colas y la regla del bus
just up                        # api + los cinco workers

# 1. POST /alerts/channels  →  copia el token del link_url
just telegram-update <token>   # hace de Telegram: /start <token> al webhook

# 2. GET /alerts/channels  →  status "verified", chat_hint "…4321"
# 3. registra un movimiento (just seed, o POST /financial/transactions)
# 4. el mensaje aparece en el stream `alerts` de `just up`
```

El script acepta `--chat-type group` y `--secret <otro>` para comprobar los dos
rechazos: un grupo no vincula nunca, y una cabecera equivocada es un `403`.

---

## Cuando no funciona

| Síntoma | Causa casi siempre |
|---|---|
| `ParameterNotFound … /finflow/production/telegram-bot-token` al arrancar dev | Faltan los overrides en `samconfig.toml`; el stack cayó al default de producción |
| `503` en `POST /alerts/channels` | Ese despliegue no tiene bot configurado. El resto de la app funciona |
| El enlace abre el bot, pulsas Empezar y no pasa nada | El webhook no está registrado (`just telegram-webhook-info-<entorno>`) |
| `403` en el webhook | La cabecera no coincide con `ALERTS_TELEGRAM_WEBHOOK_SECRET`, o no hay secreto configurado |
| Vincula pero no llega el «listo» | El bot no puede escribir a ese chat. Se vincula igual; el aviso fallará por lo mismo |
| «Este Telegram ya está conectado a otra cuenta» | Es literal. Desvincúlalo en la otra cuenta primero |
| Los avisos llegan repetidos | Mira que `AlertsFunction` siga con `ReservedConcurrentExecutions: 1` |
| Nada llega y la cola crece | La alarma `<stack>-alerts-events-dead-letters` debería estar avisando |

Los logs están en `/finflow/<stack>/alerts`:

```bash
just deploy-logs-dev AlertsFunction
```

**No esperes encontrar el mensaje ahí.** Lo que se registra es el id del canal
y un código de estado; el texto lleva el monto y la contraparte de alguien.

---

## Dos decisiones que sorprenden al leer el código

### La marca de entrega se escribe *después* de mandar

Al revés que `ProcessedEventStore` de Merchant, que reclama antes de trabajar.
Los dos fallos no son simétricos:

- marcar antes → una caída entre la marca y el envío es **una compra de la que
  nadie se enteró**: silenciosa, sin recuperación, y justo lo que este contexto
  existe para evitar;
- marcar después → una caída entre el envío y la marca es **un mensaje
  repetido**: visible y molesto, nada más.

### Por qué una sola ejecución a la vez

Porque lo anterior solo se sostiene contra redelivery *secuencial*. Dos workers
a la vez leerían ambos «todavía no entregado» y mandarían los dos. A este
volumen, serializar no cuesta nada: `ReservedConcurrentExecutions: 1`.

---

## Qué falta

- **La hora es la misma para todo el mundo.** No hay zona horaria por usuario
  en ninguna parte del proyecto; el `13/09 04:46 p. m.` se calcula con
  `ALERTS_DISPLAY_TIMEZONE`. Deja de servir el día que alguien lo use desde
  otro huso.
- **Dos tipos de aviso.** «Se movió plata» y el resumen del lunes. El aviso de
  cobro recurrente (E2) y el de presupuesto (E4) se enchufan sin migrar nada:
  una preferencia que nadie ha expresado se resuelve con el valor por defecto
  que el propio tipo declara.
- **Un solo transporte.** El puerto `MessageSender` no sabe qué es Telegram,
  así que un segundo canal es un adaptador más y ningún cambio de dominio.
