"""What the suspension notice on screen is drawn from."""

from collections.abc import Iterator

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.contexts.ingestion.application.model_access import (
    GetModelAccessUseCase,
)
from personal_finance.contexts.ingestion.domain.model_standing import ModelStanding
from personal_finance.contexts.ingestion.presentation.http.model_access import (
    get_model_access_use_case,
    router,
)
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER = UserId.from_string("11111111-1111-1111-1111-111111111111")
PATH = "/ingestion/model-access"
FAR_AWAY = 4_102_444_800  # 2100


class Standings:
    def __init__(self) -> None:
        self.standing: ModelStanding | None = None
        self.asked_for: list[UserId] = []

    def get(self, user_id: UserId) -> ModelStanding:
        self.asked_for.append(user_id)

        return self.standing or ModelStanding.clean(user_id)

    def save(self, standing: ModelStanding) -> bool:  # pragma: no cover - unused
        del standing

        return True


@pytest.fixture
def standings() -> Standings:
    return Standings()


@pytest.fixture
def client(standings: Standings) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_model_access_use_case] = lambda: GetModelAccessUseCase(
        standings=standings
    )
    app.dependency_overrides[get_current_user_id] = lambda: USER

    yield TestClient(app)

    app.dependency_overrides.clear()


def test_somebody_never_suspended_is_open(client: TestClient) -> None:
    response = client.get(PATH)

    assert response.status_code == 200
    assert response.json() == {"access": "open", "paused_until": None, "strikes": 0}
    assert response.headers["cache-control"] == "no-store"


def test_a_pause_says_until_when(client: TestClient, standings: Standings) -> None:
    standing = ModelStanding.clean(USER)
    standing.paused_until = PosixTime.from_epoch_seconds(FAR_AWAY)
    standing.strikes = (PosixTime.now(),)
    standings.standing = standing

    assert client.get(PATH).json() == {
        "access": "paused",
        "paused_until": FAR_AWAY,
        "strikes": 1,
    }


def test_a_revocation_has_no_end(client: TestClient, standings: Standings) -> None:
    standing = ModelStanding.clean(USER)
    standing.revoked = True
    standings.standing = standing

    assert client.get(PATH).json()["access"] == "revoked"


def test_it_reads_only_the_callers_own_record(
    client: TestClient,
    standings: Standings,
) -> None:
    client.get(PATH)

    assert standings.asked_for == [USER]
