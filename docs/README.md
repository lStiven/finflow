# Documentación de Finflow

Cada guía tiene un trabajo. Busca aquí la tuya y no leas las otras.

| Quiero… | Guía |
|---|---|
| Entender qué hace el sistema y por qué está partido así | [overview.md](overview.md) |
| **Levantar la app en local** (backend, workers y frontend) | [running.md](running.md#local) |
| **Correrla contra AWS** desde mi máquina, en development o producción | [running.md](running.md) |
| **Desplegar el backend** a development o a producción | [deploy.md](deploy.md#backend--development) |
| **Publicar el frontend** | [deploy.md](deploy.md#frontend--cloudflare-pages) |
| **Integrar el frontend con la API**: endpoints, orden de llamadas, reglas | [frontend-integration.md](frontend-integration.md) |
| Trabajar dentro del frontend (tipos, dev server, diagnóstico) | [../frontend/README.md](../frontend/README.md) |
| Saber cómo un usuario conecta su banco | [email-forwarding.md](email-forwarding.md) |
| Probar la API a mano | [postman/](postman/README.md) |
| Por qué algo está hecho así, y qué se descartó | [decisions.md](decisions.md) |

## Los cuatro caminos, en corto

**Local, todo en una terminal.** El emulador, los recursos, los datos demo y
los cinco procesos:

```bash
cp .env.example .env       # y edita dos valores
just up                    # API en http://localhost:8000
just web                   # frontend en http://localhost:5173
```

**Desplegar el backend.** Aprovisionar primero, desplegar después; el orden no
es negociable:

```bash
just provision-dev && just deploy-dev && just smoke <ApiUrl>     # development
just provision-prod && just deploy-prod && just smoke-prod <ApiUrl>
```

**Publicar el frontend.** Construye contra la `ApiUrl` del stack y sube:

```bash
just web-publish-dev       # o `just web-publish` para producción
```

Y después, una sola vez: el origen que imprime va a `CorsOrigins` en
`infra/samconfig.toml` y el backend se vuelve a desplegar.

**Integrar.** El contrato es `docs/openapi.json`, generado desde los routers;
de él salen los tipos TypeScript del frontend:

```bash
just web-types    # regenera el contrato y los tipos
just check-all    # las dos mitades
```
