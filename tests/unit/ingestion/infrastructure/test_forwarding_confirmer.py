"""What the confirmer will and will not send a request to.

Confirming is two requests: a `GET` that lands on the page holding Google's
one-button form, then the `POST` that submits it. A `GET` alone renders the
page and confirms nothing, which is what this used to do.
"""

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
REDIRECTED = "https://mail.google.com/mail/vf-%5BANGjdJ-abc%5D-def"

# What Google actually serves, trimmed to the part that matters.
FORM_PAGE = '<html><body><form action="" method="post">'
DONE_PAGE = "<html><head><title>¡Confirmación exitosa!</title>"


def _confirmer(handler: object) -> HttpForwardingConfirmer:
    transport = httpx.MockTransport(handler)  # pyright: ignore[reportArgumentType]

    return HttpForwardingConfirmer(
        client=httpx.Client(transport=transport, follow_redirects=False),
    )


def _seen(calls: list[tuple[str, str]]) -> object:
    """A handler recording every request, answering the way Google does."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))

        if request.method == "GET" and str(request.url) == URL:
            return httpx.Response(302, headers={"Location": REDIRECTED})

        if request.method == "GET":
            return httpx.Response(200, text=FORM_PAGE)

        return httpx.Response(200, text=DONE_PAGE)

    return handler


def test_the_form_behind_the_link_is_posted() -> None:
    """The whole point: a `GET` renders the button, a `POST` presses it."""
    calls: list[tuple[str, str]] = []

    assert _confirmer(_seen(calls)).confirm(ForwardingConfirmation(url=URL)) is True
    assert calls == [("GET", URL), ("POST", REDIRECTED)]


def test_a_page_served_without_a_redirect_is_posted_where_it_was_found() -> None:
    """The form's `action` is empty, which means the page's own address."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))

        return httpx.Response(200, text=FORM_PAGE if request.method == "GET" else "")

    assert _confirmer(handler).confirm(ForwardingConfirmation(url=URL)) is True
    assert calls == [("GET", URL), ("POST", URL)]


def test_a_relative_redirect_stays_on_the_confirmation_host() -> None:
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))

        if request.method == "GET":
            return httpx.Response(302, headers={"Location": "/mail/vf-next"})

        return httpx.Response(200)

    assert _confirmer(handler).confirm(ForwardingConfirmation(url=URL)) is True
    assert calls[-1] == ("POST", "https://mail-settings.google.com/mail/vf-next")


def test_a_redirect_off_google_is_never_followed() -> None:
    """The `Location` came out of untrusted mail. Nothing is sent to it."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))

        return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

    assert _confirmer(handler).confirm(ForwardingConfirmation(url=URL)) is False
    assert calls == [("GET", URL)]


def test_a_redirect_to_a_lookalike_host_is_never_followed() -> None:
    """The same trap the request-side host check exists for, on the way back."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))

        return httpx.Response(
            302,
            headers={"Location": "https://mail.google.com.evil.test/ok"},
        )

    assert _confirmer(handler).confirm(ForwardingConfirmation(url=URL)) is False
    assert calls == [("GET", URL)]


def test_a_host_merely_ending_in_the_domain_is_never_followed() -> None:
    """`notgoogle.com` ends in `google.com` and is somebody else."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))

        return httpx.Response(302, headers={"Location": "https://notgoogle.com/ok"})

    assert _confirmer(handler).confirm(ForwardingConfirmation(url=URL)) is False
    assert calls == [("GET", URL)]


def test_a_plain_http_redirect_is_never_followed() -> None:
    """Downgrading the scheme is not something Google's own flow does."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))

        return httpx.Response(302, headers={"Location": "http://mail.google.com/ok"})

    assert _confirmer(handler).confirm(ForwardingConfirmation(url=URL)) is False
    assert calls == [("GET", URL)]


def test_a_link_google_refuses_is_reported_not_raised() -> None:
    """An expired or already-used link is a normal outcome, not a failure."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, str(request.url)))

        return httpx.Response(400)

    assert _confirmer(handler).confirm(ForwardingConfirmation(url=URL)) is False
    assert calls == [("GET", URL)]


def test_a_form_that_is_refused_when_submitted_is_not_a_confirmation() -> None:
    """The page was found and the button pressed, and Google said no."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, text=FORM_PAGE)

        return httpx.Response(403)

    assert _confirmer(handler).confirm(ForwardingConfirmation(url=URL)) is False


def test_a_redirect_status_without_a_location_leads_nowhere() -> None:
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
