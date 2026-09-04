"""The vocabulary a user files their merchants under.

Two halves, deliberately kept apart in storage and joined here. The shipped
categories are code — the same on every deployment, never written to a table,
never anybody's to delete — because an app whose dropdown starts empty
classifies nothing on the first run. The rest are rows one person added
because "Mercado" and "Compras" do not describe how they actually spend.

Everything that needs to know what a user may choose asks this module, which
is what keeps the endpoint, the edit command and the model's prompt from
holding three lists that drift.
"""

from __future__ import annotations

import dataclasses

from personal_finance.contexts.merchant.application.ports import (
    CategoryChoice,
    CategoryRepository,
    MerchantRepository,
)
from personal_finance.contexts.merchant.domain.categories import Category
from personal_finance.contexts.merchant.domain.exceptions import (
    ShippedCategoryError,
    UnknownCategoryError,
)
from personal_finance.contexts.merchant.domain.value_objects import (
    CategoryKey,
    MerchantCategory,
)
from personal_finance.shared.application.ports import EventPublisher
from personal_finance.shared.domain.value_objects import PosixTime, UserId


SHIPPED_CATEGORIES: tuple[CategoryChoice, ...] = tuple(
    CategoryChoice(
        key=CategoryKey.default(category),
        # English, like every other label this API publishes: `key` is the
        # stable half and a client showing another language builds its own
        # words from it. A user's own category is the exception — that label
        # is their text, and nobody gets to restate it.
        label=category.value.replace("_", " ").capitalize(),
        shipped=True,
    )
    for category in MerchantCategory
)


class CategoryCatalog:
    """What one user may choose from, and whether a given key is one of them.

    Reads on every validation. A person has a handful of categories in the
    partition their merchants already live in, and the alternative — trusting
    a key because it looks well-formed — is how a merchant ends up filed
    under a bucket that does not exist.
    """

    def __init__(self, *, repository: CategoryRepository) -> None:
        self._repository = repository

    def list(self, user_id: UserId) -> list[CategoryChoice]:
        """The shipped ones in their declared order, then the user's by name.

        The order is the dropdown's, and it puts the familiar ones where they
        have always been rather than sorting somebody's new category into the
        middle of them.
        """
        return [*SHIPPED_CATEGORIES, *map(_choice, self._sorted(user_id))]

    def resolve(self, *, user_id: UserId, key: CategoryKey) -> CategoryKey:
        """The key back, or a refusal naming the one that does not exist."""
        if any(choice.key == key for choice in self.list(user_id)):
            return key

        raise UnknownCategoryError(f"No category {key.value!r}")

    def _sorted(self, user_id: UserId) -> list[Category]:
        return sorted(
            self._repository.list_by_user(user_id),
            key=lambda category: category.label.casefold(),
        )


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class CategoryView:
    """One category as a screen reads it, rather than as a rule checks it."""

    key: CategoryKey
    label: str
    shipped: bool
    # None unless it was asked for. Counting means reading the user's
    # merchants, and every dropdown in the app reads this list — only the
    # screen that manages categories has any use for the number.
    usage: int | None = None


class ListCategoriesUseCase:
    """The vocabulary a screen draws from, optionally with how much of the
    user's data sits in each bucket.
    """

    def __init__(
        self,
        *,
        catalog: CategoryCatalog,
        merchants: MerchantRepository,
    ) -> None:
        self._catalog = catalog
        self._merchants = merchants

    def execute(
        self,
        user_id: UserId,
        *,
        with_usage: bool = False,
    ) -> list[CategoryView]:
        counts = self._counts(user_id) if with_usage else {}

        return [
            CategoryView(
                key=choice.key,
                label=choice.label,
                shipped=choice.shipped,
                usage=counts.get(choice.key, 0) if with_usage else None,
            )
            for choice in self._catalog.list(user_id)
        ]

    def _counts(self, user_id: UserId) -> dict[CategoryKey, int]:
        counts: dict[CategoryKey, int] = {}

        for merchant in self._merchants.list_by_user(user_id):
            counts[merchant.category] = counts.get(merchant.category, 0) + 1

        return counts


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class CreateCategoryCommand:
    user_id: UserId
    label: str


class CreateCategoryUseCase:
    """Adds one category to a user's own half of the vocabulary."""

    def __init__(self, *, repository: CategoryRepository) -> None:
        self._repository = repository

    def execute(self, command: CreateCategoryCommand, *, now: PosixTime) -> Category:
        category = Category.create(
            user_id=command.user_id,
            label=command.label,
            created_at=now,
        )
        # Refused by the write itself when the name is already taken, so two
        # taps on the same button cannot both win.
        self._repository.add(category)

        return category


def _choice(category: Category) -> CategoryChoice:
    return CategoryChoice(key=category.id, label=category.label, shipped=False)


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RenameCategoryCommand:
    user_id: UserId
    key: CategoryKey
    label: str


class RenameCategoryUseCase:
    """Fixes the name of a category, and nothing else.

    Cheap precisely because the key is not the name: no merchant is touched,
    no movement is re-attributed, and the correction shows up everywhere at
    once — every screen reads the label through this vocabulary.
    """

    def __init__(self, *, repository: CategoryRepository) -> None:
        self._repository = repository

    def execute(self, command: RenameCategoryCommand) -> Category:
        category = _require_own(
            self._repository,
            user_id=command.user_id,
            key=command.key,
        )
        previous = category.label
        category.rename(command.label)

        if category.label != previous:
            self._repository.rename(category, previous_label=previous)

        return category


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class DeleteCategoryCommand:
    user_id: UserId
    key: CategoryKey


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class DeletedCategory:
    """What was removed, and how much of the user's data moved with it."""

    key: CategoryKey
    label: str
    # Merchants that were filed under it and are now uncategorized. Their
    # movements follow, because a movement's category is its merchant's.
    merchants_moved: int


class DeleteCategoryUseCase:
    """Removes a category and puts everything filed under it back in the
    default bucket.

    The merchants move *first*. A merchant naming a category that no longer
    exists reads as a bucket its owner never made, and every screen that
    groups by category would show it under a raw key — so the window where
    that could be true is closed on the safe side: a crash after the move and
    before the delete leaves an empty category, which is a category the user
    can delete again.

    They are moved rather than deleted, and their status is left alone: the
    user removed a bucket, they did not review a single merchant, and marking
    them confirmed would empty their review queue on their behalf.
    """

    def __init__(
        self,
        *,
        repository: CategoryRepository,
        merchants: MerchantRepository,
        event_publisher: EventPublisher,
    ) -> None:
        self._repository = repository
        self._merchants = merchants
        self._event_publisher = event_publisher

    def execute(self, command: DeleteCategoryCommand) -> DeletedCategory:
        category = _require_own(
            self._repository,
            user_id=command.user_id,
            key=command.key,
        )
        filed = [
            merchant
            for merchant in self._merchants.list_by_user(command.user_id)
            if merchant.category == command.key
        ]

        for merchant in filed:
            merchant.uncategorize()
            self._merchants.save(merchant)

        self._repository.delete(category)

        for merchant in filed:
            self._event_publisher.publish(merchant.pull_events())

        return DeletedCategory(
            key=category.id,
            label=category.label,
            merchants_moved=len(filed),
        )


def _require_own(
    repository: CategoryRepository,
    *,
    user_id: UserId,
    key: CategoryKey,
) -> Category:
    """The user's own category, or the refusal that says why it is not."""
    if not key.is_custom:
        raise ShippedCategoryError(
            f"{key.value!r} is one of the categories the app ships, "
            "and those are the same for everybody",
        )

    category = repository.find(user_id=user_id, key=key)

    if category is None:
        # Same answer whether it never existed or belongs to somebody else.
        raise UnknownCategoryError(f"No category {key.value!r}")

    return category
