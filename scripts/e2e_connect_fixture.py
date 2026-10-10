"""The facts the browser suites need and no browser can produce.

    ENV_FILE=.env PYTHONPATH=src uv run python scripts/e2e_connect_fixture.py \
        ticket <email>
    ENV_FILE=.env PYTHONPATH=src uv run python scripts/e2e_connect_fixture.py \
        confirm <forwarding address>
    ENV_FILE=.env PYTHONPATH=src uv run python scripts/e2e_connect_fixture.py \
        person <email> --password <password>

In a real setup both come from outside the app. Registering needs a ticket
earned by reading a code out of an inbox; Google's forwarding confirmation is
written when the ingest worker follows the link Google mailed to the shared
mailbox. Locally there is no inbox and no Google, so this writes each of them
the way `just seed` does — through the same repositories, never through an
endpoint, because no endpoint may claim either — and refuses to run anywhere
but the emulator.

`person` is a whole account, for the suites that need a fresh person but are
not about registering: it runs the same registration use case the endpoint
runs, with a ticket written the same way, and prints the access token. It
skips only the endpoint's per-address door — ten accounts a quarter hour —
which a full browser run would otherwise exhaust on its own. The suite that
*is* about registering, `e2e-connect`, still goes through `/identity/register`.

Each fact prints what its caller reads from stdout and nothing else.
"""

from __future__ import annotations

import argparse

from personal_finance.contexts.identity.application.commands import (
    RegisterUserCommand,
)
from personal_finance.contexts.identity.presentation.cli.verification_tickets import (
    issue_registration_ticket,
)
from personal_finance.contexts.identity.presentation.http.router import (
    get_register_use_case,
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


def _person(email: str, password: str) -> str:
    result = get_register_use_case().execute(
        RegisterUserCommand(
            email=email,
            password=password,
            verification_token=issue_registration_ticket(email),
            name="E2E",
        ),
    )

    return result.access_token.value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("fact", choices=("ticket", "confirm", "person"))
    parser.add_argument("subject", help="The email to register, or the address.")
    parser.add_argument("--password", help="For `person`: the account's password.")
    args = parser.parse_args()

    if not get_aws_settings().is_local:
        raise SystemExit(
            "Refusing to run: this writes facts only Google and an inbox may "
            "produce, and ENVIRONMENT is not local.",
        )

    if args.fact == "ticket":
        print(issue_registration_ticket(args.subject))
    elif args.fact == "person":
        if not args.password:
            raise SystemExit("`person` needs --password.")
        print(_person(args.subject, args.password))
    else:
        _confirm(args.subject)


if __name__ == "__main__":
    main()
