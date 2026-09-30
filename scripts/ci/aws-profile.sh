#!/usr/bin/env bash
# Give the temporary credentials GitHub's OIDC role handed out a profile name.
#
#   scripts/ci/aws-profile.sh finflow-production
#
# Every recipe here reads a named profile — `samconfig.toml`, the `.env.*`
# files through AWS_PROFILE, `web-publish` — because that is what keeps a dev
# command from ever reading production's account by default. CI keeps that
# property instead of teaching each recipe a second way to authenticate.
set -euo pipefail

profile="${1:?uso: $0 <perfil>}"

aws configure set aws_access_key_id "$AWS_ACCESS_KEY_ID" --profile "$profile"
aws configure set aws_secret_access_key "$AWS_SECRET_ACCESS_KEY" --profile "$profile"
aws configure set aws_session_token "$AWS_SESSION_TOKEN" --profile "$profile"
aws configure set region "${AWS_REGION:-us-east-1}" --profile "$profile"

echo "perfil $profile listo: $(aws sts get-caller-identity --profile "$profile" --query Arn --output text)"
