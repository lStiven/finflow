from __future__ import annotations

import dataclasses

from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import ValueObject


@dataclasses.dataclass(frozen=True, slots=True)
class AuthorizedSenderPolicy(ValueObject):
    """Decides whether an email sender is trusted to originate bank
    notifications for one user.

    The allow-list itself is data owned by the user and loaded from storage;
    this class only encodes the matching rule.

    An empty policy is a valid state — a user who has not approved any sender
    yet — and it authorizes nothing. The rule fails closed by construction.
    """

    allowed_addresses: frozenset[EmailAddress] = dataclasses.field(
        default_factory=lambda: frozenset[EmailAddress](),
    )
    allowed_domains: frozenset[str] = dataclasses.field(
        default_factory=lambda: frozenset[str](),
    )

    def __post_init__(self) -> None:
        normalized_domains = frozenset(
            domain.strip().lower() for domain in self.allowed_domains
        )
        object.__setattr__(self, "allowed_domains", normalized_domains)

    def is_authorized(self, sender: EmailAddress) -> bool:
        return sender in self.allowed_addresses or sender.domain in self.allowed_domains
