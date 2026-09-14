"""The endpoints an authenticated owner uses to manage their channels.

The webhook is deliberately *not* here. It has a completely different story
about who the caller is — a shared secret with Telegram rather than a bearer
token — and mixing the two in one module is how a route ends up missing the
dependency that was supposed to protect it. It lives in `telegram.py`.

One rule worth stating because a later change could quietly break it: the
link token is returned by `POST /alerts/channels` and never again. There is
no field for it on `ChannelResponse`, which is what makes that hard to undo
by accident.
"""

from __future__ import annotations

from collections.abc import Generator
import contextlib
from decimal import Decimal, InvalidOperation
import functools
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from personal_finance.contexts.alerts.application.commands import (
    CreateChannelCommand,
    DeleteChannelCommand,
    UpdateChannelPreferenceCommand,
)
from personal_finance.contexts.alerts.application.handlers import (
    CreateChannelUseCase,
    DeleteChannelUseCase,
    UpdateChannelPreferenceUseCase,
)
from personal_finance.contexts.alerts.application.queries import (
    ChannelView,
    ListChannelsUseCase,
)
from personal_finance.contexts.alerts.domain.exceptions import (
    ChannelNotFoundError,
    TooManyChannelsError,
)
from personal_finance.contexts.alerts.domain.value_objects import (
    AlertType,
    ChannelId,
    ChannelKind,
)
from personal_finance.contexts.alerts.infrastructure.events import (
    build_alerts_event_publisher,
)
from personal_finance.contexts.alerts.infrastructure.security.secret_generator import (
    SecretsTokenGenerator,
)
from personal_finance.contexts.alerts.infrastructure.security.secret_hashing import (
    Sha256TokenHasher,
)
from personal_finance.contexts.alerts.infrastructure.telegram.deep_link import (
    build_deep_link,
)
from personal_finance.contexts.alerts.presentation.http.dependencies import (
    alerts_are_configured,
    build_channel_repository,
    build_link_repository,
)
from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.shared.domain.value_objects import Currency, Money, UserId
from personal_finance.shared.infrastructure.config.settings import (
    get_alerts_settings,
)


router = APIRouter(prefix="/alerts", tags=["alerts"])

MAX_MINIMUM_AMOUNT_LENGTH = 32
_AMOUNT_PATTERN = r"^\d+(\.\d+)?$"


# ----------------------------------------------------------------------
# Payloads and responses
# ----------------------------------------------------------------------


class CreateChannelPayload(BaseModel):
    kind: ChannelKind = ChannelKind.TELEGRAM


class PreferenceResponse(BaseModel):
    alert_type: AlertType
    enabled: bool
    # A string, like money everywhere else that crosses a boundary here.
    minimum_amount: str | None = None
    minimum_currency: Currency | None = None


class ChannelResponse(BaseModel):
    """One channel, as its owner may see it.

    No token, and no chat id: `chat_hint` is the last few characters of the
    destination, which is all the owner needs to recognise which Telegram
    account is bound.
    """

    channel_id: str
    kind: ChannelKind
    status: str
    chat_hint: str | None
    label: str | None
    created_at: int
    verified_at: int | None
    preferences: list[PreferenceResponse]


class ChannelListResponse(BaseModel):
    channels: list[ChannelResponse]


class CreatedChannelResponse(BaseModel):
    """The new channel, and the one and only copy of its link.

    `link_url` is shown once. It is not stored — only its hash is — and no
    other endpoint can hand it back. Losing it means asking for another one,
    which is the whole difference between a one-time credential and a
    permanent one.
    """

    channel: ChannelResponse
    link_url: str
    expires_in_minutes: int


class PreferencePayload(BaseModel):
    alert_type: AlertType
    enabled: bool
    # A string for the same reason it leaves as one. `None` clears the floor.
    minimum_amount: str | None = Field(
        default=None,
        pattern=_AMOUNT_PATTERN,
        max_length=MAX_MINIMUM_AMOUNT_LENGTH,
    )
    minimum_currency: Currency = Currency.COP


# ----------------------------------------------------------------------
# Wiring
# ----------------------------------------------------------------------


@functools.lru_cache(maxsize=1)
def _build_create_channel_use_case() -> CreateChannelUseCase:
    settings = get_alerts_settings()

    return CreateChannelUseCase(
        channels=build_channel_repository(),
        links=build_link_repository(),
        tokens=SecretsTokenGenerator(),
        hasher=Sha256TokenHasher(),
        link_ttl_minutes=settings.link_ttl_minutes,
        max_channels_per_user=settings.max_channels_per_user,
    )


@functools.lru_cache(maxsize=1)
def _build_list_channels_use_case() -> ListChannelsUseCase:
    return ListChannelsUseCase(channels=build_channel_repository())


@functools.lru_cache(maxsize=1)
def _build_update_preference_use_case() -> UpdateChannelPreferenceUseCase:
    return UpdateChannelPreferenceUseCase(channels=build_channel_repository())


@functools.lru_cache(maxsize=1)
def _build_delete_channel_use_case() -> DeleteChannelUseCase:
    return DeleteChannelUseCase(
        channels=build_channel_repository(),
        links=build_link_repository(),
        publisher=build_alerts_event_publisher(),
    )


# The thin indirection every router here keeps: `Depends` points at these, so
# a test can replace one without reaching into the cache behind it.
def get_create_channel_use_case() -> CreateChannelUseCase:
    return _build_create_channel_use_case()


def get_list_channels_use_case() -> ListChannelsUseCase:
    return _build_list_channels_use_case()


def get_update_preference_use_case() -> UpdateChannelPreferenceUseCase:
    return _build_update_preference_use_case()


def get_delete_channel_use_case() -> DeleteChannelUseCase:
    return _build_delete_channel_use_case()


CurrentUser = Annotated[UserId, Depends(get_current_user_id)]


def require_alerts_configured() -> None:
    """Refuse to hand out a link this deployment could never complete.

    503 rather than 500, and rather than taking the app down at boot: a
    missing bot token makes alerts unavailable and nothing else. Reading the
    channels one already has still works — that is a question this deployment
    can answer.
    """
    if not alerts_are_configured():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=("Los avisos no están configurados en este despliegue todavía."),
        )


@contextlib.contextmanager
def _domain_errors() -> Generator[None]:
    try:
        yield
    except ChannelNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except TooManyChannelsError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error


def _channel(view: ChannelView) -> ChannelResponse:
    return ChannelResponse(
        channel_id=str(view.channel_id.value),
        kind=view.kind,
        status=view.status.value,
        chat_hint=view.chat_hint,
        label=view.label,
        created_at=view.created_at.as_epoch_seconds(),
        verified_at=(view.verified_at.as_epoch_seconds() if view.verified_at else None),
        preferences=[
            PreferenceResponse(
                alert_type=preference.alert_type,
                enabled=preference.enabled,
                minimum_amount=(
                    str(preference.minimum_amount.amount)
                    if preference.minimum_amount
                    else None
                ),
                minimum_currency=(
                    preference.minimum_amount.currency
                    if preference.minimum_amount
                    else None
                ),
            )
            for preference in view.preferences
        ],
    )


def _minimum_amount(payload: PreferencePayload) -> Money | None:
    if payload.minimum_amount is None:
        return None

    try:
        amount = Decimal(payload.minimum_amount)
    except InvalidOperation as error:  # pragma: no cover - the pattern catches it
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Not an amount",
        ) from error

    return Money(amount=amount, currency=payload.minimum_currency)


# ----------------------------------------------------------------------
# Endpoints
# ----------------------------------------------------------------------


@router.post(
    "/channels",
    response_model=CreatedChannelResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_alerts_configured)],
)
def create_channel(
    payload: CreateChannelPayload,
    user_id: CurrentUser,
    use_case: Annotated[
        CreateChannelUseCase,
        Depends(get_create_channel_use_case),
    ],
) -> CreatedChannelResponse:
    """Open a channel and hand back the link that will bind it.

    Following the link in Telegram is what verifies it — there is no code to
    type, and no chat id to look up. Asking again retires whatever attempt
    was outstanding, so one account never has two live links at once.
    """
    settings = get_alerts_settings()

    with _domain_errors():
        issued = use_case.execute(
            CreateChannelCommand(user_id=user_id, kind=payload.kind),
        )

    return CreatedChannelResponse(
        channel=_channel(ChannelView.of(issued.channel)),
        link_url=build_deep_link(
            bot_username=settings.telegram_bot_username,
            token=issued.token,
        ),
        expires_in_minutes=settings.link_ttl_minutes,
    )


@router.get("/channels", response_model=ChannelListResponse)
def list_channels(
    user_id: CurrentUser,
    use_case: Annotated[ListChannelsUseCase, Depends(get_list_channels_use_case)],
) -> ChannelListResponse:
    """Every channel this caller owns, bound or still waiting.

    Polled by the screen while a link is outstanding: binding happens in
    Telegram, where the page cannot see it, so asking is the only way it
    learns. Never carries the link — see this module's docstring.
    """
    return ChannelListResponse(
        channels=[_channel(view) for view in use_case.execute(user_id)],
    )


@router.patch("/channels/{channel_id}", response_model=ChannelResponse)
def update_preference(
    channel_id: str,
    payload: PreferencePayload,
    user_id: CurrentUser,
    use_case: Annotated[
        UpdateChannelPreferenceUseCase,
        Depends(get_update_preference_use_case),
    ],
) -> ChannelResponse:
    """Say how much of one kind of alert this channel wants.

    A floor is about noise, not about money: below it nothing is sent, which
    is what keeps a three-thousand-peso coffee from spending the attention a
    four-hundred-thousand-peso charge needs.
    """
    with _domain_errors():
        channel = use_case.execute(
            UpdateChannelPreferenceCommand(
                user_id=user_id,
                channel_id=_channel_id(channel_id),
                alert_type=payload.alert_type,
                enabled=payload.enabled,
                minimum_amount=_minimum_amount(payload),
            ),
        )

    return _channel(ChannelView.of(channel))


@router.delete("/channels/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_channel(
    channel_id: str,
    user_id: CurrentUser,
    use_case: Annotated[
        DeleteChannelUseCase,
        Depends(get_delete_channel_use_case),
    ],
) -> None:
    """Unlink a destination. Nothing more is sent to it, and the Telegram
    account it held is free to be bound again — by anyone."""
    with _domain_errors():
        use_case.execute(
            DeleteChannelCommand(user_id=user_id, channel_id=_channel_id(channel_id)),
        )


def _channel_id(raw: str) -> ChannelId:
    try:
        return ChannelId.from_string(raw)
    except ValueError as error:
        # A malformed id is answered the same way a stranger's id is: there
        # is no such channel here.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such channel",
        ) from error
