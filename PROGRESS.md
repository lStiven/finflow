# Progress

Living log of where the work stands. This exists so a fresh session (after
`/clear` or a new session) can pick up without replaying old conversation —
read this first, and update it after finishing a task or a meaningful chunk
of work, instead of relying on conversation history to carry it forward.

`Last completed` holds only the single most recent item — keep it short,
that's the point. Full history lives in `Session log`, one line per entry.

## Current focus

Nothing in-flight — real AWS prod account is provisioned and verified.

## Last completed

- 2026-08-22 — Diagnosed and fixed the real-AWS (prod) infra: provisioning
  had only run once, before later tables/queues were added. Re-ran
  `just provision-prod` (now with progress logs — see
  `shared/infrastructure/aws/provisioning.py`), filled the missing Merchant
  env vars in `.env.production`. `just prepare` green.

## Next steps

- [ ] Nothing queued. Possible next: expose backfill as part of onboarding
      once a frontend exists; Financial context is still unimplemented
      (see `docs/overview.md`).

## Open questions / blockers

- none

## Session log

### 2026-08-22

- Set up `PROGRESS.md` + `CLAUDE.md` workflow rule for session continuity.
- Wrote `docs/mailbox-connection.md`, a backend-only guide to the mailbox
  OAuth/simulated connection flow.
- Connected the real Gmail account `stiven.ddh@gmail.com` end to end locally
  (no public tunnel — OAuth via `localhost` redirect, `/mailboxes/refresh`
  stands in for the push webhook). Caught and fixed a Client ID/Secret that
  briefly landed in the tracked `.env.production.example`.
- Built the current-month mailbox backfill feature end to end (ports, Gmail +
  simulated adapters, `POST /identity/mailboxes/backfill`, tests, docs).
- Added the Postman/Bruno collection (`docs/postman/`) covering every
  endpoint, plus the `CLAUDE.md` rule to keep it updated on every future
  endpoint change. Untracked `.vscode/settings.json`.
- Diagnosed real-AWS prod infra was stale (only one table existed); added
  progress logging to `provisioning.py`, re-provisioned, fixed
  `.env.production`'s missing Merchant section.
