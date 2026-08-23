# Progress

Living log of where the work stands. This exists so a fresh session (after
`/clear` or a new session) can pick up without replaying old conversation —
read this first, and update it after finishing a task or a meaningful chunk
of work, instead of relying on conversation history to carry it forward.

`Last completed` holds only the single most recent item — keep it short,
that's the point. Full history lives in `Session log`, one line per entry.

## Current focus

Nothing in-flight — real end-to-end mailbox smoke test done, `just prepare`
green (383 tests). Financial is the natural next context to build (see Next
steps): it is still 100% empty scaffolding, so the pipeline currently stops
at Merchant — no account balance or net worth is ever produced yet.

## Last completed

- 2026-08-23 — Real IMAP smoke test against the live `finflowingest@gmail.com`
  account: a manually-forwarded email correctly got `ignored` (sender header
  rewritten to the personal address by Gmail's manual "Forward", not the
  bank's — expected, matches `docs/email-forwarding.md`); a properly
  auto-forwarded/approved-sender email went all the way through and created
  merchants + aliases. Added `logging.basicConfig` to the API's `lifespan`
  (main.py) so request-triggered INFO logs are no longer silently dropped,
  matching the three workers. `just prepare` green.

## Next steps

- [ ] **Build the Financial bounded context** (Account, Transaction,
      balances, debt, net worth) — currently every file under
      `contexts/financial/` is an empty stub, no tests. This is the missing
      link that turns an extracted+classified transaction into an actual
      balance/net-worth change, i.e. the app's core stated goal. Follow the
      usual order: domain -> unit tests -> application -> port ->
      infrastructure adapter -> endpoint/worker -> integration test. Needs a
      design pass first on how it sources data: subscribe to Ingestion's
      `TransactionExtracted` (has amount/account/date) and/or Merchant's
      events (has canonical merchant_id/category).
- [ ] CloudWatch log shipping: deferred by explicit product decision
      (2026-08-23) — for now, add plain `logging.getLogger(__name__)` +
      `extra={...}` calls by hand wherever useful (existing pattern, see any
      `sqs_worker.py`), no dedicated shipping module. Revisit once hosting
      (ECS/EC2/Lambda) is decided — see the shelved plan in git history if
      picked back up.

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

### 2026-08-23

- Real mailbox smoke test with the user, live against `finflowingest@gmail.com`
  (App Password now set). First attempt: user manually clicked "Forward" in
  Gmail, which rewrites `From` to the forwarder's own address — correctly
  landed as `ignored` (sender not approved), `raw_content` empty by design
  (`ignore()`/`complete()` both discard the body on purpose, confirmed not a
  bug). Second attempt with a proper approved sender went end to end:
  notification -> parse -> `TransactionExtracted` -> merchant created with its
  alias. Explained the existing logging story (stdlib `logging` +
  `extra={}`, already present in ~20 call sites, but only workers called
  `logging.basicConfig`); added the same call to the API's `lifespan` so
  request-triggered logs stop being silently dropped. Discussed shipping to
  CloudWatch (watchtower vs. platform-native capture) — user's decision:
  defer the dedicated module, add manual log lines where useful instead.
  Updated this file's Next steps accordingly and flagged Financial (still an
  empty scaffold) as the natural next context.
- Added test-visibility logging: fixed `logging.basicConfig`'s default
  formatter silently dropping every `extra={...}` field (all ~20 existing
  call sites were affected, invisible until now) via a shared
  `configure_logging()` in `shared/infrastructure/observability/`, wired
  into the API's `lifespan` and all three workers. Added a
  `transaction_extracted_payload` log in ingestion's integration-event
  translator so the exact payload Financial will eventually consume is
  visible now, ahead of that context existing. `just prepare` green.
- Deleted the orphaned `mailbox_connections` and `simulated_mailbox` tables
  from the real AWS account (confirmed empty first) — the OAuth->forwarding
  cleanup item from 2026-08-22 is done.
- Split what `PENDING_FALLBACK` could mean: added `NotificationDeferredReason`
  (`NO_FALLBACK_CONFIGURED` / `FALLBACK_FOUND_NOTHING`), threaded through
  `defer_to_fallback(reason=...)`, `TransactionExtractionDeferred`, persisted
  on `BankNotification` (optional column, old items without it still load),
  and surfaced in `just inspect`. Fixes the exact confusion hit live: a
  `pending_fallback` notification whose fallback had actually already run
  and declined the email looked identical to one that was never attempted.
  4 new tests (domain, application x2, persistence round-trip + legacy-item
  compat). `just prepare` green, 387 tests.
- Prerequisite for Financial: added `bank: str` to `ExtractedTransaction`
  (required, non-empty) — deterministic parsers already knew their own bank
  (`parser.bank`), threaded through; LLM fallback schema gained a `bank`
  field + prompt instruction. Flows through `TransactionExtracted`'s
  integration-event payload. Needed because account-matching in Financial
  must key on (bank, instrument.kind, instrument.last_four) — two banks can
  coincidentally reuse the same last four digits. `just prepare` green, 388
  tests.
- Gathered Financial v1 requirements with the user (see chat): `Account`
  (kind + ASSET/LIABILITY category, balance as unsigned `Money` matching the
  existing pattern), auto-created on first sighting of a new
  (bank, instrument) pair, plus manual account creation (mortgages/loans
  that don't email per-movement). A transaction with no matching account (or
  no instrument at all) is kept "unassigned" rather than guessed, same
  pending-fallback philosophy. Multi-user/household shared view explicitly
  deferred out of v1 — not designed yet, not blocking.
