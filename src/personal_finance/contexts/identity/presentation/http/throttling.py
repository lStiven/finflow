"""What each door of this context allows, and why that number.

Every figure here is chosen against the same two questions, in this order:

1. **Could a real person hit it by accident?** If yes, it is wrong, however
   well it stops an attack. A limit that locks somebody out of their own
   money is not a security control — it is the outage an attacker wanted.
2. **Does it leave the attack worth running?** Five guesses per quarter hour
   against one address is 480 a day; a six-character password is millions.

The addresses are the risky dimension, because they are shared: a house, an
office, a carrier putting a whole city behind one NAT. Three things keep that
safe — **only refusals are counted**, the address budgets are several times
what a crowd of honest people could spend, and an address that cannot be
established is skipped rather than guessed at.

The limits in the *domain* are not repeated here. One address can already
only cause five mails an hour, and one code can only be guessed five times
before the challenge is spent: those are rules about an account and they live
with the account. What these add is the dimension the domain cannot see — the
same attacker moving across a thousand different addresses, each of which
looks perfectly innocent on its own.
"""

from __future__ import annotations

from personal_finance.shared.domain.throttling import RateLimit
from personal_finance.shared.presentation.throttling import Door


QUARTER_HOUR = 15 * 60
MINUTE = 60
FIVE_MINUTES = 5 * 60

#: Wrong passwords against **one account**. A lock: small budget, long
#: window, and checked *before* the password is verified — which is the only
#: way a limit stops guessing at all, since an attempt that gets verified is
#: an attempt that was allowed.
#:
#: Five per quarter hour is 480 a day against a real password, which is not a
#: search that finishes. The person it can inconvenience is the owner of the
#: account being attacked, who waits fifteen minutes or uses the reset link —
#: and that is the trade every account lockout makes, which is why the window
#: is fifteen minutes and not a day.
LOGIN = Door(
    name="login",
    # And a **brake**, not a lock. This is the dimension that can hurt
    # somebody who did nothing: an address is shared — a house, an office, a
    # carrier putting a whole city behind one NAT — so whatever it refuses,
    # it refuses to strangers too.
    #
    # A minute, deliberately. The first version of this was thirty failures
    # per quarter hour, and the browser suite caught what that means: after a
    # burst of wrong passwords, a **correct** one from the same address was
    # refused for fifteen minutes. Twenty a minute bounds an attacker to a
    # crawl — and bcrypt makes each of those attempts cost the server real
    # work, which is the other reason this is checked before verifying —
    # while the worst an innocent neighbour can wait is under a minute.
    address=RateLimit(attempts=20, window_seconds=MINUTE),
    subject=RateLimit(attempts=5, window_seconds=QUARTER_HOUR),
)

#: Creating accounts. Counted on every attempt, not only the failures: here
#: the account *is* the cost, so the attempt worth limiting is the one that
#: works. Ten per quarter hour is a family setting up together twice over; a
#: script filling the deployment with addresses wants thousands, and each of
#: those also needs a mail-verified ticket it cannot get any faster than the
#: door below allows.
REGISTRATION = Door(
    name="register",
    address=RateLimit(attempts=10, window_seconds=QUARTER_HOUR),
)

#: Asking this deployment to send mail — the verification code and the reset
#: link. One address is already capped at five mails an hour by the challenge
#: itself, so what this adds is the spray across *many* addresses from one
#: place, which is how a mailbox loses its sender reputation. Twenty a
#: quarter hour covers a household several times over and recovers fast
#: enough that a burst of abuse cannot keep a neighbour from registering.
MAIL = Door(name="mail", address=RateLimit(attempts=20, window_seconds=QUARTER_HOUR))

#: Guessing a six-digit code or a reset link. Each challenge is already spent
#: after five wrong answers, so this is somebody trying five against every
#: address they can think of. A brake like the login's, for the same reason:
#: the people it could inconvenience are whoever shares the address with the
#: guesser.
CHALLENGE = Door(
    name="challenge",
    address=RateLimit(attempts=30, window_seconds=FIVE_MINUTES),
)

#: The current password, asked for again before it can be changed. Anybody
#: reaching this door already holds a valid token, so the attack it stops is
#: narrow and real: a session left open on a shared machine, guessed at until
#: it becomes somebody else's account. Scoped to the account, because the
#: token already says which account it is; the address gets a brake for the
#: same reason every other door has one.
PASSWORD_CHANGE = Door(
    name="password-change",
    address=RateLimit(attempts=60, window_seconds=FIVE_MINUTES),
    subject=RateLimit(attempts=10, window_seconds=QUARTER_HOUR),
)
