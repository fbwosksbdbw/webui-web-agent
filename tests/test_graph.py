from langgraph.checkpoint.memory import InMemorySaver

from hui_agent.graph import build_langgraph, build_workflow
from hui_agent.state import HUITestState


def test_workflow_runs_steps_in_order_until_complete():
    calls = []

    def make_step(step_id):
        def handler(state):
            calls.append(step_id)
            return state.mark_completed(step_id)

        return handler

    workflow = build_workflow(
        step_handlers={
            "0a": make_step("0a"),
            "0b": make_step("0b"),
        },
        step_order=("0a", "0b"),
    )

    result = workflow.invoke(HUITestState.new("基金分红查询"))

    assert calls == ["0a", "0b"]
    assert result.completed_steps == ["0a", "0b"]
    assert result.current_step == "DONE"


def test_workflow_records_node_failure_and_stops():
    def ok(state):
        return state.mark_completed("0a")

    def fail(state):
        raise RuntimeError("CDP unavailable")

    workflow = build_workflow(
        step_handlers={"0a": ok, "0b": fail},
        step_order=("0a", "0b"),
    )

    result = workflow.invoke(HUITestState.new("基金分红查询"))

    assert result.completed_steps == ["0a"]
    assert result.current_step == "0b"
    assert result.status == "failed"
    assert result.failure_scope == "menu"
    assert result.errors[-1]["step"] == "0b"
    assert "CDP unavailable" in result.errors[-1]["message"]


def test_build_langgraph_compiles_checkpointed_state_graph():
    def ok(state):
        return state.mark_completed("0a")

    graph = build_langgraph(
        step_handlers={"0a": ok},
        step_order=("0a",),
        checkpointer=InMemorySaver(),
    )

    result = graph.invoke(
        HUITestState.new("基金分红查询").to_dict(),
        {"configurable": {"thread_id": "thread-1"}},
    )

    assert result["completed_steps"] == ["0a"]
    assert result["current_step"] == "DONE"


def test_langgraph_does_not_overwrite_terminal_menu_failure_with_success():
    def fail_after_report(state):
        return (
            state.mark_completed("5")
            .record_failure("5", "pytest verification failed after repair attempts", scope="menu")
        )

    graph = build_langgraph(
        step_handlers={"5": fail_after_report},
        step_order=("5",),
        checkpointer=InMemorySaver(),
    )

    result = graph.invoke(
        HUITestState.new("基金分红查询").to_dict(),
        {"configurable": {"thread_id": "thread-failed-report"}},
    )

    assert result["completed_steps"] == ["5"]
    assert result["status"] == "failed"
    assert result["failure_reason"] == "pytest verification failed after repair attempts"
