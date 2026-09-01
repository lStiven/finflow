# Decisions

Why things are the way they are: what was rejected, what risk was knowingly
accepted, the reason behind a shape the code does not explain on its own.
It lives apart from `PROGRESS.md` because it does not expire — `PROGRESS.md`
says where the work stands today, this says why it got that way.

One compact entry per decision, edited in place when it changes rather than
appending a new one about the same thing. No narrating diffs: git holds that.

**Never read this whole file, and never at the start of a session** — it is
about twenty thousand tokens. Search it for the specific "why" in question.

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

- **Onboarding progress is derived, never stored** (2026-08-29).
  `GET /ingestion/setup` recomputes four steps from the inbox record on every
  call; there is no endpoint that advances one and no field a client writes.
  A step counter written by a screen is wrong the moment the same person
  opens a second browser, and it cannot see the half of this that happens in
  a mailbox. What made deriving it affordable is keeping the two facts that
  *are* events on the inbox item — `forwarding_confirmed_at` and
  `first_accepted_at` — so the answer costs one read rather than a walk
  through everything the account ever received; the screen polls it while
  somebody watches. Rejected: reusing the notification reader's counts, which
  is the same answer at a cost that grows with the account's whole history.
  `ready` deliberately does not require the Gmail confirmation — somebody
  forwarding each alert by hand is connected and will never have one — but
  does require the allow-list to still approve somebody.
- **The two milestones are conditional updates, and `save` stopped being a
  `put_item`** (2026-08-29). Both writers touch the same inbox item: the
  ingest worker marking a confirmation, and its owner editing approved
  senders from a browser. A full-item write from either side erased the
  other, and a lost confirmation never comes back — Google does not send the
  mail twice.
- **A forwarding request for an unregistered alias is never confirmed**
  (2026-08-29). It used to be: any Gmail confirmation that reached the
  mailbox got its link followed. Confirming one routes a stranger's mail into
  the only mailbox this deployment reads, on the say-so of an email, so the
  alias is now resolved to a registered inbox first and the rest are
  acknowledged and counted (`unclaimed_confirmations`) without a fetch.
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

- **History is replayed, never stored** (2026-08-30). `GET /financial/history`
  reconstructs what everything was worth at any past instant instead of
  writing snapshots. It works because `restate_balance` solves
  `opening_balance` backwards, which makes `opening_balance + movements ≤ t`
  the balance at `t` by construction. A snapshot table was rejected: it needs
  a schedule, it costs writes forever, and it would be a second copy to keep
  in step with a ledger that is already the authority. The accepted
  consequence is that history is the *current best reconstruction of the
  past*, not a log of what was believed then — declaring an account today
  changes what last March reports, which is the same property that makes
  adoption retroactive.

- **The month comparison is aligned by day of the month** (2026-08-30). On the
  15th it is the 1st–15th against the 1st–15th. Comparing a young month
  against a finished one was rejected outright: it reports spending down by
  most of it every month and is right about nothing. A rolling 30-day window
  was also considered and rejected — statistically smoother, but every other
  surface in this product is month-shaped and "vs mes pasado" would stop
  being true. When the previous month is too short to reach the same day —
  the 31st against a February — the window is its whole length and `clamped`
  says so, because a client captioning it "vs julio" needs to caption that
  case differently.

- **Everything in one forward walk, and half-open throughout** (2026-08-30).
  Balances are folded once across a sorted ledger with a cursor per account
  (`Account.balance_after(..., starting=)`), not replayed per account per
  month — the naive version was ~380 passes over every transaction at
  `months=36`, on an endpoint meant to be polled. Boundaries are exclusive to
  match the month buckets, so a date-only movement landing on local midnight
  opens the month it starts rather than closing the one before. Month
  boundaries are stepped as year/month integers and resolved through UTC,
  because Havana and Asunción have started daylight saving *at* midnight on
  the 1st and that local time does not exist.

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

### Transfers between the owner's own accounts (2026-08-31)

- **A card payment is two movements, and the type system says so.** The alert
  names two of the holder's own instruments, so `ExtractedTransaction` — one
  direction, one instrument — cannot describe it. Read as a single movement it
  is wrong either way round: booked on the account the card keeps showing a
  debt that was paid; booked on the card, an outgoing movement *raises* what
  is owed, adding the payment to the balance it just cleared. Ingestion grew
  `ExtractedTransfer` (source and destination, both with digits, both
  required) and the parser's return type widened to a union, which is what
  makes every caller handle it instead of quietly reading `.counterparty`.

- **Its own detail type, not a version bump** (`TransferExtracted` v1).
  Version is for a payload whose shape changed; this is a different fact with
  different subscribers — Financial writes both sides, Merchant must never see
  it, because there is no shop in a card payment and creating one would put
  somebody's own card in their merchant list. It also deploys in either order:
  a consumer that does not know the type never matches it, where an unknown
  *version* of a subscribed type sits on the queue until the dead-letter takes
  it. Financial's EventBridge rule now lists both; Merchant's still lists one.

- **Each side is placed independently, and that is the design.** Only one of
  the two accounts may be declared, so the other side is recorded unassigned
  and adopted later through the same retroactive path as any alert — nothing
  in `ManageAccounts` had to learn about transfers. Each side's identity comes
  from its own content, so a redelivery after a partial failure completes the
  pair instead of doubling the half that succeeded. The counterparty of a side
  is the *other* instrument, spelled canonically (`credit_card *1234`): it
  feeds the fingerprint, so it can never be reworded, and clients render the
  structured `transfer` block instead.

- **A transfer is not spending, and the defaults say which surface believes
  that.** `/transactions` includes both sides (they explain why an account
  fell); `/summary` and `/history` exclude them (counting them would report a
  card payment as the month's largest expense and again as income on the
  card). The `transfers` parameter — `include` / `exclude` / `only` — is what
  lets a screen make a figure and the list behind it agree, and the
  dashboard's tiles pass `exclude` for exactly that reason.

- **One side cannot be corrected alone** (`TransferLegError` → 422). The two
  rows state one movement; editing one amount would leave two balances that no
  longer reconcile, and the aggregate holding one side cannot move the other's
  balance inside its own write. Routing and notes stay editable, which is what
  a misrouted side actually needs.

- **The model is told to refuse these.** The LLM fallback is asked for one
  movement and cannot be asked which of two instruments is the source without
  guessing — and the wrong guess moves a real balance the wrong way. So the
  prompt sets `understood=false` for money between two of the holder's own
  instruments, and only deterministic templates produce transfers. The cost is
  stated: a card payment from a bank with no template is deferred, visible,
  and not recorded — which is the safe half of the trade.

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

### Identity (2026-08-30)

- **The access token names its holder, and that is what reaches the record.**
  The `users` table is partitioned by **email**, so reading an
  account by id needed a global secondary index — a provisioning change and a
  recurring cost — for what is, at this scale, one lookup. Rejected in favour
  of carrying `email` in the token beside `sub`: `PATCH /identity/me` resolves
  the record by that claim and then checks the loaded id against `sub`, so a
  token cannot reach an address that was reassigned after it was issued. This
  only holds while the email is immutable, which is why editing it is not
  offered — moving the partition key is a migration, not an edit, and would
  have to be designed as one. `name` rides along in the token for the client
  to render at login, but it is a snapshot of issuing time: `GET /identity/me`
  reads storage, so a rename shows up there immediately and in the token only
  at the next login. Accepted cost: `verify` now requires the `email` claim,
  so every token issued before this change is refused — one forced login for
  everybody, chosen over accepting a token that cannot reach its own account.

- **`access_token_ttl_minutes` defaults to 1 minute, and stays that way.**
  Every `.env*`, the SAM template and both deployed stacks set 1440, and
  `docs/frontend-integration.md` documents that number, so the default is
  reached only by a deployment that forgot the variable — where a one-minute
  token is a loud, immediate failure (the client keeps a 30 s expiry margin,
  so login lasts about thirty seconds) rather than a quiet one. Raising the
  default to 1440 would make that same mistake silent and long-lived, which
  is the worse of the two. Not a leftover; leave it.

### Identity — proving an address, and getting back in (2026-09-01)

- **An address has to answer before an account exists behind it.**
  Registration is three calls: `verification/request` mails a six-digit code,
  `verification/confirm` trades it for a one-time ticket, `register` spends
  the ticket. Without it, anything that can POST fills the deployment with
  accounts nobody can reach — and, worse for a finance app, an account can be
  created on somebody else's address and then quietly hold their forwarded
  bank mail. Rejected: a link-based confirmation *after* the account exists,
  which is friendlier but leaves the unverified account real in the meantime.

- **Verifying is not reserving, so `confirm` hands out a ticket.** The
  alternative — marking the challenge "verified" and letting `register` look
  it up by address — would let anybody who guessed that an address had just
  been verified race its owner to the account. The ticket is 256 random bits,
  stored only as a SHA-256, spent by a conditional `DeleteItem` that checks
  the hash and the expiry in the same write.

- **The ticket is spent before the duplicate-email check.** That ordering is
  what stops `register`'s 409 from being an oracle: learning that an address
  is registered here now costs reading the code mailed to it. `POST /login`
  was already uninformative; this closes the other half.

- **Both unauthenticated endpoints mail something in every branch.** An
  address that already has an account gets "you already have one" instead of
  a code; an address with no account asking for a reset gets "there is nothing
  here". Silence in one branch would itself be the answer. It also equalises
  the timing, since an SMTP round trip dominates everything else in the
  request.

- **A one-time secret is never a low-entropy value guarding itself.** The
  six-digit code is bcrypt-hashed (salted, slow) because a million
  possibilities is seconds of work against a fast hash if the table leaks; its
  real defence is the five-attempt cap and fifteen-minute life. The reset and
  registration tokens are SHA-256 because the hash *is* the lookup key and has
  to be deterministic — safe only because what goes in is
  `secrets.token_urlsafe(32)`. Two hashers, and picking the wrong one is a
  real mistake in both directions.

- **Time-to-live is housekeeping, never a check.** DynamoDB's sweep is
  eventual and routinely hours late, so every expiry that decides anything is
  also compared in the condition expression. The record's own TTL is the
  *last* moment anything in it matters: a code accepted at minute 59 of a
  60-minute window buys a 30-minute ticket, and sweeping on the window alone
  would delete a live ticket out from under somebody still filling in the
  form.

- **Changing a password ends every session, and that costs one read per
  request.** `User.credential_version` is a counter carried in the token as
  `cv` and compared against storage by `AuthenticateUseCase`, which
  `get_current_user` now goes through. A JWT is otherwise valid until it
  expires no matter what happens to the account behind it, so a token stolen
  before a reset would keep working through the reset meant to stop it — for a
  day, at this deployment's TTL. That made the reset a formality. The price is
  a strongly-consistent `GetItem` on every authenticated request, which at a
  handful of users is far below the cost of the endpoints behind it, and it
  also stops a token for a deleted account.
  A **counter, not the moment of the change**: the comparison has to be exact,
  and a timestamp in seconds cannot tell a password set one millisecond after
  registration from the registration itself — which is precisely the case
  where the token must stop working. Accepted cost: every token issued before
  this change lacks the claim and is refused, so everybody logs in once more.

- **Password change asks for the current password even though it is
  authenticated, and a wrong one is 403.** A session left open on a shared
  machine must not be enough to take an account over. The status is the
  interesting part: 401 is the code every HTTP client turns into "you have
  been signed out", and answering it here would log somebody out for a typo.
  The caller *is* authenticated — their token is fine — so a failed
  re-challenge for one action is 403, and 401 on this route keeps meaning only
  "no token". Same reasoning covers the spent registration ticket.
  It returns a fresh token because the change invalidates the one that asked,
  and a client that did not replace it would see what looks like a
  spontaneous logout.

- **`Retry-After` is exposed through CORS.** Only a handful of response
  headers are readable across origins by default and this is not one of them,
  so the frontend on Cloudflare Pages would have seen the 429 and not the
  wait — which is the *only* thing that answer carries, since how much mail an
  address has caused and whether it has an account are exactly what these
  endpoints refuse to say.

- **SMTP over the deployment's own Gmail, not SES.** SES will not mail a
  stranger until a domain is verified and the sandbox is lifted, and this
  deployment owns no domain — the same reason the intake side is IMAP with an
  App Password rather than OAuth. It is the same casilla: read over IMAP,
  written over SMTP, one credential. Configured separately
  (`IDENTITY_MAIL_*`) all the same, so identity does not read ingestion's
  settings and so the sending account can move without the receiving one.

- **An unconfigured mailbox stops the API from starting, everywhere but
  local.** The two degraded alternatives were both worse: accepting
  registrations that can never be completed, or falling back to the logging
  notifier and writing live codes and reset links into CloudWatch. On
  `ENVIRONMENT=local` that fallback is exactly what runs, and
  `verification/request` also returns the code in its own response so a script
  can finish the flow without a mailbox. That echo is derived from nothing but
  `is_local` — a separate switch would be a switch somebody could set in
  production.

- **Scripts create accounts through an operator path, not a back door.**
  `seed_local.py` invents addresses and `smoke.py`'s canary lives at
  `@finflow.local`, which no mail server will deliver to, so neither can go
  through the code exchange. They write a ready ticket straight into the
  challenges table
  (`identity/presentation/cli/verification_tickets.py`) and then spend it
  through the real endpoint. It needs DynamoDB write access — the privilege
  whoever runs them already holds — and is reachable over no HTTP surface.
  Rejected: an environment flag that disables verification, which is a switch
  that can be set in production. Accepted cost: `just smoke`'s default depth
  now needs AWS credentials, which its docstring used to promise it did not.

- **An address with no pending challenge still costs a hash comparison.**
  `confirm` answers identically for a wrong code and for an address nothing is
  registering, but without a decoy the second branch would return before the
  bcrypt comparison the first pays for — and the difference is readable off
  the clock. It is the same guard `LoginUseCase` already runs for an unknown
  email, for the same reason.

- **Both unauthenticated endpoints are rate limited per address**, one message
  a minute and five an hour, answered with 429 and `Retry-After`. Not
  politeness: without it they are a way to aim this deployment's mailbox at
  somebody else's inbox and to burn its Gmail sending quota, which would take
  registration down for everybody.

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

- **The seed writes the one fact nothing local can produce** (2026-08-31).
  `forwarding_confirmed` is normally marked by the ingest worker after it
  follows Google's confirmation email, and no Gmail account forwards to the
  emulator — so the demo user sat permanently one step short, and the guide
  could never be seen in the state it spends most of its life in.
  `seed_local.py` now writes that mark through the inbox repository directly,
  at a fixed instant the day before the first alert. Deliberately not an
  endpoint: it is a verified fact, and a client able to claim it would turn a
  proof into an assertion. What keeps that safe is the seed refusing to run
  outside `ENVIRONMENT=local`, which it already did. `ready` never depended on
  this step — it is first alert plus an approved sender — so nothing about
  what the app considers connected changed.

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
- **Running and deploying are two guides, not one** (2026-09-01).
  `docs/running.md` had grown to 972 lines covering both, and the deploy steps
  for one environment were spread across three sections that each held part of
  the answer — secrets in one, provisioning in another, `sam deploy` in a
  third. Split into `docs/deploy.md` (getting the system into AWS: permissions,
  secrets, provision, deploy, publish, smoke) and `running.md` (running the
  processes: local, and from your machine against a real account), with
  `docs/README.md` as the index. The rule that keeps them from merging back:
  if a step needs AWS credentials to change something that persists, it
  belongs in `deploy.md`. Anchors other docs link to —
  `running.md#el-frontend`, `#4-configurar-la-cuenta-de-ingesta`,
  `#cuando-algo-falla` — were kept where they were on purpose.
- **The frontend is not hosted on this AWS account, because it cannot be**
  (2026-09-01). S3 plus CloudFront was written, reviewed and deployed, and
  died on a gate no template can pass: *"Your account must be verified before
  you can add new CloudFront resources."* An account-level block AWS puts on
  young accounts — CloudFront is a favourite of whoever wants a trustworthy
  domain in front of a phishing page — and lifting it means a support case and
  a wait. The bucket, the Origin Access Control and the response headers
  policy all created fine; the distribution alone is gated. Reverted rather
  than left half-built, because a template that cannot deploy blocks every
  unrelated change to the same stack, and this one had already cost three
  rollbacks.
- **What that design was for, in case it comes back.** The reason to put
  hosting in the same stack was never S3: it was that the frontend's origin
  and the API's `API_CORS_ORIGINS` have to agree, and `!GetAtt
  WebDistribution.DomainName` made them agree by construction. Anywhere else
  — Cloudflare Pages, Amplify, a bucket behind another CDN — that agreement
  is a domain somebody copies into `samconfig.toml`, and a stale copy fails as
  a CORS error in a browser, which reads as a broken app and never as a wrong
  deploy parameter. Cloudflare Pages does not get that for free, so
  `_web-publish` ends by grepping `samconfig.toml` for the origin it just
  published to and saying so when it is absent — a check, where the template
  had a guarantee. The rest of what was built came back unchanged in form:
  `_redirects` rewrites everything to `/index.html` with a **200** (without it
  every deep link and refresh away from `/` breaks, and only in the published
  app, never under `vite dev`), and `_headers` marks `/assets/*` immutable for
  a year while leaving `index.html` alone — Pages revalidates it per request,
  which is the invalidation this no longer has to buy.
- **`wrangler` is a devDependency whose install scripts are deliberately not
  approved** (2026-09-01). It arrives with two — `workerd` and a second copy
  of `esbuild` — and `package.json`'s `allowScripts` covers neither. Both
  exist for `wrangler dev`, which this repo never runs: a Pages direct upload
  of a built `dist/` bundles nothing and starts no worker, and `npx wrangler
  --version` answers fine with the scripts unrun. Approving them to silence
  the npm warning would buy nothing and widen what executes at install time.
- **The IAM policy grew a second file and then lost its reason**
  (2026-09-01). Granting CloudFront pushed `finflow-deploy-policy.json` past
  the 6,144-character cap on a managed policy — it was at 6,006, so the
  headroom was 138 characters that nothing in the file mentioned. The split
  into a second attached policy was right and is worth remembering the day
  anything else is added; the CloudFront half of it is now unused, and
  `finflow-web-deploy` can be detached from `dev-proyecto-ddd` in IAM.
- **One stack polls the mailbox, and it is production** (`IngestScheduleState`,
  2026-08-31). The `dev-` prefix separates tables, queues and the bus; it
  cannot separate a Gmail account. The reader searches `UNSEEN` and marks what
  it takes `\Seen`, so two deployed pollers do not duplicate an email — they
  race for it, and each message becomes invisible to whichever asked second.
  Development is `DISABLED`; a disabled stack still works in full, only its
  automatic intake stops. A second Gmail account would remove the constraint
  and was not worth standing up for a rehearsal environment.
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
  would have made the feature quietly undo itself. The one redirect this
  follows is checked the same way before it is followed.
  What confirming does **not** do is widen access: anybody holding the alias
  can already mail it directly, so forwarding is a delivery route rather than
  a permission, and the approved-sender filter is the boundary either way.
  **Confirming is a POST, and fetching the link confirmed nothing**
  (2026-09-01, found in production against the real Google). The exchange is
  `GET mail-settings…` → 302 → `GET mail.google.com…` → a page holding
  `<form action="" method="post">` with no fields, and only the POST to that
  same address confirms. This was written as a single `GET` and was wrong
  twice over: first demanding a 2xx, so every attempt was recorded as
  `refused` — and because a refusal is acknowledged on purpose, an expired
  link answers that way forever, the mail was dropped and never retried;
  then, briefly, accepting the redirect as the acknowledgement, which would
  have recorded every attempt as confirmed while the forwarding stayed
  pending. Both readings mistook the page a person clicks for the click.
  The redirect is now followed, once, only after its host is checked — and
  followed by hand rather than by the client, because an automatic redirect
  turns a POST back into a GET. Scheme and the `google.com` domain are pinned;
  which host inside it serves the form is not, since that is undocumented and
  naming today's would break this the day it changes.
  **Accepted risk: an error page served with status 200** (2026-08-28) — an
  expired or already-used link — remains indistinguishable from success. Not
  solved; what was fixed is the conflation beside it, since a link Google
  refuses at the HTTP level counts as `refused_confirmations` and never as
  `confirmations`, which is the number that claims somebody's forwarding is
  set up.
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


### Brand assets (2026-08-31)

The artwork is two generated PNGs and there is no vector, so the icon set is
resampled from `frontend/brand/finflow-logo.png` rather than redrawn. A traced
SVG was tried first and rejected: hand-tracing produced a recognisably
different mark, and a logo that drifts from the one the owner approved is
worse than a slightly soft 16px.

The sources live in `frontend/brand/`, not `frontend/public/`: everything in
`public/` is copied verbatim into the bundle, and the two originals are 3 MB
nobody would ever download on purpose. `build-icons.py` is run by hand and its
output committed, so a clone needs no image toolchain to serve the icons.

Two things the script does that are not obvious. It premultiplies by alpha
before resampling — outside the tile the source is transparent *black*, and
resampling the colour channels alone averages that into a dark fringe that at
16px is most of the mark. And the Apple touch icon is zoomed to 122%: iOS
masks with a 22.4% corner radius while this tile's own radius is 27.4%, so at
1:1 the flat fill would show as four dark notches outside the artwork.

The social card is composed, not cropped. The artwork is square and the card
is not; a centre-crop to 1200x630 leaves the wordmark 38px from the edge, so
the square is scaled until the lockup sits at 63% of the height and the gap
either side is filled by stretching the artwork's own outermost columns. The
backdrop there is a smooth vignette, so the seam has nothing to show. JPEG,
because lossless costs half a megabyte for a picture scrapers downsample.

`display: standalone` in the manifest is a deliberate choice, not a default —
added to a home-screen install it drops the browser chrome. Reverting it is a
one-line change if that turns out to be the wrong call for a web app people
also open as a tab.

The slogan lives only in metadata — `<meta description>`, `og:description`,
the manifest — and nowhere on screen. That is a gap, not an oversight: no
placement was agreed. The `og:*` URLs are relative because the address belongs
to whatever ends up serving the bundle, which the bundle is not told.

### Frontend (2026-08-29)

- **80% calm, 20% neon.** The ground and the surfaces stay sober; colour
  appears only where somebody should look or act. Three hues carry meaning —
  magenta for what is primary and for money leaving, green for money
  arriving, cyan for the secondary and for chart data — and the glow lives on
  `:hover`, which is what keeps one card lit at a time rather than all of
  them. At rest a card is nearly flat: a hairline of light on its top edge and
  a contact shadow that reads as a seam, because a heavy drop shadow on a
  ground this dark reads as dirt.

- **A figure may count up; a figure at rest may not be a float.**
  `CountUpMoney` interpolates through a float for the frames in between and
  only those — the moment it lands, and for anyone who asked for reduced
  motion, it renders the original decimal string through `Money`. It also
  seeds its state at zero rather than null: the effect runs after the first
  paint, so starting from the real amount would show the final figure, snap
  to zero and count up to it again.

- **A KPI that opens a list must bound it the same way.** The tiles report
  month to date, so the link they carry passes that month's date range.
  Without it, tapping "Gastos" showed every expense ever recorded beneath a
  figure covering one month — two numbers that cannot both be right.

- **Elevation on dark is a highlight, not a shadow.** A darker smudge under
  a dark card reads as dirt, so what separates a surface here is the hairline
  of light along its top edge where a real one would catch the room; the
  shadow underneath only anchors it. Two `.surface` rules carry it, and the
  lift is a separate class — only what can be clicked moves, because a static
  tile that reacts invites a click that does nothing. `prefers-reduced-motion`
  turns all of it off, and its `!important` is deliberate: the durations it
  has to beat are Tailwind utilities on the elements themselves, which a plain
  base-layer declaration loses to.

- **Onboarding is derived on the server and acknowledged in the browser**
  (2026-08-30). `GET /ingestion/setup` already answers the four checkable
  facts and deliberately has no field a client writes, so the screen adds
  nothing to it. What it keeps locally is only what nobody else can observe:
  the explanation was read, the address was copied, the Gmail rule was
  claimed, the welcome was shown once, the "you are connected" message was
  shown once. The two dialogs are mirror images — `welcome` needs `ready`
  false and `celebrate` needs it true — so the shell mounts both and they can
  never stack. Putting those in
  the same record as verified facts would mix a claim with a proof, and each
  stage says which of the two it is ("Verificado" against "Hecho"). Stated
  cost: signing in on another browser replays the reading, never the work.

- **`ready` closes the guide, not five green ticks** (2026-08-30). Somebody
  forwarding each alert by hand never gets Google's confirmation, so the
  forwarding stage also closes on a first alert having arrived, and arriving
  expenses settle every stage behind them — including the reading nobody
  acknowledged, because the recap reports on the setup rather than on which
  paragraphs were opened. The reverse holds too: emptying the allow-list
  makes `ready` false again and the guide comes back pointing at senders,
  which is right, since the next alert would be dropped.

- **The connect entry changes meaning instead of disappearing** (2026-08-30,
  retargeted 2026-08-31). While anything is open it is "Conectar", carries a
  dot and a count, and resumes where the person left off; once expenses
  arrive it becomes "Guías" and points at the index rather than at the
  walkthrough, which is one click further in and now sits beside the written
  guide to accounts and movements. A second permanent entry for the
  walkthrough would be one asking for something nobody has left to do, and a
  banner that outlives its onboarding is the thing people learn to stop
  reading.

- **One plasma, two strengths** (2026-08-31). The door — login and register
  — wears it at full strength: four wide pools of pink, violet, cyan and
  magenta travelling and turning across each other on a ground of their own,
  because that is the one screen where somebody is waiting on a form rather
  than reading a figure. Every signed-in screen wears the same four pools at
  10% opacity and a slower tempo, masked clear through the middle of the
  viewport so they only light the empty margins and never sit under a table
  or a balance. Two separate grounds would have made the app look like two
  products, and the earlier fully-still app read as a different design
  altogether; the door and the app now differ only in `--plasma-opacity` and
  `--plasma-tempo`.

  Rejected on the way: particles, the sweeping beam and any pulsing glow — an
  intermittent flash pulls the eye on a schedule, which is the opposite of
  ambient. The panning grid went too: behind a table it was one more set of
  lines to read. What makes it read as plasma rather than as blurred discs is
  a mismatched border-radius that turns as it travels, two colour stops per
  pool instead of one, and `screen` blending inside an isolated container.
  Each pool runs `alternate`, so it walks its path out and back rather than
  snapping home, and the four periods are near-coprime. Every layer animates
  on transform alone and none carries a fill mode, so `prefers-reduced-motion`
  — which the base layer already collapses to 0.01ms — leaves each one resting
  at the state its own class describes: a composed still, not a half-drawn
  frame.

- **Signing in and signing up are two places, not one form with two buttons**
  (2026-08-30). They share a card and a switch, but the copy, the hue (magenta
  for the return, cyan for the arrival), the panel beside them and the fields
  differ — registering asks for a name and spends the panel explaining the
  forwarding step, which is the part of Finflow nobody guesses. The mode is
  keyed in React so switching replays the entrance instead of relabelling the
  same screen in place.

- **An expiry is announced on a timer, not discovered on the next request**
  (2026-08-30). `expiresAt` is known the moment the token arrives, so the
  moment it lapses is knowable too: a tab left open overnight says so at the
  time rather than looking signed in until somebody clicks. `readStoredSession`
  exists to tell "there was no session" from "the one you had ran out" — a
  corrupt entry is deliberately *not* an expiry, since saying so would be a
  lie — and the login screen turns the second into a notice above the form. A
  deliberate sign-out reports nothing, because nothing happened to explain.

- **The beta mark is handwriting, and the only webfont** (2026-08-30). One
  small face (Caveat) loaded for one word, pencilled into a corner: it should
  read as noted by hand rather than as a shipped label. `cursive` is the
  fallback if the request never lands, and nothing else on screen wears it.

- **The correction body is a pure function with its own tests**
  (`lib/correction.ts`). Three rules of `PATCH /financial/transactions/{id}`
  are invisible in the payload's shape and each one had already produced a
  bug: amount and currency must travel together (either alone is a 422), the
  empty string clears a note while `null` means "leave it", and `account_id`
  and `detach` are refused together. Building the body inline in the form is
  what let those slip; it is testable now.

- **The transaction filters live in the URL, not in state.** A filtered list
  is something people send to themselves and come back to, and the back
  button has to undo a filter rather than leave the screen — so `apply()`
  pushes history instead of replacing it, and only pagination and the search
  box behave differently. It also lets the route loader fetch exactly what
  will be rendered. The cost is that every filter is a navigation.

- **A correction sends only what changed, and compares fields as rendered.**
  Every field on `PATCH /financial/transactions/{id}` is optional, so posting
  the whole form back would rewrite what nobody touched — and would send
  `account_id` beside `detach`, which contradict each other. The subtle half
  is `occurred_at`: the `datetime-local` input holds minutes, so a movement
  recorded at 12:30:45 reads back as 12:30. Comparing instants would send a
  45-second "correction" every time the form was opened for something else,
  permanently splitting the movement from its `stated` record. The comparison
  is therefore string-against-string, on what the field actually shows.

- **Charts may use floats; nothing a person reads may.** `money.ts` refuses
  arithmetic on purpose — a float loses cents and this is a ledger. The
  Resumen's donut broke that rule twice, knowingly and in one direction only:
  `toChartValue` for slice angles and `percentChange` for the vs-last-month
  badge. Both are ratios rendered as geometry or a rounded percentage, where
  an error of 1e-15 is not observable; every *figure* beside them still comes
  from the original decimal string. The one visible leak is the "Otros" wedge,
  whose amount is a float sum — acceptable because each exact amount is one
  category away. If a screen ever needs a total it can be held to, add a
  decimal library rather than widening this exception.

- **The month comparison is same-distance, not month-against-month.** The
  dashboard asks the summary for two windows: this month to date, and the
  same number of seconds into the previous month. The obvious version —
  comparing a three-day-old month against a complete one — makes every 1st
  report spending "down 100%", which is arithmetically true and destroys
  trust in every other number on the screen. Costs one extra query.

- **Navigation shows the screens that do not exist yet, disabled.** Six
  destinations are listed from the first release; five are `aria-disabled`
  with a "próximamente" title until their screen lands. A nav that grows an
  item per release reads as instability, and a link that goes nowhere reads
  as a bug.

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

- **The empty accounts screen is the explanation, and it is spent once**
  (2026-08-31). Finflow asks for something no other finance app does —
  declare a label yourself, for an app that is not connected to your bank —
  so with no accounts the screen is that argument: what a cuenta is, that
  everything already works without one, and that declaring one is
  retroactive. It disappears for good at the first account, because after
  that the balances say it better than any paragraph, and the same material
  stays reachable in `/guias` for whoever wants it later.

- **Declaring an account is three clicks, and the third one is a paragraph**
  (2026-08-31). Pick a kind (which advances on the click), keep the name it
  suggests, confirm. Everything else is optional and says so. The confirm
  step is kept anyway, against the "fewer clicks" rule, because it is the
  only moment somebody can be told what an account *does* here — it adopts
  what already arrived, it is not a connection to a bank, and all of it can
  be corrected — and a wizard that ends on a fourth "next" would waste it.
  The instrument fields are deliberately not prefilled: the suggested option
  is marked in the list instead, so choosing one is what makes its digits
  required rather than a hidden default trapping somebody who has no card to
  hand.

- **The accounts screen reports on the same scope as the dashboard**
  (2026-08-31). `GET /financial/accounts` computes `net_worth` over the scope
  asked for, so a screen that listed `all` would put a second, larger
  patrimonio next to the dashboard's — two answers to one question, one of
  them apparently wrong. Both ask for `open`. Nothing in the app can close an
  account yet, so nothing is hidden by it today.

- **The instrument key is decoded in the client, knowingly** (2026-08-31).
  `AccountResponse.instruments` publishes the account's stored matching keys,
  length-prefixed (`11:bancolombia|10:debit_card|4:0530|`), and showing "2
  formas de llegar" without saying which two is useless on the one screen
  where a missing link is the silent failure. `src/accounts/instruments.ts`
  parses it, decides nothing from the result, and shows anything it cannot
  read whole rather than mangled. The real fix is on the other side —
  publishing bank, kind and last four as fields — and until then this is the
  only place that knows the shape; it is recorded in
  `docs/frontend-integration.md` under what the backend does not expose.

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
  the account, which nothing in this repo can create — set up by hand and
  verified on 2026-09-01: `OverFiveDollars` watches
  `AWS/Billing EstimatedCharges` (USD, threshold 5, six-hour period) and
  mails `stiven.ddh@gmail.com` through the `over_five_dollars` topic, whose
  subscription is confirmed. It lives only in the console, so it is invisible
  to `sam deploy` and survives nothing but the account itself. Twenty-five is
  what the entire free allowance used to be *shared across all seven
  objects*, so as a per-object ceiling it is far more headroom than this ever
  had.


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
