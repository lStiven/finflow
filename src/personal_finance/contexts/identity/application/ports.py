from __future__ import annotations

import dataclasses
from typing import Protocol

from personal_finance.contexts.identity.domain.credentials import (
    EmailVerification,
    PasswordResetTicket,
    PasswordResetWindow,
    VerificationState,
)
from personal_finance.contexts.identity.domain.entities import User
from personal_finance.contexts.identity.domain.value_objects import (
    Email,
    PasswordHash,
    PersonName,
    SecretHash,
)
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

    def rename(self, user: User) -> bool:
        """Write `user.name` onto the stored record, in place.

        Guarded by the account's id as well as its email, so it can only ever
        touch the record the caller actually loaded — never one that replaced
        it in between. Returns False when nothing matched, which the caller
        reads as "that account is gone".
        """
        ...

    def change_password(self, user: User) -> bool:
        """Write the new password hash and the moment it was set, in place.

        Guarded the same way `rename` is, and for a sharper reason: this write
        is what ends every session opened with the old password, so it must
        land on the account that was actually read and on no other.
        """
        ...


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


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class AuthenticatedUser:
    """Who a verified token says is calling.

    The email travels in the token as well as the id because identity's own
    records are keyed by email: carrying it means an authenticated request
    can reach its account without a second index to look the address up. It
    is only ever read back out of a signature this deployment produced, never
    off the wire. The name rides along for the client's benefit — a snapshot
    taken when the token was issued, so a rename does not show up in it until
    the next login; `GET /identity/me` is the authoritative answer.
    """

    user_id: UserId
    email: Email
    name: PersonName | None = None
    # Which generation of the account's credentials this token was cut from.
    # Zero is the value a token that predates the claim decodes to, and it
    # matches no live account, so an old token fails closed rather than open.
    credential_version: int = 0


class TokenIssuer(Protocol):
    """Port for issuing and verifying the bearer token a client uses to prove
    it already authenticated as a given user.
    """

    def issue(self, user: AuthenticatedUser) -> AccessToken: ...

    def verify(self, token: str) -> AuthenticatedUser:
        """Return who the token was issued for.

        Raises for a token that is missing, malformed, expired, or signed
        with a different secret — the caller treats all of those as
        "not authenticated", never distinguishing them for the client.
        """
        ...


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class InboxRegistration:
    """The senders trusted for a user's forwarding address.

    No address here: every user gets exactly one, derived from their own id
    by ingestion, never chosen by identity or by the caller. This is nothing
    but the sender policy — empty by default, which reads nothing until the
    user approves at least one sender.
    """

    allowed_domains: frozenset[str] = dataclasses.field(
        default_factory=lambda: frozenset[str](),
    )
    allowed_addresses: frozenset[str] = dataclasses.field(
        default_factory=lambda: frozenset[str](),
    )


class InboxRegistrar(Protocol):
    """Identity's own view of "set this user's approved senders".

    This is identity's port, not ingestion's: it depends on nothing from
    ingestion's domain or application layer. The adapter that implements it
    is what is allowed to know ingestion exists.
    """

    def register(
        self,
        *,
        user_id: UserId,
        inbox: InboxRegistration,
    ) -> RegisteredInbox: ...


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RegisteredInbox:
    """A user's forwarding address and the senders approved for it, as
    identity reports it back to its owner.

    Deliberately a plain string address rather than ingestion's value
    objects: this crosses a context boundary outwards, so it carries data,
    not another context's domain types.
    """

    address: str
    allowed_domains: frozenset[str]
    allowed_addresses: frozenset[str]


class InboxReader(Protocol):
    """Reads back the inbox a user owns.

    Separate from `InboxRegistrar` so a caller that only reads cannot also
    modify; one adapter happens to satisfy both.
    """

    def get_for_user(self, user_id: UserId) -> RegisteredInbox | None: ...


class SecretHasher(Protocol):
    """Turns a short-lived secret into something safe to store, and checks a
    presented one against it.

    Two implementations, and the difference is deliberate. A one-time code is
    low-entropy and is compared by reading the record first, so it wants a
    slow, salted hash. A reset token is 256 random bits and *is* the lookup
    key, so its hash has to be deterministic — which a salted one is not.
    """

    def hash(self, secret: str) -> SecretHash: ...

    def verify(self, secret: str, hashed: SecretHash) -> bool: ...


class SecretGenerator(Protocol):
    """Where the random half of every one-time secret comes from.

    A port rather than a call to `secrets` inside a use case, so a test can
    say which code was sent instead of reading it back out of a mailbox.
    """

    def verification_code(self) -> str:
        """Six digits, uniformly drawn."""
        ...

    def opaque_token(self) -> str:
        """A URL-safe secret with enough entropy to be unguessable on its
        own — it travels in a link and is the only thing guarding it.
        """
        ...


class EmailVerificationRepository(Protocol):
    """Persistence port for the pending proof that an address is reachable.

    `save` is optimistic: it lands only when the stored record still shows the
    counters that were read, so two people racing the same code cannot both
    spend an attempt or both be handed a ticket.
    """

    def find(self, email: Email) -> EmailVerification | None: ...

    def save(
        self,
        verification: EmailVerification,
        *,
        expected: VerificationState | None,
    ) -> bool:
        """Persist the challenge, returning False when the record moved since
        it was read. `expected` is None for a challenge that did not exist,
        which is then written only if it still does not.
        """
        ...

    def consume_ticket(
        self,
        *,
        email: Email,
        ticket_hash: SecretHash,
        now: PosixTime,
    ) -> bool:
        """Spend the registration ticket, once and atomically.

        The check and the removal have to be one write: two registrations
        racing the same ticket must not both find it valid, or one verified
        address becomes two accounts.
        """
        ...


class PasswordResetRepository(Protocol):
    """Persistence port for reset links and the mail they cause.

    Two records, found by different things: the window by the address that
    asked, the ticket by the secret in the link. Only the second is a
    credential; the first exists so an unauthenticated endpoint cannot be
    turned on somebody's inbox.
    """

    def find_window(self, email: Email) -> PasswordResetWindow | None: ...

    def save_window(
        self,
        window: PasswordResetWindow,
        *,
        expected_sends: int | None,
    ) -> bool:
        """Persist the send window, returning False when it moved since it
        was read.
        """
        ...

    def save_ticket(self, ticket: PasswordResetTicket) -> None: ...

    def delete_ticket(self, token_hash: SecretHash) -> None:
        """Retire a link that a newer request replaced."""
        ...

    def consume_ticket(
        self,
        *,
        token_hash: SecretHash,
        now: PosixTime,
    ) -> PasswordResetTicket | None:
        """Spend a reset link, once and atomically, returning what it was
        issued for. None covers unknown, expired and already-spent alike.
        """
        ...


class CredentialNotifier(Protocol):
    """The mail this context sends, and the only mail it sends.

    Every method takes an address that a stranger may have typed, so nothing
    here is a template with a hole in it: each one is a fixed message with a
    code or a link substituted, never caller-supplied text.

    The two "nothing to do" messages are not politeness. Answering an unknown
    address with silence and a known one with mail is itself an answer, so
    both branches send something and the endpoint stays uninformative.
    """

    def send_verification_code(
        self,
        *,
        email: Email,
        code: str,
        expires_in_minutes: int,
    ) -> None: ...

    def send_registration_notice_for_existing_account(
        self,
        *,
        email: Email,
    ) -> None: ...

    def send_password_reset(
        self,
        *,
        email: Email,
        link: str,
        expires_in_minutes: int,
    ) -> None: ...

    def send_password_reset_for_unknown_account(self, *, email: Email) -> None: ...
