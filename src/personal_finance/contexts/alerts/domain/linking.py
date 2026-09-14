"""The short-lived proof that turns a tap in Telegram into a bound channel.

A direct mirror of `identity/domain/credentials.py::PasswordResetTicket`, and
for the same reason: it is found by the secret somebody presents rather than
by who they are, so it has to carry its own scope. Holding the channel *and*
the owner is what stops a link from ever landing on an account other than the
one that asked for it.

The plaintext token never reaches here. It is generated in infrastructure,
handed to the caller once inside the deep link, and only its hash is stored —
the same discipline a password gets.
"""

from __future__ import annotations

import dataclasses

from personal_finance.contexts.alerts.domain.value_objects import (
    ChannelId,
    SecretHash,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


@dataclasses.dataclass(frozen=True, slots=True)
class ChannelLink:
    """A pending invitation to bind one channel, spendable once."""

    token_hash: SecretHash
    channel_id: ChannelId
    user_id: UserId
    expires_at: PosixTime

    def is_expired(self, now: PosixTime) -> bool:
        return now.to_datetime() >= self.expires_at.to_datetime()
