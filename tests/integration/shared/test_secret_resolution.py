"""Reading a secret out of Parameter Store, against moto's real SSM API.

The convention is one prefix: `ssm:/path` is a reference, anything else is the
secret itself. That is what keeps local development on plain values while a
deployed environment holds nothing but references.
"""

from collections.abc import Iterator

from mypy_boto3_ssm.client import SSMClient
from pydantic import SecretStr
import pytest

from personal_finance.shared.infrastructure.aws import session
from personal_finance.shared.infrastructure.aws.session import (
    get_ssm_client,
    reset_session,
)
from personal_finance.shared.infrastructure.config.secrets import (
    SecretResolutionError,
    reset_secrets_cache,
    resolve,
)
from personal_finance.shared.infrastructure.config.settings import (
    AwsSettings,
    reset_settings,
)


PARAMETER = "/finflow/test/jwt-secret"
SECRET = "a-real-signing-secret"


@pytest.fixture
def ssm_client(aws: None, monkeypatch: pytest.MonkeyPatch) -> Iterator[SSMClient]:
    """The client the resolver itself will build, pointed at moto.

    `reset_settings` matters as much as the mock: `.env` names the local
    emulator's endpoint, and without dropping the cached settings the resolver
    would talk to that instead of to the mock this test set up.
    """
    del aws
    monkeypatch.setattr(
        session,
        "get_aws_settings",
        lambda: AwsSettings(endpoint_url=None, region="us-east-1"),
    )
    reset_settings()
    reset_session()
    reset_secrets_cache()

    yield get_ssm_client()

    reset_settings()
    reset_session()
    reset_secrets_cache()


def test_a_plain_value_is_the_secret_itself(ssm_client: SSMClient) -> None:
    del ssm_client

    assert resolve(SecretStr(SECRET)).get_secret_value() == SECRET


def test_a_reference_is_read_out_of_parameter_store(ssm_client: SSMClient) -> None:
    ssm_client.put_parameter(Name=PARAMETER, Value=SECRET, Type="SecureString")

    resolved = resolve(SecretStr(f"ssm:{PARAMETER}"))

    assert resolved.get_secret_value() == SECRET


def test_a_reference_to_nothing_stops_the_process(ssm_client: SSMClient) -> None:
    # Never an empty string: a missing signing secret has to stop startup, not
    # quietly downgrade it to one an attacker can guess.
    del ssm_client

    with pytest.raises(SecretResolutionError):
        resolve(SecretStr("ssm:/finflow/test/not-there"))


def test_a_reference_with_no_name_is_refused(ssm_client: SSMClient) -> None:
    del ssm_client

    with pytest.raises(SecretResolutionError):
        resolve(SecretStr("ssm:   "))


def test_a_resolved_secret_is_fetched_once(ssm_client: SSMClient) -> None:
    """Cached for the life of the process.

    Settings are read on every request that touches them, and a network call
    per read would turn a signing key into a latency budget. The trade is that
    a rotated secret needs a restart.
    """
    ssm_client.put_parameter(Name=PARAMETER, Value=SECRET, Type="SecureString")

    assert resolve(SecretStr(f"ssm:{PARAMETER}")).get_secret_value() == SECRET

    ssm_client.put_parameter(
        Name=PARAMETER,
        Value="rotated",
        Type="SecureString",
        Overwrite=True,
    )

    assert resolve(SecretStr(f"ssm:{PARAMETER}")).get_secret_value() == SECRET
