from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SqlColumnRequirement:
    column: str
    purpose: str
    expected_value: str
    source: str
    allow_default: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "purpose": self.purpose,
            "expected_value": self.expected_value,
            "source": self.source,
            "allow_default": self.allow_default,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class SqlRequirement:
    table: str
    physical_tables: list[str]
    role: str
    columns: list[SqlColumnRequirement]
    source_sql: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "physical_tables": self.physical_tables,
            "role": self.role,
            "columns": [column.to_dict() for column in self.columns],
            "source_sql": self.source_sql,
        }


def build_sql_requirements(
    case_plan: dict[str, Any],
    *,
    schema_cache: dict[str, Any] | None = None,
) -> list[SqlRequirement]:
    strategy = case_plan.get("preset_strategy", {}) if isinstance(case_plan.get("preset_strategy"), dict) else {}
    requirements = case_plan.get("log_analysis", {}).get("requirements", {})
    where_columns = requirements.get("where_columns", {}) if isinstance(requirements.get("where_columns"), dict) else {}
    join_key_columns = requirements.get("join_key_columns", {}) if isinstance(requirements.get("join_key_columns"), dict) else {}
    raw_sqls = [str(sql) for sql in requirements.get("raw_sqls", []) if isinstance(sql, str)]
    literal_values = _sql_literal_constants(raw_sqls)
    value_context = _build_value_context(case_plan, literal_values)
    physical_table_map = case_plan.get("shard", {}).get("physical_tables", {})
    result: list[SqlRequirement] = []
    for role, tables in (("base", strategy.get("base_tables", []) or []), ("business", strategy.get("biz_tables", []) or [])):
        for table in _dedupe(str(item) for item in tables if str(item or "").strip()):
            physical_tables = _physical_tables_for(table, physical_table_map)
            schema = _schema_for(table, physical_tables, schema_cache)
            column_requirements = _column_requirements_for_table(
                table,
                schema=schema,
                where_columns=set(str(column) for column in where_columns.get(table, []) or []),
                join_columns=set(str(column) for column in join_key_columns.get(table, []) or []),
                value_context=value_context,
            )
            if not column_requirements:
                continue
            result.append(
                SqlRequirement(
                    table=table,
                    physical_tables=physical_tables,
                    role=role,
                    columns=column_requirements,
                    source_sql=_raw_sqls_for_table(table, raw_sqls),
                )
            )
    return result


def _column_requirements_for_table(
    table: str,
    *,
    schema: dict[str, Any],
    where_columns: set[str],
    join_columns: set[str],
    value_context: dict[str, tuple[str, str]],
) -> list[SqlColumnRequirement]:
    all_columns = _extract_column_list(schema.get("all_columns", []))
    required_columns = _extract_column_list(schema.get("required_columns", []))
    ordered = _dedupe([*all_columns, *where_columns, *join_columns, *required_columns])
    result: list[SqlColumnRequirement] = []
    for column in ordered:
        if column in where_columns:
            result.append(_requirement(column, "required_match", value_context, "where_columns", allow_default=False))
        elif column in join_columns:
            result.append(_requirement(column, "join_key", value_context, "join_key_columns", allow_default=False))
        elif column in required_columns:
            result.append(_requirement(column, "schema_required", value_context, "schema.required_columns", allow_default=True))
    return result


def _requirement(
    column: str,
    purpose: str,
    value_context: dict[str, tuple[str, str]],
    reason: str,
    *,
    allow_default: bool,
) -> SqlColumnRequirement:
    expected_value, source = value_context.get(column, ("", ""))
    return SqlColumnRequirement(
        column=column,
        purpose=purpose,
        expected_value=expected_value,
        source=source,
        allow_default=allow_default,
        reason=reason,
    )


def _build_value_context(
    case_plan: dict[str, Any],
    literal_values: dict[str, str],
) -> dict[str, tuple[str, str]]:
    context: dict[str, tuple[str, str]] = {}
    for key, value in (case_plan.get("presets", {}) if isinstance(case_plan.get("presets"), dict) else {}).items():
        _put_context(context, str(key), _clean_value(value), f"presets.{key}")
        for column in _columns_for_semantic(str(key)):
            _put_context(context, column, _clean_value(value), f"presets.{key}")
    for column, value in literal_values.items():
        _put_context(context, column, value, f"log_analysis.literal.{column}")
    for field in case_plan.get("form_fields", {}).get("all", []) if isinstance(case_plan.get("form_fields"), dict) else []:
        if not isinstance(field, dict):
            continue
        label = str(field.get("label") or "")
        value = _clean_value(field.get("value", ""))
        for column in _label_to_columns(label):
            _put_context(context, column, value, f"form_fields.{label}")
    return context


def _sql_literal_constants(raw_sqls: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_sql in raw_sqls:
        hydrated = _hydrate_current_params_sql(raw_sql)
        for match in re.finditer(
            r"(?:\b[A-Za-z_][A-Za-z0-9_]*\.)?\b([A-Za-z_][A-Za-z0-9_]*)\b\s*=\s*'([^']*)'",
            hydrated,
        ):
            result.setdefault(match.group(1), match.group(2))
        for match in re.finditer(
            r"(?:\b[A-Za-z_][A-Za-z0-9_]*\.)?\b([A-Za-z_][A-Za-z0-9_]*)\b\s*(?:=|>=|>|<=|<)\s*(\d{4,})",
            hydrated,
            flags=re.IGNORECASE,
        ):
            result.setdefault(match.group(1), match.group(2))
        for match in re.finditer(
            r"(?:\b[A-Za-z_][A-Za-z0-9_]*\.)?\b([A-Za-z_][A-Za-z0-9_]*)\b\s+in\s*\(([^)]*)\)",
            hydrated,
            flags=re.IGNORECASE,
        ):
            values = _literal_list_values(match.group(2))
            if values:
                result.setdefault(match.group(1), values[0])
    return result


def _literal_list_values(text: str) -> list[str]:
    quoted = [match.group(1) for match in re.finditer(r"'([^']*)'", text)]
    if quoted:
        return quoted
    return [match.group(1) for match in re.finditer(r"(?:^|,)\s*(\d{4,})\s*(?=,|$)", text)]


def _hydrate_current_params_sql(raw_sql: str) -> str:
    sql, params = _split_current_params(raw_sql)
    if not params or "?" not in sql:
        return sql
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
            result.append(f"'{params[param_index]}'")
            param_index += 1
        else:
            result.append(char)
    return "".join(result)


def _split_current_params(raw_sql: str) -> tuple[str, list[str]]:
    match = re.search(r"\s+(?:当前参数|輝念歌方)\s*[:：]\s*(.*)$", raw_sql, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return raw_sql, []
    return raw_sql[: match.start()].strip(), _split_params(match.group(1).strip())


def _split_params(params_text: str) -> list[str]:
    params_text = re.split(
        r"\s+(?:Actual\s+SQL|当前SQL|SQL执行)\s*[:：]",
        params_text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
    if not params_text:
        return []
    return [part.strip().strip("'\"") for part in params_text.split(",")]


def _schema_for(table: str, physical_tables: list[str], schema_cache: dict[str, Any] | None) -> dict[str, Any]:
    if not schema_cache:
        return {}
    for key in [*physical_tables, table, _logical_table_name(table)]:
        entry = schema_cache.get(key)
        if isinstance(entry, dict):
            return entry
    logical = _logical_table_name(table)
    for key, entry in schema_cache.items():
        if _logical_table_name(str(key)) == logical and isinstance(entry, dict):
            return entry
    return {}


def _physical_tables_for(table: str, physical_table_map: dict[str, Any]) -> list[str]:
    physicals = physical_table_map.get(table) if isinstance(physical_table_map, dict) else None
    if not isinstance(physicals, list) or not physicals:
        return [table]
    return _dedupe(str(physical) for physical in physicals if str(physical or "").strip())


def _raw_sqls_for_table(table: str, raw_sqls: list[str]) -> list[str]:
    table_name = _logical_table_name(table).lower()
    return [sql for sql in raw_sqls if re.search(rf"\b{re.escape(table_name)}\b", sql, flags=re.IGNORECASE)]


def _extract_column_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _put_context(context: dict[str, tuple[str, str]], column: str, value: str, source: str) -> None:
    if column and value and column not in context:
        context[column] = (value, source)


def _clean_value(value: Any) -> str:
    result = str(value or "")
    if ":" in result:
        return result.split(":", 1)[0]
    return result


def _label_to_columns(label: str) -> list[str]:
    semantic = _semantic_for_label(label)
    columns = list(_columns_for_semantic(semantic))
    label_text = str(label or "")
    if "开始" in label_text or "起始" in label_text:
        columns.append("query_date_start")
    if "结束" in label_text or "截止" in label_text or "终止" in label_text:
        columns.append("query_date_end")
    return _dedupe(columns)


def _semantic_for_label(label: str) -> str:
    mapping = _load_field_semantics().get("label_to_semantic", {})
    label_text = str(label or "").strip()
    if not isinstance(mapping, dict) or not label_text:
        return ""
    if label_text in mapping:
        return str(mapping[label_text] or "")
    for candidate, semantic in sorted(mapping.items(), key=lambda item: len(str(item[0])), reverse=True):
        candidate_text = str(candidate or "").strip()
        if candidate_text and (candidate_text in label_text or label_text in candidate_text):
            return str(semantic or "")
    return ""


def _columns_for_semantic(semantic: str) -> list[str]:
    mapping = _load_field_semantics().get("semantic_to_columns", {})
    if not isinstance(mapping, dict):
        return []
    columns = mapping.get(str(semantic or ""))
    if not isinstance(columns, list):
        return []
    return [str(column) for column in columns if str(column or "").strip()]


@lru_cache(maxsize=1)
def _load_field_semantics() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "knowledge" / "config" / "field_semantics.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _dedupe(values) -> list[str]:
    result: list[str] = []
    for value in values:
        text = str(value or "")
        if text and text not in result:
            result.append(text)
    return result


def _logical_table_name(table_name: str) -> str:
    return re.sub(r"\d+$", "", str(table_name or "").strip())
