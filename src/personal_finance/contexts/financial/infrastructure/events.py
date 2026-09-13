from __future__ import annotations

import functools

from personal_finance.contexts.financial.application.integration_events import (
    FinancialIntegrationEventTranslator,
)
from personal_finance.shared.application.ports import EventPublisher
from personal_finance.shared.infrastructure.aws.eventbridge import (
    EventBridgeEventPublisher,
)
from personal_finance.shared.infrastructure.aws.session import get_eventbridge_client
from personal_finance.shared.infrastructure.config.settings import (
    get_ingestion_settings,
)
from personal_finance.shared.infrastructure.observability.composite_event_publisher import (  # noqa: E501
    CompositeEventPublisher,
)
from personal_finance.shared.infrastructure.observability.logging_event_publisher import (  # noqa: E501
    LoggingEventPublisher,
)


@functools.lru_cache(maxsize=1)
def build_financial_event_publisher() -> EventPublisher:
    """Wire the publisher the worker and the API both share.

    Logging first: every domain event stays in the local audit trail, and it
    is already written if the bus rejects the publish.

    One builder for both entry points on purpose. Financial is the context
    with the most places that move money — the worker draining the queue and
    every endpoint that records, edits, assigns or erases — and while each of
    them constructed its own publisher, the one that mattered was whichever
    was edited last. A single cached builder is what keeps a movement recorded
    through the API indistinguishable, to a subscriber, from one recorded by
    the worker.
    """
    return CompositeEventPublisher(
        LoggingEventPublisher(),
        EventBridgeEventPublisher(
            client=get_eventbridge_client(),
            # The bus is one shared resource; ingestion's setting names it for
            # every context.
            event_bus_name=get_ingestion_settings().event_bus_name,
            translator=FinancialIntegrationEventTranslator(),
        ),
    )
