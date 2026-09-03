"""Storage for accounts, the fingerprints they answer to, and the ledger.

One table, partitioned by user, with the sort key carrying the record type:

    ACCOUNT#<id>        the account, its balance a signed running total
    FINGERPRINT#<print> a bank/instrument pair -> the account it reaches
    MOVEMENT#<id>       one ledger row, assigned or not

Two things here are load-bearing and neither is incidental.

**The ledger row is written conditionally, and it is the dedup.** A movement's
id is derived from its content, so a redelivered alert writes the same key;
`attribute_not_exists` is what turns the second write into a no-op instead of
a second expense. Nothing remembers what it has already seen — that answer
cannot be reached from memory when the queue is at-least-once and processes
restart.

**The balance moves by `ADD`, not by writing a number back.** A read-then-write
would lose one of two movements that land in the same instant, and a balance
that quietly drops a row is exactly what the ledger exists to prevent. `ADD`
is a running total applied by the database, which is what a running total is.
The row and the balance change go out as one `TransactWriteItems`, so a
balance can never move without a row behind it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
import datetime as dt
from decimal import Decimal
import enum
from typing import TYPE_CHECKING

from personal_finance.contexts.financial.application.ports import (
    BalanceReversal,
)
from personal_finance.contexts.financial.domain.entities import Account, Transaction
from personal_finance.contexts.financial.domain.financing import (
    AmortizationStyle,
    ChargeBasis,
    InterestRate,
    InvestmentTerms,
    LoanTerms,
    RateBasis,
    RecurringCharge,
)
from personal_finance.contexts.financial.domain.value_objects import (
    AccountFingerprint,
    AccountId,
    AccountKind,
    Balance,
    MovementDirection,
    MovementId,
    StatedMovement,
    TransactionOrigin,
    TransferId,
    TransferLeg,
    TransferRole,
)
from personal_finance.shared.domain.value_objects import (
    Currency,
    Money,
    PosixTime,
    UserId,
)


if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient
    from mypy_boto3_dynamodb.type_defs import (
        AttributeValueTypeDef,
        QueryInputTypeDef,
        TransactWriteItemTypeDef,
    )


PARTITION_KEY = "user_id"
SORT_KEY = "entity_id"

ACCOUNT_PREFIX = "ACCOUNT#"
FINGERPRINT_PREFIX = "FINGERPRINT#"
MOVEMENT_PREFIX = "MOVEMENT#"

ACCOUNT_ID_ATTRIBUTE = "account_id"

# Where the ledger row sits in `record`'s transaction. Its condition is the
# only one that means "this movement is already recorded".
_LEDGER_ROW = 0
BALANCE_ATTRIBUTE = "balance_amount"
MOVEMENTS_APPLIED_ATTRIBUTE = "movements_applied"
OPENING_BALANCE_ATTRIBUTE = "opening_balance"


CONDITIONAL_CHECK_FAILED = "ConditionalCheckFailed"


class CorruptFinancialItemError(Exception):
    """Raised when a stored record does not match the expected shape."""


def refused_by_condition(
    error: Exception,
    *,
    index: int | None = None,
) -> bool:
    """Whether a cancelled transaction was cancelled by *our* condition.

    DynamoDB cancels a transaction for several reasons that look identical
    from the outside: a throttle, a conflict with another writer, a capacity
    limit. Treating all of them as "this row already exists" is how a real
    movement disappears — the write never happened, the caller reports a
    duplicate, and the message is deleted. Only the condition failing means
    what the caller thinks it means; everything else has to be raised so the
    message is redelivered.
    """
    reasons = getattr(error, "response", {}).get("CancellationReasons", [])

    if index is not None:
        return (
            len(reasons) > index
            and reasons[index].get("Code") == CONDITIONAL_CHECK_FAILED
        )

    return any(reason.get("Code") == CONDITIONAL_CHECK_FAILED for reason in reasons)


def _string(item: Mapping[str, AttributeValueTypeDef], key: str) -> str | None:
    return item.get(key, {}).get("S")


def _number(item: Mapping[str, AttributeValueTypeDef], key: str) -> Decimal:
    raw = item.get(key, {}).get("N")

    return Decimal(raw) if raw is not None else Decimal(0)


def _enum[T: enum.Enum](kind: type[T], value: str, what: str) -> T:
    """Read a stored string back into its enum, or say the record is corrupt.

    A bare `ValueError` from here surfaces inside a repository call, where no
    caller expects one — it escapes the worker's guards and stops the loop
    over a single unreadable row.
    """
    try:
        return kind(value)
    except ValueError as error:
        raise CorruptFinancialItemError(
            f"Stored {what} is not one this version knows: {value!r}",
        ) from error


# Written only when the account carries them, so `save` has to clear whatever
# is now absent rather than leave a stale value behind.
OPTIONAL_ACCOUNT_ATTRIBUTES = frozenset(
    {"closed_at", "credit_limit", "loan", "investment", "accrued_through"},
)


def account_to_item(account: Account) -> dict[str, AttributeValueTypeDef]:
    return {
        PARTITION_KEY: {"S": str(account.user_id.value)},
        SORT_KEY: {"S": f"{ACCOUNT_PREFIX}{account.id.value}"},
        ACCOUNT_ID_ATTRIBUTE: {"S": str(account.id.value)},
        "name": {"S": account.name},
        "kind": {"S": account.kind.value},
        "currency": {"S": account.currency.value},
        OPENING_BALANCE_ATTRIBUTE: {
            "N": str(account.opening_balance.signed_amount),
        },
        BALANCE_ATTRIBUTE: {"N": str(account.balance.signed_amount)},
        MOVEMENTS_APPLIED_ATTRIBUTE: {"N": str(account.movements_applied)},
        "opened_at": {"N": str(account.opened_at.as_epoch_seconds())},
        "bank": {"S": account.bank or ""},
        "fingerprints": {
            "SS": sorted(print_.value for print_ in account.fingerprints),
        }
        if account.fingerprints
        else {"NULL": True},
        **(
            {"closed_at": {"N": str(account.closed_at.as_epoch_seconds())}}
            if account.closed_at is not None
            else {}
        ),
        # Absent rather than zero when unstated: zero is a real limit, and an
        # account already stored before this field existed has none.
        **(
            {"credit_limit": {"N": str(account.credit_limit.amount)}}
            if account.credit_limit is not None
            else {}
        ),
        # The terms the balance moves under, absent on everything that has
        # none — which is every account stored before this existed, and every
        # savings account after it.
        **(
            {"loan": {"M": _loan_to_item(account.loan)}}
            if account.loan is not None
            else {}
        ),
        **(
            {"investment": {"M": _investment_to_item(account.investment)}}
            if account.investment is not None
            else {}
        ),
        **(
            {"accrued_through": {"S": account.accrued_through.isoformat()}}
            if account.accrued_through is not None
            else {}
        ),
    }


# Money on the terms is stored as a string for the same reason a movement's
# amount is: a DynamoDB number round-trips through a float in some clients,
# and a cent lost inside an insurance rate is a cent every future period
# repeats. The currency is never stored beside it — it is the account's, and a
# second copy is a second thing that can disagree.
def _loan_to_item(terms: LoanTerms) -> dict[str, AttributeValueTypeDef]:
    return {
        "rate": {"M": _rate_to_item(terms.rate)},
        "disbursed_on": {"S": terms.disbursed_on.isoformat()},
        "term_months": {"N": str(terms.term_months)},
        "statement_day": {"N": str(terms.statement_day)},
        "style": {"S": terms.style.value},
        "installment_covers_charges": {"BOOL": terms.installment_covers_charges},
        "charges": {"L": [{"M": _charge_to_item(c)} for c in terms.charges]},
        **(
            {"payment_day": {"N": str(terms.payment_day)}}
            if terms.payment_day is not None
            else {}
        ),
        **(
            {"principal": {"S": str(terms.principal.amount)}}
            if terms.principal is not None
            else {}
        ),
        **(
            {"installment": {"S": str(terms.installment.amount)}}
            if terms.installment is not None
            else {}
        ),
    }


def _investment_to_item(terms: InvestmentTerms) -> dict[str, AttributeValueTypeDef]:
    return {
        "opened_on": {"S": terms.opened_on.isoformat()},
        "statement_day": {"N": str(terms.statement_day)},
        "charges": {"L": [{"M": _charge_to_item(c)} for c in terms.charges]},
        **(
            {"rate": {"M": _rate_to_item(terms.rate)}} if terms.rate is not None else {}
        ),
        **(
            {"matures_on": {"S": terms.matures_on.isoformat()}}
            if terms.matures_on is not None
            else {}
        ),
    }


def _rate_to_item(rate: InterestRate) -> dict[str, AttributeValueTypeDef]:
    return {"value": {"S": str(rate.value)}, "basis": {"S": rate.basis.value}}


def _charge_to_item(charge: RecurringCharge) -> dict[str, AttributeValueTypeDef]:
    return {
        "name": {"S": charge.name},
        "basis": {"S": charge.basis.value},
        "charged_to_balance": {"BOOL": charge.charged_to_balance},
        **({"amount": {"S": str(charge.amount.amount)}} if charge.amount else {}),
        **({"rate": {"S": str(charge.rate)}} if charge.rate is not None else {}),
        **({"base": {"S": str(charge.base.amount)}} if charge.base else {}),
    }


def account_to_entity(item: Mapping[str, AttributeValueTypeDef]) -> Account:
    user_id = _string(item, PARTITION_KEY)
    account_id = _string(item, ACCOUNT_ID_ATTRIBUTE)
    name = _string(item, "name")
    kind = _string(item, "kind")
    currency = _string(item, "currency")

    if (
        user_id is None
        or account_id is None
        or name is None
        or kind is None
        or currency is None
    ):
        raise CorruptFinancialItemError("Stored account is missing required fields")

    money = _enum(Currency, currency, "account currency")
    closed_at = item.get("closed_at", {}).get("N")
    credit_limit = item.get("credit_limit", {}).get("N")

    return Account(
        id=AccountId.from_string(account_id),
        user_id=UserId.from_string(user_id),
        name=name,
        kind=_enum(AccountKind, kind, "account kind"),
        currency=money,
        opening_balance=Balance.from_signed(
            _number(item, OPENING_BALANCE_ATTRIBUTE),
            money,
        ),
        balance=Balance.from_signed(_number(item, BALANCE_ATTRIBUTE), money),
        opened_at=PosixTime.from_epoch_seconds(int(_number(item, "opened_at"))),
        bank=_string(item, "bank") or None,
        fingerprints={
            AccountFingerprint(value=value)
            for value in item.get("fingerprints", {}).get("SS", [])
        },
        movements_applied=int(_number(item, MOVEMENTS_APPLIED_ATTRIBUTE)),
        closed_at=(
            PosixTime.from_epoch_seconds(int(closed_at))
            if closed_at is not None
            else None
        ),
        credit_limit=(
            Money(amount=Decimal(credit_limit), currency=money)
            if credit_limit is not None
            else None
        ),
        loan=_loan_to_entity(item.get("loan", {}).get("M"), money),
        investment=_investment_to_entity(item.get("investment", {}).get("M"), money),
        accrued_through=_date(_string(item, "accrued_through")),
    )


def _date(value: str | None) -> dt.date | None:
    if value is None:
        return None

    try:
        return dt.date.fromisoformat(value)
    except ValueError as error:
        raise CorruptFinancialItemError(
            f"Stored date is unreadable: {value!r}"
        ) from error


def _loan_to_entity(
    item: Mapping[str, AttributeValueTypeDef] | None,
    currency: Currency,
) -> LoanTerms | None:
    """Read a loan's terms back, or refuse the account.

    Refused rather than dropped, unlike an optional field that reads back as
    absent. Terms that came back half-read would leave a mortgage quietly
    accruing at no rate, and the owner would find out by noticing months later
    that their debt had stopped growing.
    """
    if item is None:
        return None

    disbursed_on = _date(_string(item, "disbursed_on"))
    rate = _rate_to_entity(item.get("rate", {}).get("M"))

    if disbursed_on is None or rate is None:
        raise CorruptFinancialItemError("Stored loan terms are missing required fields")

    payment_day = item.get("payment_day", {}).get("N")

    return LoanTerms(
        rate=rate,
        disbursed_on=disbursed_on,
        term_months=int(_number(item, "term_months")),
        statement_day=int(_number(item, "statement_day")),
        payment_day=int(payment_day) if payment_day is not None else None,
        style=_enum(
            AmortizationStyle,
            _string(item, "style") or AmortizationStyle.FRENCH.value,
            "amortization style",
        ),
        principal=_amount(_string(item, "principal"), currency),
        installment=_amount(_string(item, "installment"), currency),
        installment_covers_charges=bool(
            item.get("installment_covers_charges", {}).get("BOOL", False),
        ),
        charges=_charges_to_entity(item, currency),
    )


def _investment_to_entity(
    item: Mapping[str, AttributeValueTypeDef] | None,
    currency: Currency,
) -> InvestmentTerms | None:
    if item is None:
        return None

    opened_on = _date(_string(item, "opened_on"))

    if opened_on is None:
        raise CorruptFinancialItemError(
            "Stored investment terms are missing required fields",
        )

    return InvestmentTerms(
        opened_on=opened_on,
        statement_day=int(_number(item, "statement_day")),
        rate=_rate_to_entity(item.get("rate", {}).get("M")),
        matures_on=_date(_string(item, "matures_on")),
        charges=_charges_to_entity(item, currency),
    )


def _rate_to_entity(
    item: Mapping[str, AttributeValueTypeDef] | None,
) -> InterestRate | None:
    if item is None:
        return None

    value = _string(item, "value")
    basis = _string(item, "basis")

    if value is None or basis is None:
        raise CorruptFinancialItemError("Stored interest rate is incomplete")

    return InterestRate(
        value=Decimal(value), basis=_enum(RateBasis, basis, "rate basis")
    )


def _charges_to_entity(
    item: Mapping[str, AttributeValueTypeDef],
    currency: Currency,
) -> tuple[RecurringCharge, ...]:
    charges: list[RecurringCharge] = []

    for entry in item.get("charges", {}).get("L", []):
        stored = entry.get("M")

        if stored is None:
            raise CorruptFinancialItemError("Stored recurring charge is unreadable")

        name = _string(stored, "name")
        basis = _string(stored, "basis")

        if name is None or basis is None:
            raise CorruptFinancialItemError("Stored recurring charge is incomplete")

        rate = _string(stored, "rate")
        charges.append(
            RecurringCharge(
                name=name,
                basis=_enum(ChargeBasis, basis, "charge basis"),
                amount=_amount(_string(stored, "amount"), currency),
                rate=Decimal(rate) if rate is not None else None,
                base=_amount(_string(stored, "base"), currency),
                charged_to_balance=bool(
                    stored.get("charged_to_balance", {}).get("BOOL", True),
                ),
            ),
        )

    return tuple(charges)


def _amount(value: str | None, currency: Currency) -> Money | None:
    return None if value is None else Money(amount=Decimal(value), currency=currency)


def movement_to_item(transaction: Transaction) -> dict[str, AttributeValueTypeDef]:
    account_id = transaction.account_id
    account_fingerprint = transaction.account_fingerprint

    return {
        PARTITION_KEY: {"S": str(transaction.user_id.value)},
        SORT_KEY: {"S": f"{MOVEMENT_PREFIX}{transaction.id.value}"},
        "movement_id": {"S": transaction.id.value},
        "direction": {"S": transaction.direction.value},
        # A string, like everywhere else money crosses a boundary: DynamoDB
        # numbers round-trip through a float in some clients, and a cent lost
        # in storage is a cent no rebuild can recover.
        "amount": {"S": str(transaction.amount.amount)},
        "currency": {"S": transaction.amount.currency.value},
        "occurred_at": {"N": str(transaction.occurred_at.as_epoch_seconds())},
        "counterparty": {"S": transaction.counterparty},
        "bank": {"S": transaction.bank},
        "status": {"S": transaction.status.value},
        "origin": {"S": transaction.origin.value},
        **(
            {ACCOUNT_ID_ATTRIBUTE: {"S": str(account_id.value)}}
            if account_id is not None
            else {}
        ),
        **(
            {"account_fingerprint": {"S": account_fingerprint.value}}
            if account_fingerprint is not None
            else {}
        ),
        **({"note": {"S": transaction.note}} if transaction.note else {}),
        # Both sides of a transfer carry the same `transfer_id`; each carries
        # the other's movement id. Absent on everything else, which is what an
        # older row read back as: no attribute, no transfer, ordinary
        # spending.
        #
        # The three `counterpart_*` attributes are written together or not at
        # all — omitted when the other side is outside this app. Writing an
        # empty string instead would read back as a leg pointing at a movement
        # whose id is "", which is the one shape `TransferLeg` refuses.
        **(
            {"transfer": {"M": _transfer_to_item(leg)}}
            if (leg := transaction.transfer) is not None
            else {}
        ),
        **(
            {
                "stated": {
                    "M": {
                        "amount": {"S": str(stated.amount.amount)},
                        "currency": {"S": stated.amount.currency.value},
                        "occurred_at": {
                            "N": str(stated.occurred_at.as_epoch_seconds())
                        },
                        "counterparty": {"S": stated.counterparty},
                    },
                },
            }
            if (stated := transaction.stated) is not None
            else {}
        ),
    }


def _transfer_to_item(leg: TransferLeg) -> dict[str, AttributeValueTypeDef]:
    """The stored shape of one transfer leg."""
    stored: dict[str, AttributeValueTypeDef] = {
        "transfer_id": {"S": leg.transfer_id.value},
        "role": {"S": leg.role.value},
    }

    if (
        leg.counterpart_id is None
        or leg.counterpart_instrument_kind is None
        or leg.counterpart_last_four is None
    ):
        return stored

    stored["counterpart_id"] = {"S": leg.counterpart_id.value}
    stored["counterpart_instrument_kind"] = {"S": leg.counterpart_instrument_kind}
    stored["counterpart_last_four"] = {"S": leg.counterpart_last_four}

    return stored


def movement_to_entity(item: Mapping[str, AttributeValueTypeDef]) -> Transaction:
    user_id = _string(item, PARTITION_KEY)
    movement_id = _string(item, "movement_id")
    amount = _string(item, "amount")
    currency = _string(item, "currency")
    counterparty = _string(item, "counterparty")
    bank = _string(item, "bank")
    direction = _string(item, "direction")

    if (
        user_id is None
        or movement_id is None
        or amount is None
        or currency is None
        or counterparty is None
        or bank is None
        or direction is None
    ):
        raise CorruptFinancialItemError("Stored movement is missing required fields")

    account_id = _string(item, ACCOUNT_ID_ATTRIBUTE)
    fingerprint = _string(item, "account_fingerprint")
    occurred_at = item.get("occurred_at", {}).get("N")

    if occurred_at is None:
        # Defaulted to zero, this would read back as a 1970 movement that
        # `Account.rebuild` replays ahead of everything real.
        raise CorruptFinancialItemError("Stored movement has no time")

    return Transaction(
        id=MovementId(value=movement_id),
        user_id=UserId.from_string(user_id),
        direction=_enum(MovementDirection, direction, "movement direction"),
        amount=Money(
            amount=Decimal(amount),
            currency=_enum(Currency, currency, "movement currency"),
        ),
        occurred_at=PosixTime.from_epoch_seconds(int(occurred_at)),
        counterparty=counterparty,
        bank=bank,
        account_fingerprint=(
            AccountFingerprint(value=fingerprint) if fingerprint is not None else None
        ),
        origin=_enum(
            TransactionOrigin,
            _string(item, "origin") or TransactionOrigin.BANK_ALERT.value,
            "movement origin",
        ),
        account_id=(
            AccountId.from_string(account_id) if account_id is not None else None
        ),
        stated=_stated_to_entity(item.get("stated", {}).get("M")),
        note=_string(item, "note"),
        transfer=_transfer_to_entity(item.get("transfer", {}).get("M")),
    )


def _transfer_to_entity(
    item: Mapping[str, AttributeValueTypeDef] | None,
) -> TransferLeg | None:
    """Read the transfer half of a movement, or None for an ordinary one.

    Refuses a half-written one rather than dropping it: a leg that read back
    without its transfer marker would be counted as spending, and a balance
    would still be right while every total around it was wrong.

    The other side is optional but indivisible. All three `counterpart_*`
    attributes absent is a leg whose counterpart is outside this app —
    a card paid from another bank, a wallet or cash. *Some* of them absent is
    a row nothing wrote, and it is refused for the same reason the missing
    marker is: a movement pointing at a counterpart that cannot be resolved is
    a link every reader follows into nothing.
    """
    if item is None:
        return None

    transfer_id = _string(item, "transfer_id")
    role = _string(item, "role")

    if transfer_id is None or role is None:
        raise CorruptFinancialItemError("Stored transfer leg is missing fields")

    counterpart_id = _string(item, "counterpart_id")
    instrument_kind = _string(item, "counterpart_instrument_kind")
    last_four = _string(item, "counterpart_last_four")
    described = (counterpart_id, instrument_kind, last_four)

    if all(part is None for part in described):
        return TransferLeg(
            transfer_id=TransferId(value=transfer_id),
            role=_enum(TransferRole, role, "transfer role"),
        )

    if any(part is None for part in described):
        raise CorruptFinancialItemError(
            "Stored transfer leg describes half a counterpart",
        )

    # Narrowing for the type checker; the branch above already proved it.
    assert counterpart_id is not None
    assert instrument_kind is not None
    assert last_four is not None

    return TransferLeg(
        transfer_id=TransferId(value=transfer_id),
        role=_enum(TransferRole, role, "transfer role"),
        counterpart_id=MovementId(value=counterpart_id),
        counterpart_instrument_kind=instrument_kind,
        counterpart_last_four=last_four,
    )


def _stated_to_entity(
    item: Mapping[str, AttributeValueTypeDef] | None,
) -> StatedMovement | None:
    if item is None:
        return None

    amount = _string(item, "amount")
    currency = _string(item, "currency")
    counterparty = _string(item, "counterparty")
    occurred_at = item.get("occurred_at", {}).get("N")

    if (
        amount is None
        or currency is None
        or counterparty is None
        or occurred_at is None
    ):
        raise CorruptFinancialItemError("Stored original movement is incomplete")

    return StatedMovement(
        amount=Money(
            amount=Decimal(amount),
            currency=_enum(Currency, currency, "original movement currency"),
        ),
        occurred_at=PosixTime.from_epoch_seconds(int(occurred_at)),
        counterparty=counterparty,
    )


class DynamoDBAccountRepository:
    """`AccountRepository` over the single per-user partition described above."""

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def find(self, *, user_id: UserId, account_id: AccountId) -> Account | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key=_key(user_id, f"{ACCOUNT_PREFIX}{account_id.value}"),
        )
        item = response.get("Item")

        return account_to_entity(item) if item else None

    def find_by_fingerprint(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Account | None:
        pointer = self._client.get_item(
            TableName=self._table_name,
            Key=_key(user_id, f"{FINGERPRINT_PREFIX}{fingerprint.value}"),
        ).get("Item")

        if not pointer:
            return None

        account_id = _string(pointer, ACCOUNT_ID_ATTRIBUTE)

        if account_id is None:
            raise CorruptFinancialItemError("Fingerprint points at nothing")

        return self.find(user_id=user_id, account_id=AccountId.from_string(account_id))

    def list_by_user(self, user_id: UserId) -> Sequence[Account]:
        return [
            account_to_entity(item)
            for item in _query_prefix(
                self._client,
                table_name=self._table_name,
                user_id=user_id,
                prefix=ACCOUNT_PREFIX,
            )
        ]

    def save(self, account: Account) -> None:
        """Update what the owner changed, and leave the balance alone.

        Deliberately *not* a whole-item put. The balance is moved by the
        ledger's atomic `ADD`, and a read-modify-write here would silently
        discard any movement that landed between the read and this write —
        with a ledger row still on record and nothing to trigger a repair.
        A rename must not be able to lose an expense.

        `overwrite_balance` is the one path allowed to write that number, and
        it recomputes it from the rows first.
        """
        item = account_to_item(account)
        assignments = {
            name: value
            for name, value in item.items()
            if name
            not in (
                PARTITION_KEY,
                SORT_KEY,
                BALANCE_ATTRIBUTE,
                MOVEMENTS_APPLIED_ATTRIBUTE,
            )
        }
        names = {f"#{index}": name for index, name in enumerate(assignments)}
        values = {
            f":{index}": value for index, value in enumerate(assignments.values())
        }

        # An optional attribute the entity no longer carries has to be removed,
        # not merely left out of the SET: an update that only assigns leaves
        # the previous value in place, so clearing a credit limit would report
        # success and change nothing. Only ever names attributes this method
        # already owns, so a concurrent balance write is untouched.
        removals = {
            f"#r{index}": name
            for index, name in enumerate(OPTIONAL_ACCOUNT_ATTRIBUTES - set(assignments))
        }
        clauses = [
            "SET "
            + ", ".join(f"{name} = :{index}" for index, name in enumerate(names)),
        ]

        if removals:
            clauses.append("REMOVE " + ", ".join(removals))

        self._client.update_item(
            TableName=self._table_name,
            Key=_key(account.user_id, f"{ACCOUNT_PREFIX}{account.id.value}"),
            UpdateExpression=" ".join(clauses),
            ExpressionAttributeNames={**names, **removals},
            ExpressionAttributeValues=values,
        )
        self._put_fingerprints(account)

    def overwrite_balance(self, account: Account) -> None:
        """Write a balance that was recomputed from the ledger.

        The repair path, and the only writer of this number other than the
        ledger's atomic add. Racing a movement that lands mid-replay is still
        possible; unlike a lost update it is self-correcting, because the next
        replay reads the row that was missed.
        """
        self._client.update_item(
            TableName=self._table_name,
            Key=_key(account.user_id, f"{ACCOUNT_PREFIX}{account.id.value}"),
            UpdateExpression=(
                f"SET {BALANCE_ATTRIBUTE} = :balance, "
                f"{MOVEMENTS_APPLIED_ATTRIBUTE} = :applied"
            ),
            ExpressionAttributeValues={
                ":balance": {"N": str(account.balance.signed_amount)},
                ":applied": {"N": str(account.movements_applied)},
            },
        )

    def restate_balance(self, account: Account) -> None:
        """Write a balance its owner stated, and the opening balance behind it.

        One `UpdateExpression` rather than `save` followed by
        `overwrite_balance`: those two own different halves of the sum, and a
        crash between them would leave an opening balance that does not
        explain the balance beside it — inconsistent with nothing scheduled
        to repair it, since a replay only runs when a movement moves. Naming
        only these three attributes also leaves a concurrent movement's
        `ADD` on everything else untouched, the same discipline
        `overwrite_balance` keeps.
        """
        self._client.update_item(
            TableName=self._table_name,
            Key=_key(account.user_id, f"{ACCOUNT_PREFIX}{account.id.value}"),
            UpdateExpression=(
                f"SET {OPENING_BALANCE_ATTRIBUTE} = :opening, "
                f"{BALANCE_ATTRIBUTE} = :balance, "
                f"{MOVEMENTS_APPLIED_ATTRIBUTE} = :applied"
            ),
            ExpressionAttributeValues={
                ":opening": {"N": str(account.opening_balance.signed_amount)},
                ":balance": {"N": str(account.balance.signed_amount)},
                ":applied": {"N": str(account.movements_applied)},
            },
        )

    def _put_fingerprints(self, account: Account) -> None:
        for value in sorted(print_.value for print_ in account.fingerprints):
            self._client.put_item(
                TableName=self._table_name,
                Item={
                    **_key(account.user_id, f"{FINGERPRINT_PREFIX}{value}"),
                    ACCOUNT_ID_ATTRIBUTE: {"S": str(account.id.value)},
                },
            )

    def add(self, account: Account) -> bool:
        """Create the account and every fingerprint it answers to, or nothing.

        One transaction, every item conditional: two workers can be opening
        the same account from the same redelivered alert at the same moment,
        and the loser must find the winner's account rather than create a
        second one holding half the movements.
        """
        items: list[TransactWriteItemTypeDef] = [
            {
                "Put": {
                    "TableName": self._table_name,
                    "Item": account_to_item(account),
                    "ConditionExpression": f"attribute_not_exists({SORT_KEY})",
                },
            },
        ]
        items.extend(
            {
                "Put": {
                    "TableName": self._table_name,
                    "Item": {
                        **_key(account.user_id, f"{FINGERPRINT_PREFIX}{value}"),
                        ACCOUNT_ID_ATTRIBUTE: {"S": str(account.id.value)},
                    },
                    "ConditionExpression": f"attribute_not_exists({SORT_KEY})",
                },
            }
            for value in sorted(print_.value for print_ in account.fingerprints)
        )

        try:
            self._client.transact_write_items(TransactItems=items)
        except self._client.exceptions.TransactionCanceledException as error:
            if refused_by_condition(error):
                return False

            # A throttle or a conflict with another writer. Nobody won the
            # race; raising is what puts the message back on the queue rather
            # than reporting an account that does not exist.
            raise

        return True


class DynamoDBTransactionLedger:
    """`TransactionLedger` over the same partition.

    `record` is the only write, and it is the one that decides whether a
    movement is new. Everything else here reads.
    """

    def __init__(self, *, client: DynamoDBClient, table_name: str) -> None:
        self._client = client
        self._table_name = table_name

    def record(
        self,
        *,
        transaction: Transaction,
        balance_delta: Decimal | None,
    ) -> bool:
        account_id = transaction.account_id

        if (balance_delta is None) != (account_id is None):
            raise ValueError(
                "A balance change belongs to an assigned movement and only to "
                "an assigned movement",
            )

        row: TransactWriteItemTypeDef = {
            "Put": {
                "TableName": self._table_name,
                "Item": movement_to_item(transaction),
                # The dedup, and the only one. The id is derived from the
                # movement's content, so a redelivered alert writes this same
                # key and this condition is what stops the second expense.
                "ConditionExpression": f"attribute_not_exists({SORT_KEY})",
            },
        }

        if balance_delta is None or account_id is None:
            try:
                self._client.transact_write_items(TransactItems=[row])
            except self._client.exceptions.TransactionCanceledException as error:
                if refused_by_condition(error, index=_LEDGER_ROW):
                    return False

                raise

            return True

        try:
            self._client.transact_write_items(
                TransactItems=[
                    row,
                    {
                        "Update": {
                            "TableName": self._table_name,
                            "Key": _key(
                                transaction.user_id,
                                f"{ACCOUNT_PREFIX}{account_id.value}",
                            ),
                            # `ADD`, not a number written back: two movements
                            # landing in the same instant would otherwise
                            # overwrite one another and lose a real expense.
                            "UpdateExpression": (
                                f"ADD {BALANCE_ATTRIBUTE} :delta, "
                                f"{MOVEMENTS_APPLIED_ATTRIBUTE} :one"
                            ),
                            "ExpressionAttributeValues": {
                                ":delta": {"N": str(balance_delta)},
                                ":one": {"N": "1"},
                            },
                            # A balance may only move on an account that
                            # exists; without this, `ADD` would create one.
                            "ConditionExpression": f"attribute_exists({SORT_KEY})",
                        },
                    },
                ],
            )
        except self._client.exceptions.TransactionCanceledException as error:
            # Only the ledger row's own condition means "already recorded".
            # A cancelled balance update, a throttle or a conflict means the
            # movement was not written at all, and must be retried rather
            # than reported as a duplicate and deleted.
            if refused_by_condition(error, index=_LEDGER_ROW):
                return False

            raise

        return True

    def save(self, transaction: Transaction) -> None:
        """Overwrite a movement that already exists.

        Unconditional, unlike `record`: the caller holds the row and is
        changing it, not deciding whether it is new. The balance it may affect
        is recomputed from the ledger afterwards rather than nudged here — a
        correction is rare, and replaying the rows is the one path that cannot
        drift from them.
        """
        self._client.put_item(
            TableName=self._table_name,
            Item=movement_to_item(transaction),
        )

    def remove(
        self,
        transactions: Sequence[Transaction],
        *,
        reversals: Sequence[BalanceReversal],
    ) -> None:
        """Erase these rows and unwind their balances in one transaction.

        The mirror of `record`, down to the `ADD`: the balance is nudged back
        by the database rather than written from a number read moments ago,
        so an alert landing in the same instant is not lost. Deleting first
        and replaying the rows afterwards would be neither — the replay reads
        eventually, so it can still count the row that was just deleted and
        write the balance back unchanged, with the row gone and nothing left
        to trigger a repair.

        Every delete is conditional, exactly like `record`'s put, and for
        the mirror-image reason. A delete on a key that is already gone
        *succeeds* in DynamoDB, so without the condition a retried erasure
        would delete nothing and unwind the balance a second time — money
        appearing out of a row that was already taken back. There is no
        half-finished erasure for the condition to get in the way of: this is
        one `TransactWriteItems`, so either all of it happened or none of it
        did, and a second attempt finding the rows gone means the first one
        landed. That is reported as success, which is what the caller asked
        for. The balance updates keep their own condition for the reason
        `record` does — without it `ADD` would create the account it was
        meant to correct.
        """
        if not transactions:
            return

        owners = {transaction.user_id for transaction in transactions}

        if len(owners) > 1:
            # The balance half of this write is keyed by one partition, so a
            # batch spanning two people would move one owner's balance for
            # rows deleted from another's. Nothing here builds such a batch —
            # both sides of a transfer are one person's — and this is what
            # keeps it that way rather than trusting that it stays true.
            raise ValueError(
                "An erasure belongs to one owner: rows from two partitions "
                "cannot be removed in the same write",
            )

        owner = owners.pop()
        items: list[TransactWriteItemTypeDef] = [
            {
                "Delete": {
                    "TableName": self._table_name,
                    "Key": _key(
                        owner,
                        f"{MOVEMENT_PREFIX}{transaction.id.value}",
                    ),
                    "ConditionExpression": f"attribute_exists({SORT_KEY})",
                },
            }
            for transaction in transactions
        ]
        items.extend(
            {
                "Update": {
                    "TableName": self._table_name,
                    "Key": _key(
                        owner,
                        f"{ACCOUNT_PREFIX}{reversal.account_id.value}",
                    ),
                    "UpdateExpression": (
                        f"ADD {BALANCE_ATTRIBUTE} :delta, "
                        f"{MOVEMENTS_APPLIED_ATTRIBUTE} :removed"
                    ),
                    "ExpressionAttributeValues": {
                        ":delta": {"N": str(reversal.delta)},
                        ":removed": {"N": str(-reversal.movements)},
                    },
                    "ConditionExpression": f"attribute_exists({SORT_KEY})",
                },
            }
            for reversal in reversals
        )

        try:
            self._client.transact_write_items(TransactItems=items)
        except self._client.exceptions.TransactionCanceledException as error:
            # Only a *delete's* condition means "already gone", and the
            # distinction is the same one `record` makes with `_LEDGER_ROW`.
            # A row that is no longer there is the erasure this call asked
            # for, already done, with its balance already unwound. A balance
            # update refused — an account that is not there — is the write not
            # happening at all, and it has to surface or a caller would report
            # money returned that never moved.
            erasures = range(len(transactions))
            unwinds = range(len(transactions), len(items))

            if any(refused_by_condition(error, index=at) for at in unwinds):
                raise

            if not any(refused_by_condition(error, index=at) for at in erasures):
                raise

    def list_unassigned_matching(
        self,
        *,
        user_id: UserId,
        fingerprint: AccountFingerprint,
    ) -> Sequence[Transaction]:
        return [
            movement_to_entity(item)
            for item in _query_prefix(
                self._client,
                table_name=self._table_name,
                user_id=user_id,
                prefix=MOVEMENT_PREFIX,
                filter_expression=(
                    f"attribute_not_exists({ACCOUNT_ID_ATTRIBUTE}) "
                    "AND account_fingerprint = :fingerprint"
                ),
                filter_values={":fingerprint": {"S": fingerprint.value}},
            )
        ]

    def list_all(self, user_id: UserId) -> Sequence[Transaction]:
        return [
            movement_to_entity(item)
            for item in _query_prefix(
                self._client,
                table_name=self._table_name,
                user_id=user_id,
                prefix=MOVEMENT_PREFIX,
            )
        ]

    def find(self, *, user_id: UserId, transaction_id: str) -> Transaction | None:
        response = self._client.get_item(
            TableName=self._table_name,
            Key=_key(user_id, f"{MOVEMENT_PREFIX}{transaction_id}"),
        )
        item = response.get("Item")

        return movement_to_entity(item) if item else None

    def list_movements(
        self,
        *,
        user_id: UserId,
        account_id: AccountId,
    ) -> Sequence[Transaction]:
        movements = [
            movement_to_entity(item)
            for item in _query_prefix(
                self._client,
                table_name=self._table_name,
                user_id=user_id,
                prefix=MOVEMENT_PREFIX,
                # Filtered by DynamoDB rather than here: a user with one busy
                # account and years of history would otherwise transfer their
                # whole ledger to rebuild one balance.
                filter_expression=f"{ACCOUNT_ID_ATTRIBUTE} = :account_id",
                filter_values={":account_id": {"S": str(account_id.value)}},
            )
        ]
        # Oldest first: `Account.rebuild` replays in the order the money moved,
        # not the order the alerts happened to be stored in.
        movements.sort(key=lambda movement: movement.occurred_at.as_epoch_seconds())

        return movements

    def list_unassigned(self, user_id: UserId) -> Sequence[Transaction]:
        return [
            movement_to_entity(item)
            for item in _query_prefix(
                self._client,
                table_name=self._table_name,
                user_id=user_id,
                prefix=MOVEMENT_PREFIX,
                filter_expression=f"attribute_not_exists({ACCOUNT_ID_ATTRIBUTE})",
            )
        ]


def _key(user_id: UserId, sort_value: str) -> dict[str, AttributeValueTypeDef]:
    return {
        PARTITION_KEY: {"S": str(user_id.value)},
        SORT_KEY: {"S": sort_value},
    }


def _query_prefix(
    client: DynamoDBClient,
    *,
    table_name: str,
    user_id: UserId,
    prefix: str,
    filter_expression: str | None = None,
    filter_values: Mapping[str, AttributeValueTypeDef] | None = None,
) -> list[dict[str, AttributeValueTypeDef]]:
    """Every record of one type in a user's partition, following pagination.

    A query inside one partition, never a scan: it can only ever read the user
    it was asked for, which is what keeps one person's accounts unreachable
    from another's session.
    """
    request: QueryInputTypeDef = {
        "TableName": table_name,
        "KeyConditionExpression": (
            f"{PARTITION_KEY} = :user_id AND begins_with({SORT_KEY}, :prefix)"
        ),
        "ExpressionAttributeValues": {
            ":user_id": {"S": str(user_id.value)},
            ":prefix": {"S": prefix},
            **(filter_values or {}),
        },
    }

    if filter_expression is not None:
        request["FilterExpression"] = filter_expression

    items: list[dict[str, AttributeValueTypeDef]] = []

    while True:
        response = client.query(**request)
        items.extend(response.get("Items", []))
        start_key = response.get("LastEvaluatedKey")

        if not start_key:
            return items

        request["ExclusiveStartKey"] = start_key
