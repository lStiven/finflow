"""The one place Financial knows merchant exists, and what stops there."""

from collections.abc import Mapping, Sequence

from personal_finance.contexts.financial.infrastructure.merchant.merchant_directory import (  # noqa: E501
    MerchantContextDirectory,
)
from personal_finance.contexts.merchant.application.categories import CategoryCatalog
from personal_finance.contexts.merchant.application.handlers import (
    ClassifyCounterpartyUseCase,
)
from personal_finance.contexts.merchant.application.queries import (
    AttributeCounterpartiesUseCase,
)
from personal_finance.contexts.merchant.domain.categories import Category
from personal_finance.contexts.merchant.domain.entities import Merchant
from personal_finance.contexts.merchant.domain.exceptions import (
    DuplicateCategoryError,
)
from personal_finance.contexts.merchant.domain.value_objects import (
    AliasFingerprint,
    CategoryKey,
    MerchantCategory,
    MerchantId,
    MerchantRootKey,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
NOW = PosixTime.from_epoch_seconds(1_700_000_000)


class InMemoryMerchantRepository:
    def __init__(self) -> None:
        self.merchants: dict[MerchantId, Merchant] = {}

    def find(self, *, user_id: UserId, merchant_id: MerchantId) -> Merchant | None:
        del user_id

        return self.merchants.get(merchant_id)

    def find_by_alias(
        self,
        *,
        user_id: UserId,
        fingerprint: AliasFingerprint,
    ) -> Merchant | None:
        return next(
            (
                merchant
                for merchant in self.list_by_user(user_id)
                if merchant.has_alias(fingerprint)
            ),
            None,
        )

    def list_root_keys(
        self,
        user_id: UserId,
    ) -> Mapping[MerchantRootKey, MerchantId]:
        del user_id

        return {}

    def list_by_user(self, user_id: UserId) -> Sequence[Merchant]:
        return [
            merchant
            for merchant in self.merchants.values()
            if merchant.user_id == user_id
        ]

    def save(self, merchant: Merchant) -> None:
        self.merchants[merchant.id] = merchant

    def delete(self, *, user_id: UserId, merchant_id: MerchantId) -> None:
        del user_id
        self.merchants.pop(merchant_id, None)


class InMemoryCategoryRepository:
    """Only what this adapter asks of it: nothing here creates a category."""

    def __init__(self) -> None:
        self.categories: dict[CategoryKey, Category] = {}

    def list_by_user(self, user_id: UserId) -> Sequence[Category]:
        return [
            category
            for category in self.categories.values()
            if category.user_id == user_id
        ]

    def find(self, *, user_id: UserId, key: CategoryKey) -> Category | None:
        category = self.categories.get(key)

        return category if category and category.user_id == user_id else None

    def add(self, category: Category) -> None:
        if category.id in self.categories:
            raise DuplicateCategoryError(f"{category.label!r} already exists")

        self.categories[category.id] = category

    def rename(self, category: Category, *, previous_label: str) -> None:
        del previous_label
        self.categories[category.id] = category

    def delete(self, category: Category) -> None:
        self.categories.pop(category.id, None)


class NullEventPublisher:
    def publish(self, events: Sequence[Event]) -> None:
        del events


def _directory(
    repository: InMemoryMerchantRepository,
    *,
    categories: InMemoryCategoryRepository | None = None,
) -> MerchantContextDirectory:
    catalog = CategoryCatalog(
        repository=categories or InMemoryCategoryRepository(),
    )

    return MerchantContextDirectory(
        use_case=AttributeCounterpartiesUseCase(repository=repository),
        catalog=catalog,
        classify=ClassifyCounterpartyUseCase(
            repository=repository,
            event_publisher=NullEventPublisher(),
            categories=catalog,
        ),
    )


def test_merchants_vocabulary_stops_at_the_adapter() -> None:
    # Financial groups by a category it never interprets, and holds an id as
    # text. Importing merchant's enum would give that context a veto over
    # renaming its own members.
    repository = InMemoryMerchantRepository()
    merchant = Merchant.seed(
        user_id=USER_ID,
        fingerprint=AliasFingerprint.from_raw("TIENDAS ARA 123"),
        raw_text="TIENDAS ARA 123",
        seen_at=NOW,
        display_name="Ara",
        category=CategoryKey.default(MerchantCategory.GROCERIES),
    )
    repository.save(merchant)

    attributed = _directory(repository).attribute(
        user_id=USER_ID,
        counterparties=["TIENDAS ARA 123"],
    )
    attribution = attributed["TIENDAS ARA 123"]

    assert attribution.merchant_id == str(merchant.id.value)
    assert attribution.category == "groceries"
    assert attribution.display_name == "Ara"


def test_a_counterparty_nothing_owns_is_absent_rather_than_an_error() -> None:
    attributed = _directory(InMemoryMerchantRepository()).attribute(
        user_id=USER_ID,
        counterparties=["PAGO NOMINA"],
    )

    assert attributed == {}


def test_every_category_comes_back_with_the_name_it_is_shown_by() -> None:
    categories = InMemoryCategoryRepository()
    own = Category.create(user_id=USER_ID, label="Gatos", created_at=NOW)
    categories.add(own)

    labels = _directory(
        InMemoryMerchantRepository(),
        categories=categories,
    ).category_labels(user_id=USER_ID)

    assert labels[own.id.value] == "Gatos"
    assert labels["groceries"] == "Groceries"
    assert set(labels) == _directory(
        InMemoryMerchantRepository(),
        categories=categories,
    ).categories(user_id=USER_ID)
