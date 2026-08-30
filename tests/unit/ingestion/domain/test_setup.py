from personal_finance.contexts.ingestion.domain.entities import UserInbox
from personal_finance.contexts.ingestion.domain.policies import AuthorizedSenderPolicy
from personal_finance.contexts.ingestion.domain.setup import InboxSetup, SetupStep
from personal_finance.contexts.ingestion.domain.value_objects import EmailAddress
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER_ID = UserId.from_string("11111111-2222-3333-4444-555555555555")
ALIAS = EmailAddress("finflowingest+11111111222233334444555555555555@gmail.com")
BANK_DOMAIN = "an.notificacionesbancolombia.com"
CONFIRMED_AT = PosixTime.from_epoch_seconds(1_756_400_000)
FIRST_ALERT_AT = PosixTime.from_epoch_seconds(1_756_500_000)


def _inbox(
    *,
    domains: frozenset[str] = frozenset(),
    forwarding_confirmed_at: PosixTime | None = None,
    first_accepted_at: PosixTime | None = None,
) -> UserInbox:
    return UserInbox(
        user_id=USER_ID,
        address=ALIAS,
        sender_policy=AuthorizedSenderPolicy(allowed_domains=domains),
        forwarding_confirmed_at=forwarding_confirmed_at,
        first_accepted_at=first_accepted_at,
    )


def test_a_fresh_inbox_only_has_its_address() -> None:
    setup = InboxSetup.of(_inbox())

    assert setup.current is SetupStep.SENDERS_APPROVED
    assert setup.ready is False


def test_approving_a_sender_moves_on_to_the_forwarding_rule() -> None:
    setup = InboxSetup.of(_inbox(domains=frozenset({BANK_DOMAIN})))

    assert setup.current is SetupStep.FORWARDING_CONFIRMED


def test_a_confirmed_forwarding_rule_leaves_only_the_first_alert() -> None:
    setup = InboxSetup.of(
        _inbox(
            domains=frozenset({BANK_DOMAIN}),
            forwarding_confirmed_at=CONFIRMED_AT,
        ),
    )

    assert setup.current is SetupStep.FIRST_ALERT
    assert setup.ready is False


def test_the_first_alert_finishes_the_setup() -> None:
    setup = InboxSetup.of(
        _inbox(
            domains=frozenset({BANK_DOMAIN}),
            forwarding_confirmed_at=CONFIRMED_AT,
            first_accepted_at=FIRST_ALERT_AT,
        ),
    )

    assert setup.ready is True
    assert setup.current is None


def test_steps_close_out_of_order() -> None:
    """Gmail confirms before the user has approved anybody. Nothing about
    that is an error, and the confirmed step must not be re-opened by it.
    """
    setup = InboxSetup.of(_inbox(forwarding_confirmed_at=CONFIRMED_AT))
    states = {state.step: state for state in setup.steps}

    assert states[SetupStep.FORWARDING_CONFIRMED].done is True
    assert setup.current is SetupStep.SENDERS_APPROVED


def test_alerts_forwarded_by_hand_count_as_ready() -> None:
    """No confirmation will ever arrive for somebody who forwards each alert
    manually, and their expenses are landing all the same.
    """
    setup = InboxSetup.of(
        _inbox(domains=frozenset({BANK_DOMAIN}), first_accepted_at=FIRST_ALERT_AT),
    )

    assert setup.ready is True
    assert setup.current is None


def test_emptying_the_allow_list_stops_being_ready() -> None:
    setup = InboxSetup.of(
        _inbox(forwarding_confirmed_at=CONFIRMED_AT, first_accepted_at=FIRST_ALERT_AT),
    )

    assert setup.ready is False
    assert setup.current is SetupStep.SENDERS_APPROVED


def test_it_reports_when_each_milestone_happened() -> None:
    setup = InboxSetup.of(
        _inbox(
            domains=frozenset({BANK_DOMAIN}),
            forwarding_confirmed_at=CONFIRMED_AT,
            first_accepted_at=FIRST_ALERT_AT,
        ),
    )
    states = {state.step: state for state in setup.steps}

    assert states[SetupStep.FORWARDING_CONFIRMED].at == CONFIRMED_AT
    assert states[SetupStep.FIRST_ALERT].at == FIRST_ALERT_AT
    # States rather than events: nothing was recorded about when they became
    # true, and inventing a timestamp here would be the wrong kind of tidy.
    assert states[SetupStep.ADDRESS_ASSIGNED].at is None
    assert states[SetupStep.SENDERS_APPROVED].at is None
