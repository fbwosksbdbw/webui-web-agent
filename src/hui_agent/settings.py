from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class AgentSettings:
    skill_root: Path
    runs_root: Path
    checkpoint_db: Path
    serial_browser: bool = True
    default_model: str = "gpt-4.1"
    max_step_retries: int = 3

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AgentSettings":
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        runtime: dict[str, Any] = data.get("runtime", {})
        models: dict[str, Any] = data.get("models", {})
        limits: dict[str, Any] = data.get("limits", {})
        base = Path(path).resolve().parent
        return cls(
            skill_root=_resolve_path(base, os.getenv("HUI_SKILL_ROOT") or runtime.get("skill_root", "./knowledge")),
            runs_root=_resolve_path(base, os.getenv("HUI_RUNS_ROOT") or runtime.get("runs_root", "./runs")),
            checkpoint_db=_resolve_path(
                base,
                os.getenv("HUI_CHECKPOINT_DB") or runtime.get("checkpoint_db", "./runs/checkpoints.sqlite"),
            ),
            serial_browser=bool(runtime.get("serial_browser", True)),
            default_model=os.getenv("HUI_AGENT_MODEL") or models.get("default", "gpt-4.1"),
            max_step_retries=int(limits.get("max_step_retries", 3)),
        )


def _resolve_path(base: Path, value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else base / path
