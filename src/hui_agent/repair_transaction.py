from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Callable, Iterable


DEFAULT_CASE_PLAN_KEYS = ("insert_plans",)


def snapshot_repair_targets(
    case_plan: dict,
    case_plan_dir: Path,
    *,
    paths: Iterable[Path],
    case_plan_keys: Iterable[str] = DEFAULT_CASE_PLAN_KEYS,
    label_path: Callable[[Path, Path], str] | None = None,
) -> dict:
    files = []
    for path in sorted({Path(item).resolve() for item in paths}, key=lambda item: str(item)):
        existed = path.exists() and path.is_file()
        files.append(
            {
                "path": path,
                "file": label_path(path, case_plan_dir) if label_path else str(path),
                "existed": existed,
                "content": path.read_text(encoding="utf-8") if existed else "",
            }
        )
    case_plan_values = {
        key: {
            "present": key in case_plan,
            "value": deepcopy(case_plan.get(key)),
        }
        for key in case_plan_keys
    }
    return {"files": files, "case_plan": case_plan_values}


def restore_repair_snapshot(snapshot: dict, case_plan: dict) -> None:
    for item in snapshot.get("files", []):
        path = item.get("path")
        if not isinstance(path, Path):
            continue
        if item.get("existed"):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(str(item.get("content", "")), encoding="utf-8")
        elif path.exists() and path.is_file():
            path.unlink()
    for key, item in snapshot.get("case_plan", {}).items():
        if item.get("present"):
            case_plan[key] = deepcopy(item.get("value"))
        else:
            case_plan.pop(key, None)


def record_repair_restore(attempt: dict, snapshot: dict, case_plan: dict, *, status: str, reason: str) -> None:
    diff = repair_snapshot_diff(snapshot, case_plan)
    attempt["diff"] = diff
    attempt["restore"] = {
        "status": status,
        "reason": reason,
        "files": diff["files_changed"],
        "case_plan_keys": diff["case_plan_keys_changed"],
    }


def repair_snapshot_diff(snapshot: dict, case_plan: dict) -> dict:
    files_changed = []
    file_details = []
    for item in snapshot.get("files", []):
        path = item.get("path")
        if not isinstance(path, Path):
            continue
        existed_before = bool(item.get("existed"))
        existed_after = path.exists() and path.is_file()
        content_before = str(item.get("content", ""))
        content_after = path.read_text(encoding="utf-8") if existed_after else ""
        if existed_before == existed_after and content_before == content_after:
            continue
        label = str(item.get("file") or path)
        files_changed.append(label)
        file_details.append(
            {
                "file": label,
                "old_exists": existed_before,
                "new_exists": existed_after,
                "old_size": len(content_before) if existed_before else 0,
                "new_size": len(content_after) if existed_after else 0,
            }
        )
    case_plan_keys_changed = []
    for key, item in snapshot.get("case_plan", {}).items():
        present_before = bool(item.get("present"))
        present_after = key in case_plan
        value_before = item.get("value")
        value_after = case_plan.get(key)
        if present_before != present_after or value_before != value_after:
            case_plan_keys_changed.append(key)
    return {
        "files_changed": files_changed,
        "file_details": file_details,
        "case_plan_keys_changed": case_plan_keys_changed,
    }
