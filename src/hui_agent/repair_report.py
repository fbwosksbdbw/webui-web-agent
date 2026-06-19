from __future__ import annotations

from pathlib import Path


def repair_overview_lines(sql_repair_attempts: list, repair_attempts: list) -> list[str]:
    lines: list[str] = []
    if sql_repair_attempts:
        last_sql_attempt = sql_repair_attempts[-1]
        lines.append(f"- SQL dry-run 修复尝试次数: {len(sql_repair_attempts)}")
        lines.append(f"- 最后 SQL 修复状态: {last_sql_attempt.get('status', 'unknown')}")
    if repair_attempts:
        last_attempt = repair_attempts[-1]
        patched_count = sum(1 for attempt in repair_attempts if attempt.get("actions"))
        lines.append(f"- 修复尝试次数: {len(repair_attempts)}")
        lines.append(f"- SQL patch 轮次: {patched_count}")
        lines.append(f"- 最后修复状态: {last_attempt.get('status', 'unknown')}")
    return lines


def repair_detail_lines(sql_repair_attempts: list, repair_attempts: list) -> list[str]:
    lines: list[str] = []
    if sql_repair_attempts:
        lines.append("")
        lines.append("## SQL dry-run 修复过程")
        for attempt in sql_repair_attempts:
            lines.extend(_render_repair_attempt_block(attempt, validation_key="validation"))
    if repair_attempts:
        lines.append("")
        lines.append("## pytest 修复过程")
        for attempt in repair_attempts:
            lines.extend(_render_repair_attempt_block(attempt, validation_key="verification"))
    return lines


def _render_repair_attempt_block(attempt: dict, *, validation_key: str) -> list[str]:
    lines = [_render_repair_attempt_line(attempt)]
    validation = attempt.get(validation_key) or {}
    validation_text = _format_repair_validation(validation)
    if validation_text:
        lines.append(f"  - 验证: {validation_text}")
    restore = attempt.get("restore") or {}
    if restore:
        restore_text = _format_repair_restore(restore)
        if restore_text:
            lines.append(f"  - 回滚: {restore_text}")
    changed = _format_repair_changed_targets(attempt)
    if changed:
        lines.append(f"  - 变更: {changed}")
    return lines


def _render_repair_attempt_line(attempt: dict) -> str:
    attempt_no = attempt.get("attempt", "?")
    status = attempt.get("status", "unknown")
    pytest_returncode = (attempt.get("pytest") or {}).get("returncode", "")
    sql_validation = attempt.get("sql_validation") or {}
    llm = attempt.get("llm_decision") or {}
    llm_summary = llm.get("summary") or attempt.get("llm_error") or ""
    actions = ",".join(action.get("type", "") for action in attempt.get("actions", []) if action.get("type")) or "none"
    trigger = f", sql={sql_validation.get('status', '')}" if sql_validation else ""
    return (
        f"- 第{attempt_no}轮: status={status}, pytest={pytest_returncode}{trigger}, "
        f"llm={llm_summary}, actions={actions}"
    )


def _format_repair_validation(validation: dict) -> str:
    if not isinstance(validation, dict) or not validation:
        return ""
    kind = validation.get("kind", "")
    if kind == "sql_dry_run":
        parts = [
            f"kind={kind}",
            f"status={validation.get('status', 'unknown')}",
            f"attempt={validation.get('attempt', '')}",
            f"failed={validation.get('failed_count', 0)}",
        ]
        if validation.get("failure_reason"):
            parts.append(f"reason={validation.get('failure_reason')}")
        return " ".join(str(part) for part in parts if str(part).strip())
    if kind == "pytest":
        parts = [
            f"kind={kind}",
            f"phase={validation.get('phase', '')}",
            f"returncode={validation.get('returncode', '')}",
        ]
        return " ".join(str(part) for part in parts if str(part).strip())
    return ", ".join(f"{key}={value}" for key, value in validation.items() if value not in (None, ""))


def _format_repair_restore(restore: dict) -> str:
    if not isinstance(restore, dict) or not restore:
        return ""
    parts = [
        f"status={restore.get('status', 'unknown')}",
        f"reason={restore.get('reason', '')}",
    ]
    files = _format_short_names(restore.get("files", []))
    if files:
        parts.append(f"files={files}")
    case_plan_keys = _format_short_names(restore.get("case_plan_keys", []))
    if case_plan_keys:
        parts.append(f"case_plan={case_plan_keys}")
    return " ".join(str(part) for part in parts if str(part).strip() and not str(part).endswith("="))


def _format_repair_changed_targets(attempt: dict) -> str:
    diff = attempt.get("diff") or {}
    if not isinstance(diff, dict):
        return ""
    parts = []
    files = _format_short_names(diff.get("files_changed", []))
    if files:
        parts.append(f"files={files}")
    case_plan_keys = _format_short_names(diff.get("case_plan_keys_changed", []))
    if case_plan_keys:
        parts.append(f"case_plan={case_plan_keys}")
    return " ".join(parts)


def _format_short_names(values: object, *, limit: int = 5) -> str:
    if not isinstance(values, list):
        return ""
    names = [Path(str(value)).name for value in values if str(value or "").strip()]
    if not names:
        return ""
    head = names[:limit]
    suffix = f",+{len(names) - limit} more" if len(names) > limit else ""
    return ",".join(head) + suffix
