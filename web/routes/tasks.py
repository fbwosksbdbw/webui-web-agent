"""Task execution API routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


class RunRequest(BaseModel):
    menu_name: str = Field(..., min_length=1, description="Name of the menu to execute")


class RunResponse(BaseModel):
    task_id: str
    menu_name: str
    status: str
    current_step: str
    completed_steps: list[str]
    failure_scope: str | None = None
    failure_reason: str | None = None
    result_path: str


# The adapter is injected at app startup (see web/main.py).
_adapter = None


def set_adapter(adapter) -> None:  # noqa: ANN001
    global _adapter  # noqa: PLW0603
    _adapter = adapter


@router.post("/run", response_model=RunResponse)
def run_task(req: RunRequest) -> RunResponse:
    """Execute a single menu through the full agent workflow.

    This is a **synchronous** call — it blocks until the agent finishes.
    Typical execution time is 3-10 minutes per menu.
    """
    if _adapter is None:
        raise HTTPException(status_code=503, detail="AgentAdapter not initialised")
    try:
        result = _adapter.run_menu(req.menu_name)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Execution failed: {exc}") from exc
    return RunResponse(**result)
