# Colección de API — Postman / Bruno

Dos archivos, formato estándar de Postman Collection v2.1 (Bruno importa este
mismo formato con "Import Collection" → "Postman Collection"):

- `finflow_v2.postman_collection.json` — los requests, agrupados por contexto
  acotado (Identity, Ingestion, Merchant, Financial), igual que el código.
- `finflow_v2.postman_environment.json` — el ambiente `finflow_v2 - local`.

## Importar

**Postman**: File → Import → arrastra los dos archivos.
**Bruno**: Import Collection → Postman Collection → el archivo de la
colección; luego Import Environment con el archivo de ambiente (o crea uno
nuevo y copia las 4 variables).

Selecciona el ambiente `finflow_v2 - local` antes de mandar cualquier
request.

## Variables del ambiente

| Variable | Cómo se llena |
|---|---|
| `base_url` | Fija, `http://localhost:8000` por defecto. Cámbiala para apuntar a otro despliegue. |
| `fetch_token` | El `access_token` crudo que devuelve Register/Login. |
| `token` | `"Bearer " + fetch_token` — el valor exacto que se manda en `Authorization`. |
| `user_id` | El id del usuario autenticado. |

`fetch_token`, `token` y `user_id` **se llenan solos**: los requests
Identity → Auth → Register y Login llevan un test script que lee la respuesta
y actualiza el ambiente. Corre cualquiera de los dos una vez y todos los demás
requests autenticados (`Authorization: {{token}}`) ya funcionan.

## Qué no está pensado para llamarse a mano

- **Bank notification webhook**: solo existe con `ENVIRONMENT=local` — es una
  costura de pruebas, no un camino de producto. En producción nada llama esto
  por HTTP: el `ingest worker` lee el buzón compartido directamente (ver
  [../overview.md](../overview.md)).

## Mantenimiento

Cada vez que se agrega un endpoint nuevo al backend, esta colección se
actualiza en el mismo cambio — es la regla en `CLAUDE.md`. Si un request
aquí no coincide con lo que expone la API, algo quedó desactualizado.
