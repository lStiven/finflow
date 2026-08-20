from __future__ import annotations

import dataclasses

from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import ValueObject


@dataclasses.dataclass(frozen=True, slots=True)
class AuthorizedSenderPolicy(ValueObject):
    """Decides whether an email sender is trusted to originate bank
    notifications.

    The allow-list itself is configuration (e.g. loaded from application
    settings); this class only encodes the matching rule, not the data.
    """

    allowed_addresses: frozenset[EmailAddress] = dataclasses.field(
        default_factory=lambda: frozenset[EmailAddress](),
    )
    allowed_domains: frozenset[str] = dataclasses.field(
        default_factory=lambda: frozenset[str](),
    )

    def __post_init__(self) -> None:
        if not self.allowed_addresses and not self.allowed_domains:
            raise ValueError(
                "AuthorizedSenderPolicy requires at least one allowed "
                "address or domain",
            )

        normalized_domains = frozenset(
            domain.strip().lower() for domain in self.allowed_domains
        )
        object.__setattr__(self, "allowed_domains", normalized_domains)

    def is_authorized(self, sender: EmailAddress) -> bool:
        return sender in self.allowed_addresses or sender.domain in self.allowed_domains
