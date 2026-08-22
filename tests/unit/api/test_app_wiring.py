"""Which routes the app exposes depends on the environment.

The bank-notification webhook is unauthenticated and, now that users connect a
mailbox instead of forwarding mail, has no caller outside local testing. These
guard against it drifting back into a real deployment.
"""

from personal_finance.api.main import create_app


WEBHOOK = "/ingestion/bank-notifications"
MAILBOX_EVENTS = "/ingestion/mailbox-events/simulated"


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


def test_the_provider_doorbell_survives_in_a_real_deployment() -> None:
    # This one is the actual intake path.
    assert MAILBOX_EVENTS in _routes(expose_local_only_routes=False)


def test_identity_survives_in_a_real_deployment() -> None:
    paths = _routes(expose_local_only_routes=False)

    assert "/identity/mailboxes" in paths
    assert "/identity/login" in paths


def test_health_is_always_available() -> None:
    assert "/health" in _routes(expose_local_only_routes=False)


def test_merchants_survive_in_a_real_deployment() -> None:
    paths = _routes(expose_local_only_routes=False)

    assert "/merchants" in paths
    assert "/merchants/{merchant_id}" in paths
    assert "/merchants/categories" in paths
