"""That `configure_logging` still wins when something configured logging first.

The case worth pinning down is Lambda's: the managed runtime attaches a
handler to the root logger before any of this package is imported, and a
`basicConfig` without `force=True` is documented to return silently when that
is true. The failure it produced was invisible rather than loud — every
worker's INFO line was dropped, so a healthy deployment and a dead one logged
the same nothing.
"""

from collections.abc import Iterator
import io
import logging

import pytest

from personal_finance.shared.infrastructure.observability.logging_config import (
    SILENCED_LOGGERS,
    configure_logging,
)


@pytest.fixture
def root_logger_configured_elsewhere() -> Iterator[None]:
    """Leave the root logger looking the way Lambda's runtime leaves it.

    Explicit rather than autouse, like the other resets in this suite: a test
    that mutates global logging state should say so in its signature. The
    restore matters as much as the setup — `configure_logging` is global by
    design, so without it the first test to run would decide how every later
    one logs.
    """
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level

    root.handlers[:] = [logging.StreamHandler(io.StringIO())]
    root.setLevel(logging.WARNING)
    try:
        yield
    finally:
        root.handlers[:] = handlers
        root.setLevel(level)


def _capture() -> io.StringIO:
    """Point the handler `configure_logging` installed at a buffer."""
    stream = io.StringIO()
    logging.getLogger().handlers[0].stream = stream  # type: ignore[attr-defined]

    return stream


def test_info_survives_a_root_logger_that_was_already_configured(
    root_logger_configured_elsewhere: None,
) -> None:
    configure_logging()
    stream = _capture()

    logging.getLogger("finflow.test").info("batch polled")

    assert "batch polled" in stream.getvalue()


def test_the_preexisting_handler_is_replaced_rather_than_added_to(
    root_logger_configured_elsewhere: None,
) -> None:
    """Two handlers would mean every line logged twice."""
    configure_logging()

    assert len(logging.getLogger().handlers) == 1


def test_extra_fields_survive_that_same_takeover(
    root_logger_configured_elsewhere: None,
) -> None:
    """The runtime's own formatter renders `%(message)s` and drops `extra`,
    so replacing it is the point rather than a side effect.
    """
    configure_logging()
    stream = _capture()

    logging.getLogger("finflow.test").info("batch polled", extra={"fetched": 3})

    assert "fetched=3" in stream.getvalue()


def test_the_requested_level_is_applied_over_the_existing_one(
    root_logger_configured_elsewhere: None,
) -> None:
    configure_logging(level=logging.DEBUG)

    assert logging.getLogger().level == logging.DEBUG


# ----------------------------------------------------------------------
# And that it turns down the libraries that would print a credential
# ----------------------------------------------------------------------


@pytest.fixture
def silenced_levels_restored() -> Iterator[None]:
    levels = {name: logging.getLogger(name).level for name in SILENCED_LOGGERS}
    try:
        yield
    finally:
        for name, level in levels.items():
            logging.getLogger(name).setLevel(level)


@pytest.mark.parametrize("name", SILENCED_LOGGERS)
def test_a_library_that_logs_request_urls_is_pinned_above_info(
    name: str,
    silenced_levels_restored: None,
) -> None:
    """`httpx` writes the request URL at INFO, and one URL here is a secret.

    Telegram's API carries the bot token in the path, so the line the alerts
    adapter takes such care never to write, httpx would write for it — on
    every alert. Found by running the thing and reading the log.
    """
    del silenced_levels_restored
    configure_logging()

    assert not logging.getLogger(name).isEnabledFor(logging.INFO)


@pytest.mark.parametrize("name", SILENCED_LOGGERS)
def test_turning_the_root_logger_up_does_not_unpin_them(
    name: str,
    silenced_levels_restored: None,
) -> None:
    """DEBUG is what somebody reaches for while chasing an unrelated bug."""
    del silenced_levels_restored
    configure_logging(level=logging.DEBUG)

    assert not logging.getLogger(name).isEnabledFor(logging.INFO)


def test_this_codebases_own_loggers_still_say_what_they_are_doing(
    silenced_levels_restored: None,
) -> None:
    del silenced_levels_restored
    configure_logging()

    assert logging.getLogger("personal_finance.anything").isEnabledFor(logging.INFO)
