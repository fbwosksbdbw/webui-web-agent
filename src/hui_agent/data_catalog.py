from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any


class DataPolicyError(RuntimeError):
    pass


class DataCatalog:
    """Data-source policy boundary for batch menu generation.

    Cache is treated as reusable knowledge; the database is a refresh and
    verification source. Existing business rows are never a source for tests.
    """

    def __init__(
        self,
        *,
        schema_cache: dict[str, dict[str, Any]],
        table_mapping: dict[str, str] | None = None,
        reference_records: dict[str, dict[str, Any]] | None = None,
        db_refresh: Callable[[str], dict[str, Any]] | None = None,
    ) -> None:
        self.schema_cache = dict(schema_cache)
        self.table_mapping = table_mapping or {}
        self.reference_records = reference_records or {}
        self.db_refresh = db_refresh

    def get_schema(self, table: str) -> dict[str, Any]:
        key = table.lower()
        if key in self.schema_cache:
            return self.schema_cache[key]
        if table in self.schema_cache:
            return self.schema_cache[table]
        if not self.db_refresh:
            raise DataPolicyError(f"表结构缓存缺失且未配置数据库刷新: {table}")
        refreshed = self.db_refresh(table)
        self.schema_cache[key] = refreshed
        return refreshed

    def get_table_db(self, table: str) -> str:
        key = table.lower()
        if key in self.table_mapping:
            return self.table_mapping[key]
        if table in self.table_mapping:
            return self.table_mapping[table]
        raise DataPolicyError(f"表库映射缺失，必须刷新 table_db_mapping: {table}")

    def get_reference_record(self, key: str) -> dict[str, Any]:
        if key not in self.reference_records:
            raise DataPolicyError(f"基础配置缓存缺失: {key}")
        return self.reference_records[key]

    def get_runtime_route(self, sql_log: str) -> dict[str, Any]:
        physical_tables: dict[str, list[str]] = {}
        for table in re.findall(r"\b(tb[a-zA-Z0-9_]*?)(\d{1,2})\b", sql_log, flags=re.IGNORECASE):
            base, suffix = table
            physical = f"{base}{suffix}".lower()
            logical = base.lower()
            physical_tables.setdefault(logical, [])
            if physical not in physical_tables[logical]:
                physical_tables[logical].append(physical)
        return {"physical_tables": physical_tables}

    def select_existing_business_data(self, query: str) -> None:
        del query
        raise DataPolicyError("禁止依赖数据库现有业务数据；测试业务数据必须由 InsertPlan 生成")
