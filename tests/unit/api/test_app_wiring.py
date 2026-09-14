"""Which routes the app exposes depends on the environment.

The bank-notification webhook is unauthenticated and, now that a user
forwards bank email to their own address instead of us reading their real
mailbox, has no HTTP caller at all outside local testing — the ingest worker
reads the ingest mailbox directly, in-process, no webhook involved. This
guards against it drifting back into a real deployment.
"""

from fastapi.testclient import TestClient

from personal_finance.api.main import create_app


WEBHOOK = "/ingestion/bank-notifications"


def _routes(*, expose_local_only_routes: bool) -> set[str]:
    """What the app actually publishes.

    Read from the OpenAPI schema rather than `app.routes`, which holds opaque
    router objects instead of flattened paths.
    """
    app = create_app(expose_local_only_routes=expose_local_only_routes)

    return set(app.openapi()["paths"])


def test_the_webhook_is_available_locally() -> None:
    assert WEBHOOK in _routes(expose_local_only_routes=True)


def test_the_webhook_is_not_exposed_in_a_real_deployment() -> None:
    # An unauthenticated public write surface with no caller is only a
    # liability.
    assert WEBHOOK not in _routes(expose_local_only_routes=False)


def test_identity_survives_in_a_real_deployment() -> None:
    paths = _routes(expose_local_only_routes=False)

    assert "/identity/inbox" in paths
    assert "/identity/login" in paths


def test_health_is_always_available() -> None:
    assert "/health" in _routes(expose_local_only_routes=False)


def test_ingestions_read_surface_survives_in_a_real_deployment() -> None:
    """Unlike the webhook. Listing what arrived is how somebody finds out
    their forwarding rule is not working, which is exactly the question a
    real deployment raises.
    """
    assert "/ingestion/notifications" in _routes(expose_local_only_routes=False)


def test_alerts_survive_in_a_real_deployment() -> None:
    paths = _routes(expose_local_only_routes=False)

    assert "/alerts/channels" in paths
    assert "/alerts/channels/{channel_id}" in paths


def test_the_telegram_webhook_is_exposed_in_a_real_deployment() -> None:
    """The opposite of the bank-notification webhook above, and the nearness
    of the two invites the wrong assumption.

    That one has no caller outside local testing. This one is how every
    channel in every environment gets bound: Telegram posts here when
    somebody presses Start, and without it linking does not work at all. It
    is unauthenticated by bearer token and protected by a shared secret
    compared in constant time.
    """
    assert "/alerts/telegram/webhook" in _routes(expose_local_only_routes=False)


def test_an_unconfigured_bot_does_not_take_the_deployment_down() -> None:
    """It did, once. A forgotten SAM parameter pointed the development stack
    at production's Telegram secret, the lookup failed, and the whole API
    refused to boot — no login, no movements, nothing — for a feature that
    adds to the app rather than holding it up.

    Building the app is what boots it, so this passing at all is the
    assertion; the 503 those endpoints answer is covered in the alerts tests.
    """
    paths = _routes(expose_local_only_routes=False)

    assert "/identity/login" in paths
    assert "/financial/transactions" in paths


def test_merchants_survive_in_a_real_deployment() -> None:
    paths = _routes(expose_local_only_routes=False)

    assert "/merchants" in paths
    assert "/merchants/{merchant_id}" in paths
    assert "/merchants/categories" in paths


def test_health_names_the_environment_it_is_serving() -> None:
    """The only other way to tell two deployments apart is the URL.

    `scripts/smoke.py` refuses to write to production, and it can only do that
    if the deployment says which one it is: the env file the script loaded is
    about its own machine, not about the address it was handed.
    """
    response = TestClient(
        create_app(expose_local_only_routes=False),
    ).get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "environment": "local"}
