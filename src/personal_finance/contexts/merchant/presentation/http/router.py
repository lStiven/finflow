"""The merchant surface a frontend draws itself from.

Everything is scoped to the authenticated caller. Merchants are per-user, so
there is no endpoint that takes a user id: the token decides whose merchants
are read and whose can be edited, and a merchant belonging to somebody else is
reported as missing rather than as forbidden.
"""

from __future__ import annotations

from collections.abc import Generator
import contextlib
import functools
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, model_validator

from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.contexts.merchant.application.commands import (
    ConfirmMerchantCommand,
    EditMerchantCommand,
    MergeMerchantsCommand,
    MoveAliasCommand,
    SplitAliasCommand,
)
from personal_finance.contexts.merchant.application.handlers import (
    ConfirmMerchantUseCase,
    EditMerchantUseCase,
    MerchantNotFoundError,
    MergeMerchantsUseCase,
    MoveAliasUseCase,
    SameMerchantError,
    SplitAliasUseCase,
)
from personal_finance.contexts.merchant.application.queries import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    GetMerchantUseCase,
    ListMerchantsUseCase,
    MerchantQuery,
    MerchantSort,
)
from personal_finance.contexts.merchant.domain.entities import Merchant
from personal_finance.contexts.merchant.domain.exceptions import (
    LastAliasError,
    UnknownAliasError,
)
from personal_finance.contexts.merchant.domain.value_objects import (
    AliasFingerprint,
    MerchantAlias,
    MerchantCategory,
    MerchantId,
)
from personal_finance.contexts.merchant.infrastructure.events import (
    build_merchant_event_publisher,
)
from personal_finance.contexts.merchant.infrastructure.persistence.dynamodb import (
    DynamoDBMerchantRepository,
)
from personal_finance.shared.domain.value_objects import UserId
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import get_merchant_settings


router = APIRouter(prefix="/merchants", tags=["merchants"])

MAX_NAME_LENGTH = 120
MAX_FINGERPRINT_LENGTH = 512


class AliasResponse(BaseModel):
    """One child: a spelling that resolves to this merchant."""

    fingerprint: str
    raw_text: str
    root_key: str
    # `seed` | `derived` | `suggested` | `manual` — a review screen shows the
    # difference between what the system guessed and what the user decided.
    origin: str
    times_seen: int
    first_seen: int
    last_seen: int


class MerchantResponse(BaseModel):
    id: str
    display_name: str
    category: str
    status: str
    needs_review: bool
    alias_count: int
    # Sightings of the name, not money: what was spent is Financial's answer.
    times_seen: int
    first_seen: int
    last_seen: int


class MerchantDetailResponse(MerchantResponse):
    aliases: list[AliasResponse]


class MerchantListResponse(BaseModel):
    merchants: list[MerchantResponse]
    total: int
    # Across everything the user owns, so the badge does not move when they
    # type in the search box.
    needs_review: int
    limit: int
    offset: int


class CategoryResponse(BaseModel):
    value: str
    label: str


class CategoryListResponse(BaseModel):
    categories: list[CategoryResponse]


class EditMerchantPayload(BaseModel):
    display_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_NAME_LENGTH,
    )
    category: MerchantCategory | None = None

    @model_validator(mode="after")
    def _require_something_to_change(self) -> EditMerchantPayload:
        if self.display_name is None and self.category is None:
            raise ValueError("Provide a display_name, a category, or both")

        return self


class MoveAliasPayload(BaseModel):
    """Reattach one child to a different parent."""

    fingerprint: str = Field(min_length=1, max_length=MAX_FINGERPRINT_LENGTH)
    target_merchant_id: str = Field(min_length=1, max_length=64)


class SplitAliasPayload(BaseModel):
    """Pull one child out into a merchant of its own."""

    fingerprint: str = Field(min_length=1, max_length=MAX_FINGERPRINT_LENGTH)
    display_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_NAME_LENGTH,
    )
    category: MerchantCategory | None = None


class MergeMerchantsPayload(BaseModel):
    """The merchant in the path survives and takes everything this one had."""

    absorbed_merchant_id: str = Field(min_length=1, max_length=64)


@functools.lru_cache(maxsize=1)
def build_repository() -> DynamoDBMerchantRepository:
    return DynamoDBMerchantRepository(
        client=get_dynamodb_client(),
        table_name=get_merchant_settings().merchants_table,
    )


@functools.lru_cache(maxsize=1)
def _build_list_use_case() -> ListMerchantsUseCase:
    return ListMerchantsUseCase(repository=build_repository())


@functools.lru_cache(maxsize=1)
def _build_get_use_case() -> GetMerchantUseCase:
    return GetMerchantUseCase(repository=build_repository())


@functools.lru_cache(maxsize=1)
def _build_edit_use_case() -> EditMerchantUseCase:
    return EditMerchantUseCase(
        repository=build_repository(),
        event_publisher=build_merchant_event_publisher(),
    )


@functools.lru_cache(maxsize=1)
def _build_confirm_use_case() -> ConfirmMerchantUseCase:
    return ConfirmMerchantUseCase(
        repository=build_repository(),
        event_publisher=build_merchant_event_publisher(),
    )


@functools.lru_cache(maxsize=1)
def _build_move_use_case() -> MoveAliasUseCase:
    return MoveAliasUseCase(
        repository=build_repository(),
        event_publisher=build_merchant_event_publisher(),
    )


@functools.lru_cache(maxsize=1)
def _build_split_use_case() -> SplitAliasUseCase:
    return SplitAliasUseCase(
        repository=build_repository(),
        event_publisher=build_merchant_event_publisher(),
    )


@functools.lru_cache(maxsize=1)
def _build_merge_use_case() -> MergeMerchantsUseCase:
    return MergeMerchantsUseCase(
        repository=build_repository(),
        event_publisher=build_merchant_event_publisher(),
    )


def get_list_merchants_use_case() -> ListMerchantsUseCase:
    return _build_list_use_case()


def get_merchant_use_case() -> GetMerchantUseCase:
    return _build_get_use_case()


def get_edit_merchant_use_case() -> EditMerchantUseCase:
    return _build_edit_use_case()


def get_confirm_merchant_use_case() -> ConfirmMerchantUseCase:
    return _build_confirm_use_case()


def get_move_alias_use_case() -> MoveAliasUseCase:
    return _build_move_use_case()


def get_split_alias_use_case() -> SplitAliasUseCase:
    return _build_split_use_case()


def get_merge_merchants_use_case() -> MergeMerchantsUseCase:
    return _build_merge_use_case()


CurrentUser = Annotated[UserId, Depends(get_current_user_id)]


@router.get("/categories", response_model=CategoryListResponse)
def list_categories() -> CategoryListResponse:
    """The category vocabulary, for a dropdown that cannot drift from it."""
    return CategoryListResponse(
        categories=[
            CategoryResponse(
                value=category.value,
                label=category.value.replace("_", " ").capitalize(),
            )
            for category in MerchantCategory
        ],
    )


@router.get("", response_model=MerchantListResponse)
def list_merchants(
    user_id: CurrentUser,
    use_case: Annotated[ListMerchantsUseCase, Depends(get_list_merchants_use_case)],
    search: Annotated[str | None, Query(max_length=200)] = None,
    category: MerchantCategory | None = None,
    needs_review: bool | None = None,
    sort: MerchantSort = MerchantSort.LAST_SEEN,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MerchantListResponse:
    page = use_case.execute(
        MerchantQuery(
            user_id=user_id,
            search=search,
            category=category,
            needs_review=needs_review,
            sort=sort,
            limit=limit,
            offset=offset,
        ),
    )

    return MerchantListResponse(
        merchants=[_summary(merchant) for merchant in page.merchants],
        total=page.total,
        needs_review=page.needs_review,
        limit=limit,
        offset=offset,
    )


@router.get("/{merchant_id}", response_model=MerchantDetailResponse)
def get_merchant(
    merchant_id: str,
    user_id: CurrentUser,
    use_case: Annotated[GetMerchantUseCase, Depends(get_merchant_use_case)],
) -> MerchantDetailResponse:
    merchant = use_case.execute(
        user_id=user_id,
        merchant_id=_merchant_id(merchant_id),
    )

    if merchant is None:
        raise _not_found(merchant_id)

    return _detail(merchant)


@router.patch("/{merchant_id}", response_model=MerchantDetailResponse)
def edit_merchant(
    merchant_id: str,
    payload: EditMerchantPayload,
    user_id: CurrentUser,
    use_case: Annotated[EditMerchantUseCase, Depends(get_edit_merchant_use_case)],
) -> MerchantDetailResponse:
    """Rename a merchant, recategorize it, or both.

    Either counts as reviewing it: a user who named a merchant has looked at
    what it is, so it leaves the review queue.
    """
    with _domain_errors():
        merchant = use_case.execute(
            EditMerchantCommand(
                user_id=user_id,
                merchant_id=_merchant_id(merchant_id),
                display_name=payload.display_name,
                category=payload.category,
            ),
        )

    return _detail(merchant)


@router.post("/{merchant_id}/confirm", response_model=MerchantDetailResponse)
def confirm_merchant(
    merchant_id: str,
    user_id: CurrentUser,
    use_case: Annotated[ConfirmMerchantUseCase, Depends(get_confirm_merchant_use_case)],
) -> MerchantDetailResponse:
    """Accept a merchant as it stands, guessed children included."""
    with _domain_errors():
        merchant = use_case.execute(
            ConfirmMerchantCommand(
                user_id=user_id,
                merchant_id=_merchant_id(merchant_id),
            ),
        )

    return _detail(merchant)


@router.post("/{merchant_id}/aliases/move", response_model=MerchantDetailResponse)
def move_alias(
    merchant_id: str,
    payload: MoveAliasPayload,
    user_id: CurrentUser,
    use_case: Annotated[MoveAliasUseCase, Depends(get_move_alias_use_case)],
) -> MerchantDetailResponse:
    """Move one spelling to another merchant, and return the new parent.

    The correction is permanent: from here on that spelling resolves by exact
    match, and no rule re-derives where it belongs.
    """
    with _domain_errors():
        merchant = use_case.execute(
            MoveAliasCommand(
                user_id=user_id,
                merchant_id=_merchant_id(merchant_id),
                fingerprint=_fingerprint(payload.fingerprint),
                target_merchant_id=_merchant_id(payload.target_merchant_id),
            ),
        )

    return _detail(merchant)


@router.post(
    "/{merchant_id}/aliases/split",
    status_code=status.HTTP_201_CREATED,
    response_model=MerchantDetailResponse,
)
def split_alias(
    merchant_id: str,
    payload: SplitAliasPayload,
    user_id: CurrentUser,
    use_case: Annotated[SplitAliasUseCase, Depends(get_split_alias_use_case)],
) -> MerchantDetailResponse:
    """Pull one spelling out into a merchant of its own, and return it."""
    with _domain_errors():
        merchant = use_case.execute(
            SplitAliasCommand(
                user_id=user_id,
                merchant_id=_merchant_id(merchant_id),
                fingerprint=_fingerprint(payload.fingerprint),
                display_name=payload.display_name,
                category=payload.category,
            ),
        )

    return _detail(merchant)


@router.post("/{merchant_id}/merge", response_model=MerchantDetailResponse)
def merge_merchants(
    merchant_id: str,
    payload: MergeMerchantsPayload,
    user_id: CurrentUser,
    use_case: Annotated[MergeMerchantsUseCase, Depends(get_merge_merchants_use_case)],
) -> MerchantDetailResponse:
    """Fold one merchant into the one named in the path, which survives."""
    with _domain_errors():
        merchant = use_case.execute(
            MergeMerchantsCommand(
                user_id=user_id,
                merchant_id=_merchant_id(merchant_id),
                absorbed_merchant_id=_merchant_id(payload.absorbed_merchant_id),
            ),
        )

    return _detail(merchant)


def _summary(merchant: Merchant) -> MerchantResponse:
    return MerchantResponse(
        id=str(merchant.id.value),
        display_name=merchant.display_name,
        category=merchant.category.value,
        status=merchant.status.value,
        needs_review=merchant.needs_review,
        alias_count=len(merchant.aliases),
        times_seen=merchant.times_seen,
        first_seen=merchant.first_seen.as_epoch_seconds(),
        last_seen=merchant.last_seen.as_epoch_seconds(),
    )


def _detail(merchant: Merchant) -> MerchantDetailResponse:
    return MerchantDetailResponse(
        **_summary(merchant).model_dump(),
        aliases=[_alias(alias) for alias in merchant.children],
    )


def _alias(alias: MerchantAlias) -> AliasResponse:
    return AliasResponse(
        fingerprint=alias.fingerprint.value,
        raw_text=alias.raw_text,
        root_key=alias.root_key.value,
        origin=alias.origin.value,
        times_seen=alias.times_seen,
        first_seen=alias.first_seen.as_epoch_seconds(),
        last_seen=alias.last_seen.as_epoch_seconds(),
    )


def _merchant_id(value: str) -> MerchantId:
    try:
        return MerchantId.from_string(value)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error


def _fingerprint(value: str) -> AliasFingerprint:
    # `from_raw` rather than the bare constructor, so a client may send either
    # the fingerprint it read from the alias list or the raw text behind it.
    try:
        return AliasFingerprint.from_raw(value)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error


def _not_found(merchant_id: str) -> HTTPException:
    # Same answer whether it never existed or belongs to somebody else: one
    # user must not be able to probe another's merchants.
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"No merchant {merchant_id}",
    )


@contextlib.contextmanager
def _domain_errors() -> Generator[None]:
    """Turn the refusals the model makes into the answers HTTP has for them."""
    try:
        yield
    except MerchantNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except UnknownAliasError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except LastAliasError as error:
        # Not a bad request: the state of the merchant is what refuses it.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except SameMerchantError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
