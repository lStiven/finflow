set shell := ['bash', '-cu']

# Configuration lives in env files, never inline here: a value hardcoded in a
# recipe would silently override the one you edited in `.env`. The application
# reads the file named by ENV_FILE.
local_env := "ENV_FILE=.env PYTHONPATH=src"
prod_env := "ENV_FILE=.env.production PYTHONPATH=src"

# Local AWS runs on moto, an in-process emulator: no Docker, no credentials.
moto_port := "5000"
moto_endpoint := "http://localhost:" + moto_port

default:
    just --list

prepare:
    uv run ruff format --check .
    uv run ruff check .
    uv run pyright
    uv run pytest

fix:
    uv run ruff format .
    uv run ruff check --fix .

format:
    uv run ruff format .

format-check:
    uv run ruff format --check .

lint:
    uv run ruff check .

typecheck:
    uv run pyright

test *args:
    uv run pytest {{args}}

test-unit:
    uv run pytest tests/unit

test-integration:
    uv run pytest tests/integration

# --------------------------------------------------
# Local AWS (moto)
# --------------------------------------------------

# Start the emulator, then create every resource. Safe to re-run.
aws-init: aws-up aws-provision

# `setsid` detaches the server so it outlives this recipe's shell.
aws-up:
    @if curl -s -o /dev/null --max-time 2 {{moto_endpoint}}; then \
        echo "moto already listening on {{moto_endpoint}}"; \
    else \
        (setsid nohup uv run moto_server -p {{moto_port}} > /tmp/moto_server.log 2>&1 &); \
        for _ in $(seq 1 20); do \
            curl -s -o /dev/null --max-time 1 {{moto_endpoint}} && break; \
            sleep 0.5; \
        done; \
        echo "moto listening on {{moto_endpoint}} (log: /tmp/moto_server.log)"; \
    fi

aws-down:
    @pkill -f moto_server && echo "moto stopped" || echo "moto was not running"

aws-provision: (_require-env ".env")
    {{local_env}} uv run python -m personal_finance.shared.infrastructure.aws.provisioning

# What actually exists right now, through the app's own session.
aws-status env_file=".env": (_require-env env_file)
    @ENV_FILE={{env_file}} PYTHONPATH=src uv run python -c "\
    from personal_finance.shared.infrastructure.aws import session as s; \
    print('tables:', s.get_dynamodb_client().list_tables()['TableNames']); \
    print('queues:', s.get_sqs_client().list_queues().get('QueueUrls', [])); \
    print('buses :', [b['Name'] for b in s.get_eventbridge_client().list_event_buses()['EventBuses']])"

# Register an inbound address and the senders its owner trusts, e.g.
#   just register-inbox --address me@inbound.test --domain bank.com
register-inbox *args: (_require-env ".env")
    {{local_env}} uv run python -m \
        personal_finance.contexts.ingestion.presentation.cli.register_inbox {{args}}

register-inbox-prod *args: (_require-env ".env.production")
    {{prod_env}} uv run python -m \
        personal_finance.contexts.ingestion.presentation.cli.register_inbox {{args}}

# --------------------------------------------------
# Running the API
# --------------------------------------------------

# What ingestion currently holds: inboxes, notifications, queue depth.
inspect env_file=".env" *args: (_require-env env_file)
    @ENV_FILE={{env_file}} PYTHONPATH=src uv run python -m \
        personal_finance.contexts.ingestion.presentation.cli.show_status {{args}}

# Drain the parse queue: SQS -> DynamoDB -> deterministic parser.
parse-worker: (_require-env ".env")
    {{local_env}} uv run python -m \
        personal_finance.contexts.ingestion.presentation.cli.run_parse_worker

parse-worker-prod: (_require-env ".env.production")
    {{prod_env}} uv run python -m \
        personal_finance.contexts.ingestion.presentation.cli.run_parse_worker

# Local: hot reload, against the emulator.
dev: (_require-env ".env")
    {{local_env}} uv run fastapi dev src/personal_finance/api/main.py

# Production-shaped: real AWS, real credentials, no reload, no emulator.
# Authenticate first, e.g. `aws sso login --profile <name>`.
run-prod: (_require-env ".env.production")
    {{prod_env}} uv run uvicorn personal_finance.api.main:app --host 0.0.0.0 --port 8000

# Create the resources in the real account named by .env.production.
provision-prod: (_require-env ".env.production")
    {{prod_env}} uv run python -m personal_finance.shared.infrastructure.aws.provisioning

_require-env env_file:
    @test -f {{env_file}} || { \
        echo "Missing {{env_file}} — copy it from {{env_file}}.example"; \
        exit 1; \
    }

sync:
    uv sync

deps-update:
    uv lock --upgrade
    uv sync

deps-update-one package:
    uv lock --upgrade-package {{package}}
    uv sync
