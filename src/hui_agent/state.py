from __future__ import annotations

from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any, Literal
from uuid import uuid4

from hui_agent.failure_policy import FailureScope, scope_for_exception

STEP_ORDER: tuple[str, ...] = (
    "0a",
    "0b",
    "1a",
    "1b",
    "2a",
    "2b",
    "2c",
    "2d",
    "3",
    "4",
    "5",
)

RunStatus = Literal["running", "success", "retrying", "skipped", "failed", "blocked"]


def next_step(completed_steps: list[str]) -> str:
    for step in STEP_ORDER:
        if step not in completed_steps:
            return step
    return "DONE"


@dataclass(frozen=True)
class HUITestState:
    thread_id: str
    menu_name: str
    task_id: str | None = None
    current_step: str = "0a"
    status: RunStatus = "running"
    completed_steps: list[str] = field(default_factory=list)
    config_path: str = ""
    case_plan_path: str = ""
    case_dir: str = ""
    sql_paths: dict[str, Any] = field(default_factory=dict)
    gate_decisions: dict[str, Any] = field(default_factory=dict)
    form_fields_summary: dict[str, Any] = field(default_factory=dict)
    shard: dict[str, Any] = field(default_factory=dict)
    preset_strategy: dict[str, Any] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    retry_counts: dict[str, int] = field(default_factory=dict)
    failure_scope: FailureScope | None = None
    failure_reason: str | None = None
    reports: list[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    @classmethod
    def new(cls, menu_name: str, task_id: str | None = None) -> "HUITestState":
        return cls(
            thread_id=f"hui-{uuid4().hex[:12]}",
            menu_name=menu_name,
            task_id=task_id,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "HUITestState":
        allowed = set(cls.__dataclass_fields__)
        return cls(**{key: value for key, value in payload.items() if key in allowed})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def mark_completed(self, step: str) -> "HUITestState":
        completed = list(self.completed_steps)
        if step not in completed:
            completed.append(step)
        return replace(
            self,
            completed_steps=completed,
            current_step=next_step(completed),
            status="success" if next_step(completed) == "DONE" else "running",
            failure_scope=None,
            failure_reason=None,
            updated_at=datetime.now().isoformat(),
        )

    def with_paths(
        self,
        *,
        config_path: str = "",
        case_plan_path: str = "",
        case_dir: str = "",
    ) -> "HUITestState":
        return replace(
            self,
            config_path=config_path or self.config_path,
            case_plan_path=case_plan_path or self.case_plan_path,
            case_dir=case_dir or self.case_dir,
            updated_at=datetime.now().isoformat(),
        )

    def merge(self, **updates: Any) -> "HUITestState":
        return replace(self, **updates, updated_at=datetime.now().isoformat())

    def retry_from(self, step: str) -> "HUITestState":
        if step not in STEP_ORDER:
            raise ValueError(f"Unknown step: {step}")
        index = STEP_ORDER.index(step)
        retry_counts = dict(self.retry_counts)
        retry_counts[step] = retry_counts.get(step, 0) + 1
        return replace(
            self,
            completed_steps=[s for s in self.completed_steps if STEP_ORDER.index(s) < index],
            current_step=step,
            status="retrying",
            retry_counts=retry_counts,
            failure_scope=None,
            failure_reason=None,
            updated_at=datetime.now().isoformat(),
        )

    def record_failure(
        self,
        step: str,
        message: str,
        *,
        scope: FailureScope = "menu",
    ) -> "HUITestState":
        errors = list(self.errors)
        errors.append(
            {
                "step": step,
                "message": message,
                "timestamp": datetime.now().isoformat(),
            }
        )
        status: RunStatus = "blocked" if scope == "environment" else "failed"
        return replace(
            self,
            current_step=step,
            status=status,
            errors=errors,
            failure_scope=scope,
            failure_reason=message,
            updated_at=datetime.now().isoformat(),
        )

    def record_exception(self, step: str, exc: Exception) -> "HUITestState":
        return self.record_failure(step, str(exc), scope=scope_for_exception(exc))
