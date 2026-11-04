"""Regression tests for moved packages + new combined application routes."""

import inspect

import pytest
from fastapi.testclient import TestClient

from app.agent.manager import AgentManager
from app.agent.user_agent import UserAgent
from app.main import app
from app.models.agent_event import AgentEvent, EventReceipt, EventType
from app.models.agent_status import AgentStatus


def test_refactored_modules_import():
    assert inspect.isclass(AgentManager)
    assert inspect.isclass(UserAgent)
    assert inspect.isclass(AgentEvent)
    assert inspect.isclass(EventReceipt)
    assert inspect.isclass(AgentStatus)
    assert EventType.INITIAL_BLOCKS.value == "initial_blocks"


def test_openapi_has_both_old_and_new_endpoints():
    paths = app.openapi()["paths"]
    assert "/internal/v1/agent/events" in paths
    assert "/internal/v1/agents/{user_id}" in paths
    assert "/internal/v1/initial-blocks" in paths
    assert "/health" in paths


def test_initial_blocks_endpoint_registered_in_main(monkeypatch):
    monkeypatch.setenv("FINTRIS_INTERNAL_TOKEN", "integration-only-secret")
    with TestClient(app) as client:
        response = client.post(
            "/internal/v1/initial-blocks",
            json={
                "request_id": "init-42-001",
                "user": {"user_id": 42},
                "transactions": [],
            },
            headers={"X-Internal-Token": "integration-only-secret"},
        )
        assert response.status_code == 200
        assert response.json()["transaction_count"] == 0
