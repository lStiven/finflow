"""Finishing an OAuth authorization.

This is the seam the whole Gmail integration hangs from: everything before it
is a browser redirect, and everything after it is the ordinary connection
lifecycle. It exists as a use case rather than as router code so the rules —
whose mailbox it is, that the address must be the one Google confirmed, that
the subscription starts immediately — are testable without HTTP.
"""

from __future__ import annotations

import dataclasses
import logging
from typing import Protocol

from personal_finance.contexts.ingestion.application.connection_handlers import (
    ConnectMailboxCommand,
    ConnectMailboxUseCase,
)
from personal_finance.contexts.ingestion.application.mailbox import (
    MailboxConnection,
    MailboxProvider,
)
from personal_finance.contexts.ingestion.application.subscription_handlers import (
    KeepSubscriptionAliveUseCase,
    RenewalOutcome,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import UserId


_logger = logging.getLogger(__name__)


class MailboxAuthorizationError(Exception):
    """The provider did not give us usable access."""


class OAuthExchange(Protocol):
    """The provider-specific half: swap a callback code for a lasting
    permission, and tell us which mailbox it is actually for.
    """

    def complete(self, code: str) -> AuthorizedMailbox: ...


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AuthorizedMailbox:
    address: EmailAddress
    refresh_token: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class CompleteAuthorizationCommand:
    user_id: UserId
    provider: MailboxProvider
    code: str


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AuthorizationResult:
    connection: MailboxConnection
    subscribed: bool


class CompleteMailboxAuthorizationUseCase:
    """Turns a callback code into a connected, subscribed mailbox.

    The address comes from the provider, never from the request: a user must
    not be able to connect a mailbox by naming an address they do not own.
    """

    def __init__(
        self,
        *,
        exchanges: dict[MailboxProvider, OAuthExchange],
        connect_use_case: ConnectMailboxUseCase,
        keep_alive: KeepSubscriptionAliveUseCase,
        token_store: TokenWriter,
    ) -> None:
        self._exchanges = exchanges
        self._connect_use_case = connect_use_case
        self._keep_alive = keep_alive
        self._token_store = token_store

    def execute(self, command: CompleteAuthorizationCommand) -> AuthorizationResult:
        exchange = self._exchanges.get(command.provider)

        if exchange is None:
            raise MailboxAuthorizationError(
                f"{command.provider.value} is not configured",
            )

        authorized = exchange.complete(command.code)

        # The permission is stored before the connection exists: a connection
        # whose token is missing would look healthy and fail on every read.
        self._token_store.save_refresh_token(
            provider=command.provider,
            address=authorized.address,
            refresh_token=authorized.refresh_token,
        )
        connection = self._connect_use_case.execute(
            ConnectMailboxCommand(
                user_id=command.user_id,
                address=authorized.address,
                provider=command.provider,
            ),
        )

        # Subscribe right away rather than waiting for the next sweep, so a
        # user who just authorized starts receiving mail immediately. `force`
        # because a reconnected mailbox may still carry a stale expiry from
        # its previous life.
        renewal = self._keep_alive.execute(connection, force=True)

        if renewal.outcome is not RenewalOutcome.RENEWED:
            _logger.warning(
                "mailbox connected but not subscribed",
                extra={
                    "provider": command.provider.value,
                    "outcome": renewal.outcome.value,
                },
            )

        return AuthorizationResult(
            connection=renewal.connection,
            subscribed=renewal.outcome is RenewalOutcome.RENEWED,
        )


class TokenWriter(Protocol):
    def save_refresh_token(
        self,
        *,
        provider: MailboxProvider,
        address: EmailAddress,
        refresh_token: str,
    ) -> None: ...
