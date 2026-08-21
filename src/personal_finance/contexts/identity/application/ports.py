from __future__ import annotations

from collections.abc import Sequence
import dataclasses
from typing import Protocol

from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.value_objects import Email, PasswordHash
from personal_finance.shared.domain.value_objects import PosixTime, UserId


class UserRepository(Protocol):
    """Persistence port for `User`.

    `add_if_new` must be an atomic conditional write keyed on the email, so
    two concurrent registrations for the same address can never both succeed.
    """

    def add_if_new(self, user: User) -> User | None:
        """Persist `user` and return None. If the email is already taken,
        write nothing and return the stored record instead.
        """
        ...

    def find_by_email(self, email: Email) -> User | None: ...


class PasswordHasher(Protocol):
    """Port for turning a plaintext password into something safe to store,
    and for checking a login attempt against it. The algorithm is an
    infrastructure concern; the domain only ever holds the result.
    """

    def hash(self, password: str) -> PasswordHash: ...

    def verify(self, password: str, hashed: PasswordHash) -> bool: ...


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AccessToken:
    value: str
    expires_at: PosixTime


class TokenIssuer(Protocol):
    """Port for issuing and verifying the bearer token a client uses to prove
    it already authenticated as a given user.
    """

    def issue(self, user_id: UserId) -> AccessToken: ...

    def verify(self, token: str) -> UserId:
        """Return the user id the token was issued for.

        Raises for a token that is missing, malformed, expired, or signed
        with a different secret — the caller treats all of those as
        "not authenticated", never distinguishing them for the client.
        """
        ...


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class InboxRegistration:
    """One inbound address to listen on, plus the senders trusted for it.

    Registering a user does not require any of this — an account can be
    created bare and inboxes attached later — so every field beyond the
    address itself is optional.
    """

    address: str
    allowed_domains: frozenset[str] = dataclasses.field(
        default_factory=lambda: frozenset[str](),
    )
    allowed_addresses: frozenset[str] = dataclasses.field(
        default_factory=lambda: frozenset[str](),
    )


class InboxRegistrar(Protocol):
    """Identity's own view of "attach an inbound address to a user".

    This is identity's port, not ingestion's: it depends on nothing from
    ingestion's domain or application layer. The adapter that implements it
    is what is allowed to know ingestion exists.
    """

    def register(
        self,
        *,
        user_id: UserId,
        inboxes: Sequence[InboxRegistration],
    ) -> None: ...


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RegisteredInbox:
    """One inbox as identity reports it back to its owner.

    Deliberately plain strings rather than ingestion's value objects: this
    crosses a context boundary outwards, so it carries data, not another
    context's domain types.
    """

    address: str
    allowed_domains: frozenset[str]
    allowed_addresses: frozenset[str]


class InboxReader(Protocol):
    """Reads back the inboxes a user owns.

    Separate from `InboxRegistrar` so a caller that only lists cannot also
    modify; one adapter happens to satisfy both.
    """

    def list_for_user(self, user_id: UserId) -> Sequence[RegisteredInbox]: ...


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ConnectedMailbox:
    """One mailbox as identity reports it back to its owner.

    Plain strings again: this crosses a context boundary outwards. It carries
    no token and no cursor — the first is a secret, the second is ingestion's
    own bookkeeping and means nothing to the person reading it.
    """

    address: str
    provider: str
    status: str


class MailboxConnector(Protocol):
    """Identity's view of "let this user attach a mailbox we may read".

    The real provider flow ends here: once an OAuth callback has a token, it
    calls this with the address the user authorized.
    """

    def connect(self, *, user_id: UserId, address: str, provider: str) -> None: ...

    def disconnect(self, *, user_id: UserId, address: str, provider: str) -> None: ...

    def list_for_user(self, user_id: UserId) -> Sequence[ConnectedMailbox]: ...
