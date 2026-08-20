from collections.abc import Iterator

import boto3
from moto import mock_aws
from mypy_boto3_dynamodb.client import DynamoDBClient
from mypy_boto3_events.client import EventBridgeClient
from mypy_boto3_sqs.client import SQSClient
import pytest


REGION = "us-east-1"


@pytest.fixture
def aws(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Run the test against moto's in-process AWS.

    The credentials are dummies boto3 needs in order to sign, and the endpoint
    override is removed so moto — not a local emulator — answers the calls.
    """
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)

    with mock_aws():
        yield


# `boto3.client` is overloaded over every AWS service and boto3-stubs only
# types the installed ones, so pyright reports the symbol as partially unknown
# even though the selected overload is fully typed.
@pytest.fixture
def dynamodb_client(aws: None) -> DynamoDBClient:
    del aws

    return boto3.client("dynamodb", region_name=REGION)  # pyright: ignore[reportUnknownMemberType]


@pytest.fixture
def sqs_client(aws: None) -> SQSClient:
    del aws

    return boto3.client("sqs", region_name=REGION)  # pyright: ignore[reportUnknownMemberType]


@pytest.fixture
def eventbridge_client(aws: None) -> EventBridgeClient:
    del aws

    return boto3.client("events", region_name=REGION)  # pyright: ignore[reportUnknownMemberType]
