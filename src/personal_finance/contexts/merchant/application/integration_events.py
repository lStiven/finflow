"""What merchant publishes to the rest of the system.

This module is the context's outward contract. Nothing here is derived
automatically from a domain object: every field is listed by hand, so adding
one to an aggregate can never silently widen what other contexts receive.

Four facts leave, and they are the ones that invalidate what a downstream
context may be holding:

* a merchant now exists;
* a spelling resolves to it — which is what lets a context holding past
  transactions re-attribute them after a user corrects a grouping;
* its category changed;
* it absorbed another, so anybody holding the absorbed id has a dangling
  reference.

Renames and sightings stay inside. A name is cosmetic and the id is stable,
and how often we saw a spelling is this context's own bookkeeping.
"""

from __future__ import annotations

from personal_finance.contexts.merchant.domain.events import (
    MerchantAliasLinked,
    MerchantIdentified,
    MerchantReclassified,
    MerchantsMerged,
)
from personal_finance.shared.application.integration import IntegrationEvent
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import JsonValue


SOURCE = "finflow.merchant"

MERCHANT_IDENTIFIED = "MerchantIdentified"
MERCHANT_ALIAS_LINKED = "MerchantAliasLinked"
MERCHANT_RECLASSIFIED = "MerchantReclassified"
MERCHANTS_MERGED = "MerchantsMerged"
VERSION = 1


class MerchantIntegrationEventTranslator:
    def translate(self, event: Event) -> IntegrationEvent | None:
        match event:
            case MerchantIdentified():
                return _event(
                    event,
                    MERCHANT_IDENTIFIED,
                    {
                        "display_name": event.display_name,
                        "category": event.category.value,
                    },
                )
            case MerchantAliasLinked():
                return _event(
                    event,
                    MERCHANT_ALIAS_LINKED,
                    {
                        "alias": event.fingerprint.value,
                        "origin": event.origin.value,
                    },
                )
            case MerchantReclassified():
                return _event(
                    event,
                    MERCHANT_RECLASSIFIED,
                    {"category": event.category.value},
                )
            case MerchantsMerged():
                return _event(
                    event,
                    MERCHANTS_MERGED,
                    {"absorbed_merchant_id": str(event.absorbed_merchant_id.value)},
                )
            case _:
                return None


def _event(
    event: MerchantIdentified
    | MerchantAliasLinked
    | MerchantReclassified
    | MerchantsMerged,
    detail_type: str,
    payload: dict[str, JsonValue],
) -> IntegrationEvent:
    return IntegrationEvent(
        source=SOURCE,
        detail_type=detail_type,
        version=VERSION,
        event_id=event.event_id,
        payload={
            "merchant_id": str(event.merchant_id.value),
            "user_id": str(event.user_id.value),
            **payload,
        },
        occurred_at=event.occurred_at,
    )
