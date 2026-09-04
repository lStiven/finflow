"""The surface a frontend calls, exercised end to end over fakes."""

from collections.abc import Mapping, Sequence
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from personal_finance.contexts.identity.presentation.http.router import (
    get_current_user_id,
)
from personal_finance.contexts.merchant.application.categories import (
    CategoryCatalog,
    CreateCategoryUseCase,
    DeleteCategoryUseCase,
    ListCategoriesUseCase,
    RenameCategoryUseCase,
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
from personal_finance.contexts.merchant.domain.categories import Category
from personal_finance.contexts.merchant.domain.entities import Merchant
from personal_finance.contexts.merchant.domain.exceptions import (
    DuplicateCategoryError,
)
from personal_finance.contexts.merchant.domain.normalization import derive_slug
from personal_finance.contexts.merchant.domain.value_objects import (
    AliasFingerprint,
    CategoryKey,
    CounterpartyKind,
    MerchantId,
    MerchantRootKey,
)
from personal_finance.contexts.merchant.presentation.http.router import (
    get_category_catalog,
    get_confirm_merchant_use_case,
    get_create_category_use_case,
    get_delete_category_use_case,
    get_edit_merchant_use_case,
    get_list_categories_use_case,
    get_list_merchants_use_case,
    get_merchant_use_case,
    get_merge_merchants_use_case,
    get_move_alias_use_case,
    get_rename_category_use_case,
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


class InMemoryCategoryRepository:
    """The user's own half of the vocabulary, holding the same rules the
    conditional writes hold in DynamoDB: a name is claimed by exactly one
    category, and the claim moves when the name does.
    """

    def __init__(self) -> None:
        self.categories: dict[tuple[UserId, CategoryKey], Category] = {}
        self.names: dict[tuple[UserId, str], CategoryKey] = {}

    def list_by_user(self, user_id: UserId) -> Sequence[Category]:
        return [
            category
            for (owner, _), category in self.categories.items()
            if owner == user_id
        ]

    def find(self, *, user_id: UserId, key: CategoryKey) -> Category | None:
        stored = self.categories.get((user_id, key))

        # A copy, like a real repository hands back: whatever the caller does
        # to it is not stored until they say so, and a write that is refused
        # must leave nothing behind.
        return None if stored is None else _copy(stored)

    def add(self, category: Category) -> None:
        self._claim(category)
        self.categories[(category.user_id, category.id)] = _copy(category)

    def rename(self, category: Category, *, previous_label: str) -> None:
        previous = derive_slug(previous_label)

        if previous != category.name_key:
            self._claim(category)
            self.names.pop((category.user_id, previous), None)

        self.categories[(category.user_id, category.id)] = _copy(category)

    def delete(self, category: Category) -> None:
        self.categories.pop((category.user_id, category.id), None)
        self.names.pop((category.user_id, category.name_key), None)

    def _claim(self, category: Category) -> None:
        held = self.names.get((category.user_id, category.name_key))

        if held is not None and held != category.id:
            raise DuplicateCategoryError(f"{category.label!r} already exists")

        self.names[(category.user_id, category.name_key)] = category.id


def _copy(category: Category) -> Category:
    return Category(
        id=category.id,
        user_id=category.user_id,
        label=category.label,
        created_at=category.created_at,
    )


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
def category_repository() -> InMemoryCategoryRepository:
    return InMemoryCategoryRepository()


@pytest.fixture
def client(
    repository: InMemoryMerchantRepository,
    category_repository: InMemoryCategoryRepository,
) -> TestClient:
    publisher = NullEventPublisher()
    catalog = CategoryCatalog(repository=category_repository)
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: USER_ID
    app.dependency_overrides[get_list_merchants_use_case] = lambda: (
        ListMerchantsUseCase(repository=repository)
    )
    app.dependency_overrides[get_merchant_use_case] = lambda: GetMerchantUseCase(
        repository=repository,
    )
    app.dependency_overrides[get_category_catalog] = lambda: catalog
    app.dependency_overrides[get_create_category_use_case] = lambda: (
        CreateCategoryUseCase(repository=category_repository)
    )
    app.dependency_overrides[get_list_categories_use_case] = lambda: (
        ListCategoriesUseCase(catalog=catalog, merchants=repository)
    )
    app.dependency_overrides[get_rename_category_use_case] = lambda: (
        RenameCategoryUseCase(repository=category_repository)
    )
    app.dependency_overrides[get_delete_category_use_case] = lambda: (
        DeleteCategoryUseCase(
            repository=category_repository,
            merchants=repository,
            event_publisher=publisher,
        )
    )
    app.dependency_overrides[get_edit_merchant_use_case] = lambda: EditMerchantUseCase(
        repository=repository,
        event_publisher=publisher,
        categories=catalog,
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
        categories=catalog,
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


def test_a_user_can_add_a_category_and_then_see_it_in_their_own_list(
    client: TestClient,
) -> None:
    created = client.post("/merchants/categories", json={"label": "Mascotas"})

    assert created.status_code == 201, created.text
    assert created.json()["label"] == "Mascotas"
    assert created.json()["custom"] is True
    # Opaque, and nothing to do with the name: the name is the half that can
    # be corrected later.
    assert created.json()["value"].startswith("custom:")

    listed = client.get("/merchants/categories").json()["categories"]

    assert listed[-1] == created.json()
    # The shipped ones are still there, in the order they have always had.
    assert listed[0]["value"] == "uncategorized"
    assert listed[0]["custom"] is False
    # Counting merchants is not free, so nobody pays for it unasked.
    assert listed[0]["usage"] is None


def test_the_same_category_twice_is_a_conflict_not_a_second_category(
    client: TestClient,
) -> None:
    client.post("/merchants/categories", json={"label": "Mascotas"})

    response = client.post("/merchants/categories", json={"label": "mascotas"})

    assert response.status_code == 409
    assert len(client.get("/merchants/categories").json()["categories"]) == 17


def test_a_category_name_too_long_for_a_chip_is_refused(client: TestClient) -> None:
    response = client.post(
        "/merchants/categories",
        json={"label": "Cosas de la casa y del carro"},
    )

    assert response.status_code == 422


def test_a_category_name_with_nothing_to_key_it_by_is_refused(
    client: TestClient,
) -> None:
    response = client.post("/merchants/categories", json={"label": "!!!"})

    assert response.status_code == 422


def test_a_merchant_can_be_filed_under_a_category_its_owner_wrote(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    merchant = _see(seed, "AGROPECUARIA EL CAMPO")
    key = _make_category(client, "Mascotas")

    response = client.patch(
        f"/merchants/{merchant.id.value}",
        json={"category": key},
    )

    assert response.status_code == 200, response.text
    assert response.json()["category"] == key
    # And the list can then be filtered down to it.
    filtered = client.get(f"/merchants?category={key}").json()
    assert [found["id"] for found in filtered["merchants"]] == [str(merchant.id.value)]


def _make_category(client: TestClient, label: str) -> str:
    response = client.post("/merchants/categories", json={"label": label})

    assert response.status_code == 201, response.text

    return str(response.json()["value"])


def test_a_typo_in_a_category_name_is_fixed_without_moving_anything(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    """The reason the key is not the name: a correction is one write, and
    every merchant already filed under it stays where it is.
    """
    merchant = _see(seed, "AGROPECUARIA EL CAMPO")
    key = _make_category(client, "Mascotss")
    client.patch(f"/merchants/{merchant.id.value}", json={"category": key})

    renamed = client.patch(f"/merchants/categories/{key}", json={"label": "Mascotas"})

    assert renamed.status_code == 200, renamed.text
    assert renamed.json() == {
        "value": key,
        "label": "Mascotas",
        "custom": True,
        "usage": None,
    }
    # Same key, so the merchant never moved and the filter still finds it.
    still = client.get(f"/merchants?category={key}").json()
    assert [found["id"] for found in still["merchants"]] == [str(merchant.id.value)]


def test_a_rename_onto_a_name_already_taken_is_refused(client: TestClient) -> None:
    _make_category(client, "Mascotas")
    other = _make_category(client, "Bici")

    response = client.patch(
        f"/merchants/categories/{other}", json={"label": "mascotas"}
    )

    assert response.status_code == 409
    # And the losing category kept its own name.
    listed = client.get("/merchants/categories").json()["categories"]
    assert sorted(c["label"] for c in listed if c["custom"]) == ["Bici", "Mascotas"]


def test_a_category_frees_its_old_name_when_it_is_renamed(client: TestClient) -> None:
    key = _make_category(client, "Mascotas")
    client.patch(f"/merchants/categories/{key}", json={"label": "Perros"})

    # "Mascotas" is nobody's now, so it can be taken again.
    assert (
        client.post("/merchants/categories", json={"label": "Mascotas"}).status_code
        == 201
    )


def test_removing_a_category_puts_what_was_in_it_back_in_the_default_bucket(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    merchant = _see(seed, "AGROPECUARIA EL CAMPO")
    key = _make_category(client, "Mascotas")
    client.patch(f"/merchants/{merchant.id.value}", json={"category": key})

    removed = client.request("DELETE", f"/merchants/categories/{key}")

    assert removed.status_code == 200, removed.text
    assert removed.json() == {"value": key, "label": "Mascotas", "merchants_moved": 1}

    # Gone from the vocabulary, and nothing is left naming it.
    listed = client.get("/merchants/categories").json()["categories"]
    assert [c for c in listed if c["custom"]] == []
    assert (
        client.get(f"/merchants/{merchant.id.value}").json()["category"]
        == "uncategorized"
    )


def test_removing_a_category_does_not_count_as_reviewing_what_was_in_it(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    """Filing a merchant confirms it; having its bucket taken away does not.

    Otherwise removing one category would quietly empty somebody's review
    queue of every merchant that happened to be in it.
    """
    reviewed = _see(seed, "AGROPECUARIA EL CAMPO")
    unreviewed = _see(seed, "TIENDAS ARA 123")
    key = _make_category(client, "Mascotas")
    client.patch(f"/merchants/{reviewed.id.value}", json={"category": key})

    client.request("DELETE", f"/merchants/categories/{key}")

    assert client.get(f"/merchants/{reviewed.id.value}").json()["needs_review"] is False
    assert (
        client.get(f"/merchants/{unreviewed.id.value}").json()["needs_review"] is True
    )


def test_the_categories_the_app_ships_are_nobodys_to_edit(client: TestClient) -> None:
    assert (
        client.patch("/merchants/categories/groceries", json={"label": "Mercado"})
    ).status_code == 409
    assert (
        client.request("DELETE", "/merchants/categories/groceries").status_code == 409
    )


def test_editing_somebody_elses_category_is_indistinguishable_from_a_typo(
    client: TestClient,
) -> None:
    missing = "custom:0123456789abcdef0123456789abcdef"

    assert (
        client.patch(f"/merchants/categories/{missing}", json={"label": "Mascotas"})
    ).status_code == 422
    assert (
        client.request("DELETE", f"/merchants/categories/{missing}").status_code == 422
    )


def test_usage_says_how_much_would_move_if_a_category_went_away(
    client: TestClient,
    seed: ResolveMerchantUseCase,
) -> None:
    """The number the confirmation dialog needs, and the reason it is opt-in:
    answering it means reading the caller's merchants.
    """
    merchant = _see(seed, "AGROPECUARIA EL CAMPO")
    _see(seed, "TIENDAS ARA 123")
    key = _make_category(client, "Mascotas")
    client.patch(f"/merchants/{merchant.id.value}", json={"category": key})

    listed = client.get("/merchants/categories?with_usage=true").json()["categories"]
    usage = {category["value"]: category["usage"] for category in listed}

    assert usage[key] == 1
    assert usage["uncategorized"] == 1
    assert usage["groceries"] == 0


def test_filtering_by_a_category_nobody_has_is_refused(client: TestClient) -> None:
    """Not answered with an empty page: on a money screen "nothing here" and
    "you asked for a bucket that does not exist" must not look the same.
    """
    assert client.get("/merchants?category=custom:mascotas").status_code == 422


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
    assert "last_seen" in [option["value"] for option in catalog["sorts"]]
    assert "confirmed" in [option["value"] for option in catalog["statuses"]]
    assert "manual" in [option["value"] for option in catalog["alias_origins"]]
    assert "person" in [option["value"] for option in catalog["counterparty_kinds"]]


def test_the_catalog_does_not_publish_categories(client: TestClient) -> None:
    """It cannot: half of that vocabulary belongs to whoever is asking, and
    this answer is the same for everybody.
    """
    assert "categories" not in client.get("/merchants/catalog").json()
