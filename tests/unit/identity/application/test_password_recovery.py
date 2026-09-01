"""Forgetting a password, resetting it through the mail, and changing it.

The properties that matter here are not "it works": they are that the link is
single-use, that the endpoint says nothing about who is registered, and that a
password that changes takes every session opened with the old one with it.
"""

from collections.abc import Sequence

import pytest

from personal_finance.contexts.identity.application.commands import (
    ChangePasswordCommand,
    RequestPasswordResetCommand,
    ResetPasswordCommand,
)
from personal_finance.contexts.identity.application.credential_handlers import (
    ChangePasswordUseCase,
    RequestPasswordResetUseCase,
    ResetPasswordUseCase,
)
from personal_finance.contexts.identity.application.ports import (
    AccessToken,
    AuthenticatedUser,
)
from personal_finance.contexts.identity.domain.credentials import (
    PasswordResetTicket,
    PasswordResetWindow,
)
from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.events import PasswordChanged
from personal_finance.contexts.identity.domain.exceptions import (
    DeliveryThrottledError,
    InvalidCredentialsError,
    InvalidPasswordResetTokenError,
    PasswordUnchangedError,
    UserNotFoundError,
)
from personal_finance.contexts.identity.domain.policies import WeakPasswordError
from personal_finance.contexts.identity.domain.value_objects import (
    Email,
    PasswordHash,
    SecretHash,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import PosixTime, UserId


EMAIL = "person@example.com"
PASSWORD = "correct horse battery staple"
NEW_PASSWORD = "a whole different password"
RESET_URL = "https://app.example.com/restablecer"


class FakeHasher:
    def hash(self, password: str) -> PasswordHash:
        return PasswordHash(f"hashed:{password}")

    def verify(self, password: str, hashed: PasswordHash) -> bool:
        return hashed.value == f"hashed:{password}"


class FakeSecretHasher:
    def hash(self, secret: str) -> SecretHash:
        return SecretHash(f"hashed:{secret}")

    def verify(self, secret: str, hashed: SecretHash) -> bool:
        return hashed.value == f"hashed:{secret}"


class FixedSecretGenerator:
    def __init__(self) -> None:
        self.tokens: list[str] = []

    def verification_code(self) -> str:
        return "123456"

    def opaque_token(self) -> str:
        token = f"token-{len(self.tokens)}"
        self.tokens.append(token)

        return token


class InMemoryUserRepository:
    def __init__(self, *users: User) -> None:
        self.by_email: dict[Email, User] = {user.email: user for user in users}

    def add_if_new(self, user: User) -> User | None:
        raise NotImplementedError

    def find_by_email(self, email: Email) -> User | None:
        return self.by_email.get(email)

    def rename(self, user: User) -> bool:
        raise NotImplementedError

    def change_password(self, user: User) -> bool:
        stored = self.by_email.get(user.email)

        if stored is None or stored.id != user.id:
            return False

        stored.password_hash = user.password_hash
        stored.credential_version = user.credential_version

        return True


class InMemoryPasswordResetRepository:
    def __init__(self) -> None:
        self.windows: dict[Email, PasswordResetWindow] = {}
        self.tickets: dict[str, PasswordResetTicket] = {}

    def find_window(self, email: Email) -> PasswordResetWindow | None:
        return self.windows.get(email)

    def save_window(
        self,
        window: PasswordResetWindow,
        *,
        expected_sends: int | None,
    ) -> bool:
        current = self.windows.get(window.email)
        seen = current.window.sends if current is not None else None

        if seen != expected_sends:
            return False

        self.windows[window.email] = window

        return True

    def save_ticket(self, ticket: PasswordResetTicket) -> None:
        self.tickets[ticket.token_hash.value] = ticket

    def delete_ticket(self, token_hash: SecretHash) -> None:
        self.tickets.pop(token_hash.value, None)

    def consume_ticket(
        self,
        *,
        token_hash: SecretHash,
        now: PosixTime,
    ) -> PasswordResetTicket | None:
        ticket = self.tickets.pop(token_hash.value, None)

        if ticket is None or ticket.is_expired(now):
            return None

        return ticket


class RecordingNotifier:
    def __init__(self) -> None:
        self.reset_links: list[tuple[str, str]] = []
        self.unknown_account_notices: list[str] = []

    def send_verification_code(
        self,
        *,
        email: Email,
        code: str,
        expires_in_minutes: int,
    ) -> None:
        raise NotImplementedError

    def send_registration_notice_for_existing_account(self, *, email: Email) -> None:
        raise NotImplementedError

    def send_password_reset(
        self,
        *,
        email: Email,
        link: str,
        expires_in_minutes: int,
    ) -> None:
        del expires_in_minutes
        self.reset_links.append((email.value, link))

    def send_password_reset_for_unknown_account(self, *, email: Email) -> None:
        self.unknown_account_notices.append(email.value)


class RecordingEventPublisher:
    def __init__(self) -> None:
        self.published: list[Event] = []

    def publish(self, events: Sequence[Event]) -> None:
        self.published.extend(events)


class RecordingTokenIssuer:
    def __init__(self) -> None:
        self.issued_for: list[AuthenticatedUser] = []

    def issue(self, user: AuthenticatedUser) -> AccessToken:
        self.issued_for.append(user)

        return AccessToken(value="a-token", expires_at=PosixTime.now())

    def verify(self, token: str) -> AuthenticatedUser:
        raise NotImplementedError


def _account() -> User:
    user = User.register(
        email=Email(EMAIL),
        password_hash=PasswordHash(f"hashed:{PASSWORD}"),
        registered_at=PosixTime.from_epoch_seconds(1_700_000_000),
    )
    user.pull_events()

    return user


def _request_use_case(
    *,
    users: InMemoryUserRepository,
    resets: InMemoryPasswordResetRepository,
    notifier: RecordingNotifier,
) -> RequestPasswordResetUseCase:
    return RequestPasswordResetUseCase(
        resets=resets,
        users=users,
        generator=FixedSecretGenerator(),
        token_hasher=FakeSecretHasher(),
        notifier=notifier,
        reset_url=RESET_URL,
        ttl_minutes=30,
        window_minutes=60,
    )


def _reset_use_case(
    *,
    users: InMemoryUserRepository,
    resets: InMemoryPasswordResetRepository,
    event_publisher: RecordingEventPublisher | None = None,
) -> ResetPasswordUseCase:
    return ResetPasswordUseCase(
        resets=resets,
        users=users,
        hasher=FakeHasher(),
        token_hasher=FakeSecretHasher(),
        event_publisher=event_publisher or RecordingEventPublisher(),
    )


def _token_from(link: str) -> str:
    return link.split("token=", 1)[1]


# ----------------------------------------------------------------------
# Asking for a link
# ----------------------------------------------------------------------


def test_a_reset_link_is_mailed_to_an_address_that_has_an_account() -> None:
    resets = InMemoryPasswordResetRepository()
    notifier = RecordingNotifier()

    _request_use_case(
        users=InMemoryUserRepository(_account()),
        resets=resets,
        notifier=notifier,
    ).execute(RequestPasswordResetCommand(email=EMAIL))

    [(address, link)] = notifier.reset_links
    assert address == EMAIL
    assert link.startswith(f"{RESET_URL}?token=")
    assert resets.tickets


def test_only_the_hash_of_the_link_is_stored() -> None:
    resets = InMemoryPasswordResetRepository()
    notifier = RecordingNotifier()

    _request_use_case(
        users=InMemoryUserRepository(_account()),
        resets=resets,
        notifier=notifier,
    ).execute(RequestPasswordResetCommand(email=EMAIL))

    [(_, link)] = notifier.reset_links
    # A leak of the table would hand out no working links: what is stored is
    # the hash, and the hash is also the key it is found by.
    assert _token_from(link) not in resets.tickets


def test_an_unknown_address_is_answered_with_mail_rather_than_silence() -> None:
    """Silence for an unknown address is itself an answer.

    It would make this endpoint a way to ask which addresses have accounts
    here, one request at a time.
    """
    notifier = RecordingNotifier()

    _request_use_case(
        users=InMemoryUserRepository(),
        resets=InMemoryPasswordResetRepository(),
        notifier=notifier,
    ).execute(RequestPasswordResetCommand(email=EMAIL))

    assert notifier.reset_links == []
    assert notifier.unknown_account_notices == [EMAIL]


def test_an_unknown_address_is_throttled_the_same_way_a_known_one_is() -> None:
    resets = InMemoryPasswordResetRepository()
    use_case = _request_use_case(
        users=InMemoryUserRepository(),
        resets=resets,
        notifier=RecordingNotifier(),
    )
    use_case.execute(RequestPasswordResetCommand(email=EMAIL))

    with pytest.raises(DeliveryThrottledError):
        use_case.execute(RequestPasswordResetCommand(email=EMAIL))


def test_no_ticket_is_written_for_an_address_with_no_account() -> None:
    resets = InMemoryPasswordResetRepository()

    _request_use_case(
        users=InMemoryUserRepository(),
        resets=resets,
        notifier=RecordingNotifier(),
    ).execute(RequestPasswordResetCommand(email=EMAIL))

    assert resets.tickets == {}


# ----------------------------------------------------------------------
# Spending it
# ----------------------------------------------------------------------


def _with_a_live_link() -> tuple[
    InMemoryUserRepository,
    InMemoryPasswordResetRepository,
    str,
]:
    users = InMemoryUserRepository(_account())
    resets = InMemoryPasswordResetRepository()
    notifier = RecordingNotifier()
    _request_use_case(users=users, resets=resets, notifier=notifier).execute(
        RequestPasswordResetCommand(email=EMAIL),
    )
    [(_, link)] = notifier.reset_links

    return users, resets, _token_from(link)


def test_a_link_sets_the_new_password() -> None:
    users, resets, token = _with_a_live_link()

    _reset_use_case(users=users, resets=resets).execute(
        ResetPasswordCommand(token=token, new_password=NEW_PASSWORD),
    )

    stored = users.by_email[Email(EMAIL)]
    assert stored.password_hash == PasswordHash(f"hashed:{NEW_PASSWORD}")


def test_a_link_works_once() -> None:
    users, resets, token = _with_a_live_link()
    use_case = _reset_use_case(users=users, resets=resets)
    use_case.execute(ResetPasswordCommand(token=token, new_password=NEW_PASSWORD))

    with pytest.raises(InvalidPasswordResetTokenError):
        use_case.execute(
            ResetPasswordCommand(token=token, new_password="yet another password"),
        )


def test_an_unknown_link_is_refused() -> None:
    users, resets, _ = _with_a_live_link()

    with pytest.raises(InvalidPasswordResetTokenError):
        _reset_use_case(users=users, resets=resets).execute(
            ResetPasswordCommand(token="never-issued", new_password=NEW_PASSWORD),
        )


def test_a_weak_password_does_not_burn_the_link() -> None:
    """The policy check comes first on purpose.

    A password the policy refuses is the caller's own typo, and spending their
    one link over it would send them back to their inbox for nothing.
    """
    users, resets, token = _with_a_live_link()
    use_case = _reset_use_case(users=users, resets=resets)

    with pytest.raises(WeakPasswordError):
        use_case.execute(ResetPasswordCommand(token=token, new_password="short"))

    use_case.execute(ResetPasswordCommand(token=token, new_password=NEW_PASSWORD))

    assert users.by_email[Email(EMAIL)].password_hash == PasswordHash(
        f"hashed:{NEW_PASSWORD}",
    )


def test_a_reset_moves_the_credential_version_forward() -> None:
    # This is what ends every session opened with the old password. Without
    # it, a stolen token outlives the reset that was meant to stop it.
    users, resets, token = _with_a_live_link()
    before = users.by_email[Email(EMAIL)].credential_version

    _reset_use_case(users=users, resets=resets).execute(
        ResetPasswordCommand(token=token, new_password=NEW_PASSWORD),
    )

    assert users.by_email[Email(EMAIL)].credential_version == before + 1


def test_a_reset_records_that_the_password_changed() -> None:
    users, resets, token = _with_a_live_link()
    published = RecordingEventPublisher()

    _reset_use_case(users=users, resets=resets, event_publisher=published).execute(
        ResetPasswordCommand(token=token, new_password=NEW_PASSWORD),
    )

    assert [type(event) for event in published.published] == [PasswordChanged]


def test_a_link_for_an_account_that_went_away_is_refused() -> None:
    _, resets, token = _with_a_live_link()

    with pytest.raises(InvalidPasswordResetTokenError):
        _reset_use_case(users=InMemoryUserRepository(), resets=resets).execute(
            ResetPasswordCommand(token=token, new_password=NEW_PASSWORD),
        )


def test_a_link_cannot_land_on_an_account_that_took_over_the_address() -> None:
    """The ticket names an account, not only an address.

    Otherwise a link issued for one account could set the password of whoever
    registered that address afterwards.
    """
    _, resets, token = _with_a_live_link()
    replacement = User.register(
        email=Email(EMAIL),
        password_hash=PasswordHash("hashed:theirs"),
        registered_at=PosixTime.now(),
    )

    with pytest.raises(InvalidPasswordResetTokenError):
        _reset_use_case(
            users=InMemoryUserRepository(replacement),
            resets=resets,
        ).execute(ResetPasswordCommand(token=token, new_password=NEW_PASSWORD))


# ----------------------------------------------------------------------
# Changing it while logged in
# ----------------------------------------------------------------------


def _change_use_case(
    users: InMemoryUserRepository,
    *,
    token_issuer: RecordingTokenIssuer | None = None,
    event_publisher: RecordingEventPublisher | None = None,
) -> ChangePasswordUseCase:
    return ChangePasswordUseCase(
        users=users,
        hasher=FakeHasher(),
        token_issuer=token_issuer or RecordingTokenIssuer(),
        event_publisher=event_publisher or RecordingEventPublisher(),
    )


def _caller(user: User) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user.id,
        email=user.email,
        credential_version=user.credential_version,
    )


def test_changing_a_password_needs_the_current_one() -> None:
    """A token alone must not be enough to take an account over.

    Somebody who walked up to an open session should not be able to lock the
    owner out of it.
    """
    account = _account()
    users = InMemoryUserRepository(account)

    with pytest.raises(InvalidCredentialsError):
        _change_use_case(users).execute(
            caller=_caller(account),
            command=ChangePasswordCommand(
                current_password="not it",
                new_password=NEW_PASSWORD,
            ),
        )

    assert users.by_email[Email(EMAIL)].password_hash == PasswordHash(
        f"hashed:{PASSWORD}",
    )


def test_a_change_returns_a_token_that_replaces_the_one_that_asked() -> None:
    # The change invalidates the caller's own session too, so handing back a
    # fresh token is what keeps it from looking like a logout.
    account = _account()
    issuer = RecordingTokenIssuer()

    _change_use_case(InMemoryUserRepository(account), token_issuer=issuer).execute(
        caller=_caller(account),
        command=ChangePasswordCommand(
            current_password=PASSWORD,
            new_password=NEW_PASSWORD,
        ),
    )

    [issued] = issuer.issued_for
    assert issued.credential_version == account.credential_version


def test_setting_the_same_password_again_is_refused() -> None:
    # Not a security rule but an honest one: it would end every session and
    # change nothing, which looks like a bug.
    account = _account()

    with pytest.raises(PasswordUnchangedError):
        _change_use_case(InMemoryUserRepository(account)).execute(
            caller=_caller(account),
            command=ChangePasswordCommand(
                current_password=PASSWORD,
                new_password=PASSWORD,
            ),
        )


def test_a_weak_new_password_is_refused() -> None:
    account = _account()

    with pytest.raises(WeakPasswordError):
        _change_use_case(InMemoryUserRepository(account)).execute(
            caller=_caller(account),
            command=ChangePasswordCommand(
                current_password=PASSWORD,
                new_password="short",
            ),
        )


def test_a_token_for_an_account_that_is_gone_cannot_change_a_password() -> None:
    account = _account()

    with pytest.raises(UserNotFoundError):
        _change_use_case(InMemoryUserRepository()).execute(
            caller=_caller(account),
            command=ChangePasswordCommand(
                current_password=PASSWORD,
                new_password=NEW_PASSWORD,
            ),
        )


def test_a_token_whose_id_does_not_match_the_address_is_refused() -> None:
    # The address was reassigned after the token was issued; that token must
    # not reach the account that holds it now.
    someone_else = _account()
    caller = AuthenticatedUser(
        user_id=UserId.new(),
        email=Email(EMAIL),
        credential_version=someone_else.credential_version,
    )

    with pytest.raises(UserNotFoundError):
        _change_use_case(InMemoryUserRepository(someone_else)).execute(
            caller=caller,
            command=ChangePasswordCommand(
                current_password=PASSWORD,
                new_password=NEW_PASSWORD,
            ),
        )
