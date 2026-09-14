# syntax=docker/dockerfile:1

# One image for all six functions.
#
# Which one a container becomes is decided by the entry point the SAM template
# gives it, never by anything baked in here: the four workers run Lambda's
# runtime client against their handler, and the API runs the same uvicorn
# command as `just run-prod` behind the Lambda Web Adapter. Six images would
# be six things to keep in step, and the drift would only show in production.
#
# Both stages sit on Lambda's own base image on purpose. `bcrypt` is a
# compiled extension, so wheels built against another distribution's glibc can
# import here and fail at runtime — the one failure mode a Dockerfile is
# supposed to remove.

FROM public.ecr.aws/lambda/python:3.13 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /build

# The lockfile alone, so a source edit does not reinstall every dependency.
COPY pyproject.toml uv.lock ./

# `--frozen` fails rather than silently relocking: the image must contain the
# versions that were tested, not whatever resolves today.
RUN uv export --frozen --no-dev --no-emit-project --format requirements.txt \
        -o requirements.txt \
    && uv pip install --python "$(command -v python)" --target /deps \
        -r requirements.txt


FROM public.ecr.aws/lambda/python:3.13

# The adapter is a Lambda extension, not a Python package: it registers with
# the Runtime API and forwards each invocation to whatever HTTP server the
# container is running. It is inert for the four worker functions, which never
# start one.
COPY --from=public.ecr.aws/awsguru/aws-lambda-adapter:0.9.1 \
     /lambda-adapter /opt/extensions/lambda-adapter

# Where the API listens, and the endpoint the adapter waits for before
# reporting the container ready. `/health` already exists and touches nothing,
# which is what makes it usable as a readiness probe.
ENV AWS_LWA_PORT=8000 \
    AWS_LWA_READINESS_CHECK_PATH=/health

COPY --from=builder /deps ${LAMBDA_TASK_ROOT}
COPY src/personal_finance ${LAMBDA_TASK_ROOT}/personal_finance

# A default so the image is runnable on its own; every function overrides it.
CMD ["personal_finance.contexts.ingestion.presentation.awslambda.parse_handler.handler"]
