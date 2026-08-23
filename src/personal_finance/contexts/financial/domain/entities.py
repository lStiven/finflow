from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Self

from personal_finance.contexts.financial.domain.events import (
    AccountBalanceChanged,
    AccountBalanceRebuilt,
    AccountClosed,
    AccountConfirmed,
    AccountFingerprintLinked,
    AccountOpened,
    AccountRenamed,
)
from personal_finance.contexts.financial.domain.exceptions import (
    AccountClosedError,
    CurrencyMismatchError,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountCategory,
    AccountFingerprint,
    AccountId,
    AccountKind,
    AccountStatus,
    Balance,
    LedgerMovement,
    MovementDirection,
    normalize_last_four,
)
from personal_finance.shared.domain.entities import AggregateRoot
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


MAX_ACCOUNT_NAME_LENGTH = 120


@dataclass(slots=True)
class Account(AggregateRoot[AccountId]):
    """One place a user's money sits, and what it holds right now.

    The balance is a running total, not the authority. The authority is the
    ledger of movements that were accepted as new; this aggregate never
    decides whether it has seen a movement before — that answer cannot be
    reached from memory when the queue is at-least-once and processes
    restart. The application layer writes the ledger row and this balance in
    one atomic write, so a balance can never move without a row behind it,
    and `rebuild` recomputes the total from those rows whenever the two are
    suspected of having drifted apart.

    An account is found by its fingerprints — the (bank, instrument, last
    four) pairs its alerts arrive under. It can hold several: one real
    checking account emails as a debit card for purchases and as an account
    number for transfers.
    """

    user_id: UserId
    name: str
    kind: AccountKind
    currency: Currency
    status: AccountStatus
    opening_balance: Balance
    balance: Balance
    opened_at: PosixTime
    bank: str | None = None
    fingerprints: set[AccountFingerprint] = field(
        default_factory=lambda: set[AccountFingerprint](),
    )
    movements_applied: int = 0
    closed_at: PosixTime | None = None

    def __post_init__(self) -> None:
        self.name = _valid_name(self.name)

    @classmethod
    def open_automatically(
        cls,
        *,
        user_id: UserId,
        bank: str,
        instrument_kind: str,
        last_four: str,
        kind: AccountKind,
        currency: Currency,
        opened_at: PosixTime,
    ) -> Self:
        """Create the account a first sighting implies, named generically.

        The opening balance is zero because it is unknown: this account was
        discovered by an alert, not declared by its owner, and nothing tells
        us what it held before. The running total is therefore movement since
        discovery until the user says otherwise — which is why it may go
        negative on an asset.
        """
        institution = bank.strip().lower()
        fingerprint = AccountFingerprint.from_parts(
            bank=institution,
            instrument_kind=instrument_kind,
            last_four=last_four,
        )
        account = cls(
            id=AccountId.new(),
            user_id=user_id,
            name=_suggest_name(institution, normalize_last_four(last_four)),
            kind=kind,
            currency=currency,
            status=AccountStatus.AUTOMATIC,
            opening_balance=Balance.zero(currency),
            balance=Balance.zero(currency),
            opened_at=opened_at,
            bank=institution or None,
        )
        account._announce_opening()
        account.link_fingerprint(fingerprint)

        return account

    @classmethod
    def open_manually(
        cls,
        *,
        user_id: UserId,
        name: str,
        kind: AccountKind,
        currency: Currency,
        opened_at: PosixTime,
        opening_balance: Money | None = None,
        bank: str | None = None,
    ) -> Self:
        """Create an account the user declared, with the balance they stated.

        This is the path for what never emails a movement: a mortgage, a
        student loan, cash in a drawer. On a liability the opening balance is
        what is owed.
        """
        institution = (bank or "").strip().lower()
        opening = (
            Balance.zero(currency)
            if opening_balance is None
            else Balance(amount=opening_balance)
        )

        if opening.currency is not currency:
            raise CurrencyMismatchError(
                f"An opening balance in {opening.currency.value} cannot open a "
                f"{currency.value} account",
            )

        account = cls(
            id=AccountId.new(),
            user_id=user_id,
            name=name,
            kind=kind,
            currency=currency,
            status=AccountStatus.CONFIRMED,
            opening_balance=opening,
            balance=opening,
            opened_at=opened_at,
            bank=institution or None,
        )
        account._announce_opening()

        return account

    @property
    def category(self) -> AccountCategory:
        return self.kind.category

    @property
    def is_closed(self) -> bool:
        return self.closed_at is not None

    @property
    def needs_review(self) -> bool:
        return self.status is AccountStatus.AUTOMATIC

    def matches(self, fingerprint: AccountFingerprint) -> bool:
        return fingerprint in self.fingerprints

    def link_fingerprint(self, fingerprint: AccountFingerprint) -> None:
        """Teach the account to answer to another of its bank's names."""
        if fingerprint in self.fingerprints:
            return

        self.fingerprints.add(fingerprint)
        self.record_event(
            AccountFingerprintLinked(
                account_id=self.id,
                user_id=self.user_id,
                fingerprint=fingerprint,
            ),
        )

    def apply(self, movement: LedgerMovement) -> None:
        """Move the balance by one movement the ledger already accepted.

        Direction alone does not say which way the number goes: spending on a
        credit card *raises* what that account holds, because what it holds is
        debt. Direction crossed with category is the whole rule.
        """
        if self.is_closed:
            raise AccountClosedError(
                f"Account {self.id.value} is closed and takes no movements",
            )

        self.balance = self._moved(self.balance, movement)
        self.movements_applied += 1
        self.record_event(
            AccountBalanceChanged(
                account_id=self.id,
                user_id=self.user_id,
                movement_id=movement.movement_id,
                direction=movement.direction,
                amount=movement.amount,
                balance=self.balance,
            ),
        )

    # TODO: some banks state the resulting balance in the alert itself. Decide
    # whether that number reconciles the running total (and how to tell a
    # stale alert from a current one, given they arrive out of order) or is
    # only kept for reference. Unknown which local banks do this — left open
    # deliberately rather than guessed at.

    def rebuild(self, movements: Iterable[LedgerMovement]) -> None:
        """Recompute the balance from the ledger, oldest movement first.

        The repair path for a running total that drifted. It is also what
        keeps the incremental balance honest: if replaying the ledger does not
        reproduce it, the number was wrong and now it is not.
        """
        balance = self.opening_balance
        applied = 0

        # Replayed into locals, not onto the account: a movement in the wrong
        # currency must abort the repair, not leave half of one behind. A
        # replay is not new money moving either, so it records one event at
        # the end rather than one per movement — and a closed account can
        # still be repaired.
        for movement in movements:
            balance = self._moved(balance, movement)
            applied += 1

        self.balance = balance
        self.movements_applied = applied
        self.record_event(
            AccountBalanceRebuilt(
                account_id=self.id,
                user_id=self.user_id,
                balance=self.balance,
                movements_applied=self.movements_applied,
            ),
        )

    def rename(self, name: str) -> None:
        self.name = _valid_name(name)
        # Naming it is reviewing it: the user looked at this account and said
        # what it is.
        self.status = AccountStatus.CONFIRMED
        self.record_event(
            AccountRenamed(
                account_id=self.id,
                user_id=self.user_id,
                name=self.name,
            ),
        )

    def confirm(self) -> None:
        """Accept the account as it stands, generic name included."""
        self.status = AccountStatus.CONFIRMED
        self.record_event(
            AccountConfirmed(account_id=self.id, user_id=self.user_id),
        )

    def close(self, closed_at: PosixTime) -> None:
        """Stop taking movements, keeping the history and the balance.

        Not a delete: a closed account still explains past transactions, and
        a paid-off loan closing at zero is exactly what should be visible.
        """
        if self.is_closed:
            return

        self.closed_at = closed_at
        self.record_event(
            AccountClosed(account_id=self.id, user_id=self.user_id),
        )

    def _moved(self, balance: Balance, movement: LedgerMovement) -> Balance:
        """Where the balance lands after one movement. The rule, alone."""
        grows = (
            movement.direction is MovementDirection.INCOMING
            if self.category is AccountCategory.ASSET
            else movement.direction is MovementDirection.OUTGOING
        )

        return (
            balance.plus(movement.amount) if grows else balance.minus(movement.amount)
        )

    def _announce_opening(self) -> None:
        self.record_event(
            AccountOpened(
                account_id=self.id,
                user_id=self.user_id,
                name=self.name,
                kind=self.kind,
                category=self.category,
                currency=self.currency,
                opening_balance=self.opening_balance,
            ),
        )


def _suggest_name(bank: str, last_four: str) -> str:
    """A placeholder the user will recognise and can rename.

    Trimmed to fit the name limit rather than allowed to breach it: the bank
    is whatever an email said it was, and a long one must not stop an account
    from being created for a real transaction.
    """
    suffix = f" ••{last_four}"
    institution = (bank.title() if bank else "Account")[
        : MAX_ACCOUNT_NAME_LENGTH - len(suffix)
    ].strip()

    return f"{institution}{suffix}"


def _valid_name(name: str) -> str:
    stripped = name.strip()

    if not stripped:
        raise ValueError("An account needs a name")

    if len(stripped) > MAX_ACCOUNT_NAME_LENGTH:
        raise ValueError(
            f"Account name exceeds {MAX_ACCOUNT_NAME_LENGTH} characters",
        )

    return stripped
