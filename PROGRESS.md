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

Financial is complete for v1, backend-side. Accounts are **declared by
their owner**, never discovered: Finflow works with none at all — every alert
is recorded and stays unassigned, which is a complete answer for somebody
watching only what comes in and goes out. Declaring an account starts the
association and is retroactive. Money that never emails can be entered by
hand, and anything recorded can be corrected.

Twenty-nine endpoints across the four contexts, the three SQS workers, the
atomic ledger write, secrets from SSM, point-in-time recovery on every table,
CORS, and `just seed` to refill the emulator. Movements now carry the
canonical merchant behind the bank's text, and `GET /financial/summary`
answers what a period adds up to. All verified against the local emulator, not
only in tests. `just prepare` green (694 tests).

DynamoDB runs on-demand: at this deployment's volume the bill is on the order
of a cent a month, and the free tier's 25 provisioned units were shaping the
schema for no real saving.

Deployment is declared: five Lambda functions from one container image, in
`infra/template.yaml`, with the API reachable over HTTPS through a Function
URL and no domain to buy. Never actually built or deployed, so the first
`just deploy-dev` is the real test. The DevContainer can now run it: it
borrows the host's Docker daemon and installs the SAM CLI (2026-08-28).

The frontend has started. `frontend/` holds a Vite + React + TypeScript SPA
whose types are generated from the API's own OpenAPI document, so a router
change in Python fails the TypeScript build rather than a screen in a browser.
Session, money and date handling, and the first two screens (login/register
and the accounts dashboard) run against the local emulator.

What is missing for production is observability, a cap on LLM spending and the
rest of the frontend — see **Next steps**.

## Last completed

- 2026-08-29 — the docs cover both halves. `running.md` was backend-only and
  predated the frontend; it now has an **El frontend** section (the two Vite
  modes, the single `VITE_API_BASE_URL`, what breaks silently, and that
  publishing it is unsolved), reached from `frontend/README.md` and
  `frontend-integration.md`. Same pass fixed `just web` not reaching the
  browser: the port chain was already correct — proved with
  `host.docker.internal:5173` → 200 from inside the container — and the
  browser was on Vite's Docker-internal `172.17.x.x` URL.

## Next steps

- [ ] **Next: what production actually needs.** In order of what hurts
      soonest:
      1. **Observability.** Closed, pending one manual step. Logs go to
         `/finflow/<stack>/<name>`, and three CloudWatch alarms — one per DLQ
         — publish to an SNS topic on any message at all. What remains is not
         code: `AlertEmail` must be set in `infra/samconfig.toml` per
         environment, and the SNS subscription confirmed from the email AWS
         sends, or the alarms page nobody.
      2. **CI.** There is a Dockerfile, a SAM template and now a post-deploy
         gate (`just smoke`), but nothing builds or deploys automatically.
         `sam build`, `sam deploy` and the `finflow-dev` stack have all now
         run for real from the DevContainer, so nothing here is untested any
         more — it is simply manual. The pipeline worth building:
         build the image **once**,
         deploy it to `finflow-dev`, `just smoke <dev-url> --with-pipeline`,
         then promote the same image to production behind a manual approval
         and `just smoke-prod <url>`. Building separately per environment
         would mean what was tested is not what shipped.
      3. **A spending cap on the LLM** (see the standalone item below). The
         cheapest of the three and the only one that costs money while it is
         missing.
      4. **The rest of the frontend.** The foundation is in (`just web`);
         what is left is screens, not plumbing: movements with their filters,
         the connect-your-bank screen (forwarding address, approved senders,
         what actually arrived), merchant review, the spending summary, and
         manual entry. `docs/frontend-integration.md` is still the contract
         each of them has to honour. Nothing is deployed: no hosting is
         provisioned and `API_CORS_ORIGINS` names only localhost.
- [ ] **Decide whether merchants are per-user or shared.** They are per-user
      today — partition key is the owner, and `just verify` shows Ana and
      Bruno holding separate `Éxito` records that renaming one does not touch.
      Shared merchants would match the intuition that a business is the same
      business for everybody, but renames and categories are personal
      decisions, and `times_seen` would leak one person's habits into
      another's list. The middle option nobody has designed yet: a shared
      catalogue of canonical merchants with per-user overrides on top.
- [ ] **Set a billing alarm on the AWS account.** The per-table request
      ceiling bounds a runaway's *rate*, not a month's spend, and it is the
      only guard there is. Nothing in this repo can create it; it is a
      console/CLI step on the account itself.
- [ ] **A manual entry can be attributed but never creates a merchant.** The
      join is a fingerprint lookup, so a hand-entered `TIENDAS ARA` does find
      the merchant that owns that spelling — but a name that never arrived by
      email belongs to nobody and stays in the `null` bucket forever. Closing
      it means Financial publishing its own integration event for manual
      entries and merchant subscribing to a second source, which is a session
      of its own.
- [ ] **`GET /ingestion/notifications` reads the whole partition per page.**
      `limit`/`offset` shrink the response, not the read: the counts beside
      the list genuinely need the full set, but the page window does not, and
      the index runs at 3 RCU.
- [ ] **Only Bancolombia has a parser**, with three sender domains mapped.
      Every other bank falls through to the LLM, which costs money per email
      and refuses when unsure. More banks get added over time; this is
      deliberate, not a gap.
- [ ] **No spending cap on the LLM.** Every unrecognised email calls Gemini.
- [ ] **Validate the authorization filter against real alerts.** An
      authorization and its posting are two different emails with different
      bodies, so the rule lives at parse time: an authorization never becomes
      a `TransactionExtracted` at all. The deterministic templates only match
      completed facts (`Compraste`, `Pagaste`, …) and the LLM is now told to
      refuse anything approved/held/in process. What is missing is
      confirmation against real authorization emails from each bank — the
      refusal wording was written without one in hand.
- [ ] **The whole SQS worker is duplicated**, not just the envelope. *(The
      behavioural gap between the two copies is closed as of 2026-08-29 and
      both are unit-tested, so what is left is the extraction itself. Two
      things found while closing it and worth carrying into that session:
      `ingestion`'s parse worker still has no guard around its use case at
      all, and `shared/infrastructure/messaging/lambda_batch.py::drain`
      already states the rule the three copies each re-implement — it is the
      shape the generic worker should take.)*
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

- **`finflow-dev` and `finflow-production` currently resolve to the same AWS
  account and the same IAM user**, so the account separation recorded under
  Environments is intended but not yet real. Until a second account exists,
  the `dev-` prefix is again the only thing keeping the two apart, and the
  comments claiming otherwise in `.env.development.example`,
  `infra/samconfig.toml` and `docs/running.md` overstate the isolation.

## Decisions

### Intake

- **Ingestion has one read surface, and it carries no email bodies**
  (2026-08-24). `GET /ingestion/notifications` exists so a client can tell
  three failures apart that otherwise look identical — nothing arrived, it
  arrived from a sender nobody approved, it arrived and nothing could be read
  out of it. It reads a `by_user` index whose projection lists the attributes
  a screen shows and excludes `raw_content`: an index is a full copy of what
  it projects, and duplicating every untrusted message to answer a question
  that never involves one is not a trade worth making. The counts beside the
  list cover every status the user has regardless of the filter, so the
  summary does not move when somebody narrows the list. The cost of the index
  is what eventually moved the whole account off provisioned capacity — see
  **Billing** below.

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

### Financial (accounts are the user's, 2026-08-24)

- **Accounts are declared, never discovered.** Auto-opening was removed. The
  product has two uses and this serves both: watching what comes in and goes
  out needs no accounts at all — every movement is recorded and stays
  unassigned, which is complete rather than degraded — and watching a card's
  running state without opening the bank's app needs an account its owner
  declared, with the kind and currency they chose. Guessing a kind from an
  instrument would have meant guessing asset against liability, which inverts
  net worth. `AccountStatus`, `needs_review` and the generated
  `Bancolombia ••7653` names went with it: nothing is discovered any more, so
  there is nothing to review.
- **Declaring an account is retroactive.** The alerts that arrived under its
  card before it existed are still in the ledger, unassigned, and they belong
  to it: they are adopted and the balance replays from them. Adoption cannot
  be atomic across every row it touches, so it assigns the rows first and
  recomputes the total last. A crash in between leaves a stale total, which
  `rebuild` repairs — the ledger is the authority and the balance is derived
  from it, which is exactly what makes that recoverable instead of lost.
- **Restating a balance takes today's figure, not the opening one**
  (2026-08-27). Declaring an account used to be a dead end for anybody who
  could not remember what it held before the alerts already on record —
  `opening_balance` was write-once at declaration, and getting it wrong meant
  closing the account and redeclaring it. The endpoint deliberately asks for
  the number the bank shows *now*, because that is the only half a person can
  look up; the opening balance is solved backwards from the ledger. Rejected:
  exposing `opening_balance` for editing, which is the same arithmetic with
  the unknowable half facing the user. No movement is touched, so the ledger
  stays the authority and a replay still reproduces the number. Both figures
  go out in one `UpdateExpression` rather than `save` + `overwrite_balance`:
  they are two halves of one sum, and a crash between two writes would leave
  an opening balance that does not explain the balance beside it, with no
  replay scheduled to notice.
- **A movement's identity never moves with an edit.** It is derived from the
  bank's own statement, so a redelivery of a corrected alert still lands on
  the same row rather than arriving as a second expense. The first correction
  keeps what the bank said in `stated`, which is the only way to tell later
  whether the alert or the correction was wrong. Manual entries get a random
  identity instead: two identical ones are two entries somebody meant to
  record, and there is nothing to deduplicate against.
- **Corrections replay the balance; only new movements use the atomic add.**
  Editing, adopting and moving a movement all recompute the affected account
  from its rows. Nudging by a delta would work, but replaying is the only
  version that cannot end up disagreeing with the ledger, and these are the
  rare paths.
- **Secrets are referenced, not stored.** A configured value of
  `ssm:/finflow/production/jwt-secret` means the SecureString at that path;
  anything else is the secret itself. Explicit per-secret rather than a global
  switch, so `grep ssm:` answers which values are real and one deployment can
  move across one at a time. Parameter Store over Secrets Manager: standard
  parameters are free, nothing here rotates automatically, and paying per
  secret per month buys nothing. Resolved once per process, so a rotation
  needs a restart.

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

### Reading Financial and Merchant together (2026-08-25)

- **The movement↔merchant join is made on read, never stored.**
  A movement keeps what the bank wrote; which canonical merchant that text
  belongs to is Merchant's decision and the user's to change. Stamping a
  `merchant_id` onto the row at write time would mean re-attributing every
  past movement after a rename, a move or a merge — a background pass that can
  fail halfway and leave two screens disagreeing. Joined on read, a correction
  is visible everywhere at once with nothing to re-process. The cost is a read
  of the user's merchants per page (a point lookup on the alias index for a
  single movement), which is one query in one partition at this scale.
  Financial declares a `MerchantDirectory` port and
  `AttributeCounterpartiesUseCase` is Merchant's published read surface behind
  it; the adapter in `financial/infrastructure/merchant/` is where Merchant's
  `MerchantId` and `MerchantCategory` stop and plain strings continue.
  Rejected: publishing a `CounterpartyResolved` integration event Financial
  would store — the merchant worker never learns Financial's movement id, and
  matching them back up would have to redo the normalization Merchant owns.

- **The attribution read is exact and never decides anything.**
  `ResolveMerchantUseCase` derives, guesses and creates; the read only answers
  from aliases that already exist. Asking a second time on the read path would
  let a screen show a merchant that does not own the spelling, and the two
  answers could differ. A counterparty nobody has resolved yet reads as
  `merchant: null` — ordinary for the seconds before merchant's worker drains
  its queue, and permanent for a name only a manual entry ever used.

- **A summary totals per currency and orders by movement count.**
  Ordering the buckets by amount would compare a figure in one currency
  against a figure in another, and no rate exists anywhere in this system; a
  count means the same thing in both. A client showing one currency has every
  amount it needs to reorder them itself. Months are the exception and run
  newest first. The bucket a grouping cannot place keeps `key: null` rather
  than being dropped — without it the groups stop adding up to the total,
  which is the one way a spending screen can lie quietly.
- **Months are grouped in a stated timezone, defaulting to Bogotá.**
  A purchase at 8pm on the 31st is the following month once it
  is read in UTC, which is wrong for everybody this deployment serves. The
  timezone is a query parameter rather than a guess from a locale, and one
  that does not exist is refused rather than quietly read as UTC.
- **A `category` filter that names nothing is refused, a `merchant_id` is
  not.** An unknown category would filter everything out and
  answer an empty page, and on a money screen zero is a credible number — so
  Financial asks the directory for the vocabulary and returns 422. An unknown
  merchant id stays an empty page on purpose: saying it does not exist would
  tell a stranger whether it is somebody else's.

### Operations

- **CORS is configuration, not code** (2026-08-24). `API_CORS_ORIGINS` is a
  comma-separated allow-list; empty mounts no middleware at all, which is the
  right default for a frontend served from the same origin. Credentials are
  off: authentication is a bearer token the client attaches itself, so asking
  browsers to carry ambient authority would add nothing but risk. Methods and
  headers are the ones this surface actually uses (`GET`/`POST`/`PATCH`,
  `Authorization`/`Content-Type`) rather than `*`. A `*` origin is refused at
  startup when `ENVIRONMENT=production`, in `ApiSettings` itself — which reads
  `ENVIRONMENT` a second time on purpose, so the rule holds wherever those
  settings are constructed instead of only where the app is built. Each origin
  is also checked for shape at startup: the comparison against the browser's
  `Origin` header is exact, so a trailing slash or a path would block every
  cross-origin call while erroring nowhere.

- **Local data is ephemeral on purpose; `just seed` is what makes that cheap**
  (2026-08-24). moto holds everything in memory, so `just aws-down`, a
  container rebuild or a reboot leaves an empty environment — nothing created
  in local development survives. Rejected: DynamoDB Local with `-dbPath`
  (durable, but only DynamoDB — SQS and EventBridge would still be moto, so
  two emulators for half the surface), LocalStack persistence (Pro only), and
  a real AWS dev environment (worth it when the frontend needs durable
  staging, not before — and now cheap enough to reconsider, since on-demand
  charges a parallel set nothing while it sits idle). The seed drives the ASGI app in process and the workers' own
  `build_worker()`, so it exercises the whole chain rather than writing rows:
  alerts in, queues drained, accounts declared *after* they arrive so
  retroactive adoption is covered. Idempotent, and the model stays unwired
  unless `--with-llm` so it neither bills nor needs the network.

- **Point-in-time recovery is on for every table** (2026-08-24), applied on
  each provisioning run rather than only at creation, so an environment that
  predates it catches up. On by default because every table holds something
  nothing else can reconstruct — the ledger balances are replayed from, the
  credentials people log in with, the merchants they renamed by hand — and
  because it is the only safeguard on the production list that cannot be added
  after it is needed. An alarm nobody set up can be set up the day it is
  missed; a table that was never backed up is gone.
- **Provisioning reconciles, it does not only create** (2026-08-24). Same
  reasoning as the line above, generalized: `create_table` is skipped once a
  table exists, so capacity lowered in configuration would have reached new
  environments only and left a running one over the free allowance the number
  was chosen to fit. It now brings tables and indexes to the configured
  throughput on every run, and waits for a new index to finish backfilling
  before returning — the endpoint reading that index is mounted the moment a
  deploy lands, and DynamoDB refuses a query against an index it is still
  filling.


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

### Environments (2026-08-26)

- **A third environment exists because on-demand bills per request.** local
  (emulator), development (real AWS, everything prefixed `dev-`), production
  (bare names). Testing against production to avoid paying twice is exactly
  the thing worth spending a cent a month to prevent.
- **The container's `~/.aws` is a named Docker volume (`finflow-v2-aws`), not
  a bind mount of the host's.** The home directory is part of the container
  layer, so every rebuild used to destroy the credentials; the documented
  workaround was copying them into `.aws/` in the repo root, which put a
  credential inside the git working tree with only `.gitignore` in front of
  it. Bind-mounting the host's `~/.aws` was rejected for the opposite reason:
  it would hand this container every AWS profile on the machine, including
  ones that have nothing to do with Finflow. The volume exposes neither —
  credentials are created inside and persist there until
  `docker volume rm finflow-v2-aws`.
- **development lives in its own AWS account** (profile `finflow-dev`,
  separate from `finflow-production`), decided 2026-08-28. It was originally
  specified as sharing production's account with only the `dev-` prefix
  keeping them apart; the account boundary is now the primary separation and
  the prefix is defence in depth — a wrong account no longer implies wrong
  data, because the names do not collide either. The consequence to remember
  is that everything account-scoped is now duplicated: the three SSM secrets
  under `/finflow/development/*` must exist in the dev account, and any
  recipe naming a profile has a `-dev` and a `-prod` form rather than one
  recipe with a production default (`deploy-outputs-*`, `deploy-logs-*`).
- **The prefix is applied in code, not by configuring nine names.** Tables,
  queues and the event bus all pass through `Environment.resource_prefix` in
  the settings validators, so `ENVIRONMENT=development` is the whole
  mechanism. Configuring each name by hand was rejected for the reason this
  whole module keeps returning to: eight right and one forgotten is the case
  that costs real data, and nothing reports it. Idempotent, so spelling the
  prefix into the variable as well is harmless. EventBridge *rules* are not
  prefixed — a rule name is unique per bus and the bus already is.
- **A merchant sighting that fails after its claim is lost, deliberately.**
  `ProcessedEventStore.claim` writes before the work, so a failure in between
  spends the claim: the redelivery answers `DUPLICATE`, the message is
  deleted, and no dead-letter alarm fires. Accepted because what is lost is
  one sighting's counters and the next sighting of the same spelling recreates
  the merchant, whereas double-counting would inflate a number the user reads.
  Financial cannot make this trade and does not — its identity comes from the
  movement's content, so a retry re-does the work correctly. Worth knowing
  before anyone "fixes" merchant's worker to expect a dead-letter it will
  never get.
- **The dead-letter alarms name their queues instead of referencing them.**
  The template owns compute and `provisioning.py` owns the queues, so there is
  no `!Ref` to reach across. The consequence to remember: renaming a queue in
  `provisioning.py` silently orphans an alarm, and an alarm over a queue that
  does not exist sits in `INSUFFICIENT_DATA` rather than failing — it looks
  healthy. Rejected the alternative of moving the queues into the template,
  which would leave the local moto environment with no way to exist.
- **The `dev-` prefix does not cover the ingest mailbox, and nothing else
  does either.** It namespaces AWS resources; the mailbox is a Gmail account.
  The reader searches `UNSEEN` and marks `\Seen`, so a local or dev
  `ingest-worker` pointed at production's mailbox consumes production's mail —
  the same failure as sharing a queue, and the one the prefix was built to
  prevent. The only separation available is a different Gmail account, or an
  empty App Password so the worker refuses to start. Documented in
  `docs/running.md`; not enforced in code.
- **Development inherits every production restriction except the emulator
  one.** `*` as a CORS origin is refused (the check is "not LOCAL"), and the
  bank-notification webhook stays unmounted (`is_local` is LOCAL only), so
  the one unauthenticated write surface never exists outside a developer's
  machine. `AWS_ENDPOINT_URL` is allowed, which is what lets the whole dev
  configuration be rehearsed against moto before it costs anything.

- **`just verify` is the integrity and isolation harness.** Two users through
  the whole chain, then two families of assertions: balances equal the
  movements behind them and the summary's buckets add up (integrity), and
  neither user reaches the other by listing, searching, reading a known id or
  writing to one (isolation). Missing rather than forbidden throughout — a
  403 would itself answer whether somebody else's record exists. It refuses
  `ENVIRONMENT=production`: it registers two users with a password committed
  to this repository, and writes movements nothing can delete.
- **Isolation is pinned at the adapter level, not only at the endpoint.**
  `tests/integration/financial/test_user_isolation.py` puts both users'
  records in one real table, because a repository missing its partition
  condition still passes a test whose fake was a dict keyed by user. The
  sharpest case is `list_unassigned_matching`: `AccountFingerprint` carries no
  user, so two people at one bank with the same last four digits produce the
  same key, and only the partition keeps their money apart.


### Deployment (2026-08-26)

- **Lambda rather than a box, and the cost is what settled it.** Four of the
  five processes are pollers that idle ~99.9% of the time, so a always-on
  instance (~$12/month) buys mostly the privilege of waiting. On Lambda the
  waiting is AWS's and free: ~50k invocations and ~23k GB-seconds a month
  against free tiers of 1M and 400k that do not expire. The Function URL is
  what tipped it past "cheaper but more work" — HTTPS with no domain, no
  certificate and no load balancer, which deletes a whole section of the
  alternative rather than trading against it.
- **One image, five entry points, chosen by the template.** Five images would
  be five things to keep in step and the drift would only show in production.
  Both Dockerfile stages sit on Lambda's own base image because `bcrypt` is a
  compiled extension: wheels built against another distribution's glibc import
  fine and fail at runtime.
- **The API needed no code.** The Lambda Web Adapter runs the same uvicorn
  command as `just run-prod`, so `create_app`, the `lifespan` eager build and
  `/docs` all survive untouched. Mangum was rejected for re-implementing the
  ASGI bridge and for its history with `lifespan` — the eager build is exactly
  what must keep working.
- **SAM owns compute, `provisioning.py` keeps the data plane.** Not an
  arbitrary split: moto emulates a queue and cannot run a function, so moving
  tables and queues into SAM would leave the local environment with no way to
  exist. The consequence is an ordering constraint — provision, then deploy.
- **Lambda inverts the queue's default and that is the whole risk.** Under
  `poll_once` a message survives unless deleted; under Lambda it is deleted
  unless the response names it. `lambda_batch.drain` holds that inversion in
  one place, and it only works because of
  `FunctionResponseTypes: [ReportBatchItemFailures]` — without that line the
  response is ignored and the batch is deleted whole, including what asked to
  come back. `tests/integration/shared/test_lambda_handlers_flow.py` drives
  real bus → real rule → real queue → handler → DynamoDB rather than trusting
  it.
- **Concurrency: Financial scales, Merchant and Ingest do not.** The polling
  loops were serial by construction and Lambda is not. Financial is safe
  because the ledger row is a conditional write and the balance an atomic
  `ADD` inside one transaction. Merchant has no equivalent proof — two alerts
  naming the same unseen merchant could each create one — so it is pinned to
  one execution until it does. Ingest is pinned because two pollers would race
  for the same `UNSEEN` mail and each mark the other's read.
- **Batch size is set by the queue's visibility timeout, not by taste.**
  Lambda refuses an event source mapping whose function timeout exceeds
  `VisibilityTimeout` (120s). With a model that can take 30s per message, ten
  per batch does not fit, so parse and merchant take three; financial calls no
  model and takes ten.
- **A deployed environment needs its own end-to-end check, and it cannot be
  `verify_flow.py`.** That script drives the app through `TestClient`, in
  process: it proves the data holds together, and proves nothing about a
  deployment — not the Function URL, not the cold start, not that the API
  resolved its signing secret from Parameter Store. `scripts/smoke.py` crosses
  the network for every call instead. Two constraints shaped it. It must not
  drain a queue: against a deployment the event source mappings are the
  consumers, and a second consumer in the script would race them for the same
  messages, so it forwards one alert and *polls the API* until the movement
  surfaces. And its user is random per run rather than fixed, because
  `verify_flow`'s two people share a password written into a tracked file —
  fine for an environment nobody else uses, not for anything reachable from
  the internet. Writes are refused against production, since no endpoint can
  delete a user afterwards; what production gets is `--read-only`.
- **`/health` names its environment because a guard on local settings is not
  a guard.** The first version of that refusal read `ENVIRONMENT` from the
  loaded env file — but `just smoke` loads `.env.development` whatever URL
  follows it, so aiming it at the production Function URL would have written
  a permanent canary user into production with the check reporting success.
  The deployment is now the one that answers the question, and the script
  fails closed when it gets no answer. Same mismatch made `--with-pipeline`
  able to publish an alert into one environment's queue while polling
  another's API; it now refuses unless ENV_FILE and the target agree.
- **Credentials must not be pinned on Lambda.** Lambda publishes its role's
  credentials as `AWS_*` environment variables, so settings read them like any
  other value and `build_session` passed them to boto3 as *static* strings.
  Harmless for a process that outlives them by seconds; fatal for the ingest
  function, whose environment stays warm for hours — `ExpiredTokenException`
  hours after a deploy that looked healthy. Detected with
  `AWS_LAMBDA_FUNCTION_NAME` from the real environment, never from settings:
  it is a fact about the runtime and an env file must not be able to claim it.
- **One SSM parameter per function, because `/finflow/*` crossed the
  environment line.** The first draft granted every function the whole prefix.
  Both stacks live in one AWS account, so that wildcard let the dev stack read
  `/finflow/production/jwt-secret` — the single value that authenticates the
  whole API, now on a public URL. The `dev-` prefix isolates tables, queues
  and the bus; nothing was doing the same for secrets. Secrets also left
  `Globals`: a reference every function carries is a permission every function
  needs. Financial resolves none and is granted none.
- **The template is checked without deploying it.** Written when the
  DevContainer had no SAM CLI and no Docker, so the first deploy would have
  been the first read of the template; it earns its place anyway, because it
  transforms **both** parameter sets and `sam validate` transforms neither.
  `just infra-check` runs cfn-lint plus SAM's own
  `samtranslator` transform — the second is what matters, because SAM's
  `Globals` accepts a fixed property list that no CloudFormation schema
  describes. It immediately found `PackageType: Image` there, which fails the
  transform outright rather than being merged into the five functions. The
  editor could not have caught it: its errors on this file were all
  `Unresolved tag: !Sub`, the YAML extension not knowing CloudFormation's
  short forms, now settled with `yaml.customTags` in `.vscode/settings.json`.
- **Gmail's forwarding confirmation is ingestion's own mail, not a
  notification.** It goes to the user's `+alias`, so only the operator could
  read it, and every new user waited on somebody fishing their link out by
  hand. It is now recognised *before* the approved-sender filter, because that
  filter would file it under an unapproved sender and discard its body — the
  link with it. Following a URL that arrived in untrusted mail is the risk,
  and it is pinned three ways: exact sender address (not the domain, which
  also sends everything else Google mails anybody), scheme plus exact host
  checked against the parsed hostname, and the `vf-` path prefix — the same
  message carries the `uf-` *cancel* link one sentence away, and a loose match
  would have made the feature quietly undo itself. Redirects are not followed.
  What confirming does **not** do is widen access: anybody holding the alias
  can already mail it directly, so forwarding is a delivery route rather than
  a permission, and the approved-sender filter is the boundary either way.
  **Accepted risk: a 2xx is the only signal there is** (2026-08-28). Google
  answers the link with an ordinary page, not an API, so an error page served
  with status 200 — an expired or already-used link — is indistinguishable
  from success and would be counted as a confirmation. Not solved; what was
  fixed is the conflation beside it, since a link Google refuses at the HTTP
  level now counts as `refused_confirmations` and never as `confirmations`,
  which is the number that claims somebody's forwarding is set up.
- **Declaring takes the enum; receiving keeps the bank's own words.** A real
  run lost five of six movements to one silent failure: a savings account
  declared with `instrument_kind: "savings"` — the *account* kind — while its
  alerts name the instrument `account`. Accepted, stored, and then matching
  nothing forever, with no error anywhere. `AccountFingerprint.from_parts`
  now takes `InstrumentKind`, so the 422 arrives at declaration time. What it
  deliberately did **not** do is narrow the receiving side: `from_alert` still
  takes free text, because a bank naming an instrument this context has never
  enumerated still describes a real account, and storing the key it gave is
  what lets that movement be adopted the day the word is added — retroactively
  and with no migration. Narrowing there would discard routing information at
  the only moment it exists.
- **A credit limit is not an opening balance, and the trap was real.** The
  same run put 12M in `opening_balance` on a card, meaning "my limit is 12M";
  the ledger read it as "you already owe 12M" and every purchase raised it.
  The arithmetic was right — on a liability the balance *is* the debt, and net
  worth subtracts it — but there was nowhere else to put the number.
  `credit_limit` is now its own field, `available` is derived from the two,
  and asking an asset for one is a 422. `available` is signed rather than
  clamped: a card over its limit is the one case worth showing. It is `None`
  and not `0` when unstated, because zero reads as "no credit left".
- **Each context publishes its own vocabulary; there is no one catalogue.**
  A client cannot guess an enum member, and hardcoding the list on the other
  side means two lists drifting apart, the failure being a 422 nobody sees
  until a user hits it. One aggregated endpoint would have been convenient and
  would have had to reach into all three contexts to build it, so each
  publishes its own from the enum its endpoints already validate against.
  Unauthenticated, like the `/merchants/categories` that predates them: this
  is the shape of the API, not anybody's data, and the registration screen
  needs it before a session exists. `instrument_kinds` sits in Ingestion's
  catalogue rather than Financial's even though it is Financial that receives
  it, because those are the words an alert arrives with — Financial takes a
  free string and matches on it. That last one is a real trap: an account
  declared with any other spelling is accepted and then never matches an
  alert, silently. Making it an enum in Financial's own vocabulary, mapped at
  the boundary the way `MovementDirection` already is, is the fix nobody has
  made yet.
- **The stack runner leaves the mailbox alone locally, and that is its one
  opinionated default.** `just up` starts four processes, not five: the Gmail
  account is shared by all three environments and the poller marks what it
  reads as seen, so a local run against an in-memory emulator would consume
  the mail development was going to process, reporting nothing anywhere. The
  webhook the API mounts under `ENVIRONMENT=local` is the intake that exists
  for this. `--with-ingest` overrides it. `up-dev` starts all five, because
  there the mail is supposed to arrive the way production receives it.
  Production is refused outright: there the five are Lambda functions AWS
  invokes, and the `*-prod` recipes remain for driving one deliberately.
- **The Web Adapter ships in all five images and is inert only by accident**
  (2026-08-28, deferred). It is in the image, so it starts in the four
  workers too, where there is no HTTP server for it to translate for. What
  keeps it harmless is that its readiness probe against `:8000/health` never
  passes, so it never reaches the point of claiming invocations — the workers
  keep running on the normal Python runtime. The cost is noise: it retries
  every 2s forever, and in `ingest`, the only worker held alive by a
  schedule, that was 235 of 301 log lines in an hour. Measured at ~18 MB a
  month against a 5 GB allowance, so this is legibility, not money, and it
  was left alone until the logging fix showed how the logs actually read.
  The fix, when it is worth it, is two final stages in the one Dockerfile
  selected per function with `DockerBuildTarget` — cheaper than the "one
  image, five functions" comment implies, since ECR already holds five
  separate copies either way.
- **The IAM block is cleared and `finflow-dev` is deployed** (2026-08-28).
  `dev-proyecto-ddd` was granted what it lacked and the stack reached
  `UPDATE_COMPLETE`; an alert forwarded that evening went mailbox to balance
  through the five deployed functions. What is still denied is read-only and
  incidental — `ce:GetCostAndUsage`, so the bill cannot be read from here, and
  `ecr:GetLifecyclePolicy`, now in `infra/iam/finflow-deploy-policy.json` but
  not yet granted, so `just ecr-prune-*` can write a policy it cannot read
  back. Both are account-side grants with nothing to fix in this repo.


### Frontend (2026-08-29)

- **A static SPA, not a server-rendered framework.** Vite + React +
  TypeScript, built to static files. Next.js was the reflex and was rejected:
  SSR earns its complexity through SEO and cookie sessions, and this has
  neither — every screen is behind a bearer token the client attaches itself,
  and the audience is the author and a few friends. What it would have added
  is a Node server to run and a framework that churns, against a project whose
  stated goal is being cheap to maintain. Serving the SPA from the API's own
  Lambda with `StaticFiles` was also considered — it would remove CORS
  entirely — and rejected because it puts the frontend inside the container
  image, making a CSS change a Lambda redeploy.

- **One repository, `frontend/` beside `src/`.** The coupling between the two
  halves is the OpenAPI contract, and in one repo a changed endpoint, the
  regenerated types and the calling code are one commit that `git bisect` can
  reason about. Two repos would buy independent teams, separate access control
  and decoupled release cadence — none of which exist here. Deliberately *not*
  a monorepo toolchain: no pnpm workspaces, no Turborepo, no Nx. There is no
  shared JavaScript package to hoist when the other half is Python.

- **`docs/openapi.json` is committed, and generated from the app rather than
  curled from a running server** (`just openapi`). CI has no server to curl,
  and a schema fetched from whatever happened to be running cannot be told
  apart from a stale one. It is built with `expose_local_only_routes=False`
  so the generated client cannot offer `POST /ingestion/bank-notifications` —
  the one call the integration guide forbids. `just openapi-check` fails when
  it drifts, which is how an endpoint changed without regenerating gets
  caught before the TypeScript build does.

- **Money is formatted by `Intl.NumberFormat` from the decimal string, with no
  decimal library.** `format()` accepts a string and keeps full precision, so
  nothing in the client ever calls `Number()` — verified against
  `9007199254740993.99`, which a float rounds and this does not. TypeScript
  types the overload as a template literal that no `string` satisfies, so the
  cast is guarded by a regex: a non-numeric value renders as its raw text
  instead of throwing a `RangeError` that would take a whole screen of
  balances down. No arithmetic helpers exist on purpose — `GET
  /financial/summary` returns totals pre-summed, and the day the client needs
  to add money is the day to reach for a decimal library, not to teach that
  module unsafe arithmetic.

- **Biome instead of ESLint plus Prettier**, for the frontend only. One
  dependency and one config replacing roughly eight, which is the same
  argument as everything else here. Root JSON and YAML stay with Prettier,
  which already handled them.

- **`src/routeTree.gen.ts` is committed** even though it is generated. It is
  produced by the Vite plugin during a dev run or a build, so ignoring it
  would mean a fresh clone cannot typecheck until someone builds first. The
  diff noise is limited to when routes are added or removed.

- **Node is pinned in the DevContainer** (`node:1` feature, version 24) rather
  than left to whatever the base image carries, for the same reason `uv.lock`
  exists: a rebuild must not silently move the toolchain.

- **A session change must invalidate the router, not just update its
  context.** `RouterProvider` merges a new context without re-running
  `beforeLoad`, so handing the guards a new session leaves them believing the
  old one: signing in bounced straight back to `/login`, and signing out left
  the dashboard mounted until a cleared cache refetched into a 401. One
  `useEffect` in `RoutedApp` calling `router.invalidate()` on every session
  change is what drives all three transitions — in, out, and a 401 arriving
  from a screen nobody navigated away from, which had no handler at all.
  Screens therefore do **not** navigate after logging in; the guard's own
  redirect does it, and doing both races.

- **A 401 from `/identity/login` or `/identity/register` is not an expired
  session.** The client middleware skips its clear-and-redirect for those two
  paths, and the message for any 401 is a fixed Spanish one rather than the
  server's body: the API returns an identical 401 for an unknown email, a
  wrong password and a malformed one precisely so the interface cannot tell
  them apart, so there is nothing in that body worth showing.

- **The frontend has a test runner, and it covers `lib/money.ts` and
  `lib/dates.ts` only.** Those two files hold the rules that a screen cannot
  be trusted to re-derive — the liability sign and its caption, no value ever
  passing through a float, months grouped in Bogota. Components are left
  untested on purpose for now: they are still changing shape, and the rules
  they must not break are asserted underneath them.

- **The frontend's guardrails live in `.claude/`, which is gitignored.** The
  `frontend-auditor` subagent and the `frontend/**` quality-gate rule are
  therefore per-machine, not part of a clone. That matches how
  `.claude/rules/quality-gate.md` already worked for Python and is fine while
  this is a one-developer project — but a second checkout gets neither, and
  nothing announces their absence. Un-ignoring `.claude/agents/` and
  `.claude/rules/` is the fix on the day that matters.

- **The DevContainer publishes its ports at the Docker level; it does not
  rely on the editor's tunnel** (2026-08-29). A devcontainer publishes
  nothing at the Docker level by default — `docker inspect` showed
  `PortBindings: {}` — and reaches the host browser only through the
  editor's auto-detected tunnel. Leaving that to detection is what made both
  URLs Vite prints dead: `localhost:5173` had nothing behind it on the host,
  and the `172.17.x.x` address it advertises is internal to Docker's bridge
  and never routable from Windows. `forwardPorts` was tried first —
  declaring the ports without publishing them, so the tunnel would not
  depend on auto-detection — and still did not come up reliably even after a
  rebuild, so it was replaced with `appPort`, which publishes them as real
  Docker ports and does not depend on the editor at all. 8000 is published
  alongside 5173 because the API call happens in the host's browser, not in
  the container, so publishing only the page would load a screen where
  everything fails. Works only because both dev servers bind 0.0.0.0, and
  takes effect only on a full rebuild, not a restart.

- **"The frontend does not come up" was the wrong URL, and the port work
  underneath it was already correct** (2026-08-29). Three container rebuilds
  went into this. What ended it was measuring the chain instead of changing
  it: `docker ps` showed `0.0.0.0:5173->5173`, Vite was listening on
  `*:5173`, and — the line that settles it — `curl
  http://host.docker.internal:5173/` from inside the container returned 200.
  `host.docker.internal` *is* the Windows host, so the host could reach the
  page all along. The browser was pointed at `172.17.0.2:5173`, the
  "Network" URL Vite prints: the container's address on Docker's bridge,
  routable only inside Docker, and dead from the host by design. It fails as
  ERR_CONNECTION_REFUSED, which is also what an unpublished port looks like,
  so it read as "the rebuild did not work" every time.

  Keep that diagnostic order. Every earlier round changed configuration
  first and never established which hop was broken, which is why a working
  chain kept getting rebuilt. A `configureServer` plugin in `vite.config.ts`
  now prints, under the URL list, that the Network one is not routable —
  the advice has to sit where the misleading URL is, not in a README.

- **Vite's host check has to be off for this project's delivery model**
  (2026-08-29). Separate from the above and found while measuring it: Vite
  answers `localhost` and bare IPs and refuses every *name* with "Blocked
  request. This host is not allowed." The editor's tunnel, an mDNS name and
  a phone on the LAN all arrive under a name, so all three were dead ends.
  `server.allowedHosts: true`, and the same for `preview`, which repeats the
  check against the built bundle. No rebuild is involved: Vite reloads its
  own config.

  Set to `true` rather than a list because the hosts that have to work are
  not knowable in advance — a tunnel URL is generated per session. The check
  guards against a hostile page pointing a domain it owns at 127.0.0.1 to
  read the dev server's source; accepted, because the dev server holds no
  secret this repo does not, it is never what gets deployed, and the product
  is explicitly meant to be opened from a phone.

- **The deprecation check is import-aware, and that is not a detail.** Two
  cheaper designs were tried and both produce noise instead of findings.
  *File-level matching*: `@tanstack/react-router`'s `fileRoute.d.ts` carries
  `@deprecated` on the `FileRoute` class while this project uses
  `createFileRoute` from the same file. *Bare symbol matching*: `@types/node`
  deprecates things called `format`, `parse` and `register`, none of which are
  the ones this code uses. Only a symbol imported *from the package that
  deprecates it* is a finding — and the package to look in is usually
  `@types/*`, not the runtime one. A hand-rolled sweep that checked `react`
  but not `@types/react` reported the frontend clean while `FormEvent` sat in
  the login form; that miss is why this is a committed script inside
  `just web-check` rather than something a person remembers to run.

  What it deliberately does not cover, because it cannot do so without noise:
  deprecated *members* of a type we do use (a field on a live interface is
  not an import), and globals that are never imported. Those stay the
  `frontend-auditor` subagent's job, which reads rather than matches.

- **The auditor confirms symbols, never files.** A `.d.ts` containing
  `@deprecated` says nothing about what this project calls: at the time of
  writing `@tanstack/react-router`'s `fileRoute.d.ts` carries the tag on the
  `FileRoute` class while we use `createFileRoute` from the same file, and
  `tailwind-merge`'s tags sit on Tailwind *class names* (`container`), not on
  `twMerge`. Both would be false positives under file-level matching, which
  is why the agent is told to read the declaration the tag sits on before
  reporting anything.

- **No component library yet.** The primitives in `src/components/ui/` are a
  handful of hand-written files. shadcn/ui is the intended destination — it
  copies components into the repo rather than adding a dependency — but
  pulling Radix in for a button and a card would be paying its cost before
  anything needs a dialog or a dropdown.

### Billing (2026-08-25)

- **DynamoDB runs on-demand, and the free tier was the thing costing money.**
  PROVISIONED gives 25 read and 25 write units free across the account, and
  DynamoDB charges a secondary index like a table: five tables and two indexes
  at three units each already spent 21 of the 25, so every new index meant
  lowering capacity again — the schema being shaped by an allowance rather
  than by the data, and one more index away from not fitting at all. Three
  units is also roughly three 4 KB reads a second, while several endpoints
  read a whole partition, so ordinary use sat one refresh away from
  throttling. Measured against a full seed run (six alerts end to end, three
  accounts, two manual entries, the summary reads): 67 DynamoDB writes and 52
  reads. At ten forwarded emails a day that is on the order of **a cent a
  month** on-demand. The trade is an unmeasurable bill for a ceiling that no
  longer constrains the design. PROVISIONED is still reachable by
  configuration, and provisioning moves existing tables in either direction.
  That last part cuts both ways and is the hazard to remember: an env file
  left on the old mode is not inert, it *reverts* live tables on the next
  provisioning run, reporting success. The setting lives in all three `.env`
  files, so all three have to move together — `.env.production` was found
  still on PROVISIONED a day later.
- **Provisioning reconciles the billing mode, not only the throughput.**
  `create_table` decides the mode only for a table that does not exist yet, so
  without this, changing the configured mode would have reached new
  environments only and said nothing while a running one stayed on the old
  one — a reconciler that silently does nothing, which is worse than one never
  written because the run reports success either way.
- **Every `UpdateTable` waits for the table to go back to ACTIVE.** DynamoDB
  refuses a second control-plane change while one is applying, and
  provisioning issues several in a row (mode, then capacity, then each index).
  Without the wait the second raises `ResourceInUseException` and the run dies
  partway through, leaving a table half-reconciled — and moto does not model
  `UPDATING`, so no test could have caught it.
- **ECR is the only bill here that grows on its own, and SAM will not cap
  it** (2026-08-28). `resolve_image_repos = true` does not push one image: it
  creates one repository *per function* in a companion stack SAM generates,
  and pushes a ~250 MB copy to each, so a deploy costs 1.26 GB and two
  deploys had already left ten images. That companion stack declares a name,
  tags and a repository policy and no `LifecyclePolicy`, so nothing ever
  prunes — past the first-year 500 MB allowance this is cents today and a few
  dollars a month, forever, after twenty deploys. `just ecr-prune-dev` /
  `-prod` applies the cap out of band rather than declaring it in
  `infra/template.yaml`, and that is the decision worth keeping: owning these
  repositories in our own template means dropping `resolve_image_repos` and
  declaring five `AWS::ECR::Repository` resources, which migrates where every
  image is pushed to fix a bill of cents. CloudFormation leaves a
  hand-applied policy alone precisely because the companion stack never
  declares that property. Re-run it if a function is ever added.
  Worth knowing before the CI pipeline is built: "promote the same image from
  dev to prod" is harder than it sounds when SAM keeps five repositories per
  account.
- **The on-demand ceiling is a blast radius, not a budget.**
  `INGESTION_DYNAMODB_MAX_*_UNITS` caps requests per second per table and per
  index, so a runaway loop throttles instead of billing; pegged for a month it
  would still cost real money. The guard for spending is a billing alarm on
  the account, which nothing in this repo can create. Twenty-five is what the
  entire free allowance used to be *shared across all seven objects*, so as a
  per-object ceiling it is far more headroom than this ever had.


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
