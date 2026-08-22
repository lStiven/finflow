"""The Gmail calls this project makes.

`watch` asks Google to keep telling us when the mailbox changes, `history`
says what changed since a position, `messages.get` fetches one message, and
`messages.list` searches directly — the one exception to "only ever read
forward", used solely for a one-time, user-requested backfill. Nothing else:
the narrower the surface, the less of someone's mailbox this code is even
capable of touching.
"""

from __future__ import annotations

import dataclasses
from typing import Any

import httpx

from personal_finance.contexts.ingestion.application.mailbox import (
    MailboxAccessRevokedError,
    MailboxTemporarilyUnavailableError,
)
from personal_finance.shared.infrastructure.serialization import (
    as_json_array,
    as_json_object,
    read_string,
)


BASE_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
DEFAULT_TIMEOUT_SECONDS = 30.0

# Gmail keeps history records for about a week. Past that it answers 404 to a
# history id it no longer holds, and the only way forward is a bounded
# re-read rather than resuming.
HISTORY_EXPIRED_STATUS = 404


class GmailHistoryExpiredError(Exception):
    """The stored position is older than Gmail's history window.

    Not an authorization problem: the grant is fine, we simply cannot resume
    from where we stopped and must fall back to a bounded catch-up.
    """


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class WatchResponse:
    history_id: str
    # Google returns milliseconds since the epoch, as a string.
    expiration_epoch_millis: int


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class HistoryPage:
    message_ids: tuple[str, ...]
    history_id: str | None
    next_page_token: str | None


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class RawMessage:
    id: str
    raw: str
    internal_date_epoch_millis: int


@dataclasses.dataclass(frozen=True, slots=True, kw_only=True)
class MessagesPage:
    message_ids: tuple[str, ...]
    next_page_token: str | None


class GmailApiClient:
    """Thin, typed wrapper over the REST endpoints.

    Takes an access token per call rather than holding one: refreshing is the
    token store's job, and a client caching a token would quietly serve an
    expired one.
    """

    def __init__(self, *, transport: httpx.Client | None = None) -> None:
        self._transport = transport

    def watch(
        self,
        *,
        access_token: str,
        topic_name: str,
        label_ids: tuple[str, ...] = ("INBOX",),
    ) -> WatchResponse:
        """Subscribe, or renew. Google treats a repeat call as a renewal."""
        payload = self._request(
            "POST",
            "/watch",
            access_token=access_token,
            json={
                "topicName": topic_name,
                "labelIds": list(label_ids),
                "labelFilterBehavior": "INCLUDE",
            },
        )
        history_id = read_string(payload, "historyId")
        expiration = payload.get("expiration")

        if history_id is None or expiration is None:
            raise MailboxTemporarilyUnavailableError(
                "Gmail watch returned an unusable response",
            )

        return WatchResponse(
            history_id=history_id,
            expiration_epoch_millis=int(expiration),
        )

    def stop(self, *, access_token: str) -> None:
        self._request("POST", "/stop", access_token=access_token, json={})

    def list_history(
        self,
        *,
        access_token: str,
        start_history_id: str,
        page_token: str | None = None,
    ) -> HistoryPage:
        """What arrived since `start_history_id`.

        Restricted to `messageAdded`: a message being read, labelled or
        deleted is none of this application's business.
        """
        params: dict[str, str] = {
            "startHistoryId": start_history_id,
            "historyTypes": "messageAdded",
            "labelId": "INBOX",
        }

        if page_token is not None:
            params["pageToken"] = page_token

        payload = self._request(
            "GET",
            "/history",
            access_token=access_token,
            params=params,
        )

        return HistoryPage(
            message_ids=_message_ids(payload),
            history_id=read_string(payload, "historyId"),
            next_page_token=read_string(payload, "nextPageToken"),
        )

    def list_messages(
        self,
        *,
        access_token: str,
        query: str,
        page_token: str | None = None,
    ) -> MessagesPage:
        """Search the mailbox directly, by Gmail's own query syntax.

        Only ever used for a bounded, one-time backfill: the history window
        (`list_history`) does not reach back a whole calendar month, so this
        is the one place this project searches instead of resuming from a
        position.
        """
        params: dict[str, str] = {"q": query}

        if page_token is not None:
            params["pageToken"] = page_token

        payload = self._request(
            "GET",
            "/messages",
            access_token=access_token,
            params=params,
        )

        return MessagesPage(
            message_ids=tuple(
                message_id
                for entry in as_json_array(payload.get("messages"))
                if (message_id := read_string(as_json_object(entry), "id")) is not None
            ),
            next_page_token=read_string(payload, "nextPageToken"),
        )

    def get_raw_message(self, *, access_token: str, message_id: str) -> RawMessage:
        payload = self._request(
            "GET",
            f"/messages/{message_id}",
            access_token=access_token,
            params={"format": "raw"},
        )
        raw = read_string(payload, "raw")

        if raw is None:
            raise MailboxTemporarilyUnavailableError(
                f"Gmail returned no body for message {message_id}",
            )

        internal_date = payload.get("internalDate")

        return RawMessage(
            id=message_id,
            raw=raw,
            internal_date_epoch_millis=(
                int(internal_date) if internal_date is not None else 0
            ),
        )

    def _client(self) -> httpx.Client:
        if self._transport is not None:
            return self._transport

        return httpx.Client(timeout=DEFAULT_TIMEOUT_SECONDS)

    def _request(
        self,
        method: str,
        path: str,
        *,
        access_token: str,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            with self._client() as client:
                response = client.request(
                    method,
                    f"{BASE_URL}{path}",
                    headers={"Authorization": f"Bearer {access_token}"},
                    params=params,
                    json=json,
                )
        except httpx.HTTPError as error:
            raise MailboxTemporarilyUnavailableError(str(error)) from error

        if response.status_code in {401, 403}:
            # The grant is gone. Retrying a revoked token is how an
            # application gets rate-limited for nothing.
            raise MailboxAccessRevokedError(
                f"Gmail refused the token ({response.status_code})",
            )

        if response.status_code == HISTORY_EXPIRED_STATUS and path == "/history":
            raise GmailHistoryExpiredError(
                "Gmail no longer holds history from that position",
            )

        if response.status_code >= 400:
            raise MailboxTemporarilyUnavailableError(
                f"Gmail {path} failed with {response.status_code}",
            )

        return as_json_object(response.json() if response.content else {})


def _message_ids(payload: dict[str, Any]) -> tuple[str, ...]:
    """Pull the added-message ids out of a history page.

    Gmail nests them two levels deep and repeats a message once per label
    event, so the order is preserved but duplicates are dropped.
    """
    seen: dict[str, None] = {}

    for record in as_json_array(payload.get("history")):
        added = as_json_array(as_json_object(record).get("messagesAdded"))

        for entry in added:
            message = as_json_object(as_json_object(entry).get("message"))
            message_id = read_string(message, "id")

            if message_id is not None:
                seen[message_id] = None

    return tuple(seen)
