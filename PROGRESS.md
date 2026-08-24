# Progress

Living log of where the work stands. This exists so a fresh session (after
`/clear` or a new session) can pick up without replaying old conversation —
read this first, and update it after finishing a task or a meaningful chunk
of work, instead of relying on conversation history to carry it forward.

`Last completed` holds only the single most recent item — keep it short,
that's the point.

`Decisions` is the durable half of this file: **why** things are the way they
are, what was rejected and what risk was knowingly accepted. Git already
records *what* changed and when, so this file must not narrate diffs —
one compact entry per decision that a fresh session could not recover by
reading the code.

## Current focus

Building Financial. Its domain is complete for v1 — `Account`,
`Transaction`, the movement fingerprint — and the inbound boundary reads
`finflow.ingestion` off the bus into `RecordMovementCommand`. What is missing
is everything that makes it durable: no use case, no repository, no worker, no
endpoints. An integration test proves the chain as far as the boundary and
stands in for the use case by hand. `just prepare` green (499 tests).

## Last completed

- 2026-08-24 — Financial's `Transaction` aggregate and its inbound
  boundary (`RecordMovementCommand`, `infrastructure/messaging/inbound.py`),
  with an end-to-end test from ingestion's translator to a balance.

## Next steps

- [ ] **Next: Financial's write side — use case, port, DynamoDB adapter,
      worker.** The domain and the inbound boundary are done; nothing
      persists yet, so every balance dies with the process. In order:
      1. The use case behind `RecordMovementCommand`: find the account by
         `(user_id, account_fingerprint)`, open one on first sighting, assign
         and apply. The lookup must be user-scoped — `AccountFingerprint`
         carries no `user_id`, unlike `MovementFingerprint`, so two people at
         one bank with the same last four resolve to one account otherwise.
      2. Port → DynamoDB adapter: conditional write on `MovementId` (which is
         the movement fingerprint), with the ledger row and the balance
         update in one atomic write, per the Decisions below.
      3. The SQS worker. It must map `UnsupportedPayloadVersionError` to
         "leave on the queue" and `ValidationError` to "discard", the way
         merchant's does, and drop an aggregate's pending events when the
         conditional write refuses the row — `event_id` is fresh per attempt,
         so republishing them would look like new work downstream.
      Endpoints and the Postman collection come with the read side (account
      list, net worth), not with this step.
- [ ] **Reassignment has no path.** `Transaction.assign_to` refuses any second
      account, and nothing reverses an amount off the balance that holds it.
      A movement auto-assigned to the wrong account is stuck there — which
      matters because reading a debit card as a savings account is a
      deliberate guess the user may need to correct.
- [ ] **Settlement (authorization → posting) is unbuilt.** The fingerprint
      deliberately cannot catch it: the two differ in time and often in
      amount. It needs both records in hand, so it waits for the ledger.
- [ ] **`IntegrationEventEnvelope` is duplicated** in merchant's worker and
      Financial's inbound module, along with the source/detail-type
      constants. It describes EventBridge's transport, not any context's
      rules, so it belongs beside `EventBridgeEventPublisher` in
      `shared/infrastructure/aws/`. The duplication is already what let
      Financial's copy ship without merchant's version guard.
- [ ] **Decide how a bank-reported balance is treated** (TODO left in
      `financial/domain/entities.py`). Some alerts state the resulting
      balance; unknown which local banks do. Either it reconciles the running
      total — which needs a way to tell a stale alert from a current one,
      since they arrive out of order — or it is kept for reference only.
- [ ] CloudWatch log shipping — deferred, see Decisions.

## Open questions / blockers

- none

## Decisions

### Intake

- **Email forwarding, not Gmail OAuth** (2026-08-22, reversal to the original
  plan). Every user forwards bank mail to `finflowingest+<user_id>@gmail.com`,
  one shared account read over IMAP + App Password. Killed the entire OAuth
  path: no Google Cloud project, no consent screen, no push subscriptions to
  renew, no per-user tokens to refresh. Cost: the current-month backfill
  feature was deleted with it — there is no mailbox access to backfill from
  anymore.
- **Accepted risk: forwarded mail has no usable SPF/DKIM signal.** Someone who
  already knew a user's forwarding address *and* their bank's exact sender
  address could forge an alert. Documented in `docs/email-forwarding.md`; the
  address derives from a full UUID, which is the only thing making it hard to
  guess. Not solved.
- **Gmail's manual "Forward" button rewrites `From`** to the forwarder's own
  address, so such mail is correctly rejected as an unapproved sender. Only
  Gmail's *automatic* forwarding (filter-based) preserves the bank's `From`.
  Hit this live during testing — it is expected behaviour, not a bug.

### Parsing

- **`bank` is part of `ExtractedTransaction`** and is normalized (strip +
  lowercase) in the value object itself, not trusted from each producer. A
  template parser knows its own bank; the LLM fallback has to read it and
  answered with different casing. Financial keys account-matching on
  (bank, instrument.kind, instrument.last_four) — two banks can reuse the same
  last four digits, and a casing mismatch would split one real account in two.
  It is therefore the one string field the LLM schema requires rather than
  defaults to `""`, and an answer that still names no bank is refused whole:
  a placeholder institution would merge two banks' cards that share their
  last four into one account holding somebody's money twice. The email keeps
  its body as `pending_fallback`, so a refusal costs a re-read, not the data.
- **`PENDING_FALLBACK` carries a `NotificationDeferredReason`**
  (`NO_FALLBACK_CONFIGURED` / `FALLBACK_FOUND_NOTHING`). Without it, an email
  the LLM had already read and declined looked identical to one never
  attempted — the state name reads as "still queued" when it is final.

### Financial (v1 requirements, agreed 2026-08-23)

- `Account` has a kind (savings, checking, credit card, loan, mortgage, …) and
  an ASSET/LIABILITY category. Net worth = Σ assets − Σ liabilities.
- **`Balance` keeps `Money` unsigned and carries the sign beside it**, so
  direction stays outside the quantity as everywhere else. A credit card
  holding 1.2M means "you owe 1.2M"; its `category` is what makes it
  subtract. The sign exists because an account discovered from an alert
  starts at zero with its real opening balance unknown, so an asset's running
  total legitimately goes below zero — a known-incomplete history, where
  refusing to represent it would mean inventing a starting number.
- **Accounts auto-create** on first sighting of a new (bank, instrument) pair,
  with a generic renameable name — same spirit as Merchant's auto-grouping.
- **Manual account creation also required**, for mortgages/loans that never
  email per-movement.
- A transaction with no matching account, or no instrument at all, is kept
  **unassigned** rather than guessed at — same philosophy as
  `pending_fallback`.
- **The ledger is the authority; the balance is a running total.**
  `Account.apply` never asks whether it has seen a movement before — that
  answer cannot be reached from memory when the queue is at-least-once and
  processes restart. Novelty is the ledger's job (conditional write on the
  movement fingerprint), and the ledger row and the balance update must land
  in one atomic write so a balance can never move without a row behind it.
  `Account.rebuild` replays the ledger, so drift is repairable instead of
  permanent. Rejected: keeping applied movement ids inside the aggregate (it
  grows without bound) and a "last movement applied" guard (it only catches
  an immediate redelivery, while reading as if it caught everything).
- **An account is matched by (bank, instrument kind, last four), and an
  alert with no last four stays unassigned.** Matching on bank + kind alone
  would merge every savings account a user holds at one bank into a single
  wrong balance. The digits are normalized to the trailing four: the LLM
  fallback can return more of the number than a template parser does.
- **A movement's identity is derived from its content, never from the
  email.** `message_id` would fail the case it exists for: a bank can announce
  one purchase in two messages, and SQS delivers at least once. The key is
  user + bank + instrument + direction + amount + time + counterparty, hashed.
  `kind` is left out — it is a classification, and a re-parse that
  reclassifies a movement must not turn it into a second one.
- **Accepted risk: the key resolves to the minute, so two identical charges
  inside one minute collapse into one movement.** Both extraction paths stop
  at `HH:MM` (the template parser's regex and the LLM prompt), so a real
  second charge and a duplicate announcement of the first carry byte-identical
  data — nothing in the payload can separate them, and idempotency under
  at-least-once delivery is the requirement that has to hold. Rejected: a
  time bucket (folds *more* real charges together), and `message_id` as a
  tiebreaker (defeats dedup entirely). Making that lost charge visible is the
  ledger's job — it can record what announced each row — not the key's.
- **The fingerprint is hashed for obfuscation, not confidentiality.** It
  reaches ledger keys and `AccountBalanceChanged`, so a readable key would put
  amounts, counterparties and card digits in plaintext wherever an id is
  logged. The inputs are enumerable, so anyone already holding the logs could
  confirm a guess. Rejected an HMAC: it would put a secret inside a domain
  value object, and losing or rotating that key would re-apply every movement
  ever stored — doubling balances — to buy privacy the deployment's threat
  model does not need.
- **Ingestion's vocabulary is read at Financial's boundary, and the
  reading may be imprecise within a category but never across it.**
  `AccountKind.from_instrument` maps a debit card to a savings account, which
  may really be a checking one — both are assets, so being wrong costs a
  rename. Reading a debit card as a credit card would turn money held into
  money owed and invert net worth, so credit and debit are taken from the
  alert and never inferred. An instrument it does not recognise concludes
  nothing and leaves the movement unassigned. Direction and currency are the
  opposite case: no safe default exists, so an unreadable one refuses the
  whole payload.
- **One account holds many fingerprints.** A single checking account emails
  as a debit card for purchases and as an account number for transfers.
- **Multi-user / household shared view is explicitly out of v1.** The intended
  shape (each `Account` stays owned by one user; a mutually-accepted link in
  Identity; the combined view is a query, not new data) is recorded here so
  the door stays open, but nothing is designed or built.

### Operations

- **CloudWatch log shipping deferred** (2026-08-23). Options weighed:
  app-level (`watchtower`) vs. platform-native capture (awslogs driver /
  CloudWatch Agent / Lambda). The latter depends on where the app runs, which
  is undecided. For now: plain `logging.getLogger(__name__)` + `extra={...}`
  by hand where useful.
- **`configure_logging()` exists because `logging.basicConfig`'s default
  formatter silently drops every `extra={...}` field.** All ~20 existing call
  sites were affected and invisible. Wired into the API's `lifespan` and all
  three workers.
- Orphaned `mailbox_connections` / `simulated_mailbox` tables removed from the
  real AWS account (2026-08-23). Provisioning only ever creates, never
  deletes, so leftovers from a removed feature need deleting by hand.

### Review findings worth remembering

- **IMAP `FETCH (RFC822)` marks a message `\Seen` on read**, before `ack()`
  runs — it defeated the "ack only after a durable write" design and could
  drop bank alerts on a mid-batch crash. Must stay `BODY.PEEK[]`.
- **The `TransactionExtracted` integration payload must never be logged.** It
  is a line of somebody's spending history — amount, counterparty, bank and
  card digits in one record — and an INFO log of it was shipping exactly that
  to wherever logs land, ungated by environment. `LoggingEventPublisher`
  records that an event happened, by type and id; that is what an audit trail
  needs.
- Rejected as false positives in that same review: "only validates address,
  not app_password" and "fails at first request, not startup" — the API
  already eager-validates at boot via `lifespan()`.
