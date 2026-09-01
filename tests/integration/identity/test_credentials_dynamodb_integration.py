"""The conditional writes, against a real DynamoDB.

The one-time-ness of a code, a ticket and a reset link is not a property of
the use cases: it is a property of the condition expressions underneath them.
An in-memory double can be written to agree with whatever the use case does,
which is exactly why these run against the real thing.
"""

from datetime import timedelta

from mypy_boto3_dynamodb.client import DynamoDBClient
import pytest

from personal_finance.contexts.identity.domain.credentials import (
    EmailVerification,
    PasswordResetTicket,
    PasswordResetWindow,
    SendWindow,
)
from personal_finance.contexts.identity.domain.value_objects import Email, SecretHash
from personal_finance.contexts.identity.infrastructure.persistence.credentials_dynamodb import (  # noqa: E501
    PARTITION_KEY,
    DynamoDBEmailVerificationRepository,
    DynamoDBPasswordResetRepository,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId
from personal_finance.shared.infrastructure.aws.provisioning import provision_table


TABLE_NAME = "credential_challenges"
EMAIL = Email("person@example.com")
CODE_HASH = SecretHash("hashed:123456")
TICKET_HASH = SecretHash("hashed:ticket")
TOKEN_HASH = SecretHash("hashed:reset-token")
NOW = PosixTime.from_epoch_seconds(1_700_000_000)


def _later(seconds: int) -> PosixTime:
    return PosixTime.from_datetime(NOW.to_datetime() + timedelta(seconds=seconds))


@pytest.fixture
def table(dynamodb_client: DynamoDBClient) -> DynamoDBClient:
    provision_table(
        dynamodb_client,
        table_name=TABLE_NAME,
        partition_key=PARTITION_KEY,
        enable_ttl=True,
    )

    return dynamodb_client


@pytest.fixture
def verifications(table: DynamoDBClient) -> DynamoDBEmailVerificationRepository:
    return DynamoDBEmailVerificationRepository(client=table, table_name=TABLE_NAME)


@pytest.fixture
def resets(table: DynamoDBClient) -> DynamoDBPasswordResetRepository:
    return DynamoDBPasswordResetRepository(client=table, table_name=TABLE_NAME)


def _verification() -> EmailVerification:
    return EmailVerification.issue(
        email=EMAIL,
        code_hash=CODE_HASH,
        now=NOW,
        code_ttl_minutes=15,
        window_minutes=60,
    )


# ----------------------------------------------------------------------
# The verification challenge
# ----------------------------------------------------------------------


def test_a_challenge_round_trips(
    verifications: DynamoDBEmailVerificationRepository,
) -> None:
    verification = _verification()

    assert verifications.save(verification, expected=None)

    stored = verifications.find(EMAIL)
    assert stored is not None
    assert stored.code_hash == CODE_HASH
    assert stored.window.sends == 1
    assert stored.attempts == 0
    assert stored.ticket_hash is None


def test_an_unknown_address_has_no_challenge(
    verifications: DynamoDBEmailVerificationRepository,
) -> None:
    assert verifications.find(Email("nobody@example.com")) is None


def test_a_first_write_refuses_to_overwrite_an_existing_challenge(
    verifications: DynamoDBEmailVerificationRepository,
) -> None:
    """`expected=None` means "there was nothing here".

    If something appeared in between, the write must lose: otherwise two
    requests a millisecond apart each reset the other's send window, and the
    window stops capping anything.
    """
    verifications.save(_verification(), expected=None)

    assert not verifications.save(_verification(), expected=None)


def test_a_write_lands_only_on_the_record_it_read(
    verifications: DynamoDBEmailVerificationRepository,
) -> None:
    verifications.save(_verification(), expected=None)
    read = verifications.find(EMAIL)
    assert read is not None
    stale = read.state

    read.record_attempt(NOW)
    assert verifications.save(read, expected=stale)

    # A second write carrying the same stale counters is the losing half of a
    # race, and is refused.
    other = _verification()
    other.record_attempt(NOW)
    assert not verifications.save(other, expected=stale)


def test_a_ticket_is_spent_exactly_once(
    verifications: DynamoDBEmailVerificationRepository,
) -> None:
    verification = _verification()
    verification.accept(ticket_hash=TICKET_HASH, now=NOW, ticket_ttl_minutes=30)
    verifications.save(verification, expected=None)

    assert verifications.consume_ticket(
        email=EMAIL,
        ticket_hash=TICKET_HASH,
        now=NOW,
    )
    # One verified address, one account.
    assert not verifications.consume_ticket(
        email=EMAIL,
        ticket_hash=TICKET_HASH,
        now=NOW,
    )


def test_another_ticket_does_not_spend_this_one(
    verifications: DynamoDBEmailVerificationRepository,
) -> None:
    verification = _verification()
    verification.accept(ticket_hash=TICKET_HASH, now=NOW, ticket_ttl_minutes=30)
    verifications.save(verification, expected=None)

    assert not verifications.consume_ticket(
        email=EMAIL,
        ticket_hash=SecretHash("hashed:someone-elses"),
        now=NOW,
    )
    assert verifications.find(EMAIL) is not None


def test_an_expired_ticket_is_refused_even_though_the_record_is_still_there(
    verifications: DynamoDBEmailVerificationRepository,
) -> None:
    """The table's own sweep is eventual and can be hours late.

    An item whose time-to-live passed is still readable, so the expiry has to
    be part of the condition rather than left to DynamoDB.
    """
    verification = _verification()
    verification.accept(ticket_hash=TICKET_HASH, now=NOW, ticket_ttl_minutes=30)
    verifications.save(verification, expected=None)

    assert not verifications.consume_ticket(
        email=EMAIL,
        ticket_hash=TICKET_HASH,
        now=_later(30 * 60),
    )


def test_a_challenge_with_no_ticket_cannot_be_spent(
    verifications: DynamoDBEmailVerificationRepository,
) -> None:
    verifications.save(_verification(), expected=None)

    assert not verifications.consume_ticket(
        email=EMAIL,
        ticket_hash=TICKET_HASH,
        now=NOW,
    )


# ----------------------------------------------------------------------
# Reset links and the window that caps them
# ----------------------------------------------------------------------


def _window(*, token_hash: SecretHash | None = None) -> PasswordResetWindow:
    return PasswordResetWindow(
        email=EMAIL,
        window=SendWindow.opened(now=NOW, window_minutes=60),
        issued_token_hash=token_hash,
    )


def _ticket(*, expires_at: PosixTime) -> PasswordResetTicket:
    return PasswordResetTicket(
        token_hash=TOKEN_HASH,
        user_id=UserId.new(),
        email=EMAIL,
        expires_at=expires_at,
    )


def test_a_reset_window_round_trips(resets: DynamoDBPasswordResetRepository) -> None:
    assert resets.save_window(_window(token_hash=TOKEN_HASH), expected_sends=None)

    stored = resets.find_window(EMAIL)
    assert stored is not None
    assert stored.window.sends == 1
    assert stored.issued_token_hash == TOKEN_HASH


def test_a_reset_window_write_lands_only_on_the_record_it_read(
    resets: DynamoDBPasswordResetRepository,
) -> None:
    resets.save_window(_window(), expected_sends=None)

    assert not resets.save_window(_window(), expected_sends=None)


def test_a_reset_link_is_spent_exactly_once(
    resets: DynamoDBPasswordResetRepository,
) -> None:
    ticket = _ticket(expires_at=_later(1_800))
    resets.save_ticket(ticket)

    spent = resets.consume_ticket(token_hash=TOKEN_HASH, now=NOW)

    assert spent is not None
    assert spent.user_id == ticket.user_id
    assert spent.email == EMAIL
    assert resets.consume_ticket(token_hash=TOKEN_HASH, now=NOW) is None


def test_an_expired_reset_link_is_refused_while_the_record_survives(
    resets: DynamoDBPasswordResetRepository,
) -> None:
    resets.save_ticket(_ticket(expires_at=_later(1_800)))

    assert resets.consume_ticket(token_hash=TOKEN_HASH, now=_later(1_800)) is None


def test_an_unknown_reset_link_is_refused(
    resets: DynamoDBPasswordResetRepository,
) -> None:
    assert resets.consume_ticket(token_hash=SecretHash("hashed:made-up"), now=NOW) is (
        None
    )


def test_a_superseded_link_can_be_retired(
    resets: DynamoDBPasswordResetRepository,
) -> None:
    # Asking again replaces the link rather than leaving five of them alive.
    resets.save_ticket(_ticket(expires_at=_later(1_800)))

    resets.delete_ticket(TOKEN_HASH)

    assert resets.consume_ticket(token_hash=TOKEN_HASH, now=NOW) is None


def test_a_verification_and_a_reset_for_one_address_do_not_collide(
    verifications: DynamoDBEmailVerificationRepository,
    resets: DynamoDBPasswordResetRepository,
) -> None:
    # Three shapes share one table, told apart by a prefix on the key.
    verifications.save(_verification(), expected=None)
    resets.save_window(_window(), expected_sends=None)
    resets.save_ticket(_ticket(expires_at=_later(1_800)))

    assert verifications.find(EMAIL) is not None
    assert resets.find_window(EMAIL) is not None
    assert resets.consume_ticket(token_hash=TOKEN_HASH, now=NOW) is not None
