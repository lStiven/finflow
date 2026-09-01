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

One email is not always one movement: paying a credit card from an account at
the same bank moves two balances and is neither spending nor income. That path
is closed end to end — a deterministic template, its own integration event,
two linked ledger rows, and totals that leave both of them out.

Twenty-nine endpoints across the four contexts, the three SQS workers, the
atomic ledger write, secrets from SSM, point-in-time recovery on every table,
CORS, and `just seed` to refill the emulator. Movements now carry the
canonical merchant behind the bank's text, and `GET /financial/summary`
answers what a period adds up to. `uv run pytest` green (954). `just prepare`
does **not** pass end to end: a pre-existing pyright error in
`shared/infrastructure/llm/gemini.py:120` stops it before the tests run.

DynamoDB runs on-demand: at this deployment's volume the bill is on the order
of a cent a month, and the free tier's 25 provisioned units were shaping the
schema for no real saving.

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
account and linking its alerts), `/perfil`, the `/conectar` onboarding guide
and the `/guias` section all run against the local emulator.

The frontend lives on **Cloudflare Pages**, not on this AWS account: S3 plus
CloudFront was built, deployed and reverted on 2026-09-01 because the account
is not allowed to create a distribution at all. `just web-publish` reads the
stack's `ApiUrl`, builds and uploads in one command. The support case that
would lift the CloudFront block is worth opening anyway — until it answers,
this is where the bundle is served from. See **Deployment** in
[docs/decisions.md](docs/decisions.md).

What is missing for production: publishing the bundle against it (its
`CorsOrigins` names `http://localhost:5173`, not the Pages origin, so a
published app would fail every preflight), observability's one manual step,
and a cap on LLM spending — see **Next steps**.

## Last completed

- 2026-09-01 — The frontend is live against development at
  https://finflow-dev-2tc.pages.dev — headers, SPA fallback and the bundle
  verified over HTTPS. Its calls fail the preflight until `just deploy-dev`
  carries the origin now in `CorsOrigins`.
- 2026-09-01 — The docs answer four questions without overlapping: deploying
  (new `docs/deploy.md`), running (`running.md`, now 646 lines instead of
  972), integrating (`frontend-integration.md`) and where to start
  (`docs/README.md`). Every relative link and anchor checked; every `just`
  command named in them exists.
- 2026-09-01 — Forwarding confirmation actually confirms: it is a `POST` to
  the form behind the link, not a `GET` of the link, which only rendered the
  page a person would have clicked.
- 2026-09-01 — The two `PUT` endpoints are reachable from a browser again:
  the CORS allow-list never listed `PUT`, so restating a balance and setting a
  credit limit failed the preflight in every deployed environment.
  `uv run pytest` green (953).

## Next steps

- [ ] **Next: two fixes and a mailbox split are written but not deployed.**
      Both stacks are running older code than this working tree:
      1. `just deploy-dev` — carries development's own ingest mailbox
         (`IngestMailboxAddress` in `infra/samconfig.toml`). Until it runs,
         the deployed dev stack still polls production's account and the two
         race for every forwarded email.
      2. `just deploy-prod` — carries the CORS fix (`PUT` was missing from
         the allow-list, so restating a balance and setting a credit limit
         failed the preflight in every deployed environment) and the
         forwarding-confirmation fix (a 302 was being read as a refusal).
         Note it also picks up the JWT secret rotated on 2026-09-01, which
         invalidates every token issued before it: expect to log in again.
      3. `just smoke-prod <ApiUrl>` / `just smoke <dev-url>` after each.
- [ ] **Publish the frontend against production.** `just web-publish` builds
      from the stack's own `ApiUrl` and uploads to Cloudflare Pages, then
      the origin it prints has to go into `CorsOrigins`
      (`infra/samconfig.toml`) and `just deploy-prod` run once more. That
      second deploy is not optional: production currently allows
      `http://localhost:5173` and nothing else, so a published bundle would
      load and then fail every call. Both origins can be listed at once.

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
         connect-your-bank guide, `/cuentas` and the `/guias` section. What is
         left is screens, not plumbing: merchant review, the spending summary
         and reports, and configuration. `docs/frontend-integration.md` is
         still the contract each of them has to honour. Nothing is deployed,
         and hosting is a live question again — see **Deployment** in
         [docs/decisions.md](docs/decisions.md).
- [ ] **Three account edits the API cannot do, so the app cannot offer
      them.** Reopening a closed account (`close` is one-way and there is no
      inverse); deleting one outright (deliberate — a closed account still
      explains its past movements, so this may stay refused rather than be
      built); and unlinking an instrument from an account, which is the one
      that hurts: `POST .../instruments` only adds, so a card linked to the
      wrong account cannot be moved off it from anywhere. Each needs an
      endpoint before a screen.
- [ ] **`access_token_ttl_minutes` defaults to 1, not 1440.** Every `.env*`
      sets 1440 and `docs/frontend-integration.md` documents it, so this only
      bites a deployment where the variable is missing — where it logs people
      out about thirty seconds after login, since the client keeps a 30 s
      expiry margin. Reads like a debugging leftover.
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
