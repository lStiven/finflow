from collections.abc import Sequence

from personal_finance.contexts.identity.application.mailbox_handlers import (
    ConnectMailboxCommand,
    ConnectMailboxUseCase,
)
from personal_finance.contexts.identity.application.ports import (
    ConnectedMailbox,
    InboxRegistration,
    MailboxBackfillSummary,
    MailboxRefreshSummary,
)
from personal_finance.shared.domain.value_objects import UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
ADDRESS = "ana@gmail.test"


class RecordingInboxRegistrar:
    def __init__(self) -> None:
        self.calls: list[tuple[UserId, tuple[InboxRegistration, ...]]] = []

    def register(
        self,
        *,
        user_id: UserId,
        inboxes: Sequence[InboxRegistration],
    ) -> None:
        self.calls.append((user_id, tuple(inboxes)))


class RecordingConnector:
    def __init__(self, *, order: list[str] | None = None) -> None:
        self.connected: list[tuple[UserId, str, str]] = []
        self._order = order

    def connect(self, *, user_id: UserId, address: str, provider: str) -> None:
        if self._order is not None:
            self._order.append("connect")

        self.connected.append((user_id, address, provider))

    def disconnect(self, *, user_id: UserId, address: str, provider: str) -> None:
        raise NotImplementedError

    def list_for_user(self, user_id: UserId) -> Sequence[ConnectedMailbox]:
        raise NotImplementedError

    def refresh_for_user(self, user_id: UserId) -> MailboxRefreshSummary:
        raise NotImplementedError

    def backfill_current_month_for_user(
        self,
        user_id: UserId,
    ) -> MailboxBackfillSummary:
        raise NotImplementedError


class OrderedRegistrar(RecordingInboxRegistrar):
    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self._order = order

    def register(
        self,
        *,
        user_id: UserId,
        inboxes: Sequence[InboxRegistration],
    ) -> None:
        self._order.append("register")
        super().register(user_id=user_id, inboxes=inboxes)


def _command(
    *,
    domains: frozenset[str] = frozenset({"bancolombia.com.co"}),
    addresses: frozenset[str] = frozenset(),
) -> ConnectMailboxCommand:
    return ConnectMailboxCommand(
        address=ADDRESS,
        provider="simulated",
        allowed_domains=domains,
        allowed_addresses=addresses,
    )


def test_connecting_records_both_the_connection_and_the_senders() -> None:
    registrar = RecordingInboxRegistrar()
    connector = RecordingConnector()
    use_case = ConnectMailboxUseCase(
        connector=connector,
        inbox_registrar=registrar,
    )

    use_case.execute(user_id=USER_ID, command=_command())

    # One user action, so a user cannot end up connected with no filter and
    # wonder why no mail arrives.
    assert connector.connected == [(USER_ID, ADDRESS, "simulated")]
    assert len(registrar.calls) == 1
    _, inboxes = registrar.calls[0]
    assert inboxes[0].allowed_domains == frozenset({"bancolombia.com.co"})


def test_the_sender_filter_is_in_place_before_the_connection_goes_active() -> None:
    order: list[str] = []
    use_case = ConnectMailboxUseCase(
        connector=RecordingConnector(order=order),
        inbox_registrar=OrderedRegistrar(order),
    )

    use_case.execute(user_id=USER_ID, command=_command())

    assert order == ["register", "connect"]


def test_both_records_belong_to_the_authenticated_user() -> None:
    registrar = RecordingInboxRegistrar()
    connector = RecordingConnector()
    use_case = ConnectMailboxUseCase(
        connector=connector,
        inbox_registrar=registrar,
    )

    use_case.execute(user_id=USER_ID, command=_command())

    assert connector.connected[0][0] == USER_ID
    assert registrar.calls[0][0] == USER_ID


def test_connecting_without_senders_is_allowed_but_filters_nothing_in() -> None:
    registrar = RecordingInboxRegistrar()
    use_case = ConnectMailboxUseCase(
        connector=RecordingConnector(),
        inbox_registrar=registrar,
    )

    use_case.execute(user_id=USER_ID, command=_command(domains=frozenset()))

    # An empty allow-list is a normal state that reads nothing — the safe
    # default rather than a broken one.
    _, inboxes = registrar.calls[0]
    assert inboxes[0].allowed_domains == frozenset()
    assert inboxes[0].allowed_addresses == frozenset()
