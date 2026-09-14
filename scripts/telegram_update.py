"""Pretend to be Telegram, so linking can be exercised on a laptop.

    just telegram-update <token>

Telegram cannot reach `localhost`, and `setWebhook` insists on a public HTTPS
name, so the one part of the flow the network will not allow locally is the
*inbound* half. This supplies it: a well-formed private-chat `/start <token>`
update, posted to the local webhook with the secret header the real Telegram
would have echoed.

Everything else stays real. The same route, the same parser, the same
conditional writes, and — if a bot token is configured — a genuine Telegram
message arriving on a real phone. Only the knock on the door is simulated.

A script rather than a `curl` in the recipe, for the reason the justfile
already gives elsewhere: a value hardcoded in a recipe silently overrides the
env file, and this way the secret and the base URL are read through the
application's own settings.
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

from personal_finance.contexts.alerts.presentation.http.telegram import SECRET_HEADER
from personal_finance.shared.infrastructure.config.settings import get_alerts_settings


DEFAULT_BASE_URL = "http://localhost:8000"
WEBHOOK_PATH = "/alerts/telegram/webhook"

# A fixed chat, so re-running binds the same destination rather than filling
# the table with imaginary people. Any digits would do.
DEFAULT_CHAT_ID = 987654321


def _update(
    *,
    token: str,
    chat_id: int,
    chat_type: str,
    first_name: str,
) -> dict[str, object]:
    return {
        "update_id": 1,
        "message": {
            "message_id": 1,
            "date": 0,
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": chat_id, "is_bot": False, "first_name": first_name},
            "text": f"/start {token}",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "token",
        help=(
            "The `start=` payload from the link_url that "
            "POST /alerts/channels returned."
        ),
    )
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--chat-id", type=int, default=DEFAULT_CHAT_ID)
    parser.add_argument("--first-name", default="Local")
    parser.add_argument(
        "--chat-type",
        default="private",
        help="Use `group` to check that a group chat is refused.",
    )
    parser.add_argument(
        "--secret",
        default=None,
        help="Override the header. Use a wrong one to check it is refused.",
    )
    arguments = parser.parse_args()

    secret = arguments.secret
    if secret is None:
        secret = get_alerts_settings().telegram_webhook_secret.get_secret_value()

    if not secret:
        print(
            "ALERTS_TELEGRAM_WEBHOOK_SECRET is not set. It is required in "
            "every environment, including this one: an empty expected value "
            "would match an empty header and leave the webhook open.",
            file=sys.stderr,
        )

        return 1

    response = httpx.post(
        f"{arguments.base_url.rstrip('/')}{WEBHOOK_PATH}",
        json=_update(
            token=arguments.token,
            chat_id=arguments.chat_id,
            chat_type=arguments.chat_type,
            first_name=arguments.first_name,
        ),
        headers={SECRET_HEADER: secret},
        timeout=15.0,
    )

    print(f"{response.status_code} {json.dumps(response.json())}")
    print(
        "\nThe webhook answers 200 to everything it has an answer for — an "
        "unknown token included — so check GET /alerts/channels to see "
        "whether the channel actually bound.",
    )

    return 0 if response.status_code == 200 else 1


if __name__ == "__main__":
    raise SystemExit(main())
