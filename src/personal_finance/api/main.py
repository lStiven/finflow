from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI

from personal_finance.contexts.identity.presentation.http.oauth_router import (
    router as mailbox_oauth_router,
)
from personal_finance.contexts.identity.presentation.http.router import (
    get_login_use_case,
    get_register_use_case,
    router as identity_router,
)
from personal_finance.contexts.ingestion.presentation.http.mailbox_router import (
    router as mailbox_router,
)
from personal_finance.contexts.ingestion.presentation.http.router import (
    get_use_case,
    router as ingestion_router,
)
from personal_finance.shared.infrastructure.config.settings import get_aws_settings


health_router = APIRouter(tags=["health"])


@health_router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    # Build the dependency graph eagerly so a missing queue URL, secret or
    # sender allow-list stops the boot instead of failing the first request.
    get_use_case()
    get_register_use_case()
    get_login_use_case()

    yield


def create_app(*, expose_local_only_routes: bool) -> FastAPI:
    """Build the application.

    A factory rather than a module-level side effect: which routes exist is a
    decision, and a decision that cannot be exercised without reimporting a
    module is a decision nothing tests.
    """
    app = FastAPI(title="Finflow", lifespan=lifespan)
    app.include_router(health_router)
    app.include_router(mailbox_router)
    app.include_router(identity_router)
    app.include_router(mailbox_oauth_router)

    if expose_local_only_routes:
        # Users connect a mailbox; nobody forwards mail here. That leaves the
        # bank-notification webhook with no caller in a real deployment, and
        # an unauthenticated public write surface with no caller is only a
        # liability. It stays for local testing, where it is the cheapest way
        # to replay one email.
        app.include_router(ingestion_router)

    return app


app = create_app(expose_local_only_routes=get_aws_settings().is_local)
