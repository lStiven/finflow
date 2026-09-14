"""What Telegram posts to the webhook, and the little of it we believe.

Everything here except the secret header is written by whoever is talking to
the bot, so this is a boundary in the strict sense: a model with `extra`
ignored, every field optional, every length capped, and exactly three values
read out of it — the chat, the text, and a display name.

Two refusals are worth knowing about:

* **The chat must be private.** Add the bot to a group, press Start, and
  without this every purchase its owner makes would be broadcast to everyone
  in that group. Nothing else in the design prevents it.
* **The identity is the token, never `from.id`.** The chat id is an address
  to write to. Reading the owner off anything the caller supplied would let
  whoever holds a link decide whose account it binds.
"""

from __future__ import annotations

import dataclasses
import re

from pydantic import BaseModel, ConfigDict, Field

from personal_finance.contexts.alerts.infrastructure.telegram.deep_link import (
    LINK_PAYLOAD,
)


# Telegram caps a message at 4096 characters. Saying so here means the model
# refuses a forged body long before any of it is looked at.
MAX_TEXT_LENGTH = 4096
MAX_NAME_LENGTH = 256

PRIVATE_CHAT = "private"

# Int64, which is what a Telegram id is. Never `int()` over an unbounded
# string: the conversion itself is the denial of service.
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1

# `/start <payload>`, or `/start@thebot <payload>` as a group would send it —
# parsed even though a group is refused later, so that the refusal is the
# reason it stops rather than a parse failure that looks like a bad link.
_START = re.compile(r"^/start(?:@[A-Za-z0-9_]{1,32})?\s+(\S+)$")


class TelegramChat(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: int = Field(ge=_INT64_MIN, le=_INT64_MAX)
    type: str = Field(default="", max_length=32)


class TelegramUser(BaseModel):
    model_config = ConfigDict(extra="ignore")

    first_name: str | None = Field(default=None, max_length=MAX_NAME_LENGTH)


class TelegramMessage(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    chat: TelegramChat | None = None
    text: str | None = Field(default=None, max_length=MAX_TEXT_LENGTH)
    # `from` is a keyword, so the field is named for what it holds.
    sender: TelegramUser | None = Field(default=None, alias="from")


class TelegramUpdate(BaseModel):
    """One update. Only `message` is read; edits and posts are not linking."""

    model_config = ConfigDict(extra="ignore")

    message: TelegramMessage | None = None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class StartCommand:
    """Somebody pressed Start on a deep link, in a chat we may write to."""

    chat_id: str
    token: str
    label: str | None


def extract_start_command(update: TelegramUpdate) -> StartCommand | None:
    """The linking attempt inside an update, or None for everything else.

    None is the ordinary answer: a plain message, an edit, a group, a
    malformed payload. The caller answers all of them the same way, which is
    why they are not told apart here.
    """
    message = update.message

    if message is None or message.chat is None or message.text is None:
        return None

    if message.chat.type != PRIVATE_CHAT:
        # A group. One person's purchases, to everyone in the room.
        return None

    match = _START.match(message.text.strip())

    if match is None:
        return None

    token = match.group(1)

    # Checked before it is hashed, so a token field can never be a carrier
    # for anything but Telegram's own deep-link alphabet.
    if not LINK_PAYLOAD.fullmatch(token):
        return None

    return StartCommand(
        chat_id=str(message.chat.id),
        token=token,
        label=message.sender.first_name if message.sender else None,
    )
