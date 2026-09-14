"""The webhook Telegram posts to when somebody presses Start.

Kept apart from the authenticated router on purpose: the caller here is not a
user of this system and carries no bearer token, and a route with that story
sitting among routes with the other one is how a dependency ends up quietly
missing from the wrong endpoint.

**This is a product path, not a local seam.** Unlike
`/ingestion/bank-notifications`, which exists only so a developer can replay
an email, this is how every channel in every environment gets bound. It is
mounted everywhere, and `tests/unit/api/test_app_wiring.py` says so — because
the ingestion precedent invites exactly the opposite assumption.

What protects it:

* A shared secret in `X-Telegram-Bot-Api-Secret-Token`, compared in constant
  time, checked **before the body is parsed**. It is the only authentication
  a Lambda Function URL can have — `AuthType: NONE` has no resource policy,
  so Telegram's address ranges cannot be pinned — and checking it first means
  an anonymous flood costs one comparison rather than a parse and a read.
* A body that is validated, capped, and read for exactly three values.
* A token that is single-use and minutes old, and that decides *by itself*
  whose account is being bound. Nothing the caller says about identity is
  believed.

And it answers `200` to everything it has an answer for, which is explained
at `_accepted`.
"""

from __future__ import annotations

import hmac
import logging
from typing import TYPE_CHECKING, Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ValidationError

from personal_finance.contexts.alerts.application.commands import (
    RedeemChannelLinkCommand,
)
from personal_finance.contexts.alerts.application.handlers import (
    RedeemChannelLinkUseCase,
)
from personal_finance.contexts.alerts.application.ports import (
    DestinationRefusedError,
    TransportUnavailableError,
)
from personal_finance.contexts.alerts.domain.exceptions import (
    ChannelAlreadyVerifiedError,
    ChatAlreadyLinkedError,
    InvalidLinkTokenError,
)
from personal_finance.contexts.alerts.domain.value_objects import ChatId
from personal_finance.contexts.alerts.infrastructure.events import (
    build_alerts_event_publisher,
)
from personal_finance.contexts.alerts.infrastructure.security.secret_hashing import (
    Sha256TokenHasher,
)
from personal_finance.contexts.alerts.infrastructure.telegram.updates import (
    TelegramUpdate,
    extract_start_command,
)
from personal_finance.contexts.alerts.presentation.http.dependencies import (
    build_channel_repository,
    build_link_repository,
    build_message_sender,
)
from personal_finance.shared.infrastructure.config.settings import get_alerts_settings


if TYPE_CHECKING:
    from personal_finance.contexts.alerts.domain.entities import AlertChannel


_logger = logging.getLogger(__name__)

router = APIRouter(prefix="/alerts/telegram", tags=["alerts"])

SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"

# Telegram caps a message at 4096 characters and an update is a little
# JSON around one. Anything of this size is not Telegram, and refusing to
# parse it is the only bound available here: a Lambda Function URL has no
# request-size control of its own.
MAX_BODY_BYTES = 64 * 1024


class WebhookAck(BaseModel):
    """What Telegram gets back. Always the same thing — see `_accepted`."""

    ok: bool = True


def _build_redeem_use_case() -> RedeemChannelLinkUseCase:
    return RedeemChannelLinkUseCase(
        channels=build_channel_repository(),
        links=build_link_repository(),
        hasher=Sha256TokenHasher(),
        sender=build_message_sender(),
        publisher=build_alerts_event_publisher(),
    )


def get_redeem_use_case() -> RedeemChannelLinkUseCase:
    return _build_redeem_use_case()


def verify_telegram_secret(
    secret_token: Annotated[str | None, Header(alias=SECRET_HEADER)] = None,
) -> None:
    """The only thing standing between the internet and this endpoint.

    `hmac.compare_digest`, never `==`: a string comparison returns early on
    the first differing byte, and the timing of that is enough to recover a
    secret one character at a time.

    A missing header and a wrong one are the same answer. `403` rather than
    `401`, because there is no credential to prompt a browser for — and
    because a real misconfiguration should be loud rather than look like an
    ordinary rejection.
    """
    expected = get_alerts_settings().telegram_webhook_secret.get_secret_value()

    if not expected:
        # Refused rather than compared. `compare_digest("", "")` is True, so
        # an unset secret would not weaken the door — it would remove it, and
        # anybody who found the URL could bind channels. Nothing legitimate
        # reaches this endpoint without a secret, so nothing is lost by
        # answering the same way to everyone until one is configured.
        _logger.error(
            "ALERTS_TELEGRAM_WEBHOOK_SECRET is not set; refusing every "
            "telegram webhook call until it is",
        )

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )

    if not hmac.compare_digest(secret_token or "", expected):
        # Without the value, either of them.
        _logger.warning("rejected a telegram webhook call with a bad secret")

        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden",
        )


async def raw_body(request: Request) -> bytes:
    """The body as it arrived, unparsed.

    A typed body parameter would be decoded and validated as part of solving
    the request, which puts a JSON parse and a Pydantic pass in front of the
    secret check — free work for anyone who finds the URL. This hands the
    endpoint bytes and lets it decide when to spend anything on them.
    """
    return await request.body()


def _accepted() -> WebhookAck:
    """Why almost everything here answers 200.

    Telegram retries any non-2xx with backoff and, on sustained failure,
    disables the webhook and queues updates — so answering "no" to a body we
    simply do not care about would turn ordinary traffic into a retry storm
    and eventually take linking down for everybody.

    It is also what keeps the endpoint uninformative: an unknown token, a
    spent one and an expired one are indistinguishable from outside, so
    nothing here can be used to find out whether a link exists.

    A genuine infrastructure failure is the exception — it is allowed to
    raise into a 5xx so that Telegram retries and the user's tap is not
    lost. That is safe because redeeming is idempotent: the second attempt
    finds no token and answers 200 like everything else.
    """
    return WebhookAck()


@router.post(
    "/webhook",
    response_model=WebhookAck,
    dependencies=[Depends(verify_telegram_secret)],
)
def receive_update(
    body: Annotated[bytes, Depends(raw_body)],
    use_case: Annotated[RedeemChannelLinkUseCase, Depends(get_redeem_use_case)],
) -> WebhookAck:
    """Bind a channel, if this update is somebody following their own link.

    The body arrives as raw bytes and is decoded here rather than in the
    signature, for two reasons. FastAPI validates a typed body as part of
    solving the request, which would put a JSON parse *before* the secret
    check and hand anonymous callers free work; and a shape Telegram changed
    would become a 422, which is a non-2xx, which is backoff and eventually a
    disabled webhook.
    """
    if len(body) > MAX_BODY_BYTES:
        _logger.warning("ignoring an oversized telegram update")

        return _accepted()

    try:
        update = TelegramUpdate.model_validate_json(body)
    except ValidationError:
        # Not logged with the body: it is a stranger's message, and a
        # `/start` in it would be a live token.
        _logger.info("ignoring a telegram update this cannot read")

        return _accepted()

    command = extract_start_command(update)

    if command is None:
        # An ordinary message, an edit, or a group chat — a group is refused
        # because linking one would broadcast a person's purchases to a room.
        return _accepted()

    try:
        channel = use_case.execute(
            RedeemChannelLinkCommand(
                token=command.token,
                chat_id=ChatId(command.chat_id),
                label=command.label,
            ),
        )
    except InvalidLinkTokenError:
        _logger.info("a telegram link was not valid")

        return _accepted()
    except ChatAlreadyLinkedError:
        _logger.info("a telegram chat is already linked to another account")

        return _accepted()
    except ChannelAlreadyVerifiedError:
        _logger.info("a telegram channel was already linked")

        return _accepted()
    except DestinationRefusedError:
        # The channel is bound; only the hello could not be delivered. That
        # is not a reason to make Telegram retry a binding that succeeded.
        _logger.warning("a newly linked destination refused the confirmation")

        return _accepted()
    except TransportUnavailableError:
        # Same: the binding is already written and committed.
        _logger.warning("could not confirm a new link; telegram is unavailable")

        return _accepted()

    _log_linked(channel)

    return _accepted()


def _log_linked(channel: AlertChannel) -> None:
    _logger.info(
        "alert channel linked",
        # Ids only. Never the chat id in full, never the token, never the
        # name the owner chose for themselves in Telegram.
        extra={
            "channel_id": str(channel.id.value),
            "kind": channel.kind.value,
        },
    )
