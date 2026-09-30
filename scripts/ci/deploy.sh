#!/usr/bin/env bash
# Deploy one environment — backend and web, in that order and together — from
# a working tree.
#
#   scripts/ci/deploy.sh production            # the tree you are in
#   scripts/ci/deploy.sh production ../older   # another tree: a rollback
#
# The same steps as `just deploy-*` and `just web-publish*`, with the one
# difference a pipeline needs: nobody is at the terminal to confirm the
# changeset, so it is not asked. The tree is an argument so that rolling back
# is this same script run over the previous release rather than a second path
# that is only ever exercised on the worst day.
#
# Backend and web never go out apart: a web built against an API that is not
# there yet breaks the screen, which is what happened on 2026-09-01.
set -euo pipefail

environment="${1:?uso: $0 development|production [árbol]}"
tree="${2:-.}"

case "$environment" in
  development) config=development provision=provision-dev web=web-publish-dev ;;
  production) config=production provision=provision-prod web=web-publish ;;
  *) echo "entorno desconocido: $environment" >&2; exit 2 ;;
esac

cd "$tree"

# Resources first: the code about to go out may read a table, an index or a
# queue attribute this deploy is the first to need.
just "$provision"

just _docker-config
docker_config="$(just --evaluate docker_config)"

DOCKER_CONFIG="$docker_config" sam build --config-env "$config" \
  --config-file infra/samconfig.toml --template infra/template.yaml
DOCKER_CONFIG="$docker_config" sam deploy --config-env "$config" \
  --config-file infra/samconfig.toml \
  --no-confirm-changeset --no-fail-on-empty-changeset

just "$web"
