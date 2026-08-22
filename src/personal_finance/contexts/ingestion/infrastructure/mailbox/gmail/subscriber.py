"""Keeping Gmail talking to us.

Gmail caps a `watch` at seven days and will not remind anyone. Calling
`watch` again is how it is renewed — there is no separate renew call — which
is why this is written to be safe to invoke at any time.
"""

from __future__ import annotations

import logging

from personal_finance.contexts.ingestion.application.mailbox import (
    MailboxConnection,
    MailboxEventDelivery,
    MailboxProvider,
    Subscription,
)
from personal_finance.contexts.ingestion.infrastructure.mailbox.gmail.api import (
    GmailApiClient,
)
from personal_finance.contexts.ingestion.infrastructure.mailbox.gmail.reader import (
    GmailAccessTokenProviderProtocol,
)
from personal_finance.shared.domain.value_objects import PosixTime


_logger = logging.getLogger(__name__)


# Gmail caps a watch at seven days and refuses anything longer.
GMAIL_WATCH_LIFETIME_SECONDS = 7 * 24 * 3_600


class GmailMailboxSubscriber:
    provider = MailboxProvider.GMAIL
    delivery = MailboxEventDelivery.PUSH
    lifetime_seconds = GMAIL_WATCH_LIFETIME_SECONDS

    def __init__(
        self,
        *,
        api: GmailApiClient,
        token_provider: GmailAccessTokenProviderProtocol,
        topic_name: str,
    ) -> None:
        self._api = api
        self._token_provider = token_provider
        self._topic_name = topic_name

    def subscribe(self, connection: MailboxConnection) -> Subscription:
        access_token = self._token_provider.access_token(connection.address)
        response = self._api.watch(
            access_token=access_token,
            topic_name=self._topic_name,
        )
        _logger.info(
            "gmail watch renewed",
            extra={
                "user_id": str(connection.user_id.value),
                "expires_at": response.expiration_epoch_millis,
            },
        )

        return Subscription(
            expires_at=PosixTime.from_epoch_milliseconds(
                response.expiration_epoch_millis,
            ),
            # Where a first sync starts from. The caller keeps its own cursor
            # when it already has one, so this only ever seeds a new
            # connection.
            cursor=response.history_id,
        )

    def unsubscribe(self, connection: MailboxConnection) -> None:
        access_token = self._token_provider.access_token(connection.address)
        self._api.stop(access_token=access_token)
