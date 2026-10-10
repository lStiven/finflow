"""The two facts the connect-your-bank e2e needs and no browser can produce.

    ENV_FILE=.env PYTHONPATH=src uv run python scripts/e2e_connect_fixture.py \
        ticket <email>
    ENV_FILE=.env PYTHONPATH=src uv run python scripts/e2e_connect_fixture.py \
        confirm <forwarding address>

In a real setup both come from outside the app. Registering needs a ticket
earned by reading a code out of an inbox; Google's forwarding confirmation is
written when the ingest worker follows the link Google mailed to the shared
mailbox. Locally there is no inbox and no Google, so this writes each of them
the way `just seed` does — through the same repositories, never through an
endpoint, because no endpoint may claim either — and refuses to run anywhere
but the emulator.

Driven by `frontend/scripts/e2e-connect.mjs`, which prints a ticket's token
from stdout and nothing else, so this prints nothing else either.
"""

from __future__ import annotations

import argparse

from personal_finance.contexts.identity.presentation.cli.verification_tickets import (
    issue_registration_ticket,
)
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.contexts.ingestion.infrastructure.persistence.user_inbox_dynamodb import (  # noqa: E501
    DynamoDBUserInboxRepository,
)
from personal_finance.shared.domain.value_objects import PosixTime
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import (
    get_aws_settings,
    get_ingestion_settings,
)


def _confirm(address: str) -> None:
    settings = get_ingestion_settings()
    repository = DynamoDBUserInboxRepository(
        client=get_dynamodb_client(),
        table_name=settings.user_inboxes_table,
    )
    repository.mark_forwarding_confirmed(
        address=EmailAddress(address),
        confirmed_at=PosixTime.now(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fact", choices=("ticket", "confirm"))
    parser.add_argument("subject", help="The email to register, or the address.")
    args = parser.parse_args()

    if not get_aws_settings().is_local:
        raise SystemExit(
            "Refusing to run: this writes facts only Google and an inbox may "
            "produce, and ENVIRONMENT is not local.",
        )

    if args.fact == "ticket":
        print(issue_registration_ticket(args.subject))
    else:
        _confirm(args.subject)


if __name__ == "__main__":
    main()
