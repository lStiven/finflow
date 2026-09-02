# Progress

Living log of where the work stands. This exists so a fresh session (after
`/clear` or a new session) can pick up without replaying old conversation —
read this first, and update it after finishing a task or a meaningful chunk
of work, instead of relying on conversation history to carry it forward.

Only that: what is done, what is next, what is blocked, short enough to read
at a glance — every session pays for this file. `Last completed` holds the
five most recent items at most; the oldest drops off rather than piling up.

**Why** things are the way they are lives in
[docs/decisions.md](docs/decisions.md) — what was rejected, what risk was
knowingly accepted, everything a fresh session could not recover by reading
the code. Git already records *what* changed, so neither file narrates diffs.

## Current focus

Financial is complete for v1, backend-side. Accounts are **declared by
their owner**, never discovered: Finflow works with none at all — every alert
is recorded and stays unassigned, which is a complete answer for somebody
watching only what comes in and goes out. Declaring an account starts the
association and is retroactive. Money that never emails can be entered by
hand, and anything recorded can be corrected.

One email is not always one movement, and one movement is not always half of
an email. Paying a credit card from an account at the same bank moves two
balances and is neither spending nor income: that path is closed end to end —
a deterministic template, its own integration event, two linked ledger rows,
and totals that leave both of them out. Paid from **another** bank, a wallet
or cash, no alert can name both instruments, so the side that is knowable is
entered by hand through `POST /financial/transactions/transfer` and recorded
as a transfer leg whose counterpart is external. It moves its account's
balance and counts for nothing, which is what stops a card payment reporting
as the month's largest income.

Identity now proves an address before it will build an account on it, and
knows how to let somebody back in. Registering is three calls — a six-digit
code by mail, traded for a one-time ticket that `POST /identity/register`
spends — and a forgotten password is recovered through a 30-minute link.
Changing a password ends every session opened with the old one, which is the
one thing that makes the reset worth anything: every authenticated request now
compares the token's credential generation against the account's.

Thirty-six endpoints across the four contexts, the three SQS workers, the
atomic ledger write, secrets from SSM, point-in-time recovery on every table,
CORS, and `just seed` to refill the emulator. Movements now carry the
canonical merchant behind the bank's text, and the reporting surface is
finished backend-side: `GET /financial/summary` answers what a period adds up
to, ranks and folds it, and compares it against the window before; `GET
/financial/trends` answers the stacked chart. `just prepare` passes end to end
— format, lint, types and 1233 tests. The pyright error in
`shared/infrastructure/llm/gemini.py:120` that had been stopping it is gone
without the file changing, so it was the installed stubs, not the code.

DynamoDB runs on-demand: at this deployment's volume the bill is on the order
of a cent a month, and the free tier's 25 provisioned units were shaping the
schema for no real saving. Spending is watched from outside the repo: the
`OverFiveDollars` alarm on `AWS/Billing EstimatedCharges` mails
`stiven.ddh@gmail.com` through the `over_five_dollars` topic — subscription
confirmed, state OK.

**Both stacks are deployed and running** (2026-09-01), five Lambda functions
each from one container image, the API on a Function URL with no domain to
buy:

| | Stack | Ingest mailbox | Poll |
|---|---|---|---|
| production | `finflow` | `finflowingest@gmail.com` | 1 min |
| development | `finflow-dev` | `finflowdevelopment@gmail.com` | 5 min |

`just deploy-outputs-prod` / `-dev` print their URLs. Separate mailboxes is
what lets both poll at once — one account behind two pollers is a race, not a
second environment. Production carries a real user whose personal Gmail
forwards to their alias.

The frontend has started. `frontend/` holds a Vite + React + TypeScript SPA
whose types are generated from the API's own OpenAPI document, so a router
change in Python fails the TypeScript build rather than a screen in a browser.
Session, money and date handling, the door (login/register, on a moving neon
ground), the dashboard, the Transacciones surface, `/cuentas` (declaring an
account and linking its alerts), `/comercios` (the review queue and the
corrections behind it), `/reportes`, `/perfil`, the `/conectar` onboarding
guide and the `/guias` section all run against the local emulator.

The frontend lives on **Cloudflare Pages**, not on this AWS account: S3 plus
CloudFront was built, deployed and reverted on 2026-09-01 because the account
is not allowed to create a distribution at all. `just web-publish` reads the
stack's `ApiUrl`, builds and uploads in one command. The support case that
would lift the CloudFront block is worth opening anyway — until it answers,
this is where the bundle is served from. See **Deployment** in
[docs/decisions.md](docs/decisions.md).

Both bundles are published and answered: `https://finflow-apk.pages.dev`
and `https://finflow-dev-2tc.pages.dev` are in each stack's deployed
`CorsOrigins`. Production's own verification/recovery release is held until
development has run a few days on it: its table exists, the deploy and the
republish do not. That plus observability's one manual step and a cap on LLM
spending is what production still misses. See **Next steps**.

## Last completed

- 2026-09-02 — **A card paid from outside the app is no longer an expense.**
  `TransferLeg`'s counterpart became optional — all three fields together,
  refused half-written in the domain and again in the DynamoDB reader — so a
  leg can say plainly that its other side is elsewhere. `POST
  /financial/transactions/transfer` enters one: `role` fixes the direction and
  the account is required, because the movement asserts a balance moved and
  nothing would ever adopt it later. A lone leg is correctable where a paired
  one answers 409, and cannot be detached at all. The seed now covers both
  shapes side by side, plus Lulo's three alerts and the account that adopts
  them. `/code-review high` found the detach hole; it is closed with tests.
  **Frontend follow-up is open** — see Next steps.
- 2026-09-02 — **`/reportes` is built**, both halves now done. One filter row
  (periodo, cuenta, moneda) scopes seven reads, so every figure on screen
  describes the same window: four KPI tiles with deltas, cashflow columns,
  categories ranked with a delta each, the stacked run over time, top
  merchants, spend by weekday, and the biggest movements. Charts are boxes,
  not SVG — hit targets, keyboard labels carrying the figures, and an sr-only
  table twin. Shaping lives in `reports/shape.ts` and `lib/periods.ts`, both
  tested (213 frontend tests). Reviewed by hand: the pass caught the stacked
  chart labelling *unattributed* spending as the folded remainder, and an
  `aria-describedby` that would have read the whole table once per column.
- 2026-09-02 — **The reports backend is done.** `/financial/summary` gained
  `day`/`week`/`weekday` buckets, a `currency` pin, `order=amount`, `top` with
  an `others` remainder, and `compare` against the preceding window of equal
  length; `/financial/transactions` gained `sort=amount`; and
  `GET /financial/trends` is new — a stacked time series, bands ranked once
  over the whole range and buckets dense. Postman, the frontend contract and
  Decisions updated. A `/code-review high` pass found three real bugs
  (a bucket cut short by `to` not saying so, a period miscounted off an
  exclusive `to`, and `compare` wrongly dropped for `weekday`); all three are
  fixed with regression tests.
- 2026-09-02 — **Lulo bank has a deterministic parser**, the second bank to
  get one: three templates (Bre-B in and out, plain incoming transfer) built
  from four real alerts, Spanish long dates on a 12-hour clock, and
  `lulobank.com` both in the registry and as a one-click sender on
  `/conectar`. Its own word for an account is ignored on purpose — see
  Decisions.
- 2026-09-02 — **Development runs the verification/recovery release end to
  end**: the stack redeployed at 00:21 (so `Retry-After` is exposed and a
  wrong current password answers 403), and the bundle republished — its
  registration sends `verification_token`, and the lazy chunks for
  `/recuperar` and `/restablecer` call `password/forgot` and
  `password/reset`. `just smoke <dev-url>` passes 17/17 against it.
  Production is held back on purpose until this has run a few days.
## Next steps

- [ ] **The frontend has not been updated for external transfer legs**, and
      three concrete breakages are already known (found by the `/code-review`
      pass over the backend change, not guessed at). Only the backend and its
      docs were in scope on 2026-09-02; this is the other half.
      1. `frontend/src/api/schema.d.ts` is stale — regenerate with
         `just web-types`. It still types `counterpart_movement_id` as
         `string`, has no `external`, no `transfer_roles` and no
         `POST /financial/transactions/transfer`, which is precisely why
         `tsc` does not catch the two below.
      2. `routes/transacciones/$transactionId.tsx:384` links to
         `transfer.counterpart_movement_id` unconditionally — null on an
         external leg, so "ver la otra mitad" navigates to
         `/transacciones/null`. Gate it on `transfer.external`.
      3. `lib/transfers.ts:34` renders `Pago a otra cuenta tuya ···· null` on
         the dashboard, the list and the detail, and the detail hides the
         `Contraparte` row for any transfer — so "Nequi", the only thing
         naming the other side, is never shown. Both need the `external`
         branch.
      Then the screen that makes the endpoint reachable: a "fue un pago de
      tarjeta / traslado entre mis cuentas" option on the manual-entry form,
      posting `role` + `account_id` instead of `direction`. `just seed` already
      leaves two of these in the local data to look at.

- [ ] **Production waits for development to prove itself.** Deliberate, and
      the reason the steps below are not being run today: the release is live
      in development end to end, so production goes second and only once dev
      has run a few days without surprises. Half of the prerequisite is
      already done — `credential_challenges` exists in the production account
      (2026-09-02), so `provision-prod` is behind us. What is left, in this
      order, the day the decision is made:
      1. `just deploy-prod` — carries `MailFromAddress`,
         `MailAppPasswordParameter` and `PasswordResetUrl`, which are
         **required**: the API refuses to start without them. The secret they
         point at already exists
         (`/finflow/production/mailbox-app-password`, the ingest worker's own
         App Password — one credential, read by IMAP and written by SMTP), so
         there is nothing to `secret-put`.
      2. `just web-publish` — **in the same sitting, not later.** Production's
         published bundle predates this work and posts a registration without
         `verification_token`, which the new API answers 422. This is exactly
         what broke development for a few hours on 2026-09-01.
      3. `just smoke-prod <ApiUrl>` — note it only checks health and the auth
         guard (`--read-only`, because a write leaves a user nothing can
         delete), so it will not tell you the new endpoints work. That answer
         comes from development.
      Note the deploy also rotates every session, twice over: tokens now carry
      a `cv` claim and are refused without it. Everybody logs in again.

- [ ] **Next: what production actually needs.** In order of what hurts
      soonest:
      1. **Observability.** One click away, and only in production. Logs go
         to `/finflow/<stack>/<name>`, three CloudWatch alarms — one per DLQ
         — publish to an SNS topic on any message at all, and `AlertEmail` is
         set and deployed in both environments. Development's subscription is
         confirmed; **production's is still `PendingConfirmation`**, so its
         alarms page nobody. Confirm it from the mail AWS sent to
         `llstiven.work@gmail.com`, or resubscribe if it expired.
      2. **CI.** There is a Dockerfile, a SAM template and now a post-deploy
         gate (`just smoke`), but nothing builds or deploys automatically.
         Both stacks have been built and deployed by hand from the
         DevContainer, so nothing here is untested — it is simply manual, and
         a fix can sit in the working tree while production runs without it,
         which is what happened on 2026-09-01. The pipeline worth building:
         build the image **once**,
         deploy it to `finflow-dev`, `just smoke <dev-url> --with-pipeline`,
         then promote the same image to production behind a manual approval
         and `just smoke-prod <url>`. Building separately per environment
         would mean what was tested is not what shipped.
      3. **A spending cap on the LLM** (see the standalone item below). The
         cheapest of the three and the only one that costs money while it is
         missing.
      4. **The rest of the frontend.** The foundation is in (`just web`).
         Done: the dashboard, the whole Transacciones surface, the
         connect-your-bank guide, `/cuentas`, `/comercios`, `/reportes`, the
         `/guias` section, the door's code step, `/recuperar`, `/restablecer`
         and the password card on `/perfil`. What is left is **one** screen:
         configuración, still the only entry disabled in the shell's nav.
         Nothing has been looked at in a browser this session — there is no
         headless browser in the DevContainer, so `/reportes` was verified by
         its seven queries against the emulator, by unit tests over real
         payload shapes, and by the build. It has never been *seen*.
- [ ] **An instrument cannot be unlinked from an account.** Giving an
      account de baja is *not* the gap — `POST /financial/accounts/{id}/close`
      exists and `/cuentas` calls it, with a "Cerradas" tab to see what was
      closed. What is missing is narrower, and only the first one hurts:
      `POST .../instruments` only adds, so a card attached to the wrong
      account cannot be moved off it from anywhere; there is no inverse of
      `close`, so a closed account cannot be reopened; and there is no
      `DELETE` at all, which is deliberate — a closed account still explains
      its past movements. The first needs an endpoint before a screen.
- [ ] **Decide whether merchants are per-user or shared.** They are per-user
      today — partition key is the owner, and `just verify` shows Ana and
      Bruno holding separate `Éxito` records that renaming one does not touch.
      Shared merchants would match the intuition that a business is the same
      business for everybody, but renames and categories are personal
      decisions, and `times_seen` would leak one person's habits into
      another's list. The middle option nobody has designed yet: a shared
      catalogue of canonical merchants with per-user overrides on top.
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
- [ ] **Two banks have a parser**: Bancolombia (three sender domains) and
      Lulo bank (`lulobank.com`). Every other bank falls through to the LLM,
      which costs money per email and refuses when unsure. More banks get
      added over time; this is deliberate, not a gap. Lulo's own list is not
      finished either — the four alerts it was built from are two incoming
      shapes and one outgoing, so a card purchase, a withdrawal or a fee at
      Lulo still goes to the model.
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

- **`finflow-dev` and `finflow-production` resolve to the same AWS account**
  (792884702854), so the account separation recorded under Environments is
  intended but not real. As of 2026-09-01 they are at least two different IAM
  users (`dev-proyecto-ddd`, `proyecto-ddd`), which separates permissions but
  not data: the `dev-` prefix and the SSM path are still the only things
  keeping the two apart, and the comments claiming otherwise in
  `.env.development.example`, `infra/samconfig.toml` and `docs/running.md`
  overstate the isolation. It stops being a note the moment production holds
  other people's finances: a mistyped profile then points a rehearsal command
  at real money.
- **The `proyecto-ddd` user's permissions are unverified.** It reads SSM and
  CloudFormation, but cannot describe a DynamoDB table or list its own IAM
  policies, so whether `FinflowDeploy` (`infra/iam/finflow-deploy-policy.json`)
  is attached to it is unknown — and `just provision-prod` needs more than the
  deploy itself does. Find out on the next `deploy-prod` rather than by
  guessing.

## Decisions

Moved to [docs/decisions.md](docs/decisions.md). They were 1292 of this
file's 1500 lines and they do not expire, so they buried the part that
changes every day: what is done and what is left.

This file answers **where the work stands**. That one answers **why it is
this way**. A new decision goes there, editing the existing entry when there
is already one about the same thing.
