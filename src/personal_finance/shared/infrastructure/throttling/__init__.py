"""Building the one guard every door shares.

Cached, like every other wiring in this deployment: two guards would be two
trust decisions about the same front door, and the day they disagreed the
limits would quietly stop applying to half the endpoints.
"""

from __future__ import annotations

import functools

from personal_finance.shared.infrastructure.aws.session import (
    get_throttling_dynamodb_client,
)
from personal_finance.shared.infrastructure.config.settings import get_api_settings
from personal_finance.shared.infrastructure.throttling.dynamodb import (
    DynamoDBAttemptCounter,
)
from personal_finance.shared.presentation.throttling import Guard


@functools.lru_cache(maxsize=1)
def build_guard() -> Guard:
    settings = get_api_settings()

    return Guard(
        DynamoDBAttemptCounter(
            client=get_throttling_dynamodb_client(),
            table_name=settings.throttle_table,
        ),
        trust_proxy=settings.trust_proxy_headers,
    )
