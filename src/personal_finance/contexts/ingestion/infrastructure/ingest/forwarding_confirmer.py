"""Submitting Gmail's confirmation form, and refusing to submit anything else.

The one place this context makes an outbound request to something that is not
AWS, aimed at a URL that arrived inside untrusted mail. Everything here exists
to keep that from becoming a way to make the worker call arbitrary addresses:

* the URL was already pinned to scheme, host and `vf-` path prefix by
  `ForwardingConfirmation`, which refuses to exist otherwise;
* it is checked again here, against the parsed host rather than the string,
  because this is the call site and a value object can be constructed anywhere;
* every redirect is checked the same way before it is followed, and only a
  few are, so a `Location` out of untrusted mail cannot walk the request off
  Google.

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
for a person, in the mailbox account's own language. **A status code is not an
answer.** Reading the POST's 2xx as success marked a real user's forwarding
confirmed on 2026-10-04 while Gmail still listed it pending: the checkmark is
what tells a user they are done, so a false one strands them. What is checked
instead is the shape of the page, which no translation changes: the page that
asks holds a form and the page that confirms does not, and both are served
from Gmail itself — a page anywhere else on Google (a sign-in, a "sorry, unusual
traffic" check from a datacenter address) is not an answer at all.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urljoin, urlparse

import httpx

from personal_finance.contexts.ingestion.domain.forwarding_confirmation import (
    CONFIRMATION_HOSTS,
    ForwardingConfirmation,
)


_logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 15.0

# Where a redirect may lead. Which host Google bounces through is not
# documented, so scheme and domain are pinned and the host inside it is not.
# The page that *answers* is held tighter, to the Gmail hosts themselves: a
# false "no" costs the user one resend from Gmail, a false "yes" costs them
# the step.
CONFIRMATION_DOMAIN = "google.com"

MAX_REDIRECTS = 3

_FORM = re.compile(r"<form\b", re.IGNORECASE)
_TITLE = re.compile(r"<title[^>]*>([^<]*)", re.IGNORECASE)


class UnexpectedConfirmationHostError(Exception):
    """The URL did not point where a confirmation link must point.

    Raised rather than returned: reaching here means a `ForwardingConfirmation`
    exists whose URL its own validation should have refused, so the invariant
    is broken and no request should be attempted.
    """


class ConfirmationUnansweredError(Exception):
    """Google answered with something other than Gmail's own page.

    Raised rather than returned, so the mail is left for the next poll: a
    sign-in, an unusual-traffic check or a 5xx says nothing about the link,
    which is still unspent and stays valid for days. Acknowledging it would
    lose the user's only confirmation over a transient answer.
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

        # One client for both requests, so whatever cookie the form page sets
        # travels with the POST the way it would in a browser.
        with httpx.Client(
            timeout=self._timeout_seconds,
            follow_redirects=False,
        ) as client:
            return self._submit(client, confirmation.url)

    def _submit(self, client: httpx.Client, url: str) -> bool:
        """Load the form, post it, and read what came back.

        Redirects are never followed by the client itself. Each one is
        resolved here instead, so the host it names is checked before anything
        is sent to it — and so the POST stays a POST, which an automatic
        redirect would silently turn back into a GET.
        """
        page = _follow(client, client.get(url, follow_redirects=False), url)

        if page is None:
            return False

        page_url, response = page
        _assert_answered(page_url, response)

        if not (_is_gmail_page(page_url, response) and _has_form(response)):
            # An expired or already-used link lands here, and so does anything
            # Google serves instead of the form.
            _logger.warning(
                "confirmation link did not lead to the form",
                extra=_describe(page_url, response),
            )

            return False

        # No fields: the page's own form carries none, and the token that
        # authorises this is the one already in the URL. The form's `action`
        # is empty, which means the address that served the page.
        answer = _follow(
            client,
            client.post(page_url, follow_redirects=False),
            page_url,
        )

        if answer is None:
            return False

        answer_url, answer_response = answer
        _assert_answered(answer_url, answer_response)

        if _is_gmail_page(answer_url, answer_response) and not _has_form(
            answer_response,
        ):
            return True

        _logger.warning(
            "confirmation form was not accepted",
            extra=_describe(answer_url, answer_response),
        )

        return False


def _assert_expected_host(url: str) -> None:
    parsed = urlparse(url)

    # Compared against the parsed host, not searched for in the string:
    # `https://mail-settings.google.com.evil.test/` contains the expected host
    # and is not it.
    if parsed.scheme != "https" or parsed.hostname not in CONFIRMATION_HOSTS:
        raise UnexpectedConfirmationHostError(
            f"Refusing to fetch a confirmation URL at {parsed.hostname!r}",
        )


def _follow(
    client: httpx.Client,
    response: httpx.Response,
    url: str,
) -> tuple[str, httpx.Response] | None:
    """The page a response finally leads to, or `None` if there is nowhere safe.

    Only GETs from here on, only to Google, and only a few: the exchange
    observed takes one hop, and a chain longer than that is not Google's flow.
    """
    for _ in range(MAX_REDIRECTS):
        if not response.is_redirect:
            return url, response

        # `has_redirect_location`, not `is_redirect`: the latter is only the
        # status class, and a 3xx with no `Location` names no target at all.
        if not response.has_redirect_location:
            _logger.warning(
                "confirmation redirect named no target",
                extra={"status_code": response.status_code},
            )

            return None

        # Relative targets are ordinary and resolve against a URL already
        # pinned to Google, so they are checked rather than assumed.
        target = urljoin(url, response.headers["location"])
        parsed = urlparse(target)

        if parsed.scheme != "https" or not _is_google(parsed.hostname):
            _logger.warning(
                "confirmation link redirected off Google and was not followed",
                extra={
                    "status_code": response.status_code,
                    # The host only: the rest of a confirmation URL is a
                    # credential.
                    "location_host": parsed.hostname,
                },
            )

            return None

        url = target
        response = client.get(url, follow_redirects=False)

    if response.is_redirect:
        _logger.warning("confirmation link redirected too many times")

        return None

    return url, response


def _assert_answered(url: str, response: httpx.Response) -> None:
    if response.status_code >= 500 or urlparse(url).hostname not in (
        CONFIRMATION_HOSTS
    ):
        raise ConfirmationUnansweredError(
            f"Google did not answer the confirmation: {_describe(url, response)}",
        )


def _is_gmail_page(url: str, response: httpx.Response) -> bool:
    """A page Gmail itself served, as opposed to somewhere else on Google."""
    return response.is_success and urlparse(url).hostname in CONFIRMATION_HOSTS


def _has_form(response: httpx.Response) -> bool:
    return _FORM.search(response.text) is not None


def _describe(url: str, response: httpx.Response) -> dict[str, object]:
    """What an operator needs to tell the cases apart — never the URL itself."""
    title = _TITLE.search(response.text)

    return {
        "status_code": response.status_code,
        "host": urlparse(url).hostname,
        "has_form": _has_form(response),
        "title": title.group(1).strip()[:80] if title else None,
    }


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
