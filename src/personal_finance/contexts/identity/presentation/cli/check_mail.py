"""Does this deployment's mailbox actually send?

The one question the application itself cannot answer honestly. Everything
else about the credential flow is checkable in a test — the codes, the
tickets, the conditional writes — but "the message left the building and
arrived" is not, and a deployment that cannot send is one where nobody can
register or recover an account.

So this walks the same four steps `SmtpCredentialNotifier` does and says which
one failed, because they fail for completely different reasons:

    1. resolve   — is the configuration even there? (`ssm:` refs are followed)
    2. connect   — is smtp.gmail.com:465 reachable from here?
    3. log in    — is the App Password right, and does it belong to *that*
                   address? A mismatched pair is the usual answer, and SMTP
                   reports it as one opaque `535`.
    4. send      — hand over a real message, to an address you can open.

Without a recipient it stops after step 3, which is the check worth running in
a hurry: it proves the credential without putting anything in anybody's inbox.

    just mail-check                      # configuration and login only
    just mail-check you@gmail.com        # …and one real message
    just mail-check you@gmail.com .env.production
"""

from __future__ import annotations

import argparse
import smtplib
import ssl
import sys

from personal_finance.contexts.identity.domain.value_objects import Email
from personal_finance.contexts.identity.infrastructure.email.smtp import (
    DEFAULT_TIMEOUT_SECONDS,
    MailDeliveryError,
    SmtpCredentialNotifier,
)
from personal_finance.shared.infrastructure.config.settings import (
    ENV_FILE,
    get_aws_settings,
    get_identity_settings,
)


# What a person would have received. Sending the real message rather than a
# "test" one is the point: a mail that arrives in spam, or with a broken link,
# has not really worked, and only the real one shows that.
SAMPLE_CODE = "424242"


def _step(label: str) -> None:
    print(f"  {label} ... ", end="", flush=True)


def _ok(detail: str = "") -> None:
    print("ok" + (f" — {detail}" if detail else ""), flush=True)


def _readable(reply: bytes | str) -> str:
    """`smtplib` types its reply as `bytes`, and sends `str` often enough."""
    if isinstance(reply, bytes):
        return reply.decode("utf-8", "replace").strip()

    return reply.strip()


def _failed(explanation: str, *, fix: str) -> None:
    print("FAILED", flush=True)
    print(f"\n  {explanation}\n\n  Fix: {fix}\n", file=sys.stderr)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "recipient",
        nargs="?",
        help=(
            "Where to send one real message. Use an address you can open. "
            "Omit it to check the configuration and the login only."
        ),
    )
    arguments = parser.parse_args()

    settings = get_identity_settings()
    environment = get_aws_settings().environment.value
    print(f"Checking identity mail for {ENV_FILE} ({environment})\n")

    # --- 1. resolve ---------------------------------------------------
    _step("configuration")

    if not settings.mail_configured:
        _failed(
            "IDENTITY_MAIL_FROM_ADDRESS and IDENTITY_MAIL_APP_PASSWORD are "
            "not both set, so nothing would be sent."
            + (
                "\n  On ENVIRONMENT=local that is fine: the code goes to the "
                "log and\n  comes back in the response instead. There is "
                "nothing to check here."
                if environment == "local"
                else "\n  Outside local this also stops the API from starting."
            ),
            fix=(
                "set both in the env file, or put the App Password in "
                "Parameter Store\n       with `just secret-put "
                f"/finflow/{environment}/mail-app-password` and reference it."
            ),
        )
        raise SystemExit(1)

    _ok(f"{settings.mail_login} -> {settings.mail_host}:{settings.mail_port}")

    if settings.mail_login != settings.mail_from_address:
        print(
            f"    note: logging in as {settings.mail_login} but sending as "
            f"{settings.mail_from_address}. Gmail refuses that unless the "
            "second is a verified alias of the first.",
        )

    # --- 2 and 3. connect and log in ----------------------------------
    _step("connect and log in")

    try:
        with smtplib.SMTP_SSL(
            settings.mail_host,
            settings.mail_port,
            timeout=DEFAULT_TIMEOUT_SECONDS,
        ) as server:
            server.login(
                settings.mail_login,
                settings.mail_app_password.get_secret_value(),
            )
    except smtplib.SMTPAuthenticationError as error:
        _failed(
            f"The mail server refused the credentials: {error.smtp_code} "
            f"{_readable(error.smtp_error)}\n"
            "  Almost always one of two things: the App Password belongs to a "
            "different\n  Gmail account than IDENTITY_MAIL_FROM_ADDRESS, or "
            "it was revoked.",
            fix=(
                "check that the address and the App Password are from the "
                "same account.\n       Generate a new one at "
                "https://myaccount.google.com/apppasswords\n       (it needs "
                "2-step verification turned on)."
            ),
        )
        raise SystemExit(1) from error
    except (TimeoutError, OSError, ssl.SSLError) as error:
        _failed(
            f"Could not reach {settings.mail_host}:{settings.mail_port} — "
            f"{error}\n  Nothing was sent; this is the network, not the "
            "credentials.",
            fix=(
                "check outbound access on that port. A Lambda inside a VPC "
                "with no NAT\n       cannot reach it either — these functions "
                "are deliberately not in one."
            ),
        )
        raise SystemExit(1) from error

    _ok("the App Password is accepted")

    if arguments.recipient is None:
        print(
            "\nConfiguration and credentials are good. Nothing was sent — "
            "pass an address\nyou can open to have one real message "
            "delivered:\n\n    just mail-check you@example.com\n",
        )
        return

    # --- 4. send ------------------------------------------------------
    _step(f"send to {arguments.recipient}")

    notifier = SmtpCredentialNotifier(
        host=settings.mail_host,
        port=settings.mail_port,
        username=settings.mail_login,
        password=settings.mail_app_password.get_secret_value(),
        from_address=settings.mail_from_address,
        from_name=settings.mail_from_name,
    )

    try:
        recipient = Email(arguments.recipient)
    except ValueError as error:
        _failed(
            f"{arguments.recipient!r} is not an email address.", fix="type it again."
        )
        raise SystemExit(1) from error

    try:
        notifier.send_verification_code(
            email=recipient,
            code=SAMPLE_CODE,
            expires_in_minutes=settings.verification_code_ttl_minutes,
        )
    except MailDeliveryError as error:
        _failed(
            f"The message was refused after login: {error}",
            fix=(
                "read the traceback above. A recipient the server will not "
                "accept and a\n       rate limit both land here."
            ),
        )
        raise SystemExit(1) from error

    _ok()
    print(
        f"\nSent. Open {arguments.recipient} — it should hold a message from "
        f"{settings.mail_from_name}\nwith the code {SAMPLE_CODE} in the "
        "subject.\n\n"
        "What to look at, since arriving is not the same as working:\n"
        "  * Did it land in spam? A private Gmail account sending to "
        "strangers often\n    does at first. That is deliverability, not a "
        "bug, and it is the reason to\n    check with a real address rather "
        "than assume.\n"
        "  * Is the code readable on a phone?\n"
        f"  * Reset links point at {settings.password_reset_url or '(unset)'} "
        "— open it and\n    make sure that page exists.\n",
    )


if __name__ == "__main__":
    main()
