# CLAUDE.md

<project_context>
Finflow is an event-driven personal finance backend.

Goal: process bank-email events automatically, extract financial transactions,
normalize merchants, update account balances, and maintain real-time net worth
without manual user input.

Main flow:
Email webhook -> SQS -> deterministic parser -> LLM fallback -> Pydantic validation
-> Merchant/Classification -> Financial/Account -> integration events.

Bounded Contexts:

* Ingestion: webhook intake, sender filtering, deduplication, parsing, extraction.
* Financial: accounts, transactions, balances, debt, net worth.
* Merchant: canonical merchants, aliases/sub-merchants, categories.
  </project_context>

<tech_stack>

* Python >=3.12; DevContainer currently uses Python 3.13.
* FastAPI.
* uv is the only Python dependency/environment manager.
* VS Code DevContainer is the canonical development environment.
* AWS: SQS, EventBridge, DynamoDB, boto3.
* Pydantic v2 for boundary validation and mandatory LLM structured outputs.
* just for project task shortcuts.
  </tech_stack>

<strict_rules>

* Never use `pip`, `poetry`, `pipenv`, `conda`, `venv`, or `virtualenv`.
* Use `uv add`, `uv remove`, `uv sync`, and `uv run`.
* Prefer an existing `just` recipe when available; inspect `justfile` before using it.
* Never edit `uv.lock` manually.
* Assume commands run inside the DevContainer at `/workspaces/finflow`.
* Keep Bounded Contexts isolated. Do not import another context's internal domain model.
* Dependency direction is `presentation/infrastructure -> application -> domain`.
* Domain code must not depend on FastAPI, boto3, DynamoDB, SQS, EventBridge, or LLM providers.
* `shared` must remain minimal and contain no context-specific business logic.
* Ingestion never updates financial balances directly.
* Financial owns Account, Transaction, balances, debt, and net-worth rules.
* Merchant owns canonical merchant identity, aliases, and classification.
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
* Use English for variables, functions, classes, modules, and domain names.
* Keep comments/docstrings concise and only where intent is not obvious from code.
  </strict_rules>

## workflow

* Work in small iterations; do not implement multiple bounded contexts unless explicitly requested.
* Before editing, inspect the relevant code, `pyproject.toml`, and `justfile`.
* Prefer this order: domain -> unit tests -> application -> port -> infrastructure adapter -> endpoint/worker -> integration test.
* Run affected tests and existing lint/type-check recipes before considering a change complete.
* Do not refactor unrelated code unless required to complete the task.

## commands

* Install/sync: `uv sync`
* Add dependency: `uv add <package>`
* Add dev dependency: `uv add --group dev <package>`
* Run tests: `uv run pytest`
* Run API: `uv run fastapi dev src/personal_finance/api/main.py`
