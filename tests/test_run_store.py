from hui_agent.run_store import RunStore
from hui_agent.state import HUITestState


def test_run_store_writes_events_and_result(tmp_path):
    store = RunStore(tmp_path)
    state = HUITestState.new("基金分红查询", task_id="task-1")

    store.write_event(state.thread_id, "0a", "started", {"menu": state.menu_name})
    result_path = store.write_result(state.mark_completed("0a"))

    assert result_path.exists()
    assert "基金分红查询" in result_path.read_text(encoding="utf-8")

    events = store.read_events(state.thread_id)
    assert events[0]["step"] == "0a"
    assert events[0]["event"] == "started"
    assert events[0]["payload"]["menu"] == "基金分红查询"


def test_run_store_writes_human_readable_agent_log(tmp_path):
    store = RunStore(tmp_path)
    state = HUITestState.new("基金分红查询", task_id="task-1")

    path = store.write_log_line(state.thread_id, "0a", "started", "菜单初始化")

    assert path.name == "agent.log"
    text = path.read_text(encoding="utf-8")
    assert "0a" in text
    assert "started" in text
    assert "菜单初始化" in text
