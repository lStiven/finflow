"""Drops a message into the simulated provider's mailbox.

    just mailbox-deliver --address ana@gmail.test \\
        --sender alertas@bancolombia.com.co \\
        --body "Bancolombia: Compraste ..."

This stands in for the bank sending an email. Nothing is ingested yet: the
message just sits in the mailbox until the provider rings the doorbell, which
is exactly how a real provider behaves. Ring it with `just mailbox-notify`.
"""

from __future__ import annotations

import argparse
import uuid

from personal_finance.contexts.ingestion.application.mailbox import InboundEmail
from personal_finance.contexts.ingestion.domain.value_objects import (
    EmailAddress,
    EmailMessageId,
)
from personal_finance.contexts.ingestion.infrastructure.mailbox.simulated import (
    SimulatedMailboxStore,
)
from personal_finance.shared.domain.value_objects import PosixTime
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import (
    get_ingestion_settings,
)


DEFAULT_BODY = (
    "Bancolombia: Compraste $45.000 en EXITO CALI con tu T.Cred *1234, "
    "el 20/08/2026 a las 10:15"
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--address",
        required=True,
        help="the mailbox to deliver into, i.e. the user's own email address",
    )
    parser.add_argument(
        "--sender",
        default="alertas@bancolombia.com.co",
        help="who the message is from",
    )
    parser.add_argument("--subject", default="Notificación Bancolombia")
    parser.add_argument(
        "--body",
        default=DEFAULT_BODY,
        help="the raw message text the parser will read",
    )
    parser.add_argument(
        "--message-id",
        default=None,
        help="defaults to a fresh one; repeat a value to exercise deduplication",
    )

    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    settings = get_ingestion_settings()
    store = SimulatedMailboxStore(
        client=get_dynamodb_client(),
        table_name=settings.simulated_mailbox_table,
    )
    message_id = args.message_id or f"<{uuid.uuid4()}@simulated.test>"

    sequence = store.deliver(
        InboundEmail(
            message_id=EmailMessageId(message_id),
            recipient=EmailAddress(args.address),
            sender=EmailAddress(args.sender),
            subject=args.subject,
            raw_content=args.body,
            received_at=PosixTime.now(),
        ),
    )

    print(f"Delivered to {args.address} at position {sequence}")
    print(f"  from       {args.sender}")
    print(f"  message id {message_id}")
    print(
        "\nNothing has been read yet — the mailbox is not ours to look into "
        "until the provider says so. Ring it with:\n"
        f"  just mailbox-notify --address {args.address}",
    )


if __name__ == "__main__":
    main()
