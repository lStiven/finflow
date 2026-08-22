"""Which providers this deployment can actually talk to.

One place decides, so adding a provider is one entry rather than a hunt
through routers and workers. Gmail only appears when it is fully configured:
a half-configured provider that accepts a connection and then fails on every
notification is worse than one that is simply absent.
"""

from __future__ import annotations

import functools

from personal_finance.contexts.ingestion.application.mailbox import MailboxProvider
from personal_finance.contexts.ingestion.application.ports import (
    MailboxReader,
    MailboxSubscriber,
)
from personal_finance.contexts.ingestion.infrastructure.mailbox.gmail.api import (
    GmailApiClient,
)
from personal_finance.contexts.ingestion.infrastructure.mailbox.gmail.oauth import (
    GmailOAuthClient,
)
from personal_finance.contexts.ingestion.infrastructure.mailbox.gmail.reader import (
    GmailMailboxReader,
)
from personal_finance.contexts.ingestion.infrastructure.mailbox.gmail.subscriber import (  # noqa: E501
    GmailMailboxSubscriber,
)
from personal_finance.contexts.ingestion.infrastructure.mailbox.simulated import (
    SimulatedMailboxReader,
    SimulatedMailboxSubscriber,
)
from personal_finance.contexts.ingestion.infrastructure.mailbox.tokens import (
    GmailAccessTokenProvider,
    MailboxTokenStore,
    SecretsManagerTokenStore,
)
from personal_finance.shared.infrastructure.aws.session import (
    get_dynamodb_client,
    get_secretsmanager_client,
)
from personal_finance.shared.infrastructure.config.settings import (
    get_aws_settings,
    get_ingestion_settings,
)


@functools.lru_cache(maxsize=1)
def get_token_store() -> MailboxTokenStore:
    return SecretsManagerTokenStore(client=get_secretsmanager_client())


@functools.lru_cache(maxsize=1)
def get_gmail_oauth_client() -> GmailOAuthClient:
    settings = get_ingestion_settings()

    return GmailOAuthClient(
        client_id=settings.gmail_client_id,
        client_secret=settings.gmail_client_secret.get_secret_value(),
        redirect_uri=settings.gmail_redirect_uri,
    )


@functools.lru_cache(maxsize=1)
def _gmail_token_provider() -> GmailAccessTokenProvider:
    return GmailAccessTokenProvider(
        token_store=get_token_store(),
        oauth_client=get_gmail_oauth_client(),
    )


@functools.lru_cache(maxsize=1)
def get_readers() -> dict[MailboxProvider, MailboxReader]:
    settings = get_ingestion_settings()
    readers: dict[MailboxProvider, MailboxReader] = {}

    if get_aws_settings().is_local:
        # The hosted stand-in exists to exercise the pipeline; it has no place
        # in a real deployment, where every mailbox belongs to someone.
        readers[MailboxProvider.SIMULATED] = SimulatedMailboxReader(
            client=get_dynamodb_client(),
            table_name=settings.simulated_mailbox_table,
        )

    if settings.gmail_configured:
        readers[MailboxProvider.GMAIL] = GmailMailboxReader(
            api=GmailApiClient(),
            token_provider=_gmail_token_provider(),
        )

    return readers


@functools.lru_cache(maxsize=1)
def get_subscribers() -> dict[MailboxProvider, MailboxSubscriber]:
    settings = get_ingestion_settings()
    subscribers: dict[MailboxProvider, MailboxSubscriber] = {}

    if get_aws_settings().is_local:
        subscribers[MailboxProvider.SIMULATED] = SimulatedMailboxSubscriber()

    if settings.gmail_configured:
        subscribers[MailboxProvider.GMAIL] = GmailMailboxSubscriber(
            api=GmailApiClient(),
            token_provider=_gmail_token_provider(),
            topic_name=settings.gmail_pubsub_topic,
        )

    return subscribers


def configured_providers() -> tuple[str, ...]:
    return tuple(sorted(provider.value for provider in get_readers()))
