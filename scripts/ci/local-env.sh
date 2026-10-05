#!/usr/bin/env bash
# The local settings a fresh runner needs: `.env.example` as it is, plus the
# values it deliberately leaves empty and without which the local stack does
# not boot. Nothing here reaches a real service — the stack runs against the
# in-process emulator — and the secret is made per run rather than committed.
# An existing `.env` is left alone.
set -euo pipefail

[ -f .env ] && exit 0

cp .env.example .env

# Replaced in place rather than appended: a second `KEY=` line would leave
# which one wins up to the dotenv parser. Fails loudly if the example stopped
# carrying the empty line, instead of starting a stack that cannot boot.
fill() {
  local key="$1" value="$2"
  sed -i "s|^${key}=\$|${key}=${value}|" .env
  grep -qx "${key}=${value}" .env || {
    echo "no encontré ${key}= vacío en .env.example" >&2
    exit 1
  }
}

# The API refuses to boot without a token-signing secret.
fill IDENTITY_JWT_SECRET "$(openssl rand -hex 32)"
# Registering derives each user's alias from this address. The IMAP worker is
# not started by `just up`, so nothing ever polls it: it only has to be
# well-formed, and `.local` cannot belong to anybody.
fill INGESTION_INGEST_MAILBOX_ADDRESS "ingest@finflow.local"
