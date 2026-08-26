"""Run every process the app needs, in one terminal, under one Ctrl+C.

    just up        # local, against the emulator
    just up-dev    # development, against the dev- resources in real AWS

Five processes make Finflow work — the API and the four workers — and each one
is a loop that never returns. Started by hand that is five terminals, five
things to remember and five things to forget to stop. This starts them
together, tags every line with the service that wrote it, and takes them all
down on the first Ctrl+C.

It refuses to run against production on purpose. There the five are Lambda
functions AWS invokes on its own, and driving them from a laptop instead is
not a smaller version of that — it is a different thing wearing its name, one
that consumes production's queues and competes for its mailbox. The
`*-prod` recipes still exist for when that is genuinely what you want; asking
for it one process at a time is the point.

Not a process manager. No restarts, no health checks, no dependency ordering:
if a service exits, the whole stack comes down, because in development a
worker that died silently is worse than one that took the terminal with it.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import contextlib
import dataclasses
import os
import signal
import subprocess
import sys
import threading
import time
from types import FrameType

from personal_finance.shared.infrastructure.config.settings import (
    Environment,
    get_aws_settings,
)


# ANSI colours, one per service, so a glance at the left edge is enough to
# tell whose line this is. Disabled when stdout is not a terminal.
RESET = "\033[0m"
DIM = "\033[2m"

# How long a service gets to stop politely before it is killed. It has to
# exceed the SQS long poll: all three workers poll with WaitTimeSeconds=20 and
# deliberately finish the batch in flight rather than drop it, so a shorter
# grace kills exactly what that design protects.
GRACE_SECONDS = 25.0

DEFAULT_API_PORT = 8000


@dataclasses.dataclass(frozen=True)
class Service:
    name: str
    colour: str
    command: Sequence[str]


def _worker(module: str) -> Sequence[str]:
    return ("uv", "run", "python", "-m", module)


def _services(*, reload: bool, ingest: bool, port: int) -> Sequence[Service]:
    api = (
        (
            "uv",
            "run",
            "fastapi",
            "dev",
            "src/personal_finance/api/main.py",
            "--port",
            str(port),
        )
        if reload
        else (
            "uv",
            "run",
            "uvicorn",
            "personal_finance.api.main:app",
            "--host",
            "0.0.0.0",
            "--port",
            str(port),
        )
    )

    poller = Service(
        "ingest",
        "\033[35m",
        _worker(
            "personal_finance.contexts.ingestion.presentation.cli.run_ingest_worker"
        ),
    )

    return (
        Service("api", "\033[36m", api),
        *((poller,) if ingest else ()),
        Service(
            "parse",
            "\033[33m",
            _worker(
                "personal_finance.contexts.ingestion.presentation.cli.run_parse_worker"
            ),
        ),
        Service(
            "merchant",
            "\033[32m",
            _worker(
                "personal_finance.contexts.merchant.presentation.cli.run_merchant_worker"
            ),
        ),
        Service(
            "financial",
            "\033[34m",
            _worker(
                "personal_finance.contexts.financial.presentation.cli"
                ".run_financial_worker"
            ),
        ),
    )


class Stack:
    """The running processes, and the one way they are brought down."""

    def __init__(self, services: Sequence[Service], *, colour: bool) -> None:
        self._services = services
        self._colour = colour
        self._width = max(len(service.name) for service in services)
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._readers: list[threading.Thread] = []
        self._lock = threading.Lock()
        # print() is not atomic across threads, and five services writing at
        # once splices one line into another.
        self._output = threading.Lock()
        self._stopping = threading.Event()
        self._impatient = threading.Event()
        self._first_exit: str | None = None

    def _tag(self, service: Service) -> str:
        label = service.name.ljust(self._width)

        return f"{service.colour}{label}{RESET} | " if self._colour else f"{label} | "

    def _say(self, line: str) -> None:
        with self._output:
            print(line, flush=True)

    def _note(self, message: str) -> None:
        label = "stack".ljust(self._width)
        prefix = f"{DIM}{label}{RESET}" if self._colour else label
        self._say(f"{prefix} | {message}")

    def _pump(self, service: Service, process: subprocess.Popen[str]) -> None:
        """Forward one service's output, a line at a time, tagged."""
        tag = self._tag(service)
        stream = process.stdout

        if stream is None:
            return

        for line in stream:
            self._say(f"{tag}{line.rstrip()}")

        process.wait()

        with self._lock:
            if self._first_exit is None and not self._stopping.is_set():
                self._first_exit = service.name

        # One service down means the stack is no longer what was asked for.
        self._stopping.set()

    def start(self) -> None:
        for service in self._services:
            process = subprocess.Popen(
                service.command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                # Its own process group, so a child that spawns children of
                # its own — uvicorn's reloader does — can be signalled whole.
                start_new_session=True,
            )
            self._processes[service.name] = process

            reader = threading.Thread(
                target=self._pump,
                args=(service, process),
                daemon=True,
            )
            reader.start()
            self._readers.append(reader)

            self._note(f"started {service.name} (pid {process.pid})")

    def request_stop(self) -> None:
        """Ask the stack to come down. Safe to call from a signal handler.

        A second request stops asking: the workers finish the poll in flight,
        which is up to GRACE_SECONDS of a terminal that looks hung, and
        somebody who presses Ctrl+C again means it. Without this they reach
        for `kill -9` on the supervisor, and every child is in its own session
        — five orphans holding a port and draining queues.
        """
        if self._stopping.is_set():
            self._impatient.set()

        self._stopping.set()

    def stop(self) -> None:
        self._stopping.set()

        for name, process in self._processes.items():
            if process.poll() is not None:
                continue

            self._note(f"stopping {name}")
            self._signal(process, signal.SIGTERM)

        self._note(
            f"waiting up to {GRACE_SECONDS:.0f}s for the poll in flight to "
            f"finish — Ctrl+C again to kill now"
        )
        deadline = time.monotonic() + GRACE_SECONDS

        for name, process in self._processes.items():
            while process.poll() is None and not self._impatient.is_set():
                if time.monotonic() >= deadline:
                    break

                # Short waits rather than one long one, so a second Ctrl+C is
                # noticed while this is still counting down.
                with contextlib.suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=0.2)

            if process.poll() is not None:
                continue

            reason = (
                "asked twice" if self._impatient.is_set() else "did not stop in time"
            )
            self._note(f"{name} {reason} — killing it")
            self._signal(process, signal.SIGKILL)

    def _signal(self, process: subprocess.Popen[str], number: int) -> None:
        # It may have stopped between the poll above and this call.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(process.pid), number)

    def wait(self) -> int:
        """Block until a service exits or a signal arrives. Returns exit code."""
        self._stopping.wait()

        if self._first_exit is not None:
            self._note(f"{self._first_exit} exited — bringing the rest down")

        self.stop()

        return 1 if self._first_exit is not None else 0


def _environment_or_refuse() -> Environment:
    environment = get_aws_settings().environment

    if environment is Environment.PRODUCTION:
        print(
            "Refusing to run the whole stack against production.\n"
            "\n"
            "In production these five are Lambda functions AWS invokes itself.\n"
            "Running them from here would consume production's queues and\n"
            "compete for its mailbox. If that is really what you want, start\n"
            "them one at a time with the *-prod recipes.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    return environment


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the whole stack")
    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Serve the API without hot reload (the default off local).",
    )
    # Tri-state on purpose: the default depends on the environment, and both
    # overrides have to be spellable.
    poller = parser.add_mutually_exclusive_group()
    poller.add_argument(
        "--with-ingest",
        dest="ingest",
        action="store_true",
        default=None,
        help="Poll the mailbox even locally. See the warning below.",
    )
    poller.add_argument(
        "--without-ingest",
        dest="ingest",
        action="store_false",
        default=None,
        help="Do not poll the mailbox.",
    )
    # Two stacks on one machine is a normal thing to want — local against the
    # emulator while development runs against real AWS — and they cannot share
    # a port.
    parser.add_argument(
        "--api-port",
        type=int,
        default=DEFAULT_API_PORT,
        help=f"Port for the API (default {DEFAULT_API_PORT}).",
    )
    arguments = parser.parse_args()

    environment = _environment_or_refuse()
    reload = environment is Environment.LOCAL and not arguments.no_reload
    colour = sys.stdout.isatty()

    # Off by default locally, and this is the one default here worth arguing
    # for. The mailbox is shared across environments and the poller marks what
    # it reads as seen, so a local run against an in-memory emulator would
    # quietly consume the mail development was going to process — a loss that
    # reports nothing anywhere. Locally the intake seam is the webhook the API
    # mounts for exactly this, so nothing is missing.
    ingest = (
        arguments.ingest
        if arguments.ingest is not None
        else environment is not Environment.LOCAL
    )

    services = _services(reload=reload, ingest=ingest, port=arguments.api_port)
    stack = Stack(services, colour=colour)

    def _handle(number: int, frame: FrameType | None) -> None:
        stack.request_stop()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    prefix = environment.resource_prefix or "(no prefix)"
    print(
        f"{environment.value} — resources {prefix} — "
        f"{len(services)} processes on port {arguments.api_port}, "
        f"Ctrl+C stops all",
        flush=True,
    )

    if not ingest:
        print(
            "ingest is not running: the mailbox is shared with the other "
            "environments and polling it here would consume their mail. "
            "POST /ingestion/bank-notifications is the local intake. "
            "Use --with-ingest to override.",
            flush=True,
        )

    try:
        stack.start()
        code = stack.wait()
    except BaseException:
        # A Popen that raises partway leaves the services before it running,
        # and they are in their own sessions: nothing else would reap them.
        stack.stop()
        raise

    raise SystemExit(code)


if __name__ == "__main__":
    main()
