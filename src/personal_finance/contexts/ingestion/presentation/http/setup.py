"""How far the caller got connecting their bank.

The read behind the screen that walks a new user through forwarding their
first alert. It exists because two of the four steps close somewhere the user
cannot see — Google confirms a forwarding request by mailing an address only
this deployment can read, and the first alert lands in a worker — so without
this the screen could only say "we'll let you know", which is the part of
every onboarding that feels broken even when it works.

Nothing here is stored progress. There is no endpoint to advance a step and
no field a client writes: the state is computed from the inbox record on
every call, so it is the same in every browser and cannot drift from what the
mailbox actually did. Cheap enough to poll — one item read while the answer
is still changing.

Scoped to the token, like every other read in this context.
"""

from __future__ import annotations

import functools
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.contexts.ingestion.application.inbox_handlers import (
    GetInboxSetupUseCase,
    InboxSetupView,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.contexts.ingestion.infrastructure.persistence.dynamodb import (
    DynamoDBNotificationReader,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.user_inbox_dynamodb import (  # noqa: E501
    DynamoDBUserInboxRepository,
)
from personal_finance.shared.domain.value_objects import UserId
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import (
    get_ingestion_settings,
)


router = APIRouter(prefix="/ingestion", tags=["ingestion"])


class SetupStepResponse(BaseModel):
    """One step of connecting a bank.

    `at` is null for the two steps that are states rather than events (the
    address exists, somebody is approved) — and for any step still open.
    """

    key: str
    done: bool
    at: int | None


class InboxSetupResponse(BaseModel):
    address: str
    steps: list[SetupStepResponse]
    # The step to point at, or null once nothing is left — so a client never
    # has to read "done" out of a value that otherwise means "do this".
    current: str | None
    # Whether expenses are arriving on their own right now. Not simply every
    # step being done: somebody forwarding each alert by hand is connected
    # and will never have a confirmation to show for it.
    ready: bool
    # Senders whose mail arrived and was discarded for not being approved.
    # Empty once `ready`.
    unapproved_senders: list[str]


@functools.lru_cache(maxsize=1)
def _build_use_case() -> GetInboxSetupUseCase:
    settings = get_ingestion_settings()
    client = get_dynamodb_client()

    if not settings.ingest_mailbox_address:
        # The same setting registration derives every alias from. Without it
        # there is no address to look one up by, and no account could have
        # been registered in the first place.
        raise ValueError(
            "INGESTION_INGEST_MAILBOX_ADDRESS is not set: there is no "
            "address to derive a forwarding alias from. "
            "See docs/email-forwarding.md.",
        )

    return GetInboxSetupUseCase(
        inbox_repository=DynamoDBUserInboxRepository(
            client=client,
            table_name=settings.user_inboxes_table,
        ),
        notification_reader=DynamoDBNotificationReader(
            client=client,
            table_name=settings.notifications_table,
        ),
        base_address=EmailAddress(settings.ingest_mailbox_address),
    )


def get_setup_use_case() -> GetInboxSetupUseCase:
    return _build_use_case()


@router.get("/setup", response_model=InboxSetupResponse)
def get_setup(
    user_id: Annotated[UserId, Depends(get_current_user_id)],
    use_case: Annotated[GetInboxSetupUseCase, Depends(get_setup_use_case)],
) -> InboxSetupResponse:
    """The caller's four steps, which of them are done, and what is next."""
    view = use_case.execute(user_id)

    if view is None:
        # Registration always creates one; reaching this means the token
        # verified for a user whose inbox record is somehow gone.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No inbox found for this account",
        )

    return _setup_response(view)


def _setup_response(view: InboxSetupView) -> InboxSetupResponse:
    current = view.setup.current

    return InboxSetupResponse(
        address=view.address.value,
        steps=[
            SetupStepResponse(
                key=state.step.value,
                done=state.done,
                at=None if state.at is None else state.at.as_epoch_seconds(),
            )
            for state in view.setup.steps
        ],
        current=None if current is None else current.value,
        ready=view.setup.ready,
        unapproved_senders=[sender.value for sender in view.unapproved_senders],
    )
