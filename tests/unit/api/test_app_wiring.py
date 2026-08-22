"""Which routes the app exposes depends on the environment.

The bank-notification webhook is unauthenticated and, now that a user
forwards bank email to their own address instead of us reading their real
mailbox, has no HTTP caller at all outside local testing — the ingest worker
reads the ingest mailbox directly, in-process, no webhook involved. This
guards against it drifting back into a real deployment.
"""

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


def test_merchants_survive_in_a_real_deployment() -> None:
    paths = _routes(expose_local_only_routes=False)

    assert "/merchants" in paths
    assert "/merchants/{merchant_id}" in paths
    assert "/merchants/categories" in paths
