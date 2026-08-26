"""The surface a frontend calls, exercised end to end over fakes."""

from collections.abc import Mapping, Sequence
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.contexts.merchant.application.commands import (
    RecordSightingCommand,
)
from personal_finance.contexts.merchant.application.handlers import (
    ConfirmMerchantUseCase,
    EditMerchantUseCase,
    MergeMerchantsUseCase,
    MoveAliasUseCase,
    ResolveMerchantUseCase,
    SplitAliasUseCase,
)
from personal_finance.contexts.merchant.application.queries import (
    GetMerchantUseCase,
    ListMerchantsUseCase,
)
from personal_finance.contexts.merchant.domain.entities import Merchant
from personal_finance.contexts.merchant.domain.value_objects import (
    AliasFingerprint,
    CounterpartyKind,
    MerchantId,
    MerchantRootKey,
)
from personal_finance.contexts.merchant.presentation.http.router import (
    get_confirm_merchant_use_case,
    get_edit_merchant_use_case,
    get_list_merchants_use_case,
    get_merchant_use_case,
    get_merge_merchants_use_case,
    get_move_alias_use_case,
    get_split_alias_use_case,
    router,
)
from personal_finance.shared.domain.events import Event
from personal_finance.shared.domain.value_objects import PosixTime, UserId


USER_ID = UserId.from_string("11111111-1111-1111-1111-111111111111")
NOW = PosixTime.from_epoch_seconds(1_700_000_000)


class InMemoryMerchantRepository:
    def __init__(self) -> None:
        self.merchants: dict[tuple[UserId, MerchantId], Merchant] = {}
        self.alias_pointers: dict[tuple[UserId, str], MerchantId] = {}
        self.root_pointers: dict[tuple[UserId, str], MerchantId] = {}

    def find(self, *, user_id: UserId, merchant_id: MerchantId) -> Merchant | None:
        return self.merchants.get((user_id, merchant_id))

    def find_by_alias(
        self,
        *,
        user_id: UserId,
        fingerprint: AliasFingerprint,
    ) -> Merchant | None:
        merchant_id = self.alias_pointers.get((user_id, fingerprint.value))

        if merchant_id is None:
            return None

        merchant = self.find(user_id=user_id, merchant_id=merchant_id)

        return merchant if merchant and merchant.has_alias(fingerprint) else None

    def list_root_keys(self, user_id: UserId) -> Mapping[MerchantRootKey, MerchantId]:
        return {
            MerchantRootKey(value=root_key): merchant_id
            for (owner, root_key), merchant_id in self.root_pointers.items()
            if owner == user_id
        }

    def list_by_user(self, user_id: UserId) -> Sequence[Merchant]:
        return [
            merchant
            for (owner, _), merchant in self.merchants.items()
            if owner == user_id
        ]

    def save(self, merchant: Merchant) -> None:
        self.merchants[(merchant.user_id, merchant.id)] = merchant

        for alias in merchant.children:
            self.alias_pointers[(merchant.user_id, alias.fingerprint.value)] = (
                merchant.id
            )

        for root_key in merchant.root_keys:
            self.root_pointers[(merchant.user_id, root_key.value)] = merchant.id

    def delete(self, *, user_id: UserId, merchant_id: MerchantId) -> None:
        self.merchants.pop((user_id, merchant_id), None)


class AlwaysNewProcessedEventStore:
    def claim(self, *, user_id: UserId, event_id: uuid.UUID) -> bool:
        del user_id, event_id

        return True


class NullEventPublisher:
    def publish(self, events: Sequence[Event]) -> None:
        del events


@pytest.fixture
def repository() -> InMemoryMerchantRepository:
    return InMemoryMerchantRepository()


@pytest.fixture
def seed(repository: InMemoryMerchantRepository) -> ResolveMerchantUseCase:
    """Merchants only ever come into existence through a parsed email, so the
    tests create them the same way the system does.
    """
    return ResolveMerchantUseCase(
        repository=repository,
        processed_events=AlwaysNewProcessedEventStore(),
        event_publisher=NullEventPublisher(),
    )


@pytest.fixture
def client(repository: InMemoryMerchantRepository) -> TestClient:
    publisher = NullEventPublisher()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_list_merchants_use_case] = lambda: (
        ListMerchantsUseCase(repository=repository)
    )
    app.dependency_overrides[get_merchant_use_case] = lambda: GetMerchantUseCase(
        repository=repository,
    )
    app.dependency_overrides[get_edit_merchant_use_case] = lambda: EditMerchantUseCase(
        repository=repository,
        event_publisher=publisher,
    )
    app.dependency_overrides[get_confirm_merchant_use_case] = lambda: (
        ConfirmMerchantUseCase(
            repository=repository,
            event_publisher=publisher,
        )
    )
    app.dependency_overrides[get_move_alias_use_case] = lambda: MoveAliasUseCase(
        repository=repository,
        event_publisher=publisher,
    )
    app.dependency_overrides[get_split_alias_use_case] = lambda: SplitAliasUseCase(
        repository=repository,
        event_publisher=publisher,
    )
    app.dependency_overrides[get_merge_merchants_use_case] = lambda: (
        MergeMerchantsUseCase(
            repository=repository,
            event_publisher=publisher,
        )
    )

    return TestClient(app)


def _see(use_case: ResolveMerchantUseCase, counterparty: str) -> Merchant:
    result = use_case.execute(
        RecordSightingCommand(
            user_id=USER_ID,
            counterparty=counterparty,
            occurred_at=NOW,
            event_id=uuid.uuid4(),
            kind=CounterpartyKind.BUSINESS,
        ),
    )
    assert result.merchant is not None

    return result.merchant


def test_the_categories_a_dropdown_can_offer_are_published(
    client: TestClient,
) -> None:
    response = client.get("/merchants/categories")

    assert response.status_code == 200
    values = [category["value"] for category in response.json()["categories"]]
    assert "groceries" in values
    assert "uncategorized" in values


def test_listing_returns_the_users_merchants_with_a_review_badge(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    _see(seed, "TIENDAS ARA 123")
    _see(seed, "JUAN VALDEZ")

    response = client.get("/merchants")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["needs_review"] == 2
    assert {merchant["display_name"] for merchant in body["merchants"]} == {
        "Tiendas Ara 123",
        "Juan Valdez",
    }


def test_listing_can_be_filtered_down_to_what_needs_review(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    reviewed = _see(seed, "TIENDAS ARA")
    _see(seed, "JUAN VALDEZ")
    client.patch(
        f"/merchants/{reviewed.id.value}",
        json={"display_name": "Ara", "category": "groceries"},
    )

    response = client.get("/merchants", params={"needs_review": True})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["merchants"][0]["display_name"] == "Juan Valdez"
    # The badge counts everything the user owns, not the filtered page.
    assert body["needs_review"] == 1


def test_search_matches_what_the_bank_wrote(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    merchant = _see(seed, "TIENDAS ARA 123")
    client.patch(
        f"/merchants/{merchant.id.value}",
        json={"display_name": "Corner shop"},
    )

    response = client.get("/merchants", params={"search": "ara"})

    assert response.status_code == 200
    assert response.json()["total"] == 1


def test_the_detail_view_lists_every_child(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    merchant = _see(seed, "TIENDAS ARA 123")
    _see(seed, "ARA CALLE 80")

    response = client.get(f"/merchants/{merchant.id.value}")

    assert response.status_code == 200
    body = response.json()
    assert body["alias_count"] == 2
    assert {alias["fingerprint"] for alias in body["aliases"]} == {
        "TIENDAS ARA 123",
        "ARA CALLE 80",
    }
    assert {alias["origin"] for alias in body["aliases"]} == {"seed", "derived"}


def test_a_merchant_that_is_not_this_users_reads_as_missing(
    client: TestClient,
) -> None:
    response = client.get(f"/merchants/{uuid.uuid4()}")

    assert response.status_code == 404


def test_a_merchant_id_that_is_not_an_id_is_rejected(client: TestClient) -> None:
    response = client.get("/merchants/not-an-id")

    assert response.status_code == 422


def test_renaming_and_categorizing_takes_it_out_of_review(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    merchant = _see(seed, "TIENDAS ARA 123")

    response = client.patch(
        f"/merchants/{merchant.id.value}",
        json={"display_name": "Ara", "category": "groceries"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["display_name"] == "Ara"
    assert body["category"] == "groceries"
    assert body["needs_review"] is False


def test_an_edit_that_changes_nothing_is_rejected(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    merchant = _see(seed, "TIENDAS ARA")

    response = client.patch(f"/merchants/{merchant.id.value}", json={})

    assert response.status_code == 422


def test_an_unknown_category_is_rejected(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    merchant = _see(seed, "TIENDAS ARA")

    response = client.patch(
        f"/merchants/{merchant.id.value}",
        json={"category": "crypto-jets"},
    )

    assert response.status_code == 422


def test_confirming_accepts_the_grouping_as_it_stands(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    merchant = _see(seed, "EXITO")
    _see(seed, "EXITO EXPRESS")

    response = client.post(f"/merchants/{merchant.id.value}/confirm")

    assert response.status_code == 200
    assert response.json()["needs_review"] is False


def test_a_child_can_be_moved_to_another_merchant(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    guessed = _see(seed, "EXITO")
    _see(seed, "EXITO EXPRESS")
    elsewhere = _see(seed, "CARULLA")

    response = client.post(
        f"/merchants/{guessed.id.value}/aliases/move",
        json={
            "fingerprint": "EXITO EXPRESS",
            "target_merchant_id": str(elsewhere.id.value),
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(elsewhere.id.value)
    assert {alias["fingerprint"] for alias in body["aliases"]} == {
        "CARULLA",
        "EXITO EXPRESS",
    }


def test_moving_a_child_the_merchant_does_not_have_is_a_404(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    merchant = _see(seed, "EXITO")
    elsewhere = _see(seed, "CARULLA")

    response = client.post(
        f"/merchants/{merchant.id.value}/aliases/move",
        json={
            "fingerprint": "SOMETHING ELSE",
            "target_merchant_id": str(elsewhere.id.value),
        },
    )

    assert response.status_code == 404


def test_moving_the_only_child_away_is_refused(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    merchant = _see(seed, "EXITO")
    elsewhere = _see(seed, "CARULLA")

    response = client.post(
        f"/merchants/{merchant.id.value}/aliases/move",
        json={
            "fingerprint": "EXITO",
            "target_merchant_id": str(elsewhere.id.value),
        },
    )

    # It would leave a merchant nothing can reach.
    assert response.status_code == 409


def test_a_child_can_be_split_into_a_merchant_of_its_own(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    guessed = _see(seed, "EXITO")
    _see(seed, "EXITO SEGUROS")

    response = client.post(
        f"/merchants/{guessed.id.value}/aliases/split",
        json={
            "fingerprint": "EXITO SEGUROS",
            "display_name": "Éxito Seguros",
            "category": "fees",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["id"] != str(guessed.id.value)
    assert body["display_name"] == "Éxito Seguros"
    assert body["category"] == "fees"


def test_two_merchants_can_be_merged(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    survivor = _see(seed, "NEQUI")
    duplicate = _see(seed, "BANCOLOMBIA NEQUI")

    response = client.post(
        f"/merchants/{survivor.id.value}/merge",
        json={"absorbed_merchant_id": str(duplicate.id.value)},
    )

    assert response.status_code == 200
    assert response.json()["alias_count"] == 2
    assert client.get(f"/merchants/{duplicate.id.value}").status_code == 404


def test_a_merchant_cannot_be_merged_into_itself(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    merchant = _see(seed, "NEQUI")

    response = client.post(
        f"/merchants/{merchant.id.value}/merge",
        json={"absorbed_merchant_id": str(merchant.id.value)},
    )

    assert response.status_code == 400


def test_the_catalog_publishes_every_vocabulary_the_endpoints_use(
    client: TestClient,
) -> None:
    response = client.get("/merchants/catalog")

    assert response.status_code == 200

    catalog = response.json()
    assert "groceries" in [option["value"] for option in catalog["categories"]]
    assert "last_seen" in [option["value"] for option in catalog["sorts"]]
    assert "confirmed" in [option["value"] for option in catalog["statuses"]]
    assert "manual" in [option["value"] for option in catalog["alias_origins"]]
    assert "person" in [option["value"] for option in catalog["counterparty_kinds"]]


def test_the_catalog_and_the_older_categories_endpoint_agree(
    client: TestClient,
) -> None:
    """Two endpoints answer the same list, so nothing may drift between them."""
    catalog = client.get("/merchants/catalog").json()["categories"]
    categories = client.get("/merchants/categories").json()["categories"]

    assert catalog == categories
