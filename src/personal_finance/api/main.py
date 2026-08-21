from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

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


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    # Build the dependency graph eagerly so a missing queue URL, secret or
    # sender allow-list stops the boot instead of failing the first request.
    get_use_case()
    get_register_use_case()
    get_login_use_case()

    yield


app = FastAPI(title="Finflow", lifespan=lifespan)
app.include_router(ingestion_router)
app.include_router(mailbox_router)
app.include_router(identity_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
