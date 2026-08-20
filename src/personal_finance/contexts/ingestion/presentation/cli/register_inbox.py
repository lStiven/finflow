"""Registers an inbound address and the senders its owner trusts.

    uv run python -m personal_finance.contexts.ingestion.presentation.cli\\
        .register_inbox --address u-7f3a9c@inbound.example.com \\
        --domain bancolombia.com.co --sender alertas@nequi.com.co
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
    parser.add_argument(
        "--address",
        required=True,
        help="the inbound address the user forwards their bank email to",
    )
    parser.add_argument(
        "--user-id",
        default=None,
        help="existing user to attach this address to (default: a new user)",
    )
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
    )
    inbox = use_case.execute(
        RegisterUserInboxCommand(
            address=EmailAddress(args.address),
            user_id=UserId.from_string(args.user_id) if args.user_id else None,
            allowed_domains=frozenset(args.domains),
            allowed_addresses=frozenset(
                EmailAddress(sender) for sender in args.senders
            ),
        ),
    )

    policy = inbox.sender_policy
    print(f"Registered {inbox.address.value}")
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
