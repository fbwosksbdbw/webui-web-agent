from __future__ import annotations

import re
from typing import Any


def evaluate_insert_plan_coverage(case_plan: dict[str, Any], plans: list[Any], skipped: list[dict[str, Any]]) -> dict[str, Any]:
    """Check that every table selected for preset SQL actually produced a plan."""
    expected = _expected_preset_tables(case_plan)
    generated = _generated_plan_tables(plans)
    skipped_by_table = _skipped_by_table(skipped)
    missing_expected = []
    for table, physicals in expected.items():
        for physical in physicals:
            if _matches_generated(table, physical, generated):
                continue
            skip = skipped_by_table.get(_normalize(physical)) or skipped_by_table.get(_normalize(table)) or {}
            missing_expected.append(
                {
                    "table": table,
                    "physical_table": physical,
                    "reason": skip.get("reason", "not_generated"),
                    "missing_columns": skip.get("missing_columns", []),
                }
            )

    strategy = case_plan.get("preset_strategy", {}) if isinstance(case_plan.get("preset_strategy"), dict) else {}
    known = {
        _normalize(table)
        for table in [
            *strategy.get("base_tables", []),
            *strategy.get("biz_tables", []),
            *strategy.get("no_insert_tables", []),
        ]
    }
    observed_missing_strategy = [
        table
        for table in _observed_log_tables(case_plan)
        if _normalize(table) not in known and not _looks_system_table(table)
    ]
    no_insert_observed = [
        table
        for table in _observed_log_tables(case_plan)
        if _normalize(table) in {_normalize(item) for item in strategy.get("no_insert_tables", [])}
    ]
    status = "failed" if missing_expected or observed_missing_strategy else "passed"
    result = {
        "status": status,
        "expected_tables": expected,
        "generated_tables": sorted(generated),
        "missing_expected_tables": missing_expected,
        "observed_missing_strategy": observed_missing_strategy,
        "observed_no_insert_tables": no_insert_observed,
    }
    if status == "failed":
        result["failure_reason"] = _coverage_failure_reason(result)
    return result


def _expected_preset_tables(case_plan: dict[str, Any]) -> dict[str, list[str]]:
    strategy = case_plan.get("preset_strategy", {}) if isinstance(case_plan.get("preset_strategy"), dict) else {}
    physical_tables = case_plan.get("shard", {}).get("physical_tables", {})
    result: dict[str, list[str]] = {}
    for table in [*strategy.get("base_tables", []), *strategy.get("biz_tables", [])]:
        table_name = str(table)
        physicals = physical_tables.get(table_name)
        if not isinstance(physicals, list) or not physicals:
            physicals = [table_name]
        result[table_name] = [str(physical) for physical in physicals]
    return result


def _generated_plan_tables(plans: list[Any]) -> set[str]:
    result: set[str] = set()
    for plan in plans:
        table = getattr(plan, "table", "")
        physical = getattr(plan, "physical_table", "")
        for value in (table, physical):
            if value:
                result.add(_normalize(str(value)))
    return result


def _matches_generated(table: str, physical: str, generated: set[str]) -> bool:
    return (
        _normalize(table) in generated
        or _normalize(physical) in generated
        or _logical_table_name(physical) in generated
    )


def _skipped_by_table(skipped: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for item in skipped:
        for key in ("table", "physical_table"):
            value = item.get(key)
            if value:
                result.setdefault(_normalize(str(value)), item)
    return result


def _observed_log_tables(case_plan: dict[str, Any]) -> list[str]:
    requirements = case_plan.get("log_analysis", {}).get("requirements", {})
    observed: list[str] = []

    def add(value: Any) -> None:
        if not value:
            return
        table = str(value)
        if table and table not in observed:
            observed.append(table)

    for key in ("where_columns", "join_key_columns"):
        value = requirements.get(key, {})
        if isinstance(value, dict):
            for table in value:
                add(table)
    for table in requirements.get("join_tables", []) if isinstance(requirements.get("join_tables"), list) else []:
        add(table)
    physical_tables = case_plan.get("shard", {}).get("physical_tables", {})
    if isinstance(physical_tables, dict):
        for table in physical_tables:
            add(table)
    return observed


def _coverage_failure_reason(result: dict[str, Any]) -> str:
    parts = []
    for item in result.get("missing_expected_tables", [])[:5]:
        text = f"{item.get('physical_table') or item.get('table')}:{item.get('reason')}"
        missing = item.get("missing_columns") or []
        if missing:
            text += f"({','.join(missing)})"
        parts.append(text)
    missing_strategy = result.get("observed_missing_strategy", [])
    if missing_strategy:
        parts.append("observed_not_classified:" + ",".join(missing_strategy[:5]))
    return "SQL coverage gate failed" + (f"; { '; '.join(parts) }" if parts else "")


def _normalize(table: str) -> str:
    return _logical_table_name(str(table)).lower()


def _logical_table_name(table: str) -> str:
    return re.sub(r"\d+$", "", str(table or "").strip())


def _looks_system_table(table: str) -> bool:
    value = _normalize(table)
    if value in {"tbuserbranch"}:
        return True
    return (
        value.startswith("tsys_")
        or value.startswith("act_")
        or value.startswith("flow_")
        or value.startswith("tbsysarg")
        or value.startswith("tbworkflow")
        or value.startswith("tbtrans")
        or value.startswith("tblogs")
        or value.startswith("tbsequence")
        or value.startswith("tbbranch")
        or value.startswith("tbparam")
    )
