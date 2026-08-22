"""How a counterparty finds its merchant, tier by tier."""

from collections.abc import Mapping, Sequence
import uuid

import pytest

from personal_finance.contexts.merchant.application.commands import (
    EditMerchantCommand,
    MergeMerchantsCommand,
    MoveAliasCommand,
    RecordSightingCommand,
    SplitAliasCommand,
)
from personal_finance.contexts.merchant.application.handlers import (
    EditMerchantUseCase,
    MerchantNotFoundError,
    MergeMerchantsUseCase,
    MoveAliasUseCase,
    Resolution,
    ResolveMerchantUseCase,
    SameMerchantError,
    SplitAliasUseCase,
)
from personal_finance.contexts.merchant.application.ports import (
    MerchantAdvice,
    MerchantCandidate,
)
from personal_finance.contexts.merchant.application.queries import (
    ListMerchantsUseCase,
    MerchantQuery,
)
from personal_finance.contexts.merchant.domain.entities import Merchant
from personal_finance.contexts.merchant.domain.value_objects import (
    AliasFingerprint,
    AliasOrigin,
    CounterpartyKind,
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
    """Mirrors the storage layout: the merchant record, plus separate pointer
    entries for every spelling and grouping key it is reachable by.

    Keeping the pointers separate is not decoration — it is what lets a test
    reproduce a merchant written without them, which is the state a crash
    between the two writes leaves behind.
    """

    def __init__(self) -> None:
        self.merchants: dict[tuple[UserId, MerchantId], Merchant] = {}
        self.alias_pointers: dict[tuple[UserId, str], MerchantId] = {}
        self.root_pointers: dict[tuple[UserId, str], MerchantId] = {}

    def find(self, *, user_id: UserId, merchant_id: MerchantId) -> Merchant | None:
        return self.merchants.get((user_id, merchant_id))

    def find_by_alias(
        self,
        *,
        user_id: UserId,
        fingerprint: AliasFingerprint,
    ) -> Merchant | None:
        merchant_id = self.alias_pointers.get((user_id, fingerprint.value))

        if merchant_id is None:
            return None

        merchant = self.find(user_id=user_id, merchant_id=merchant_id)

        return merchant if merchant and merchant.has_alias(fingerprint) else None

    def list_root_keys(self, user_id: UserId) -> Mapping[MerchantRootKey, MerchantId]:
        return {
            MerchantRootKey(value=root_key): merchant_id
            for (owner, root_key), merchant_id in self.root_pointers.items()
            if owner == user_id
        }

    def list_by_user(self, user_id: UserId) -> Sequence[Merchant]:
        return [
            merchant
            for (owner, _), merchant in self.merchants.items()
            if owner == user_id
        ]

    def save(self, merchant: Merchant) -> None:
        self.merchants[(merchant.user_id, merchant.id)] = merchant

        for alias in merchant.children:
            self.alias_pointers[(merchant.user_id, alias.fingerprint.value)] = (
                merchant.id
            )

        for root_key in merchant.root_keys:
            self.root_pointers[(merchant.user_id, root_key.value)] = merchant.id

    def delete(self, *, user_id: UserId, merchant_id: MerchantId) -> None:
        self.merchants.pop((user_id, merchant_id), None)


class InMemoryProcessedEventStore:
    def __init__(self) -> None:
        self.claimed: set[tuple[UserId, uuid.UUID]] = set()

    def claim(self, *, user_id: UserId, event_id: uuid.UUID) -> bool:
        key = (user_id, event_id)

        if key in self.claimed:
            return False

        self.claimed.add(key)

        return True


class RecordingEventPublisher:
    def __init__(self) -> None:
        self.published: list[Event] = []

    def publish(self, events: Sequence[Event]) -> None:
        self.published.extend(events)


@pytest.fixture
def repository() -> InMemoryMerchantRepository:
    return InMemoryMerchantRepository()


@pytest.fixture
def publisher() -> RecordingEventPublisher:
    return RecordingEventPublisher()


@pytest.fixture
def use_case(
    repository: InMemoryMerchantRepository,
    publisher: RecordingEventPublisher,
) -> ResolveMerchantUseCase:
    return ResolveMerchantUseCase(
        repository=repository,
        processed_events=InMemoryProcessedEventStore(),
        event_publisher=publisher,
    )


def _sighting(
    counterparty: str,
    *,
    user_id: UserId = USER_ID,
    kind: CounterpartyKind = CounterpartyKind.BUSINESS,
    event_id: uuid.UUID | None = None,
) -> RecordSightingCommand:
    return RecordSightingCommand(
        user_id=user_id,
        counterparty=counterparty,
        occurred_at=NOW,
        event_id=event_id or uuid.uuid4(),
        kind=kind,
    )


def test_an_unknown_counterparty_becomes_a_merchant(
    use_case: ResolveMerchantUseCase,
) -> None:
    result = use_case.execute(_sighting("TIENDAS ARA 123"))

    assert result.resolution is Resolution.CREATED
    assert result.merchant is not None
    assert result.merchant.display_name == "Tiendas Ara 123"


def test_the_same_spelling_again_resolves_to_the_same_merchant(
    use_case: ResolveMerchantUseCase,
) -> None:
    first = use_case.execute(_sighting("TIENDAS ARA 123"))
    second = use_case.execute(_sighting("Tiendas Ara 123"))

    assert second.resolution is Resolution.KNOWN
    assert first.merchant is not None
    assert second.merchant is not None
    assert second.merchant.id == first.merchant.id
    assert second.merchant.times_seen == 2


def test_a_new_spelling_of_the_same_name_joins_it_as_a_child(
    use_case: ResolveMerchantUseCase,
) -> None:
    parent = use_case.execute(_sighting("TIENDAS ARA 123"))
    child = use_case.execute(_sighting("ARA CALLE 80"))

    assert child.resolution is Resolution.DERIVED
    assert parent.merchant is not None
    assert child.merchant is not None
    assert child.merchant.id == parent.merchant.id
    assert len(child.merchant.children) == 2


def test_a_branded_variant_is_attached_but_flagged_for_review(
    use_case: ResolveMerchantUseCase,
) -> None:
    use_case.execute(_sighting("EXITO"))
    result = use_case.execute(_sighting("EXITO EXPRESS BOGOTA"))

    assert result.resolution is Resolution.SUGGESTED
    assert result.merchant is not None
    assert result.merchant.needs_review
    alias = result.merchant.aliases[AliasFingerprint.from_raw("EXITO EXPRESS BOGOTA")]
    assert alias.origin is AliasOrigin.SUGGESTED


def test_a_transfer_is_never_grouped_by_a_shared_first_name(
    use_case: ResolveMerchantUseCase,
) -> None:
    # A person is not a sub-brand of another person.
    use_case.execute(_sighting("JUAN VALDEZ", kind=CounterpartyKind.UNKNOWN))
    result = use_case.execute(
        _sighting("JUAN VALDEZ PEREZ", kind=CounterpartyKind.UNKNOWN),
    )

    assert result.resolution is Resolution.CREATED


def test_the_closest_parent_wins_a_suggestion(
    use_case: ResolveMerchantUseCase,
) -> None:
    use_case.execute(_sighting("EXITO"))
    express = use_case.execute(_sighting("EXITO EXPRESS"))
    result = use_case.execute(_sighting("EXITO EXPRESS SUBA"))

    assert express.merchant is not None
    assert result.merchant is not None
    assert result.merchant.id == express.merchant.id


def test_a_redelivered_event_is_not_counted_twice(
    use_case: ResolveMerchantUseCase,
) -> None:
    event_id = uuid.uuid4()
    first = use_case.execute(_sighting("TIENDAS ARA", event_id=event_id))
    replay = use_case.execute(_sighting("TIENDAS ARA", event_id=event_id))

    assert replay.resolution is Resolution.DUPLICATE
    assert first.merchant is not None
    assert first.merchant.times_seen == 1


def test_one_users_merchants_never_answer_for_another(
    use_case: ResolveMerchantUseCase,
) -> None:
    mine = use_case.execute(_sighting("TIENDAS ARA", user_id=USER_ID))
    theirs = use_case.execute(_sighting("TIENDAS ARA", user_id=OTHER_USER_ID))

    assert mine.merchant is not None
    assert theirs.merchant is not None
    assert mine.merchant.id != theirs.merchant.id


def test_creating_a_merchant_announces_it(
    use_case: ResolveMerchantUseCase,
    publisher: RecordingEventPublisher,
) -> None:
    use_case.execute(_sighting("TIENDAS ARA"))

    assert [type(event).__name__ for event in publisher.published] == [
        "MerchantIdentified",
        "MerchantAliasLinked",
    ]


def test_a_grouping_key_pointing_at_nothing_does_not_break_the_next_sighting(
    use_case: ResolveMerchantUseCase,
    repository: InMemoryMerchantRepository,
) -> None:
    # A merchant is written before the keys that point at it, so a crash in
    # between can leave a key aimed at nothing.
    created = use_case.execute(_sighting("TIENDAS ARA"))
    assert created.merchant is not None
    repository.merchants.pop((USER_ID, created.merchant.id))

    result = use_case.execute(_sighting("ARA CALLE 80"))

    assert result.resolution is Resolution.CREATED


@pytest.fixture
def editing(
    repository: InMemoryMerchantRepository,
    publisher: RecordingEventPublisher,
) -> EditMerchantUseCase:
    return EditMerchantUseCase(repository=repository, event_publisher=publisher)


@pytest.fixture
def moving(
    repository: InMemoryMerchantRepository,
    publisher: RecordingEventPublisher,
) -> MoveAliasUseCase:
    return MoveAliasUseCase(repository=repository, event_publisher=publisher)


@pytest.fixture
def splitting(
    repository: InMemoryMerchantRepository,
    publisher: RecordingEventPublisher,
) -> SplitAliasUseCase:
    return SplitAliasUseCase(repository=repository, event_publisher=publisher)


@pytest.fixture
def merging(
    repository: InMemoryMerchantRepository,
    publisher: RecordingEventPublisher,
) -> MergeMerchantsUseCase:
    return MergeMerchantsUseCase(repository=repository, event_publisher=publisher)


def test_a_user_can_rename_and_categorize_a_merchant(
    use_case: ResolveMerchantUseCase,
    editing: EditMerchantUseCase,
) -> None:
    created = use_case.execute(_sighting("TIENDAS ARA 123"))
    assert created.merchant is not None

    edited = editing.execute(
        EditMerchantCommand(
            user_id=USER_ID,
            merchant_id=created.merchant.id,
            display_name="Ara",
            category=MerchantCategory.GROCERIES,
        ),
    )

    assert edited.display_name == "Ara"
    assert edited.category is MerchantCategory.GROCERIES
    assert not edited.needs_review


def test_editing_somebody_elses_merchant_is_indistinguishable_from_a_typo(
    use_case: ResolveMerchantUseCase,
    editing: EditMerchantUseCase,
) -> None:
    theirs = use_case.execute(_sighting("TIENDAS ARA", user_id=OTHER_USER_ID))
    assert theirs.merchant is not None

    with pytest.raises(MerchantNotFoundError):
        editing.execute(
            EditMerchantCommand(
                user_id=USER_ID,
                merchant_id=theirs.merchant.id,
                display_name="Mine now",
            ),
        )


def test_moving_a_child_makes_the_correction_permanent(
    use_case: ResolveMerchantUseCase,
    moving: MoveAliasUseCase,
    repository: InMemoryMerchantRepository,
) -> None:
    # `EXITO EXPRESS` was guessed onto `EXITO`; the user says it is its own
    # business and moves it to a merchant that already exists.
    use_case.execute(_sighting("EXITO"))
    other = use_case.execute(_sighting("CARULLA"))
    guessed = use_case.execute(_sighting("EXITO EXPRESS"))
    assert other.merchant is not None
    assert guessed.merchant is not None

    moving.execute(
        MoveAliasCommand(
            user_id=USER_ID,
            merchant_id=guessed.merchant.id,
            fingerprint=AliasFingerprint.from_raw("EXITO EXPRESS"),
            target_merchant_id=other.merchant.id,
        ),
    )
    again = use_case.execute(_sighting("EXITO EXPRESS"))

    assert again.resolution is Resolution.KNOWN
    assert again.merchant is not None
    assert again.merchant.id == other.merchant.id
    assert repository.find(user_id=USER_ID, merchant_id=other.merchant.id) is not None


def test_a_moved_child_takes_its_history_with_it(
    use_case: ResolveMerchantUseCase,
    moving: MoveAliasUseCase,
) -> None:
    use_case.execute(_sighting("EXITO"))
    target = use_case.execute(_sighting("CARULLA"))
    use_case.execute(_sighting("EXITO EXPRESS"))
    guessed = use_case.execute(_sighting("EXITO EXPRESS"))
    assert target.merchant is not None
    assert guessed.merchant is not None

    moved = moving.execute(
        MoveAliasCommand(
            user_id=USER_ID,
            merchant_id=guessed.merchant.id,
            fingerprint=AliasFingerprint.from_raw("EXITO EXPRESS"),
            target_merchant_id=target.merchant.id,
        ),
    )

    alias = moved.aliases[AliasFingerprint.from_raw("EXITO EXPRESS")]
    assert alias.times_seen == 2
    assert alias.origin is AliasOrigin.MANUAL


def test_splitting_a_child_out_gives_it_a_merchant_of_its_own(
    use_case: ResolveMerchantUseCase,
    splitting: SplitAliasUseCase,
) -> None:
    use_case.execute(_sighting("EXITO"))
    guessed = use_case.execute(_sighting("EXITO SEGUROS"))
    assert guessed.merchant is not None

    created = splitting.execute(
        SplitAliasCommand(
            user_id=USER_ID,
            merchant_id=guessed.merchant.id,
            fingerprint=AliasFingerprint.from_raw("EXITO SEGUROS"),
            display_name="Éxito Seguros",
            category=MerchantCategory.FEES,
        ),
    )

    assert created.id != guessed.merchant.id
    assert created.display_name == "Éxito Seguros"
    assert created.category is MerchantCategory.FEES
    assert not created.needs_review


def test_a_split_child_stops_resolving_to_its_old_parent(
    use_case: ResolveMerchantUseCase,
    splitting: SplitAliasUseCase,
) -> None:
    use_case.execute(_sighting("EXITO"))
    guessed = use_case.execute(_sighting("EXITO SEGUROS"))
    assert guessed.merchant is not None

    created = splitting.execute(
        SplitAliasCommand(
            user_id=USER_ID,
            merchant_id=guessed.merchant.id,
            fingerprint=AliasFingerprint.from_raw("EXITO SEGUROS"),
        ),
    )
    again = use_case.execute(_sighting("EXITO SEGUROS"))

    assert again.merchant is not None
    assert again.merchant.id == created.id


def test_merging_folds_a_duplicate_into_the_merchant_that_survives(
    use_case: ResolveMerchantUseCase,
    merging: MergeMerchantsUseCase,
    repository: InMemoryMerchantRepository,
) -> None:
    # Nothing groups these two automatically — neither root extends the
    # other — but a user knows they are one business.
    survivor = use_case.execute(_sighting("NEQUI"))
    duplicate = use_case.execute(_sighting("BANCOLOMBIA NEQUI"))
    assert survivor.merchant is not None
    assert duplicate.merchant is not None

    merged = merging.execute(
        MergeMerchantsCommand(
            user_id=USER_ID,
            merchant_id=survivor.merchant.id,
            absorbed_merchant_id=duplicate.merchant.id,
        ),
    )

    assert len(merged.children) == 2
    assert repository.find(user_id=USER_ID, merchant_id=duplicate.merchant.id) is None


def test_a_merge_teaches_the_survivor_the_absorbed_spellings(
    use_case: ResolveMerchantUseCase,
    merging: MergeMerchantsUseCase,
) -> None:
    # Nothing groups these two automatically — neither root extends the
    # other — but a user knows they are one business.
    survivor = use_case.execute(_sighting("NEQUI"))
    duplicate = use_case.execute(_sighting("BANCOLOMBIA NEQUI"))
    assert survivor.merchant is not None
    assert duplicate.merchant is not None
    merging.execute(
        MergeMerchantsCommand(
            user_id=USER_ID,
            merchant_id=survivor.merchant.id,
            absorbed_merchant_id=duplicate.merchant.id,
        ),
    )

    # A later branch of the absorbed spelling lands on the survivor rather
    # than recreating the duplicate.
    later = use_case.execute(_sighting("BANCOLOMBIA NEQUI 123"))

    assert later.merchant is not None
    assert later.merchant.id == survivor.merchant.id


def test_a_merchant_cannot_be_merged_into_itself(
    use_case: ResolveMerchantUseCase,
    merging: MergeMerchantsUseCase,
) -> None:
    created = use_case.execute(_sighting("EXITO"))
    assert created.merchant is not None

    with pytest.raises(SameMerchantError):
        merging.execute(
            MergeMerchantsCommand(
                user_id=USER_ID,
                merchant_id=created.merchant.id,
                absorbed_merchant_id=created.merchant.id,
            ),
        )


def test_the_list_shows_only_this_users_merchants(
    use_case: ResolveMerchantUseCase,
    repository: InMemoryMerchantRepository,
) -> None:
    use_case.execute(_sighting("EXITO", user_id=USER_ID))
    use_case.execute(_sighting("CARULLA", user_id=OTHER_USER_ID))

    page = ListMerchantsUseCase(repository=repository).execute(
        MerchantQuery(user_id=USER_ID),
    )

    assert [merchant.display_name for merchant in page.merchants] == ["Exito"]
    assert page.total == 1


def test_search_finds_a_renamed_merchant_by_what_the_bank_wrote(
    use_case: ResolveMerchantUseCase,
    editing: EditMerchantUseCase,
    repository: InMemoryMerchantRepository,
) -> None:
    created = use_case.execute(_sighting("TIENDAS ARA 123"))
    assert created.merchant is not None
    editing.execute(
        EditMerchantCommand(
            user_id=USER_ID,
            merchant_id=created.merchant.id,
            display_name="Groceries around the corner",
        ),
    )

    page = ListMerchantsUseCase(repository=repository).execute(
        MerchantQuery(user_id=USER_ID, search="ara"),
    )

    assert len(page.merchants) == 1


def test_the_review_badge_ignores_the_active_filter(
    use_case: ResolveMerchantUseCase,
    repository: InMemoryMerchantRepository,
) -> None:
    use_case.execute(_sighting("EXITO"))
    use_case.execute(_sighting("CARULLA"))

    page = ListMerchantsUseCase(repository=repository).execute(
        MerchantQuery(user_id=USER_ID, search="exito"),
    )

    assert page.total == 1
    # Both are still unreviewed, and the badge must not move when a user types
    # in the search box.
    assert page.needs_review == 2


class StubAdvisor:
    """Stands in for the model, with the port's exact contract."""

    def __init__(self, *, advice: MerchantAdvice | None = None) -> None:
        self.advice = advice
        self.seen_candidates: list[tuple[MerchantCandidate, ...]] = []

    def advise(
        self,
        *,
        counterparty: str,
        kind: CounterpartyKind,
        candidates: Sequence[MerchantCandidate],
    ) -> MerchantAdvice | None:
        del counterparty, kind
        self.seen_candidates.append(tuple(candidates))

        return self.advice


def _advised(
    repository: InMemoryMerchantRepository,
    advisor: StubAdvisor,
) -> ResolveMerchantUseCase:
    return ResolveMerchantUseCase(
        repository=repository,
        processed_events=InMemoryProcessedEventStore(),
        event_publisher=RecordingEventPublisher(),
        advisor=advisor,
    )


def test_the_advisor_is_only_asked_when_every_rule_has_missed(
    repository: InMemoryMerchantRepository,
) -> None:
    advisor = StubAdvisor()
    use_case = _advised(repository, advisor)

    use_case.execute(_sighting("TIENDAS ARA 123"))
    # Same spelling, then the same name modulo noise: tiers 1 and 2.
    use_case.execute(_sighting("TIENDAS ARA 123"))
    use_case.execute(_sighting("ARA CALLE 80"))

    # Only the first, genuinely new, spelling reached it.
    assert len(advisor.seen_candidates) == 1


def test_the_advisor_can_group_what_no_rule_could(
    repository: InMemoryMerchantRepository,
) -> None:
    # `BANCOLOMBIA NEQUI` shares no root with `NEQUI` and does not extend it,
    # so nothing deterministic can connect them. Knowing they are one company
    # is world knowledge.
    seeding = _advised(repository, StubAdvisor())
    existing = seeding.execute(_sighting("NEQUI"))
    assert existing.merchant is not None

    advisor = StubAdvisor(
        advice=MerchantAdvice(
            parent=existing.merchant.id,
            category=MerchantCategory.TRANSFERS,
        ),
    )
    result = _advised(repository, advisor).execute(_sighting("BANCOLOMBIA NEQUI"))

    assert result.resolution is Resolution.ADVISED
    assert result.merchant is not None
    assert result.merchant.id == existing.merchant.id
    assert len(result.merchant.children) == 2


def test_what_the_advisor_groups_is_a_suggestion_the_user_can_undo(
    repository: InMemoryMerchantRepository,
) -> None:
    seeding = _advised(repository, StubAdvisor())
    existing = seeding.execute(_sighting("NEQUI"))
    assert existing.merchant is not None
    existing.merchant.confirm()
    repository.save(existing.merchant)

    advisor = StubAdvisor(
        advice=MerchantAdvice(
            parent=existing.merchant.id,
            category=MerchantCategory.TRANSFERS,
        ),
    )
    result = _advised(repository, advisor).execute(_sighting("BANCOLOMBIA NEQUI"))

    assert result.merchant is not None
    alias = result.merchant.aliases[AliasFingerprint.from_raw("BANCOLOMBIA NEQUI")]
    assert alias.origin is AliasOrigin.SUGGESTED
    # A guess landed on a merchant the user had already reviewed, so it goes
    # back in the queue.
    assert result.merchant.needs_review


def test_the_advisor_proposes_a_category_for_a_brand_new_merchant(
    repository: InMemoryMerchantRepository,
) -> None:
    advisor = StubAdvisor(
        advice=MerchantAdvice(parent=None, category=MerchantCategory.GROCERIES),
    )

    result = _advised(repository, advisor).execute(_sighting("TIENDAS ARA 123"))

    assert result.resolution is Resolution.CREATED
    assert result.merchant is not None
    assert result.merchant.category is MerchantCategory.GROCERIES
    # A proposal is not a decision: the user still gets to see it.
    assert result.merchant.needs_review


def test_a_proposal_never_overwrites_a_category_the_user_chose(
    repository: InMemoryMerchantRepository,
    editing: EditMerchantUseCase,
) -> None:
    seeding = _advised(repository, StubAdvisor())
    created = seeding.execute(_sighting("TIENDAS ARA"))
    assert created.merchant is not None
    editing.execute(
        EditMerchantCommand(
            user_id=USER_ID,
            merchant_id=created.merchant.id,
            category=MerchantCategory.SHOPPING,
        ),
    )

    created.merchant.propose_category(MerchantCategory.GROCERIES)

    assert created.merchant.category is MerchantCategory.SHOPPING


def test_the_advisor_is_only_offered_this_users_merchants(
    repository: InMemoryMerchantRepository,
) -> None:
    seeding = _advised(repository, StubAdvisor())
    seeding.execute(_sighting("CARULLA", user_id=OTHER_USER_ID))
    mine = seeding.execute(_sighting("NEQUI", user_id=USER_ID))
    assert mine.merchant is not None

    advisor = StubAdvisor()
    _advised(repository, advisor).execute(
        _sighting("BANCOLOMBIA NEQUI", user_id=USER_ID),
    )

    offered = advisor.seen_candidates[0]
    assert [candidate.display_name for candidate in offered] == ["Nequi"]


def test_advice_naming_a_merchant_that_vanished_still_creates_one(
    repository: InMemoryMerchantRepository,
) -> None:
    seeding = _advised(repository, StubAdvisor())
    existing = seeding.execute(_sighting("NEQUI"))
    assert existing.merchant is not None
    repository.merchants.pop((USER_ID, existing.merchant.id))

    advisor = StubAdvisor(
        advice=MerchantAdvice(
            parent=existing.merchant.id,
            category=MerchantCategory.TRANSFERS,
        ),
    )
    result = _advised(repository, advisor).execute(_sighting("BANCOLOMBIA NEQUI"))

    assert result.resolution is Resolution.CREATED


def test_without_an_advisor_nothing_changes(
    use_case: ResolveMerchantUseCase,
) -> None:
    result = use_case.execute(_sighting("TIENDAS ARA 123"))

    assert result.resolution is Resolution.CREATED
    assert result.merchant is not None
    assert result.merchant.category is MerchantCategory.UNCATEGORIZED
