"""Reading JSON that came from somewhere we do not control.

`json.loads` produces `Any`, and an external payload is exactly the place
where letting `Any` spread is worst: a provider can send anything, including
a list where an object was expected. These narrow once, at the boundary, so
everything downstream is typed.
"""

from __future__ import annotations

from typing import Any, cast


def as_json_object(value: object) -> dict[str, Any]:
    """The value as a JSON object, or an empty one if it is anything else."""
    if not isinstance(value, dict):
        return {}

    return cast("dict[str, Any]", value)


def as_json_array(value: object) -> list[Any]:
    """The value as a JSON array, or an empty one if it is anything else."""
    if not isinstance(value, list):
        return []

    return cast("list[Any]", value)


def read_string(payload: dict[str, Any], key: str) -> str | None:
    value: Any = payload.get(key)

    return value if isinstance(value, str) else None
