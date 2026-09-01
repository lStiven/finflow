"""Submitting Gmail's confirmation form, and refusing to submit anything else.

The one place this context makes an outbound request to something that is not
AWS, aimed at a URL that arrived inside untrusted mail. Everything here exists
to keep that from becoming a way to make the worker call arbitrary addresses:

* the URL was already pinned to scheme, host and `vf-` path prefix by
  `ForwardingConfirmation`, which refuses to exist otherwise;
* it is checked again here, against the parsed host rather than the string,
  because this is the call site and a value object can be constructed anywhere;
* the one redirect this follows is checked the same way before it is followed,
  so a `Location` out of untrusted mail cannot walk the request off Google.

**Confirming is a POST, not a GET.** The link in the mail answers a `GET` with
a redirect to `mail.google.com`, which serves an ordinary page holding one
button — `<form action="" method="post">`, no fields, no token beyond the one
already in the URL. Fetching the link therefore confirms nothing: it renders
the page a person would have clicked. This read a `GET` as the whole exchange
for a while, first demanding a 2xx (so every attempt was recorded as refused)
and then accepting the redirect (so every attempt was recorded as confirmed
while the forwarding stayed pending). Both were wrong about the same thing.

The exchange, observed against the real Google on 2026-09-01:

    GET  mail-settings.google.com/mail/vf-…  -> 302 to mail.google.com/mail/vf-…
    GET  mail.google.com/mail/vf-…           -> 200, "Confirmación" + the form
    POST mail.google.com/mail/vf-…           -> 200, "¡Confirmación exitosa!"

There is still no API and no machine-readable result — every answer is a page
for a person, in the mailbox account's own language — so success is the POST's
status, and an error page served with 200 remains indistinguishable from an
accepted one. Deliberately: reading a real confirmation as refused breaks the
feature for everybody, while reading a refusal as confirmed only sets a
checkmark early, and `InboxSetup.ready` does not depend on that checkmark.
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

# Where the form may live. The redirect observed goes to `mail.google.com`,
# but which host answers is not documented and naming that one alone would
# break this again the day it answers from another. Scheme and domain are
# pinned; the host inside the domain is not.
CONFIRMATION_DOMAIN = "google.com"


class UnexpectedConfirmationHostError(Exception):
    """The URL did not point where a confirmation link must point.

    Raised rather than returned: reaching here means a `ForwardingConfirmation`
    exists whose URL its own validation should have refused, so the invariant
    is broken and no request should be attempted.
    """


class HttpForwardingConfirmer:
    """Confirms a forwarding request by submitting the form behind its link."""

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
            return self._submit(self._client, confirmation.url)

        with httpx.Client(
            timeout=self._timeout_seconds,
            follow_redirects=False,
        ) as client:
            return self._submit(client, confirmation.url)

    def _submit(self, client: httpx.Client, url: str) -> bool:
        """Find the form and post it: two requests, both pinned to Google.

        Redirects are never followed by the client itself. A 302 is resolved
        here instead, so the host it names is checked before anything is sent
        to it — and so the POST stays a POST, which an automatic redirect
        would silently turn back into a GET.
        """
        page = client.get(url, follow_redirects=False)
        target = _form_target(page, url)

        if target is None:
            return False

        # No fields: the page's own form carries none, and the token that
        # authorises this is the one already in the URL.
        return _accepted(client.post(target, follow_redirects=False))


def _assert_expected_host(url: str) -> None:
    parsed = urlparse(url)

    # Compared against the parsed host, not searched for in the string:
    # `https://mail-settings.google.com.evil.test/` contains the expected host
    # and is not it.
    if parsed.scheme != "https" or parsed.hostname != CONFIRMATION_HOST:
        raise UnexpectedConfirmationHostError(
            f"Refusing to fetch a confirmation URL at {parsed.hostname!r}",
        )


def _form_target(page: httpx.Response, url: str) -> str | None:
    """Where to post the confirmation, or `None` if there is nowhere safe.

    The form's `action` is empty, which means the page's own address — so the
    target is the URL that served the page, either the one fetched or the one
    a redirect named.
    """
    if page.is_success:
        return url

    # `has_redirect_location`, not `is_redirect`: the latter is only the
    # status class, and a 3xx with no `Location` names no target at all.
    if not page.has_redirect_location:
        _logger.warning(
            "confirmation link did not lead to a form",
            extra={"status_code": page.status_code},
        )

        return None

    # Relative targets are ordinary and resolve against the URL already pinned
    # to the confirmation host, so they are checked rather than assumed.
    target = urljoin(url, page.headers["location"])
    parsed = urlparse(target)

    if parsed.scheme == "https" and _is_google(parsed.hostname):
        return target

    _logger.warning(
        "confirmation link redirected off Google and was not followed",
        extra={
            "status_code": page.status_code,
            # The host only: the rest of a confirmation URL is a credential.
            "location_host": parsed.hostname,
        },
    )

    return None


def _is_google(hostname: str | None) -> bool:
    """The domain itself or a subdomain of it, never a name that merely ends
    in those letters: `mail.google.com.evil.test` and `notgoogle.com` are the
    two shapes this has to keep out.
    """
    if hostname is None:
        return False

    return hostname == CONFIRMATION_DOMAIN or hostname.endswith(
        f".{CONFIRMATION_DOMAIN}",
    )


def _accepted(response: httpx.Response) -> bool:
    """Whether the submitted form was taken.

    A redirect counts: a form that succeeds commonly answers with one, and
    where it points is not what decides — the POST already happened.
    """
    if response.is_success or response.has_redirect_location:
        return True

    _logger.warning(
        "confirmation form was not accepted",
        extra={"status_code": response.status_code},
    )

    return False
