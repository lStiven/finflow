# frontend

The web app. A static SPA — Vite, React, TypeScript — served from a CDN and
talking to the API over HTTPS with a bearer token.

Why it lives here and not in its own repository, and why it is not Next.js:
see **Frontend** in [`docs/decisions.md`](../docs/decisions.md).

## Running it

```bash
just web-install                      # once
cp .env.example .env.development      # points at http://localhost:8000
just up --api-host 0.0.0.0            # emulator, seed data, API and the four workers
just web                              # this, on http://localhost:5173
```

**`--api-host 0.0.0.0` is not optional here.** `just up` binds the container's
loopback by default, which the host's browser cannot reach: the page loads on
5173 and every call it makes fails, because the fetch happens in your browser
and not in the container. `just dev` binds every interface on its own, so the
same URL works there and not here — that asymmetry is what usually costs the
hour. The full reason is in [docs/running.md](../docs/running.md#4-los-seis-procesos).

`just dev` alone is enough if you only need the API. Log in with the seeded
account: `demo@finflow.local` / `una frase larga de verdad`.

### Which backend it talks to

`VITE_API_BASE_URL` in `.env.development`, and nothing else. It is the whole
mechanism: the backend's three environments are three values of this one
variable — `http://localhost:8000`, or the `ApiUrl` of `just
deploy-outputs-dev` / `just deploy-outputs-prod`.

Two consequences worth knowing before they cost you an afternoon:

- **Nothing warns you when the two halves disagree.** `just dev` can be up on
  8000 while this app talks to AWS. The file is gitignored, so a `git diff`
  will not show it either. The browser's **Network** tab is the answer — read
  the request domain, not the address bar.
- **The seeded account exists only locally.** `just seed` fills the moto
  emulator; there is nothing to seed in a deployed environment, so
  `demo@finflow.local` gets a 401 there. Register once from the app instead.

Everything in these files ships inside the bundle — never put a secret there.
Whatever origin serves this app must also be in the backend's
`API_CORS_ORIGINS`, or every call dies in preflight.

The whole picture, for all three environments and both halves:
[`docs/running.md`](../docs/running.md#el-frontend).

### If the browser cannot reach it

**Open `http://localhost:5173`, and only that.** Vite prints a second,
"Network" URL — `http://172.17.x.x:5173` — which is the container's address
on Docker's bridge. It exists only inside Docker's network and answers
nothing from the host, so it fails with `ERR_CONNECTION_REFUSED` no matter
what else is configured. It is the single most common cause of "the frontend
does not come up", because it looks like the more specific of the two. The
dev server prints a line under the URLs saying so.

Past that, two real faults look identical from the browser. Tell them apart
before changing anything, because the fix for one does nothing for the other.

**If the page says *"Blocked request. This host is not allowed"***, the
server is up and answering; it is refusing the **name** you used. Vite
recognises `localhost` and bare IPs and nothing else, so the editor's tunnel
(`*.devtunnels.ms`), `host.docker.internal` and a phone reaching the machine
by hostname were all dead ends. `server.allowedHosts` in `vite.config.ts`
turns that check off. No rebuild is involved — Vite reloads its own config.

**If the connection is refused on `localhost` itself**, nothing is listening
on the host. That one is the ports: a DevContainer publishes none at the
Docker level on its own, so `appPort` in `.devcontainer/devcontainer.json`
publishes 5173 and 8000 as real Docker ports rather than depending on the
editor's tunnel. That takes effect only after **Dev Containers: Rebuild
Container**, not a plain restart. Check it from inside the container, in this
order — the last line is the one that proves the host can reach it, because
`host.docker.internal` *is* the host:

```bash
docker ps --format '{{.Ports}}'                 # 0.0.0.0:5173->5173, 0.0.0.0:8000->8000
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:5173/
curl -s -o /dev/null -w '%{http_code}\n' http://host.docker.internal:5173/
```

Both ports matter. With only 5173 published the page loads and every request
fails, because the fetch runs in the host's browser, not in the container.

### Opening it from a phone

The point of the product, so it is worth stating the whole chain. The phone
needs a route to the container (the editor's forwarded-port tunnel is the
easy one), and `allowedHosts` above is what stops Vite rejecting the
hostname it arrives under. The API is the part people forget: the fetch runs
on the phone, so `VITE_API_BASE_URL` must be an address the *phone* can
resolve — `localhost:8000` is the phone itself — and that origin must be in
the backend's `API_CORS_ORIGINS` or every call dies in preflight.

## The contract

`src/api/schema.d.ts` is **generated**. Do not edit it.

```bash
just web-types   # re-exports docs/openapi.json, then regenerates the types
```

Run that after touching any router or response model in Python. The point of
the arrangement is that a path, a query parameter or a response field that
changes on the backend stops this project from compiling, rather than failing
in a browser for whoever opens that screen next. `just openapi-check` fails
when the committed contract has drifted from the code.

[`docs/frontend-integration.md`](../docs/frontend-integration.md) is the
business contract the screens have to honour — what "unassigned" means, why
liabilities are positive, why two currencies are never summed. Read it before
building a screen, not after.

## Checks

```bash
just web-check   # biome, tsc, the deprecation check, and the tests
just web-fix     # apply what biome can fix
just web-audit   # npm audit + the deprecation check, on their own
just check-all   # both halves of the repo
```

`scripts/check-deprecated.mjs` fails the build when this project imports a
symbol its own dependencies mark `@deprecated`. It is import-aware — a symbol
counts only when we import it from the package that deprecates it, and
`@types/*` is resolved alongside the runtime package, which is where the
deprecation that prompted the script (`FormEvent`) was hiding. Read the
comment at the top of that file before extending it.

The tests cover the rules a screen cannot be trusted to get right on its own,
not the screens themselves. `lib/money.ts` and `lib/dates.ts` are the core of
it — that a liability's positive balance reads as money owed, that a paid-off
card is not captioned "Debes", that no amount ever passes through a float, and
that months are grouped in Bogota rather than in the browser's timezone — and
the same applies to what an account's forms accept (`accounts/`), what a
merchant correction may not take away (`merchants/`), what deleting a movement
says will happen (`lib/deletion.ts`), and the two transfer roles
(`lib/transfers.ts`). Add to them before changing any of those files.

A screen itself is checked by looking at it: `just shot` drives a headless
Chromium over the running app (`just up --api-host 0.0.0.0` in one terminal,
`just web` in another) and writes PNGs to `.screenshots/`, which git ignores.

## Layout

```
src/
├── api/        client.ts (auth, error mapping), queries.ts (every call), schema.d.ts
├── auth/       token.ts (session storage), AuthContext.tsx
├── lib/        money.ts, dates.ts — the rules that must not be got wrong
├── accounts/   the copy and the validation an account's forms need
├── merchants/  the same for merchants: categories, alias origins, what a
│               spelling may not be taken away from
├── navigation/ the destinations, and which of them the phone's bar can fit —
│               what falls out of the bar has to land in the sheet, and a
│               test says so
├── components/ primitives in ui/, plus Money
└── routes/     file-based; routeTree.gen.ts is generated by the Vite plugin
```

Two files carry most of the risk. `lib/money.ts` never converts a decimal
string to a number, and `describeBalance` is the single place deciding that a
credit card's positive balance means *you owe that much*. Changing either
without reading the integration guide is how this app starts lying.

## Sizes

Finflow is opened from a phone, so a phone is not the degraded case — it is
the case. Two rules hold the layout together, and both were broken before they
were written down:

- **Nothing scrolls sideways.** A grid track is at least its item's
  min-content, and a `truncate` never wraps, so one list of movements held the
  whole dashboard open at 440px. Grid children get `min-w-0`.
- **Every destination is reachable at every width.** Below `lg` the bar shows
  three and `MoreSheet` carries the rest; above it, the rail shows all.

`scripts/shot.mjs` photographs a width; checking a change against several is
the point. 320, 360, 390, 430, 768, 1024 and 1440 are the ones worth a look,
plus a phone on its side.
