"""Sets the senders trusted for an existing user's forwarding address.

    uv run python -m personal_finance.contexts.ingestion.presentation.cli\\
        .register_inbox --user-id 11111111-... \\
        --domain bancolombia.com.co --sender alertas@nequi.com.co

Every account already has its forwarding address from the moment it
registers; this only ever updates who may send to it. It replaces the
approved-sender list rather than adding to it.
"""

from __future__ import annotations

import argparse

from personal_finance.contexts.ingestion.application.inbox_handlers import (
    RegisterUserInboxCommand,
    RegisterUserInboxUseCase,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.contexts.ingestion.infrastructure.persistence.user_inbox_dynamodb import (  # noqa: E501
    DynamoDBUserInboxRepository,
)
from personal_finance.shared.domain.value_objects import UserId
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import (
    get_ingestion_settings,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True, help="an existing user's id")
    parser.add_argument(
        "--domain",
        action="append",
        default=[],
        dest="domains",
        help="trust every sender from this domain; repeatable",
    )
    parser.add_argument(
        "--sender",
        action="append",
        default=[],
        dest="senders",
        help="trust this exact sender address; repeatable",
    )

    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    settings = get_ingestion_settings()

    use_case = RegisterUserInboxUseCase(
        inbox_repository=DynamoDBUserInboxRepository(
            client=get_dynamodb_client(),
            table_name=settings.user_inboxes_table,
        ),
        base_address=EmailAddress(settings.ingest_mailbox_address),
    )
    inbox = use_case.execute(
        RegisterUserInboxCommand(
            user_id=UserId.from_string(args.user_id),
            allowed_domains=frozenset(args.domains),
            allowed_addresses=frozenset(
                EmailAddress(sender) for sender in args.senders
            ),
        ),
    )

    policy = inbox.sender_policy
    print(f"Forwarding address: {inbox.address.value}")
    print(f"  user id : {inbox.user_id.value}")
    print(f"  domains : {sorted(policy.allowed_domains) or '(none)'}")
    print(
        f"  senders : "
        f"{sorted(address.value for address in policy.allowed_addresses) or '(none)'}",
    )

    if not policy.allowed_domains and not policy.allowed_addresses:
        print(
            "\nWARNING: this inbox trusts nobody, so every email to it will be "
            "stored as ignored. Add --domain or --sender.",
        )


if __name__ == "__main__":
    main()
