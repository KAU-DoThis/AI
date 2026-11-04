"""초기 블록 수신 API 테스트: 프로덕션 app/main.py에 의존하지 않음."""

import copy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.initial_blocks import router

TOKEN = "test-only-internal-token"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("FINTRIS_INTERNAL_TOKEN", TOKEN)
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def sample() -> dict:
    return {
        "request_id": "init-42-001",
        "user": {"user_id": 42, "nickname": "테스트"},
        "transactions": [
            {
                "transaction_id": 501,
                "linked_account_id": 11,
                "occurred_at": "2026-09-25T09:30:00+09:00",
                "amount": 600000,
                "direction": "OUT",
                "merchant_or_payee": "김민수",
            }
        ],
    }


def post(client: TestClient, sample: dict, token: str | None = TOKEN):
    headers = {"X-Internal-Token": token} if token is not None else {}
    return client.post("/internal/v1/initial-blocks", json=sample, headers=headers)


def test_receives_request(client: TestClient, sample: dict):
    response = post(client, sample)
    assert response.status_code == 200
    assert response.json() == {
        "status": "received",
        "request_id": "init-42-001",
        "user_id": 42,
        "transaction_count": 1,
    }
    assert "김민수" not in response.text  # 금융 거래 원문을 에코하지 않음


def test_missing_token_rejected(client: TestClient, sample: dict):
    assert post(client, sample, None).status_code == 401


def test_wrong_token_rejected(client: TestClient, sample: dict):
    assert post(client, sample, "wrong").status_code == 401


def test_missing_server_token_fails_closed(
    client: TestClient, sample: dict, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("FINTRIS_INTERNAL_TOKEN")
    assert post(client, sample).status_code == 503


def test_invalid_direction_returns_422(client: TestClient, sample: dict):
    sample["transactions"][0]["direction"] = "TRANSFER"
    assert post(client, sample).status_code == 422


def test_amount_must_be_integer(client: TestClient, sample: dict):
    sample["transactions"][0]["amount"] = 1000.5
    assert post(client, sample).status_code == 422


def test_request_id_is_required(client: TestClient, sample: dict):
    sample.pop("request_id")
    assert post(client, sample).status_code == 422


def test_empty_transactions_allowed(client: TestClient, sample: dict):
    sample["transactions"] = []
    response = post(client, sample)
    assert response.status_code == 200
    assert response.json()["transaction_count"] == 0


def test_account_id_alias_supported(client: TestClient, sample: dict):
    tx = sample["transactions"][0]
    tx["account_id"] = tx.pop("linked_account_id")
    assert post(client, sample).status_code == 200


def test_extra_fields_allowed_temporarily(client: TestClient, sample: dict):
    sample["transactions"][0]["bank_specific_field"] = "test"
    sample["user"]["other_attribute"] = "test"
    sample["metadata"] = {"source": "onboarding"}
    assert post(client, sample).status_code == 200


def test_different_users_have_independent_receipts(client: TestClient, sample: dict):
    second = copy.deepcopy(sample)
    second["request_id"] = "init-43-001"
    second["user"]["user_id"] = 43
    response_a = post(client, sample)
    response_b = post(client, second)
    assert response_a.json()["user_id"] == 42
    assert response_b.json()["user_id"] == 43
