"""Tell Telegram where to deliver updates, and with what secret.

    just telegram-webhook-dev https://<api>/alerts/telegram/webhook
    just telegram-webhook-info-dev
    just telegram-webhook-delete-dev

Without this, nothing in the repository explains how the two sides ever come
to agree on `X-Telegram-Bot-Api-Secret-Token` — and that agreement is the
whole of the webhook's authentication.

Registration is a one-off per deployment, not part of a deploy: the URL only
changes when the Function URL does. It lives as a command rather than as
something the app does at startup because a stack that re-registers on every
boot would have two environments fighting over one bot, and the loser would
silently stop receiving updates.

Telegram requires HTTPS and will not accept a private address, which is why a
laptop uses `just telegram-update` instead.
"""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import sys
from typing import cast

import httpx

from personal_finance.shared.infrastructure.config.settings import get_alerts_settings


TIMEOUT_SECONDS = 15.0


def _call(method: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    settings = get_alerts_settings()
    token = settings.telegram_bot_token.get_secret_value()

    if not token:
        raise SystemExit(
            "ALERTS_TELEGRAM_BOT_TOKEN is not set. Create a bot with "
            "@BotFather and store the token with `just secret-put`.",
        )

    # The token is in the path, so nothing here prints the URL — not on
    # success and not in an error. See the sender for why that matters.
    with httpx.Client(
        base_url=settings.telegram_api_base_url,
        timeout=TIMEOUT_SECONDS,
    ) as client:
        try:
            response = client.post(f"/bot{token}/{method}", json=payload or {})
        except httpx.HTTPError as error:
            raise SystemExit(
                f"Telegram could not be reached ({type(error).__name__})",
            ) from None

    if response.status_code >= 400:
        raise SystemExit(f"Telegram answered {response.status_code} to {method}")

    body: dict[str, object] = response.json()

    return body


def _set(url: str) -> int:
    settings = get_alerts_settings()
    secret = settings.telegram_webhook_secret.get_secret_value()

    if not secret:
        print(
            "ALERTS_TELEGRAM_WEBHOOK_SECRET is not set. It is the only thing "
            "authenticating the webhook, so registering without one would "
            "publish an endpoint that binds channels for anybody who finds "
            "it. Generate one and store it with `just secret-put`.",
            file=sys.stderr,
        )

        return 1

    if not url.startswith("https://"):
        print("Telegram only delivers to https.", file=sys.stderr)

        return 1

    _call(
        "setWebhook",
        {
            "url": url,
            "secret_token": secret,
            # Only what linking needs. Telegram sends nothing else, so
            # nothing else can arrive to be parsed.
            "allowed_updates": ["message"],
            # Anything queued from a previous registration is for a webhook
            # that no longer exists and a secret that no longer matches.
            "drop_pending_updates": True,
        },
    )
    print(f"Webhook registered at {url}")

    return 0


def _info() -> int:
    result = _call("getWebhookInfo").get("result")

    if not isinstance(result, Mapping):
        print("Telegram answered something unreadable.", file=sys.stderr)

        return 1

    info: Mapping[str, object] = cast("Mapping[str, object]", result)

    for label, key in (
        ("url", "url"),
        ("pending updates", "pending_update_count"),
        ("last error", "last_error_message"),
        ("allowed updates", "allowed_updates"),
    ):
        value = info.get(key)
        # The secret itself is never echoed back by Telegram, and this would
        # not print it if it were.
        print(f"  {label:<16}: {value if value not in (None, '') else '(none)'}")

    return 0


def _delete() -> int:
    _call("deleteWebhook", {"drop_pending_updates": True})
    print("Webhook removed. Nothing will link until one is registered again.")

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    setter = subparsers.add_parser("set", help="Register the webhook URL.")
    setter.add_argument("url", help="The https address of /alerts/telegram/webhook")
    subparsers.add_parser("info", help="What Telegram thinks it is delivering to.")
    subparsers.add_parser("delete", help="Stop delivering. Linking stops with it.")

    arguments = parser.parse_args()

    if arguments.command == "set":
        return _set(arguments.url)

    if arguments.command == "info":
        return _info()

    return _delete()


if __name__ == "__main__":
    raise SystemExit(main())
