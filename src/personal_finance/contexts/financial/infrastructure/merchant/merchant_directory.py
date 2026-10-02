from __future__ import annotations

from collections.abc import Mapping, Sequence
import functools

from personal_finance.contexts.financial.application.ports import (
    MerchantAttribution,
    UnknownMerchantCategoryError,
)
from personal_finance.contexts.merchant.application.categories import CategoryCatalog
from personal_finance.contexts.merchant.application.commands import (
    ClassifyCounterpartyCommand,
)
from personal_finance.contexts.merchant.application.handlers import (
    ClassifyCounterpartyUseCase,
)
from personal_finance.contexts.merchant.application.queries import (
    AttributeCounterpartiesUseCase,
)
from personal_finance.contexts.merchant.domain.exceptions import UnknownCategoryError
from personal_finance.contexts.merchant.domain.value_objects import CategoryKey
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


class MerchantContextDirectory:
    """Adapts Financial's `MerchantDirectory` port to merchant's own use case.

    This is the one place Financial is allowed to know merchant exists. The
    application layer above depends only on the protocol, so removing this
    adapter — or answering from a cache, or from nothing at all — never
    touches a use case or a test.

    Translating here is the point: merchant's `MerchantId` and its
    `CategoryKey` stop at this line, and plain strings continue outwards.
    Financial groups movements by a category it never interprets, which is
    exactly the amount it should know about somebody else's vocabulary.
    """

    def __init__(
        self,
        *,
        use_case: AttributeCounterpartiesUseCase,
        catalog: CategoryCatalog,
        classify: ClassifyCounterpartyUseCase,
    ) -> None:
        self._use_case = use_case
        self._catalog = catalog
        self._classify = classify

    def attribute(
        self,
        *,
        user_id: UserId,
        counterparties: Sequence[str],
    ) -> Mapping[str, MerchantAttribution]:
        attributed = self._use_case.execute(
            user_id=user_id,
            counterparties=counterparties,
        )

        return {
            counterparty: MerchantAttribution(
                merchant_id=str(attribution.merchant_id.value),
                display_name=attribution.display_name,
                category=attribution.category.value,
                needs_review=attribution.needs_review,
            )
            for counterparty, attribution in attributed.items()
        }

    def categories(self, *, user_id: UserId) -> frozenset[str]:
        return frozenset(choice.key.value for choice in self._catalog.list(user_id))

    def category_labels(self, *, user_id: UserId) -> Mapping[str, str]:
        return {
            choice.key.value: choice.label for choice in self._catalog.list(user_id)
        }

    def classify(
        self,
        *,
        user_id: UserId,
        counterparty: str,
        category: str,
        occurred_at: PosixTime,
    ) -> MerchantAttribution | None:
        try:
            key = CategoryKey(value=category)
        except ValueError:
            # Shape alone is wrong, so no vocabulary could contain it. Same
            # answer as a category nobody has.
            raise UnknownMerchantCategoryError(f"No category {category!r}") from None

        try:
            merchant = self._classify.execute(
                ClassifyCounterpartyCommand(
                    user_id=user_id,
                    counterparty=counterparty,
                    category=key,
                    occurred_at=occurred_at,
                ),
            )
        except UnknownCategoryError as error:
            # Translated at the boundary: Merchant's exceptions stop here, the
            # same way its ids and its enums do.
            raise UnknownMerchantCategoryError(str(error)) from error
        except ValueError:
            # Text no fingerprint can be built from — punctuation, a bare
            # symbol. There is no merchant to make of it, and the movement
            # behind this call is still a perfectly good movement.
            return None

        return MerchantAttribution(
            merchant_id=str(merchant.id.value),
            display_name=merchant.display_name,
            category=merchant.category.value,
            needs_review=merchant.needs_review,
        )


@functools.lru_cache(maxsize=1)
def build_merchant_directory() -> MerchantContextDirectory:
    """Wire the adapter, here rather than in Financial's router.

    Knowing that merchant stores its data in DynamoDB is this package's whole
    job. A router that built the repository itself would put that knowledge in
    Financial's presentation layer, where a change to merchant's storage would
    reach an endpoint.
    """
    table_name = get_merchant_settings().merchants_table
    repository = DynamoDBMerchantRepository(
        client=get_dynamodb_client(),
        table_name=table_name,
    )
    catalog = CategoryCatalog(
        repository=DynamoDBCategoryRepository(
            client=get_dynamodb_client(),
            table_name=table_name,
        ),
    )

    return MerchantContextDirectory(
        use_case=AttributeCounterpartiesUseCase(repository=repository),
        catalog=catalog,
        classify=ClassifyCounterpartyUseCase(
            repository=repository,
            event_publisher=build_merchant_event_publisher(),
            categories=catalog,
        ),
    )
