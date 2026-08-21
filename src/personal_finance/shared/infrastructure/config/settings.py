from __future__ import annotations

import enum
import functools
import os
from typing import Literal, Self

from pydantic import AliasChoices, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# Which env file to read, so a production-shaped run never picks up the local
# emulator's values. Real environment variables still win over the file, which
# is what containers and CI rely on.
ENV_FILE = os.getenv("ENV_FILE", ".env")


class Environment(enum.Enum):
    LOCAL = "local"
    PRODUCTION = "production"


# A Literal rather than an Enum: these are the exact strings the DynamoDB API
# expects, so there is nothing to translate at the call site.
type BillingMode = Literal["PROVISIONED", "PAY_PER_REQUEST"]


class AwsSettings(BaseSettings):
    """AWS region, endpoint and credentials.

    Field names mirror the standard AWS environment variables, so the same
    `AWS_PROFILE` / `AWS_ACCESS_KEY_ID` / `AWS_SESSION_TOKEN` a developer
    already exports are what the application reads. Nothing here reaches boto3
    implicitly: `session.py` passes every value to `boto3.Session`.
    """

    model_config = SettingsConfigDict(
        env_prefix="AWS_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    # Not an AWS_ variable: the deployment environment is application-wide.
    environment: Environment = Field(
        default=Environment.LOCAL,
        validation_alias=AliasChoices("ENVIRONMENT"),
    )
    region: str = "us-east-1"
    # Only for emulators (moto, LocalStack). Must stay unset anywhere real.
    endpoint_url: str | None = None
    profile: str | None = None
    access_key_id: str | None = None
    secret_access_key: SecretStr | None = None
    session_token: SecretStr | None = None

    @model_validator(mode="after")
    def _reject_emulator_in_production(self) -> Self:
        if self.environment is Environment.PRODUCTION and self.endpoint_url:
            raise ValueError(
                "AWS_ENDPOINT_URL must be unset when ENVIRONMENT=production: it "
                "would silently route production traffic to an emulator",
            )

        return self

    @property
    def is_local(self) -> bool:
        return self.environment is Environment.LOCAL


class IngestionSettings(BaseSettings):
    """Resources owned by the ingestion context."""

    model_config = SettingsConfigDict(
        env_prefix="INGESTION_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    notifications_table: str = "bank_notifications"
    # Approved senders are per-user data, not configuration: they live in this
    # table, keyed by the inbound address the email was delivered to.
    user_inboxes_table: str = "user_inboxes"
    # Which mailboxes a user authorized us to read, and how far we got in
    # each. Never holds a credential.
    mailbox_connections_table: str = "mailbox_connections"
    # The mailbox the simulated provider hosts, so the notify-fetch-ingest
    # path can be exercised without a real account. Local use only.
    simulated_mailbox_table: str = "simulated_mailbox"
    # The queue has two identifiers on purpose. Provisioning only knows the
    # name, because the URL embeds an account id that does not exist yet the
    # first time the resources are created. The application only accepts the
    # full URL, so it never has to look anything up at request time.
    parse_queue_name: str = "parse-notifications"
    parse_queue_url: str = ""
    event_bus_name: str = "default"
    # The tap on the integration bus. Provisioning creates the queue and the
    # rule that feeds it; only the inspection CLI reads from it, so an unset
    # URL degrades that one tool rather than the application.
    integration_events_queue_name: str = "integration-events"
    integration_events_queue_url: str = ""
    # Raw email bodies are untrusted, bulky, and only useful while a
    # notification can still be reprocessed. DynamoDB TTL drops them after this.
    retention_days: int = 90

    # PROVISIONED with small capacity stays inside the always-free DynamoDB
    # allowance (25 read and 25 write units across the account). On-demand is
    # billed from the first request, so it is not the right default for a
    # test account.
    dynamodb_billing_mode: BillingMode = "PROVISIONED"
    dynamodb_read_capacity: int = 5
    dynamodb_write_capacity: int = 5


class IdentitySettings(BaseSettings):
    """Resources owned by the identity context."""

    model_config = SettingsConfigDict(
        env_prefix="IDENTITY_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    users_table: str = "users"
    # Empty by default so a deployment that forgot to set it fails loudly at
    # startup instead of signing every token with a well-known value.
    jwt_secret: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 24


@functools.lru_cache(maxsize=1)
def get_aws_settings() -> AwsSettings:
    return AwsSettings()


@functools.lru_cache(maxsize=1)
def get_ingestion_settings() -> IngestionSettings:
    return IngestionSettings()


@functools.lru_cache(maxsize=1)
def get_identity_settings() -> IdentitySettings:
    return IdentitySettings()


def reset_settings() -> None:
    """Drop the cached settings. Only useful for tests that change the
    environment after something already read it.
    """
    get_aws_settings.cache_clear()
    get_ingestion_settings.cache_clear()
    get_identity_settings.cache_clear()
