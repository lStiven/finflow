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

Financial's write side runs end to end. A forwarded bank alert reaches
`just financial-worker` over the bus, opens the account it needs, and lands as
a ledger row plus a balance in one atomic DynamoDB write — verified against
the local emulator, not only in tests. Redeliveries are refused by the
conditional write, and a movement no account answers for is kept unassigned.

What is missing is the read side (no endpoints, no net-worth query) and the
three correctness gaps under **Next steps** — the first of which now produces
wrong money rather than merely being absent. `just prepare` green (520
tests).

## Last completed

- 2026-08-24 — Financial's write side: `RecordMovementUseCase`, both
  persistence ports, the DynamoDB adapter, `SQSFinancialWorker`,
  `just financial-worker`, and provisioning for the `financial` table and
  queue.

## Next steps

- [ ] **Validate the authorization filter against real alerts.** An
      authorization and its posting are two different emails with different
      bodies, so the rule lives at parse time: an authorization never becomes
      a `TransactionExtracted` at all. The deterministic templates only match
      completed facts (`Compraste`, `Pagaste`, …) and the LLM is now told to
      refuse anything approved/held/in process. What is missing is
      confirmation against real authorization emails from each bank — the
      refusal wording was written without one in hand.
- [ ] **An auto-opened account's currency is fixed by its first alert, with
      no repair path.** A card whose first sighting happens to be a USD
      purchase becomes a USD account; every later COP alert on it then fails
      `Account.apply` and is filed unassigned forever. Needs either a way to
      correct an account's currency or a rethink of what currency an
      auto-opened account has.
- [ ] **One real account still becomes two.** The design says one account
      answers to many fingerprints — a checking account emails as a debit
      card and as an account number — but `_resolve_account` only ever
      matches or opens, so the second instrument opens a second account and
      splits one balance. `AccountRepository.save` exists to persist the
      linking; what is missing is the user-facing action that decides two
      fingerprints are one account. Never inferred: that is a guess about
      somebody's money.
- [ ] **Reassignment has no path.** `Transaction.assign_to` refuses any second
      account, and nothing reverses an amount off the balance that holds it.
      A movement auto-assigned to the wrong account is stuck there — which
      matters because reading a debit card as a savings account is a
      deliberate guess the user may need to correct.
- [ ] **The read side.** Account list, net worth, the unassigned queue, and
      the Postman collection that goes with them.
- [ ] **The whole SQS worker is duplicated**, not just the envelope.
      `IntegrationEventEnvelope`, the source/detail-type constants, `_Outcome`,
      `PollResult`, `poll_once`, `_delete`, and both CLI runners' `_Stopper`
      and `main()` exist twice, in merchant and in Financial. It is transport,
      not any context's rules, so a generic worker parameterised by (source,
      detail-type, detail model, handler) belongs in
      `shared/infrastructure/aws/`. The duplication is already what let
      Financial's copy ship without merchant's version guard, and it has since
      let the two diverge on whether an unreadable payload is discarded.
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
- **An authorization is filtered at parse time, not reconciled later**
  (2026-08-24, corrected). An authorization and its posting are two separate
  emails whose bodies differ, so the cheap and certain place to drop the
  authorization is the parser: it never becomes a `TransactionExtracted`, and
  Financial never sees it. Rejected: matching the two inside Financial — it
  would need a heuristic over amount and time windows to undo something the
  upstream text already states plainly. The deterministic templates hold by
  construction (they only match completed facts) and a test pins that; the
  LLM is told explicitly to refuse anything that has not settled.
- **Accepted risk: the key resolves to the minute, so two identical charges
  inside one minute collapse into one movement.** Both extraction paths stop
  at `HH:MM` (the template parser's regex and the LLM prompt), so a real
  second charge and a duplicate announcement of the first carry byte-identical
  data — nothing in the payload can separate them, and idempotency under
  at-least-once delivery is the requirement that has to hold. Rejected: a
  time bucket (folds *more* real charges together), and `message_id` as a
  tiebreaker (defeats dedup entirely). Making that lost charge visible is the
  ledger's job, and **it is not built**: the row records nothing about which
  announcement produced it, and `RecordMovementCommand` deliberately drops the
  event id at the boundary. Storing the set of `event_id`s behind a row would
  separate an SQS redelivery (same id) from a second announcement (different
  id), which is the signal worth surfacing.
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

### Financial (write side, 2026-08-24)

- **The balance moves by DynamoDB's `ADD`, never by writing a number back.**
  A read-then-write loses one of two movements landing in the same instant,
  and a balance that quietly drops a row is what the ledger exists to prevent.
  `ADD` *is* a running total, applied by the database. The ledger row and the
  balance change go out as one `TransactWriteItems`, so a balance can never
  move without a row behind it. The domain computes how far the balance moves
  and the adapter applies that delta, which is why `TransactionLedger.record`
  takes one.
- **A cancelled transaction is not the same as a refused condition.**
  DynamoDB cancels for throttles and write conflicts too, and all of them look
  identical from the outside. Reading them all as "this movement is already
  recorded" is how a real expense disappears: nothing is written, the caller
  reports a duplicate, and the worker deletes the message. `record` and `add`
  inspect `CancellationReasons` and only treat *their own* condition failing
  as the answer they expect; everything else raises so the message comes back.
- **An unreadable direction or currency leaves the message on the queue; an
  unparseable payload is deleted.** `Currency` knows two members, so an alert
  in a third is a real movement the next deploy would read — deleting it to
  save a redelivery destroys it, and the dead-letter queue is where something
  genuinely unreadable belongs. A payload that fails its schema, by contrast,
  never becomes parseable and is not a movement anybody can recover.
- **A duplicate result hands back no account.** The aggregate was applied in
  memory before the write was refused, so its balance is one movement ahead of
  what is stored; returning it would let a caller report a number that
  double-counts the redelivery. Its pending events are dropped rather than
  pulled — `event_id` is fresh on every attempt, so republishing them reads as
  new work to any subscriber deduping on it.

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
