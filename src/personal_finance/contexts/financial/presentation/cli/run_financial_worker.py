"""Runs the financial worker until interrupted.

just financial-worker
"""

from __future__ import annotations

import logging

from personal_finance.contexts.financial.application.autopay import (
    SettleDueChargesUseCase,
)
from personal_finance.contexts.financial.application.bills import (
    SettleBillChargeUseCase,
)
from personal_finance.contexts.financial.application.handlers import (
    ManageTransactionsUseCase,
    RecordMovementUseCase,
    RecordTransferUseCase,
)
from personal_finance.contexts.financial.infrastructure.events import (
    build_financial_event_publisher,
)
from personal_finance.contexts.financial.infrastructure.merchant.merchant_directory import (  # noqa: E501
    build_merchant_directory,
)
from personal_finance.contexts.financial.infrastructure.messaging.sqs_worker import (
    SQSFinancialWorker,
)
from personal_finance.contexts.financial.infrastructure.persistence.dynamodb import (
    DynamoDBAccountRepository,
    DynamoDBScheduledBillRepository,
    DynamoDBTransactionLedger,
)
from personal_finance.shared.infrastructure.aws.session import (
    get_dynamodb_client,
    get_sqs_client,
)
from personal_finance.shared.infrastructure.config.settings import (
    get_financial_settings,
)
from personal_finance.shared.infrastructure.observability.logging_config import (
    configure_logging,
)
from personal_finance.shared.presentation.worker_loop import drain_until_stopped


_logger = logging.getLogger(__name__)


def build_worker() -> SQSFinancialWorker:
    settings = get_financial_settings()

    if not settings.events_queue_url:
        raise ValueError(
            "FINANCIAL_EVENTS_QUEUE_URL is not set: there is no queue to "
            "drain, and every extracted transaction would leave balances "
            "untouched. Run `just aws-provision` and copy the URL it prints.",
        )

    dynamodb = get_dynamodb_client()
    table_name = settings.accounts_table
    accounts = DynamoDBAccountRepository(client=dynamodb, table_name=table_name)
    ledger = DynamoDBTransactionLedger(client=dynamodb, table_name=table_name)
    events = build_financial_event_publisher()
    bills = DynamoDBScheduledBillRepository(client=dynamodb, table_name=table_name)

    return SQSFinancialWorker(
        client=get_sqs_client(),
        queue_url=settings.events_queue_url,
        use_case=RecordMovementUseCase(
            accounts=accounts,
            ledger=ledger,
            event_publisher=events,
        ),
        # The same queue carries both: one rule, two detail types, because a
        # transfer and a purchase both end as rows on the same balances.
        transfer_use_case=RecordTransferUseCase(
            accounts=accounts,
            ledger=ledger,
            event_publisher=events,
        ),
        # Match only, as each movement lands; built like the bills screen's
        # sweep so the two can never disagree about what pays a charge.
        reconcile=SettleDueChargesUseCase(
            bills=bills,
            charges=ledger,
            ledger=ledger,
            settle=SettleBillChargeUseCase(
                bills=bills,
                accounts=accounts,
                charges=ledger,
                transactions=ManageTransactionsUseCase(
                    accounts=accounts,
                    ledger=ledger,
                    event_publisher=events,
                ),
            ),
            merchants=build_merchant_directory(),
        ),
    )


def main() -> None:
    configure_logging()
    drain_until_stopped(build_worker(), name="financial")


if __name__ == "__main__":
    main()
