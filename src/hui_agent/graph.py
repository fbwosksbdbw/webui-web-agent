from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from hui_agent.case_plan_io import load_case_plan_into_state, sync_case_plan_to_disk
from hui_agent.state import HUITestState, STEP_ORDER

# Steps whose subprocess scripts read case_plan.json from disk.
# The wrapper will sync state.case_plan to disk *before* these steps run.
# Step 1b is excluded: its handler manages its own sync/write-back/reload cycle.
_STEPS_NEEDING_DISK_SYNC: frozenset[str] = frozenset()

# Steps whose subprocess scripts may write back to case_plan.json.
# The wrapper will reload state.case_plan from disk *after* these steps run.
# Step 1b is excluded: its handler reloads internally via _load_json.
_STEPS_WITH_WRITEBACK: frozenset[str] = frozenset()

StepHandler = Callable[[HUITestState], HUITestState]


class HUIWorkflow:
    def __init__(
        self,
        *,
        step_handlers: dict[str, StepHandler],
        step_order: Iterable[str] = STEP_ORDER,
    ) -> None:
        self.step_handlers = step_handlers
        self.step_order = tuple(step_order)

    def invoke(
        self,
        state: HUITestState | dict[str, Any],
        config: dict[str, Any] | None = None,
    ) -> HUITestState:
        del config
        current = HUITestState.from_dict(state) if isinstance(state, dict) else state
        for step in self.step_order:
            if step in current.completed_steps:
                continue
            # Sync case_plan to disk before subprocesses that read it
            if step in _STEPS_NEEDING_DISK_SYNC:
                sync_case_plan_to_disk(current)
            handler = self.step_handlers[step]
            try:
                current = handler(current)
            except Exception as exc:  # noqa: BLE001 - node boundary must capture failures
                return current.record_exception(step, exc)
            # Reload case_plan from disk if subprocess may have written back
            if step in _STEPS_WITH_WRITEBACK:
                current = load_case_plan_into_state(current)
            if current.status in {"skipped", "failed", "blocked"}:
                return current
        return current.merge(current_step="DONE", status="success")


def build_workflow(
    *,
    step_handlers: dict[str, StepHandler],
    step_order: Iterable[str] = STEP_ORDER,
) -> HUIWorkflow:
    return HUIWorkflow(step_handlers=step_handlers, step_order=step_order)


def build_langgraph(
    *,
    step_handlers: dict[str, StepHandler],
    step_order: Iterable[str] = STEP_ORDER,
    checkpointer: Any | None = None,
) -> Any:
    from langgraph.graph import END, START, StateGraph

    order = tuple(step_order)
    graph = StateGraph(dict)

    for step in order:
        graph.add_node(step, _wrap_step(step, step_handlers[step], order))

    if order:
        graph.add_edge(START, order[0])
        for current, following in zip(order, order[1:]):
            graph.add_edge(current, following)
        graph.add_edge(order[-1], END)
    else:
        graph.add_edge(START, END)

    return graph.compile(checkpointer=checkpointer)


def _wrap_step(
    step: str,
    handler: StepHandler,
    graph_order: tuple[str, ...],
) -> Callable[[dict[str, Any]], dict[str, Any]]:
    def node(payload: dict[str, Any]) -> dict[str, Any]:
        state = HUITestState.from_dict(payload)
        if step in state.completed_steps or state.status in {"skipped", "failed", "blocked"}:
            return state.to_dict()
        # Sync case_plan to disk before subprocesses that read it
        if step in _STEPS_NEEDING_DISK_SYNC:
            sync_case_plan_to_disk(state)
        try:
            next_state = handler(state)
        except Exception as exc:  # noqa: BLE001 - graph node boundary records failures
            next_state = state.record_exception(step, exc)
        # Reload case_plan from disk if subprocess may have written back
        if step in _STEPS_WITH_WRITEBACK:
            next_state = load_case_plan_into_state(next_state)
        if next_state.status not in {"skipped", "failed", "blocked"} and all(item in next_state.completed_steps for item in graph_order):
            next_state = next_state.merge(current_step="DONE", status="success")
        return next_state.to_dict()

    return node
