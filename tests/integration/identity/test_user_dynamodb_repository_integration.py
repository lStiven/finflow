from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.value_objects import (
    Email,
    PasswordHash,
    PersonName,
)
from personal_finance.contexts.identity.infrastructure.persistence.dynamodb import (
    PARTITION_KEY,
    DynamoDBUserRepository,
)
from personal_finance.shared.domain.value_objects import PosixTime
from personal_finance.shared.infrastructure.aws.provisioning import provision_table


TABLE_NAME = "users"


@pytest.fixture
def repository(dynamodb_client: DynamoDBClient) -> DynamoDBUserRepository:
    provision_table(
        dynamodb_client,
        table_name=TABLE_NAME,
        partition_key=PARTITION_KEY,
        enable_ttl=False,
    )

    return DynamoDBUserRepository(client=dynamodb_client, table_name=TABLE_NAME)


def _user(*, email: str = "person@example.com") -> User:
    return User.register(
        email=Email(email),
        password_hash=PasswordHash("$2b$12$abcdefg"),
        registered_at=PosixTime.now(),
    )


def test_first_write_stores_the_user(repository: DynamoDBUserRepository) -> None:
    assert repository.add_if_new(_user()) is None


def test_conditional_write_rejects_a_duplicate_email(
    repository: DynamoDBUserRepository,
) -> None:
    first = _user()
    repository.add_if_new(first)

    stored = repository.add_if_new(_user())

    assert stored is not None
    assert stored.id == first.id


def test_different_emails_do_not_collide(
    repository: DynamoDBUserRepository,
) -> None:
    assert repository.add_if_new(_user(email="one@example.com")) is None
    assert repository.add_if_new(_user(email="two@example.com")) is None


def test_find_by_email_returns_none_for_an_unregistered_address(
    repository: DynamoDBUserRepository,
) -> None:
    assert repository.find_by_email(Email("nobody@example.com")) is None


def test_find_by_email_returns_the_stored_user(
    repository: DynamoDBUserRepository,
) -> None:
    user = _user()
    repository.add_if_new(user)

    found = repository.find_by_email(user.email)

    assert found is not None
    assert found.id == user.id


def test_rename_writes_the_new_name_in_place(
    repository: DynamoDBUserRepository,
) -> None:
    user = _user()
    repository.add_if_new(user)
    user.rename(PersonName("Ada Lovelace"))

    assert repository.rename(user) is True

    stored = repository.find_by_email(user.email)
    assert stored is not None
    assert stored.name == PersonName("Ada Lovelace")
    # The rest of the record is untouched — the password hash in particular
    # is never rewritten from the copy the caller happened to be holding.
    assert stored.password_hash == user.password_hash
    assert stored.id == user.id


def test_rename_reports_failure_for_an_account_that_is_not_there(
    repository: DynamoDBUserRepository,
) -> None:
    user = _user()
    user.rename(PersonName("Ada Lovelace"))

    assert repository.rename(user) is False


def test_rename_refuses_an_account_whose_address_now_belongs_to_someone_else(
    repository: DynamoDBUserRepository,
) -> None:
    # Same address, different account: the id guard is what keeps a stale
    # handle from editing whoever holds the address now.
    current = _user()
    repository.add_if_new(current)
    stale = _user()
    stale.rename(PersonName("Ada Lovelace"))

    assert repository.rename(stale) is False
    stored = repository.find_by_email(current.email)
    assert stored is not None
    assert stored.name is None
