"""Read side: what a merchant screen needs to draw itself.

The repository hands over everything one user owns and the filtering happens
here. That is a deliberate choice for this deployment's scale — a person has
tens or low hundreds of merchants, all in one partition — and it keeps search,
sorting and the review badge as one consistent answer instead of three
queries that can disagree. If a user ever outgrows it, this is the seam to
push down into the index.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import dataclasses
import enum

from personal_finance.contexts.merchant.application.ports import MerchantRepository
from personal_finance.contexts.merchant.domain.entities import Merchant
from personal_finance.contexts.merchant.domain.normalization import (
    normalize_counterparty,
)
from personal_finance.contexts.merchant.domain.value_objects import (
    AliasFingerprint,
    MerchantCategory,
    MerchantId,
)
from personal_finance.shared.domain.value_objects import UserId


DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


class MerchantSort(enum.Enum):
    NAME = "name"
    # Most recently seen first: what a user looks at after opening the app.
    LAST_SEEN = "last_seen"
    # Busiest first: where renaming one merchant pays off the most.
    TIMES_SEEN = "times_seen"


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MerchantQuery:
    user_id: UserId
    # Matched against the merchant's name and every spelling under it, so
    # searching for what the bank wrote finds the merchant the user renamed.
    search: str | None = None
    category: MerchantCategory | None = None
    needs_review: bool | None = None
    sort: MerchantSort = MerchantSort.LAST_SEEN
    limit: int = DEFAULT_PAGE_SIZE
    offset: int = 0


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MerchantPage:
    merchants: Sequence[Merchant]
    # How many matched the filter, so the UI can page without guessing.
    total: int
    # Across everything the user owns, filter or no filter: this is the badge
    # on the "needs review" tab, and it must not change when they search.
    needs_review: int


class ListMerchantsUseCase:
    def __init__(self, *, repository: MerchantRepository) -> None:
        self._repository = repository

    def execute(self, query: MerchantQuery) -> MerchantPage:
        owned = self._repository.list_by_user(query.user_id)
        matching = [merchant for merchant in owned if _matches(merchant, query=query)]
        _sort(matching, query.sort)
        limit = min(max(query.limit, 1), MAX_PAGE_SIZE)
        offset = max(query.offset, 0)

        return MerchantPage(
            merchants=matching[offset : offset + limit],
            total=len(matching),
            needs_review=sum(1 for merchant in owned if merchant.needs_review),
        )


class GetMerchantUseCase:
    def __init__(self, *, repository: MerchantRepository) -> None:
        self._repository = repository

    def execute(self, *, user_id: UserId, merchant_id: MerchantId) -> Merchant | None:
        return self._repository.find(user_id=user_id, merchant_id=merchant_id)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MerchantAttribution:
    """Which merchant one spelling belongs to, as much as another context
    needs to know of it.
    """

    merchant_id: MerchantId
    display_name: str
    category: MerchantCategory
    needs_review: bool


class AttributeCounterpartiesUseCase:
    """Resolve spellings to the merchants that already own them.

    This context's published read surface for the rest of the system: it is
    what lets a context holding movements say *which* merchant one was with,
    instead of only the raw text a bank happened to send.

    Read-only and exact, on purpose. `ResolveMerchantUseCase` is the writer —
    it derives, guesses and creates — and deciding a second time here would
    let a read invent a grouping the write side never recorded, showing a
    merchant that does not own that spelling. A counterparty nobody has
    resolved yet answers nothing, which is the honest answer while its
    sighting is still on the queue.
    """

    def __init__(self, *, repository: MerchantRepository) -> None:
        self._repository = repository

    def execute(
        self,
        *,
        user_id: UserId,
        counterparties: Sequence[str],
    ) -> Mapping[str, MerchantAttribution]:
        """Keyed by the exact text handed in, so a caller finds its own back.

        One spelling is a point lookup on the alias index, which is what that
        index is for. A page of them is one read of the user's merchants
        instead of one lookup each: a person has tens of merchants in a single
        partition, and a page of movements names the same handful over and
        over.
        """
        if not counterparties:
            return {}

        if len(counterparties) == 1:
            return self._one(user_id=user_id, counterparty=counterparties[0])

        by_alias = {
            alias.fingerprint: merchant
            for merchant in self._repository.list_by_user(user_id)
            for alias in merchant.aliases.values()
        }
        attributed: dict[str, MerchantAttribution] = {}

        for counterparty in counterparties:
            fingerprint = _fingerprint_or_none(counterparty)

            if fingerprint is None:
                continue

            merchant = by_alias.get(fingerprint)

            if merchant is not None:
                attributed[counterparty] = _attribution(merchant)

        return attributed

    def _one(
        self,
        *,
        user_id: UserId,
        counterparty: str,
    ) -> Mapping[str, MerchantAttribution]:
        fingerprint = _fingerprint_or_none(counterparty)

        if fingerprint is None:
            return {}

        merchant = self._repository.find_by_alias(
            user_id=user_id,
            fingerprint=fingerprint,
        )

        return {} if merchant is None else {counterparty: _attribution(merchant)}


def _matches(merchant: Merchant, *, query: MerchantQuery) -> bool:
    if query.category is not None and merchant.category is not query.category:
        return False

    if query.needs_review is not None and merchant.needs_review != query.needs_review:
        return False

    if not query.search:
        return True

    # Searched the same way counterparties are normalized, so "exito" finds
    # `Almacén Éxito` and `ALMACEN EXITO 123` alike.
    needle = normalize_counterparty(query.search)

    if not needle:
        return True

    haystack = [normalize_counterparty(merchant.display_name)]
    haystack.extend(alias.fingerprint.value for alias in merchant.aliases.values())

    return any(needle in candidate for candidate in haystack)


def _sort(merchants: list[Merchant], sort: MerchantSort) -> None:
    """Alphabetical ascending; everything else descending, because "most" and
    "most recent" are what a user is looking for.
    """
    if sort is MerchantSort.NAME:
        merchants.sort(key=lambda merchant: merchant.display_name.casefold())
    elif sort is MerchantSort.TIMES_SEEN:
        merchants.sort(key=lambda merchant: merchant.times_seen, reverse=True)
    else:
        merchants.sort(
            key=lambda merchant: merchant.last_seen.as_epoch_seconds(),
            reverse=True,
        )


def _attribution(merchant: Merchant) -> MerchantAttribution:
    return MerchantAttribution(
        merchant_id=merchant.id,
        display_name=merchant.display_name,
        category=merchant.category,
        needs_review=merchant.needs_review,
    )


def _fingerprint_or_none(counterparty: str) -> AliasFingerprint | None:
    """None for text no fingerprint can be built from.

    Punctuation alone reaches here from a movement whose counterparty no
    merchant could ever have been made of, and that is a lookup that misses,
    not a request that fails.
    """
    try:
        return AliasFingerprint.from_raw(counterparty)
    except ValueError:
        return None
