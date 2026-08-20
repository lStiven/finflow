from __future__ import annotations

import functools

import boto3
from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_events.client import EventBridgeClient
from mypy_boto3_sqs.client import SQSClient

from personal_finance.shared.infrastructure.config.settings import (
    AwsSettings,
    get_aws_settings,
)


def _build_session(settings: AwsSettings) -> boto3.Session:
    """Resolve credentials in the order a deployment actually needs them.

    A named profile wins when one is configured (local development against a
    real account, usually via SSO). Explicit keys come next, which is how the
    emulator and CI are driven. With neither, boto3 falls back to its own
    provider chain — the path that matters in production, where credentials
    come from an instance or task role and no secret is ever configured.
    """
    if settings.profile:
        return boto3.Session(
            profile_name=settings.profile,
            region_name=settings.region,
        )

    return boto3.Session(
        region_name=settings.region,
        aws_access_key_id=settings.access_key_id,
        aws_secret_access_key=(
            settings.secret_access_key.get_secret_value()
            if settings.secret_access_key
            else None
        ),
        aws_session_token=(
            settings.session_token.get_secret_value()
            if settings.session_token
            else None
        ),
    )


@functools.lru_cache(maxsize=1)
def get_session() -> boto3.Session:
    return _build_session(get_aws_settings())


# `Session.client` is overloaded over every AWS service, but boto3-stubs only
# types the ones whose stub package is installed, so pyright sees the symbol as
# partially unknown. The overload actually selected below is fully typed, which
# is why each call site returns a concrete client type.
@functools.lru_cache(maxsize=1)
def get_dynamodb_client() -> DynamoDBClient:
    return get_session().client(  # pyright: ignore[reportUnknownMemberType]
        "dynamodb",
        endpoint_url=get_aws_settings().endpoint_url,
    )


@functools.lru_cache(maxsize=1)
def get_sqs_client() -> SQSClient:
    return get_session().client(  # pyright: ignore[reportUnknownMemberType]
        "sqs",
        endpoint_url=get_aws_settings().endpoint_url,
    )


@functools.lru_cache(maxsize=1)
def get_eventbridge_client() -> EventBridgeClient:
    return get_session().client(  # pyright: ignore[reportUnknownMemberType]
        "events",
        endpoint_url=get_aws_settings().endpoint_url,
    )


def reset_session() -> None:
    """Drop the cached session and clients."""
    get_session.cache_clear()
    get_dynamodb_client.cache_clear()
    get_sqs_client.cache_clear()
    get_eventbridge_client.cache_clear()
