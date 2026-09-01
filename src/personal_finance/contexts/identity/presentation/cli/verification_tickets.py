"""Getting an account created by a script, now that addresses have to answer.

`POST /identity/register` spends a ticket that `POST
/identity/verification/confirm` only hands out to whoever read the code mailed
to the address. That is the point of it, and it is also why the repository's
own scripts cannot go through the front door: `seed_local.py` invents
addresses, and `smoke.py`'s canary lives at `@finflow.local`, which no mail
server will ever deliver to.

So they take the operator path instead: write a ready ticket straight into the
challenges table, then spend it through the real endpoint. That is not a back
door — it needs DynamoDB write access, which is the privilege whoever runs
these scripts already holds, and it is reachable over no HTTP surface at all.
The registration itself still goes through the same use case, the same
conditional consume and the same uniqueness check as anybody else's.

The challenge written here carries an already-expired code, so what lands in
the table is a ticket and never a guessable code.
"""

from __future__ import annotations

from personal_finance.contexts.identity.domain.credentials import EmailVerification
from personal_finance.contexts.identity.domain.value_objects import Email, SecretHash
from personal_finance.contexts.identity.infrastructure.persistence.credentials_dynamodb import (  # noqa: E501
    DynamoDBEmailVerificationRepository,
)
from personal_finance.contexts.identity.infrastructure.security.secret_generator import (  # noqa: E501
    SecretsSecretGenerator,
)
from personal_finance.contexts.identity.infrastructure.security.secret_hashing import (
    Sha256SecretHasher,
)
from personal_finance.shared.domain.value_objects import PosixTime
from personal_finance.shared.infrastructure.aws.session import get_dynamodb_client
from personal_finance.shared.infrastructure.config.settings import get_identity_settings


# Nothing can hash to this, so the code half of the challenge is dead on
# arrival and only the ticket is usable.
_UNUSABLE_CODE_HASH = SecretHash("issued-by-a-script-no-code-was-sent")


def issue_registration_ticket(email: str) -> str:
    """Write a ready-to-spend verification ticket and return its token."""
    settings = get_identity_settings()
    repository = DynamoDBEmailVerificationRepository(
        client=get_dynamodb_client(),
        table_name=settings.challenges_table,
    )
    token = SecretsSecretGenerator().opaque_token()
    now = PosixTime.now()

    verification = EmailVerification.issue(
        email=Email(email),
        code_hash=_UNUSABLE_CODE_HASH,
        now=now,
        code_ttl_minutes=0,
        window_minutes=settings.delivery_window_minutes,
    )
    verification.accept(
        ticket_hash=Sha256SecretHasher().hash(token),
        now=now,
        ticket_ttl_minutes=settings.registration_ticket_ttl_minutes,
    )

    existing = repository.find(Email(email))

    if not repository.save(
        verification,
        expected=existing.state if existing is not None else None,
    ):
        raise RuntimeError(
            f"Could not write a verification ticket for {email}: something "
            "else is using that address' challenge right now.",
        )

    return token
