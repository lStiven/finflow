"""Following a Gmail confirmation link, and refusing to follow anything else.

The one place this context makes an outbound request to something that is not
AWS, aimed at a URL that arrived inside untrusted mail. Everything here exists
to keep that from becoming a way to make the worker fetch arbitrary addresses:

* the URL was already pinned to scheme, host and `vf-` path prefix by
  `ForwardingConfirmation`, which refuses to exist otherwise;
* it is checked again here, against the parsed host rather than the string,
  because this is the call site and a value object can be constructed anywhere;
* redirects are not followed, so a 302 cannot walk the request somewhere the
  checks above already rejected.

Google answers the confirm link with an ordinary page; there is no API and no
machine-readable result, so a 2xx is the whole signal available.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

import httpx

from personal_finance.contexts.ingestion.domain.forwarding_confirmation import (
    CONFIRMATION_HOST,
    ForwardingConfirmation,
)


_logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15.0


class UnexpectedConfirmationHostError(Exception):
    """The URL did not point where a confirmation link must point.

    Raised rather than returned: reaching here means a `ForwardingConfirmation`
    exists whose URL its own validation should have refused, so the invariant
    is broken and no request should be attempted.
    """


class HttpForwardingConfirmer:
    """Confirms a forwarding request by fetching its link once."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._client = client
        self._timeout_seconds = timeout_seconds

    def confirm(self, confirmation: ForwardingConfirmation) -> bool:
        _assert_expected_host(confirmation.url)

        if self._client is not None:
            return _accepted(self._get(self._client, confirmation.url))

        with httpx.Client(
            timeout=self._timeout_seconds,
            follow_redirects=False,
        ) as client:
            return _accepted(self._get(client, confirmation.url))

    def _get(self, client: httpx.Client, url: str) -> httpx.Response:
        return client.get(url, follow_redirects=False)


def _assert_expected_host(url: str) -> None:
    parsed = urlparse(url)

    # Compared against the parsed host, not searched for in the string:
    # `https://mail-settings.google.com.evil.test/` contains the expected host
    # and is not it.
    if parsed.scheme != "https" or parsed.hostname != CONFIRMATION_HOST:
        raise UnexpectedConfirmationHostError(
            f"Refusing to fetch a confirmation URL at {parsed.hostname!r}",
        )


def _accepted(response: httpx.Response) -> bool:
    if response.is_success:
        return True

    _logger.warning(
        "confirmation link was not accepted",
        extra={"status_code": response.status_code},
    )

    return False
