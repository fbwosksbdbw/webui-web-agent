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


def test_from_dict_loads_case_plan_from_disk_when_field_is_empty(tmp_path):
    """Resume compatibility: old checkpoints without case_plan should
    automatically load from the existing case_plan.json on disk."""
    import json

    case_dir = tmp_path / "case"
    case_dir.mkdir()
    cp_path = case_dir / "case_plan.json"
    cp_path.write_text(
        json.dumps({"menu": {"name": "旧菜单"}, "shard": {"db": "ui_bc"}}),
        encoding="utf-8",
    )

    old_payload = {
        "thread_id": "hui-old-checkpoint",
        "menu_name": "旧菜单",
        "case_plan_path": str(cp_path),
        "case_dir": str(case_dir),
        # case_plan is intentionally absent (pre-refactoring checkpoint)
    }
    restored = HUITestState.from_dict(old_payload)

    assert restored.case_plan["menu"]["name"] == "旧菜单"
    assert restored.case_plan["shard"]["db"] == "ui_bc"
    # The disk file should NOT have been overwritten
    on_disk = json.loads(cp_path.read_text(encoding="utf-8"))
    assert on_disk["menu"]["name"] == "旧菜单"


def test_edit_case_plan_does_not_mutate_original_state():
    """edit_case_plan must return a deep copy so that in-place edits
    don't corrupt the frozen state's internal dict."""
    state = HUITestState.new("菜单A").merge(
        case_plan={"menu": {"name": "菜单A"}, "form_fields": {"all": [1, 2]}},
    )
    original_cp = state.case_plan

    cp = state.edit_case_plan()
    cp["menu"]["name"] = "已修改"
    cp["form_fields"]["all"].append(3)
    cp["new_key"] = "should not appear"

    # Original state.case_plan must be completely untouched
    assert state.case_plan["menu"]["name"] == "菜单A"
    assert state.case_plan["form_fields"]["all"] == [1, 2]
    assert "new_key" not in state.case_plan
    # The reference itself must not have been mutated
    assert original_cp["menu"]["name"] == "菜单A"
