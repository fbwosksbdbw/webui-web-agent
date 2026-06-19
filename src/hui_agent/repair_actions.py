from __future__ import annotations

import os
import re
from pathlib import Path

from hui_agent.insert_plan import InsertColumn, InsertPlan, render_sql


def has_applied_repair_action(actions: list[dict]) -> bool:
    for action in actions:
        status = action.get("status")
        if status == "patched":
            return True
        if status is None and action.get("type"):
            return True
    return False


def repair_action_event_label(action: dict) -> str:
    label = str(action.get("type", ""))
    status = str(action.get("status", ""))
    if status:
        label = f"{label}:{status}"
    file = str(action.get("file", ""))
    if file:
        label = f"{label}@{file}"
    return label


def apply_llm_repair_actions(case_plan: dict, case_plan_dir: Path, decision: dict) -> list[dict]:
    actions: list[dict] = []
    for suggested in decision.get("actions", []) if isinstance(decision, dict) else []:
        if not isinstance(suggested, dict):
            continue
        action_type = suggested.get("type")
        if action_type == "patch_sql":
            action = _apply_llm_patch_sql_action(case_plan, case_plan_dir, suggested)
        elif action_type == "regenerate_case":
            action = _apply_llm_regenerate_case_action(case_plan, case_plan_dir, suggested)
        elif action_type == "regenerate_sql":
            action = _apply_llm_regenerate_sql_action(case_plan, case_plan_dir, suggested)
        elif action_type == "mark_unfixable":
            action = {
                "type": "llm_mark_unfixable",
                "reason": suggested.get("reason", ""),
                "status": "recorded",
            }
        else:
            action = {}
        if action:
            actions.append(action)
    return actions


def repair_sql_preset_mismatches(case_plan: dict, case_plan_dir: Path) -> list[dict]:
    replacements = _preset_sql_replacements(case_plan)
    if not replacements:
        return []
    actions: list[dict] = []
    for sql_path in repairable_sql_paths(case_plan, case_plan_dir):
        if not sql_path.exists() or not sql_path.is_file():
            continue
        original = sql_path.read_text(encoding="utf-8")
        repaired = original
        changes: list[dict] = []
        for column, new_value in replacements.items():
            repaired, column_changes = _replace_sql_column_values(repaired, column, new_value)
            changes.extend(column_changes)
        if repaired == original:
            continue
        sql_path.parent.mkdir(parents=True, exist_ok=True)
        sql_path.write_text(repaired, encoding="utf-8")
        actions.append(
            {
                "type": "patch_sql",
                "file": artifact_path(sql_path, case_plan_dir),
                "reason": "SQL literal values differ from case_plan presets",
                "changes": changes,
            }
        )
    return actions


def repair_duplicate_insert_keys(case_plan: dict, case_plan_dir: Path) -> list[dict]:
    plan_dicts = case_plan.get("insert_plans", [])
    if not isinstance(plan_dicts, list):
        return []
    actions: list[dict] = []
    changed = False
    for plan in plan_dicts:
        if not isinstance(plan, dict):
            continue
        rows = plan.get("rows")
        if not isinstance(rows, list) or len(rows) < 2:
            continue
        key_columns = _delete_where_key_columns(str(plan.get("delete_where", "")))
        if not key_columns:
            continue
        repaired_rows, changes = _dedupe_insert_plan_rows(rows, key_columns)
        if not changes:
            continue
        changed = True
        plan["rows"] = repaired_rows if len(repaired_rows) > 1 else None
        plan["columns"] = repaired_rows[0]
        plan["delete_where"] = _delete_where_from_row_dicts(repaired_rows, key_columns)
        actions.append(
            {
                "type": "patch_duplicate_insert_keys",
                "table": plan.get("physical_table") or plan.get("table"),
                "reason": "multi-row INSERT reused the same key columns",
                "changes": changes,
            }
        )
    if not changed:
        return []
    case_plan["insert_plans"] = plan_dicts
    _rewrite_sql_from_insert_plan_dicts(case_plan, case_plan_dir)
    return actions


def repairable_sql_paths(case_plan: dict, case_plan_dir: Path) -> list[Path]:
    result: list[Path] = []
    for value in (case_plan.get("sql_paths") or {}).values():
        if not isinstance(value, str) or not value.lower().endswith(".sql"):
            continue
        path = Path(resolve_artifact_path(value, case_plan_dir))
        if path not in result:
            result.append(path)
    return result


def allowed_generated_test_paths(case_plan: dict, case_plan_dir: Path) -> set[Path]:
    result: set[Path] = set()
    for value in case_plan.get("generated_tests", []):
        if not isinstance(value, str):
            continue
        result.add(Path(resolve_artifact_path(value, case_plan_dir)).resolve())
    return result


def artifact_path(path: Path, base_dir: Path) -> str:
    if not path.is_absolute():
        return path.as_posix()
    try:
        return Path(os.path.relpath(path, start=base_dir)).as_posix()
    except ValueError:
        return str(path)


def resolve_artifact_path(path: str, base_dir: Path) -> str:
    artifact = Path(path)
    if artifact.is_absolute():
        return str(artifact)
    return str((base_dir / artifact).resolve())


def _apply_llm_patch_sql_action(case_plan: dict, case_plan_dir: Path, suggested: dict) -> dict:
    sql_path = _resolve_repair_target_path(
        suggested.get("file", ""),
        case_plan_dir,
        _allowed_sql_repair_paths(case_plan, case_plan_dir),
    )
    if sql_path is None:
        return {
            "type": "llm_patch_sql",
            "status": "skipped",
            "reason": "target SQL file is not in case_plan.sql_paths",
            "requested_file": suggested.get("file", ""),
        }
    original = sql_path.read_text(encoding="utf-8")
    repaired = original
    replacements = _llm_text_replacements(suggested)
    applied: list[dict] = []
    for old, new in replacements:
        if not old or old not in repaired:
            continue
        count = repaired.count(old)
        repaired = repaired.replace(old, new)
        applied.append({"old": old, "new": new, "count": count})
    if repaired == original:
        return {
            "type": "llm_patch_sql",
            "status": "skipped",
            "file": artifact_path(sql_path, case_plan_dir),
            "reason": "no LLM replacement matched SQL content",
            "suggested_reason": suggested.get("reason", ""),
        }
    validation_error = _validate_llm_generated_sql(repaired)
    if validation_error:
        return {
            "type": "llm_patch_sql",
            "status": "skipped",
            "file": artifact_path(sql_path, case_plan_dir),
            "reason": validation_error,
            "suggested_reason": suggested.get("reason", ""),
            "changes": applied,
        }
    sql_path.parent.mkdir(parents=True, exist_ok=True)
    sql_path.write_text(repaired, encoding="utf-8")
    return {
        "type": "llm_patch_sql",
        "status": "patched",
        "file": artifact_path(sql_path, case_plan_dir),
        "reason": suggested.get("reason", ""),
        "changes": applied,
    }


def _apply_llm_regenerate_sql_action(case_plan: dict, case_plan_dir: Path, suggested: dict) -> dict:
    sql_path = _resolve_repair_target_path(
        suggested.get("file", ""),
        case_plan_dir,
        _allowed_sql_repair_paths(case_plan, case_plan_dir),
    )
    if sql_path is None:
        return {
            "type": "llm_regenerate_sql",
            "status": "skipped",
            "reason": "target SQL file is not in case_plan.sql_paths",
            "requested_file": suggested.get("file", ""),
        }
    content = str(suggested.get("content") or suggested.get("new_text") or "")
    validation_error = _validate_llm_generated_sql(content)
    if validation_error:
        return {
            "type": "llm_regenerate_sql",
            "status": "skipped",
            "file": artifact_path(sql_path, case_plan_dir),
            "reason": validation_error,
            "suggested_reason": suggested.get("reason", ""),
        }
    old_content = sql_path.read_text(encoding="utf-8") if sql_path.exists() else ""
    if content == old_content:
        return {
            "type": "llm_regenerate_sql",
            "status": "skipped",
            "file": artifact_path(sql_path, case_plan_dir),
            "reason": "generated SQL content is unchanged",
        }
    sql_path.parent.mkdir(parents=True, exist_ok=True)
    sql_path.write_text(content, encoding="utf-8")
    return {
        "type": "llm_regenerate_sql",
        "status": "patched",
        "file": artifact_path(sql_path, case_plan_dir),
        "reason": suggested.get("reason", ""),
        "old_size": len(old_content),
        "new_size": len(content),
    }


def _apply_llm_regenerate_case_action(case_plan: dict, case_plan_dir: Path, suggested: dict) -> dict:
    test_path = _resolve_repair_target_path(
        suggested.get("file", ""),
        case_plan_dir,
        allowed_generated_test_paths(case_plan, case_plan_dir),
    )
    if test_path is None:
        test_path = _find_generated_test_by_case_type(case_plan, case_plan_dir, str(suggested.get("case_type", "")))
    if test_path is None:
        return {
            "type": "llm_regenerate_case",
            "status": "skipped",
            "reason": "target pytest file is not in generated_tests",
            "requested_file": suggested.get("file", ""),
            "case_type": suggested.get("case_type", ""),
        }
    content = str(suggested.get("content") or suggested.get("new_text") or "")
    validation_error = _validate_llm_generated_pytest(content)
    if validation_error:
        return {
            "type": "llm_regenerate_case",
            "status": "skipped",
            "file": artifact_path(test_path, case_plan_dir),
            "reason": validation_error,
            "suggested_reason": suggested.get("reason", ""),
        }
    old_content = test_path.read_text(encoding="utf-8") if test_path.exists() else ""
    if content == old_content:
        return {
            "type": "llm_regenerate_case",
            "status": "skipped",
            "file": artifact_path(test_path, case_plan_dir),
            "reason": "generated content is unchanged",
        }
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(content, encoding="utf-8")
    return {
        "type": "llm_regenerate_case",
        "status": "patched",
        "file": artifact_path(test_path, case_plan_dir),
        "case_type": suggested.get("case_type", ""),
        "reason": suggested.get("reason", ""),
        "old_size": len(old_content),
        "new_size": len(content),
    }


def _llm_text_replacements(suggested: dict) -> list[tuple[str, str]]:
    replacements: list[tuple[str, str]] = []
    old_text = str(suggested.get("old_text") or "")
    new_text = str(suggested.get("new_text") or "")
    if old_text and new_text:
        replacements.append((old_text, new_text))
    for change in suggested.get("changes", []):
        if not isinstance(change, dict):
            continue
        old = change.get("old_text", change.get("old", ""))
        new = change.get("new_text", change.get("new", ""))
        if old is None or new is None:
            continue
        replacements.append((str(old), str(new)))
    return replacements


def _allowed_sql_repair_paths(case_plan: dict, case_plan_dir: Path) -> set[Path]:
    return {path.resolve() for path in repairable_sql_paths(case_plan, case_plan_dir)}


def _resolve_repair_target_path(requested: str, case_plan_dir: Path, allowed: set[Path]) -> Path | None:
    if not allowed:
        return None
    if not requested:
        return next(iter(sorted(allowed, key=lambda path: str(path)))) if len(allowed) == 1 else None
    path = Path(str(requested))
    if not path.is_absolute():
        path = case_plan_dir / path
    resolved = path.resolve()
    return resolved if resolved in allowed else None


def _find_generated_test_by_case_type(case_plan: dict, case_plan_dir: Path, case_type: str) -> Path | None:
    if not case_type:
        return None
    for path in allowed_generated_test_paths(case_plan, case_plan_dir):
        lowered = path.name.lower()
        if case_type.lower() in lowered:
            return path
    return None


def _validate_llm_generated_pytest(content: str) -> str:
    if not content.strip():
        return "LLM regenerate_case did not provide content"
    if "def test_" not in content:
        return "LLM regenerate_case content does not define a pytest test"
    if "assert True" in content or "assert 1 == 1" in content:
        return "LLM regenerate_case content contains a fake assertion"
    return ""


def _validate_llm_generated_sql(content: str) -> str:
    sql = content.strip()
    if not sql:
        return "LLM regenerate_sql did not provide content"
    if not re.search(r"\b(INSERT|DELETE|UPDATE|COMMIT)\b", sql, flags=re.IGNORECASE):
        return "LLM regenerate_sql content does not contain DML"
    forbidden = re.search(
        r"\b(DROP|ALTER|TRUNCATE|CREATE|RENAME|GRANT|REVOKE|EXEC|CALL)\b",
        sql,
        flags=re.IGNORECASE,
    )
    if forbidden:
        return f"LLM regenerate_sql content contains forbidden statement: {forbidden.group(1).upper()}"
    insert_count_error = _validate_insert_value_counts(sql)
    if insert_count_error:
        return insert_count_error
    return ""


def _validate_insert_value_counts(sql: str) -> str:
    pattern = re.compile(
        r"\bINSERT\s+INTO\s+[^\(;]+\((?P<columns>.*?)\)\s+VALUES\s*(?P<values>.*?);",
        flags=re.IGNORECASE | re.DOTALL,
    )
    for match in pattern.finditer(sql):
        columns = _split_sql_csv(match.group("columns"))
        if not columns:
            continue
        rows = _extract_sql_value_rows(match.group("values"))
        for index, row in enumerate(rows, start=1):
            values = _split_sql_csv(row)
            if len(values) != len(columns):
                return (
                    "LLM regenerate_sql INSERT value count mismatch: "
                    f"expected {len(columns)} values but row {index} has {len(values)}"
                )
    return ""


def _delete_where_key_columns(delete_where: str) -> list[str]:
    columns: list[str] = []
    for match in re.finditer(r"\b([A-Za-z_][A-Za-z0-9_]*)\b\s*=", delete_where):
        column = match.group(1)
        if column not in columns:
            columns.append(column)
    return columns


def _dedupe_insert_plan_rows(rows: list, key_columns: list[str]) -> tuple[list[list[dict]], list[dict]]:
    result: list[list[dict]] = []
    seen: set[tuple[str, ...]] = set()
    changes: list[dict] = []
    for row in rows:
        if not isinstance(row, list):
            continue
        row_dicts = [dict(column) for column in row if isinstance(column, dict)]
        identity = _row_dict_identity(row_dicts, key_columns)
        if identity not in seen:
            seen.add(identity)
            result.append(row_dicts)
            continue
        distinct_row = _make_row_dict_identity_distinct(row_dicts, key_columns, seen)
        if distinct_row:
            new_identity = _row_dict_identity(distinct_row, key_columns)
            seen.add(new_identity)
            result.append(distinct_row)
            changes.append({"action": "make_distinct", "old_key": list(identity), "new_key": list(new_identity)})
            continue
        changes.append({"action": "collapse_duplicate", "key": list(identity)})
    return result, changes


def _row_dict_identity(row: list[dict], key_columns: list[str]) -> tuple[str, ...]:
    values = {str(column.get("name")): str(column.get("value", "")) for column in row}
    return tuple(values.get(column, "") for column in key_columns)


def _make_row_dict_identity_distinct(
    row: list[dict],
    key_columns: list[str],
    seen: set[tuple[str, ...]],
) -> list[dict] | None:
    by_name = {str(column.get("name")): column for column in row}
    for candidate in ("serial_no", "global_serial_no", "ex_serial", "cfm_no", "request_no", "apply_no", "client_no"):
        if candidate not in key_columns or candidate not in by_name:
            continue
        current = str(by_name[candidate].get("value", ""))
        next_value = _increment_tail_number(current)
        for _ in range(20):
            replacements = {candidate: next_value}
            if candidate == "serial_no" and "global_serial_no" in by_name:
                replacements["global_serial_no"] = next_value
            patched = [
                {**column, "value": replacements.get(str(column.get("name")), column.get("value", ""))}
                for column in row
            ]
            identity = _row_dict_identity(patched, key_columns)
            if identity not in seen:
                return patched
            next_value = _increment_tail_number(next_value)
    return None


def _delete_where_from_row_dicts(rows: list[list[dict]], key_columns: list[str]) -> str:
    clauses: list[str] = []
    for row in rows:
        values = {str(column.get("name")): str(column.get("value", "")) for column in row}
        clause = " AND ".join(f"{column} = '{_escape_sql_literal(values[column])}'" for column in key_columns if column in values)
        if clause and clause not in clauses:
            clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return " OR ".join(f"({clause})" for clause in clauses)


def _rewrite_sql_from_insert_plan_dicts(case_plan: dict, case_plan_dir: Path) -> None:
    plans = [_insert_plan_from_dict(plan) for plan in case_plan.get("insert_plans", []) if isinstance(plan, dict)]
    sql_paths = _resolve_path_map(case_plan.get("sql_paths", {}), case_plan_dir)
    base_plans = [plan for plan in plans if plan.role == "base"]
    preset_plans = [plan for plan in plans if plan.role != "base"]
    if sql_paths.get("base_sql"):
        _write_text(Path(sql_paths["base_sql"]), render_sql(base_plans, include_db_prefix=False))
    if sql_paths.get("preset_sql"):
        _write_text(Path(sql_paths["preset_sql"]), render_sql(preset_plans, include_db_prefix=False))


def _insert_plan_from_dict(plan: dict) -> InsertPlan:
    rows = plan.get("rows")
    parsed_rows = None
    if isinstance(rows, list):
        parsed_rows = [[_insert_column_from_dict(column) for column in row] for row in rows if isinstance(row, list)]
    return InsertPlan(
        table=str(plan.get("table", "")),
        physical_table=str(plan.get("physical_table") or plan.get("table", "")),
        role=str(plan.get("role", "business")),
        columns=[_insert_column_from_dict(column) for column in plan.get("columns", []) if isinstance(column, dict)],
        delete_where=str(plan.get("delete_where", "")),
        rows=parsed_rows,
        db=str(plan.get("db", "")),
    )


def _insert_column_from_dict(column: dict) -> InsertColumn:
    return InsertColumn(
        name=str(column.get("name", "")),
        value=str(column.get("value", "")),
        source=str(column.get("source", "")),
    )


def _preset_sql_replacements(case_plan: dict) -> dict[str, str]:
    presets = case_plan.get("presets", {})
    mapping = {
        "prd_code": "prd_code",
        "ta_code": "ta_code",
        "prd_name": "prd_name",
        "customer_id": "bank_acc",
        "customer_id_type": "id_kind",
    }
    replacements: dict[str, str] = {}
    for preset_key, column in mapping.items():
        value = presets.get(preset_key)
        if not value:
            continue
        replacements[column] = _clean_sql_repair_value(value)
    return replacements


def _clean_sql_repair_value(value: object) -> str:
    text = str(value or "")
    if ":" in text:
        return text.split(":", 1)[0]
    return text


def _replace_sql_column_values(sql: str, column: str, new_value: str) -> tuple[str, list[dict]]:
    changes: list[dict] = []

    def replace_assignment(match: re.Match[str]) -> str:
        old_value = match.group("value")
        if old_value == new_value:
            return match.group(0)
        changes.append({"column": column, "old": old_value, "new": new_value, "pattern": "assignment"})
        return f"{match.group('prefix')}'{_escape_sql_literal(new_value)}'"

    assignment_pattern = re.compile(
        rf"(?P<prefix>\b{re.escape(column)}\b\s*=\s*)'(?P<value>[^']*)'",
        flags=re.IGNORECASE,
    )
    sql = assignment_pattern.sub(replace_assignment, sql)

    insert_pattern = re.compile(
        r"(?P<head>INSERT\s+INTO\s+[^\(]+\((?P<columns>[^)]*)\)\s+VALUES\s*)"
        r"(?P<values>\((?P<items>[^;]*)\))",
        flags=re.IGNORECASE | re.DOTALL,
    )

    def replace_insert(match: re.Match[str]) -> str:
        columns = [item.strip().strip("`").lower() for item in match.group("columns").split(",")]
        if column.lower() not in columns:
            return match.group(0)
        index = columns.index(column.lower())
        rows = _split_sql_value_rows(match.group("values"))
        if not rows:
            return match.group(0)
        rendered_rows: list[str] = []
        changed = False
        for row in rows:
            values = _split_sql_values(row)
            if index >= len(values):
                rendered_rows.append("(" + ", ".join(values) + ")")
                continue
            old_token = values[index].strip()
            old_value = _unquote_sql_literal(old_token)
            if old_value != new_value:
                values[index] = f"'{_escape_sql_literal(new_value)}'"
                changes.append({"column": column, "old": old_value, "new": new_value, "pattern": "insert_values"})
                changed = True
            rendered_rows.append("(" + ", ".join(values) + ")")
        if not changed:
            return match.group(0)
        return match.group("head") + ",\n  ".join(rendered_rows)

    sql = insert_pattern.sub(replace_insert, sql)
    return sql, changes


def _extract_sql_value_rows(values_sql: str) -> list[str]:
    rows: list[str] = []
    start: int | None = None
    depth = 0
    in_quote = False
    index = 0
    while index < len(values_sql):
        char = values_sql[index]
        if char == "'":
            if in_quote and index + 1 < len(values_sql) and values_sql[index + 1] == "'":
                index += 2
                continue
            in_quote = not in_quote
        elif not in_quote:
            if char == "(":
                if depth == 0:
                    start = index + 1
                depth += 1
            elif char == ")" and depth:
                depth -= 1
                if depth == 0 and start is not None:
                    rows.append(values_sql[start:index])
                    start = None
        index += 1
    return rows


def _split_sql_csv(text: str) -> list[str]:
    items: list[str] = []
    start = 0
    depth = 0
    in_quote = False
    index = 0
    while index < len(text):
        char = text[index]
        if char == "'":
            if in_quote and index + 1 < len(text) and text[index + 1] == "'":
                index += 2
                continue
            in_quote = not in_quote
        elif not in_quote:
            if char == "(":
                depth += 1
            elif char == ")" and depth:
                depth -= 1
            elif char == "," and depth == 0:
                item = text[start:index].strip()
                if item:
                    items.append(item)
                start = index + 1
        index += 1
    item = text[start:].strip()
    if item:
        items.append(item)
    return items


def _split_sql_value_rows(values_block: str) -> list[str]:
    rows: list[str] = []
    depth = 0
    in_quote = False
    start = -1
    i = 0
    while i < len(values_block):
        char = values_block[i]
        if char == "'" and not (in_quote and i + 1 < len(values_block) and values_block[i + 1] == "'"):
            in_quote = not in_quote
        elif char == "'" and in_quote and i + 1 < len(values_block) and values_block[i + 1] == "'":
            i += 1
        elif not in_quote and char == "(":
            if depth == 0:
                start = i + 1
            depth += 1
        elif not in_quote and char == ")":
            depth -= 1
            if depth == 0 and start >= 0:
                rows.append(values_block[start:i])
                start = -1
        i += 1
    return rows


def _split_sql_values(row: str) -> list[str]:
    values: list[str] = []
    in_quote = False
    start = 0
    i = 0
    while i < len(row):
        char = row[i]
        if char == "'" and in_quote and i + 1 < len(row) and row[i + 1] == "'":
            i += 2
            continue
        if char == "'":
            in_quote = not in_quote
        elif char == "," and not in_quote:
            values.append(row[start:i].strip())
            start = i + 1
        i += 1
    values.append(row[start:].strip())
    return values


def _unquote_sql_literal(token: str) -> str:
    text = token.strip()
    if len(text) >= 2 and text[0] == "'" and text[-1] == "'":
        return text[1:-1].replace("''", "'")
    return text


def _escape_sql_literal(value: str) -> str:
    return value.replace("'", "''")


def _increment_tail_number(value: str) -> str:
    match = re.search(r"(\d+)$", value or "")
    if not match:
        return f"{value}_HQ"
    number = match.group(1)
    prefix = value[: -len(number)]
    return prefix + str(int(number) + 1).zfill(len(number))


def _resolve_path_map(paths: dict, base_dir: Path) -> dict:
    return {
        key: resolve_artifact_path(str(value), base_dir) if value else value
        for key, value in paths.items()
    }


def _write_text(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
