from __future__ import annotations

from collections.abc import Mapping
import dataclasses
from datetime import UTC, date, datetime
from decimal import Decimal
import enum
import json
from typing import Self
import uuid


type JsonValue = (
    str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
)


def _serialize(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value

    if isinstance(value, Decimal):
        return str(value)

    if isinstance(value, enum.Enum):
        return _serialize(value.value)

    if isinstance(value, uuid.UUID):
        return str(value)

    if isinstance(value, datetime | date):
        return value.isoformat()

    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()  # type: ignore

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _serialize(getattr(value, field.name))
            for field in dataclasses.fields(value)
        }

    if isinstance(value, Mapping):
        return {str(key): _serialize(item) for key, item in value.items()}  # type: ignore

    if isinstance(value, list | tuple | set | frozenset):
        return [_serialize(item) for item in value]  # type: ignore

    return str(value)


class ValueObject:
    """Base class for immutable domain value objects.

    Subclasses must be dataclasses. By default `to_dict`/`to_json` walk their
    fields and convert any nested value or object into JSON-safe primitives;
    a subclass wrapping a single primitive concept (e.g. `PosixTime`) may
    override `to_dict` to return that primitive directly instead of a
    single-key dict.
    """

    __slots__ = ()

    def to_dict(self) -> JsonValue:
        return {
            field.name: _serialize(getattr(self, field.name))
            for field in dataclasses.fields(self)  # type: ignore
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())


@dataclasses.dataclass(frozen=True, slots=True)
class PosixTime(ValueObject):
    value: datetime

    def __post_init__(self) -> None:
        if self.value.tzinfo is None:
            raise ValueError("PosixTime requires a timezone-aware datetime")

        object.__setattr__(self, "value", self.value.astimezone(UTC))

    @classmethod
    def now(cls) -> Self:
        return cls(value=datetime.now(UTC))

    @classmethod
    def from_datetime(cls, value: datetime) -> Self:
        return cls(value=value)

    @classmethod
    def from_epoch_seconds(cls, seconds: int) -> Self:
        return cls(value=datetime.fromtimestamp(seconds, tz=UTC))

    @classmethod
    def from_epoch_milliseconds(cls, milliseconds: int) -> Self:
        return cls(value=datetime.fromtimestamp(milliseconds / 1000, tz=UTC))

    @classmethod
    def from_iso_string(cls, value: str) -> Self:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError(
                f"Invalid ISO 8601 datetime string: {value!r}",
            ) from error

        return cls(value=parsed)

    def to_datetime(self) -> datetime:
        return self.value

    def as_epoch_seconds(self) -> int:
        return int(self.value.timestamp())

    def as_epoch_milliseconds(self) -> int:
        return round(self.value.timestamp() * 1000)

    def to_dict(self) -> JsonValue:
        return self.as_epoch_seconds()
