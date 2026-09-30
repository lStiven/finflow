"""Send Monday's summaries once, for the week before today.

just weekly-summary            # the week before this one
just weekly-summary 2026-09-21 # the week containing that day

In the cloud the same thing runs from `weekly_summary_handler` on a schedule.
Running it twice for the same week sends nothing twice.
"""

from __future__ import annotations

import argparse
import datetime as dt
from zoneinfo import ZoneInfo

from personal_finance.contexts.alerts.application.handlers import (
    SendWeeklySummariesUseCase,
    WeeklyRun,
    WeekNotOverError,
)
from personal_finance.contexts.alerts.infrastructure.financial.adapters import (
    build_weekly_spending,
)
from personal_finance.contexts.alerts.infrastructure.persistence.dynamodb import (
    DynamoDBAlertChannelRepository,
    DynamoDBDeliveryLog,
)
from personal_finance.contexts.alerts.infrastructure.persistence.inbox import (
    DynamoDBInbox,
    DynamoDBRecipients,
)
from personal_finance.contexts.alerts.presentation.http.dependencies import (
    build_summary_sender,
)
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import get_alerts_settings
from personal_finance.shared.infrastructure.observability.logging_config import (
    configure_logging,
)


def build_weekly_use_case() -> SendWeeklySummariesUseCase:
    settings = get_alerts_settings()
    dynamodb = get_dynamodb_client()
    table_name = settings.channels_table
    sender = build_summary_sender()

    return SendWeeklySummariesUseCase(
        recipients=DynamoDBRecipients(client=dynamodb, table_name=table_name),
        spending=build_weekly_spending(timezone=settings.display_timezone),
        channels=DynamoDBAlertChannelRepository(client=dynamodb, table_name=table_name),
        deliveries=DynamoDBDeliveryLog(client=dynamodb, table_name=table_name),
        sender=sender,
        inbox=DynamoDBInbox(client=dynamodb, table_name=table_name),
    )


def last_week(today: dt.date) -> dt.date:
    """A day of the week before the one `today` is in — the finished one."""
    return today - dt.timedelta(days=today.weekday() + 7)


def run(week_of: dt.date | None = None) -> WeeklyRun:
    zone = ZoneInfo(get_alerts_settings().display_timezone)
    today = dt.datetime.now(tz=zone).date()

    return build_weekly_use_case().execute(
        week_of=week_of or last_week(today),
        today=today,
    )


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "week_of",
        nargs="?",
        type=dt.date.fromisoformat,
        help="Any day of the week to summarise (YYYY-MM-DD). Default: last week.",
    )
    arguments = parser.parse_args()

    try:
        result = run(arguments.week_of)
    except WeekNotOverError:
        parser.error(
            "esa semana todavía no termina: su resumen sale el lunes siguiente"
        )
    print(
        f"{result.users} personas, {result.summaries} resúmenes, "
        f"{result.delivered} enviados por Telegram, {result.failed} fallidos",
    )


if __name__ == "__main__":
    main()
