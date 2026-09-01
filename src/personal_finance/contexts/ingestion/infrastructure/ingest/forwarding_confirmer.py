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

Google answers the confirm link with a **redirect**, not with a page. There is
no API and no machine-readable result, so the signal is the status plus where
the redirect points — read out of the `Location` header and never requested.
Requiring a 2xx here, as this once did, recorded every real confirmation as
refused: the fetch did confirm the forwarding, and the worker then dropped the
mail as spent without marking anything.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin, urlparse

import httpx

from personal_finance.contexts.ingestion.domain.forwarding_confirmation import (
    CONFIRMATION_HOST,
    ForwardingConfirmation,
)


_logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15.0

# Where a redirect may land and still mean "confirmed". Google does not
# document which of its hosts answers, and naming the two obvious ones would
# be a guess that breaks this again the day it answers from a third.
#
# Wide on purpose, because the two mistakes do not cost the same. Reading a
# real confirmation as refused breaks the feature outright — it already did,
# for every user. Reading a refusal as confirmed sets a checkmark early, and
# `InboxSetup.ready` does not depend on that checkmark: what says a setup
# works is an alert that actually arrived.
ACCEPTED_REDIRECT_DOMAIN = "google.com"


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

    # `has_redirect_location`, not `is_redirect`: the latter is only the
    # status class, and a 3xx with no `Location` says nothing about where this
    # went. It falls through to the refusal below, where it belongs.
    if response.has_redirect_location:
        return _redirect_means_confirmed(response)

    _logger.warning(
        "confirmation link was not accepted",
        extra={"status_code": response.status_code},
    )

    return False


def _is_google(hostname: str | None) -> bool:
    """The domain itself or a subdomain of it, never a name that merely ends
    in those letters: `mail.google.com.evil.test` and `notgoogle.com` are the
    two shapes this has to keep out.
    """
    if hostname is None:
        return False

    return hostname == ACCEPTED_REDIRECT_DOMAIN or hostname.endswith(
        f".{ACCEPTED_REDIRECT_DOMAIN}",
    )


def _redirect_means_confirmed(response: httpx.Response) -> bool:
    """Whether a redirect is Google acknowledging the confirmation.

    The target is read, never fetched. That keeps the guarantee the module
    docstring makes — a `Location` out of untrusted mail cannot become a
    request — while still telling an acknowledgement apart from a bounce to
    somewhere else.
    """
    # Relative targets are ordinary and resolve against the URL already pinned
    # to the confirmation host, so they are checked rather than assumed.
    target = urljoin(str(response.request.url), response.headers["location"])
    parsed = urlparse(target)

    if parsed.scheme == "https" and _is_google(parsed.hostname):
        return True

    _logger.warning(
        "confirmation link redirected somewhere that does not mean confirmed",
        extra={
            "status_code": response.status_code,
            # The host only: the rest of a confirmation URL is a credential.
            "location_host": parsed.hostname,
        },
    )

    return False
