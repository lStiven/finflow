import dataclasses
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
import enum
import json
import uuid

import pytest

from personal_finance.shared.domain.value_objects import PosixTime, ValueObject


def test_posix_time_rejects_naive_datetime() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        PosixTime(datetime(2026, 8, 20, 12, 0, 0))


def test_posix_time_normalizes_to_utc() -> None:
    bogota = timezone(timedelta(hours=-5))
    local = datetime(2026, 8, 20, 7, 0, 0, tzinfo=bogota)

    posix_time = PosixTime(local)

    assert posix_time.value == datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)
    assert posix_time.value.tzinfo is UTC


def test_posix_time_now_is_timezone_aware() -> None:
    posix_time = PosixTime.now()

    assert posix_time.value.tzinfo is UTC


def test_posix_time_from_datetime() -> None:
    value = datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)

    posix_time = PosixTime.from_datetime(value)

    assert posix_time.to_datetime() == value


def test_posix_time_epoch_seconds_round_trip() -> None:
    original = PosixTime.from_datetime(datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC))

    rebuilt = PosixTime.from_epoch_seconds(original.as_epoch_seconds())

    assert rebuilt == original


def test_posix_time_equality_is_by_value() -> None:
    first = PosixTime.from_datetime(datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC))
    second = PosixTime.from_datetime(datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC))

    assert first == second


def test_posix_time_to_dict_returns_epoch_seconds() -> None:
    posix_time = PosixTime.from_datetime(datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC))

    assert posix_time.to_dict() == posix_time.as_epoch_seconds() == 1787227200


def test_posix_time_to_json_round_trips_through_to_dict() -> None:
    posix_time = PosixTime.now()

    assert json.loads(posix_time.to_json()) == posix_time.to_dict()


def test_posix_time_epoch_milliseconds_round_trip() -> None:
    original = PosixTime.from_datetime(
        datetime(2026, 8, 20, 12, 0, 0, 500_000, tzinfo=UTC),
    )

    rebuilt = PosixTime.from_epoch_milliseconds(original.as_epoch_milliseconds())

    assert rebuilt == original


def test_posix_time_as_epoch_milliseconds_matches_epoch_seconds() -> None:
    posix_time = PosixTime.from_datetime(datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC))

    assert posix_time.as_epoch_milliseconds() == posix_time.as_epoch_seconds() * 1000


def test_posix_time_from_iso_string() -> None:
    posix_time = PosixTime.from_iso_string("2026-08-20T12:00:00+00:00")

    assert posix_time == PosixTime.from_datetime(
        datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC),
    )


def test_posix_time_from_iso_string_round_trips_through_isoformat() -> None:
    original = PosixTime.now()

    rebuilt = PosixTime.from_iso_string(original.to_datetime().isoformat())

    assert rebuilt == original


def test_posix_time_to_dict_round_trips_through_from_epoch_seconds() -> None:
    original = PosixTime.from_datetime(datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC))

    rebuilt = PosixTime.from_epoch_seconds(original.to_dict())  # type: ignore[arg-type]

    assert rebuilt == original


def test_posix_time_from_iso_string_rejects_naive_string() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        PosixTime.from_iso_string("2026-08-20T12:00:00")


def test_posix_time_from_iso_string_rejects_invalid_string() -> None:
    with pytest.raises(ValueError, match="Invalid ISO 8601"):
        PosixTime.from_iso_string("not-a-datetime")


class Currency(enum.Enum):
    USD = "USD"
    COP = "COP"


@dataclasses.dataclass(frozen=True, slots=True)
class Money(ValueObject):
    amount: Decimal
    currency: Currency


@dataclasses.dataclass(frozen=True, slots=True)
class PlainPoint:
    """A plain dataclass, not derived from ValueObject."""

    x: int
    y: int


class Unserializable:
    def __str__(self) -> str:
        return "unserializable-repr"


@dataclasses.dataclass(frozen=True, slots=True)
class SampleValueObject(ValueObject):
    identifier: uuid.UUID
    money: Money
    point: PlainPoint
    tags: list[str]
    metadata: dict[str, int]
    created_on: date
    note: str | None
    opaque: Unserializable
    created_at: PosixTime


def _build_sample() -> SampleValueObject:
    return SampleValueObject(
        identifier=uuid.UUID("12345678-1234-5678-1234-567812345678"),
        money=Money(amount=Decimal("10.50"), currency=Currency.USD),
        point=PlainPoint(x=1, y=2),
        tags=["a", "b"],
        metadata={"count": 3},
        created_on=date(2026, 8, 20),
        note=None,
        opaque=Unserializable(),
        created_at=PosixTime.from_datetime(datetime(2026, 8, 20, 12, 0, 0, tzinfo=UTC)),
    )


def test_to_dict_converts_every_nested_type_to_json_safe_primitives() -> None:
    sample = _build_sample()

    assert sample.to_dict() == {
        "identifier": "12345678-1234-5678-1234-567812345678",
        "money": {"amount": "10.50", "currency": "USD"},
        "point": {"x": 1, "y": 2},
        "tags": ["a", "b"],
        "metadata": {"count": 3},
        "created_on": "2026-08-20",
        "note": None,
        "opaque": "unserializable-repr",
        "created_at": 1787227200,
    }


def test_to_dict_keeps_decimal_precision_as_string() -> None:
    sample = _build_sample()

    assert sample.to_dict()["money"]["amount"] == "10.50"  # type: ignore[index]


def test_to_json_produces_valid_json_matching_to_dict() -> None:
    sample = _build_sample()

    assert json.loads(sample.to_json()) == sample.to_dict()
