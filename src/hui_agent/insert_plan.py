from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class InsertColumn:
    name: str
    value: str
    source: str


@dataclass(frozen=True)
class InsertPlan:
    table: str
    physical_table: str
    role: str
    columns: list[InsertColumn]
    delete_where: str
    rows: list[list[InsertColumn]] | None = None
    db: str = ""  # 表所属数据库族（如 ui_fund, ui_trust），来自 table_db_mapping.json

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "table": self.table,
            "physical_table": self.physical_table,
            "role": self.role,
            "columns": [column.__dict__ for column in self.columns],
            "delete_where": self.delete_where,
            "db": self.db,
        }
        if self.rows is not None:
            payload["rows"] = [
                [column.__dict__ for column in row]
                for row in self.rows
            ]
        return payload


BRANCH_PERMISSION_COLUMNS = (
    "open_branch",
    "branch_no",
    "frozen_branch",
    "org_no",
    "seller_branch",
    "belong_branch",
    "branch_code",
    "open_org",
)
INTERNAL_PERMISSION_COLUMNS = ("internal_branch",)


@lru_cache(maxsize=1)
def _load_field_semantics() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[2] / "knowledge" / "config" / "field_semantics.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def build_insert_plans(
    case_plan: dict[str, Any],
    *,
    schema_cache: dict[str, Any] | None = None,
    base_tables: dict[str, Any] | None = None,
    include_skipped: bool = False,
    table_db_mapping: dict[str, str] | None = None,
) -> list[InsertPlan] | tuple[list[InsertPlan], list[dict[str, Any]]]:
    strategy = case_plan.get("preset_strategy", {})
    physical_tables = case_plan.get("shard", {}).get("physical_tables", {})
    shard = case_plan.get("shard", {})
    routing_db = str(shard.get("routing_db") or shard.get("db") or "")
    value_context = _build_value_context(case_plan)
    join_key_columns = _join_key_columns(case_plan)
    requirement_columns = _requirement_columns_by_table(case_plan)
    requirement_specs = _requirement_specs_by_table(case_plan)
    plans: list[InsertPlan] = []
    skipped: list[dict[str, Any]] = []
    promoted_base_tables = _promoted_no_insert_base_tables(case_plan, schema_cache)
    base_tables_for_plan = _dedupe_tables([*(strategy.get("base_tables", []) or []), *promoted_base_tables])
    business_tables_for_plan = strategy.get("biz_tables", []) or []
    for role, tables in (("base", base_tables_for_plan), ("business", business_tables_for_plan)):
        for table in tables:
            if _is_system_table(table):
                skipped.append(_skip(table, table, role, "system_table", []))
                continue
            for physical in _effective_physical_tables(table, physical_tables.get(table, [table])):
                if _is_system_table(physical):
                    skipped.append(_skip(table, physical, role, "system_table", []))
                    continue
                schema = _schema_for(table, physical, schema_cache)
                if schema is None:
                    skipped.append(_skip(table, physical, role, "missing_schema", []))
                    continue
                columns = _columns_for_table(table, schema, value_context, base_tables or {}, requirement_columns.get(_logical_table_name(table), []))
                rows = _tbclientmap_bank_no_variant_rows(table, schema, columns)
                if rows is None and _has_permission_cases(case_plan):
                    rows = _permission_rows(table, schema, columns, value_context)
                effective_columns = rows[0] if rows else columns
                missing_requirement_values = _missing_blocking_requirement_values(
                    requirement_specs.get(_logical_table_name(table), []),
                    effective_columns,
                )
                if missing_requirement_values:
                    skipped.append(_skip(table, physical, role, "missing_requirement_values", missing_requirement_values))
                    continue
                missing_required = [
                    column
                    for column in schema.required_columns
                    if column not in {insert_column.name for insert_column in effective_columns}
                ]
                if missing_required:
                    skipped.append(_skip(table, physical, role, "missing_required_columns", missing_required))
                    continue
                if not effective_columns:
                    skipped.append(_skip(table, physical, role, "no_mapped_columns", []))
                    continue
                plans.append(
                    InsertPlan(
                        table=table,
                        physical_table=physical,
                        role=role,
                        columns=effective_columns,
                        delete_where=_delete_where_for_rows(rows or [effective_columns], schema, table=table),
                        rows=rows,
                        db=_resolve_plan_db(table, role, table_db_mapping, routing_db=routing_db),
                    )
                )
                _remember_join_key_values(value_context, table, physical, effective_columns, join_key_columns)
    plans, join_skipped = _apply_join_key_consistency(plans, requirement_specs)
    skipped.extend(join_skipped)
    if include_skipped:
        return plans, skipped
    return plans


def load_schema_cache(skill_root: str | Path) -> dict[str, Any]:
    skill_root = Path(skill_root)
    merged: dict[str, Any] = {}
    for path in (skill_root / "config" / "cache").glob("*.json"):
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(payload, dict):
            merged.update(payload)
    return merged


def _effective_physical_tables(table: str, physicals: Any) -> list[str]:
    if not isinstance(physicals, list) or not physicals:
        physical_list = [str(table)]
    else:
        physical_list = [str(physical) for physical in physicals if str(physical or "").strip()]
    deduped: list[str] = []
    for physical in physical_list:
        if physical not in deduped:
            deduped.append(physical)
    logical = _logical_table_name(str(table))
    numbered = [
        physical
        for physical in deduped
        if physical != logical and _logical_table_name(physical) == logical
    ]
    if numbered:
        return numbered
    return deduped or [str(table)]


def _promoted_no_insert_base_tables(case_plan: dict[str, Any], schema_cache: dict[str, Any] | None) -> list[str]:
    strategy = case_plan.get("preset_strategy", {})
    promoted: list[str] = []
    for table in strategy.get("no_insert_tables", []) or []:
        table_name = str(table or "")
        if not _is_product_table(table_name):
            continue
        if not _table_mentioned_in_raw_sql(table_name, case_plan):
            continue
        if _schema_for(table_name, table_name, schema_cache) is None:
            continue
        promoted.append(table_name)
    return promoted


def _dedupe_tables(tables: list[Any]) -> list[str]:
    result: list[str] = []
    for table in tables:
        value = str(table or "")
        if value and value not in result:
            result.append(value)
    return result


def _is_product_table(table: str) -> bool:
    table_name = _logical_table_name(str(table or "").lower())
    return "product" in table_name or "prd" in table_name and "product" in table_name


def _table_mentioned_in_raw_sql(table: str, case_plan: dict[str, Any]) -> bool:
    table_name = _logical_table_name(str(table or "").lower())
    raw_sqls = case_plan.get("log_analysis", {}).get("requirements", {}).get("raw_sqls", [])
    if not isinstance(raw_sqls, list):
        return False
    return any(
        re.search(rf"\b{re.escape(table_name)}\b", str(raw_sql or ""), flags=re.IGNORECASE)
        for raw_sql in raw_sqls
    )


def load_base_tables(skill_root: str | Path) -> dict[str, Any]:
    path = Path(skill_root) / "config" / "base_tables.json"
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def render_sql(plans: list[InsertPlan], *, include_db_prefix: bool = True) -> str:
    blocks = []
    for plan in plans:
        rows = plan.rows or [plan.columns]
        names = ", ".join(column.name for column in rows[0])
        values = ",\n  ".join(
            "(" + ", ".join(_quote(column.value) for column in row) + ")"
            for row in rows
        )
        table_ref = f"{plan.db}.{plan.physical_table}" if include_db_prefix and plan.db else plan.physical_table
        blocks.append(
            f"DELETE FROM {table_ref} WHERE {plan.delete_where};\n"
            f"INSERT INTO {table_ref} ({names}) VALUES\n  {values};"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


@dataclass(frozen=True)
class TableSchema:
    all_columns: list[str]
    required_columns: list[str]
    primary_keys: list[str] | None = None
    column_types: dict[str, str] | None = None


def _schema_for(table: str, physical: str, schema_cache: dict[str, Any] | None) -> TableSchema | None:
    if not schema_cache:
        return None
    for key in (physical, table, _logical_table_name(physical)):
        entry = schema_cache.get(key)
        if not entry:
            continue
        all_columns = _extract_column_list(entry.get("all_columns", []))
        required_columns = _extract_column_list(entry.get("required_columns", []))
        primary_keys = _extract_column_list(entry.get("primary_keys", []))
        column_types = entry.get("column_types") if isinstance(entry.get("column_types"), dict) else None
        if all_columns:
            required_columns = [column for column in required_columns if column in set(all_columns)]
            primary_keys = [column for column in primary_keys if column in set(all_columns)]
            return TableSchema(all_columns=all_columns, required_columns=required_columns, primary_keys=primary_keys, column_types=column_types)
    # 精确 key 未命中 → 用前缀+数字模式匹配分片表缓存
    # 例如 physical="tbdxtrustshare16", table="tbdxtrustshare", 缓存 key="tbdxtrustshare1"
    logical = _logical_table_name(physical)
    for lookup in (logical, table):
        if not lookup:
            continue
        import re as _re
        pattern = _re.compile(rf'^{_re.escape(lookup)}\d+$')
        for key, entry in schema_cache.items():
            if not pattern.match(key):
                continue
            all_columns = _extract_column_list(entry.get("all_columns", []))
            required_columns = _extract_column_list(entry.get("required_columns", []))
            primary_keys = _extract_column_list(entry.get("primary_keys", []))
            column_types = entry.get("column_types") if isinstance(entry.get("column_types"), dict) else None
            if all_columns:
                required_columns = [column for column in required_columns if column in set(all_columns)]
                primary_keys = [column for column in primary_keys if column in set(all_columns)]
                return TableSchema(all_columns=all_columns, required_columns=required_columns, primary_keys=primary_keys, column_types=column_types)
    return None


def _extract_column_list(value: Any) -> list[str]:
    if isinstance(value, dict):
        if "all_columns" in value:
            return _extract_column_list(value["all_columns"])
        if "required_columns" in value:
            return _extract_column_list(value["required_columns"])
        return []
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        name = ""
        if isinstance(item, str):
            name = item
        elif isinstance(item, dict):
            name = str(item.get("field") or item.get("Field") or item.get("name") or "")
        if name and name not in result:
            result.append(name)
    return result


def _requirement_columns_by_table(case_plan: dict[str, Any]) -> dict[str, list[InsertColumn]]:
    result: dict[str, list[InsertColumn]] = {}
    for requirement in case_plan.get("sql_requirements", []) if isinstance(case_plan.get("sql_requirements"), list) else []:
        if not isinstance(requirement, dict):
            continue
        table = _logical_table_name(str(requirement.get("table") or ""))
        if not table:
            continue
        for column in requirement.get("columns", []) if isinstance(requirement.get("columns"), list) else []:
            if not isinstance(column, dict):
                continue
            name = str(column.get("column") or "").strip()
            value = str(column.get("expected_value") or "")
            if not name or value == "":
                continue
            purpose = str(column.get("purpose") or "requirement")
            source = str(column.get("source") or "")
            result.setdefault(table, []).append(
                InsertColumn(
                    name=name,
                    value=value,
                    source="sql_requirements." + purpose + (f".{source}" if source else ""),
                )
            )
    return result


def _requirement_specs_by_table(case_plan: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for requirement in case_plan.get("sql_requirements", []) if isinstance(case_plan.get("sql_requirements"), list) else []:
        if not isinstance(requirement, dict):
            continue
        table = _logical_table_name(str(requirement.get("table") or ""))
        if not table:
            continue
        for column in requirement.get("columns", []) if isinstance(requirement.get("columns"), list) else []:
            if not isinstance(column, dict):
                continue
            name = str(column.get("column") or "").strip()
            if not name:
                continue
            result.setdefault(table, []).append(column)
    return result


def _missing_blocking_requirement_values(requirements: list[dict[str, Any]], columns: list[InsertColumn]) -> list[str]:
    if not requirements:
        return []
    by_name = {column.name: column for column in columns}
    missing: list[str] = []
    for requirement in requirements:
        purpose = str(requirement.get("purpose") or "")
        if purpose not in {"required_match", "permission_filter"}:
            continue
        if bool(requirement.get("allow_default", True)):
            continue
        name = str(requirement.get("column") or "").strip()
        expected = str(requirement.get("expected_value") or "")
        if not name or expected:
            continue
        generated = by_name.get(name)
        if generated is None or generated.source.startswith("generated."):
            missing.append(name)
    return missing


def _apply_join_key_consistency(
    plans: list[InsertPlan],
    requirement_specs: dict[str, list[dict[str, Any]]],
) -> tuple[list[InsertPlan], list[dict[str, Any]]]:
    join_columns_by_table = _join_requirement_columns_by_table(requirement_specs)
    if not join_columns_by_table:
        return plans, []
    plans_by_table = {_logical_table_name(plan.table): plan for plan in plans}
    failures: dict[str, tuple[str, list[str]]] = {}
    values_by_column: dict[str, dict[str, str]] = {}
    for table, join_columns in join_columns_by_table.items():
        plan = plans_by_table.get(table)
        if plan is None:
            continue
        row_columns = plan.rows[0] if plan.rows else plan.columns
        by_name = {column.name: column for column in row_columns}
        for column in join_columns:
            generated = by_name.get(column)
            if generated is None or generated.value == "" or generated.source.startswith("generated."):
                failures[table] = ("missing_join_key_values", [column])
                continue
            values_by_column.setdefault(column, {})[table] = generated.value
    for column, values_by_table in values_by_column.items():
        if len(values_by_table) < 2:
            continue
        if len(set(values_by_table.values())) <= 1:
            continue
        for table in values_by_table:
            failures[table] = ("join_key_value_mismatch", [column])
    if not failures:
        return plans, []
    filtered_plans = [plan for plan in plans if _logical_table_name(plan.table) not in failures]
    skipped = []
    for plan in plans:
        table = _logical_table_name(plan.table)
        if table not in failures:
            continue
        reason, columns = failures[table]
        skipped.append(_skip(plan.table, plan.physical_table, plan.role, reason, columns))
    return filtered_plans, skipped


def _join_requirement_columns_by_table(requirement_specs: dict[str, list[dict[str, Any]]]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for table, requirements in requirement_specs.items():
        for requirement in requirements:
            if str(requirement.get("purpose") or "") != "join_key":
                continue
            if bool(requirement.get("allow_default", True)):
                continue
            column = str(requirement.get("column") or "").strip()
            if column and column not in result.setdefault(table, []):
                result[table].append(column)
    return result


def _merge_requirement_columns(columns: list[InsertColumn], requirement_columns: list[InsertColumn]) -> list[InsertColumn]:
    if not requirement_columns:
        return columns
    by_name = {column.name: column for column in requirement_columns}
    result: list[InsertColumn] = []
    included: set[str] = set()
    for column in columns:
        replacement = by_name.get(column.name)
        if replacement:
            result.append(replacement)
            included.add(column.name)
        else:
            result.append(column)
            included.add(column.name)
    for column in requirement_columns:
        if column.name not in included:
            result.append(column)
    return result


def _columns_for_table(
    table: str,
    schema: TableSchema,
    value_context: dict[str, tuple[str, str]],
    base_tables: dict[str, Any],
    requirement_columns: list[InsertColumn] | None = None,
) -> list[InsertColumn]:
    template_columns = _columns_from_base_template(table, schema, value_context, base_tables)
    if template_columns:
        # 基础表需要完整字段预埋：record 有值的用 record，其余列按类型给默认值
        # 确保 INSERT 覆盖 schema 中所有列，避免遗漏字段导致其他查询失败
        return _apply_table_semantic_defaults(
            table,
            _merge_requirement_columns(_merge_with_schema_defaults(template_columns, schema), requirement_columns or []),
            value_context,
        )
    if _is_product_table(table):
        return _apply_table_semantic_defaults(
            table,
            _merge_requirement_columns(_merge_with_schema_defaults(_columns_from_context(schema, value_context), schema), requirement_columns or []),
            value_context,
        )
    return _apply_table_semantic_defaults(
        table,
        _merge_requirement_columns(_columns_from_context(schema, value_context), requirement_columns or []),
        value_context,
    )


def _apply_table_semantic_defaults(
    table: str,
    columns: list[InsertColumn],
    value_context: dict[str, tuple[str, str]],
) -> list[InsertColumn]:
    if "hisassetacc" not in table.lower():
        return columns
    close_date = _context_value(value_context, "query_date_end").replace("-", "") or "20260130"
    result: list[InsertColumn] = []
    replaced = False
    for column in columns:
        if column.name == "close_date" and (column.value in {"", "0"} or column.source.startswith("generated.")):
            result.append(InsertColumn("close_date", close_date, "generated.historical_close_date"))
            replaced = True
        else:
            result.append(column)
    if not replaced and all(column.name != "close_date" for column in result):
        result.append(InsertColumn("close_date", close_date, "generated.historical_close_date"))
    return result


def _columns_from_base_template(
    table: str,
    schema: TableSchema,
    value_context: dict[str, tuple[str, str]],
    base_tables: dict[str, Any],
) -> list[InsertColumn]:
    records = base_tables.get(table, {}).get("records", {})
    if not isinstance(records, dict):
        return []
    record = _select_base_record(records, value_context)
    if not record:
        return []
    valid = set(schema.all_columns)
    result: list[InsertColumn] = []
    for column, value in record.items():
        if column.startswith("_") or column not in valid:
            continue
        result.append(
            InsertColumn(
                name=column,
                value=_normalize_template_value(column, value, schema.column_types),
                source=f"base_tables.{table}",
            )
        )
    return result


def _normalize_template_value(column: str, value: Any, column_types: dict[str, str] | None) -> str:
    text = str(value)
    column_type = str((column_types or {}).get(column, "")).lower()
    column_name = str(column or "").lower()
    if text.strip() == "" and column_type.startswith(("decimal", "numeric", "float", "double")):
        return "0"
    if text.strip() == "" and column_type.startswith(("int", "bigint", "smallint", "tinyint")):
        return "0"
    if text.strip() == "" and _looks_numeric_column_name(column_name):
        return "0"
    return text


def _looks_numeric_column_name(column_name: str) -> bool:
    return any(
        token in column_name
        for token in (
            "amount",
            "amt",
            "bala",
            "vol",
            "fee",
            "rate",
            "ratio",
            "price",
            "nav",
            "days",
            "num",
            "unit",
            "digit",
            "integer",
            "version",
            "times",
            "hold",
            "red",
            "sub",
            "conv",
        )
    )


def _merge_with_schema_defaults(
    template_columns: list[InsertColumn],
    schema: TableSchema,
) -> list[InsertColumn]:
    """补齐 base_templates record 中缺失的列（完整字段预埋）。

    - record 已有值的列 → 保留
    - record 缺失的必填列 → 按类型给默认值
    - record 缺失的非必填列 → 按类型给默认值（基础表需要完整字段，避免关联查询失败）
    """
    included = {column.name for column in template_columns}
    result = list(template_columns)
    for column in schema.all_columns:
        if column in included:
            continue
        default = _default_value_for_column(column, schema.column_types)
        if default is not None:
            result.append(InsertColumn(column, default, "generated.schema_default"))
    return result


def _select_base_record(records: dict[str, Any], value_context: dict[str, tuple[str, str]]) -> dict[str, Any]:
    wanted_prd = _context_value(value_context, "prd_code")
    wanted_ta = _context_value(value_context, "ta_code")
    wanted_customer = _context_value(value_context, "customer_id")
    wanted_bank_acc = _context_value(value_context, "bank_acc") or wanted_customer
    wanted_ta_client = _context_value(value_context, "ta_client")
    wanted_in_client_no = _context_value(value_context, "in_client_no")
    wanted_client_no = _context_value(value_context, "client_no")
    best_score = 0
    best: dict[str, Any] = {}
    for record in records.values():
        if not isinstance(record, dict):
            continue
        score = 0
        if wanted_prd and str(record.get("prd_code", "")) == wanted_prd:
            score += 2
        if wanted_ta and str(record.get("ta_code", "")) == wanted_ta:
            score += 1
        if wanted_bank_acc and str(record.get("bank_acc", "")) == wanted_bank_acc:
            score += 4
        if wanted_bank_acc and str(record.get("account", "")) == wanted_bank_acc:
            score += 3
        if wanted_ta_client and str(record.get("ta_client", "")) == wanted_ta_client:
            score += 2
        if wanted_in_client_no and str(record.get("in_client_no", "")) == wanted_in_client_no:
            score += 2
        if wanted_client_no and str(record.get("client_no", "")) == wanted_client_no:
            score += 1
        if score > best_score:
            best_score = score
            best = record
    return best if best_score else {}


def _columns_from_context(schema: TableSchema, value_context: dict[str, tuple[str, str]]) -> list[InsertColumn]:
    valid = set(schema.all_columns)
    result: list[InsertColumn] = []
    included: set[str] = set()
    # 1. 先添加 context 中有值的列
    for column in schema.all_columns:
        source_value = value_context.get(column)
        if not source_value:
            continue
        value, source = source_value
        if column in valid and value != "":
            result.append(InsertColumn(column, value, source))
            included.add(column)
    # 2. 对缺失的必填列，根据列类型生成默认值
    required_set = set(schema.required_columns)
    for column in schema.all_columns:
        if column in included or column not in required_set:
            continue
        default_value = _default_value_for_column(column, schema.column_types)
        if default_value is not None:
            result.append(InsertColumn(column, default_value, "generated.required_default"))
    return result


def _default_value_for_column(column: str, column_types: dict[str, str] | None) -> str | None:
    """根据列名和类型生成合理的默认值。"""
    col_type = ""
    if column_types:
        col_type = str(column_types.get(column, "")).lower()
    # 时间类型
    if "time" in col_type and "date" not in col_type:
        return "00:00:00"
    # 日期类型
    if "date" in col_type:
        return "2025-01-01"
    # 日期时间类型
    if "datetime" in col_type or "timestamp" in col_type:
        return "2025-01-01 00:00:00"
    # 整数类型
    if col_type.startswith(("int", "bigint", "smallint", "tinyint")):
        return "0"
    # 小数类型
    if col_type.startswith(("decimal", "numeric", "float", "double")):
        return "0.00"
    # 按列名语义推断
    if "date" in column.lower():
        return "20250101"
    if "time" in column.lower():
        return "0"
    if "flag" in column.lower() or "status" in column.lower():
        return "0"
    if "amount" in column.lower() or "vol" in column.lower() or "fee" in column.lower() or "rate" in column.lower():
        return "0"
    if "count" in column.lower() or "num" in column.lower():
        return "0"
    if column.lower() == "area_id":
        return "1"
    if column.lower() in {
        "cfm_no",
        "contract_no",
        "serial_no",
        "global_serial_no",
        "request_no",
        "apply_no",
        "ex_serial",
        "confirm_no",
        "order_no",
    }:
        return "UI_AUTO_0001"
    # 字符串兜底
    if not col_type or "char" in col_type or "text" in col_type or "varchar" in col_type:
        return ""
    return None


def _join_key_columns(case_plan: dict[str, Any]) -> dict[str, set[str]]:
    raw = (
        case_plan.get("log_analysis", {})
        .get("requirements", {})
        .get("join_key_columns", {})
    )
    if not isinstance(raw, dict):
        return {}
    result: dict[str, set[str]] = {}
    for table, columns in raw.items():
        if not isinstance(columns, list):
            continue
        normalized = _logical_table_name(str(table))
        result.setdefault(normalized, set()).update(str(column) for column in columns if column)
    return result


def _remember_join_key_values(
    value_context: dict[str, tuple[str, str]],
    table: str,
    physical: str,
    columns: list[InsertColumn],
    join_key_columns: dict[str, set[str]],
) -> None:
    table_keys = set()
    for key in (table, physical, _logical_table_name(table), _logical_table_name(physical)):
        table_keys.update(join_key_columns.get(key, set()))
    if not table_keys:
        return
    for column in columns:
        if column.name not in table_keys or not column.value:
            continue
        _put_context(
            value_context,
            column.name,
            column.value,
            f"join_key.{table}.{column.name}",
        )


def _has_permission_cases(case_plan: dict[str, Any]) -> bool:
    case_order = set(case_plan.get("case_order") or [])
    case_inputs = set(case_plan.get("case_inputs", {}))
    return (
        case_plan.get("case_count") == 7
        or {"branch", "headquarter"}.issubset(case_order)
        or {"branch", "headquarter"}.issubset(case_inputs)
    )


def _permission_rows(
    table: str,
    schema: TableSchema,
    columns: list[InsertColumn],
    value_context: dict[str, tuple[str, str]],
) -> list[list[InsertColumn]] | None:
    branch_column = _permission_branch_column(schema)
    if branch_column:
        rows = [
            _with_permission_values(schema, columns, value_context, branch=True, split_column=branch_column),
            _with_permission_values(schema, columns, value_context, branch=False, split_column=branch_column),
        ]
        if _needs_fund_subscribe_redeem_pairs(table, schema):
            return _fund_subscribe_redeem_pair_rows(schema, rows, value_context)
        return _ensure_distinct_permission_rows(schema, rows, branch_column)
    if "client_no" in schema.all_columns:
        rows = [
            _with_permission_values(schema, columns, value_context, branch=True, split_column="client_no"),
            _with_permission_values(schema, columns, value_context, branch=False, split_column="client_no"),
        ]
        return _ensure_distinct_permission_rows(schema, rows, "client_no")
    return None


def _tbclientmap_bank_no_variant_rows(
    table: str,
    schema: TableSchema,
    columns: list[InsertColumn],
) -> list[list[InsertColumn]] | None:
    if table.lower() != "tbclientmap" or "bank_no" not in schema.all_columns:
        return None
    by_name = {column.name: column for column in columns}
    bank_no = by_name.get("bank_no")
    if bank_no is None or not bank_no.value:
        return None
    variants = [bank_no.value]
    spaced = f" {bank_no.value}"
    if spaced not in variants:
        variants.append(spaced)
    rows: list[list[InsertColumn]] = []
    for variant in variants:
        values = dict(by_name)
        values["bank_no"] = InsertColumn("bank_no", variant, bank_no.source)
        rows.append([values[column] for column in schema.all_columns if column in values and values[column].value != ""])
    return rows


def _needs_fund_subscribe_redeem_pairs(table: str, schema: TableSchema) -> bool:
    table_name = table.lower()
    return (
        "tbdxfundtranscfm" in table_name
        and {"busin_code", "trans_date", "cfm_no"}.issubset(set(schema.all_columns))
    )


def _fund_subscribe_redeem_pair_rows(
    schema: TableSchema,
    permission_rows: list[list[InsertColumn]],
    value_context: dict[str, tuple[str, str]],
) -> list[list[InsertColumn]]:
    start_date = _normalize_yyyymmdd(_context_value(value_context, "query_date_start")) or "20250610"
    redeem_date = _plus_days_yyyymmdd(start_date, 10) or start_date
    rows: list[list[InsertColumn]] = []
    for permission_index, row in enumerate(permission_rows, start=1):
        rows.append(
            _replace_row_values(
                schema,
                row,
                {
                    "busin_code": ("120", "generated.fund_report.subscribe_busin_code"),
                    "trans_date": (start_date, "case_inputs.query_date_start"),
                    "cfm_date": (start_date, "case_inputs.query_date_start"),
                    "cfm_no": (f"UI_AUTO_SUB_{permission_index:02d}", "generated.fund_report.cfm_no"),
                },
            )
        )
        rows.append(
            _replace_row_values(
                schema,
                row,
                {
                    "busin_code": ("124", "generated.fund_report.redeem_busin_code"),
                    "trans_date": (redeem_date, "generated.fund_report.redeem_date"),
                    "cfm_date": (redeem_date, "generated.fund_report.redeem_date"),
                    "cfm_no": (f"UI_AUTO_RED_{permission_index:02d}", "generated.fund_report.cfm_no"),
                },
            )
        )
    return rows


def _replace_row_values(
    schema: TableSchema,
    row: list[InsertColumn],
    replacements: dict[str, tuple[str, str]],
) -> list[InsertColumn]:
    values = {column.name: column for column in row}
    for name, (value, source) in replacements.items():
        if name in schema.all_columns:
            values[name] = InsertColumn(name, value, source)
    return [values[column] for column in schema.all_columns if column in values and values[column].value != ""]


def _normalize_yyyymmdd(value: str) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[:8] if len(digits) >= 8 else ""


def _plus_days_yyyymmdd(value: str, days: int) -> str:
    from datetime import datetime, timedelta

    normalized = _normalize_yyyymmdd(value)
    if not normalized:
        return ""
    try:
        return (datetime.strptime(normalized, "%Y%m%d") + timedelta(days=days)).strftime("%Y%m%d")
    except ValueError:
        return ""


def _permission_branch_column(schema: TableSchema) -> str:
    for column in BRANCH_PERMISSION_COLUMNS:
        if column in schema.all_columns:
            return column
    return ""


def _with_permission_values(
    schema: TableSchema,
    columns: list[InsertColumn],
    value_context: dict[str, tuple[str, str]],
    *,
    branch: bool,
    split_column: str,
) -> list[InsertColumn]:
    values = {column.name: column for column in columns}
    if split_column in BRANCH_PERMISSION_COLUMNS:
        branch_value = _context_value(value_context, "branch_no")
        hq_value = _context_value(value_context, "hq_branch_no")
        for branch_column in BRANCH_PERMISSION_COLUMNS:
            if branch_column not in schema.all_columns:
                continue
            values[branch_column] = InsertColumn(
                branch_column,
                branch_value if branch else hq_value,
                "presets.branch_no" if branch else "presets.hq_branch_no",
            )
        for internal_column in INTERNAL_PERMISSION_COLUMNS:
            if internal_column not in schema.all_columns:
                continue
            values[internal_column] = InsertColumn(
                internal_column,
                _context_value(value_context, "internal_branch") if branch else _context_value(value_context, "hq_internal_branch"),
                "presets.internal_branch" if branch else "presets.hq_internal_branch",
            )
    elif split_column == "client_no":
        client_no = _context_value(value_context, "client_no") or _context_value(value_context, "in_client_no")
        values["client_no"] = InsertColumn(
            "client_no",
            client_no if branch else _increment_tail_number(client_no),
            "presets.client_no" if branch else "generated.headquarter.client_no",
        )
    return [
        values[column]
        for column in schema.all_columns
        if column in values and values[column].value != ""
    ]


def _ensure_distinct_permission_rows(
    schema: TableSchema,
    rows: list[list[InsertColumn]],
    split_column: str,
) -> list[list[InsertColumn]] | None:
    if len(rows) < 2:
        return rows
    identity_columns = _identity_columns(schema)
    if not identity_columns or split_column in identity_columns:
        return rows
    if _row_identity(rows[0], identity_columns) != _row_identity(rows[1], identity_columns):
        return rows
    adjusted = _make_headquarter_identity_distinct(rows[1], identity_columns)
    if adjusted is None:
        return [rows[0]]
    return [rows[0], adjusted]


def _identity_columns(schema: TableSchema) -> list[str]:
    return list(schema.primary_keys or schema.required_columns or [])


def _row_identity(row: list[InsertColumn], identity_columns: list[str]) -> tuple[str, ...]:
    values = {column.name: column.value for column in row}
    return tuple(values.get(column, "") for column in identity_columns)


def _make_headquarter_identity_distinct(
    row: list[InsertColumn],
    identity_columns: list[str],
) -> list[InsertColumn] | None:
    values = {column.name: column for column in row}
    for candidate in (
        "serial_no",
        "global_serial_no",
        "frozen_serial",
        "contract_no",
        "ex_serial",
        "cfm_no",
        "request_no",
        "apply_no",
        "close_date",
        "client_no",
    ):
        if candidate not in identity_columns or candidate not in values:
            continue
        new_value = _increment_tail_number(values[candidate].value)
        replacements = {candidate: new_value}
        if candidate == "serial_no" and "global_serial_no" in values:
            replacements["global_serial_no"] = new_value
        return [
            InsertColumn(column.name, replacements.get(column.name, column.value), column.source)
            for column in row
        ]
    return None


def _build_value_context(case_plan: dict[str, Any]) -> dict[str, tuple[str, str]]:
    context: dict[str, tuple[str, str]] = {}
    presets = case_plan.get("presets", {})
    for key, value in presets.items():
        _put_context(context, str(key), _clean_value(value), f"presets.{key}")
        for column in _columns_for_semantic(str(key)):
            _put_context(context, column, _clean_value(value), f"presets.{key}")
    for column, value in _sql_literal_constants(case_plan).items():
        _put_context(context, column, value, f"log_analysis.literal.{column}")
    for field in case_plan.get("form_fields", {}).get("all", []):
        label = field.get("label", "")
        value = _clean_value(field.get("value", ""))
        for column in _label_to_columns(label):
            _put_context(context, column, value, f"form_fields.{label}")
    for case_name, case_input in case_plan.get("case_inputs", {}).items():
        for field in case_input.get("fields", []):
            label = field.get("label", "")
            value = _clean_value(field.get("value", ""))
            for column in _label_to_columns(label):
                _put_context(context, column, value, f"case_inputs.{case_name}.{label}")
    customer_id = _context_value(context, "customer_id")
    if customer_id:
        for column in ("bank_acc", "account"):
            _put_context(context, column, customer_id, "presets.customer_id")
    bank_acc = _context_value(context, "bank_acc") or _context_value(context, "account")
    if bank_acc:
        _put_context(context, "virtual_bank_acc", _derive_virtual_bank_acc(bank_acc), "derived.bank_acc")
    return context


def _sql_literal_constants(case_plan: dict[str, Any]) -> dict[str, str]:
    requirements = case_plan.get("log_analysis", {}).get("requirements", {})
    raw_sqls = requirements.get("raw_sqls", [])
    if not isinstance(raw_sqls, list):
        return {}
    result: dict[str, str] = {}
    for raw_sql in raw_sqls:
        if not isinstance(raw_sql, str):
            continue
        raw_sql = _hydrate_current_params_sql(raw_sql)
        for match in re.finditer(
            r"(?:\b[A-Za-z_][A-Za-z0-9_]*\.)?\b([A-Za-z_][A-Za-z0-9_]*)\b\s*=\s*'([^']*)'",
            raw_sql,
        ):
            result.setdefault(match.group(1), match.group(2))
        for match in re.finditer(
            r"(?:\b[A-Za-z_][A-Za-z0-9_]*\.)?\b([A-Za-z_][A-Za-z0-9_]*)\b\s*(?:=|>=|>|<=|<)\s*(\d{4,})",
            raw_sql,
            flags=re.IGNORECASE,
        ):
            result.setdefault(match.group(1), match.group(2))
        for match in re.finditer(
            r"(?:\b[A-Za-z_][A-Za-z0-9_]*\.)?\b([A-Za-z_][A-Za-z0-9_]*)\b\s+in\s*\(\s*'([^']*)'",
            raw_sql,
            flags=re.IGNORECASE,
        ):
            result.setdefault(match.group(1), match.group(2))
    return result


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
            result.append(_quote(params[param_index]))
            param_index += 1
        else:
            result.append(char)
    return "".join(result)


def _split_current_params(raw_sql: str) -> tuple[str, list[str]]:
    match = re.search(
        r"\s+(?:当前参数|輝念歌方)\s*[:：]\s*(.*)$",
        raw_sql,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return raw_sql, []
    sql = raw_sql[: match.start()].strip()
    params_text = match.group(1).strip()
    params_text = re.split(
        r"\s+(?:Actual\s+SQL|当前SQL|SQL执行)\s*[:：]",
        params_text,
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0].strip()
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


def _put_context(context: dict[str, tuple[str, str]], column: str, value: str, source: str) -> None:
    value = _normalize_context_value_for_column(column, value)
    if column and value and column not in context:
        context[column] = (value, source)


def _context_value(context: dict[str, tuple[str, str]], column: str) -> str:
    item = context.get(column)
    return item[0] if item else ""


def _clean_value(value: Any) -> str:
    result = str(value or "")
    if ":" in result:
        return result.split(":", 1)[0]
    return result


def _normalize_context_value_for_column(column: str, value: str) -> str:
    if "date" in str(column or "").lower():
        digits = re.sub(r"\D", "", str(value or ""))
        if len(digits) >= 8:
            return digits[:8]
    return value


def _label_to_columns(label: str) -> list[str]:
    semantics = _load_field_semantics()
    label_to_semantic = semantics.get("label_to_semantic", {})
    label_text = str(label or "").strip()
    columns: list[str] = []
    semantic = ""
    if isinstance(label_to_semantic, dict) and label_text:
        if label_text in label_to_semantic:
            semantic = str(label_to_semantic[label_text] or "")
        else:
            for candidate, candidate_semantic in sorted(label_to_semantic.items(), key=lambda item: len(str(item[0])), reverse=True):
                candidate_text = str(candidate or "").strip()
                if candidate_text and (candidate_text in label_text or label_text in candidate_text):
                    semantic = str(candidate_semantic or "")
                    break
    for column in _columns_for_semantic(semantic):
        if column not in columns:
            columns.append(column)
    if "开始" in label_text or "起始" in label_text:
        if "query_date_start" not in columns:
            columns.append("query_date_start")
    if "结束" in label_text or "截止" in label_text or "终止" in label_text:
        if "query_date_end" not in columns:
            columns.append("query_date_end")
    return columns


def _columns_for_semantic(semantic: str) -> list[str]:
    semantics = _load_field_semantics()
    semantic_to_columns = semantics.get("semantic_to_columns", {})
    if not isinstance(semantic_to_columns, dict):
        return []
    columns = semantic_to_columns.get(str(semantic or ""))
    if not isinstance(columns, list):
        return []
    return [str(column) for column in columns if str(column or "").strip()]


def _derive_virtual_bank_acc(bank_acc: str) -> str:
    value = str(bank_acc or "")
    if not value:
        return ""
    if value.startswith("V"):
        return value
    if re.match(r"^[A-Za-z]\d+$", value):
        return "V" + value[1:]
    return "V" + value


def _delete_where_for_rows(rows: list[list[InsertColumn]], schema: TableSchema | None = None, *, table: str = "") -> str:
    table_clause = _support_table_delete_where(rows[0] if rows else [], table)
    if table_clause:
        return table_clause
    common_clause = _common_delete_where_for_generated_identity_rows(rows, schema)
    if common_clause:
        return common_clause
    clauses = []
    for row in rows:
        clause = _delete_where(row, schema)
        if clause not in clauses:
            clauses.append(clause)
    if len(clauses) == 1:
        return clauses[0]
    return " OR ".join(f"({clause})" for clause in clauses)


def _support_table_delete_where(columns: list[InsertColumn], table: str) -> str:
    table_name = _logical_table_name(str(table or "").lower())
    by_name = {column.name: column for column in columns if column.value != ""}
    if table_name == "tbclientseller" and "in_client_no" in by_name:
        return f"in_client_no = {_quote(by_name['in_client_no'].value)}"
    if table_name == "tbbankacc" and {"bank_acc", "in_client_no"}.issubset(by_name):
        return (
            f"bank_acc = {_quote(by_name['bank_acc'].value)} "
            f"AND in_client_no = {_quote(by_name['in_client_no'].value)}"
        )
    return ""


def _common_delete_where_for_generated_identity_rows(
    rows: list[list[InsertColumn]],
    schema: TableSchema | None,
) -> str:
    if len(rows) < 2 or schema is None:
        return ""
    identity_columns = _identity_columns(schema)
    if "close_date" not in identity_columns:
        return ""
    row_maps = [{column.name: column for column in row if column.value != ""} for row in rows]
    if not all("close_date" in row for row in row_maps):
        return ""
    close_dates = {row["close_date"].value for row in row_maps}
    if len(close_dates) < 2:
        return ""
    common_columns = [
        column
        for column in identity_columns
        if column != "close_date"
        and all(column in row for row in row_maps)
        and len({row[column].value for row in row_maps}) == 1
    ]
    strong_columns = {"in_client_no", "bank_acc", "account", "ta_code", "bank_no", "prd_code"}
    if not common_columns or not (set(common_columns) & strong_columns):
        return ""
    return " AND ".join(f"{column} = {_quote(row_maps[0][column].value)}" for column in common_columns)


def _delete_where(columns: list[InsertColumn], schema: TableSchema | None = None) -> str:
    """构造 DELETE 的 WHERE 子句，精确定位即将 INSERT 的行。

    优先级：
      1. 主键（最精确）
      2. 必填列（NOT NULL + 无默认值）
      3. 优先业务列（bank_acc, prd_code 等）
      4. 所有列 AND 拼接（兜底，不过滤空值 — 确保精确匹配）
    """
    by_name = {column.name: column for column in columns if column.value != ""}
    # 第一优先：主键
    for key_columns in (schema.primary_keys if schema else [], schema.required_columns if schema else []):
        if key_columns and all(column in by_name for column in key_columns):
            return " AND ".join(f"{column} = {_quote(by_name[column].value)}" for column in key_columns)
    # 第二优先：业务主键列
    for preferred in ("bank_acc", "account", "prd_code", "ta_code", "template_code"):
        if preferred in by_name:
            return f"{preferred} = {_quote(by_name[preferred].value)}"
    # 第三优先：第一个非空值列
    if by_name:
        first = next(iter(by_name.values()))
        return f"{first.name} = {_quote(first.value)}"
    # 兜底：所有值均为空字符串（如全 varchar 表且无 context 匹配）
    # 此时用所有列拼接 WHERE（不过滤空值），确保 DELETE 精确匹配即将 INSERT 的数据
    # 避免退化为 WHERE col = '' 误删全表
    all_by_name = {column.name: column for column in columns}
    if schema:
        for key_columns in (schema.primary_keys, schema.required_columns):
            if key_columns and all(column in all_by_name for column in key_columns):
                return " AND ".join(f"{column} = {_quote(all_by_name[column].value)}" for column in key_columns)
    return " AND ".join(f"{column.name} = {_quote(column.value)}" for column in columns)


def _quote(value: str) -> str:
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


def _logical_table_name(table_name: str) -> str:
    return re.sub(r"\d+$", "", table_name)


def _increment_tail_number(value: str) -> str:
    match = re.search(r"(\d+)$", value or "")
    if not match:
        return f"{value}_HQ"
    number = match.group(1)
    prefix = value[: -len(number)]
    return prefix + str(int(number) + 1).zfill(len(number))


def _is_system_table(table_name: str) -> bool:
    table = _logical_table_name(table_name).lower()
    if table in {"tbuserbranch"}:
        return True
    return (
        table.startswith("tsys_")
        or table.startswith("act_")
        or table.startswith("flow_")
        or table.startswith("tbsysarg")
        or table.startswith("tbworkflow")
        or table.startswith("tbtrans")
        or table.startswith("tblogs")
        or table.startswith("tbsequence")
        or table.startswith("tbbranch")
        or table.startswith("tbparam")
    )


# ── 表→数据库族映射（与 sql_extractor.py 的 table_to_db 逻辑一致）──

_TABLE_DB_MAPPING_CACHE: dict[str, str | list[str]] | None = None


def _load_table_db_mapping() -> dict[str, str | list[str]]:
    """加载 table_db_mapping.json → {table_lower: db_family/list}。

    表可能属于多个库族，保留完整列表，由 _resolve_plan_db() 按当前 routing_db 选择。
    """
    global _TABLE_DB_MAPPING_CACHE
    if _TABLE_DB_MAPPING_CACHE is not None:
        return _TABLE_DB_MAPPING_CACHE
    import json as _json
    mapping: dict[str, str | list[str]] = {}
    candidates = [
        Path(__file__).resolve().parents[2] / "knowledge" / "config" / "table_db_mapping.json",
        Path.cwd() / "knowledge" / "config" / "table_db_mapping.json",
    ]
    for mapping_path in candidates:
        if mapping_path.exists():
            try:
                raw = _json.loads(mapping_path.read_text(encoding="utf-8"))
            except Exception:
                continue
            for table, dbs in raw.items():
                if isinstance(dbs, list) and dbs:
                    mapping[table.lower()] = [str(db) for db in dbs if str(db or "").strip()]
                elif isinstance(dbs, str):
                    mapping[table.lower()] = dbs
            break
    _TABLE_DB_MAPPING_CACHE = mapping
    return mapping


def _resolve_plan_db(
    table: str,
    role: str,
    table_db_mapping: dict[str, str | list[str]] | None = None,
    *,
    routing_db: str = "",
) -> str:
    """解析 InsertPlan 的 db 字段。

    - base 表 → 固定 ui_bc
    - business 表 → 先解析库族，再用 shard.db 路由到实际分片库
    """
    if role == "base":
        return "ui_bc"
    t = table.lower()
    db = ""
    if table_db_mapping:
        db = _choose_mapped_db(table_db_mapping.get(t), routing_db)
        if db:
            return _route_plan_db(db, routing_db)
    db = _choose_mapped_db(_load_table_db_mapping().get(t), routing_db) or "ui_bc"
    return _route_plan_db(db, routing_db)


def _choose_mapped_db(value: str | list[str] | None, routing_db: str = "") -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list) or not value:
        return ""
    dbs = [str(db) for db in value if str(db or "").strip()]
    routing_family = _db_family(routing_db)
    if routing_family:
        for db in dbs:
            if _db_family(db) == routing_family:
                return db
    for db in dbs:
        if db != "ui_bc":
            return db
    return dbs[0] if dbs else ""


def _route_plan_db(db: str, routing_db: str) -> str:
    """将库族 ui_fund/ui_trust 路由成实际分片库 ui_fund1/ui_trust2。"""
    if not routing_db or db == "ui_bc":
        return db
    if db == routing_db:
        return routing_db
    if _db_family(db) == _db_family(routing_db):
        return routing_db
    return db


def _db_family(db_name: str) -> str:
    return re.sub(r"\d+$", "", str(db_name or ""))


def _skip(table: str, physical: str, role: str, reason: str, missing_columns: list[str]) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "table": table,
        "physical_table": physical,
        "role": role,
        "reason": reason,
    }
    if missing_columns:
        payload["missing_columns"] = missing_columns
    return payload
