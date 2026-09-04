"""How a counterparty finds its merchant, tier by tier."""

from collections.abc import Mapping, Sequence
import uuid

import pytest

from personal_finance.contexts.merchant.application.categories import (
    CategoryCatalog,
    CreateCategoryCommand,
    CreateCategoryUseCase,
    DeleteCategoryCommand,
    DeleteCategoryUseCase,
    ListCategoriesUseCase,
    RenameCategoryCommand,
    RenameCategoryUseCase,
)
from personal_finance.contexts.merchant.application.commands import (
    ClassifyCounterpartyCommand,
    EditMerchantCommand,
    MergeMerchantsCommand,
    MoveAliasCommand,
    RecordSightingCommand,
    SplitAliasCommand,
)
from personal_finance.contexts.merchant.application.handlers import (
    ClassifyCounterpartyUseCase,
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
    CategoryChoice,
    MerchantAdvice,
    MerchantCandidate,
)
from personal_finance.contexts.merchant.application.queries import (
    ListMerchantsUseCase,
    MerchantQuery,
)
from personal_finance.contexts.merchant.domain.categories import Category
from personal_finance.contexts.merchant.domain.entities import Merchant
from personal_finance.contexts.merchant.domain.events import MerchantReclassified
from personal_finance.contexts.merchant.domain.exceptions import (
    DuplicateCategoryError,
    InvalidCategoryLabelError,
    ShippedCategoryError,
    UnknownCategoryError,
)
from personal_finance.contexts.merchant.domain.normalization import derive_slug
from personal_finance.contexts.merchant.domain.value_objects import (
    AliasFingerprint,
    AliasOrigin,
    CategoryKey,
    CounterpartyKind,
    MerchantCategory,
    MerchantId,
    MerchantRootKey,
    MerchantStatus,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = UserId.from_string("22222222-2222-2222-2222-222222222222")
NOW = PosixTime.from_epoch_seconds(1_700_000_000)


def _key(category: MerchantCategory) -> CategoryKey:
    return CategoryKey.default(category)


class InMemoryCategoryRepository:
    """The user's own half of the vocabulary, holding the same rules the
    conditional writes hold in DynamoDB: a name is claimed by exactly one
    category, and the claim moves when the name does.
    """

    def __init__(self) -> None:
        self.categories: dict[tuple[UserId, CategoryKey], Category] = {}
        self.names: dict[tuple[UserId, str], CategoryKey] = {}

    def list_by_user(self, user_id: UserId) -> Sequence[Category]:
        return [
            category
            for (owner, _), category in self.categories.items()
            if owner == user_id
        ]

    def find(self, *, user_id: UserId, key: CategoryKey) -> Category | None:
        stored = self.categories.get((user_id, key))

        # A copy, like a real repository hands back: whatever the caller does
        # to it is not stored until they say so, and a write that is refused
        # must leave nothing behind.
        return None if stored is None else _copy(stored)

    def add(self, category: Category) -> None:
        self._claim(category)
        self.categories[(category.user_id, category.id)] = _copy(category)

    def rename(self, category: Category, *, previous_label: str) -> None:
        previous = derive_slug(previous_label)

        if previous != category.name_key:
            self._claim(category)
            self.names.pop((category.user_id, previous), None)

        self.categories[(category.user_id, category.id)] = _copy(category)

    def delete(self, category: Category) -> None:
        self.categories.pop((category.user_id, category.id), None)
        self.names.pop((category.user_id, category.name_key), None)

    def _claim(self, category: Category) -> None:
        held = self.names.get((category.user_id, category.name_key))

        if held is not None and held != category.id:
            raise DuplicateCategoryError(f"{category.label!r} already exists")

        self.names[(category.user_id, category.name_key)] = category.id


def _copy(category: Category) -> Category:
    return Category(
        id=category.id,
        user_id=category.user_id,
        label=category.label,
        created_at=category.created_at,
    )


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
def category_repository() -> InMemoryCategoryRepository:
    return InMemoryCategoryRepository()


@pytest.fixture
def catalog(category_repository: InMemoryCategoryRepository) -> CategoryCatalog:
    return CategoryCatalog(repository=category_repository)


@pytest.fixture
def editing(
    repository: InMemoryMerchantRepository,
    publisher: RecordingEventPublisher,
    catalog: CategoryCatalog,
) -> EditMerchantUseCase:
    return EditMerchantUseCase(
        repository=repository,
        event_publisher=publisher,
        categories=catalog,
    )


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
    catalog: CategoryCatalog,
) -> SplitAliasUseCase:
    return SplitAliasUseCase(
        repository=repository,
        event_publisher=publisher,
        categories=catalog,
    )


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
            category=_key(MerchantCategory.GROCERIES),
        ),
    )

    assert edited.display_name == "Ara"
    assert edited.category == _key(MerchantCategory.GROCERIES)
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
            category=_key(MerchantCategory.FEES),
        ),
    )

    assert created.id != guessed.merchant.id
    assert created.display_name == "Éxito Seguros"
    assert created.category == _key(MerchantCategory.FEES)
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
        self.seen_categories: list[tuple[CategoryChoice, ...]] = []

    def advise(
        self,
        *,
        counterparty: str,
        kind: CounterpartyKind,
        candidates: Sequence[MerchantCandidate],
        categories: Sequence[CategoryChoice],
    ) -> MerchantAdvice | None:
        del counterparty, kind
        self.seen_candidates.append(tuple(candidates))
        self.seen_categories.append(tuple(categories))

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
            category=_key(MerchantCategory.TRANSFERS),
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
            category=_key(MerchantCategory.TRANSFERS),
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
        advice=MerchantAdvice(parent=None, category=_key(MerchantCategory.GROCERIES)),
    )

    result = _advised(repository, advisor).execute(_sighting("TIENDAS ARA 123"))

    assert result.resolution is Resolution.CREATED
    assert result.merchant is not None
    assert result.merchant.category == _key(MerchantCategory.GROCERIES)
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
            category=_key(MerchantCategory.SHOPPING),
        ),
    )

    created.merchant.propose_category(_key(MerchantCategory.GROCERIES))

    assert created.merchant.category == _key(MerchantCategory.SHOPPING)


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
            category=_key(MerchantCategory.TRANSFERS),
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
    assert result.merchant.category == _key(MerchantCategory.UNCATEGORIZED)


# ------------------------------------------------------- categories of one's own


@pytest.fixture
def creating(
    category_repository: InMemoryCategoryRepository,
) -> CreateCategoryUseCase:
    return CreateCategoryUseCase(repository=category_repository)


def test_a_category_somebody_wrote_joins_the_shipped_ones(
    creating: CreateCategoryUseCase,
    catalog: CategoryCatalog,
) -> None:
    creating.execute(CreateCategoryCommand(user_id=USER_ID, label="Mascotas"), now=NOW)

    listed = catalog.list(USER_ID)

    assert [choice.key.value for choice in listed[:2]] == ["uncategorized", "groceries"]
    # Theirs comes after the shipped ones, so nothing a user adds shuffles the
    # familiar half of their own dropdown.
    assert listed[-1].label == "Mascotas"
    assert not listed[-1].shipped


def test_the_same_name_twice_is_one_category_and_one_refusal(
    creating: CreateCategoryUseCase,
) -> None:
    creating.execute(CreateCategoryCommand(user_id=USER_ID, label="Mascotas"), now=NOW)

    with pytest.raises(DuplicateCategoryError):
        creating.execute(
            # Same category, spelled the way somebody in a hurry would.
            CreateCategoryCommand(user_id=USER_ID, label="  mascotas "),
            now=NOW,
        )


def test_a_name_that_breaks_the_rules_never_reaches_storage(
    creating: CreateCategoryUseCase,
    category_repository: InMemoryCategoryRepository,
) -> None:
    with pytest.raises(InvalidCategoryLabelError):
        creating.execute(
            CreateCategoryCommand(user_id=USER_ID, label="x" * 200),
            now=NOW,
        )

    assert not category_repository.categories


def test_one_persons_category_is_not_in_anybody_elses_vocabulary(
    creating: CreateCategoryUseCase,
    catalog: CategoryCatalog,
) -> None:
    created = creating.execute(
        CreateCategoryCommand(user_id=USER_ID, label="Mascotas"),
        now=NOW,
    )

    assert catalog.resolve(user_id=USER_ID, key=created.id) == created.id

    with pytest.raises(UnknownCategoryError):
        catalog.resolve(user_id=OTHER_USER_ID, key=created.id)


def test_a_merchant_can_be_filed_under_a_category_its_owner_wrote(
    use_case: ResolveMerchantUseCase,
    creating: CreateCategoryUseCase,
    editing: EditMerchantUseCase,
) -> None:
    created = use_case.execute(_sighting("AGROPECUARIA EL CAMPO"))
    assert created.merchant is not None
    mascotas = creating.execute(
        CreateCategoryCommand(user_id=USER_ID, label="Mascotas"),
        now=NOW,
    )

    edited = editing.execute(
        EditMerchantCommand(
            user_id=USER_ID,
            merchant_id=created.merchant.id,
            category=mascotas.id,
        ),
    )

    assert edited.category == mascotas.id
    assert not edited.needs_review


def test_a_category_nobody_has_is_refused_rather_than_stored(
    use_case: ResolveMerchantUseCase,
    editing: EditMerchantUseCase,
    repository: InMemoryMerchantRepository,
) -> None:
    # A merchant pointing at a category that does not exist reads as a bucket
    # the user never made, and their spending would quietly land in it.
    created = use_case.execute(_sighting("TIENDAS ARA 123"))
    assert created.merchant is not None

    with pytest.raises(UnknownCategoryError):
        editing.execute(
            EditMerchantCommand(
                user_id=USER_ID,
                merchant_id=created.merchant.id,
                category=CategoryKey.custom("mascotas"),
            ),
        )

    stored = repository.find(user_id=USER_ID, merchant_id=created.merchant.id)
    assert stored is not None
    assert stored.category == _key(MerchantCategory.UNCATEGORIZED)


def test_the_model_is_offered_the_categories_this_user_wrote(
    repository: InMemoryMerchantRepository,
    category_repository: InMemoryCategoryRepository,
    catalog: CategoryCatalog,
    creating: CreateCategoryUseCase,
) -> None:
    del category_repository
    creating.execute(CreateCategoryCommand(user_id=USER_ID, label="Mascotas"), now=NOW)
    advisor = StubAdvisor()
    ResolveMerchantUseCase(
        repository=repository,
        processed_events=InMemoryProcessedEventStore(),
        event_publisher=RecordingEventPublisher(),
        advisor=advisor,
        categories=catalog,
    ).execute(_sighting("AGROPECUARIA EL CAMPO"))

    offered = [choice.label for choice in advisor.seen_categories[0]]

    assert "Mascotas" in offered


# ------------------------------- a name somebody typed into the movement form


@pytest.fixture
def classifying(
    repository: InMemoryMerchantRepository,
    publisher: RecordingEventPublisher,
    catalog: CategoryCatalog,
) -> ClassifyCounterpartyUseCase:
    return ClassifyCounterpartyUseCase(
        repository=repository,
        event_publisher=publisher,
        categories=catalog,
    )


def _classify(counterparty: str, category: CategoryKey) -> ClassifyCounterpartyCommand:
    return ClassifyCounterpartyCommand(
        user_id=USER_ID,
        counterparty=counterparty,
        category=category,
        occurred_at=NOW,
    )


def test_a_name_nothing_has_ever_seen_becomes_a_merchant(
    classifying: ClassifyCounterpartyUseCase,
) -> None:
    # Without this, a movement entered by hand stays outside every breakdown
    # by category forever, however many times it is entered.
    merchant = classifying.execute(
        _classify("Panadería la esquina", _key(MerchantCategory.GROCERIES)),
    )

    assert merchant.category == _key(MerchantCategory.GROCERIES)
    # A person typed it, so no rule may re-derive the grouping later.
    assert merchant.children[0].origin is AliasOrigin.MANUAL
    assert not merchant.needs_review


def test_a_name_that_already_has_a_merchant_is_refiled_rather_than_duplicated(
    use_case: ResolveMerchantUseCase,
    classifying: ClassifyCounterpartyUseCase,
    repository: InMemoryMerchantRepository,
) -> None:
    created = use_case.execute(_sighting("TIENDAS ARA 123"))
    assert created.merchant is not None

    merchant = classifying.execute(
        _classify("TIENDAS ARA 123", _key(MerchantCategory.GROCERIES)),
    )

    assert merchant.id == created.merchant.id
    assert merchant.category == _key(MerchantCategory.GROCERIES)
    assert len(repository.list_by_user(USER_ID)) == 1


def test_a_hand_typed_name_joins_the_merchant_that_already_answers_to_it(
    use_case: ResolveMerchantUseCase,
    classifying: ClassifyCounterpartyUseCase,
    repository: InMemoryMerchantRepository,
) -> None:
    """A merchant is reachable by the root key its aliases derive. Seeding a
    second one whose root the first already answers to would take that key
    away, and every later sighting would derive onto the new merchant —
    splitting a history that had been in one place.
    """
    created = use_case.execute(_sighting("TIENDAS ARA 123"))
    assert created.merchant is not None

    merchant = classifying.execute(
        _classify("Tiendas Ara Calle 80", _key(MerchantCategory.GROCERIES)),
    )

    assert merchant.id == created.merchant.id
    assert len(repository.list_by_user(USER_ID)) == 1
    # Claimed as a decision, so nothing re-derives it later.
    alias = merchant.aliases[AliasFingerprint.from_raw("Tiendas Ara Calle 80")]
    assert alias.origin is AliasOrigin.MANUAL
    # And the grouping key still reaches exactly one merchant.
    assert set(repository.list_root_keys(USER_ID).values()) == {created.merchant.id}


def test_a_hand_typed_name_is_never_grouped_on_a_guess(
    use_case: ResolveMerchantUseCase,
    classifying: ClassifyCounterpartyUseCase,
    repository: InMemoryMerchantRepository,
) -> None:
    # `EXITO EXPRESS` only *looks* like a branded variant of `EXITO`. On the
    # automatic path that is an offer the user can undo; here the spelling
    # arrives already claimed as a decision, so there would be nothing to undo.
    use_case.execute(_sighting("EXITO"))

    merchant = classifying.execute(
        _classify("Exito Express", _key(MerchantCategory.GROCERIES)),
    )

    assert len(repository.list_by_user(USER_ID)) == 2
    assert merchant.category == _key(MerchantCategory.GROCERIES)


def test_filing_a_name_under_a_category_nobody_has_is_refused(
    classifying: ClassifyCounterpartyUseCase,
    repository: InMemoryMerchantRepository,
) -> None:
    with pytest.raises(UnknownCategoryError):
        classifying.execute(
            _classify("Panadería la esquina", CategoryKey.custom("mascotas")),
        )

    assert not repository.list_by_user(USER_ID)


def test_text_no_merchant_could_be_made_of_is_refused_before_anything_is_written(
    classifying: ClassifyCounterpartyUseCase,
    repository: InMemoryMerchantRepository,
) -> None:
    with pytest.raises(ValueError, match="recognisable"):
        classifying.execute(_classify("---", _key(MerchantCategory.GROCERIES)))

    assert not repository.list_by_user(USER_ID)


# ------------------------------------------------------ fixing and removing one


@pytest.fixture
def renaming(category_repository: InMemoryCategoryRepository) -> RenameCategoryUseCase:
    return RenameCategoryUseCase(repository=category_repository)


@pytest.fixture
def deleting(
    category_repository: InMemoryCategoryRepository,
    repository: InMemoryMerchantRepository,
    publisher: RecordingEventPublisher,
) -> DeleteCategoryUseCase:
    return DeleteCategoryUseCase(
        repository=category_repository,
        merchants=repository,
        event_publisher=publisher,
    )


def _own(creating: CreateCategoryUseCase, label: str) -> CategoryKey:
    return creating.execute(
        CreateCategoryCommand(user_id=USER_ID, label=label),
        now=NOW,
    ).id


def test_a_typo_is_fixed_without_moving_what_is_filed_under_it(
    use_case: ResolveMerchantUseCase,
    creating: CreateCategoryUseCase,
    editing: EditMerchantUseCase,
    renaming: RenameCategoryUseCase,
    repository: InMemoryMerchantRepository,
) -> None:
    """The reason the key is not the name. A key that followed the name would
    make every correction a rewrite of every merchant in that bucket.
    """
    created = use_case.execute(_sighting("AGROPECUARIA EL CAMPO"))
    assert created.merchant is not None
    key = _own(creating, "Mascotss")
    editing.execute(
        EditMerchantCommand(
            user_id=USER_ID,
            merchant_id=created.merchant.id,
            category=key,
        ),
    )

    renamed = renaming.execute(
        RenameCategoryCommand(user_id=USER_ID, key=key, label="Mascotas"),
    )

    assert renamed.id == key
    assert renamed.label == "Mascotas"
    stored = repository.find(user_id=USER_ID, merchant_id=created.merchant.id)
    assert stored is not None
    assert stored.category == key


def test_a_rename_onto_a_name_already_taken_leaves_both_alone(
    creating: CreateCategoryUseCase,
    renaming: RenameCategoryUseCase,
    catalog: CategoryCatalog,
) -> None:
    _own(creating, "Mascotas")
    other = _own(creating, "Bici")

    with pytest.raises(DuplicateCategoryError):
        renaming.execute(
            RenameCategoryCommand(user_id=USER_ID, key=other, label="  mascotas "),
        )

    assert sorted(
        choice.label for choice in catalog.list(USER_ID) if not choice.shipped
    ) == ["Bici", "Mascotas"]


def test_renaming_frees_the_name_that_was_held(
    creating: CreateCategoryUseCase,
    renaming: RenameCategoryUseCase,
) -> None:
    key = _own(creating, "Mascotas")
    renaming.execute(RenameCategoryCommand(user_id=USER_ID, key=key, label="Perros"))

    # Nobody holds "Mascotas" now, so it can be taken again.
    assert _own(creating, "Mascotas") != key


def test_a_rename_that_only_changes_the_spelling_keeps_its_own_name(
    creating: CreateCategoryUseCase,
    renaming: RenameCategoryUseCase,
) -> None:
    # Same name folded down, so the claim it already holds must not refuse it.
    key = _own(creating, "mascotas")

    renamed = renaming.execute(
        RenameCategoryCommand(user_id=USER_ID, key=key, label="Mascotas"),
    )

    assert renamed.label == "Mascotas"


def test_the_categories_the_app_ships_are_nobodys_to_edit(
    renaming: RenameCategoryUseCase,
    deleting: DeleteCategoryUseCase,
) -> None:
    groceries = _key(MerchantCategory.GROCERIES)

    with pytest.raises(ShippedCategoryError):
        renaming.execute(
            RenameCategoryCommand(user_id=USER_ID, key=groceries, label="Mercado"),
        )

    with pytest.raises(ShippedCategoryError):
        deleting.execute(DeleteCategoryCommand(user_id=USER_ID, key=groceries))


def test_editing_somebody_elses_category_is_indistinguishable_from_a_typo(
    creating: CreateCategoryUseCase,
    renaming: RenameCategoryUseCase,
) -> None:
    key = _own(creating, "Mascotas")

    with pytest.raises(UnknownCategoryError):
        renaming.execute(
            RenameCategoryCommand(user_id=OTHER_USER_ID, key=key, label="Perros"),
        )


def test_removing_a_category_puts_what_was_in_it_back_in_the_default_bucket(
    use_case: ResolveMerchantUseCase,
    creating: CreateCategoryUseCase,
    editing: EditMerchantUseCase,
    deleting: DeleteCategoryUseCase,
    catalog: CategoryCatalog,
    repository: InMemoryMerchantRepository,
) -> None:
    created = use_case.execute(_sighting("AGROPECUARIA EL CAMPO"))
    assert created.merchant is not None
    key = _own(creating, "Mascotas")
    editing.execute(
        EditMerchantCommand(
            user_id=USER_ID,
            merchant_id=created.merchant.id,
            category=key,
        ),
    )

    removed = deleting.execute(DeleteCategoryCommand(user_id=USER_ID, key=key))

    assert removed.merchants_moved == 1
    assert removed.label == "Mascotas"
    # Nothing is left naming a bucket that no longer exists.
    assert [choice for choice in catalog.list(USER_ID) if not choice.shipped] == []
    stored = repository.find(user_id=USER_ID, merchant_id=created.merchant.id)
    assert stored is not None
    assert stored.category == _key(MerchantCategory.UNCATEGORIZED)


def test_removing_a_category_does_not_count_as_reviewing_what_was_in_it(
    use_case: ResolveMerchantUseCase,
    creating: CreateCategoryUseCase,
    editing: EditMerchantUseCase,
    deleting: DeleteCategoryUseCase,
    repository: InMemoryMerchantRepository,
) -> None:
    """Otherwise removing one category would quietly empty somebody's review
    queue of every merchant that happened to be in it.
    """
    created = use_case.execute(_sighting("AGROPECUARIA EL CAMPO"))
    assert created.merchant is not None
    key = _own(creating, "Mascotas")
    editing.execute(
        EditMerchantCommand(
            user_id=USER_ID,
            merchant_id=created.merchant.id,
            category=key,
        ),
    )
    # Put it back in the queue: filing it confirmed it, and what is being
    # checked here is that removing the bucket changes nothing either way.
    unreviewed = repository.find(user_id=USER_ID, merchant_id=created.merchant.id)
    assert unreviewed is not None
    unreviewed.status = MerchantStatus.AUTOMATIC
    repository.save(unreviewed)

    deleting.execute(DeleteCategoryCommand(user_id=USER_ID, key=key))

    stored = repository.find(user_id=USER_ID, merchant_id=created.merchant.id)
    assert stored is not None
    assert stored.needs_review


def test_removing_a_category_announces_every_merchant_it_moved(
    use_case: ResolveMerchantUseCase,
    creating: CreateCategoryUseCase,
    editing: EditMerchantUseCase,
    deleting: DeleteCategoryUseCase,
    publisher: RecordingEventPublisher,
) -> None:
    # Another context holds movements attributed to these merchants; a
    # category change is one of the four facts that leave this one.
    created = use_case.execute(_sighting("AGROPECUARIA EL CAMPO"))
    assert created.merchant is not None
    key = _own(creating, "Mascotas")
    editing.execute(
        EditMerchantCommand(
            user_id=USER_ID,
            merchant_id=created.merchant.id,
            category=key,
        ),
    )
    publisher.published.clear()

    deleting.execute(DeleteCategoryCommand(user_id=USER_ID, key=key))

    reclassified = [
        event
        for event in publisher.published
        if isinstance(event, MerchantReclassified)
    ]
    assert len(reclassified) == 1
    assert reclassified[0].category == _key(MerchantCategory.UNCATEGORIZED)


def test_counting_what_sits_in_each_category_is_asked_for_not_assumed(
    use_case: ResolveMerchantUseCase,
    creating: CreateCategoryUseCase,
    editing: EditMerchantUseCase,
    catalog: CategoryCatalog,
    repository: InMemoryMerchantRepository,
) -> None:
    """The number a "this will move 7 comercios" confirmation needs. Every
    dropdown in the app reads this list, and none of them wants to pay a read
    of the user's merchants for it.
    """
    created = use_case.execute(_sighting("AGROPECUARIA EL CAMPO"))
    assert created.merchant is not None
    use_case.execute(_sighting("TIENDAS ARA 123"))
    key = _own(creating, "Mascotas")
    editing.execute(
        EditMerchantCommand(
            user_id=USER_ID,
            merchant_id=created.merchant.id,
            category=key,
        ),
    )
    listing = ListCategoriesUseCase(catalog=catalog, merchants=repository)

    assert all(view.usage is None for view in listing.execute(USER_ID))

    usage = {view.key: view.usage for view in listing.execute(USER_ID, with_usage=True)}

    assert usage[key] == 1
    assert usage[_key(MerchantCategory.UNCATEGORIZED)] == 1
    assert usage[_key(MerchantCategory.GROCERIES)] == 0
