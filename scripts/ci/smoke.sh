#!/usr/bin/env bash
# Drive the API that is deployed right now, over HTTPS, and fail if it is not
# answering the way it should.
#
#   scripts/ci/smoke.sh production
#
# Production gets `smoke-prod`, which only reads: every write there would leave
# behind a user no endpoint can delete. Development gets the full smoke.
set -euo pipefail

environment="${1:?uso: $0 development|production}"

case "$environment" in
  development) stack=finflow-dev profile=finflow-dev recipe=smoke ;;
  production) stack=finflow profile=finflow-production recipe=smoke-prod ;;
  *) echo "entorno desconocido: $environment" >&2; exit 2 ;;
esac

api_url="$(aws cloudformation describe-stacks --stack-name "$stack" \
  --profile "$profile" --output text \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue')"

if [ -z "$api_url" ] || [ "$api_url" = "None" ]; then
  echo "la pila $stack no publica ApiUrl" >&2
  exit 1
fi

just "$recipe" "${api_url%/}"
