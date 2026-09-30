"""Wiring both alerts routers share.

It lives apart from either of them because two modules need it — the
authenticated router and the webhook — and a builder imported out of one
router into the other reads as an accident rather than as the arrangement it
is.
"""

from __future__ import annotations

import functools

from personal_finance.contexts.alerts.application.ports import (
    MessageSender,
    SummarySender,
)
from personal_finance.contexts.alerts.infrastructure.persistence.dynamodb import (
    DynamoDBAlertChannelRepository,
    DynamoDBChannelLinkRepository,
)
from personal_finance.contexts.alerts.infrastructure.telegram.client import (
    LoggingMessageSender,
    TelegramMessageSender,
)
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import (
    get_alerts_settings,
    get_aws_settings,
)


@functools.lru_cache(maxsize=1)
def build_channel_repository() -> DynamoDBAlertChannelRepository:
    return DynamoDBAlertChannelRepository(
        client=get_dynamodb_client(),
        table_name=get_alerts_settings().channels_table,
    )


@functools.lru_cache(maxsize=1)
def build_link_repository() -> DynamoDBChannelLinkRepository:
    return DynamoDBChannelLinkRepository(
        client=get_dynamodb_client(),
        table_name=get_alerts_settings().channels_table,
    )


def alerts_are_configured() -> bool:
    """Whether this deployment could send an alert if it had one to send.

    Asked instead of catching the failure below, so that a deployment with no
    bot answers "not available" on the alerts endpoints and serves everything
    else normally. Alerts are additive: nobody should lose the ability to log
    in or read their movements because a Telegram token is missing.
    """
    settings = get_alerts_settings()

    return bool(settings.telegram_bot_token.get_secret_value()) or (
        get_aws_settings().is_local
    )


@functools.lru_cache(maxsize=1)
def build_message_sender() -> MessageSender:
    return _build_sender()


@functools.lru_cache(maxsize=1)
def build_summary_sender() -> SummarySender:
    """The same transport, seen through the port Monday's job needs."""
    return _build_sender()


def _build_sender() -> TelegramMessageSender | LoggingMessageSender:
    """The transport this deployment sends through, or a refusal to start.

    Raises where no bot is configured, which the *worker* treats as a reason
    not to start: a process whose only job is sending has nothing to do
    without a transport. The API does not call this until an alerts endpoint
    needs it, and asks `alerts_are_configured` first — see there for why a
    missing token must not take down a whole deployment.

    The degraded alternative this still refuses is a channel bound to a
    destination nothing can ever reach, whose owner finds out at their first
    purchase rather than while they are looking at Telegram waiting.

    The local branch is derived from `ENVIRONMENT` and nothing else. A switch
    of its own would be a switch somebody could set in production, and there
    it would silently stop every alert.
    """
    settings = get_alerts_settings()

    if settings.telegram_bot_token.get_secret_value():
        return TelegramMessageSender(
            bot_token=settings.telegram_bot_token,
            api_base_url=settings.telegram_api_base_url,
            timeout_seconds=settings.send_timeout_seconds,
            display_timezone=settings.display_timezone,
        )

    if get_aws_settings().is_local:
        return LoggingMessageSender(display_timezone=settings.display_timezone)

    raise ValueError(
        "ALERTS_TELEGRAM_BOT_TOKEN is not set: this deployment could not "
        "send an alert to anybody, so linking a channel would bind a "
        "destination it can never reach. See docs/deploy.md.",
    )
