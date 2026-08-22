# Progress

Living log of where the work stands. This exists so a fresh session (after
`/clear` or a new session) can pick up without replaying old conversation —
read this first, and update it after finishing a task or a meaningful chunk
of work, instead of relying on conversation history to carry it forward.

`Last completed` holds only the single most recent item — keep it short,
that's the point. Full history lives in `Session log`, one line per entry.

## Current focus

Nothing in-flight — email-forwarding migration + its review pass are done,
`just prepare` green (383 tests).

## Last completed

- 2026-08-22 — Ran a code-review + security-review pass over the
  email-forwarding rewrite (see Session log), fixed the real findings
  (IMAP PEEK data-loss bug, batched `ack`, per-message error isolation,
  stale double-read on `PATCH /identity/inbox`, a few small DRY cleanups).
  `just prepare` green.

## Next steps

- [ ] Build a logs/observability module: centralize the ~20 scattered
      `logging.getLogger(__name__)` + `extra={...}` call sites into one
      shared setup in `shared/infrastructure/observability/`, shipping
      structured logs to CloudWatch, with CloudWatch Alarms/metric filters as
      the follow-on for alerting. **Blocked on where the app runs** — that
      decides the delivery mechanism. Don't start until that's decided.
- [ ] Configure a real dedicated Gmail ingest account (App Password) once
      ready to test actual forwarding end to end — today only a placeholder
      address is set locally, see `docs/email-forwarding.md`.
- [ ] The real AWS account still has the now-orphaned `mailbox_connections`
      and `simulated_mailbox` tables from before this migration —
      provisioning only ever creates, never deletes. Ask the user before
      removing them from the real account.

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
- Replaced Gmail-OAuth mailbox-connection with email forwarding, per explicit
  product decision to revert to the original plan: every user now forwards
  bank mail to `finflowingest+<user_id>@gmail.com`, one shared Gmail account
  read over IMAP + App Password — no OAuth, no Google Cloud project, no push
  subscriptions to renew. Deleted wholesale: Gmail OAuth (`oauth_router.py`,
  `oauth_handlers.py`, the whole `mailbox/gmail/` dir), `MailboxConnection` +
  its table, the simulated-provider test double, subscription renewal/sweep,
  and the current-month backfill (no OAuth mailbox access to backfill from
  anymore). Added `ingestion/domain/forwarding.py` (deterministic address
  derivation), `PollIngestMailboxUseCase` + `ImapIngestMailboxReader`,
  `just ingest-worker`. Identity's mailbox endpoints collapsed to
  `GET/PATCH /identity/inbox`; every registration now auto-assigns one inbox
  (was optional/list-shaped, now unconditional and singular). Rewrote
  `docs/overview.md`, `docs/running.md`; replaced `docs/mailbox-connection.md`
  with `docs/email-forwarding.md`; updated `CLAUDE.md`, the Postman
  collection, all four `.env*` files (also removed a leaked Gmail Client
  Secret sitting in the real `.env` files from the earlier OAuth setup).
  Live-smoke-tested register → assigned address → approve senders →
  `just inspect` end to end against local moto.
- Ran a code-review pass (6 parallel finder subagents) + a security-review
  pass over the full diff (`git diff HEAD`, 69 files — had to fix the
  security-review skill's own diff command first, it defaulted to
  `origin/HEAD...` and missed everything uncommitted). Confirmed and fixed:
  IMAP `FETCH (RFC822)` marks a message `\Seen` on read, before `ack()` ever
  runs — defeated the "ack only after durable write" design and could
  silently drop bank alerts on a mid-batch crash; switched to
  `BODY.PEEK[]`. Added per-message exception isolation in the poll loop
  (one bad message no longer abandons the rest of the batch) and batched
  `ack()` into one IMAP round trip per poll instead of one per message.
  Fixed a login-before-`try` socket leak, swapped hand-rolled address
  parsing for `email.utils.getaddresses`, and removed a stale/possibly-inconsistent
  DynamoDB re-read on `PATCH /identity/inbox` by threading the write's own
  result back instead. Small DRY fixes: `RegisterPayload` now inherits
  `InboxSendersPayload`, a shared `_inbox_response()`/`_to_registered()`
  helper replaced duplicated response-building. Declined (validated,
  not bugs): the "checks only address not app_password" and "only fails at
  first request not startup" findings — both false positives, the API
  already eager-validates at boot via `lifespan()`. Left as documented,
  accepted residual risk: forwarded mail has no SPF/DKIM signal, so a sender
  who already knows a user's forwarding address and an approved sender
  address could in theory forge a fake bank alert — already called out in
  `docs/email-forwarding.md`.
