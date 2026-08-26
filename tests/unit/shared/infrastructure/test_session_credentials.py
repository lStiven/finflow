"""Which credentials a session is built with, and when they must stay unpinned.

The interesting case is Lambda: it publishes its role's credentials as
ordinary environment variables, so settings read them like any other value.
Passing those to `boto3.Session` freezes them, and a warm execution
environment outlives them — a failure that only appears hours after a deploy
that looked healthy.
"""

from botocore.exceptions import ProfileNotFound
import pytest

from personal_finance.shared.infrastructure.aws.session import build_session
from personal_finance.shared.infrastructure.config.settings import AwsSettings


def _settings(**overrides: object) -> AwsSettings:
    values: dict[str, object] = {
        "region": "us-east-1",
        "access_key_id": "AKIAEXAMPLE",
        "secret_access_key": "shhh",
        "session_token": "temporary",
    }
    values.update(overrides)

    return AwsSettings.model_validate(values)


def test_explicit_keys_are_used_when_not_on_lambda(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The emulator and CI path: nothing else would authenticate there."""
    monkeypatch.delenv("AWS_LAMBDA_FUNCTION_NAME", raising=False)

    session = build_session(_settings())
    credentials = session.get_credentials()

    assert credentials is not None
    assert credentials.access_key == "AKIAEXAMPLE"


def test_lambda_credentials_are_left_to_boto3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On Lambda the session must resolve credentials itself, so that boto3
    can refresh them. Pinning the ones settings read at cold start is what
    would expire under a warm environment.
    """
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "finflow-IngestFunction")

    session = build_session(_settings(access_key_id="STALEKEY"))

    # Nothing from settings reached the session: what it resolves now comes
    # from boto3's own chain, which is the refreshable one.
    assert session.region_name == "us-east-1"

    credentials = session.get_credentials()

    assert credentials is None or credentials.access_key != "STALEKEY"


def test_a_profile_still_wins_on_lambda_if_one_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deliberate configuration is never overridden by runtime detection —
    the profile branch is checked first, and this pins that order.
    """
    monkeypatch.setenv("AWS_LAMBDA_FUNCTION_NAME", "finflow-ApiFunction")

    with pytest.raises(ProfileNotFound):
        # No such profile exists, which is exactly how we can tell the profile
        # branch was taken rather than the Lambda one.
        build_session(_settings(profile="a-profile-that-does-not-exist"))
