from hui_agent.state import HUITestState, STEP_ORDER


def test_state_round_trips_and_tracks_completed_steps():
    state = HUITestState.new(menu_name="基金分红查询", task_id="task-1")

    state = state.mark_completed("0a").with_paths(
        config_path="case/config.json",
        case_plan_path="case/case_plan.json",
        case_dir="case",
    )

    restored = HUITestState.from_dict(state.to_dict())

    assert restored.menu_name == "基金分红查询"
    assert restored.task_id == "task-1"
    assert restored.completed_steps == ["0a"]
    assert restored.current_step == "0b"
    assert restored.config_path == "case/config.json"
    assert restored.case_plan_path == "case/case_plan.json"
    assert restored.case_dir == "case"


def test_state_can_retry_from_a_step():
    state = HUITestState.new(menu_name="基金分红查询")
    for step in STEP_ORDER[:6]:
        state = state.mark_completed(step)

    retried = state.retry_from("2a")

    assert retried.completed_steps == ["0a", "0b", "1a", "1b"]
    assert retried.current_step == "2a"
    assert retried.retry_counts["2a"] == 1


def test_state_records_menu_failure_without_losing_progress():
    state = HUITestState.new(menu_name="基金分红查询").mark_completed("0a")

    failed = state.record_failure("0b", "Chrome CDP unavailable", scope="menu")

    assert failed.completed_steps == ["0a"]
    assert failed.current_step == "0b"
    assert failed.status == "failed"
    assert failed.failure_scope == "menu"
    assert failed.errors[-1]["step"] == "0b"
    assert failed.errors[-1]["message"] == "Chrome CDP unavailable"
