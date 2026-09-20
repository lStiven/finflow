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

# The brackets are not decoration: `just` runs this line through a shell whose
# own command line contains the pattern, so a bare `-f moto_server` matches
# that shell and kills it mid-recipe — moto stops, and the recipe dies by
# signal 15 before it can say so. `[m]oto_server` cannot match itself.
aws-down:
    @pkill -f "[m]oto_server" && echo "moto stopped" || echo "moto was not running"

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

# Does this deployment's mailbox actually send? Walks the same four steps the
# API does — configuration, connect, log in, send — and says which one failed.
# Without an address it stops after the login, which proves the credential
# without putting anything in anybody's inbox.
mail-check recipient="" env_file=".env": (_require-env env_file)
    @ENV_FILE={{env_file}} PYTHONPATH=src uv run python -m \
        personal_finance.contexts.identity.presentation.cli.check_mail {{recipient}}

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

# Named `alerts-worker` rather than `alerts-*`: `alerts-dev`/`alerts-prod`
# below are the CloudWatch alarm recipes, which are an unrelated thing.
#
# Drain alerts' queue: MovementRecorded -> a Telegram message.
alerts-worker: (_require-env ".env")
    {{local_env}} uv run python -m \
        personal_finance.contexts.alerts.presentation.cli.run_alerts_worker

alerts-worker-prod: (_require-env ".env.production")
    {{prod_env}} uv run python -m \
        personal_finance.contexts.alerts.presentation.cli.run_alerts_worker

# Telegram cannot reach a laptop and `setWebhook` wants a public HTTPS name,
# so the inbound half of linking is the one part the network will not allow
# locally. Everything else stays real. Pass the `start=` payload from the
# link_url that `POST /alerts/channels` returned.
#
# Pretend to be Telegram: post a synthetic `/start <token>` to the webhook.
telegram-update token *args: (_require-env ".env")
    {{local_env}} uv run python scripts/telegram_update.py {{token}} {{args}}

# Registering the webhook is how the two sides come to agree on the secret
# header, which is the whole of its authentication. A one-off per deployment,
# not part of a deploy: two stacks registering against one bot would fight,
# and the loser would silently stop receiving updates. Telegram needs https,
# so a laptop uses `just telegram-update` instead.
#
# Point the bot at a deployed webhook, e.g. `just telegram-webhook-dev https://…`.
telegram-webhook-dev url: (_require-env ".env.development")
    {{dev_env}} uv run python -m \
        personal_finance.contexts.alerts.presentation.cli.set_telegram_webhook \
        set {{url}}

# The same, for production.
telegram-webhook-prod url: (_require-env ".env.production")
    {{prod_env}} uv run python -m \
        personal_finance.contexts.alerts.presentation.cli.set_telegram_webhook \
        set {{url}}

# What Telegram thinks it is delivering to, and what went wrong last.
telegram-webhook-info-dev: (_require-env ".env.development")
    {{dev_env}} uv run python -m \
        personal_finance.contexts.alerts.presentation.cli.set_telegram_webhook info

telegram-webhook-info-prod: (_require-env ".env.production")
    {{prod_env}} uv run python -m \
        personal_finance.contexts.alerts.presentation.cli.set_telegram_webhook info

# Store a secret in SSM Parameter Store, so the env file only holds a
# reference. The value is read from a prompt, never from the command line: a
# command line is visible in `ps` to every process on the box, and lands in
# shell history.
# Usage: just secret-put /finflow/production/jwt-secret
#
# The env file is **deduced from the path**, because the path already says
# which environment it belongs to and asking for it twice only creates a way
# to disagree. It picks the AWS profile that writes and the file the printed
# reference has to be pasted into; crossed, both are wrong quietly — the
# write succeeds, and the reference lands in the env file of the *other*
# environment, which is how production ends up signing with development's
# secret. Passing it explicitly still works, for a path outside
# `/finflow/<environment>/`; the check below is only there to refuse one that
# contradicts the path.
secret-put name env_file=(if name =~ '^/finflow/development/' { ".env.development" } else { ".env.production" }): (_require-env env_file)
    @case '{{name}}' in \
        /finflow/development/*) owner=.env.development ;; \
        /finflow/production/*) owner=.env.production ;; \
        *) owner='{{env_file}}' ;; \
    esac; \
    if [ "$owner" != '{{env_file}}' ]; then \
        echo "{{name}} belongs to $owner, not {{env_file}}." >&2; \
        echo "run:  just secret-put {{name}} $owner" >&2; \
        exit 1; \
    fi
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

# `--host 0.0.0.0` matches `run-dev` and `run-prod`, which already bind it.
# The default 127.0.0.1 is reachable through the editor's port tunnel but not
# through a published Docker port, so the odd one out was the recipe most
# likely to be running when a browser cannot connect.
#
# Local: hot reload, against the emulator.
dev: (_require-env ".env")
    {{local_env}} uv run fastapi dev src/personal_finance/api/main.py \
        --host 0.0.0.0 --port 8000

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

# The six processes against the dev- resources in real AWS. Run
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

alerts-worker-dev: (_require-env ".env.development")
    {{dev_env}} uv run python -m \
        personal_finance.contexts.alerts.presentation.cli.run_alerts_worker

# --------------------------------------------------
# Deploying: the six functions, from infrastructure only
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

# Provisioning runs first, and is a dependency rather than a step somebody
# remembers: it owns the tables, and a deploy whose code queries an index the
# table does not have yet answers 500 to every user until it catches up. It is
# idempotent, so paying for it on every deploy costs a few describes.

# A Docker config of the deploys' own, with no credential helper in it.
#
# The Dev Containers extension points Docker at a helper it injects
# (`credsStore: dev-containers-<uuid>` in `~/.docker/config.json`) to forward
# the host's registry logins. That helper answers `get`, `store` and `erase`
# and **does not implement `list`** — and `list` is precisely what the Docker
# SDK calls before an image build, to collect the auth headers. The Docker CLI
# never takes that path, which is why `docker build` by hand works and
# `sam build` dies with `Credentials store ... exited with ""`, a StoreError
# from inside the SDK with no mention of the DevContainer anywhere in it.
#
# Nothing is lost by dropping the helper here: both base images are public
# (`public.ecr.aws/lambda/python` and `ghcr.io/astral-sh/uv`). A fixed
# directory rather than a temporary one, because `sam deploy` writes its own
# ECR login into it and reusing that login is the point.
docker_config := justfile_directory() / ".aws-sam" / "docker-config"

_docker-config:
    @mkdir -p {{docker_config}}
    @[ -f {{docker_config}}/config.json ] || echo '{}' > {{docker_config}}/config.json

# Build the image and deploy. Authenticate first, e.g. `aws sso login`.
deploy-dev: provision-dev _docker-config
    DOCKER_CONFIG={{docker_config}} sam build --config-env development \
        --config-file {{sam_config}} \
        --template {{sam_dir}}/template.yaml
    DOCKER_CONFIG={{docker_config}} sam deploy --config-env development \
        --config-file {{sam_config}}

deploy-prod: provision-prod _docker-config
    DOCKER_CONFIG={{docker_config}} sam build --config-env production \
        --config-file {{sam_config}} \
        --template {{sam_dir}}/template.yaml
    DOCKER_CONFIG={{docker_config}} sam deploy --config-env production \
        --config-file {{sam_config}}

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

# Who actually receives a dead-letter alarm.
#
# Asked of the topic, never of CloudFormation. An email subscription is
# created `PendingConfirmation` and only becomes real when somebody clicks the
# link; unconfirmed, AWS deletes it after three days — and the stack goes on
# reporting `AlertEmailSubscription` as `CREATE_COMPLETE` either way. That is
# why redeploying does not repair it and why this asks the other side.

# Who receives a dead-letter alarm in development, and whether they confirmed.
alerts-dev: (_alerts "finflow-dev-alerts" "finflow-dev")

# Who receives a dead-letter alarm in production, and whether they confirmed.
alerts-prod: (_alerts "finflow-alerts" "finflow-production")

_alerts topic profile:
    #!/usr/bin/env bash
    set -euo pipefail

    arn="arn:aws:sns:us-east-1:$(aws sts get-caller-identity --profile {{profile}} --query Account --output text):{{topic}}"
    # `list-subscriptions-by-topic` reports a pending one too, with the ARN
    # literally spelled `PendingConfirmation`, so the state is the column that
    # matters and not the row's presence.
    aws sns list-subscriptions-by-topic --profile {{profile}} --topic-arn "$arn" \
        --query 'Subscriptions[].[Protocol,Endpoint,SubscriptionArn]' --output text \
        | awk 'BEGIN { found = 0 } { found = 1; state = ($3 == "PendingConfirmation" ? "PENDING " : "confirmed") ; print state "  " $1 "  " $2 } END { if (!found) print "NOBODY is subscribed: an alarm on {{topic}} pages no one." }'

# Send a confirmation request to an address, then go and click the link in it.
# Nothing arrives until it is confirmed, and re-running for an address that is
# already confirmed is a no-op.

# Ask an address to receive development's alarms, e.g. `just alerts-subscribe-dev me@example.com`.
alerts-subscribe-dev email: (_alerts-subscribe "finflow-dev-alerts" "finflow-dev" email)

# Ask an address to receive production's alarms.
alerts-subscribe-prod email: (_alerts-subscribe "finflow-alerts" "finflow-production" email)

_alerts-subscribe topic profile email:
    #!/usr/bin/env bash
    set -euo pipefail

    arn="arn:aws:sns:us-east-1:$(aws sts get-caller-identity --profile {{profile}} --query Account --output text):{{topic}}"
    # `--notification-endpoint`, not `--endpoint`: the short one is a prefix of
    # the CLI's global `--endpoint-url` and is silently resolved to it.
    aws sns subscribe --profile {{profile}} --topic-arn "$arn" \
        --protocol email --notification-endpoint '{{email}}' --output text
    echo "Confirmation sent to {{email}}. Click the link, then run \`just alerts-{{ if profile == "finflow-production" { "prod" } else { "dev" } }}\`."

# Tail one function's logs, e.g. `just deploy-logs-prod FinancialFunction`.
deploy-logs-dev name="ApiFunction":
    sam logs --stack-name finflow-dev --name {{name}} \
        --profile finflow-dev --tail

deploy-logs-prod name="ApiFunction":
    sam logs --stack-name finflow --name {{name}} \
        --profile finflow-production --tail

# Every deploy pushes the image once per function — six copies of ~250 MB —
# into six repositories SAM creates and then never prunes, so ECR storage
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

# --------------------------------------------------
# The API contract, and the frontend that consumes it
# --------------------------------------------------
#
# `docs/openapi.json` is the seam between the two halves of this repo. The
# frontend's TypeScript types are generated from it, so an endpoint that
# changes without regenerating shows up as a dirty working tree (`openapi-check`)
# and then as a compile error in `web-check` — never as a broken screen.

frontend_dir := justfile_directory() / "frontend"

# The local-only bank webhook is deliberately left out of it: the frontend
# must never learn that route exists.
#
# Write the production-shaped OpenAPI document to docs/openapi.json.
openapi *args: (_require-env ".env")
    @{{local_env}} uv run python scripts/export_openapi.py {{args}}

# Fail if the committed contract is not what the code would produce.
openapi-check: (_require-env ".env")
    @{{local_env}} uv run python scripts/export_openapi.py --check

# Run this after touching any router or response model.
#
# Regenerate the frontend's TypeScript types from the contract.
web-types: openapi
    cd {{frontend_dir}} && npx openapi-typescript ../docs/openapi.json \
        -o src/api/schema.d.ts

web-install:
    cd {{frontend_dir}} && npm install

# The port is fixed at 5173 because that origin is already in
# API_CORS_ORIGINS; a port that drifts looks like a bug in the client. Needs
# the API up (`just dev`, or `just up` for the whole pipeline).
#
# Run the frontend dev server.
web:
    cd {{frontend_dir}} && npm run dev

# `vite build` runs in production mode, so it reads `.env.production` and
# ignores `.env.development`. Without one, VITE_API_BASE_URL is inlined as
# `undefined` and the bundle throws the moment it loads — a white screen that
# only whoever deploys it would discover.
#
# Build the frontend for deployment.
web-build: (_require-env "frontend/.env.production")
    cd {{frontend_dir}} && npm run build

# Cloudflare Pages holds the bundle while CloudFront is out of reach: this
# account cannot create a distribution until AWS Support verifies it, and the
# app cannot wait for a support case to answer. Direct upload, not the Git
# integration — what is published is the `dist/` built right here, from the
# working tree that was just checked, and Cloudflare never needs to reach the
# repository.
#
# Moving back to CloudFront later is this recipe going away again, plus the
# hosting resources returning to the template. Nothing else in the repo knows
# where the bundle lives.
pages_project := "finflow"

# Must match the production branch the Pages project was created with, or every
# upload lands as a preview: a subdomain of its own, per deploy, which can
# never be in the API's exact-match CORS list.
pages_branch := "master"

# Log in once per machine before the first run:
#   cd frontend && npx wrangler login
#
# `frontend/.env.production` is *generated* here from the stack's own ApiUrl
# rather than maintained by hand. The two halves have to agree on one address,
# and a value copied between them is the one that goes stale — as an app that
# loads, looks right, and fails every call against yesterday's URL.
#
# Publish the frontend to Cloudflare Pages, against production.
web-publish: (_web-publish "finflow" "finflow-production" pages_project)

# Publish the frontend to Cloudflare Pages, against the development stack.
web-publish-dev: (_web-publish "finflow-dev" "finflow-dev" (pages_project + "-dev"))

_web-publish stack profile project:
    #!/usr/bin/env bash
    set -euo pipefail

    api_url=$(aws cloudformation describe-stacks --stack-name '{{stack}}' \
        --profile '{{profile}}' --output text \
        --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue')

    # An absent output prints empty, a null one prints "None", and both mean
    # the same thing here: there is no API to point the bundle at. Building
    # anyway would inline that into the bundle and fail in a browser.
    if [ -z "${api_url:-}" ] || [ "${api_url}" = "None" ]; then
        echo "Stack {{stack}} publishes no ApiUrl — deploy it first." >&2
        exit 1
    fi

    # The Function URL comes back with a trailing slash and the client joins
    # paths onto it, which would otherwise produce `//financial/summary`.
    api_url="${api_url%/}"

    printf '%s\n' \
        "# Generated by \`just web-publish\`. Edits are overwritten on the next" \
        "# publish — the address belongs to the stack, not to this file." \
        "VITE_API_BASE_URL=${api_url}" \
        > {{frontend_dir}}/.env.production

    (cd {{frontend_dir}} && npm run build)

    # The project is created here rather than by `pages deploy`, which asks two
    # questions when it does not find one — and cannot ask them at all, because
    # the deploy's output is piped below and a pipe has no terminal. Creating it
    # explicitly also pins the production branch, which is the difference
    # between publishing and filing a preview nobody can reach.
    if ! (cd {{frontend_dir}} && npx wrangler pages project list 2>/dev/null) \
        | grep -qE '(^|[^-[:alnum:]]){{project}}([^-[:alnum:]]|$)'; then
        echo "creating Pages project {{project}} (production branch {{pages_branch}})"
        (cd {{frontend_dir}} && npx wrangler pages project create '{{project}}' \
            --production-branch '{{pages_branch}}')
    fi

    # The output is captured as well as shown, because the address the app
    # ends up on is in it and nowhere else — see below.
    log=$(mktemp)
    trap 'rm -f "$log"' EXIT

    # `--branch` has to name the project's production branch or Pages files
    # the upload as a preview, on a subdomain of its own per deploy, which can
    # never be in the API's exact-match CORS list.
    (cd {{frontend_dir}} && npx wrangler pages deploy dist \
        --project-name '{{project}}' --branch '{{pages_branch}}' \
        --commit-dirty=true) 2>&1 | tee "$log"

    # The subdomain is **not** the project name. `*.pages.dev` is globally
    # unique, so a name already taken anywhere gets a random suffix
    # (`finflow-dev` -> `finflow-dev-2tc`), and the guessed origin would then
    # name a stranger's site. Read it from what was just published instead:
    # every URL wrangler prints is `<deploy-or-branch>.<subdomain>.pages.dev`,
    # so the last three labels are the production origin.
    host=$(grep -oE 'https://[A-Za-z0-9.-]+\.pages\.dev' "$log" | tail -n 1 \
        | sed 's|https://||' | awk -F. '{print $(NF-2)"."$(NF-1)"."$NF}')
    origin="https://${host:-{{project}}.pages.dev}"

    echo
    echo "published at ${origin}"
    if ! grep -q "${origin}" {{justfile_directory()}}/infra/samconfig.toml; then
        echo
        echo "warning: ${origin} is not in CorsOrigins in infra/samconfig.toml."
        echo "Every call from it fails the preflight until it is, and the API"
        echo "is redeployed. A custom domain counts as a different origin."
    fi

# Kept out of `just prepare` on purpose: that recipe is the fast Python loop,
# and waiting on Node to find out that a Python test failed is the wrong trade.
#
# There is no browser in the DevContainer's editor, so a screen that only
# type-checks has never been *seen*. This drives a headless Chromium over the
# running app and writes PNGs — phone-sized by default, because that is what
# Finflow is.
#
# It photographs what is already up rather than starting anything: booting the
# stack from the script would be a second copy of `run_stack.py` and a process
# it could leave behind if it died mid-shot. So `just up` in one terminal and
# `just web` in another, then this. With no arguments it walks the main
# screens; `--desktop`, `--full` and `--onboarding` are the flags worth
# knowing. Output lands in `frontend/.screenshots`, which git ignores.
#
# Screenshot the running frontend, e.g. `just shot /reportes --full`.
shot *args:
    cd {{frontend_dir}} && node scripts/shot.mjs {{args}}

# Drive the bills screen in a real browser and check the data behind it.
# Needs the stack up (`just up`) and the frontend (`just web`), like `just shot`.
# Two halves: every step goes through the rendered page, and after each one the
# server is asked directly and the two answers are compared. It also reads the
# balances before and after and refuses to pass if declaring a bill moved one.
e2e-bills *args:
    cd {{frontend_dir}} && node scripts/e2e-bills.mjs {{args}}

# The dashboard's allowance, in a real browser and against the real stack.
# Declares a month, then checks the figure is exactly what `/summary` and
# `/bills` add up to — never what `/allowance` says its own parts are — and
# that confirming a bill moves it from "owed" to "spent" without moving the
# total. Needs `just up` and `just web`.
#
# Drive the allowance card and check its arithmetic.
e2e-allowance *args:
    cd {{frontend_dir}} && node scripts/e2e-allowance.mjs {{args}}

# The budgets screen, in a real browser and against the real stack.
# Declares a budget from the page, and reads every balance, the net worth and
# the ledger's row count before and after to refuse to pass if doing so moved a
# peso. Then checks the traffic light against `/summary` rather than against
# the budgets endpoint's own figures, that a budget for one month is read
# *beside* the recurring one rather than instead of it — there is no shadowing
# any more — and that one over every category counts every category.
# Needs `just up` and `just web`.
e2e-budgets *args:
    cd {{frontend_dir}} && node scripts/e2e-budgets.mjs {{args}}

# The three browser suites, in order.
e2e: e2e-bills e2e-allowance e2e-budgets

# Format check, lint and typecheck the frontend.
web-check:
    cd {{frontend_dir}} && npm run check

web-fix:
    cd {{frontend_dir}} && npm run fix

# `npm audit` for vulnerabilities, plus the import-aware deprecation check
# that `web-check` already runs. The `frontend-auditor` subagent does the
# reading around these; this is the part that is just a command.
#
# Audit the frontend's dependencies.
web-audit:
    cd {{frontend_dir}} && npm audit && npm run check:deprecated

# Both halves, for a commit that touches the seam.
check-all: prepare openapi-check web-check

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
