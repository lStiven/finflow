"""Prints what ingestion currently holds: inboxes, notifications and queues.

    just inspect                 # local emulator
    just inspect .env.production # real account

A development tool. It scans the tables, which is fine for the volumes a
personal account sees and wrong for anything larger.
"""

from __future__ import annotations

import argparse
from collections import Counter

from mypy_boto3_dynamodb.type_defs import AttributeValueTypeDef

from personal_finance.contexts.ingestion.infrastructure.persistence.user_inbox_dynamodb import (  # noqa: E501
    ALLOWED_ADDRESSES,
    ALLOWED_DOMAINS,
    INBOX_PARTITION_KEY,
)
from personal_finance.shared.infrastructure.aws.provisioning import (
    DEAD_LETTER_SUFFIX,
)
from personal_finance.shared.infrastructure.aws.session import (
    get_dynamodb_client,
    get_sqs_client,
)
from personal_finance.shared.infrastructure.config.settings import (
    get_aws_settings,
    get_ingestion_settings,
)


Item = dict[str, AttributeValueTypeDef]


def _text(item: Item, key: str) -> str:
    return item.get(key, {}).get("S") or "-"


def _number(item: Item, key: str) -> int:
    raw = item.get(key, {}).get("N")

    return int(raw) if raw else 0


def _string_list(item: Item, key: str) -> list[str]:
    return [
        value
        for element in item.get(key, {}).get("L", [])
        if (value := element.get("S")) is not None
    ]


def _print_inboxes(table_name: str) -> None:
    items = get_dynamodb_client().scan(TableName=table_name).get("Items", [])

    print(f"\nInboxes ({len(items)})")

    if not items:
        print("  none — register one with `just register-inbox`")

    for item in items:
        senders = _string_list(item, ALLOWED_ADDRESSES) + _string_list(
            item,
            ALLOWED_DOMAINS,
        )
        print(f"  {_text(item, INBOX_PARTITION_KEY)}")
        print(f"    user     {_text(item, 'user_id')}")
        print(
            f"    trusts   {', '.join(senders) or '(nobody — everything is ignored)'}"
        )


def _print_notifications(table_name: str, *, limit: int) -> None:
    items = get_dynamodb_client().scan(TableName=table_name).get("Items", [])
    counts = Counter(_text(item, "status") for item in items)

    print(f"\nNotifications ({len(items)})")

    for status, count in sorted(counts.items()):
        print(f"  {status:<18} {count}")

    if not items:
        return

    recent = sorted(items, key=lambda item: _number(item, "received_at"), reverse=True)

    print(f"\n  most recent {min(limit, len(recent))}:")

    for item in recent[:limit]:
        body = item.get("raw_content", {}).get("S") or ""
        kept = f"{len(body)} chars" if body else "discarded"
        status = _text(item, "status")
        deferred_reason = item.get("deferred_reason", {}).get("S")
        # Only ever set alongside `pending_fallback` — spelled out here so
        # the state never has to be read as "still to be tried" when it
        # is not.
        if status == "pending_fallback" and deferred_reason:
            status = f"{status} ({deferred_reason})"
        print(f"    {status:<38} {_text(item, 'sender'):<52} body: {kept}")
        print(f"      {_text(item, 'message_id')}")


def _print_queues(queue_url: str) -> None:
    client = get_sqs_client()
    print("\nQueues")

    for label, url in (
        ("parse", queue_url),
        ("dead-letter", f"{queue_url}{DEAD_LETTER_SUFFIX}"),
    ):
        try:
            attributes = client.get_queue_attributes(
                QueueUrl=url,
                AttributeNames=[
                    "ApproximateNumberOfMessages",
                    "ApproximateNumberOfMessagesNotVisible",
                ],
            )["Attributes"]
        except client.exceptions.QueueDoesNotExist:
            print(f"  {label:<12} missing — run `just aws-provision`")
            continue

        available = attributes.get("ApproximateNumberOfMessages", "0")
        in_flight = attributes.get("ApproximateNumberOfMessagesNotVisible", "0")
        print(f"  {label:<12} {available} waiting, {in_flight} in flight")


def main() -> None:
    parser = argparse.ArgumentParser(description="Show ingestion state")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    aws_settings = get_aws_settings()
    settings = get_ingestion_settings()

    print(
        f"{aws_settings.environment.value} — "
        f"{aws_settings.endpoint_url or 'real AWS'} ({aws_settings.region})",
    )

    _print_inboxes(settings.user_inboxes_table)
    _print_notifications(settings.notifications_table, limit=args.limit)
    _print_queues(settings.parse_queue_url)

    print(
        "\nDomain events go to the log; the ones that are integration events "
        "also reach EventBridge. Read them with `just events`.",
    )


if __name__ == "__main__":
    main()
