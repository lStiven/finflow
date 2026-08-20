from __future__ import annotations

import dataclasses

from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
)
from personal_finance.shared.domain.value_objects import PosixTime


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class ReceiveBankNotificationCommand:
    message_id: EmailMessageId
    sender: EmailAddress
    subject: str
    raw_content: str
    received_at: PosixTime
