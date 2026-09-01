"""Asking for a code, and trading it for the ticket registration spends.

Both endpoints behind these are unauthenticated, so most of what is asserted
here is what they *do not* say: the same answer for an address that is
registered and one that is not, and one answer for every way a code can fail.
"""

import pytest

from personal_finance.contexts.identity.application.commands import (
    ConfirmEmailVerificationCommand,
    RequestEmailVerificationCommand,
)
from personal_finance.contexts.identity.application.credential_handlers import (
    ConfirmEmailVerificationUseCase,
    RequestEmailVerificationUseCase,
)
from personal_finance.contexts.identity.domain.credentials import (
    MAX_CODE_ATTEMPTS,
    EmailVerification,
    VerificationState,
)
from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.exceptions import (
    DeliveryThrottledError,
    InvalidVerificationCodeError,
    TooManyVerificationAttemptsError,
    VerificationExpiredError,
)
from personal_finance.contexts.identity.domain.value_objects import (
    Email,
    PasswordHash,
    SecretHash,
)
from personal_finance.shared.domain.value_objects import PosixTime


EMAIL = "person@example.com"
CODE = "123456"


class FakeSecretHasher:
    def hash(self, secret: str) -> SecretHash:
        return SecretHash(f"hashed:{secret}")

    def verify(self, secret: str, hashed: SecretHash) -> bool:
        return hashed.value == f"hashed:{secret}"


class CountingSecretHasher(FakeSecretHasher):
    """Counts comparisons, so a test can assert that a branch paid for one."""

    def __init__(self) -> None:
        self.verifications = 0

    def verify(self, secret: str, hashed: SecretHash) -> bool:
        self.verifications += 1

        return super().verify(secret, hashed)


class FixedSecretGenerator:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    def verification_code(self) -> str:
        return CODE

    def opaque_token(self) -> str:
        token = f"token-{len(self.tokens)}"
        self.tokens.append(token)

        return token


class InMemoryUserRepository:
    def __init__(self, *users: User) -> None:
        self.by_email = {user.email: user for user in users}

    def add_if_new(self, user: User) -> User | None:
        raise NotImplementedError

    def find_by_email(self, email: Email) -> User | None:
        return self.by_email.get(email)

    def rename(self, user: User) -> bool:
        raise NotImplementedError

    def change_password(self, user: User) -> bool:
        raise NotImplementedError


class InMemoryVerificationRepository:
    def __init__(self) -> None:
        self.stored: dict[Email, EmailVerification] = {}
        self.rejects_next_save = False

    def find(self, email: Email) -> EmailVerification | None:
        return self.stored.get(email)

    def save(
        self,
        verification: EmailVerification,
        *,
        expected: VerificationState | None,
    ) -> bool:
        if self.rejects_next_save:
            self.rejects_next_save = False

            return False

        current = self.stored.get(verification.email)
        seen = current.state if current is not None else None

        if seen != expected and current is not verification:
            return False

        self.stored[verification.email] = verification

        return True

    def consume_ticket(
        self,
        *,
        email: Email,
        ticket_hash: SecretHash,
        now: PosixTime,
    ) -> bool:
        raise NotImplementedError


class RecordingNotifier:
    def __init__(self) -> None:
        self.codes: list[tuple[str, str]] = []
        self.existing_account_notices: list[str] = []

    def send_verification_code(
        self,
        *,
        email: Email,
        code: str,
        expires_in_minutes: int,
    ) -> None:
        del expires_in_minutes
        self.codes.append((email.value, code))

    def send_registration_notice_for_existing_account(self, *, email: Email) -> None:
        self.existing_account_notices.append(email.value)

    def send_password_reset(
        self,
        *,
        email: Email,
        link: str,
        expires_in_minutes: int,
    ) -> None:
        raise NotImplementedError

    def send_password_reset_for_unknown_account(self, *, email: Email) -> None:
        raise NotImplementedError


def _request_use_case(
    *,
    verifications: InMemoryVerificationRepository,
    notifier: RecordingNotifier,
    users: InMemoryUserRepository | None = None,
    local_echo: bool = False,
) -> RequestEmailVerificationUseCase:
    return RequestEmailVerificationUseCase(
        verifications=verifications,
        users=users or InMemoryUserRepository(),
        generator=FixedSecretGenerator(),
        code_hasher=FakeSecretHasher(),
        notifier=notifier,
        code_ttl_minutes=15,
        window_minutes=60,
        local_echo=local_echo,
    )


def _confirm_use_case(
    verifications: InMemoryVerificationRepository,
) -> ConfirmEmailVerificationUseCase:
    return ConfirmEmailVerificationUseCase(
        verifications=verifications,
        generator=FixedSecretGenerator(),
        code_hasher=FakeSecretHasher(),
        ticket_hasher=FakeSecretHasher(),
        ticket_ttl_minutes=30,
    )


def _existing_account() -> User:
    return User.register(
        email=Email(EMAIL),
        password_hash=PasswordHash("hashed:whatever"),
        registered_at=PosixTime.now(),
    )


# ----------------------------------------------------------------------
# Asking for a code
# ----------------------------------------------------------------------


def test_a_code_is_mailed_and_the_challenge_is_stored() -> None:
    verifications = InMemoryVerificationRepository()
    notifier = RecordingNotifier()

    _request_use_case(verifications=verifications, notifier=notifier).execute(
        RequestEmailVerificationCommand(email=EMAIL),
    )

    assert notifier.codes == [(EMAIL, CODE)]
    assert Email(EMAIL) in verifications.stored


def test_the_stored_challenge_holds_a_hash_rather_than_the_code() -> None:
    verifications = InMemoryVerificationRepository()

    _request_use_case(
        verifications=verifications,
        notifier=RecordingNotifier(),
    ).execute(RequestEmailVerificationCommand(email=EMAIL))

    stored = verifications.stored[Email(EMAIL)]
    assert stored.code_hash.value != CODE
    assert stored.code_hash.to_dict() == "<redacted>"


def test_an_address_that_already_has_an_account_is_mailed_too() -> None:
    """The answer must not depend on whether the address is registered.

    A branch that stayed silent would turn this endpoint into a way to ask
    which addresses have accounts here, so the known case sends a different
    message rather than no message.
    """
    verifications = InMemoryVerificationRepository()
    notifier = RecordingNotifier()

    _request_use_case(
        verifications=verifications,
        notifier=notifier,
        users=InMemoryUserRepository(_existing_account()),
    ).execute(RequestEmailVerificationCommand(email=EMAIL))

    assert notifier.codes == []
    assert notifier.existing_account_notices == [EMAIL]


def test_a_known_address_is_throttled_the_same_way_an_unknown_one_is() -> None:
    # The window has to be written for both, or the endpoint answers "this
    # address is registered" by never refusing.
    verifications = InMemoryVerificationRepository()
    use_case = _request_use_case(
        verifications=verifications,
        notifier=RecordingNotifier(),
        users=InMemoryUserRepository(_existing_account()),
    )
    use_case.execute(RequestEmailVerificationCommand(email=EMAIL))

    with pytest.raises(DeliveryThrottledError):
        use_case.execute(RequestEmailVerificationCommand(email=EMAIL))


def test_a_second_request_a_moment_later_is_refused() -> None:
    verifications = InMemoryVerificationRepository()
    use_case = _request_use_case(
        verifications=verifications,
        notifier=RecordingNotifier(),
    )
    use_case.execute(RequestEmailVerificationCommand(email=EMAIL))

    with pytest.raises(DeliveryThrottledError) as raised:
        use_case.execute(RequestEmailVerificationCommand(email=EMAIL))

    assert raised.value.retry_after_seconds > 0


def test_a_lost_race_on_the_challenge_is_a_refusal_rather_than_a_second_mail() -> None:
    # Somebody else moved the record between the read and the write, which
    # means they just caused a message to this address.
    verifications = InMemoryVerificationRepository()
    notifier = RecordingNotifier()
    verifications.rejects_next_save = True

    with pytest.raises(DeliveryThrottledError):
        _request_use_case(verifications=verifications, notifier=notifier).execute(
            RequestEmailVerificationCommand(email=EMAIL),
        )

    assert notifier.codes == []


def test_the_code_is_never_in_the_answer_outside_a_developers_machine() -> None:
    requested = _request_use_case(
        verifications=InMemoryVerificationRepository(),
        notifier=RecordingNotifier(),
    ).execute(RequestEmailVerificationCommand(email=EMAIL))

    assert requested.code is None


def test_the_code_is_echoed_back_only_where_no_mail_is_sent_at_all() -> None:
    requested = _request_use_case(
        verifications=InMemoryVerificationRepository(),
        notifier=RecordingNotifier(),
        local_echo=True,
    ).execute(RequestEmailVerificationCommand(email=EMAIL))

    assert requested.code == CODE


def test_a_malformed_address_is_refused_before_anything_is_written() -> None:
    verifications = InMemoryVerificationRepository()

    with pytest.raises(ValueError, match="Invalid email"):
        _request_use_case(
            verifications=verifications,
            notifier=RecordingNotifier(),
        ).execute(RequestEmailVerificationCommand(email="not-an-address"))

    assert verifications.stored == {}


# ----------------------------------------------------------------------
# Trading the code for a ticket
# ----------------------------------------------------------------------


def _with_a_pending_code() -> InMemoryVerificationRepository:
    verifications = InMemoryVerificationRepository()
    _request_use_case(
        verifications=verifications,
        notifier=RecordingNotifier(),
    ).execute(RequestEmailVerificationCommand(email=EMAIL))

    return verifications


def test_the_right_code_is_traded_for_a_ticket() -> None:
    verifications = _with_a_pending_code()

    ticket = _confirm_use_case(verifications).execute(
        ConfirmEmailVerificationCommand(email=EMAIL, code=CODE),
    )

    assert ticket.token
    stored = verifications.stored[Email(EMAIL)]
    # Only the hash is kept, so a leak of the table hands out no tickets.
    assert stored.ticket_hash is not None
    assert stored.ticket_hash.value != ticket.token


def test_spacing_a_pasted_code_still_works() -> None:
    verifications = _with_a_pending_code()

    ticket = _confirm_use_case(verifications).execute(
        ConfirmEmailVerificationCommand(email=EMAIL, code=" 123 456 "),
    )

    assert ticket.token


def test_a_wrong_code_is_refused_and_counted() -> None:
    verifications = _with_a_pending_code()

    with pytest.raises(InvalidVerificationCodeError):
        _confirm_use_case(verifications).execute(
            ConfirmEmailVerificationCommand(email=EMAIL, code="000000"),
        )

    # The attempt was persisted, which is what makes the cap mean anything.
    assert verifications.stored[Email(EMAIL)].attempts == 1


def test_an_address_with_no_challenge_gets_the_same_answer_as_a_wrong_code() -> None:
    with pytest.raises(InvalidVerificationCodeError):
        _confirm_use_case(InMemoryVerificationRepository()).execute(
            ConfirmEmailVerificationCommand(email=EMAIL, code=CODE),
        )


def test_an_address_with_no_challenge_still_costs_a_hash_comparison() -> None:
    """The two answers are identical; the time they take has to be too.

    Without the decoy, "no challenge for this address" would return before the
    hash comparison the other branch pays for — and the difference is exactly
    "is somebody part-way through registering this address".
    """
    hasher = CountingSecretHasher()
    use_case = ConfirmEmailVerificationUseCase(
        verifications=InMemoryVerificationRepository(),
        generator=FixedSecretGenerator(),
        code_hasher=hasher,
        ticket_hasher=FakeSecretHasher(),
        ticket_ttl_minutes=30,
    )
    # Building the use case draws the decoy; only comparisons are counted.
    before = hasher.verifications

    with pytest.raises(InvalidVerificationCodeError):
        use_case.execute(ConfirmEmailVerificationCommand(email=EMAIL, code=CODE))

    assert hasher.verifications == before + 1


def test_guessing_runs_out() -> None:
    verifications = _with_a_pending_code()
    use_case = _confirm_use_case(verifications)

    for _ in range(MAX_CODE_ATTEMPTS):
        with pytest.raises(InvalidVerificationCodeError):
            use_case.execute(
                ConfirmEmailVerificationCommand(email=EMAIL, code="000000"),
            )

    with pytest.raises(TooManyVerificationAttemptsError):
        use_case.execute(ConfirmEmailVerificationCommand(email=EMAIL, code="000000"))

    # And the right code no longer helps: the challenge is spent, not paused.
    with pytest.raises(TooManyVerificationAttemptsError):
        use_case.execute(ConfirmEmailVerificationCommand(email=EMAIL, code=CODE))


def test_an_attempt_that_could_not_be_counted_does_not_happen() -> None:
    """A save that loses its race means another request is already using this
    challenge. Handing out a ticket anyway would let two confirms racing the
    same code both succeed.
    """
    verifications = _with_a_pending_code()
    verifications.rejects_next_save = True

    with pytest.raises(InvalidVerificationCodeError):
        _confirm_use_case(verifications).execute(
            ConfirmEmailVerificationCommand(email=EMAIL, code=CODE),
        )


def test_the_same_code_cannot_be_traded_twice() -> None:
    verifications = _with_a_pending_code()
    use_case = _confirm_use_case(verifications)
    use_case.execute(ConfirmEmailVerificationCommand(email=EMAIL, code=CODE))

    with pytest.raises(VerificationExpiredError):
        use_case.execute(ConfirmEmailVerificationCommand(email=EMAIL, code=CODE))
