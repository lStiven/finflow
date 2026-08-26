from __future__ import annotations

from collections.abc import Mapping, Sequence
import functools

from personal_finance.contexts.financial.application.ports import (
    MerchantAttribution,
)
from personal_finance.contexts.merchant.application.queries import (
    AttributeCounterpartiesUseCase,
)
from personal_finance.contexts.merchant.domain.value_objects import MerchantCategory
from personal_finance.contexts.merchant.infrastructure.persistence.dynamodb import (
    DynamoDBMerchantRepository,
)
from personal_finance.shared.domain.value_objects import UserId
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import get_merchant_settings


class MerchantContextDirectory:
    """Adapts Financial's `MerchantDirectory` port to merchant's own use case.

    This is the one place Financial is allowed to know merchant exists. The
    application layer above depends only on the protocol, so removing this
    adapter — or answering from a cache, or from nothing at all — never
    touches a use case or a test.

    Translating here is the point: merchant's `MerchantId` and its
    `MerchantCategory` stop at this line, and plain strings continue outwards.
    Financial groups movements by a category it never interprets, which is
    exactly the amount it should know about somebody else's vocabulary.
    """

    def __init__(self, *, use_case: AttributeCounterpartiesUseCase) -> None:
        self._use_case = use_case

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

    def categories(self) -> frozenset[str]:
        return frozenset(category.value for category in MerchantCategory)


@functools.lru_cache(maxsize=1)
def build_merchant_directory() -> MerchantContextDirectory:
    """Wire the adapter, here rather than in Financial's router.

    Knowing that merchant stores its data in DynamoDB is this package's whole
    job. A router that built the repository itself would put that knowledge in
    Financial's presentation layer, where a change to merchant's storage would
    reach an endpoint.
    """
    return MerchantContextDirectory(
        use_case=AttributeCounterpartiesUseCase(
            repository=DynamoDBMerchantRepository(
                client=get_dynamodb_client(),
                table_name=get_merchant_settings().merchants_table,
            ),
        ),
    )
