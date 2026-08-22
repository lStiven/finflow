"""Renews the mailbox subscriptions that are due.

    just subscriptions            # one pass
    just subscriptions --watch    # keep passing, for a long-running process

This is the floor of the strategy, not all of it: notifications renew a busy
mailbox for free, and a user opening the application renews theirs. What is
left for this pass are the mailboxes too quiet to have renewed themselves —
which are exactly the ones nobody would notice going silent.

Run it on a schedule at least twice per subscription lifetime.
"""

from __future__ import annotations

import argparse
import logging
import signal
import time
from types import FrameType

from personal_finance.contexts.ingestion.application.subscription_handlers import (
    KeepSubscriptionAliveUseCase,
    SweepSubscriptionsUseCase,
)
from personal_finance.contexts.ingestion.infrastructure.mailbox.registry import (
    get_subscribers,
)
from personal_finance.contexts.ingestion.infrastructure.persistence.mailbox_connection_dynamodb import (  # noqa: E501
    DynamoDBMailboxConnectionRepository,
)
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import (
    get_ingestion_settings,
)


_logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 3_600


class _Stopper:
    def __init__(self) -> None:
        self.requested = False

    def __call__(self, signum: int, frame: FrameType | None) -> None:
        del signum, frame
        self.requested = True


def build_sweep() -> SweepSubscriptionsUseCase:
    settings = get_ingestion_settings()
    repository = DynamoDBMailboxConnectionRepository(
        client=get_dynamodb_client(),
        table_name=settings.mailbox_connections_table,
    )

    return SweepSubscriptionsUseCase(
        connection_repository=repository,
        keep_alive=KeepSubscriptionAliveUseCase(
            subscribers=get_subscribers(),
            connection_repository=repository,
        ),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Renew mailbox subscriptions")
    parser.add_argument(
        "--watch",
        action="store_true",
        help="keep sweeping on an interval instead of running once",
    )
    parser.add_argument(
        "--interval",
        type=int,
        default=DEFAULT_INTERVAL_SECONDS,
        help=f"seconds between passes (default {DEFAULT_INTERVAL_SECONDS})",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    sweep = build_sweep()
    stopper = _Stopper()
    signal.signal(signal.SIGINT, stopper)
    signal.signal(signal.SIGTERM, stopper)

    while True:
        result = sweep.execute()
        print(
            f"renewed {result.renewed} · still good {result.not_due} · "
            f"needs reauthorization {result.needs_reauth} · "
            f"deferred {result.deferred}",
        )

        if result.needs_reauth:
            print(
                "  Some mailboxes need the user to authorize again. They are "
                "listed by `GET /identity/mailboxes` with status needs_reauth.",
            )

        if not args.watch or stopper.requested:
            return

        # Woken often enough to notice a stop request promptly, rather than
        # sleeping through the whole interval.
        for _ in range(args.interval):
            if stopper.requested:
                return

            time.sleep(1)


if __name__ == "__main__":
    main()
