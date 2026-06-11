"""Tests for the /api/tasks HTTP routes — all runtime calls are mocked."""

from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from hui_agent.state import HUITestState
from web.main import create_app


def _fake_state(menu_name: str = "测试菜单", status: str = "success") -> HUITestState:
    return HUITestState.new(menu_name).merge(status=status)


@pytest.fixture()
def client():
    """Fresh TestClient per test with the adapter mocked."""
    app = create_app()
    return TestClient(app)


class TestHealthEndpoint:
    def test_health_returns_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


class TestRunTaskEndpoint:
    @patch("web.adapters.agent_adapter.run_menu_runtime")
    def test_run_success(self, mock_run, client):
        state = _fake_state("基金分红报告", status="success")
        mock_run.return_value = state

        resp = client.post("/api/tasks/run", json={"menu_name": "基金分红报告"})

        assert resp.status_code == 200
        body = resp.json()
        assert body["menu_name"] == "基金分红报告"
        assert body["status"] == "success"
        assert body["task_id"] == state.thread_id
        assert isinstance(body["completed_steps"], list)
        assert body["result_path"].endswith("result.json")

    @patch("web.adapters.agent_adapter.run_menu_runtime")
    def test_run_failed_status(self, mock_run, client):
        mock_run.return_value = _fake_state(status="failed")

        resp = client.post("/api/tasks/run", json={"menu_name": "测试菜单"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"

    @patch("web.adapters.agent_adapter.run_menu_runtime")
    def test_run_runtime_error_returns_500(self, mock_run, client):
        mock_run.side_effect = RuntimeError("Chrome not available")

        resp = client.post("/api/tasks/run", json={"menu_name": "测试菜单"})
        assert resp.status_code == 500
        assert "Chrome not available" in resp.json()["detail"]

    def test_run_missing_menu_name_returns_422(self, client):
        resp = client.post("/api/tasks/run", json={})
        assert resp.status_code == 422

    def test_run_empty_menu_name_returns_422(self, client):
        resp = client.post("/api/tasks/run", json={"menu_name": ""})
        assert resp.status_code == 422

    @patch("web.adapters.agent_adapter.run_menu_runtime")
    def test_run_blocked_status(self, mock_run, client):
        mock_run.return_value = _fake_state(status="blocked")

        resp = client.post("/api/tasks/run", json={"menu_name": "测试菜单"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "blocked"

    @patch("web.adapters.agent_adapter.run_menu_runtime")
    def test_run_skipped_status(self, mock_run, client):
        mock_run.return_value = _fake_state(status="skipped")

        resp = client.post("/api/tasks/run", json={"menu_name": "测试菜单"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "skipped"

    @patch("web.adapters.agent_adapter.run_menu_runtime")
    def test_run_failure_fields_populated(self, mock_run, client):
        state = HUITestState.new("测试菜单").record_failure("2c", "dry-run failed", scope="menu")
        mock_run.return_value = state

        resp = client.post("/api/tasks/run", json={"menu_name": "测试菜单"})
        body = resp.json()
        assert body["status"] == "failed"
        assert body["failure_scope"] == "menu"
        assert body["failure_reason"] == "dry-run failed"
