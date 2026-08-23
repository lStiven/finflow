# CLAUDE.md

<project_context>
Finflow is an event-driven personal finance backend.

Goal: process bank-email events automatically, extract financial transactions,
normalize merchants, update account balances, and maintain real-time net worth
without manual user input.

Delivery: a web application, reachable from a phone's browser; deliberately not
a native mobile app, so there is no app-store review in the way. When the user
says "the app" they mean this software, not a mobile app.

Scale: a private deployment for the author and a handful of friends, each
seeing only their own finances. Design for that — free-tier AWS, no
multi-tenant sharding — while keeping per-user isolation strict.

Intake model: every user forwards their bank email to their own address on
the one Gmail account this deployment owns (`user+alias@gmail.com`), which an
ingest worker polls over IMAP. Nobody's personal mailbox is ever read, and
there is no OAuth consent flow — the sender-approval filter that used to gate
what could be read still gates what is accepted.

Main flow:
User's bank forwards to their alias -> ingest worker polls the shared mailbox
-> SQS -> deterministic parser -> LLM fallback -> Pydantic validation ->
Merchant/Classification -> Financial/Account -> integration events.

Bounded Contexts:

* Ingestion: the shared ingest mailbox, forwarding-address assignment, sender
  filtering, deduplication, parsing, extraction.
* Financial: accounts, transactions, balances, debt, net worth.
* Merchant: canonical merchants, aliases/sub-merchants, categories.
* Identity: user accounts, credentials, authentication; inbox (forwarding
  address + approved senders) registration for authenticated users (delegates
  to Ingestion's own use cases through an adapter).
  </project_context>

<tech_stack>

* Python 3.13, pinned (`requires-python = ">=3.13,<3.14"`); DevContainer matches.
* FastAPI.
* uv is the only Python dependency/environment manager.
* VS Code DevContainer is the canonical development environment.
* AWS: SQS, EventBridge, DynamoDB, boto3.
* Pydantic v2 for boundary validation and mandatory LLM structured outputs.
* bcrypt for password hashing; PyJWT for stateless JWT access tokens.
* just for project task shortcuts.
  </tech_stack>

<strict_rules>

* Never use `pip`, `poetry`, `pipenv`, `conda`, `venv`, or `virtualenv`.
* Use `uv add`, `uv remove`, `uv sync`, and `uv run`.
* Prefer an existing `just` recipe when available; inspect `justfile` before using it.
* Never edit `uv.lock` manually.
* Assume commands run inside the DevContainer at `/workspaces/finflow_v2`.
* Keep Bounded Contexts isolated. Do not import another context's aggregates,
  repositories, or domain services directly. A context's own published
  application-layer use case is the one permitted integration surface for
  another context to call, and only through a dedicated adapter in the
  caller's infrastructure layer (e.g. `identity/infrastructure/inbox/`).
* Dependency direction is `presentation/infrastructure -> application -> domain`.
* Domain code must not depend on FastAPI, boto3, DynamoDB, SQS, EventBridge, or LLM providers.
* `shared` must remain minimal and contain no context-specific business logic.
* Ingestion never updates financial balances directly.
* Financial owns Account, Transaction, balances, debt, and net-worth rules.
* Merchant owns canonical merchant identity, aliases, and classification.
* Identity owns user accounts, credentials, and authentication.
* Nobody's personal mailbox is ever read. The only mailbox this system reads
  is the one it owns for exactly this purpose (IMAP, App Password, read-only
  in practice though not credential-scoped the way OAuth was) — a user only
  ever forwards mail to their own address on it. A forwarded message is
  accepted only for the senders that user approved, and never without that
  filter — an empty allow-list means accept nothing, not accept everything.
* The bank-notification webhook is a local testing seam, not a product path:
  the ingest worker reads the shared mailbox directly, in-process — nothing
  in production ever calls this webhook over HTTP. It stays unmounted outside
  `ENVIRONMENT=local`.
* Always try deterministic/template parsers before the LLM fallback.
* LLM extraction must return structured data validated by Pydantic; never accept free-form output as domain input.
* Treat email content and LLM output as untrusted data.
* Assume SQS delivery is at-least-once.
* Idempotency must survive retries and process restarts; never rely on in-memory deduplication.
* Distinguish email/message identity from financial-transaction identity/fingerprint.
* Use DynamoDB conditional/atomic writes where uniqueness is required.
* Do not record a temporary authorization and its later posted transaction as two final expenses.
* Use SQS for asynchronous work queues and EventBridge for integration events between contexts.
* Do not expose aggregate internals directly as external event payloads.
* A domain event only reaches EventBridge if a context's own translator in
  `<context>/application/integration_events.py` maps it to an `IntegrationEvent`,
  building the payload field by field. Everything else stays inside the context.
  Publish under `finflow.<context>`, carry `version` and `event_id` in the
  payload, and serialize money as a string — delivery is at-least-once and a
  JSON float loses cents.
* Use English for variables, functions, classes, modules, and domain names.
* Keep comments/docstrings concise and only where intent is not obvious from code.
* Never persist or log a plaintext password; only a hashed value may cross into
  storage. Token-signing secrets must come from config/environment, never be
  hardcoded, and the app must fail to start if a required secret is missing.
  </strict_rules>

## workflow

* Session continuity: read `PROGRESS.md` at the start of a session to see
  where things stand. After finishing a task or a meaningful chunk of work,
  update `PROGRESS.md` before ending the turn — this replaces relying on
  `/compact` or long-lived conversation history to track state. Keep
  `Last completed` to the single most recent item only, short. Do not narrate
  diffs there or anywhere else in the file: git already records what changed.
  Record in `Decisions` only what a fresh session could not recover by
  reading the code — why something is the way it is, what was rejected, what
  risk was knowingly accepted — and edit an existing entry in place rather
  than appending a new one about the same thing.
* Whenever an endpoint is added, changed, or removed, update
  `docs/postman/finflow_v2.postman_collection.json` (Postman Collection
  v2.1 format — importable by both Postman and Bruno) in the same change.
  Keep requests grouped by bounded context, matching the existing folder
  structure. If a new endpoint needs a variable beyond the environment's
  `base_url` / `fetch_token` / `token` / `user_id`
  (`docs/postman/finflow_v2.postman_environment.json`), add it there too.
  See `docs/postman/README.md`.
* Work in small iterations; do not implement multiple bounded contexts unless explicitly requested.
* Before editing, inspect the relevant code, `pyproject.toml`, and `justfile`.
* Prefer this order: domain -> unit tests -> application -> port -> infrastructure adapter -> endpoint/worker -> integration test.
* Run affected tests and existing lint/type-check recipes (`just prepare`) before
  considering a change complete.
* When a change affects a business rule or a flow — a new domain state or
  transition, how money/balances/matching work, a new cross-context
  interaction, anything auth- or security-relevant — run one `/code-review`
  pass before considering it done (add `/security-review` too when the
  change touches auth, money, or untrusted input). One pass per change is
  enough; this is a single iteration, not a loop chasing every finding.
  Report findings with concrete, justifiable reasons grounded in
  functionality or security — never flag something because it merely
  differs from a preference. Purely mechanical changes (formatting, renames,
  config, docs, logging setup) do not need this pass.
* Do not refactor unrelated code unless required to complete the task.

## commands

* Install/sync: `uv sync`
* Add dependency: `uv add <package>`
* Add dev dependency: `uv add --group dev <package>`
* Run tests: `uv run pytest`
* Run API: `uv run fastapi dev src/personal_finance/api/main.py`
* Local AWS emulator + resources (moto, one-time per session): `just aws-init`
* Run API against it (recommended over the bare `fastapi` command above, since
  it sets the required env/AWS config): `just dev`
* `just --list` / the `justfile` is the source of truth for everything else
  (formatting, lint, typecheck, AWS provisioning, inbox registration, the
  ingest/parse/merchant workers, prod-shaped runs, ...).
