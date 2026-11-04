import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("FINTRIS_INTERNAL_TOKEN", "test-backend-secret")
    with TestClient(app) as client:
        yield client


def example_event(user_id=12, event_id="chat-0001", event_type="chat_message"):
    return {
        "event_id": event_id,
        "user_id": user_id,
        "event_type": event_type,
        "payload": {"message_id": "m-1", "text": "식비 예산을 바꿔줘"},
    }


def auth():
    return {"X-Internal-Token": "test-backend-secret"}


def test_health(client):
    assert client.get("/health").json() == {"status": "ok", "agent_runtime": "stub"}


def test_requires_internal_auth(client):
    response = client.post("/internal/v1/agent/events", json=example_event())
    assert response.status_code == 401


def test_create_once_and_append_messages(client):
    first = client.post("/internal/v1/agent/events", json=example_event(), headers=auth())
    second = client.post("/internal/v1/agent/events", json=example_event(event_id="chat-0002"), headers=auth())
    assert first.status_code == 202
    assert first.json()["agent_created"] is True
    assert second.json()["agent_created"] is False
    assert first.json()["agent_id"] == second.json()["agent_id"]
    assert second.json()["pending_events"] == 2
    result = client.get("/internal/v1/agents/12", headers=auth())
    assert result.status_code == 200
    assert result.json()["pending_events"] == 2


def test_duplicate_delivery_is_idempotent(client):
    first = client.post("/internal/v1/agent/events", json=example_event(), headers=auth())
    second = client.post("/internal/v1/agent/events", json=example_event(), headers=auth())
    assert second.json()["duplicate"] is True
    assert first.json()["agent_id"] == second.json()["agent_id"]
    assert second.json()["pending_events"] == 1


def test_users_are_isolated(client):
    first = client.post("/internal/v1/agent/events", json=example_event(user_id=12), headers=auth())
    second = client.post("/internal/v1/agent/events", json=example_event(user_id=13), headers=auth())
    assert first.json()["agent_id"] != second.json()["agent_id"]


def test_all_three_event_types_are_accepted(client):
    cases = (
        ("initial_blocks", {"transactions": [], "summary": {"available_balance": 0}}),
        ("chat_message", {"message_id": "m-42", "text": "안녕"}),
        ("monthly_report", {"month": "2026-09", "metrics": {"spent": 100000}}),
    )
    for index, (event_type, payload) in enumerate(cases, 1):
        data = example_event(event_id=f"evt-{index}", event_type=event_type)
        data["payload"] = payload
        response = client.post("/internal/v1/agent/events", json=data, headers=auth())
        assert response.status_code == 202
    result = client.get("/internal/v1/agents/12", headers=auth())
    assert result.json()["pending_events"] == 3


def test_invalid_user_id_rejected(client):
    response = client.post(
        "/internal/v1/agent/events",
        json=example_event(user_id=-1),
        headers=auth(),
    )
    assert response.status_code == 422
