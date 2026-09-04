from __future__ import annotations

from collections.abc import Mapping, Sequence
import dataclasses
from typing import Protocol
import uuid

from personal_finance.contexts.merchant.domain.categories import Category
from personal_finance.contexts.merchant.domain.entities import Merchant
from personal_finance.contexts.merchant.domain.value_objects import (
    AliasFingerprint,
    CategoryKey,
    CounterpartyKind,
    MerchantId,
    MerchantRootKey,
)
from personal_finance.shared.domain.value_objects import UserId


class MerchantRepository(Protocol):
    """Persistence port for `Merchant`.

    Every method takes the owner. Merchants are per-user, and an
    implementation that could answer without knowing whose data it is asked
    for would be one query away from showing somebody else's spending.
    """

    def find(self, *, user_id: UserId, merchant_id: MerchantId) -> Merchant | None:
        """Load one merchant, or None when this user has no such merchant."""
        ...

    def find_by_alias(
        self,
        *,
        user_id: UserId,
        fingerprint: AliasFingerprint,
    ) -> Merchant | None:
        """Resolve a spelling to the merchant that owns it.

        The hot path: once a spelling has been seen, every later sighting of
        it must answer from here, so an implementation must not scan.
        """
        ...

    def list_root_keys(self, user_id: UserId) -> Mapping[MerchantRootKey, MerchantId]:
        """Every grouping key this user's merchants are reachable by.

        Only consulted for a spelling nobody has seen before, which is rare
        once a user's merchants have settled.
        """
        ...

    def list_by_user(self, user_id: UserId) -> Sequence[Merchant]:
        """Every merchant this user owns, for their own list view."""
        ...

    def save(self, merchant: Merchant) -> None:
        """Create or replace a merchant and everything it is reachable by."""
        ...

    def delete(self, *, user_id: UserId, merchant_id: MerchantId) -> None:
        """Remove a merchant a merge emptied out."""
        ...


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class CategoryChoice:
    """One category a given user may file a merchant under.

    Both vocabularies arrive in this shape — the shipped ones and the user's
    own — so everything downstream of the catalogue, the endpoint and the
    prompt included, works from one list and cannot treat the halves
    differently.
    """

    key: CategoryKey
    label: str
    # False for the ones this user wrote. A screen tells them apart to say
    # which are theirs; nothing else in the system cares.
    shipped: bool


class CategoryRepository(Protocol):
    """Persistence port for the categories one user wrote for themselves.

    Per-user like everything else in this context. The shipped vocabulary is
    not stored at all — it is code, the same on every deployment — so an
    implementation only ever holds what somebody added.
    """

    def list_by_user(self, user_id: UserId) -> Sequence[Category]:
        """Every category this user created, in no particular order."""
        ...

    def find(self, *, user_id: UserId, key: CategoryKey) -> Category | None:
        """One of this user's own categories, or None.

        None for a key the app ships too: those are not stored, and asking
        for one here is asking whether this person owns it.
        """
        ...

    def add(self, category: Category) -> None:
        """Store a category whose name must not already be taken.

        Raises `DuplicateCategoryError` when it is. The check belongs here
        rather than in a use case that read the list first: two taps on the
        same button are two requests, and a read-then-write would let both
        through and leave this person with two categories reading the same.
        """
        ...

    def rename(self, category: Category, *, previous_label: str) -> None:
        """Save a category whose name changed, holding the same rule.

        Raises `DuplicateCategoryError` when the new name is already one of
        this user's. Nothing filed under the category moves — the key did not
        change, which is the entire reason it is not derived from the name.
        """
        ...

    def delete(self, category: Category) -> None:
        """Remove a category and free the name it was holding.

        The caller is responsible for what pointed at it: a merchant left
        naming a category that no longer exists reads as a bucket its owner
        never made.
        """
        ...


class ProcessedEventStore(Protocol):
    """Remembers which integration events have already been applied.

    Delivery is at-least-once, so the same `TransactionExtracted` can arrive
    twice. Without this a redelivery would count one purchase as two sightings
    of a merchant. It has to survive restarts, which is why it is a port and
    not a set in the worker.
    """

    def claim(self, *, user_id: UserId, event_id: uuid.UUID) -> bool:
        """Record the event and return True the first time only.

        The claim is written before the work, so a crash in between loses one
        sighting's counters. That is the safe direction: merchant identity is
        derived from the spelling and is recreated by the next sighting, while
        double-counting would quietly inflate a number the user reads.
        """
        ...


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MerchantCandidate:
    """One merchant the advisor may attach a new spelling to."""

    merchant_id: MerchantId
    display_name: str
    # The spellings already under it. What the bank wrote is often more
    # recognisable than the name, especially once a user has renamed it.
    aliases: tuple[str, ...]


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MerchantAdvice:
    """An opinion, never a decision.

    `parent` is None when the spelling belongs to none of the candidates,
    which is the common answer and an entirely good one.
    """

    parent: MerchantId | None
    category: CategoryKey


class MerchantAdvisor(Protocol):
    """Plan B for grouping and categorizing, consulted only after the
    deterministic tiers have all missed.

    Two questions in one: does this spelling belong to a merchant this user
    already has, and what kind of spending is it? The rules can only recognise
    the same name modulo noise; knowing that `BANCOLOMBIA NEQUI` and `NEQUI`
    are one company, or that a café is not a person, is world knowledge no
    normalizer has.

    Whatever it answers is recorded as a suggestion the user can undo — it
    never confirms a merchant, and it never overwrites a category somebody
    already set.
    """

    def advise(
        self,
        *,
        counterparty: str,
        kind: CounterpartyKind,
        candidates: Sequence[MerchantCandidate],
        categories: Sequence[CategoryChoice],
    ) -> MerchantAdvice | None:
        """Return an opinion, or None when there is nothing useful to say.

        An implementation must verify that any `parent` it returns is one of
        the candidates, and any `category` one of the categories, it was
        given: the answer comes from outside the system, and naming a merchant
        that was never offered is not a grouping, it is a fabrication.

        It must not raise when the model is unreachable — None is the answer
        for that too. By the time this is consulted the sighting has already
        been claimed as handled, so failing here would lose it entirely, and a
        merchant without a suggested parent or category is merely one the user
        has to sort out themselves.
        """
        ...
