class IngestionDomainError(Exception):
    """Base exception for ingestion domain errors."""


class InvalidNotificationStateError(IngestionDomainError):
    """Raised when an invalid notification state transition is attempted."""
