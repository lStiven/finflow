from __future__ import annotations

import functools

import boto3
from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_events.client import EventBridgeClient
from mypy_boto3_sqs.client import SQSClient

from personal_finance.shared.infrastructure.config.settings import get_aws_settings


@functools.lru_cache(maxsize=1)
def get_session() -> boto3.Session:
    return boto3.Session(region_name=get_aws_settings().region)


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
