#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""执行预埋 SQL 后回放日志 SELECT，确认后台查询至少能返回数据。

所有 DML 都在同一事务中执行，检查完成后统一 ROLLBACK，不持久化数据。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import dry_run_sql  # noqa: E402

BRANCH_PERMISSION_COLUMNS = (
    "branch_no",
    "frozen_branch",
    "open_branch",
    "org_no",
    "seller_branch",
    "belong_branch",
    "branch_code",
    "open_org",
)
INTERNAL_PERMISSION_COLUMNS = ("internal_branch",)
SYSTEM_SELECT_TABLES = {
    "tbsequence",
    "tbparam",
    "tsys_menu",
    "tsys_user",
    "tbsysarg",
    "tbtrans",
}


def main() -> None:
    parser = argparse.ArgumentParser(description="SQL return-row precheck")
    parser.add_argument("--case-plan", help="case_plan.json path (omit when using --stdin)")
    parser.add_argument("--stdin", action="store_true", help="read case_plan JSON from stdin")
    parser.add_argument("--base-dir", help="base directory for resolving relative SQL paths (used with --stdin)")
    parser.add_argument("--config", required=True, help="config.json path")
    parser.add_argument("--db", default="ui_bc", help="database key for backend SELECT")
    parser.add_argument("--env", help="override env key")
    parser.add_argument("--json", action="store_true", help="print JSON result")
    args = parser.parse_args()

    if args.stdin:
        case_plan_input = json.load(sys.stdin)
    elif args.case_plan:
        case_plan_input = Path(args.case_plan)
    else:
        parser.error("either --case-plan or --stdin is required")

    result = run_precheck(
        case_plan=case_plan_input,
        config_path=Path(args.config),
        db_key=args.db,
        env_override=args.env,
        base_dir=Path(args.base_dir) if args.base_dir else None,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result.get("ok") else 1)


def run_precheck(
    case_plan: "Path | dict",
    config_path: Path,
    db_key: str,
    env_override: str | None = None,
    base_dir: Path | None = None,
) -> dict:
    if isinstance(case_plan, Path):
        if base_dir is None:
            base_dir = case_plan.parent
        case_plan = json.loads(case_plan.read_text(encoding="utf-8"))
    if base_dir is None:
        base_dir = Path.cwd()
    raw_sqls = _candidate_selects(case_plan)
    if not raw_sqls:
        return {"ok": True, "status": "skipped", "reason": "no raw SELECT SQL available", "checks": []}

    sql_file_map = _sql_file_map(case_plan, base_dir)
    if not sql_file_map:
        return {"ok": True, "status": "skipped", "reason": "no SQL files to execute", "checks": []}

    try:
        db_cfg = dry_run_sql._load_db_config(str(config_path), db_key, env_override)
        conn = dry_run_sql._connect(db_cfg)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "status": "failed",
            "failure_reason": f"database connection failed: {exc}",
            "checks": [],
        }

    cursor = conn.cursor()
    base_cursor = cursor
    checks: list[dict] = []
    permission_checks: dict = {}
    try:
        if sql_file_map.get("base_sql"):
            _execute_preset_dml(base_cursor, sql_file_map["base_sql"], default_db="ui_bc")
        if sql_file_map.get("preset_sql"):
            _execute_preset_dml(cursor, sql_file_map["preset_sql"])
        qualified_sqls = [_qualify_cross_db_tables(raw_sql, case_plan) for raw_sql in raw_sqls]
        for index, raw_sql in enumerate(qualified_sqls, start=1):
            count_sql, skip_reason = _count_sql(raw_sql)
            if skip_reason:
                checks.append({"sql_index": index, "status": "skipped", "reason": skip_reason})
                continue
            try:
                query_cursor = _cursor_for_raw_sql(raw_sql, case_plan, cursor, base_cursor)
                query_cursor.execute(count_sql)
                row = query_cursor.fetchone()
                count = _first_count_value(row)
                check = {"sql_index": index, "status": "passed" if count > 0 else "failed", "count": count, "sql": raw_sql}
                if count <= 0:
                    check["diagnostics"] = _diagnose_return_check_failure(raw_sql, case_plan)
                checks.append(check)
            except Exception as exc:  # noqa: BLE001
                checks.append(
                    {
                        "sql_index": index,
                        "status": "failed",
                        "count": 0,
                        "error": str(exc),
                        "diagnostics": _diagnose_return_check_failure(raw_sql, case_plan),
                    }
                )
        permission_checks = _run_permission_checks(cursor, base_cursor, case_plan, qualified_sqls)
    finally:
        try:
            conn.rollback()
        finally:
            cursor.close()
            conn.close()

    failed = [item for item in checks if item.get("status") == "failed"]
    runnable = [item for item in checks if item.get("status") != "skipped"]
    failed_permission = [
        item for item in permission_checks.values()
        if isinstance(item, dict) and item.get("status") == "failed"
    ]
    if failed:
        return {
            "ok": False,
            "status": "failed",
            "checks": checks,
            "permission_checks": permission_checks,
            "failure_reason": "query returned 0 rows or failed after preset SQL",
        }
    if failed_permission:
        return {
            "ok": False,
            "status": "failed",
            "checks": checks,
            "permission_checks": permission_checks,
            "failure_reason": "permission expected counts do not match actual query counts",
        }
    if not runnable:
        return {"ok": True, "status": "skipped", "reason": "no executable raw SELECT SQL", "checks": checks, "permission_checks": permission_checks}
    return {"ok": True, "status": "passed", "checks": checks, "permission_checks": permission_checks}


def _candidate_selects(case_plan: dict) -> list[str]:
    raw = case_plan.get("log_analysis", {}).get("requirements", {}).get("raw_sqls", [])
    if not isinstance(raw, list):
        return []
    result = []
    seen = set()
    for item in raw:
        sql = _hydrate_current_params_sql(str(item or "")).strip().rstrip(";")
        if not re.match(r"^\s*select\b", sql, flags=re.IGNORECASE):
            continue
        tables = _tables_in_select(sql)
        if tables and all(table in SYSTEM_SELECT_TABLES for table in tables):
            continue
        normalized = re.sub(r"\s+", " ", sql.lower())
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(sql)
    result.sort(key=lambda sql: _candidate_select_score(sql, case_plan), reverse=True)
    return result[:10]


def _candidate_select_score(sql: str, case_plan: dict) -> int:
    lowered = sql.lower()
    tables = _tables_in_select(sql)
    biz_tables = {
        _normalize_table_name(str(table))
        for table in case_plan.get("preset_strategy", {}).get("biz_tables", [])
    }
    physical_tables = case_plan.get("shard", {}).get("physical_tables", {})
    if isinstance(physical_tables, dict):
        for table in list(biz_tables):
            for physical in physical_tables.get(table, []):
                biz_tables.add(_normalize_table_name(str(physical)))
    score = 0
    if tables & biz_tables:
        score += 100
    if any(column in lowered for column in BRANCH_PERMISSION_COLUMNS + INTERNAL_PERMISSION_COLUMNS):
        score += 40
    score += 3 * len(re.findall(r"\b(?:and|where)\b", lowered))
    score += 2 * len(re.findall(r"\b(?:=|>=|<=| in\s*\()", lowered))
    if re.match(r"^\s*select\s+count\s*\(", sql, flags=re.IGNORECASE):
        score -= 5
    return score


def _sql_files(case_plan: dict, base_dir: Path) -> list[Path]:
    result = []
    for paths in _sql_file_map(case_plan, base_dir).values():
        result.extend(paths)
    return result


def _sql_file_map(case_plan: dict, base_dir: Path) -> dict[str, list[Path]]:
    result = []
    seen = set()
    sql_paths = case_plan.get("sql_paths", {})
    if not isinstance(sql_paths, dict):
        return {}
    mapped: dict[str, list[Path]] = {}
    for key in ("base_sql", "preset_sql"):
        value = sql_paths.get(key)
        if not isinstance(value, str) or not value:
            continue
        path = Path(value)
        if not path.is_absolute():
            path = base_dir / path
        path = path.resolve()
        if path in seen or not path.exists() or not path.read_text(encoding="utf-8", errors="replace").strip():
            continue
        seen.add(path)
        mapped.setdefault(key, []).append(path)
    return mapped


def _cursor_for_raw_sql(raw_sql: str, case_plan: dict, primary_cursor, base_cursor):
    tables = _tables_in_select(raw_sql)
    if not tables:
        return primary_cursor
    business_tables = {
        str(table).lower()
        for table in case_plan.get("preset_strategy", {}).get("biz_tables", [])
    }
    physical_tables = case_plan.get("shard", {}).get("physical_tables", {})
    for table in list(business_tables):
        for physical in physical_tables.get(table, []):
            business_tables.add(str(physical).lower())
    if any(table in business_tables for table in tables):
        return primary_cursor
    return base_cursor


def _qualify_cross_db_tables(raw_sql: str, case_plan: dict) -> str:
    base_tables = _public_precheck_tables(case_plan)
    if not base_tables:
        return raw_sql

    def qualify_keyword(match: re.Match) -> str:
        keyword = match.group("keyword")
        table = match.group("table")
        suffix = match.group("suffix") or ""
        normalized = _normalize_table_name(table)
        if "." in table or normalized not in base_tables:
            return match.group(0)
        return f"{keyword}ui_bc.{table}{suffix}"

    sql = re.sub(
        r"(?P<keyword>\b(?:from|join)\s+)(?P<table>`?\w+`?)(?P<suffix>\s+(?:as\s+)?\w+)?",
        qualify_keyword,
        raw_sql,
        flags=re.IGNORECASE,
    )

    def qualify_comma(match: re.Match) -> str:
        table = match.group("table")
        suffix = match.group("suffix") or ""
        normalized = _normalize_table_name(table)
        if "." in table or normalized not in base_tables:
            return match.group(0)
        return f", ui_bc.{table}{suffix}"

    return re.sub(
        r",\s*(?P<table>`?\w+`?)(?P<suffix>\s+(?:as\s+)?\w+)?",
        qualify_comma,
        sql,
        flags=re.IGNORECASE,
    )


def _public_precheck_tables(case_plan: dict) -> set[str]:
    strategy = case_plan.get("preset_strategy", {})
    public_tables = {
        _normalize_table_name(str(table))
        for table in strategy.get("base_tables", [])
    }
    mapping = _load_table_db_mapping()
    for table in strategy.get("no_insert_tables", []):
        normalized = _normalize_table_name(str(table))
        dbs = mapping.get(normalized, [])
        if dbs and set(dbs) <= {"ui_bc"}:
            public_tables.add(normalized)
    return {table for table in public_tables if table}


def _load_table_db_mapping() -> dict[str, list[str]]:
    path = SCRIPT_DIR.parent / "config" / "table_db_mapping.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    result: dict[str, list[str]] = {}
    if not isinstance(payload, dict):
        return result
    for table, dbs in payload.items():
        if isinstance(dbs, str):
            values = [dbs]
        elif isinstance(dbs, list):
            values = [str(item) for item in dbs]
        else:
            continue
        result[_normalize_table_name(str(table))] = values
    return result


def _tables_in_select(raw_sql: str) -> set[str]:
    return {table for table, _alias in _table_references_in_select(raw_sql)}


def _diagnose_return_check_failure(raw_sql: str, case_plan: dict) -> dict:
    tables = sorted(_tables_in_select(raw_sql))
    table_aliases = _table_aliases_in_select(raw_sql)
    inserted_values = _inserted_values_by_table(case_plan)
    predicates = _literal_predicates(raw_sql)
    checks = []
    for qualifier, column, query_value in predicates:
        table = _table_for_predicate_column(tables, table_aliases, inserted_values, qualifier, column)
        insert_column = inserted_values.get(table, {}).get(column) if table else None
        insert_value = str(insert_column.get("value") or "") if insert_column else ""
        source = str(insert_column.get("source") or "") if insert_column else ""
        if not insert_column:
            status = "missing_insert_value"
        elif insert_value == query_value:
            status = "matched"
        else:
            status = "mismatch" if insert_value else "missing_insert_value"
        checks.append(
            {
                "table": table or "",
                "column": column,
                "query_value": query_value,
                "insert_value": insert_value,
                "status": status,
                "source": source,
            }
        )
    return {
        "tables": tables,
        "predicate_checks": checks,
        "summary": _predicate_diagnostic_summary(checks),
    }


def _inserted_values_by_table(case_plan: dict) -> dict[str, dict[str, dict]]:
    result: dict[str, dict[str, dict]] = {}
    for plan in case_plan.get("insert_plans", []) if isinstance(case_plan.get("insert_plans"), list) else []:
        if not isinstance(plan, dict):
            continue
        table_names = {
            _normalize_table_name(str(plan.get("table") or "")),
            _normalize_table_name(str(plan.get("physical_table") or "")),
        }
        table_names.discard("")
        rows = plan.get("rows")
        columns = rows[0] if isinstance(rows, list) and rows else plan.get("columns", [])
        if not isinstance(columns, list):
            continue
        by_column = {
            str(column.get("name") or ""): column
            for column in columns
            if isinstance(column, dict) and str(column.get("name") or "")
        }
        for table in table_names:
            result.setdefault(table, {}).update(by_column)
    return result


def _literal_predicates(raw_sql: str) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    for match in re.finditer(
        r"(?:(\b[A-Za-z_][A-Za-z0-9_]*)\.)?\b([A-Za-z_][A-Za-z0-9_]*)\b\s*=\s*'([^']*)'",
        raw_sql,
    ):
        result.append((match.group(1) or "", match.group(2), match.group(3)))
    for match in re.finditer(
        r"(?:(\b[A-Za-z_][A-Za-z0-9_]*)\.)?\b([A-Za-z_][A-Za-z0-9_]*)\b\s*=\s*(\d{4,})",
        raw_sql,
    ):
        result.append((match.group(1) or "", match.group(2), match.group(3)))
    deduped: list[tuple[str, str, str]] = []
    for item in result:
        if item not in deduped:
            deduped.append(item)
    return deduped


def _table_for_predicate_column(
    tables: list[str],
    table_aliases: dict[str, str],
    inserted_values: dict[str, dict[str, dict]],
    qualifier: str,
    column: str,
) -> str:
    qualified_table = table_aliases.get(str(qualifier or "").lower())
    if qualified_table:
        return qualified_table
    for table in tables:
        if column in inserted_values.get(table, {}):
            return table
    for table, values in inserted_values.items():
        if column in values:
            return table
    return tables[0] if len(tables) == 1 else ""


def _table_aliases_in_select(raw_sql: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for table, alias in _table_references_in_select(raw_sql):
        if not table:
            continue
        result[table] = table
        if alias:
            result[alias] = table
    return result


def _table_references_in_select(raw_sql: str) -> list[tuple[str, str]]:
    result: list[tuple[str, str]] = []
    table_clause = _table_clause_in_select(raw_sql)
    if not table_clause:
        return result
    table_pattern = r"(?P<table>(?:`[^`]+`|\w+)(?:\s*\.\s*(?:`[^`]+`|\w+))?)"
    alias_pattern = r"(?:\s+(?:as\s+)?(?P<alias>\w+))?"
    for pattern in (
        rf"\b(?:from|join)\s+{table_pattern}{alias_pattern}",
        rf",\s*{table_pattern}{alias_pattern}",
    ):
        for match in re.finditer(pattern, table_clause, flags=re.IGNORECASE):
            table = _normalize_table_name(match.group("table"))
            alias = str(match.group("alias") or "").strip().lower()
            if table:
                result.append((table, alias if alias and not _is_sql_keyword(alias) else ""))
    return result


def _table_clause_in_select(raw_sql: str) -> str:
    match = re.search(r"\bfrom\b", raw_sql, flags=re.IGNORECASE)
    if not match:
        return ""
    clause = raw_sql[match.start():]
    end = re.search(r"\b(?:where|group\s+by|order\s+by|having|limit|union)\b", clause, flags=re.IGNORECASE)
    return clause[: end.start()] if end else clause


def _is_sql_keyword(value: str) -> bool:
    return value.lower() in {
        "where",
        "join",
        "left",
        "right",
        "inner",
        "outer",
        "full",
        "cross",
        "on",
        "and",
        "or",
        "group",
        "order",
        "limit",
    }


def _predicate_diagnostic_summary(checks: list[dict]) -> str:
    parts = []
    for check in checks:
        status = check.get("status")
        if status == "matched":
            continue
        column = check.get("column", "")
        query_value = check.get("query_value", "")
        insert_value = check.get("insert_value", "")
        if status == "mismatch":
            parts.append(f"{column} mismatch query={query_value} insert={insert_value}")
        elif status == "missing_insert_value":
            parts.append(f"{column} missing_insert_value query={query_value}")
    return "; ".join(parts)


def _normalize_table_name(raw_table: str) -> str:
    parts = [part.strip().strip("`").lower() for part in re.split(r"\s*\.\s*", str(raw_table or "")) if part.strip()]
    return parts[-1] if parts else ""


def _execute_preset_dml(cursor, sql_files: list[Path], default_db: str = "") -> None:
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
    cursor.execute("START TRANSACTION")
    for sql_file in sql_files:
        statements = dry_run_sql.parse_statements(sql_file.read_text(encoding="utf-8", errors="replace"))
        for stmt, _line in statements:
            if dry_run_sql._is_dml(stmt):
                cursor.execute(_qualify_dml_table(stmt, default_db) if default_db else stmt)


def _qualify_dml_table(sql: str, default_db: str) -> str:
    if not default_db:
        return sql

    def replace(match: re.Match) -> str:
        prefix = match.group("prefix")
        table = match.group("table")
        if "." in table:
            return match.group(0)
        return f"{prefix}{default_db}.{table}"

    return re.sub(
        r"(?P<prefix>\b(?:delete\s+from|insert\s+into|update|replace\s+into)\s+)(?P<table>`?\w+`?)",
        replace,
        sql,
        count=1,
        flags=re.IGNORECASE,
    )


def _count_sql(raw_sql: str) -> tuple[str, str]:
    sql = raw_sql.strip().rstrip(";")
    if "?" in sql:
        return "", "placeholder_sql"
    if re.search(r"@\w+", sql):
        return "", "named_placeholder_sql"
    if re.search(r"#\{|\$\{", sql):
        return "", "template_placeholder_sql"
    sql = re.sub(r"\s+for\s+update\s*$", "", sql, flags=re.IGNORECASE)
    if re.match(r"^\s*select\s+count\s*\(", sql, flags=re.IGNORECASE):
        return sql, ""
    if re.search(r"\bfrom\s*\(", sql, flags=re.IGNORECASE):
        return f"SELECT COUNT(*) AS cnt FROM ({sql}) hui_return_precheck_sub", ""
    return f"SELECT COUNT(*) AS cnt FROM ({_constant_projection_sql(sql)}) hui_return_precheck_sub", ""


def _constant_projection_sql(sql: str) -> str:
    match = re.search(r"\bfrom\b", sql, flags=re.IGNORECASE)
    if not match:
        return sql
    body = "FROM " + sql[match.end():].lstrip()
    body = re.sub(r"\s+order\s+by\s+.+$", "", body, flags=re.IGNORECASE | re.DOTALL)
    return f"SELECT 1 {body}"


def _hydrate_current_params_sql(raw_sql: str) -> str:
    sql, params = _split_current_params(raw_sql)
    if not params or "?" not in sql:
        return sql
    return _replace_placeholders(sql, params)


def _split_current_params(raw_sql: str) -> tuple[str, list[str]]:
    match = re.search(r"\s+当前参数\s*[:：]\s*(.*)$", raw_sql, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return raw_sql, []
    sql = raw_sql[: match.start()].strip()
    params_text = match.group(1).strip()
    params_text = re.split(r"\s+(?:Actual\s+SQL|当前SQL)\s*[:：]", params_text, maxsplit=1)[0].strip()
    return sql, _split_params(params_text)


def _split_params(params_text: str) -> list[str]:
    if not params_text:
        return []
    result = []
    current = []
    quote = ""
    for char in params_text:
        if quote:
            if char == quote:
                quote = ""
            else:
                current.append(char)
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char == ",":
            result.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    result.append("".join(current).strip())
    return result


def _replace_placeholders(sql: str, params: list[str]) -> str:
    result = []
    param_index = 0
    in_string = False
    quote = ""
    for char in sql:
        if in_string:
            result.append(char)
            if char == quote:
                in_string = False
            continue
        if char in {"'", '"'}:
            in_string = True
            quote = char
            result.append(char)
            continue
        if char == "?" and param_index < len(params):
            result.append(_quote_param(params[param_index]))
            param_index += 1
        else:
            result.append(char)
    return "".join(result)


def _quote_param(value: str) -> str:
    cleaned = str(value or "").strip()
    if cleaned.lower() in {"null", "none"}:
        return "NULL"
    return "'" + cleaned.replace("'", "''") + "'"


def _run_permission_checks(cursor, base_cursor, case_plan: dict, raw_sqls: list[str]) -> dict:
    expected_counts = case_plan.get("expected_counts", {})
    if not isinstance(expected_counts, dict) or not expected_counts:
        return {}
    presets = case_plan.get("presets", {}) if isinstance(case_plan.get("presets"), dict) else {}
    result = {}
    for role in ("branch", "headquarter"):
        if role not in expected_counts:
            continue
        check = _run_one_permission_check(cursor, base_cursor, case_plan, raw_sqls, role, int(expected_counts.get(role) or 0), presets)
        result[role] = check
    return result


def _run_one_permission_check(cursor, base_cursor, case_plan: dict, raw_sqls: list[str], role: str, expected: int, presets: dict) -> dict:
    for index, raw_sql in enumerate(raw_sqls, start=1):
        role_sql, reason = _permission_role_sql(raw_sql, role, presets)
        if reason:
            continue
        count_sql, skip_reason = _count_sql(role_sql)
        if skip_reason:
            continue
        try:
            query_cursor = _cursor_for_raw_sql(role_sql, case_plan, cursor, base_cursor)
            query_cursor.execute(count_sql)
            actual = _first_count_value(query_cursor.fetchone())
        except Exception as exc:  # noqa: BLE001
            return {
                "sql_index": index,
                "expected": expected,
                "actual": 0,
                "status": "failed",
                "error": str(exc),
            }
        return {
            "sql_index": index,
            "expected": expected,
            "actual": actual,
            "status": "passed" if actual == expected else "failed",
            "sql": role_sql,
        }
    return {
        "expected": expected,
        "actual": None,
        "status": "skipped",
        "reason": "no permission SQL could be derived",
    }


def _permission_role_sql(raw_sql: str, role: str, presets: dict) -> tuple[str, str]:
    value_by_column = _permission_values_for_role(role, presets)
    if not value_by_column:
        return "", "missing_permission_presets"
    sql = raw_sql.strip().rstrip(";")
    changed = False
    for column, value in value_by_column.items():
        if not value:
            continue
        sql, count = re.subn(
            rf"(\b{re.escape(column)}\b\s*=\s*)'[^']*'",
            lambda m, v=value: f"{m.group(1)}'{v}'",
            sql,
            flags=re.IGNORECASE,
        )
        changed = changed or count > 0
        sql, count = re.subn(
            rf"(\b{re.escape(column)}\b\s*=\s*)\d+",
            lambda m, v=value: f"{m.group(1)}'{v}'",
            sql,
            flags=re.IGNORECASE,
        )
        changed = changed or count > 0
        sql, count = re.subn(
            rf"(\b{re.escape(column)}\b\s+in\s*\(\s*)'[^']*'(\s*\))",
            lambda m, c=column, v=value: f"{m.group(1)}{_permission_in_values(c, v, role, presets)}{m.group(2)}",
            sql,
            flags=re.IGNORECASE,
        )
        changed = changed or count > 0
    if changed:
        return sql, ""
    alias_sql = _permission_alias_sql(sql, role, presets)
    if alias_sql:
        return alias_sql, ""
    return "", "no_permission_filter"


def _permission_in_values(column: str, value: str, role: str, presets: dict) -> str:
    if role != "headquarter" or column not in BRANCH_PERMISSION_COLUMNS:
        return f"'{value}'"
    branch_value = str(presets.get("branch_no") or "")
    hq_value = str(presets.get("hq_branch_no") or "")
    values = [item for item in (branch_value, hq_value) if item]
    if not values:
        values = [value]
    return ", ".join(f"'{item}'" for item in dict.fromkeys(values))


def _permission_alias_sql(sql: str, role: str, presets: dict) -> str:
    aliases = _projected_permission_aliases(sql)
    if not aliases:
        return ""
    if set(aliases) >= {"branch_sub", "branch_red"} and role == "headquarter":
        branch_value = str(presets.get("branch_no") or "")
        hq_value = str(presets.get("hq_branch_no") or "")
        if not branch_value or not hq_value:
            return ""
        return (
            f"SELECT * FROM ({sql}) hui_permission_sub "
            f"WHERE branch_sub = branch_red AND branch_sub IN ('{branch_value}', '{hq_value}')"
        )
    value = str(
        presets.get("branch_no") if role == "branch" else presets.get("hq_branch_no")
        or ""
    )
    if not value:
        return ""
    predicates = " AND ".join(f"{alias} = '{value}'" for alias in aliases)
    return f"SELECT * FROM ({sql}) hui_permission_sub WHERE {predicates}"


def _projected_permission_aliases(sql: str) -> list[str]:
    pair_aliases = [
        alias
        for alias in ("branch_sub", "branch_red")
        if re.search(rf"\b{re.escape(alias)}\b", sql, flags=re.IGNORECASE)
    ]
    if pair_aliases:
        return pair_aliases
    return [
        alias
        for alias in ("open_branch", "branch_no")
        if re.search(rf"\b{re.escape(alias)}\b", sql, flags=re.IGNORECASE)
    ]


def _permission_values_for_role(role: str, presets: dict) -> dict:
    if role == "branch":
        return _permission_values(
            branch_value=str(presets.get("branch_no") or ""),
            internal_value=str(presets.get("internal_branch") or ""),
        )
    if role == "headquarter":
        return _permission_values(
            branch_value=str(presets.get("hq_branch_no") or ""),
            internal_value=str(presets.get("hq_internal_branch") or ""),
        )
    return {}


def _permission_values(branch_value: str, internal_value: str) -> dict:
    result = {column: branch_value for column in BRANCH_PERMISSION_COLUMNS}
    for column in INTERNAL_PERMISSION_COLUMNS:
        result[column] = internal_value
    return result


def _first_count_value(row) -> int:
    if row is None:
        return 0
    if isinstance(row, dict):
        value = next(iter(row.values()), 0)
    elif isinstance(row, (list, tuple)):
        value = row[0] if row else 0
    else:
        value = row
    try:
        return int(value)
    except Exception:
        return 0


if __name__ == "__main__":
    main()
