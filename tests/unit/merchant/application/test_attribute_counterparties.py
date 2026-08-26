"""The read another context joins its movements through."""

from collections.abc import Mapping, Sequence
import uuid

import pytest

from personal_finance.contexts.merchant.application.commands import (
    RecordSightingCommand,
)
from personal_finance.contexts.merchant.application.handlers import (
    ResolveMerchantUseCase,
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
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = UserId.from_string("22222222-2222-2222-2222-222222222222")
NOW = PosixTime.from_epoch_seconds(1_700_000_000)


class InMemoryMerchantRepository:
    def __init__(self) -> None:
        self.merchants: dict[tuple[UserId, MerchantId], Merchant] = {}
        self.reads = 0
        self.alias_lookups = 0

    def find(self, *, user_id: UserId, merchant_id: MerchantId) -> Merchant | None:
        return self.merchants.get((user_id, merchant_id))

    def find_by_alias(
        self,
        *,
        user_id: UserId,
        fingerprint: AliasFingerprint,
    ) -> Merchant | None:
        # Counted apart from `list_by_user`: the point of the index is that
        # answering one spelling does not read the whole partition.
        self.alias_lookups += 1

        return next(
            (
                merchant
                for (owner, _), merchant in self.merchants.items()
                if owner == user_id and merchant.has_alias(fingerprint)
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
        self.reads += 1

        return [
            merchant
            for (owner, _), merchant in self.merchants.items()
            if owner == user_id
        ]

    def save(self, merchant: Merchant) -> None:
        self.merchants[(merchant.user_id, merchant.id)] = merchant

    def delete(self, *, user_id: UserId, merchant_id: MerchantId) -> None:
        self.merchants.pop((user_id, merchant_id), None)


class InMemoryProcessedEventStore:
    def __init__(self) -> None:
        self.claimed: set[uuid.UUID] = set()

    def claim(self, *, user_id: UserId, event_id: uuid.UUID) -> bool:
        del user_id

        if event_id in self.claimed:
            return False

        self.claimed.add(event_id)

        return True


class NullPublisher:
    def publish(self, events: Sequence[Event]) -> None:
        del events


@pytest.fixture
def repository() -> InMemoryMerchantRepository:
    return InMemoryMerchantRepository()


@pytest.fixture
def use_case(repository: InMemoryMerchantRepository) -> AttributeCounterpartiesUseCase:
    return AttributeCounterpartiesUseCase(repository=repository)


def _sight(repository: InMemoryMerchantRepository, counterparty: str) -> None:
    """Resolve a spelling the way the worker does, so the read has something
    real to answer from rather than a merchant a test hand-built.
    """
    ResolveMerchantUseCase(
        repository=repository,
        processed_events=InMemoryProcessedEventStore(),
        event_publisher=NullPublisher(),
    ).execute(
        RecordSightingCommand(
            user_id=USER_ID,
            counterparty=counterparty,
            occurred_at=NOW,
            event_id=uuid.uuid4(),
        ),
    )


def test_a_spelling_that_was_resolved_comes_back_with_its_merchant(
    repository: InMemoryMerchantRepository,
    use_case: AttributeCounterpartiesUseCase,
) -> None:
    _sight(repository, "TIENDAS ARA 123")

    attributed = use_case.execute(
        user_id=USER_ID,
        counterparties=["TIENDAS ARA 123"],
    )

    assert attributed["TIENDAS ARA 123"].display_name == "Tiendas Ara 123"
    assert attributed["TIENDAS ARA 123"].category is MerchantCategory.UNCATEGORIZED
    # Nobody has looked at this grouping, and a movement should be able to say so.
    assert attributed["TIENDAS ARA 123"].needs_review is True


def test_the_answer_is_keyed_by_the_exact_text_handed_in(
    repository: InMemoryMerchantRepository,
    use_case: AttributeCounterpartiesUseCase,
) -> None:
    # A caller looks its own movement's counterparty back up; the key must be
    # what it passed, not the normalized form it does not hold.
    _sight(repository, "Almacén Éxito")

    attributed = use_case.execute(
        user_id=USER_ID,
        counterparties=["almacen exito", "Almacén Éxito"],
    )

    assert set(attributed) == {"almacen exito", "Almacén Éxito"}
    assert (
        attributed["almacen exito"].merchant_id
        == attributed["Almacén Éxito"].merchant_id
    )


def test_a_spelling_nobody_has_resolved_yet_is_simply_absent(
    use_case: AttributeCounterpartiesUseCase,
) -> None:
    # Its sighting may still be on the queue. Absent is the honest answer;
    # inventing a merchant here would show one that does not own the spelling.
    assert use_case.execute(user_id=USER_ID, counterparties=["NEQUI"]) == {}


def test_the_read_never_creates_a_merchant(
    repository: InMemoryMerchantRepository,
    use_case: AttributeCounterpartiesUseCase,
) -> None:
    use_case.execute(user_id=USER_ID, counterparties=["SOMETHING NEW"])

    assert repository.merchants == {}


def test_a_counterparty_with_no_recognisable_text_misses_instead_of_raising(
    use_case: AttributeCounterpartiesUseCase,
) -> None:
    # Punctuation alone builds no fingerprint. That is a lookup that misses,
    # not a request that fails.
    assert use_case.execute(user_id=USER_ID, counterparties=["***"]) == {}


def test_one_users_merchants_never_answer_for_another(
    repository: InMemoryMerchantRepository,
    use_case: AttributeCounterpartiesUseCase,
) -> None:
    _sight(repository, "TIENDAS ARA")

    assert use_case.execute(user_id=OTHER_USER_ID, counterparties=["TIENDAS ARA"]) == {}


def test_a_page_of_movements_costs_one_read_however_many_names_it_repeats(
    repository: InMemoryMerchantRepository,
    use_case: AttributeCounterpartiesUseCase,
) -> None:
    _sight(repository, "TIENDAS ARA")
    repository.reads = 0

    use_case.execute(
        user_id=USER_ID,
        counterparties=["TIENDAS ARA"] * 40,
    )

    assert repository.reads == 1


def test_one_spelling_is_a_point_lookup_not_a_read_of_every_merchant(
    repository: InMemoryMerchantRepository,
    use_case: AttributeCounterpartiesUseCase,
) -> None:
    # Opening one movement must not walk the user's whole merchant partition:
    # the alias index exists for exactly this question.
    _sight(repository, "TIENDAS ARA")
    repository.reads = 0
    repository.alias_lookups = 0

    attributed = use_case.execute(user_id=USER_ID, counterparties=["TIENDAS ARA"])

    assert attributed["TIENDAS ARA"].display_name == "Tiendas Ara"
    assert repository.reads == 0
    assert repository.alias_lookups == 1


def test_nothing_is_read_when_there_is_nothing_to_attribute(
    repository: InMemoryMerchantRepository,
    use_case: AttributeCounterpartiesUseCase,
) -> None:
    use_case.execute(user_id=USER_ID, counterparties=[])

    assert repository.reads == 0


def test_a_rename_reaches_every_past_movement_without_reprocessing_anything(
    repository: InMemoryMerchantRepository,
    use_case: AttributeCounterpartiesUseCase,
) -> None:
    # The whole reason the join is made on read: the grouping is the user's to
    # change, and a movement stores what the bank wrote.
    _sight(repository, "TIENDAS ARA 123")
    merchant = next(iter(repository.merchants.values()))
    merchant.rename("Ara")
    repository.save(merchant)

    attributed = use_case.execute(user_id=USER_ID, counterparties=["TIENDAS ARA 123"])

    assert attributed["TIENDAS ARA 123"].display_name == "Ara"
    assert attributed["TIENDAS ARA 123"].needs_review is False
