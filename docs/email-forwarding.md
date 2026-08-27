# Cómo un usuario conecta su banco (guía de backend)

Contrato de API para el cliente (front, u otro backend) que lleva a un usuario
autenticado a obtener su dirección de reenvío y aprobar remitentes. Esto es
solo el lado del backend: qué endpoint llamar y qué esperar de vuelta. La UI
que lo envuelve —pantallas, textos— es decisión del front y no vive aquí.

Para el porqué del modelo (una sola cuenta de Gmail compartida, `+alias` por
usuario, nada de OAuth) ver
[overview.md](overview.md#el-modelo-de-entrada). Para configurar esa cuenta
como operador del despliegue, ver
[running.md](running.md#4-configurar-la-cuenta-de-ingesta).

## Las dos piezas

"Conectar el banco" son dos cosas, y ninguna de las dos requiere ir a la
consola de ningún proveedor:

1. **La dirección de reenvío** — se asigna sola, en el momento del registro.
   Es una función pura del `user_id` sobre la cuenta de Gmail que este
   despliegue posee (`finflowingest+<id>@gmail.com`). Nunca la elige el
   usuario, nunca la manda el cliente, no hay nada que colisionar.
2. **Los remitentes aprobados** — a qué direcciones/dominios se les permite
   generar transacciones. Los pone el usuario, explícitamente.

Sin remitentes aprobados, la dirección existe pero **no acepta nada** — el
valor seguro por defecto, nunca "aceptarlo todo mientras tanto".

## 1. Registrarse ya asigna la dirección

```
POST /identity/register
Content-Type: application/json

{
  "email": "yo@example.com",
  "password": "una frase larga de verdad",
  "allowed_domains": [],
  "allowed_addresses": []
}
```

`allowed_domains`/`allowed_addresses` son opcionales — se pueden dejar vacíos
y aprobar remitentes después. La respuesta es la de siempre (token, no
incluye la dirección); pídela con el siguiente paso.

## 2. Ver la dirección de reenvío

```
GET /identity/inbox
Authorization: Bearer <token>
```

```json
{
  "address": "finflowingest+8f3c1a2b9e7d4f0a8b6c5d4e3f2a1b0c@gmail.com",
  "allowed_domains": [],
  "allowed_addresses": []
}
```

Esta es la dirección que se le muestra al usuario para que la use en el
paso 4. No cambia nunca para ese usuario — es determinista, no un token que
expira ni se regenera.

## 3. Aprobar remitentes

```
PATCH /identity/inbox
Authorization: Bearer <token>
Content-Type: application/json

{
  "allowed_domains": ["an.notificacionesbancolombia.com"],
  "allowed_addresses": []
}
```

**Reemplaza la lista completa**, no la extiende — mandar `allowed_domains`
vacío borra los dominios ya aprobados. La respuesta es el mismo objeto que
`GET /identity/inbox`, ya actualizado.

## 4. El usuario configura el reenvío (esto lo hace él, no la API)

En Gmail — la mayoría de bancos notifican a un Gmail o algo que se comporta
igual —:

1. **Configuración** (⚙️) → **Ver toda la configuración** → pestaña
   **Reenvío y POP/IMAP**.
2. **Agregar una dirección de reenvío** → pega la dirección del paso 2.
3. Gmail manda una solicitud de confirmación **a esa dirección**. Ya no hay
   que hacer nada: el `ingest worker` la reconoce y la confirma solo, en su
   siguiente pasada. El log lo cuenta aparte, como `confirmations=1`.

   Es **una por usuario**, no una por instalación: cada quien tiene su propio
   `+alias`, así que Gmail pide confirmación para cada uno. Antes eso
   significaba que el operador tenía que entrar al buzón compartido a buscar
   el enlace de cada persona, porque nadie más puede leer esa cuenta.
4. Ya verificada, el usuario crea un **filtro**: "de:
   `alertasynotificaciones@an.notificacionesbancolombia.com`" → **Reenviar a**
   → su dirección.

A partir de ahí, cada alerta nueva se copia sola. El `ingest worker` la
recoge en su siguiente pasada (por defecto, cada
`INGESTION_INGEST_POLL_INTERVAL_SECONDS` segundos — 60 por defecto).

## Lo que hay que saber sobre este modelo

- **El reenvío automático de Gmail preserva el remitente original** en el
  `From` — a diferencia de darle clic manual a "Reenviar", que reescribe el
  correo como si lo mandara el usuario. El filtro por `allowed_domains` sigue
  funcionando exactamente igual que si hubiéramos leído el original.
- **Un correo reenviado casi siempre falla SPF/DKIM** en quien lo recibe,
  porque ya no viene del servidor real del banco. Esto es un riesgo residual
  conocido, no resuelto todavía: en teoría, alguien que adivinara la
  dirección de un usuario *y* conociera la dirección exacta de su banco
  podría mandar un correo que pase el filtro de remitente sin haber pasado
  por el banco de verdad. La dirección en sí es difícil de adivinar (deriva
  de un UUID completo), pero no hay verificación de autenticidad del correo
  entrante más allá de eso todavía.
- **Confirmar un reenvío no concede nada nuevo.** Quien ya tenga la
  dirección de un usuario puede escribirle directo; el reenvío es una ruta de
  entrega, no un permiso. Lo que decide si un mensaje llega al ledger sigue
  siendo el filtro de remitentes aprobados, igual en los dos casos.
- **El enlace de confirmación se sigue con la correa corta.** Viene dentro de
  un correo, que es entrada no confiable, así que está fijado por esquema,
  host exacto (`mail-settings.google.com`) y el prefijo de ruta `vf-` que lo
  distingue del enlace de *cancelar* que viene en el mismo mensaje. No se
  siguen redirecciones. Un enlace que no cumpla todo eso no se pide.
- **Nunca se lee el buzón del usuario.** El `ingest worker` solo abre la
  cuenta que el propio despliegue posee — nunca pide permiso sobre la cuenta
  de nadie más, porque nunca la toca.

## Errores que puede devolver este contrato

| Código | Cuándo | Cuerpo típico |
|---|---|---|
| `401` | Falta o es inválido el `Authorization: Bearer`. | — |
| `409` | `POST /identity/register` con un email ya registrado. | `{"detail": "An account with this email already exists"}` |
| `422` | Cuerpo inválido (contraseña débil, dirección de remitente sin `@`). | `{"detail": "..."}` |
| `404` | `GET`/`PATCH /identity/inbox` para una cuenta sin inbox — no debería ocurrir nunca en la práctica, el registro siempre la crea. | `{"detail": "No inbox found for this account"}` |

## Lo que el cliente nunca ve ni maneja

- La App Password de la cuenta de ingesta no se expone en ningún endpoint —
  es configuración del operador, en `.env`/gestor de secretos, nunca visible
  para un usuario final.
- No hay tokens de OAuth, refresh tokens, ni suscripciones que renovar del
  lado del usuario — todo lo que había que gestionar ahí desapareció con el
  modelo anterior.
