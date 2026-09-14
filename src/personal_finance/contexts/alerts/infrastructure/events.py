"""Where this context's domain events go, which is the log and nowhere else.

Alerts is the first leaf context here: it subscribes to `finflow.financial`
and has no subscribers of its own. So there is no
`application/integration_events.py` and no translator — and *not writing one
is the mechanism*, not an oversight. The project's rule is that an event
reaches EventBridge only through a translator its context wrote; without one,
nothing leaves.

Two reasons it should stay that way. There is nobody on the other side, so a
`MessageDelivered` on the bus would be a contract no one reads while creating
a second at-least-once path to reason about. And what these events carry —
that an account bound a destination — is this context's own lifecycle, not
something another context should be able to depend on.

If that changes, the thing to add is a translator, not a call from somewhere
else into here.
"""

from __future__ import annotations

import functools

from personal_finance.shared.application.ports import EventPublisher
from personal_finance.shared.infrastructure.observability.logging_event_publisher import (  # noqa: E501
    LoggingEventPublisher,
)


@functools.lru_cache(maxsize=1)
def build_alerts_event_publisher() -> EventPublisher:
    return LoggingEventPublisher()
