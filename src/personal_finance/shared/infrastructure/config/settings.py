from __future__ import annotations

import functools

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AwsSettings(BaseSettings):
    """AWS resource identifiers, read from the environment.

    `endpoint_url` is only meant for local emulators (LocalStack); it must stay
    unset in deployed environments so boto3 resolves the real endpoints.
    """

    model_config = SettingsConfigDict(env_prefix="AWS_", extra="ignore")

    region: str = "us-east-1"
    endpoint_url: str | None = None


class IngestionSettings(BaseSettings):
    """Resources owned by the ingestion context."""

    model_config = SettingsConfigDict(env_prefix="INGESTION_", extra="ignore")

    notifications_table: str = "bank_notifications"
    # No default: an empty queue URL would silently drop every notification.
    parse_queue_url: str
    event_bus_name: str = "default"
    authorized_sender_domains: frozenset[str] = Field(default_factory=frozenset)
    authorized_sender_addresses: frozenset[str] = Field(default_factory=frozenset)
    # Raw email bodies are untrusted, bulky, and only useful while a
    # notification can still be reprocessed. DynamoDB TTL drops them after this.
    retention_days: int = 90


@functools.lru_cache(maxsize=1)
def get_aws_settings() -> AwsSettings:
    return AwsSettings()


@functools.lru_cache(maxsize=1)
def get_ingestion_settings() -> IngestionSettings:
    # Required fields are supplied by the environment, which the type checker
    # cannot see.
    return IngestionSettings()  # pyright: ignore[reportCallIssue]
