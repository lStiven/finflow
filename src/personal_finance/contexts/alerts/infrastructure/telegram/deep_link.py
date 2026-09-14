"""The URL that turns a tap into a bound channel.

Telegram's `?start=<payload>` is handed back to the bot verbatim the moment
somebody presses Start, which is the entire mechanism: the token never
touches a form, and the person never has to find out what a chat id is.

The payload alphabet Telegram accepts is exactly what `token_urlsafe`
produces, so nothing here escapes or encodes anything — and a token that
would have needed escaping is a token this refuses to build a link for.
"""

from __future__ import annotations

import re


TELEGRAM_BASE_URL = "https://t.me"

# Telegram's own deep-link payload alphabet, and the one `token_urlsafe`
# draws from. Checked on the way out as well as on the way in, so a
# generator swapped for a sloppier one fails here rather than silently
# producing links that lose characters in transit.
LINK_PAYLOAD = re.compile(r"[A-Za-z0-9_-]{1,64}")

_BOT_USERNAME = re.compile(r"[A-Za-z0-9_]{5,32}")


def build_deep_link(*, bot_username: str, token: str) -> str:
    username = bot_username.removeprefix("@")

    if not _BOT_USERNAME.fullmatch(username):
        raise ValueError(f"Not a Telegram bot username: {bot_username!r}")

    if not LINK_PAYLOAD.fullmatch(token):
        raise ValueError("Link token is not usable as a deep-link payload")

    return f"{TELEGRAM_BASE_URL}/{username}?start={token}"
