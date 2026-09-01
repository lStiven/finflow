"""What the confirmer will and will not send a request to."""

import httpx
import pytest

from personal_finance.contexts.ingestion.domain.forwarding_confirmation import (
    ForwardingConfirmation,
)
from personal_finance.contexts.ingestion.infrastructure.ingest.forwarding_confirmer import (  # noqa: E501
    HttpForwardingConfirmer,
    UnexpectedConfirmationHostError,
)


URL = "https://mail-settings.google.com/mail/vf-%5BANGjdJ-abc%5D-def"


def _confirmer(handler: object) -> HttpForwardingConfirmer:
    transport = httpx.MockTransport(handler)  # pyright: ignore[reportArgumentType]

    return HttpForwardingConfirmer(
        client=httpx.Client(transport=transport, follow_redirects=False),
    )


def test_a_confirmed_request_reports_success() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))

        return httpx.Response(200, text="ok")

    assert _confirmer(handler).confirm(ForwardingConfirmation(url=URL)) is True
    assert seen == [URL]


def test_a_link_google_refuses_is_reported_not_raised() -> None:
    """An expired or already-used link is a normal outcome, not a failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request

        return httpx.Response(400)

    assert _confirmer(handler).confirm(ForwardingConfirmation(url=URL)) is False


def test_a_redirect_is_not_followed() -> None:
    """A 302 must not walk the request past the host checks above it."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))

        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

    assert _confirmer(handler).confirm(ForwardingConfirmation(url=URL)) is False
    assert requested == [URL]


def test_a_redirect_back_into_gmail_reports_success() -> None:
    """How Google actually answers a link it accepted. Read, never followed:
    requiring a 2xx here recorded every real confirmation as refused.
    """
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))

        return httpx.Response(
            302,
            headers={"Location": "https://mail.google.com/mail/u/0/#settings"},
        )

    assert _confirmer(handler).confirm(ForwardingConfirmation(url=URL)) is True
    assert requested == [URL]


def test_a_relative_redirect_stays_on_the_confirmation_host() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request

        return httpx.Response(302, headers={"Location": "/mail/vf-done"})

    assert _confirmer(handler).confirm(ForwardingConfirmation(url=URL)) is True


def test_any_google_subdomain_counts_as_confirmed() -> None:
    """Which host answers is not documented, and guessing narrowly is what
    broke this before. A checkmark set early costs less than a feature that
    reports every real confirmation as refused.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        del request

        return httpx.Response(
            302,
            headers={"Location": "https://accounts.google.com/ServiceLogin"},
        )

    assert _confirmer(handler).confirm(ForwardingConfirmation(url=URL)) is True


def test_a_redirect_to_a_lookalike_host_is_not_a_confirmation() -> None:
    """The same trap the request-side host check exists for, on the way back."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request

        return httpx.Response(
            302,
            headers={"Location": "https://mail.google.com.evil.test/ok"},
        )

    assert _confirmer(handler).confirm(ForwardingConfirmation(url=URL)) is False


def test_a_host_merely_ending_in_the_domain_is_not_a_confirmation() -> None:
    """`notgoogle.com` ends in `google.com` and is somebody else."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request

        return httpx.Response(302, headers={"Location": "https://notgoogle.com/ok"})

    assert _confirmer(handler).confirm(ForwardingConfirmation(url=URL)) is False


def test_a_plain_http_redirect_is_not_a_confirmation() -> None:
    """Downgrading the scheme is not something Google's own flow does."""

    def handler(request: httpx.Request) -> httpx.Response:
        del request

        return httpx.Response(302, headers={"Location": "http://mail.google.com/ok"})

    assert _confirmer(handler).confirm(ForwardingConfirmation(url=URL)) is False


def test_a_redirect_status_without_a_location_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        del request

        return httpx.Response(302)

    assert _confirmer(handler).confirm(ForwardingConfirmation(url=URL)) is False


def test_a_url_on_another_host_is_refused_before_any_request() -> None:
    """Defence in depth: the value object refuses this too, but this is the
    call site, and a value object can be constructed anywhere.
    """
    confirmation = ForwardingConfirmation.__new__(ForwardingConfirmation)
    object.__setattr__(
        confirmation,
        "url",
        "https://mail-settings.google.com.evil.test/mail/vf-abc",
    )

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        del request
        raise AssertionError("no request should have been made")

    with pytest.raises(UnexpectedConfirmationHostError):
        _confirmer(handler).confirm(confirmation)


def test_redirects_stay_unfollowed_even_behind_a_client_that_would_follow_them() -> (
    None
):
    """The refusal is per request, not a property of the client handed in.

    An injected client is a test seam and a caller could configure it either
    way; the one place that must not be overridable is this.
    """
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))

        if "vf-" in str(request.url):
            return httpx.Response(
                302,
                headers={"Location": "http://169.254.169.254/latest/meta-data/"},
            )

        return httpx.Response(200)

    confirmer = HttpForwardingConfirmer(
        client=httpx.Client(
            transport=httpx.MockTransport(handler),
            follow_redirects=True,
        ),
    )

    assert confirmer.confirm(ForwardingConfirmation(url=URL)) is False
    assert requested == [URL]
