import pytest

from hui_agent.failure_policy import ConfigFailure, EnvironmentFailure, MenuFailure
from hui_agent.graph import build_workflow
from hui_agent.state import HUITestState


def test_state_records_menu_failure_as_failed_without_human_review():
    state = HUITestState.new("基金分红查询").record_failure(
        "2c",
        "dry-run failed after retries",
        scope="menu",
    )

    assert state.status == "failed"
    assert state.failure_scope == "menu"
    assert state.failure_reason == "dry-run failed after retries"
    assert "human_required" not in state.to_dict()


def test_state_records_environment_failure_as_blocked():
    state = HUITestState.new("基金分红查询").record_failure(
        "0b",
        "CDP unavailable",
        scope="environment",
    )

    assert state.status == "blocked"
    assert state.failure_scope == "environment"


def test_workflow_skips_menu_failures_and_blocks_environment_failures():
    def fail_menu(state):
        raise MenuFailure("no stable SQL could be generated")

    failed = build_workflow(
        step_handlers={"2c": fail_menu},
        step_order=("2c",),
    ).invoke(HUITestState.new("基金分红查询"))

    assert failed.status == "failed"
    assert failed.failure_scope == "menu"

    def fail_env(state):
        raise EnvironmentFailure("database unavailable")

    blocked = build_workflow(
        step_handlers={"0b": fail_env},
        step_order=("0b",),
    ).invoke(HUITestState.new("基金分红查询"))

    assert blocked.status == "blocked"
    assert blocked.failure_scope == "environment"


def test_config_failure_skips_current_menu():
    state = HUITestState.new("基金分红查询").record_exception(
        "0a",
        ConfigFailure("menu config missing"),
    )

    assert state.status == "failed"
    assert state.failure_scope == "config"
