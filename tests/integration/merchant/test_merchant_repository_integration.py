"""The merchant table, against a real DynamoDB API.

What these check is the storage layout itself: that a merchant round-trips
with its children intact, that the pointers make it findable by spelling and
by grouping key, and that the claim on a handled event is genuinely atomic.
"""

import uuid

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.merchant.domain.categories import Category
from personal_finance.contexts.merchant.domain.entities import Merchant
from personal_finance.contexts.merchant.domain.exceptions import (
    DuplicateCategoryError,
)
from personal_finance.contexts.merchant.domain.value_objects import (
    AliasFingerprint,
    AliasOrigin,
    CategoryKey,
    MerchantCategory,
    MerchantRootKey,
)
from personal_finance.contexts.merchant.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
    SORT_KEY,
    DynamoDBCategoryRepository,
    DynamoDBMerchantRepository,
    DynamoDBProcessedEventStore,
    category_to_item,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId
from personal_finance.shared.infrastructure.aws.provisioning import provision_table


TABLE_NAME = "merchants"
USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = UserId.from_string("22222222-2222-2222-2222-222222222222")
NOW = PosixTime.from_epoch_seconds(1_700_000_000)


def _provision(client: DynamoDBClient) -> None:
    provision_table(
        client,
        table_name=TABLE_NAME,
        partition_key=PARTITION_KEY,
        sort_key=SORT_KEY,
        sort_key_type="S",
    )


@pytest.fixture
def repository(dynamodb_client: DynamoDBClient) -> DynamoDBMerchantRepository:
    _provision(dynamodb_client)

    return DynamoDBMerchantRepository(client=dynamodb_client, table_name=TABLE_NAME)


@pytest.fixture
def categories(dynamodb_client: DynamoDBClient) -> DynamoDBCategoryRepository:
    _provision(dynamodb_client)

    return DynamoDBCategoryRepository(client=dynamodb_client, table_name=TABLE_NAME)


@pytest.fixture
def processed_events(dynamodb_client: DynamoDBClient) -> DynamoDBProcessedEventStore:
    _provision(dynamodb_client)

    return DynamoDBProcessedEventStore(client=dynamodb_client, table_name=TABLE_NAME)


def _merchant(raw: str = "TIENDAS ARA 123", *, user_id: UserId = USER_ID) -> Merchant:
    return Merchant.seed(
        user_id=user_id,
        fingerprint=AliasFingerprint.from_raw(raw),
        raw_text=raw,
        seen_at=NOW,
    )


def test_a_merchant_round_trips_with_its_children(
    repository: DynamoDBMerchantRepository,
) -> None:
    merchant = _merchant()
    merchant.link_alias(
        fingerprint=AliasFingerprint.from_raw("ARA CALLE 80"),
        raw_text="Ara Calle 80",
        origin=AliasOrigin.DERIVED,
        seen_at=NOW,
    )
    merchant.recategorize(CategoryKey.default(MerchantCategory.GROCERIES))
    repository.save(merchant)

    stored = repository.find(user_id=USER_ID, merchant_id=merchant.id)

    assert stored is not None
    assert stored.display_name == merchant.display_name
    assert stored.category == CategoryKey.default(MerchantCategory.GROCERIES)
    assert not stored.needs_review
    assert len(stored.children) == 2
    assert stored.aliases[AliasFingerprint.from_raw("ARA CALLE 80")].raw_text == (
        "Ara Calle 80"
    )


def test_a_merchant_is_found_by_the_spelling_that_reaches_it(
    repository: DynamoDBMerchantRepository,
) -> None:
    merchant = _merchant()
    repository.save(merchant)

    found = repository.find_by_alias(
        user_id=USER_ID,
        fingerprint=AliasFingerprint.from_raw("TIENDAS ARA 123"),
    )

    assert found is not None
    assert found.id == merchant.id


def test_a_pointer_left_behind_by_a_move_does_not_answer_for_the_old_parent(
    repository: DynamoDBMerchantRepository,
) -> None:
    source = _merchant("EXITO")
    source.link_alias(
        fingerprint=AliasFingerprint.from_raw("EXITO EXPRESS"),
        raw_text="EXITO EXPRESS",
        origin=AliasOrigin.SUGGESTED,
        seen_at=NOW,
    )
    repository.save(source)
    source.detach_alias(AliasFingerprint.from_raw("EXITO EXPRESS"))
    repository.save(source)

    # The alias pointer still exists — nothing deletes it — but the merchant
    # it names no longer holds the spelling, so it must not be returned.
    assert (
        repository.find_by_alias(
            user_id=USER_ID,
            fingerprint=AliasFingerprint.from_raw("EXITO EXPRESS"),
        )
        is None
    )


def test_grouping_keys_are_listed_for_the_owner_only(
    repository: DynamoDBMerchantRepository,
) -> None:
    mine = _merchant("TIENDAS ARA 123")
    repository.save(mine)
    repository.save(_merchant("CARULLA", user_id=OTHER_USER_ID))

    keys = repository.list_root_keys(USER_ID)

    assert keys == {MerchantRootKey(value="ARA"): mine.id}


def test_a_users_list_never_includes_another_users_merchants(
    repository: DynamoDBMerchantRepository,
) -> None:
    repository.save(_merchant("TIENDAS ARA"))
    repository.save(_merchant("CARULLA", user_id=OTHER_USER_ID))

    assert [merchant.display_name for merchant in repository.list_by_user(USER_ID)] == [
        "Tiendas Ara",
    ]


def test_deleting_a_merchant_leaves_the_others_alone(
    repository: DynamoDBMerchantRepository,
) -> None:
    kept = _merchant("TIENDAS ARA")
    absorbed = _merchant("CARULLA")
    repository.save(kept)
    repository.save(absorbed)

    repository.delete(user_id=USER_ID, merchant_id=absorbed.id)

    assert repository.find(user_id=USER_ID, merchant_id=absorbed.id) is None
    assert repository.find(user_id=USER_ID, merchant_id=kept.id) is not None


def test_an_event_can_only_be_claimed_once(
    processed_events: DynamoDBProcessedEventStore,
) -> None:
    event_id = uuid.uuid4()

    assert processed_events.claim(user_id=USER_ID, event_id=event_id)
    assert not processed_events.claim(user_id=USER_ID, event_id=event_id)


def test_two_users_can_claim_the_same_event_id(
    processed_events: DynamoDBProcessedEventStore,
) -> None:
    # Claims are scoped to the owner, like everything else in the table.
    event_id = uuid.uuid4()

    assert processed_events.claim(user_id=USER_ID, event_id=event_id)
    assert processed_events.claim(user_id=OTHER_USER_ID, event_id=event_id)


def test_a_category_round_trips_and_is_only_its_owners(
    categories: DynamoDBCategoryRepository,
) -> None:
    mine = Category.create(user_id=USER_ID, label="Mascotas", created_at=NOW)
    theirs = Category.create(user_id=OTHER_USER_ID, label="Bici", created_at=NOW)
    categories.add(mine)
    categories.add(theirs)

    stored = categories.list_by_user(USER_ID)

    assert [category.id for category in stored] == [mine.id]
    assert stored[0].label == "Mascotas"
    assert stored[0].created_at.as_epoch_seconds() == NOW.as_epoch_seconds()


def test_two_writes_of_one_name_leave_one_category(
    categories: DynamoDBCategoryRepository,
) -> None:
    """The conditional write on the name claim is the uniqueness rule: two
    taps on the same button are two requests, and reading the list first would
    let both through and leave this person with two categories reading alike.
    """
    categories.add(Category.create(user_id=USER_ID, label="Mascotas", created_at=NOW))

    with pytest.raises(DuplicateCategoryError):
        categories.add(
            Category.create(user_id=USER_ID, label="mascotas", created_at=NOW),
        )

    assert len(categories.list_by_user(USER_ID)) == 1


def test_a_renamed_category_keeps_its_key_and_moves_its_claim(
    categories: DynamoDBCategoryRepository,
) -> None:
    category = Category.create(user_id=USER_ID, label="Mascotss", created_at=NOW)
    categories.add(category)
    category.rename("Mascotas")

    categories.rename(category, previous_label="Mascotss")

    stored = categories.find(user_id=USER_ID, key=category.id)
    assert stored is not None
    assert stored.label == "Mascotas"
    # The name it used to hold is free, and the one it holds now is not.
    categories.add(Category.create(user_id=USER_ID, label="Mascotss", created_at=NOW))

    with pytest.raises(DuplicateCategoryError):
        categories.add(
            Category.create(user_id=USER_ID, label="Mascotas", created_at=NOW),
        )


def test_a_rename_that_loses_the_name_changes_nothing(
    categories: DynamoDBCategoryRepository,
) -> None:
    taken = Category.create(user_id=USER_ID, label="Mascotas", created_at=NOW)
    categories.add(taken)
    other = Category.create(user_id=USER_ID, label="Bici", created_at=NOW)
    categories.add(other)
    other.rename("mascotas")

    with pytest.raises(DuplicateCategoryError):
        categories.rename(other, previous_label="Bici")

    stored = categories.find(user_id=USER_ID, key=other.id)
    assert stored is not None
    assert stored.label == "Bici"


def test_only_the_spelling_changing_is_not_a_conflict_with_itself(
    categories: DynamoDBCategoryRepository,
) -> None:
    # The claim it is about to take is the one it already holds.
    category = Category.create(user_id=USER_ID, label="mascotas", created_at=NOW)
    categories.add(category)
    category.rename("Mascotas")

    categories.rename(category, previous_label="mascotas")

    stored = categories.find(user_id=USER_ID, key=category.id)
    assert stored is not None
    assert stored.label == "Mascotas"


def test_a_deleted_category_gives_its_name_back(
    categories: DynamoDBCategoryRepository,
) -> None:
    category = Category.create(user_id=USER_ID, label="Mascotas", created_at=NOW)
    categories.add(category)

    categories.delete(category)

    assert categories.find(user_id=USER_ID, key=category.id) is None
    assert categories.list_by_user(USER_ID) == []
    categories.add(Category.create(user_id=USER_ID, label="Mascotas", created_at=NOW))


def test_a_claim_left_behind_by_a_crash_does_not_block_a_name_forever(
    categories: DynamoDBCategoryRepository,
    dynamodb_client: DynamoDBClient,
) -> None:
    """The claim is written before the record and released after it, so a
    crash in between can leave one pointing at nothing. Verified when it
    blocks somebody rather than swept, the same way a merchant's alias
    pointer is.
    """
    orphan = Category.create(user_id=USER_ID, label="Mascotas", created_at=NOW)
    categories.add(orphan)
    dynamodb_client.delete_item(
        TableName=TABLE_NAME,
        Key={
            PARTITION_KEY: {"S": str(USER_ID.value)},
            SORT_KEY: {"S": f"CATEGORY#{orphan.id.value}"},
        },
    )

    categories.add(Category.create(user_id=USER_ID, label="Mascotas", created_at=NOW))

    assert [c.label for c in categories.list_by_user(USER_ID)] == ["Mascotas"]


def test_a_claim_whose_category_was_renamed_away_stops_blocking(
    categories: DynamoDBCategoryRepository,
    dynamodb_client: DynamoDBClient,
) -> None:
    # The other half of the same crash: the record moved on, the old claim
    # did not.
    category = Category.create(user_id=USER_ID, label="Mascotas", created_at=NOW)
    categories.add(category)
    category.rename("Perros")
    dynamodb_client.put_item(TableName=TABLE_NAME, Item=category_to_item(category))

    categories.add(Category.create(user_id=USER_ID, label="Mascotas", created_at=NOW))

    assert sorted(c.label for c in categories.list_by_user(USER_ID)) == [
        "Mascotas",
        "Perros",
    ]


def test_one_persons_category_names_do_not_reach_another(
    categories: DynamoDBCategoryRepository,
) -> None:
    categories.add(Category.create(user_id=USER_ID, label="Mascotas", created_at=NOW))

    # Same name, different partition: no claim of theirs is in the way.
    categories.add(
        Category.create(user_id=OTHER_USER_ID, label="Mascotas", created_at=NOW),
    )

    assert len(categories.list_by_user(USER_ID)) == 1
    assert len(categories.list_by_user(OTHER_USER_ID)) == 1


def test_categories_do_not_show_up_among_a_users_merchants(
    repository: DynamoDBMerchantRepository,
    categories: DynamoDBCategoryRepository,
) -> None:
    # One table, one partition, and the sort key prefix is what keeps the two
    # kinds of record apart.
    repository.save(_merchant())
    categories.add(Category.create(user_id=USER_ID, label="Mascotas", created_at=NOW))

    assert len(repository.list_by_user(USER_ID)) == 1
