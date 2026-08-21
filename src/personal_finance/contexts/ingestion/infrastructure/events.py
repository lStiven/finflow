from __future__ import annotations

import functools

from personal_finance.contexts.ingestion.application.integration_events import (
    IngestionIntegrationEventTranslator,
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
def build_ingestion_event_publisher() -> EventPublisher:
    """Wire the publisher both of ingestion's entry points share.

    Logging first: every domain event stays in the local audit trail, and it
    is already written if the bus rejects the publish.
    """
    return CompositeEventPublisher(
        LoggingEventPublisher(),
        EventBridgeEventPublisher(
            client=get_eventbridge_client(),
            event_bus_name=get_ingestion_settings().event_bus_name,
            translator=IngestionIntegrationEventTranslator(),
        ),
    )
