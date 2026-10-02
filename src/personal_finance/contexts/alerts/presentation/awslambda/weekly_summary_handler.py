"""Lambda entry point for Monday's summaries, fired by a schedule.

Raising is how a run asks to be retried: an asynchronous invocation that
fails is tried again by Lambda, and the delivery log and the inbox's
conditional write make a second run send only what the first could not.
"""

from __future__ import annotations

import dataclasses

from personal_finance.contexts.alerts.presentation.cli.run_weekly_summary import run
from personal_finance.shared.infrastructure.observability.logging_config import (
    configure_logging,
)


def handler(_event: object, _context: object) -> dict[str, int]:
    configure_logging()

    return dataclasses.asdict(run())
