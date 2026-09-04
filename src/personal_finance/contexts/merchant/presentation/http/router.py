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
from personal_finance.contexts.merchant.application.categories import (
    CategoryCatalog,
    CategoryView,
    CreateCategoryCommand,
    CreateCategoryUseCase,
    DeleteCategoryCommand,
    DeleteCategoryUseCase,
    DeletedCategory,
    ListCategoriesUseCase,
    RenameCategoryCommand,
    RenameCategoryUseCase,
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
from personal_finance.contexts.merchant.domain.categories import (
    MAX_CATEGORY_LABEL_LENGTH,
)
from personal_finance.contexts.merchant.domain.entities import Merchant
from personal_finance.contexts.merchant.domain.exceptions import (
    DuplicateCategoryError,
    InvalidCategoryLabelError,
    LastAliasError,
    ShippedCategoryError,
    UnknownAliasError,
    UnknownCategoryError,
)
from personal_finance.contexts.merchant.domain.value_objects import (
    AliasFingerprint,
    AliasOrigin,
    CategoryKey,
    CounterpartyKind,
    MerchantAlias,
    MerchantId,
    MerchantStatus,
)
from personal_finance.contexts.merchant.infrastructure.events import (
    build_merchant_event_publisher,
)
from personal_finance.contexts.merchant.infrastructure.persistence.dynamodb import (
    DynamoDBCategoryRepository,
    DynamoDBMerchantRepository,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import get_merchant_settings
from personal_finance.shared.presentation.catalog import CatalogOption, options


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
    """One category this caller may file a merchant under.

    `label` is English for the ones the app ships — `value` is the stable half
    and a client showing another language builds its own words from it — and
    the user's own text for the ones they wrote, which nobody gets to restate.
    `custom` says which of the two it is, so a screen can render the second
    kind as it arrived.
    """

    value: str
    label: str
    custom: bool
    # How many of this caller's merchants sit in it, and null unless
    # `with_usage` asked — counting means reading their merchants, and every
    # dropdown in the app reads this list. Movements follow their merchant,
    # so this is also how much spending moves if the category is removed.
    usage: int | None = None


class CategoryListResponse(BaseModel):
    categories: list[CategoryResponse]


class CategoryNamePayload(BaseModel):
    """A category's name, on the way in. Held short deliberately: it is read
    in a dropdown, in a chip beside a movement and in a chart legend on a
    phone, and a long one makes all three unreadable.
    """

    label: str = Field(min_length=1, max_length=MAX_CATEGORY_LABEL_LENGTH)


class DeletedCategoryResponse(BaseModel):
    """What was removed, and how much moved with it."""

    value: str
    label: str
    # Merchants that were filed under it and are now uncategorized, along with
    # every movement of theirs. Zero is the common answer for a category
    # created by mistake and removed straight away.
    merchants_moved: int


class MerchantCatalogResponse(BaseModel):
    """Every vocabulary this context's endpoints accept or return.

    Categories are not among them, and cannot be: half of that vocabulary
    belongs to whoever is asking, and this answer is the same for everybody.
    `GET /merchants/categories` is the one place to read it from.
    """

    sorts: list[CatalogOption]
    statuses: list[CatalogOption]
    alias_origins: list[CatalogOption]
    counterparty_kinds: list[CatalogOption]


class EditMerchantPayload(BaseModel):
    display_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_NAME_LENGTH,
    )
    category: str | None = Field(default=None, max_length=64)

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
    category: str | None = Field(default=None, max_length=64)


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
def build_category_repository() -> DynamoDBCategoryRepository:
    return DynamoDBCategoryRepository(
        client=get_dynamodb_client(),
        table_name=get_merchant_settings().merchants_table,
    )


@functools.lru_cache(maxsize=1)
def build_category_catalog() -> CategoryCatalog:
    return CategoryCatalog(repository=build_category_repository())


@functools.lru_cache(maxsize=1)
def _build_create_category_use_case() -> CreateCategoryUseCase:
    return CreateCategoryUseCase(repository=build_category_repository())


@functools.lru_cache(maxsize=1)
def _build_list_categories_use_case() -> ListCategoriesUseCase:
    return ListCategoriesUseCase(
        catalog=build_category_catalog(),
        merchants=build_repository(),
    )


@functools.lru_cache(maxsize=1)
def _build_rename_category_use_case() -> RenameCategoryUseCase:
    return RenameCategoryUseCase(repository=build_category_repository())


@functools.lru_cache(maxsize=1)
def _build_delete_category_use_case() -> DeleteCategoryUseCase:
    return DeleteCategoryUseCase(
        repository=build_category_repository(),
        merchants=build_repository(),
        event_publisher=build_merchant_event_publisher(),
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
        categories=build_category_catalog(),
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
        categories=build_category_catalog(),
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


def get_category_catalog() -> CategoryCatalog:
    return build_category_catalog()


def get_create_category_use_case() -> CreateCategoryUseCase:
    return _build_create_category_use_case()


def get_list_categories_use_case() -> ListCategoriesUseCase:
    return _build_list_categories_use_case()


def get_rename_category_use_case() -> RenameCategoryUseCase:
    return _build_rename_category_use_case()


def get_delete_category_use_case() -> DeleteCategoryUseCase:
    return _build_delete_category_use_case()


CurrentUser = Annotated[UserId, Depends(get_current_user_id)]


@router.get("/categories", response_model=CategoryListResponse)
def list_categories(
    user_id: CurrentUser,
    use_case: Annotated[ListCategoriesUseCase, Depends(get_list_categories_use_case)],
    with_usage: bool = False,
) -> CategoryListResponse:
    """Everything this caller may file a merchant under, in dropdown order.

    Authenticated because half of the answer is theirs: the categories the app
    ships, then the ones they wrote for themselves.

    `with_usage` adds how many merchants sit in each, which costs a read of
    this caller's merchants — so it is asked for rather than always paid, and
    only the screen that manages categories has any use for it.
    """
    return CategoryListResponse(
        categories=[
            _category(view) for view in use_case.execute(user_id, with_usage=with_usage)
        ],
    )


@router.post(
    "/categories",
    response_model=CategoryResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_category(
    payload: CategoryNamePayload,
    user_id: CurrentUser,
    use_case: Annotated[CreateCategoryUseCase, Depends(get_create_category_use_case)],
) -> CategoryResponse:
    """Add a category of one's own, for spending the shipped list does not
    describe.

    The name is the whole request: the key its merchants are stored under is
    derived from it, and is not something a client chooses or can change
    later.
    """
    with _domain_errors():
        category = use_case.execute(
            CreateCategoryCommand(user_id=user_id, label=payload.label),
            now=PosixTime.now(),
        )

    return CategoryResponse(value=category.id.value, label=category.label, custom=True)


@router.patch("/categories/{category_key:path}", response_model=CategoryResponse)
def rename_category(
    category_key: str,
    payload: CategoryNamePayload,
    user_id: CurrentUser,
    use_case: Annotated[RenameCategoryUseCase, Depends(get_rename_category_use_case)],
) -> CategoryResponse:
    """Fix the name of a category of one's own.

    Nothing filed under it moves: the value merchants are stored under is not
    the name and never was, which is what makes correcting a typo one write
    instead of a rewrite of everything in that bucket. The new name shows up
    everywhere at once.

    The categories the app ships are not editable — they are the same for
    everybody — and naming one here is a 409.
    """
    with _domain_errors():
        category = use_case.execute(
            RenameCategoryCommand(
                user_id=user_id,
                key=_category_key(category_key),
                label=payload.label,
            ),
        )

    return CategoryResponse(value=category.id.value, label=category.label, custom=True)


@router.delete(
    "/categories/{category_key:path}", response_model=DeletedCategoryResponse
)
def delete_category(
    category_key: str,
    user_id: CurrentUser,
    use_case: Annotated[DeleteCategoryUseCase, Depends(get_delete_category_use_case)],
) -> DeletedCategoryResponse:
    """Remove a category of one's own, and say what moved.

    Every merchant filed under it goes back to `uncategorized`, and their
    movements follow — a movement's category is its merchant's, joined when an
    answer is read. Nothing is deleted but the category itself.

    Those merchants keep whatever review status they had. Removing a bucket is
    not reviewing what was in it, and marking them confirmed would empty
    somebody's review queue on their behalf.
    """
    with _domain_errors():
        removed = use_case.execute(
            DeleteCategoryCommand(
                user_id=user_id,
                key=_category_key(category_key),
            ),
        )

    return _deleted(removed)


@router.get("/catalog", response_model=MerchantCatalogResponse)
def get_catalog() -> MerchantCatalogResponse:
    """What a client may send, and what the words in a response mean.

    Categories are not here — they depend on who is asking. Read them from
    `GET /merchants/categories`.
    """
    return MerchantCatalogResponse(
        sorts=options(MerchantSort),
        statuses=options(MerchantStatus),
        alias_origins=options(AliasOrigin),
        counterparty_kinds=options(CounterpartyKind),
    )


@router.get("", response_model=MerchantListResponse)
def list_merchants(
    user_id: CurrentUser,
    use_case: Annotated[ListMerchantsUseCase, Depends(get_list_merchants_use_case)],
    catalog: Annotated[CategoryCatalog, Depends(get_category_catalog)],
    search: Annotated[str | None, Query(max_length=200)] = None,
    category: Annotated[str | None, Query(max_length=64)] = None,
    needs_review: bool | None = None,
    sort: MerchantSort = MerchantSort.LAST_SEEN,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = DEFAULT_PAGE_SIZE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MerchantListResponse:
    with _domain_errors():
        # Refused rather than answered with an empty page: on a screen about
        # money, "nothing here" and "you asked for a bucket that does not
        # exist" must not look the same.
        wanted = (
            None
            if category is None
            else catalog.resolve(user_id=user_id, key=_category_key(category))
        )

    page = use_case.execute(
        MerchantQuery(
            user_id=user_id,
            search=search,
            category=wanted,
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
                category=_optional_category_key(payload.category),
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
                category=_optional_category_key(payload.category),
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


def _category(view: CategoryView) -> CategoryResponse:
    return CategoryResponse(
        value=view.key.value,
        label=view.label,
        custom=not view.shipped,
        usage=view.usage,
    )


def _deleted(removed: DeletedCategory) -> DeletedCategoryResponse:
    return DeletedCategoryResponse(
        value=removed.key.value,
        label=removed.label,
        merchants_moved=removed.merchants_moved,
    )


def _category_key(value: str) -> CategoryKey:
    try:
        return CategoryKey(value=value)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error


def _optional_category_key(value: str | None) -> CategoryKey | None:
    return None if value is None else _category_key(value)


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
    except DuplicateCategoryError as error:
        # The name is fine; it is the state of this user's vocabulary that
        # refuses it, and asking again will refuse it again.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except ShippedCategoryError as error:
        # Well-formed, and refused by what the world is rather than by what
        # was asked: those categories are code, not this person's data.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except (InvalidCategoryLabelError, UnknownCategoryError) as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(error),
        ) from error
