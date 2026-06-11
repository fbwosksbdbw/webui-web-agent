from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def collect_repair_evidence(
    case_plan: dict[str, Any],
    case_plan_dir: Path,
    attempt_no: int,
    pytest_result: Any,
    config_path: str | None = None,
    db_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    logs = _collect_logs(case_plan_dir, attempt_no)
    failed_nodeids = _failed_nodeids(getattr(pytest_result, "stdout", ""))
    return {
        "attempt": attempt_no,
        "menu": case_plan.get("menu", {}),
        "pytest": {
            "returncode": getattr(pytest_result, "returncode", None),
            "stdout_tail": _tail(getattr(pytest_result, "stdout", "")),
            "stderr_tail": _tail(getattr(pytest_result, "stderr", "")),
            "failed_nodeids": failed_nodeids,
        },
        "query_log_tail": _read_tail(case_plan_dir / "query_log.txt"),
        "logs": logs,
        "presets": case_plan.get("presets", {}),
        "case_inputs": case_plan.get("case_inputs", {}),
        "expected_counts": case_plan.get("expected_counts", {}),
        "shard": case_plan.get("shard", {}),
        "preset_strategy": case_plan.get("preset_strategy", {}),
        "sql_files": _collect_sql_files(case_plan, case_plan_dir),
        "generated_tests": _collect_generated_tests(case_plan, case_plan_dir, failed_nodeids),
        "database_checks": case_plan.get("database_checks", []),
        "previous_repair_attempts": case_plan.get("repair_attempts", []),
        "table_schemas": _collect_table_schemas_for_evidence(case_plan),
        "db_snapshot": db_snapshot or {},
    }


def _collect_logs(case_plan_dir: Path, attempt_no: int) -> dict[str, str]:
    return {
        "main_query": _read_tail(case_plan_dir / "query_log.txt"),
        "sql_probe_joined": _join_log_files(case_plan_dir, case_plan_dir / "sql_probe" / "log_*.txt"),
        "repair_attempt": _join_log_files(
            case_plan_dir,
            case_plan_dir / "repair_logs" / f"attempt_{attempt_no:02d}" / "*.txt",
        ),
    }


def _join_log_files(case_plan_dir: Path, pattern: Path, limit: int = 16000) -> str:
    parts: list[str] = []
    for path in sorted(pattern.parent.glob(pattern.name)):
        if not path.is_file():
            continue
        try:
            label = path.relative_to(case_plan_dir).as_posix()
        except ValueError:
            label = str(path)
        content = _read_tail(path, limit=limit)
        if not content:
            continue
        parts.append(f"=== {label} ===\n{content}")
    return _tail("\n\n".join(parts), limit=limit)


def _collect_sql_files(case_plan: dict[str, Any], case_plan_dir: Path) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for key, value in (case_plan.get("sql_paths") or {}).items():
        if not isinstance(value, str) or not value.lower().endswith(".sql"):
            continue
        path = Path(value)
        if not path.is_absolute():
            path = (case_plan_dir / path).resolve()
        result.append(
            {
                "kind": key,
                "path": str(path),
                "content_tail": _read_tail(path, limit=12000),
            }
        )
    return result


def _collect_generated_tests(case_plan: dict[str, Any], case_plan_dir: Path, failed_nodeids: list[str]) -> list[dict[str, Any]]:
    failed_paths = {_nodeid_path_key(nodeid) for nodeid in failed_nodeids}
    result: list[dict[str, Any]] = []
    for value in case_plan.get("generated_tests", []):
        if not isinstance(value, str):
            continue
        path = Path(value)
        if not path.is_absolute():
            path = (case_plan_dir / path).resolve()
        path_key = _nodeid_path_key(str(path))
        result.append(
            {
                "path": str(path),
                "case_type": _infer_case_type_from_path(path),
                "is_failed": _is_failed_test_path(path_key, failed_paths),
                "content_tail": _read_tail(path, limit=16000),
            }
        )
    return result


def _failed_nodeids(stdout: object) -> list[str]:
    result: list[str] = []
    for line in str(stdout or "").splitlines():
        stripped = line.strip()
        if not stripped.startswith("FAILED "):
            continue
        nodeid = stripped[len("FAILED "):]
        if " - " in nodeid:
            nodeid = nodeid.split(" - ", 1)[0]
        else:
            nodeid = nodeid.split()[0]
        nodeid = nodeid.strip()
        if nodeid and nodeid not in result:
            result.append(nodeid)
    return result


def _nodeid_path_key(nodeid: str) -> str:
    path_part = str(nodeid).split("::", 1)[0]
    return path_part.replace("\\", "/").lower()


def _is_failed_test_path(path_key: str, failed_paths: set[str]) -> bool:
    for failed in failed_paths:
        if path_key == failed or path_key.endswith("/" + failed) or failed.endswith("/" + Path(path_key).name.lower()):
            return True
    return False


def _infer_case_type_from_path(path: Path) -> str:
    stem = path.stem.lower()
    for case_type in ("queryall", "querysingle", "querymulti", "reset", "preview", "branch", "headquarter"):
        if case_type in stem:
            return case_type
    match = re.search(r"test_[^_]+_(.+)$", stem)
    return match.group(1) if match else ""


def _collect_table_schemas_for_evidence(case_plan: dict[str, Any]) -> dict[str, Any]:
    """从 case_plan 的 insert_plans 中提取涉及表的列信息。"""
    schemas: dict[str, Any] = {}
    for plan in case_plan.get("insert_plans", []):
        table = plan.get("physical_table") or plan.get("table", "")
        if not table or table in schemas:
            continue
        columns = [c.get("name", "") for c in plan.get("columns", []) if isinstance(c, dict) and c.get("name")]
        schemas[table] = {"columns_in_insert": columns}
    return schemas


def _read_tail(path: Path, limit: int = 8000) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return _tail(path.read_text(encoding="utf-8", errors="replace"), limit=limit)


def _tail(value: object, limit: int = 8000) -> str:
    text = str(value or "")
    return text[-limit:]
