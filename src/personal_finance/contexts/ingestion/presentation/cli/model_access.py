"""Look at, or give back, one person's access to the model.

just model-access <user_id>                 # how it stands, local
just model-access-prod <user_id> --reinstate

Reinstating is what "comunícate con servicio al cliente" ends in: it lifts a
pause or a revocation and clears the strikes, so the next burst starts over
at the first one. It sends nobody anything.
"""

from __future__ import annotations

import argparse
import uuid

from personal_finance.contexts.ingestion.application.model_access import (
    GetModelAccessUseCase,
    ModelAccessView,
    ReinstateModelReadingUseCase,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.model_standing_dynamodb import (  # noqa: E501
    DynamoDBModelStandings,
)
from personal_finance.shared.domain.value_objects import UserId
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import (
    get_ingestion_settings,
)


def _describe(view: ModelAccessView) -> str:
    until = (
        ""
        if view.paused_until is None
        else f" until {view.paused_until.to_datetime().isoformat()}"
    )

    return f"{view.access.value}{until}, {view.strikes} standing strike(s)"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Look at, or give back, one person's access to the model.",
    )
    parser.add_argument("user_id", type=uuid.UUID)
    parser.add_argument(
        "--reinstate",
        action="store_true",
        help="lift any suspension and clear the strikes",
    )
    arguments = parser.parse_args()

    user_id = UserId(value=arguments.user_id)
    settings = get_ingestion_settings()
    standings = DynamoDBModelStandings(
        client=get_dynamodb_client(),
        table_name=settings.notifications_table,
    )

    if not arguments.reinstate:
        view = GetModelAccessUseCase(standings=standings).execute(user_id=user_id)
        print(f"{settings.notifications_table}: {_describe(view)}")

        return

    before = ReinstateModelReadingUseCase(standings=standings).execute(user_id=user_id)
    print(
        f"{settings.notifications_table}: was {_describe(before)}; now open, no strikes"
    )


if __name__ == "__main__":
    main()
