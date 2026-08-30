from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.ingestion.domain.entities import UserInbox
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.contexts.ingestion.infrastructure.persistence.user_inbox_dynamodb import (  # noqa: E501
    INBOX_BY_USER_INDEX,
    INBOX_PARTITION_KEY,
    USER_ID_ATTRIBUTE,
    DynamoDBUserInboxRepository,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId
from personal_finance.shared.infrastructure.aws.provisioning import (
    SecondaryIndex,
    provision_table,
)


TABLE_NAME = "user_inboxes"
USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
OTHER_USER_ID = UserId.from_string("22222222-2222-2222-2222-222222222222")

BY_USER = SecondaryIndex(name=INBOX_BY_USER_INDEX, partition_key=USER_ID_ATTRIBUTE)


def _provision(client: DynamoDBClient) -> None:
    provision_table(
        client,
        table_name=TABLE_NAME,
        partition_key=INBOX_PARTITION_KEY,
        enable_ttl=False,
        secondary_indexes=(BY_USER,),
    )


@pytest.fixture
def repository(dynamodb_client: DynamoDBClient) -> DynamoDBUserInboxRepository:
    _provision(dynamodb_client)

    return DynamoDBUserInboxRepository(
        client=dynamodb_client,
        table_name=TABLE_NAME,
    )


def _inbox(
    *,
    address: str,
    user_id: UserId = USER_ID,
    domains: frozenset[str] = frozenset({"bank.com"}),
    addresses: frozenset[str] = frozenset(),
) -> UserInbox:
    return UserInbox(
        user_id=user_id,
        address=EmailAddress(address),
        sender_policy=AuthorizedSenderPolicy(
            allowed_domains=domains,
            allowed_addresses=frozenset(EmailAddress(value) for value in addresses),
        ),
    )


def test_a_user_with_no_inboxes_reads_back_empty(
    repository: DynamoDBUserInboxRepository,
) -> None:
    assert list(repository.find_by_user(USER_ID)) == []


def test_the_index_returns_every_inbox_the_user_owns(
    repository: DynamoDBUserInboxRepository,
) -> None:
    repository.save(_inbox(address="a@inbound.test"))
    repository.save(_inbox(address="b@inbound.test"))

    found = repository.find_by_user(USER_ID)

    assert sorted(inbox.address.value for inbox in found) == [
        "a@inbound.test",
        "b@inbound.test",
    ]


def test_the_index_never_leaks_another_users_inbox(
    repository: DynamoDBUserInboxRepository,
) -> None:
    repository.save(_inbox(address="mine@inbound.test"))
    repository.save(_inbox(address="theirs@inbound.test", user_id=OTHER_USER_ID))

    found = repository.find_by_user(USER_ID)

    assert [inbox.address.value for inbox in found] == ["mine@inbound.test"]


def test_the_projection_carries_the_trusted_senders_back(
    repository: DynamoDBUserInboxRepository,
) -> None:
    repository.save(
        _inbox(
            address="a@inbound.test",
            domains=frozenset({"bancolombia.com.co"}),
            addresses=frozenset({"alertas@nequi.com.co"}),
        ),
    )

    inbox = repository.find_by_user(USER_ID)[0]

    # A keys-only projection would return the address and nothing else, which
    # is the whole reason the index projects every attribute.
    assert inbox.sender_policy.allowed_domains == frozenset({"bancolombia.com.co"})
    assert inbox.sender_policy.allowed_addresses == frozenset(
        {EmailAddress("alertas@nequi.com.co")},
    )


def test_re_registering_an_address_does_not_duplicate_it_in_the_index(
    repository: DynamoDBUserInboxRepository,
) -> None:
    repository.save(_inbox(address="a@inbound.test"))
    repository.save(_inbox(address="a@inbound.test", domains=frozenset({"other.com"})))

    found = repository.find_by_user(USER_ID)

    assert len(found) == 1
    assert found[0].sender_policy.allowed_domains == frozenset({"other.com"})


def test_index_is_added_to_a_table_that_predates_it(
    dynamodb_client: DynamoDBClient,
) -> None:
    # A table created before the index existed must gain it on the next
    # provision, or the listing endpoint would only ever work on a fresh
    # environment.
    provision_table(
        dynamodb_client,
        table_name=TABLE_NAME,
        partition_key=INBOX_PARTITION_KEY,
        enable_ttl=False,
    )

    _provision(dynamodb_client)

    described = dynamodb_client.describe_table(TableName=TABLE_NAME)["Table"]
    indexes = {
        index.get("IndexName") for index in described.get("GlobalSecondaryIndexes", [])
    }
    assert INBOX_BY_USER_INDEX in indexes


def test_provisioning_the_index_twice_is_safe(
    dynamodb_client: DynamoDBClient,
) -> None:
    _provision(dynamodb_client)
    _provision(dynamodb_client)

    described = dynamodb_client.describe_table(TableName=TABLE_NAME)["Table"]

    assert len(described.get("GlobalSecondaryIndexes", [])) == 1


CONFIRMED_AT = PosixTime.from_epoch_seconds(1_756_400_000)
LATER = PosixTime.from_epoch_seconds(1_756_500_000)


def test_a_confirmed_forwarding_rule_reads_back(
    repository: DynamoDBUserInboxRepository,
) -> None:
    repository.save(_inbox(address="a@inbound.test"))

    assert (
        repository.mark_forwarding_confirmed(
            address=EmailAddress("a@inbound.test"),
            confirmed_at=CONFIRMED_AT,
        )
        is True
    )

    inbox = repository.find_by_user(USER_ID)[0]
    assert inbox.forwarding_confirmed_at == CONFIRMED_AT
    assert inbox.first_accepted_at is None


def test_a_milestone_keeps_the_first_time_it_happened(
    repository: DynamoDBUserInboxRepository,
) -> None:
    """Mail is delivered at least once, so the same confirmation can be
    handled twice. The honest answer is the first time, not the retry.
    """
    repository.save(_inbox(address="a@inbound.test"))
    repository.mark_first_accepted(
        address=EmailAddress("a@inbound.test"),
        accepted_at=CONFIRMED_AT,
    )
    repository.mark_first_accepted(
        address=EmailAddress("a@inbound.test"),
        accepted_at=LATER,
    )

    assert repository.find_by_user(USER_ID)[0].first_accepted_at == CONFIRMED_AT


def test_an_alias_nobody_registered_is_not_conjured_into_an_inbox(
    repository: DynamoDBUserInboxRepository,
) -> None:
    marked = repository.mark_forwarding_confirmed(
        address=EmailAddress("nobody@inbound.test"),
        confirmed_at=CONFIRMED_AT,
    )

    assert marked is False
    assert repository.find_by_address(EmailAddress("nobody@inbound.test")) is None


def test_editing_approved_senders_does_not_erase_a_confirmation(
    repository: DynamoDBUserInboxRepository,
) -> None:
    """The two writers run at once — the ingest worker marking a confirmation
    while its owner edits their senders in a browser. A full-item write here
    would drop a checkmark that never comes back, because Google does not
    send the mail twice.
    """
    repository.save(_inbox(address="a@inbound.test"))
    repository.mark_forwarding_confirmed(
        address=EmailAddress("a@inbound.test"),
        confirmed_at=CONFIRMED_AT,
    )

    repository.save(_inbox(address="a@inbound.test", domains=frozenset({"other.com"})))

    inbox = repository.find_by_user(USER_ID)[0]
    assert inbox.forwarding_confirmed_at == CONFIRMED_AT
    assert inbox.sender_policy.allowed_domains == frozenset({"other.com"})
