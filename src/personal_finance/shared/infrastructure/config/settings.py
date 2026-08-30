from __future__ import annotations

import enum
import functools
import os
from typing import Literal, Self
import urllib.parse

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from personal_finance.shared.infrastructure.config.secrets import (
    reset_secrets_cache,
    resolve,
)


# Which env file to read, so a production-shaped run never picks up the local
# emulator's values. Real environment variables still win over the file, which
# is what containers and CI rely on.
ENV_FILE = os.getenv("ENV_FILE", ".env")


class Environment(enum.Enum):
    LOCAL = "local"
    DEVELOPMENT = "development"
    PRODUCTION = "production"

    @property
    def resource_prefix(self) -> str:
        """What every AWS resource this environment owns is named with.

        Production keeps the bare names it already created, and local runs
        against an emulator holding nothing else. Development is the one that
        needs a namespace, and it is the whole reason this exists: it shares
        one AWS account with production. Without it a dev run writes into
        somebody's real ledger — silently, because the write succeeds — and a
        dev worker *consumes* production's queue messages, which is worse:
        the message is deleted from the queue production was going to read.

        Applied centrally rather than by configuring nine names by hand, for
        the same reason: eight right and one forgotten is the case that costs
        real data, and nothing would report it.
        """
        return "dev-" if self is Environment.DEVELOPMENT else ""


# A Literal rather than an Enum: these are the exact strings the DynamoDB API
# expects, so there is nothing to translate at the call site.
type BillingMode = Literal["PROVISIONED", "PAY_PER_REQUEST"]


def _namespaced(name: str, *, environment: Environment) -> str:
    """A resource name with its environment's prefix, applied at most once.

    Idempotent so a deployment that also spells the prefix into the
    environment variable — the obvious thing to try — gets one prefix rather
    than `dev-dev-users`.
    """
    prefix = environment.resource_prefix

    if not prefix or name.startswith(prefix):
        return name

    return f"{prefix}{name}"


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


_ORIGIN_SCHEMES = frozenset({"http", "https"})


def _split_origins(raw: str) -> tuple[str, ...]:
    """Read a comma-separated origin list, the way an env file writes lists.

    Lowercased on the way in. Scheme and host are case-insensitive by
    specification and a browser sends them folded, but the middleware compares
    what is configured here to that header verbatim — so `HTTPS://App.example`
    would be accepted and then match nothing. An origin has no path, which is
    what makes folding the whole string safe.
    """
    return tuple(origin.strip().lower() for origin in raw.split(",") if origin.strip())


def _check_origin(origin: str) -> None:
    """An origin is a scheme, a host and maybe a port. Nothing else.

    Checked at startup because the alternative failure is silent and slow to
    diagnose: a browser sends `Origin: https://app.example.com`, a configured
    `https://app.example.com/` is compared to it verbatim and never matches,
    and every call from the real frontend is blocked with nothing logged
    anywhere.
    """
    parts = urllib.parse.urlsplit(origin)

    if (
        parts.scheme not in _ORIGIN_SCHEMES
        or not parts.netloc
        or parts.path
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            f"{origin!r} is not a browser origin. Write scheme://host[:port] "
            "with no trailing slash and no path: that is exactly what a "
            "browser puts in its Origin header, and anything else never "
            "matches it",
        )


class ApiSettings(BaseSettings):
    """How the HTTP surface may be reached from a browser.

    Only CORS lives here: which origins are allowed to call this API from a
    page this API did not serve. Empty means none, and that is the right
    default — a frontend served from the same origin needs no CORS at all, and
    the browser's own rule is the one worth keeping until somebody has a
    reason to widen it.
    """

    model_config = SettingsConfigDict(
        env_prefix="API_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    # Read here as well as in `AwsSettings` — same variable, and this class
    # needs it to refuse a wildcard outside a developer's machine without
    # reaching into another settings object's cache.
    environment: Environment = Field(
        default=Environment.LOCAL,
        validation_alias=AliasChoices("ENVIRONMENT"),
    )
    # Comma-separated rather than JSON, so an env file stays readable:
    # `API_CORS_ORIGINS=http://localhost:5173,https://app.example.com`.
    cors_origins: str = ""

    @model_validator(mode="after")
    def _validate_origins(self) -> Self:
        for origin in _split_origins(self.cors_origins):
            if origin != "*":
                _check_origin(origin)
                continue

            if self.environment is not Environment.LOCAL:
                raise ValueError(
                    "API_CORS_ORIGINS must name real origins when "
                    "ENVIRONMENT=production: '*' lets any page on the internet "
                    "call this API from a visitor's browser",
                )

        return self

    @property
    def allowed_origins(self) -> tuple[str, ...]:
        return _split_origins(self.cors_origins)


class IngestionSettings(BaseSettings):
    """Resources owned by the ingestion context."""

    model_config = SettingsConfigDict(
        env_prefix="INGESTION_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    # Read here as well as in `AwsSettings`, so a table name carries its
    # environment's prefix wherever these settings are built rather than only
    # where the app is wired.
    environment: Environment = Field(
        default=Environment.LOCAL,
        validation_alias=AliasChoices("ENVIRONMENT"),
    )
    # Bare names: the environment adds its own prefix below.
    notifications_table: str = "bank_notifications"
    # Approved senders are per-user data, not configuration: they live in this
    # table, keyed by the inbound address the email was delivered to.
    user_inboxes_table: str = "user_inboxes"

    @model_validator(mode="after")
    def _namespace_resources(self) -> Self:
        self.notifications_table = _namespaced(
            self.notifications_table,
            environment=self.environment,
        )
        self.user_inboxes_table = _namespaced(
            self.user_inboxes_table,
            environment=self.environment,
        )
        self.parse_queue_name = _namespaced(
            self.parse_queue_name,
            environment=self.environment,
        )
        self.integration_events_queue_name = _namespaced(
            self.integration_events_queue_name,
            environment=self.environment,
        )
        # The bus too, which is what scopes the rules on it: rule names are
        # unique per bus, so a namespaced bus needs no namespaced rules.
        # `default` is AWS's own bus and is never renamed.
        if self.event_bus_name != "default":
            self.event_bus_name = _namespaced(
                self.event_bus_name,
                environment=self.environment,
            )

        return self

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

    # On-demand, because the free allowance was costing more than it saved.
    # PROVISIONED gives 25 read and 25 write units free across the account,
    # and DynamoDB charges a secondary index like a table: five tables and two
    # indexes at three units each already spent 21 of the 25, so every new
    # index meant lowering capacity again — the schema being shaped by an
    # allowance rather than by the data. Three units is also roughly three
    # 4 KB reads a second, and several endpoints read a whole partition, so
    # normal use was one refresh away from throttling.
    #
    # On-demand is billed per request from the first one, which at this
    # deployment's volume (a handful of forwarded emails a day) is on the
    # order of a cent a month. That is the whole trade: an unmeasurable bill
    # in exchange for a ceiling that no longer constrains the design.
    # PROVISIONED is still supported and reachable by configuration.
    dynamodb_billing_mode: BillingMode = "PAY_PER_REQUEST"
    # Only read under PROVISIONED.
    dynamodb_read_capacity: int = 3
    dynamodb_write_capacity: int = 3
    # Only read under PAY_PER_REQUEST: a ceiling on requests per second, per
    # table and per index, so a runaway loop throttles instead of billing.
    #
    # It is a rate limit, not a budget — pegged for a month it would still
    # cost real money, so it is a blast radius and not a spending cap. The
    # guard for that is a billing alarm on the account. Twenty-five is what
    # the entire free allowance used to be *shared across all seven objects*,
    # so as a per-object ceiling it is far more headroom than this ever had,
    # while still being a number a bug has to get past.
    dynamodb_max_read_units: int = 25
    dynamodb_max_write_units: int = 25

    # The one Gmail account every user forwards their bank email to, read over
    # plain IMAP with an App Password. Empty by default so a deployment that
    # has not set this up yet fails loudly and specifically — "the ingest
    # worker has nothing to read" — rather than starting half-configured.
    ingest_mailbox_address: str = ""
    ingest_mailbox_app_password: SecretStr = SecretStr("")
    ingest_mailbox_host: str = "imap.gmail.com"
    ingest_mailbox_port: int = 993
    # How often the ingest worker polls, in seconds.
    ingest_poll_interval_seconds: int = 60

    @field_validator("ingest_mailbox_app_password")
    @classmethod
    def _resolve(cls, value: SecretStr) -> SecretStr:
        return resolve(value)

    @property
    def ingest_mailbox_configured(self) -> bool:
        return bool(
            self.ingest_mailbox_address
            and self.ingest_mailbox_app_password.get_secret_value(),
        )


class MerchantSettings(BaseSettings):
    """Resources owned by the merchant context."""

    model_config = SettingsConfigDict(
        env_prefix="MERCHANT_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    # Read here as well as in `AwsSettings`, so a table name carries its
    # environment's prefix wherever these settings are built rather than only
    # where the app is wired.
    environment: Environment = Field(
        default=Environment.LOCAL,
        validation_alias=AliasChoices("ENVIRONMENT"),
    )
    # Merchants, the spellings under them, the keys they are found by, and the
    # integration events already applied — all in one per-user partition.
    merchants_table: str = "merchants"
    # This context's own subscription to the bus. Two identifiers for the same
    # reason ingestion's parse queue has two: provisioning knows only the name
    # because the URL embeds an account id that does not exist yet.
    events_queue_name: str = "merchant-events"
    events_queue_url: str = ""

    @model_validator(mode="after")
    def _namespace_resources(self) -> Self:
        self.merchants_table = _namespaced(
            self.merchants_table,
            environment=self.environment,
        )
        self.events_queue_name = _namespaced(
            self.events_queue_name,
            environment=self.environment,
        )

        return self


class FinancialSettings(BaseSettings):
    """Resources owned by the financial context."""

    model_config = SettingsConfigDict(
        env_prefix="FINANCIAL_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    # Read here as well as in `AwsSettings`, so a table name carries its
    # environment's prefix wherever these settings are built rather than only
    # where the app is wired.
    environment: Environment = Field(
        default=Environment.LOCAL,
        validation_alias=AliasChoices("ENVIRONMENT"),
    )
    # Accounts, the fingerprints they answer to, and the ledger of movements —
    # all in one per-user partition, because a balance and the rows behind it
    # have to be written together.
    accounts_table: str = "financial"
    # This context's own subscription to the bus, named and resolved
    # separately for the same reason merchant's is.
    events_queue_name: str = "financial-events"
    events_queue_url: str = ""

    @model_validator(mode="after")
    def _namespace_resources(self) -> Self:
        self.accounts_table = _namespaced(
            self.accounts_table,
            environment=self.environment,
        )
        self.events_queue_name = _namespaced(
            self.events_queue_name,
            environment=self.environment,
        )

        return self


class LLMSettings(BaseSettings):
    """The language model every context falls back to.

    Shared because the credential and the endpoint are one deployment-wide
    concern; what to *ask* the model is not, and lives in each context's own
    `infrastructure/llm/`.
    """

    model_config = SettingsConfigDict(
        env_prefix="LLM_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    # Empty by default so a deployment that has not set Google up simply has
    # no fallback — deterministic parsing still runs, and an email no template
    # matches waits instead of failing. `GEMINI_API_KEY` is accepted too
    # because that is what Google's own tooling exports.
    api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("LLM_API_KEY", "GEMINI_API_KEY"),
    )
    model: str = "gemini-3.6-flash"
    # The answers are small structured objects; the ceiling exists to stop a
    # runaway generation, not to shape the output.
    max_output_tokens: int = 4_096
    # A worker holds an SQS message for 120 seconds, and a parse plus this
    # call has to fit inside that.
    timeout_seconds: int = 30
    # Email bodies are untrusted and occasionally enormous. Past this they are
    # cut, with a warning, rather than sent whole.
    max_input_characters: int = 40_000

    @field_validator("api_key")
    @classmethod
    def _resolve(cls, value: SecretStr) -> SecretStr:
        return resolve(value)

    @property
    def configured(self) -> bool:
        return bool(self.api_key.get_secret_value())


class IdentitySettings(BaseSettings):
    """Resources owned by the identity context."""

    model_config = SettingsConfigDict(
        env_prefix="IDENTITY_",
        env_file=ENV_FILE,
        extra="ignore",
    )

    # Read here as well as in `AwsSettings`, so a table name carries its
    # environment's prefix wherever these settings are built rather than only
    # where the app is wired.
    environment: Environment = Field(
        default=Environment.LOCAL,
        validation_alias=AliasChoices("ENVIRONMENT"),
    )
    users_table: str = "users"
    # Empty by default so a deployment that forgot to set it fails loudly at
    # startup instead of signing every token with a well-known value.
    jwt_secret: SecretStr = SecretStr("")
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 1

    @model_validator(mode="after")
    def _namespace_resources(self) -> Self:
        self.users_table = _namespaced(
            self.users_table,
            environment=self.environment,
        )

        return self

    @field_validator("jwt_secret")
    @classmethod
    def _resolve(cls, value: SecretStr) -> SecretStr:
        return resolve(value)


@functools.lru_cache(maxsize=1)
def get_aws_settings() -> AwsSettings:
    return AwsSettings()


@functools.lru_cache(maxsize=1)
def get_api_settings() -> ApiSettings:
    return ApiSettings()


@functools.lru_cache(maxsize=1)
def get_ingestion_settings() -> IngestionSettings:
    return IngestionSettings()


@functools.lru_cache(maxsize=1)
def get_merchant_settings() -> MerchantSettings:
    return MerchantSettings()


@functools.lru_cache(maxsize=1)
def get_financial_settings() -> FinancialSettings:
    return FinancialSettings()


@functools.lru_cache(maxsize=1)
def get_llm_settings() -> LLMSettings:
    return LLMSettings()


@functools.lru_cache(maxsize=1)
def get_identity_settings() -> IdentitySettings:
    return IdentitySettings()


def reset_settings() -> None:
    """Drop the cached settings. Only useful for tests that change the
    environment after something already read it.
    """
    get_aws_settings.cache_clear()
    get_api_settings.cache_clear()
    get_ingestion_settings.cache_clear()
    get_merchant_settings.cache_clear()
    get_financial_settings.cache_clear()
    get_llm_settings.cache_clear()
    get_identity_settings.cache_clear()
    reset_secrets_cache()
