"""A number that changes without a deploy: read from SSM, and read again once
its copy is old — but never replaced by a default nobody chose.
"""

import pytest

from personal_finance.shared.infrastructure.config.tunable import (
    TunableNumber,
    TunableValueError,
)


PARAMETER = "/finflow/production/llm-daily-calls-per-user"


class Parameters:
    """SSM as far as this needs it: one value, how often it was read, and an
    outage to switch on."""

    def __init__(self, value: str) -> None:
        self.value = value
        self.reads = 0
        self.down = False

    def __call__(self, name: str) -> str:
        assert name == PARAMETER
        self.reads += 1

        if self.down:
            raise ConnectionError("ssm is down")

        return self.value


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _tunable(parameters: Parameters, clock: Clock) -> TunableNumber:
    return TunableNumber(
        f"ssm:{PARAMETER}",
        max_age_seconds=300,
        read_parameter=parameters,
        clock=clock,
    )


def test_a_plain_value_is_the_number_and_never_touches_ssm() -> None:
    parameters = Parameters("99")

    assert TunableNumber(" 20 ", read_parameter=parameters).current() == 20
    assert parameters.reads == 0


@pytest.mark.parametrize("raw", ["veinte", "-1", "2.5", "", "ssm:", "²", "٢٠"])
def test_a_value_that_is_not_a_whole_number_stops_the_process(raw: str) -> None:
    with pytest.raises(TunableValueError):
        TunableNumber(raw)


def test_a_parameter_is_read_once_and_kept_while_fresh() -> None:
    parameters, clock = Parameters("20"), Clock()
    tunable = _tunable(parameters, clock)

    assert tunable.current() == 20
    clock.now += 299
    assert tunable.current() == 20
    assert parameters.reads == 1


def test_a_changed_parameter_is_seen_once_the_copy_is_stale() -> None:
    parameters, clock = Parameters("20"), Clock()
    tunable = _tunable(parameters, clock)
    tunable.current()

    parameters.value = "35"
    clock.now += 300

    assert tunable.current() == 35


def test_an_ssm_outage_keeps_the_last_good_value_for_another_window() -> None:
    parameters, clock = Parameters("20"), Clock()
    tunable = _tunable(parameters, clock)
    tunable.current()

    parameters.down = True
    clock.now += 300
    assert tunable.current() == 20
    # Not asked again on every read while it is down.
    clock.now += 10
    assert tunable.current() == 20
    assert parameters.reads == 2


def test_a_parameter_edited_into_nonsense_keeps_the_last_good_value() -> None:
    parameters, clock = Parameters("20"), Clock()
    tunable = _tunable(parameters, clock)
    tunable.current()

    parameters.value = "veinte"
    clock.now += 300

    assert tunable.current() == 20


def test_the_first_read_has_nothing_to_fall_back_on_and_raises() -> None:
    parameters = Parameters("20")
    parameters.down = True

    with pytest.raises(TunableValueError):
        _tunable(parameters, Clock()).current()
