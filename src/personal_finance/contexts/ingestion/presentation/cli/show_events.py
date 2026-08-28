"""Prints the integration events sitting on the bus tap.

    just events              # local emulator
    just events --follow     # keep polling until interrupted

Reads the queue that `provision_integration_event_subscription` attached to
the event bus. Messages are deleted as they are read, so each event is shown
once — this is a development tool, not a subscriber.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import signal
from types import FrameType
from typing import TYPE_CHECKING, Any, cast

from personal_finance.shared.infrastructure.aws.session import get_sqs_client
from personal_finance.shared.infrastructure.config.settings import (
    get_aws_settings,
    get_ingestion_settings,
)


if TYPE_CHECKING:
    from mypy_boto3_sqs.client import SQSClient


MAX_MESSAGES_PER_POLL = 10


class _Stopper:
    def __init__(self) -> None:
        self.requested = False

    def __call__(self, signum: int, frame: FrameType | None) -> None:
        del signum, frame
        self.requested = True


def _format_detail(detail: dict[str, Any], *, indent: str) -> str:
    return "\n".join(
        f"{indent}{key:<16} {json.dumps(value, ensure_ascii=False)}"
        for key, value in sorted(detail.items())
    )


def _print_envelope(body: str) -> None:
    try:
        envelope: dict[str, Any] = json.loads(body)
    except json.JSONDecodeError:
        print(f"  (unreadable message body: {body[:120]}...)")

        return

    raw: object = envelope.get("detail")
    detail = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    when = envelope.get("time", "-")

    print(f"\n  {envelope.get('detail-type', '?')}  <- {envelope.get('source', '?')}")
    print(f"    at             {when}")
    print(_format_detail(detail, indent="    "))


def _drain(client: SQSClient, queue_url: str, *, wait_seconds: int) -> int:
    response = client.receive_message(
        QueueUrl=queue_url,
        MaxNumberOfMessages=MAX_MESSAGES_PER_POLL,
        WaitTimeSeconds=wait_seconds,
    )
    messages = response.get("Messages", [])

    for message in messages:
        _print_envelope(message.get("Body", ""))
        receipt = message.get("ReceiptHandle")

        if receipt is not None:
            client.delete_message(QueueUrl=queue_url, ReceiptHandle=receipt)

    return len(messages)


def main() -> None:
    parser = argparse.ArgumentParser(description="Show integration events")
    parser.add_argument(
        "--follow",
        action="store_true",
        help="keep polling until interrupted",
    )
    args = parser.parse_args()

    aws_settings = get_aws_settings()
    settings = get_ingestion_settings()
    queue_url = settings.integration_events_queue_url

    if not queue_url:
        raise SystemExit(
            "INGESTION_INTEGRATION_EVENTS_QUEUE_URL is not set. Run "
            "`just aws-provision` and copy the URL it prints.",
        )

    print(
        f"{aws_settings.environment.value} — bus {settings.event_bus_name} "
        f"({aws_settings.endpoint_url or 'real AWS'})",
    )

    client = get_sqs_client()

    if not args.follow:
        seen = _drain(client, queue_url, wait_seconds=1)
        print(
            f"\n{seen} event(s). Nothing else is waiting." if seen else "\nNo events."
        )

        return

    stopper = _Stopper()
    signal.signal(signal.SIGINT, stopper)
    print("following — Ctrl-C to stop")

    while not stopper.requested:
        if _drain(client, queue_url, wait_seconds=5):
            print(f"    ---- {datetime.now(UTC).isoformat(timespec='seconds')}")


if __name__ == "__main__":
    main()
