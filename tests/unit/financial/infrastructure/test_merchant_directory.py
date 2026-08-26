"""The one place Financial knows merchant exists, and what stops there."""

from collections.abc import Mapping, Sequence

from personal_finance.contexts.financial.infrastructure.merchant.merchant_directory import (  # noqa: E501
    MerchantContextDirectory,
)
from personal_finance.contexts.merchant.application.queries import (
    AttributeCounterpartiesUseCase,
)
from personal_finance.contexts.merchant.domain.entities import Merchant
from personal_finance.contexts.merchant.domain.value_objects import (
    AliasFingerprint,
    MerchantCategory,
    MerchantId,
    MerchantRootKey,
)
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


def _directory(
    repository: InMemoryMerchantRepository,
) -> MerchantContextDirectory:
    return MerchantContextDirectory(
        use_case=AttributeCounterpartiesUseCase(repository=repository),
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
        category=MerchantCategory.GROCERIES,
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
