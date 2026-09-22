from __future__ import annotations

import functools
import os
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config

from personal_finance.shared.infrastructure.config.settings import (
    AwsSettings,
    get_aws_settings,
)


if TYPE_CHECKING:
    from mypy_boto3_dynamodb.client import DynamoDBClient
    from mypy_boto3_events.client import EventBridgeClient
    from mypy_boto3_sqs.client import SQSClient
    from mypy_boto3_ssm.client import SSMClient


def _on_lambda() -> bool:
    """Whether this process is a Lambda invocation.

    Read from the real environment rather than from settings on purpose: this
    is a fact about the runtime, not a configuration choice, and an env file
    must not be able to claim it.
    """
    return bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME"))


def build_session(settings: AwsSettings) -> boto3.Session:
    """Resolve credentials in the order a deployment actually needs them.

    A named profile wins when one is configured (local development against a
    real account, usually via SSO). Explicit keys come next, which is how the
    emulator and CI are driven. With neither, boto3 falls back to its own
    provider chain — the path that matters in production, where credentials
    come from an instance or task role and no secret is ever configured.

    On Lambda that order has to be cut short. Lambda hands its role's
    credentials to the process as `AWS_ACCESS_KEY_ID` and friends, so settings
    read them like any other variable and would pin them into the session — as
    *static* strings, which boto3 then never refreshes. That is harmless for a
    process that outlives its credentials by seconds and fatal for one that
    does not: the ingest function is woken every minute, so its execution
    environment stays warm for hours and would start failing with
    `ExpiredTokenException` long after the deploy that looked fine. Handing
    boto3 nothing is what lets it build refreshable credentials instead.
    """
    if settings.profile:
        return boto3.Session(
            profile_name=settings.profile,
            region_name=settings.region,
        )

    if _on_lambda():
        return boto3.Session(region_name=settings.region)

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
    return build_session(get_aws_settings())


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
def get_throttling_dynamodb_client() -> DynamoDBClient:
    """The same table, on a much shorter leash.

    Its own client because the rate limiter is the one caller here that would
    rather be wrong than slow. Every other read in this app is the answer
    somebody asked for; this one only decides whether to *let them ask*, and
    it fails open. With boto3's defaults — sixty seconds to connect, sixty to
    read, several retries — a DynamoDB outage would not disable the limiter,
    it would hang the login screen for minutes while deciding to ignore it.

    Two seconds and one retry is enough to survive a dropped packet and short
    enough that nobody notices the day the table is gone.
    """
    return get_session().client(  # pyright: ignore[reportUnknownMemberType]
        "dynamodb",
        endpoint_url=get_aws_settings().endpoint_url,
        config=Config(
            connect_timeout=1,
            read_timeout=2,
            retries={"max_attempts": 2, "mode": "standard"},
        ),
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


@functools.lru_cache(maxsize=1)
def get_ssm_client() -> SSMClient:
    return get_session().client(  # pyright: ignore[reportUnknownMemberType]
        "ssm",
        endpoint_url=get_aws_settings().endpoint_url,
    )


def reset_session() -> None:
    """Drop the cached session and clients."""
    get_session.cache_clear()
    get_dynamodb_client.cache_clear()
    get_throttling_dynamodb_client.cache_clear()
    get_sqs_client.cache_clear()
    get_eventbridge_client.cache_clear()
    get_ssm_client.cache_clear()
