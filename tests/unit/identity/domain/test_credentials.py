from datetime import timedelta

import pytest

from personal_finance.contexts.identity.domain.credentials import (
    MAX_CODE_ATTEMPTS,
    MAX_SENDS_PER_WINDOW,
    MIN_SECONDS_BETWEEN_SENDS,
    EmailVerification,
    PasswordResetTicket,
    SendWindow,
)
from personal_finance.contexts.identity.domain.exceptions import (
    DeliveryThrottledError,
    TooManyVerificationAttemptsError,
    VerificationExpiredError,
)
from personal_finance.contexts.identity.domain.value_objects import Email, SecretHash
from personal_finance.shared.domain.value_objects import PosixTime, UserId


NOW = PosixTime.from_epoch_seconds(1_700_000_000)
EMAIL = Email("person@example.com")
CODE_HASH = SecretHash("hashed:123456")
TICKET_HASH = SecretHash("hashed:ticket")


def _later(seconds: int) -> PosixTime:
    return PosixTime.from_datetime(NOW.to_datetime() + timedelta(seconds=seconds))


def _verification(now: PosixTime = NOW) -> EmailVerification:
    return EmailVerification.issue(
        email=EMAIL,
        code_hash=CODE_HASH,
        now=now,
        code_ttl_minutes=15,
        window_minutes=60,
    )


# ----------------------------------------------------------------------
# The send window
# ----------------------------------------------------------------------


def test_a_second_message_in_the_same_minute_is_refused() -> None:
    window = SendWindow.opened(now=NOW, window_minutes=60)

    with pytest.raises(DeliveryThrottledError) as raised:
        window.extended(_later(MIN_SECONDS_BETWEEN_SENDS - 1))

    assert raised.value.retry_after_seconds == 1


def test_a_message_after_the_interval_is_allowed_and_counted() -> None:
    window = SendWindow.opened(now=NOW, window_minutes=60)

    extended = window.extended(_later(MIN_SECONDS_BETWEEN_SENDS))

    assert extended.sends == 2
    # The window itself does not move: five messages buy an hour of quiet,
    # not an hour from the last one.
    assert extended.expires_at == window.expires_at


def test_the_window_caps_how_many_messages_one_address_can_cause() -> None:
    window = SendWindow.opened(now=NOW, window_minutes=60)

    for send in range(1, MAX_SENDS_PER_WINDOW):
        window = window.extended(_later(MIN_SECONDS_BETWEEN_SENDS * send))

    assert window.sends == MAX_SENDS_PER_WINDOW

    with pytest.raises(DeliveryThrottledError) as raised:
        window.extended(_later(3_000))

    # Long enough to be honest about it: the caller has to wait out the window.
    assert raised.value.retry_after_seconds > MIN_SECONDS_BETWEEN_SENDS


def test_a_window_reports_when_it_is_over() -> None:
    window = SendWindow.opened(now=NOW, window_minutes=60)

    assert not window.is_over(_later(3_599))
    assert window.is_over(_later(3_600))


# ----------------------------------------------------------------------
# The verification challenge
# ----------------------------------------------------------------------


def test_a_code_can_be_guessed_a_bounded_number_of_times() -> None:
    verification = _verification()

    for _ in range(MAX_CODE_ATTEMPTS):
        verification.record_attempt(NOW)

    with pytest.raises(TooManyVerificationAttemptsError):
        verification.record_attempt(NOW)


def test_an_expired_code_is_refused_before_it_is_compared() -> None:
    verification = _verification()

    with pytest.raises(VerificationExpiredError):
        verification.record_attempt(_later(15 * 60))


def test_expiry_is_checked_before_the_attempt_cap() -> None:
    """Both are refusals, but they tell the caller different things to do.

    An expired code says "ask for a new one" and a spent one says "you have
    run out", and a challenge that is both should say the first.
    """
    verification = _verification()

    for _ in range(MAX_CODE_ATTEMPTS):
        verification.record_attempt(NOW)

    with pytest.raises(VerificationExpiredError):
        verification.record_attempt(_later(15 * 60))


def test_accepting_a_code_retires_it_and_issues_a_ticket() -> None:
    verification = _verification()
    verification.record_attempt(NOW)

    verification.accept(ticket_hash=TICKET_HASH, now=NOW, ticket_ttl_minutes=30)

    assert verification.ticket_matches(ticket_hash=TICKET_HASH, now=NOW)
    # The digits that worked cannot work twice: a replay of the same confirm
    # request must not hand out a second ticket.
    with pytest.raises(VerificationExpiredError):
        verification.record_attempt(NOW)


def test_a_ticket_stops_matching_once_it_expires() -> None:
    verification = _verification()
    verification.accept(ticket_hash=TICKET_HASH, now=NOW, ticket_ttl_minutes=30)

    assert verification.ticket_matches(ticket_hash=TICKET_HASH, now=_later(30 * 60 - 1))
    assert not verification.ticket_matches(ticket_hash=TICKET_HASH, now=_later(30 * 60))


def test_a_challenge_with_no_ticket_matches_nothing() -> None:
    assert not _verification().ticket_matches(ticket_hash=TICKET_HASH, now=NOW)


def test_another_ticket_does_not_match() -> None:
    verification = _verification()
    verification.accept(ticket_hash=TICKET_HASH, now=NOW, ticket_ttl_minutes=30)

    assert not verification.ticket_matches(
        ticket_hash=SecretHash("hashed:someone-elses"),
        now=NOW,
    )


def test_a_new_code_resets_the_attempts_but_not_the_window() -> None:
    verification = _verification()
    verification.record_attempt(NOW)
    verification.record_attempt(NOW)

    verification.reissue(
        code_hash=SecretHash("hashed:654321"),
        now=_later(MIN_SECONDS_BETWEEN_SENDS),
        code_ttl_minutes=15,
    )

    # The cap exists to stop guessing at *a* code, and this is another one.
    assert verification.attempts == 0
    # The window is what stops this being an unlimited way to mail somebody.
    assert verification.window.sends == 2


def test_asking_for_a_new_code_too_soon_is_refused() -> None:
    verification = _verification()

    with pytest.raises(DeliveryThrottledError):
        verification.reissue(code_hash=CODE_HASH, now=NOW, code_ttl_minutes=15)


def test_a_ticket_already_handed_out_survives_a_new_code() -> None:
    # Somebody who is already filling in the registration form should not be
    # stranded because they pressed "send it again" first.
    verification = _verification()
    verification.accept(ticket_hash=TICKET_HASH, now=NOW, ticket_ttl_minutes=30)

    verification.reissue(
        code_hash=SecretHash("hashed:654321"),
        now=_later(MIN_SECONDS_BETWEEN_SENDS),
        code_ttl_minutes=15,
    )

    assert verification.ticket_matches(ticket_hash=TICKET_HASH, now=NOW)


def test_the_state_a_challenge_is_recognised_by_moves_with_every_change() -> None:
    verification = _verification()
    first = verification.state

    verification.record_attempt(NOW)

    assert verification.state != first


# ----------------------------------------------------------------------
# The reset ticket
# ----------------------------------------------------------------------


def test_a_reset_ticket_expires() -> None:
    ticket = PasswordResetTicket(
        token_hash=SecretHash("hashed:token"),
        user_id=UserId.new(),
        email=EMAIL,
        expires_at=_later(1_800),
    )

    assert not ticket.is_expired(_later(1_799))
    assert ticket.is_expired(_later(1_800))
