"""Sending, over Telegram's HTTP API.

**The bot token is in the URL.** Telegram's API is
`https://api.telegram.org/bot<TOKEN>/sendMessage`, and httpx puts the request
URL into `HTTPStatusError`, into `repr(request)`, and into the default text of
most of its exceptions. One `logger.exception(...)` around a send would write
the token into CloudWatch, where it would stay for the log group's retention
and let whoever read it message every linked user as this deployment.

So: the token goes in `base_url` and never into a logged string, exceptions
are caught by type and never logged as objects, and what gets recorded is a
status code and a channel id. That discipline is the reason this module is
more careful than its twenty lines of behaviour suggest.
"""

from __future__ import annotations

from collections.abc import Mapping
import logging
from typing import TYPE_CHECKING

import httpx

from personal_finance.contexts.alerts.application.ports import (
    DestinationRefusedError,
    TransportUnavailableError,
)
from personal_finance.contexts.alerts.infrastructure.telegram.messages import (
    compose_chat_already_linked,
    compose_link_confirmation,
    compose_movement_alert,
)


if TYPE_CHECKING:
    from pydantic import SecretStr

    from personal_finance.contexts.alerts.application.messages import MovementAlert
    from personal_finance.contexts.alerts.domain.value_objects import ChatId


_logger = logging.getLogger(__name__)

TELEGRAM_API_BASE_URL = "https://api.telegram.org"
DEFAULT_TIMEOUT_SECONDS = 10.0

# Telegram answers 429 with a retry-after when it wants the caller to slow
# down. It is a 4xx by number and an outage by meaning, so it is listed here
# rather than inferred from the class of the code.
_RETRYABLE_STATUSES = frozenset({408, 429})


class TelegramMessageSender:
    """`MessageSender` over Telegram's bot API."""

    def __init__(
        self,
        *,
        bot_token: SecretStr,
        api_base_url: str = TELEGRAM_API_BASE_URL,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        display_timezone: str,
        client: httpx.Client | None = None,
    ) -> None:
        self._token = bot_token
        self._api_base_url = api_base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._display_timezone = display_timezone
        self._client = client

    def send_movement_alert(self, *, chat_id: ChatId, alert: MovementAlert) -> None:
        self._send(
            chat_id,
            compose_movement_alert(alert, timezone=self._display_timezone),
        )

    def send_link_confirmation(self, *, chat_id: ChatId) -> None:
        self._send(chat_id, compose_link_confirmation())

    def send_chat_already_linked(self, *, chat_id: ChatId) -> None:
        self._send(chat_id, compose_chat_already_linked())

    def _send(self, chat_id: ChatId, text: str) -> None:
        payload: Mapping[str, object] = {
            "chat_id": chat_id.value,
            "text": text,
            # No `parse_mode`. Part of this text is what a bank wrote, so
            # there is no markup to render and nothing to escape.
            "disable_web_page_preview": True,
        }

        try:
            response = self._post(payload)
        except httpx.HTTPError as error:
            # By type, never by value: `str(error)` carries the URL, and the
            # URL carries the token.
            raise TransportUnavailableError(
                f"Telegram could not be reached ({type(error).__name__})",
            ) from None

        if response.status_code in _RETRYABLE_STATUSES or response.status_code >= 500:
            raise TransportUnavailableError(
                f"Telegram answered {response.status_code}",
            )

        if response.status_code >= 400:
            # Blocked, kicked, or a chat that no longer exists. Saying which
            # would mean quoting Telegram's body, which echoes the request.
            raise DestinationRefusedError(
                f"Telegram refused this destination ({response.status_code})",
            )

        _logger.info(
            "alert delivered",
            # A status code, and nothing else. The body is a line of
            # somebody's spending history and the URL holds the token.
            extra={"status_code": response.status_code},
        )

    def _post(self, payload: Mapping[str, object]) -> httpx.Response:
        url = f"/bot{self._token.get_secret_value()}/sendMessage"

        if self._client is not None:
            return self._client.post(url, json=payload)

        with httpx.Client(
            base_url=self._api_base_url,
            timeout=self._timeout_seconds,
        ) as client:
            return client.post(url, json=payload)


class LoggingMessageSender:
    """`MessageSender` that writes the message instead of sending it.

    What a local run uses when no bot token is configured, so the exact text
    a real Telegram user would have received is visible in the worker's
    output. Selected by `ENVIRONMENT` alone — never by a setting of its own,
    because a setting is something somebody can turn on in production.
    """

    def __init__(self, *, display_timezone: str) -> None:
        self._display_timezone = display_timezone

    def send_movement_alert(self, *, chat_id: ChatId, alert: MovementAlert) -> None:
        self._log(
            chat_id,
            compose_movement_alert(alert, timezone=self._display_timezone),
        )

    def send_link_confirmation(self, *, chat_id: ChatId) -> None:
        self._log(chat_id, compose_link_confirmation())

    def send_chat_already_linked(self, *, chat_id: ChatId) -> None:
        self._log(chat_id, compose_chat_already_linked())

    def _log(self, chat_id: ChatId, text: str) -> None:
        _logger.info(
            "telegram message (not sent: no bot configured)\nto %s\n%s",
            chat_id.masked,
            text,
        )
