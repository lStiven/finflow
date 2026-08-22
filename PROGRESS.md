# Progress

Living log of where the work stands. This exists so a fresh session (after
`/clear` or a new session) can pick up without replaying old conversation —
read this first, and update it after finishing a task or a meaningful chunk
of work, instead of relying on conversation history to carry it forward.

## Current focus

Nothing in-flight — last task (Postman/Bruno collection + `.vscode` untrack)
is done.

## Last completed

- 2026-08-22 — Added a standing workflow rule (`CLAUDE.md`): every time an
  endpoint is added/changed/removed, update
  `docs/postman/finflow_v2.postman_collection.json` in the same change.
  Built the initial collection now, covering every current endpoint
  (Identity/Ingestion/Merchant, grouped by bounded context) plus
  `docs/postman/finflow_v2.postman_environment.json` with the 4 requested
  variables (`base_url`, `fetch_token`, `token`, `user_id`) — Register/Login
  carry a test script that fills the last three from the response
  automatically. `docs/postman/README.md` explains import + maintenance.
  Postman Collection v2.1 format, importable by both Postman and Bruno.
- 2026-08-22 — Untracked `.vscode/settings.json`: it was staged (not yet
  committed) despite `?? .vscode/` at session start, and `.gitignore` had an
  explicit `!.vscode/settings.json` exception re-including it. Unstaged it
  and removed that exception, so it stays local-only going forward.
- 2026-08-22 — Built the current-month mailbox backfill feature (Ingestion +
  Identity), opt-in and separate from the ordinary forward-only sync:
  - `MailboxReader.fetch_range` added to the port; implemented for both Gmail
    (`GmailApiClient.list_messages` + `GmailMailboxReader.fetch_range`, via
    Gmail search since history only retains ~7 days) and the simulated
    provider (`SimulatedMailboxReader.fetch_range`).
  - New `BackfillMailboxUseCase` / `BackfillUserMailboxesUseCase` in
    `ingestion/application/backfill_handlers.py`, reusing
    `ReceiveBankNotificationUseCase`'s dedup path — never touches the
    incremental cursor.
  - Wired through identity's `MailboxConnector` port/adapter
    (`IngestionMailboxConnector`) to a new endpoint:
    `POST /identity/mailboxes/backfill`.
  - Tests: `tests/unit/ingestion/application/test_backfill_mailbox.py` (new),
    `tests/integration/ingestion/test_mailbox_backfill_flow.py` (new),
    `test_sync_mailbox.py` and `test_connect_mailbox.py` fakes updated for the
    port change. `just prepare` clean, 435 tests passing.
  - Docs updated: `docs/mailbox-connection.md` (new "Ponerse al día con el mes
    en curso" section) and the endpoint table in `docs/running.md`.
- 2026-08-22 — Connected the real Gmail account `stiven.ddh@gmail.com` end to
  end against the local devcontainer stack (moto + `just dev`), no public
  tunnel: OAuth works because `INGESTION_GMAIL_REDIRECT_URI` is
  `http://localhost:8000/...`; push notifications don't reach localhost, so
  `/identity/mailboxes/refresh` (manual pull) stands in for the webhook.
  Bancolombia (`an.notificacionesbancolombia.com`) is the approved sender.
  Corrected a real Client ID/Secret that briefly landed in the git-tracked
  `.env.production.example` — moved to the gitignored `.env`, template
  restored, never committed.
- 2026-08-22 — Added [docs/mailbox-connection.md](docs/mailbox-connection.md):
  a backend-only API guide for how a client walks a user through connecting a
  mailbox (Gmail OAuth flow and the simulated-provider flow), the two-piece
  connection+sender-filter model, connection statuses, refresh/disconnect, and
  the error table. Cross-linked from `docs/overview.md` and `docs/running.md`.

## Next steps

- [ ] Nothing queued. Possible next: expose backfill as part of onboarding
      once a frontend exists; Financial context is still unimplemented
      (see `docs/overview.md`).
- [ ] The rest of the git staging area (identity/ingestion changes from this
      session) is left staged but uncommitted, as usual — commit only when
      the user asks.

## Open questions / blockers

- none

## Session log

### 2026-08-22

- Set up `PROGRESS.md` + `CLAUDE.md` workflow rule for session continuity.
- Wrote `docs/mailbox-connection.md` (backend guide to mailbox authorization)
  by reading the actual OAuth/connection code.
- Walked the user through connecting their real Gmail (Google Cloud OAuth
  client + Pub/Sub topic, local `.env`, register/authorize/callback/refresh
  via curl and Bruno). Caught and fixed a Client ID/Secret briefly pasted
  into the tracked `.env.production.example` before it was committed.
- Implemented the current-month backfill feature end to end (domain-free;
  ports + application use cases + Gmail/simulated adapters + identity
  endpoint + unit/integration tests + docs). `just prepare` green.
- Added the Postman/Bruno collection (`docs/postman/`) covering every
  current endpoint, plus the `CLAUDE.md` rule to keep it updated going
  forward. Untracked `.vscode/settings.json` per user request.
