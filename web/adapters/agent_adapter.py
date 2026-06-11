"""Adapter that bridges the web layer to the existing agent runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from hui_agent.cli import run_menu_runtime


class AgentAdapter:
    """Thin wrapper around ``run_menu_runtime`` for web callers.

    Accepts the same *skill_root / runs_root / checkpoint_db* knobs that the
    CLI exposes, executes a single menu, and returns a plain ``dict`` suitable
    for an HTTP JSON response.
    """

    def __init__(
        self,
        *,
        skill_root: str | None = None,
        runs_root: str = "runs",
        checkpoint_db: str = "runs/checkpoints.sqlite",
    ) -> None:
        self.skill_root = skill_root
        self.runs_root = runs_root
        self.checkpoint_db = checkpoint_db

    def run_menu(self, menu_name: str) -> dict[str, Any]:
        """Execute *menu_name* and return the result as a JSON-serialisable dict.

        The dict mirrors the fields returned by ``HUITestState.to_dict()`` but
        is filtered to the subset that the API contract requires.
        """
        state = run_menu_runtime(
            menu_name,
            skill_root=self.skill_root,
            runs_root=self.runs_root,
            checkpoint_db=self.checkpoint_db,
        )
        return _state_to_response(state)


def _state_to_response(state: Any) -> dict[str, Any]:
    """Convert a ``HUITestState`` into the API response shape."""
    result_path = str(Path(state.runs_root) / state.thread_id / "result.json") if hasattr(state, "runs_root") else ""
    # Derive result_path from RunStore convention when runs_root is not on state.
    if not result_path:
        result_path = str(Path("runs") / state.thread_id / "result.json")
    return {
        "task_id": state.thread_id,
        "menu_name": state.menu_name,
        "status": state.status,
        "current_step": state.current_step,
        "completed_steps": list(state.completed_steps),
        "failure_scope": state.failure_scope,
        "failure_reason": state.failure_reason,
        "result_path": result_path,
    }
