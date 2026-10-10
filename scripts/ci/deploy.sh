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

# Absolute, like `sam_config` in the justfile and for the same reason: `sam
# build` resolves a relative `--config-file` against the template's directory
# and `sam deploy` does not, so `infra/samconfig.toml` sent build looking in
# infra/infra/. Taken after the `cd`, so a rollback reads the older tree's own.
sam_config="$(pwd)/infra/samconfig.toml"

# Pull every external image the Dockerfile names, once and in series, before
# the build. `sam build` builds the seven functions in parallel, and on a fresh
# runner each build pulled the same three images itself: ~20 anonymous pulls at
# once from a shared IP, which public.ecr.aws answers with `toomanyrequests`
# (2026-10-10). With them local, the builder uses them and pulls nothing.
# Stage names (`builder`) have no `/` or `:`, which is what tells them apart.
images="$(grep -oE '^FROM [^ ]+|--from=[^ ]+' Dockerfile \
  | sed -E 's/^(FROM |--from=)//' | grep -E '[/:]' | sort -u)"
for image in $images; do
  for attempt in 1 2 3 4; do
    DOCKER_CONFIG="$docker_config" docker pull --quiet "$image" && break
    [ "$attempt" -eq 4 ] && exit 1
    echo "pull de $image rechazado; reintento en $((attempt * 15)) s" >&2
    sleep $((attempt * 15))
  done
done

DOCKER_CONFIG="$docker_config" sam build --config-env "$config" \
  --config-file "$sam_config" --template infra/template.yaml
DOCKER_CONFIG="$docker_config" sam deploy --config-env "$config" \
  --config-file "$sam_config" \
  --no-confirm-changeset --no-fail-on-empty-changeset

just "$web"
