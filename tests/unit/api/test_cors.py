"""Which browser origins may call this API.

The default is none: a frontend served from the same origin needs no CORS, and
a wrong answer here is what lets a page nobody wrote read somebody's finances
from their own browser.
"""

from fastapi.testclient import TestClient
import pytest

from personal_finance.api.main import create_app
from personal_finance.shared.infrastructure.config.settings import (
    ApiSettings,
    Environment,
)


ALLOWED = "https://app.example.com"
STRANGER = "https://not-the-frontend.example.com"
ALLOW_ORIGIN = "access-control-allow-origin"


def _client(*origins: str) -> TestClient:
    return TestClient(
        create_app(expose_local_only_routes=False, cors_origins=origins),
    )


def test_no_origins_configured_means_no_cors_headers() -> None:
    response = _client().get("/health", headers={"Origin": ALLOWED})

    # The request still succeeds — CORS is enforced by the browser, not here.
    # What it must not do is tell the browser the answer may be read.
    assert response.status_code == 200
    assert ALLOW_ORIGIN not in response.headers


def test_a_configured_origin_may_read_the_answer() -> None:
    response = _client(ALLOWED).get("/health", headers={"Origin": ALLOWED})

    assert response.status_code == 200
    assert response.headers[ALLOW_ORIGIN] == ALLOWED


def test_an_origin_nobody_named_may_not() -> None:
    response = _client(ALLOWED).get("/health", headers={"Origin": STRANGER})

    # The answer is produced either way — CORS is the browser's rule, not the
    # server's — and what is withheld is the permission to read it.
    assert response.status_code == 200
    assert ALLOW_ORIGIN not in response.headers


def test_a_preflight_from_an_origin_nobody_named_is_refused_outright() -> None:
    """The one case the middleware answers itself, so it can say no with a
    status. Worth pinning: it is what a developer sees first when the origin
    list is wrong.
    """
    response = _client(ALLOWED).options(
        "/financial/accounts",
        headers={
            "Origin": STRANGER,
            "Access-Control-Request-Method": "GET",
        },
    )

    assert response.status_code == 400
    assert ALLOW_ORIGIN not in response.headers


def test_the_preflight_allows_the_token_header() -> None:
    """Without this the browser never sends the real request: every
    authenticated call carries `Authorization`, which is not a header CORS
    permits by default.
    """
    response = _client(ALLOWED).options(
        "/financial/accounts",
        headers={
            "Origin": ALLOWED,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code == 200
    assert response.headers[ALLOW_ORIGIN] == ALLOWED
    assert "authorization" in response.headers["access-control-allow-headers"].lower()


def test_credentials_are_never_allowed() -> None:
    """Authentication here is a bearer token the client sends itself. Nothing
    reads a cookie, so nothing should ask browsers to attach one.
    """
    response = _client(ALLOWED).get("/health", headers={"Origin": ALLOWED})

    assert "access-control-allow-credentials" not in response.headers


def test_a_wildcard_is_refused_outside_local() -> None:
    with pytest.raises(ValueError, match="API_CORS_ORIGINS"):
        ApiSettings(
            environment=Environment.PRODUCTION,
            cors_origins=f"{ALLOWED},*",
        )


def test_a_wildcard_is_fine_on_a_developer_machine() -> None:
    settings = ApiSettings(environment=Environment.LOCAL, cors_origins="*")

    assert settings.allowed_origins == ("*",)


@pytest.mark.parametrize(
    "origin",
    [
        # The mistake this exists for: a browser sends no trailing slash, so a
        # configured one silently matches nothing at all.
        "https://app.example.com/",
        "https://app.example.com/app",
        "app.example.com",
        "ftp://app.example.com",
        "https://",
    ],
)
def test_something_that_is_not_an_origin_is_refused_at_startup(origin: str) -> None:
    with pytest.raises(ValueError, match="not a browser origin"):
        ApiSettings(environment=Environment.LOCAL, cors_origins=origin)


def test_an_origin_is_folded_to_the_case_a_browser_sends() -> None:
    """Scheme and host are case-insensitive by specification, and the
    middleware compares what is configured here to the header verbatim.
    """
    settings = ApiSettings(
        environment=Environment.LOCAL,
        cors_origins="HTTPS://App.Example.com",
    )

    assert settings.allowed_origins == ("https://app.example.com",)


def test_a_port_is_part_of_the_origin() -> None:
    settings = ApiSettings(
        environment=Environment.LOCAL,
        cors_origins="http://localhost:5173",
    )

    assert settings.allowed_origins == ("http://localhost:5173",)


def test_origins_are_read_as_a_comma_separated_list() -> None:
    settings = ApiSettings(
        environment=Environment.LOCAL,
        cors_origins=f" {ALLOWED} , http://localhost:5173 ,",
    )

    assert settings.allowed_origins == (ALLOWED, "http://localhost:5173")


def test_nothing_configured_is_an_empty_list_not_a_blank_origin() -> None:
    assert (
        ApiSettings(environment=Environment.LOCAL, cors_origins="").allowed_origins
        == ()
    )


def test_every_method_the_api_exposes_survives_a_preflight() -> None:
    """The allow-list is a second copy of what the routers declare, and a
    browser cannot reach a method missing from it — the preflight fails and
    the real request is never sent. `PUT` was absent while two endpoints used
    it, which is what this pins.
    """
    app = create_app(expose_local_only_routes=False, cors_origins=(ALLOWED,))
    client = TestClient(app)
    # From the schema, not `app.routes`: included routers stay nested there,
    # so walking it collects the docs endpoints and nothing else.
    declared = {
        method.upper()
        for operations in app.openapi()["paths"].values()
        for method in operations
        if method.upper() not in {"HEAD", "OPTIONS"}
    }

    assert {"GET", "POST", "PATCH", "PUT", "DELETE"} <= declared

    for method in sorted(declared):
        response = client.options(
            "/financial/accounts",
            headers={
                "Origin": ALLOWED,
                "Access-Control-Request-Method": method,
            },
        )

        assert response.status_code == 200, f"{method} fails the preflight"
        assert method in response.headers["access-control-allow-methods"]


def test_the_wait_after_a_429_is_readable_across_origins() -> None:
    """`Retry-After` is not a header a browser exposes by default.

    The credential endpoints answer a throttled request with it and with
    nothing else — how much mail an address has already caused, and whether it
    has an account at all, is exactly what they refuse to say. Unexposed, the
    frontend on its own origin would see the 429 and not the wait.
    """
    app = create_app(expose_local_only_routes=False, cors_origins=(ALLOWED,))
    client = TestClient(app)

    response = client.get("/health", headers={"Origin": ALLOWED})

    exposed = response.headers["access-control-expose-headers"]
    assert "Retry-After" in exposed
