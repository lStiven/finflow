set shell := ['bash', '-cu']

# Configuration lives in env files, never inline here: a value hardcoded in a
# recipe would silently override the one you edited in `.env`. The application
# reads the file named by ENV_FILE.
local_env := "ENV_FILE=.env PYTHONPATH=src"
prod_env := "ENV_FILE=.env.production PYTHONPATH=src"
dev_env := "ENV_FILE=.env.development PYTHONPATH=src"

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

# Fill the emulator with the demo user, its alerts and its accounts. moto
# holds everything in memory, so this is what makes a restart cheap rather
# than expensive. Safe to re-run.
seed *args: (_require-env ".env")
    {{local_env}} uv run python scripts/seed_local.py {{args}}

# Set the senders an existing user trusts, e.g.
#   just register-inbox --user-id 11111111-... --domain bank.com
register-inbox *args: (_require-env ".env")
    {{local_env}} uv run python -m \
        personal_finance.contexts.ingestion.presentation.cli.register_inbox {{args}}

register-inbox-dev *args: (_require-env ".env.development")
    {{dev_env}} uv run python -m \
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

# Integration events that reached the bus. `--follow` keeps polling.
events *args: (_require-env ".env")
    @{{local_env}} uv run python -m \
        personal_finance.contexts.ingestion.presentation.cli.show_events {{args}}

events-dev *args: (_require-env ".env.development")
    @{{dev_env}} uv run python -m \
        personal_finance.contexts.ingestion.presentation.cli.show_events {{args}}

events-prod *args: (_require-env ".env.production")
    @{{prod_env}} uv run python -m \
        personal_finance.contexts.ingestion.presentation.cli.show_events {{args}}

# Poll the ingest mailbox: IMAP -> ReceiveBankNotificationUseCase -> SQS.
ingest-worker: (_require-env ".env")
    {{local_env}} uv run python -m \
        personal_finance.contexts.ingestion.presentation.cli.run_ingest_worker

ingest-worker-prod: (_require-env ".env.production")
    {{prod_env}} uv run python -m \
        personal_finance.contexts.ingestion.presentation.cli.run_ingest_worker

# Drain the parse queue: SQS -> DynamoDB -> deterministic parser.
parse-worker: (_require-env ".env")
    {{local_env}} uv run python -m \
        personal_finance.contexts.ingestion.presentation.cli.run_parse_worker

parse-worker-prod: (_require-env ".env.production")
    {{prod_env}} uv run python -m \
        personal_finance.contexts.ingestion.presentation.cli.run_parse_worker

# Drain merchant's queue: TransactionExtracted -> canonical merchants.
merchant-worker: (_require-env ".env")
    {{local_env}} uv run python -m \
        personal_finance.contexts.merchant.presentation.cli.run_merchant_worker

merchant-worker-prod: (_require-env ".env.production")
    {{prod_env}} uv run python -m \
        personal_finance.contexts.merchant.presentation.cli.run_merchant_worker

# Drain financial's queue: TransactionExtracted -> ledger rows and balances.
financial-worker: (_require-env ".env")
    {{local_env}} uv run python -m \
        personal_finance.contexts.financial.presentation.cli.run_financial_worker

financial-worker-prod: (_require-env ".env.production")
    {{prod_env}} uv run python -m \
        personal_finance.contexts.financial.presentation.cli.run_financial_worker

# Store a secret in SSM Parameter Store, so the env file only holds a
# reference. The value is read from a prompt, never from the command line: a
# command line is visible in `ps` to every process on the box, and lands in
# shell history.
# Usage: just secret-put /finflow/production/jwt-secret
secret-put name env_file=".env.production": (_require-env env_file)
    @printf 'value: ' >&2; \
    read -rs FINFLOW_SECRET_VALUE; echo >&2; \
    ENV_FILE={{env_file}} PYTHONPATH=src \
    FINFLOW_SECRET_NAME='{{name}}' \
    FINFLOW_SECRET_VALUE="$FINFLOW_SECRET_VALUE" \
    uv run python -c "\
    import os; \
    from personal_finance.shared.infrastructure.aws.session import get_ssm_client; \
    get_ssm_client().put_parameter(Name=os.environ['FINFLOW_SECRET_NAME'], \
        Value=os.environ['FINFLOW_SECRET_VALUE'], Type='SecureString', \
        Overwrite=True); \
    print('stored', os.environ['FINFLOW_SECRET_NAME'])"
    @echo "put this in {{env_file}}:  ssm:{{name}}"

# Local: hot reload, against the emulator.
dev: (_require-env ".env")
    {{local_env}} uv run fastapi dev src/personal_finance/api/main.py

# --------------------------------------------------
# The whole stack, one terminal
# --------------------------------------------------
#
# `dev`, the four workers and — locally — the emulator behind them, started
# together and stopped together. See scripts/run_stack.py.

# Everything local: emulator, resources, demo data, then the processes.
# Flags go through, e.g. `just up --api-port 8001 --with-ingest`.
up *args: aws-init seed
    @{{local_env}} uv run python scripts/run_stack.py {{args}}

# The five processes against the dev- resources in real AWS. Run
# `just provision-dev` once first; there is nothing to seed, the data persists.
up-dev *args: (_require-env ".env.development")
    @{{dev_env}} uv run python scripts/run_stack.py {{args}}

# Production-shaped: real AWS, real credentials, no reload, no emulator.
# Authenticate first, e.g. `aws sso login --profile <name>`.
run-prod: (_require-env ".env.production")
    {{prod_env}} uv run uvicorn personal_finance.api.main:app --host 0.0.0.0 --port 8000

# Create the resources in the real account named by .env.production.
provision-prod: (_require-env ".env.production")
    {{prod_env}} uv run python -m personal_finance.shared.infrastructure.aws.provisioning

# --------------------------------------------------
# Development: a real AWS account, every resource prefixed `dev-`
# --------------------------------------------------

# Create the `dev-` resources in the dev account named by .env.development.
# Separate account from production, and prefixed on top of that.
provision-dev: (_require-env ".env.development")
    {{dev_env}} uv run python -m personal_finance.shared.infrastructure.aws.provisioning

run-dev: (_require-env ".env.development")
    {{dev_env}} uv run uvicorn personal_finance.api.main:app --host 0.0.0.0 --port 8000

ingest-worker-dev: (_require-env ".env.development")
    {{dev_env}} uv run python -m \
        personal_finance.contexts.ingestion.presentation.cli.run_ingest_worker

parse-worker-dev: (_require-env ".env.development")
    {{dev_env}} uv run python -m \
        personal_finance.contexts.ingestion.presentation.cli.run_parse_worker

merchant-worker-dev: (_require-env ".env.development")
    {{dev_env}} uv run python -m \
        personal_finance.contexts.merchant.presentation.cli.run_merchant_worker

financial-worker-dev: (_require-env ".env.development")
    {{dev_env}} uv run python -m \
        personal_finance.contexts.financial.presentation.cli.run_financial_worker

# --------------------------------------------------
# Deploying: the five functions, from infrastructure only
# --------------------------------------------------
#
# SAM owns the compute plane (functions, triggers, roles, the API's URL).
# `provisioning.py` still owns the data plane (tables, queues, bus, rules),
# because local runs against moto with the same code and moto cannot run a
# function. Provision first, deploy second: the event source mappings below
# point at queues that have to exist.

sam_dir := "infra"
# Absolute on purpose: `sam build` resolves `--config-file` relative to the
# template's directory and `sam deploy` does not, so any relative path is wrong
# for one of the two. `infra/samconfig.toml` sent build looking in infra/infra/.
sam_config := justfile_directory() / "infra" / "samconfig.toml"

sam-validate:
    sam validate --lint --template {{sam_dir}}/template.yaml

# Both environments in one pass, which `sam-validate` does not do: cfn-lint
# plus SAM's own transform, once per parameter set. Needs no Docker.
infra-check:
    PYTHONPATH=src uv run python scripts/check_template.py

# Build the image and deploy. Authenticate first, e.g. `aws sso login`.
deploy-dev: (_require-env ".env.development")
    sam build --config-env development \
        --config-file {{sam_config}} \
        --template {{sam_dir}}/template.yaml
    sam deploy --config-env development --config-file {{sam_config}}

deploy-prod: (_require-env ".env.production")
    sam build --config-env production \
        --config-file {{sam_config}} \
        --template {{sam_dir}}/template.yaml
    sam deploy --config-env production --config-file {{sam_config}}

# Split per environment on purpose: the two live in different AWS accounts, so
# a single recipe with a default profile would read the wrong account whenever
# the stack name and the credentials disagree.

# The API's HTTPS address, and everything else the stack published.
deploy-outputs-dev:
    @aws cloudformation describe-stacks --stack-name finflow-dev \
        --profile finflow-dev \
        --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output table

deploy-outputs-prod:
    @aws cloudformation describe-stacks --stack-name finflow \
        --profile finflow-production \
        --query 'Stacks[0].Outputs[].[OutputKey,OutputValue]' --output table

# Tail one function's logs, e.g. `just deploy-logs-prod FinancialFunction`.
deploy-logs-dev name="ApiFunction":
    sam logs --stack-name finflow-dev --name {{name}} \
        --profile finflow-dev --tail

deploy-logs-prod name="ApiFunction":
    sam logs --stack-name finflow --name {{name}} \
        --profile finflow-production --tail

# Every deploy pushes the image once per function — five copies of ~250 MB —
# into five repositories SAM creates and then never prunes, so ECR storage
# grows by the whole set on each run and is billed for as long as the account
# exists. It is the only bill in this project that grows on its own.
#
# The policy attaches to the repositories, not to today's images, so it keeps
# applying to later deploys and re-running this is a no-op. `keep` is how many
# deploys back a rollback can still reach. Lambda serves a running function
# from its own cached copy of the image, so expiring one here does not disturb
# it — it removes the option of rolling back to it.

# Cap each SAM repository at `keep` images, newest first.
ecr-prune-dev keep="3": (_ecr-prune "finflow-dev" keep)

# Cap each SAM repository at `keep` images, newest first.
ecr-prune-prod keep="3": (_ecr-prune "finflow-production" keep)

# Repositories are found by the tag SAM puts on them, not by name: the names
# carry a hash of the stack that cannot be reproduced from here.
_ecr-prune profile keep:
    #!/usr/bin/env bash
    set -euo pipefail

    policy=$(printf '{"rules":[{"rulePriority":1,"description":"Keep the %s most recent images; older ones are billed forever.","selection":{"tagStatus":"any","countType":"imageCountMoreThan","countNumber":%s},"action":{"type":"expire"}}]}' '{{keep}}' '{{keep}}')

    capped=0
    while read -r name arn; do
        [ -n "$name" ] || continue

        managed=$(aws ecr list-tags-for-resource --profile {{profile}} \
            --resource-arn "$arn" \
            --query "tags[?Key=='ManagedStackSource'].Value" --output text)

        if [ "$managed" != "AwsSamCli" ]; then
            echo "skipped  $name (not SAM-managed)"
            continue
        fi

        aws ecr put-lifecycle-policy --profile {{profile}} \
            --repository-name "$name" \
            --lifecycle-policy-text "$policy" > /dev/null
        echo "capped   $name at {{keep}} images"
        capped=$((capped + 1))
    done < <(aws ecr describe-repositories --profile {{profile}} \
        --query 'repositories[].[repositoryName,repositoryArn]' --output text)

    test "$capped" -gt 0 || { echo "No SAM-managed repositories found."; exit 1; }

# Drive two users end to end and assert nothing of one reaches the other.
# Reads ENV_FILE, so it runs against whichever environment you point it at.
verify env_file=".env" *args: (_require-env env_file)
    ENV_FILE={{env_file}} PYTHONPATH=src uv run python scripts/verify_flow.py {{args}}

# Drive a *deployed* API over HTTP: HTTPS, cold start, the token the
# deployment signed. The URL comes from `just deploy-outputs-dev`. Add
# `--with-pipeline` to also forward one alert and wait for the deployed
# workers to place it.
smoke base_url *args: (_require-env ".env.development")
    {{dev_env}} uv run python scripts/smoke.py {{base_url}} {{args}}

# Production gets the read-only depth: every write leaves behind a user that
# no endpoint can delete.
smoke-prod base_url *args: (_require-env ".env.production")
    {{prod_env}} uv run python scripts/smoke.py {{base_url}} --read-only {{args}}

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
