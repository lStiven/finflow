from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from personal_finance.contexts.financial.presentation.http.router import (
    router as financial_router,
)
from personal_finance.contexts.identity.presentation.http.router import (
    get_login_use_case,
    get_register_use_case,
    router as identity_router,
)
from personal_finance.contexts.ingestion.presentation.http.notifications import (
    router as ingestion_notifications_router,
)
from personal_finance.contexts.ingestion.presentation.http.router import (
    get_use_case,
    router as ingestion_webhook_router,
)
from personal_finance.contexts.ingestion.presentation.http.setup import (
    router as ingestion_setup_router,
)
from personal_finance.contexts.merchant.presentation.http.router import (
    router as merchant_router,
)
from personal_finance.shared.infrastructure.config.settings import (
    get_api_settings,
    get_aws_settings,
)
from personal_finance.shared.infrastructure.observability.logging_config import (
    configure_logging,
)


health_router = APIRouter(tags=["health"])


@health_router.get("/health")
def health() -> dict[str, str]:
    """Alive, and which deployment this is.

    The environment is here because the only other way to tell two stacks
    apart is the URL, and a URL is exactly what gets pasted into the wrong
    command. Anything about to write — `scripts/smoke.py` above all — asks the
    deployment itself rather than trusting the env file it happened to load.
    """
    return {"status": "ok", "environment": get_aws_settings().environment.value}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    # Same as every worker's `main()`: without this, INFO logs raised while
    # handling a request (domain events, use-case outcomes, ...) have no
    # handler and are silently dropped.
    configure_logging()

    # Build the dependency graph eagerly so a missing queue URL, secret or
    # sender allow-list stops the boot instead of failing the first request.
    get_use_case()
    get_register_use_case()
    get_login_use_case()

    yield


def create_app(
    *,
    expose_local_only_routes: bool,
    cors_origins: tuple[str, ...] = (),
) -> FastAPI:
    """Build the application.

    A factory rather than a module-level side effect: which routes exist is a
    decision, and a decision that cannot be exercised without reimporting a
    module is a decision nothing tests. The origins allowed to call it from a
    browser are the same kind of decision, so they arrive the same way.
    """
    app = FastAPI(title="Finflow", lifespan=lifespan)

    if cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cors_origins),
            # This API authenticates with a bearer token the client sends
            # itself, never a cookie the browser attaches. Allowing credentials
            # would ask browsers to carry ambient authority that nothing here
            # reads, and it is what makes a mistaken origin dangerous.
            allow_credentials=False,
            # Only what the surface actually uses. OPTIONS is handled by the
            # middleware itself for the preflight.
            #
            # This list is a second copy of a fact the routers already state,
            # and it drifted once: `PUT` was missing while two endpoints used
            # it, so restating a balance and setting a credit limit failed the
            # preflight and never reached the API at all. `test_cors` compares
            # it against the routes to keep the copy honest.
            allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE"],
            allow_headers=["Authorization", "Content-Type"],
            # Without this the browser hides it. Only a handful of response
            # headers are readable across origins by default, and
            # `Retry-After` is not one of them — so the 429 from the
            # credential endpoints would reach the frontend served from
            # Cloudflare Pages with no way to say how long to wait, and it is
            # the only detail those endpoints give.
            expose_headers=["Retry-After"],
        )

    app.include_router(health_router)
    app.include_router(identity_router)
    app.include_router(merchant_router)
    app.include_router(financial_router)
    # Ingestion's read surface, unlike its webhook, belongs everywhere: it is
    # how somebody finds out that what they forwarded arrived, or why nothing
    # came of it.
    app.include_router(ingestion_notifications_router)
    app.include_router(ingestion_setup_router)

    if expose_local_only_routes:
        # Real intake never calls this: a user forwards bank email to their
        # own address, and the ingest worker reads it directly — nothing HTTP
        # in between. That leaves this webhook with no caller in a real
        # deployment, and an unauthenticated public write surface with no
        # caller is only a liability. It stays for local testing, where it is
        # the cheapest way to replay one email without touching a mailbox.
        app.include_router(ingestion_webhook_router)

    return app


app = create_app(
    expose_local_only_routes=get_aws_settings().is_local,
    cors_origins=get_api_settings().allowed_origins,
)
