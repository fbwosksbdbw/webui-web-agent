"""Tests for AgentAdapter — all runtime calls are mocked."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from hui_agent.state import HUITestState
from web.adapters.agent_adapter import AgentAdapter


def _fake_state(menu_name: str = "测试菜单", status: str = "success") -> HUITestState:
    """Return a minimal HUITestState for testing."""
    return HUITestState.new(menu_name).merge(status=status)


class TestAgentAdapterRunMenu:
    """AgentAdapter.run_menu delegates to run_menu_runtime and converts output."""

    @patch("web.adapters.agent_adapter.run_menu_runtime")
    def test_returns_expected_keys(self, mock_run):
        mock_run.return_value = _fake_state()
        adapter = AgentAdapter()
        result = adapter.run_menu("测试菜单")

        assert "task_id" in result
        assert "menu_name" in result
        assert "status" in result
        assert "current_step" in result
        assert "completed_steps" in result
        assert "failure_scope" in result
        assert "failure_reason" in result
        assert "result_path" in result

    @patch("web.adapters.agent_adapter.run_menu_runtime")
    def test_passes_menu_name_through(self, mock_run):
        mock_run.return_value = _fake_state("基金分红报告")
        adapter = AgentAdapter()
        adapter.run_menu("基金分红报告")

        mock_run.assert_called_once_with(
            "基金分红报告",
            skill_root=None,
            runs_root="runs",
            checkpoint_db="runs/checkpoints.sqlite",
        )

    @patch("web.adapters.agent_adapter.run_menu_runtime")
    def test_status_reflects_state(self, mock_run):
        mock_run.return_value = _fake_state(status="failed")
        adapter = AgentAdapter()
        result = adapter.run_menu("测试菜单")

        assert result["status"] == "failed"

    @patch("web.adapters.agent_adapter.run_menu_runtime")
    def test_custom_paths_forwarded(self, mock_run):
        mock_run.return_value = _fake_state()
        adapter = AgentAdapter(
            skill_root="/custom/skill",
            runs_root="/custom/runs",
            checkpoint_db="/custom/checkpoints.sqlite",
        )
        adapter.run_menu("测试菜单")

        mock_run.assert_called_once_with(
            "测试菜单",
            skill_root="/custom/skill",
            runs_root="/custom/runs",
            checkpoint_db="/custom/checkpoints.sqlite",
        )

    @patch("web.adapters.agent_adapter.run_menu_runtime")
    def test_result_path_contains_thread_id(self, mock_run):
        state = _fake_state()
        mock_run.return_value = state
        adapter = AgentAdapter()
        result = adapter.run_menu("测试菜单")

        assert state.thread_id in result["result_path"]
        assert result["result_path"].endswith("result.json")

    @patch("web.adapters.agent_adapter.run_menu_runtime")
    def test_propagates_exception(self, mock_run):
        mock_run.side_effect = RuntimeError("boom")
        adapter = AgentAdapter()

        with pytest.raises(RuntimeError, match="boom"):
            adapter.run_menu("测试菜单")
