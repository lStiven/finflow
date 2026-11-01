# Colección de API — Postman / Bruno

Dos archivos, formato estándar de Postman Collection v2.1 (Bruno importa este
mismo formato con "Import Collection" → "Postman Collection"):

- `finflow_v2.postman_collection.json` — los requests, agrupados por contexto
  acotado (Identity, Ingestion, Merchant, Financial, Alerts), igual que el
  código. Cubre las 91 operaciones del contrato, más el webhook local.
- `finflow_v2.postman_environment.json` — el ambiente `finflow_v2 - local`.

## Importar

**Postman**: File → Import → arrastra los dos archivos.
**Bruno**: Import Collection → Postman Collection → el archivo de la
colección; luego Import Environment con el archivo de ambiente (o crea uno
nuevo y copia sus variables).

Selecciona el ambiente `finflow_v2 - local` antes de mandar cualquier
request.

## Variables del ambiente

| Variable | Cómo se llena |
| --- | --- |
| `base_url` | Fija, `http://localhost:8000` por defecto. Cámbiala para apuntar a otro despliegue. |
| `fetch_token` | El `access_token` crudo que devuelve Register/Login. |
| `token` | `"Bearer " + fetch_token` — el valor exacto que se manda en `Authorization`. |
| `user_id` | El id del usuario autenticado. |

`fetch_token`, `token` y `user_id` **se llenan solos**: los requests
Identity → Auth → Register y Login llevan un test script que lee la respuesta
y actualiza el ambiente. Corre cualquiera de los dos una vez y todos los demás
requests autenticados (`Authorization: {{token}}`) ya funcionan.

El resto de las variables son ids que un request usa y otro produce. Algunas
también se llenan solas; las demás se copian de la respuesta que las trae:

| Variable | Se llena sola con | Si no, cópiala de |
| --- | --- | --- |
| `verification_code` | Identity → Verification → Request verification code (solo en local, donde la respuesta trae el código) | El correo |
| `verification_token` | Identity → Verification → Confirm verification code | — |
| `password_reset_token` | — | El enlace del correo de restablecimiento (`?token=…`) |
| `account_id` | Financial → Open account | `GET /financial/accounts` |
| `movement_id` | — | `GET /financial/transactions` |
| `bill_id`, `bill_period` | — | `GET /financial/bills` (`id` y el `due_on` de un cobro) |
| `budget_id` | — | `POST` o `GET /financial/budgets` |
| `budget_category`, `budget_month` | — | Una categoría de `GET /merchants/categories`; un mes como `2026-09` |
| `alert_channel_id`, `telegram_link_token` | Alerts → Create channel | — |
| `alert_entry_id` | Alerts → Inbox | — |
| `telegram_webhook_secret` | — | El valor de `ALERTS_TELEGRAM_WEBHOOK_SECRET` en tu `.env` |

## Qué no está pensado para llamarse a mano

- **Bank notification webhook**: solo existe con `ENVIRONMENT=local` — es una
  costura de pruebas, no un camino de producto. En producción nada llama esto
  por HTTP: el `ingest worker` lee el buzón compartido directamente (ver
  [../overview.md](../overview.md)).

## Mantenimiento

Cada vez que se agrega un endpoint nuevo al backend, esta colección se
actualiza en el mismo cambio — es la regla en `CLAUDE.md`. Si un request
aquí no coincide con lo que expone la API, algo quedó desactualizado.
