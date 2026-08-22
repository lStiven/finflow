"""The merchant table, against a real DynamoDB API.

What these check is the storage layout itself: that a merchant round-trips
with its children intact, that the pointers make it findable by spelling and
by grouping key, and that the claim on a handled event is genuinely atomic.
"""

import uuid

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.merchant.domain.entities import Merchant
from personal_finance.contexts.merchant.domain.value_objects import (
    AliasFingerprint,
    AliasOrigin,
    MerchantCategory,
    MerchantRootKey,
)
from personal_finance.contexts.merchant.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
    SORT_KEY,
    DynamoDBMerchantRepository,
    DynamoDBProcessedEventStore,
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
    merchant.recategorize(MerchantCategory.GROCERIES)
    repository.save(merchant)

    stored = repository.find(user_id=USER_ID, merchant_id=merchant.id)

    assert stored is not None
    assert stored.display_name == merchant.display_name
    assert stored.category is MerchantCategory.GROCERIES
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
