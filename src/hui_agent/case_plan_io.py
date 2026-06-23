"""Bridge between LangGraph state (source of truth) and case_plan.json on disk.

case_plan.json is kept on disk only for backward compatibility with
subprocess scripts (cdp_executor, sql_extractor, etc.) that read it
directly.  The authoritative copy lives in ``HUITestState.case_plan``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hui_agent.state import HUITestState


def sync_case_plan_to_disk(state: "HUITestState") -> None:
    """Write *state.case_plan* to the file at *state.case_plan_path*.

    Call this **before** any subprocess script that reads case_plan.json
    from disk.  Safe to call even when the path is empty (no-op).
    """
    if not state.case_plan_path:
        return
    path = Path(state.case_plan_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(state.case_plan, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_case_plan_into_state(state: "HUITestState") -> "HUITestState":
    """Read case_plan.json from disk and merge it into *state*.

    Used after a subprocess (e.g. cdp_executor --write-back) has mutated
    the file, or when resuming from a checkpoint that stored only the path.
    """
    import dataclasses

    if not state.case_plan_path:
        return state
    path = Path(state.case_plan_path)
    if path.exists():
        case_plan = json.loads(path.read_text(encoding="utf-8"))
        return dataclasses.replace(
            state,
            case_plan=case_plan,
        )
    return state


def update_case_plan(
    state: "HUITestState", updates: dict
) -> "HUITestState":
    """Shallow-merge *updates* into *state.case_plan* and return a new state.

    This is a convenience re-export of ``HUITestState.update_case_plan``
    so callers can use it as a free function without importing the class.
    """
    return state.update_case_plan(updates)
