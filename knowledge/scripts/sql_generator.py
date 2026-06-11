# -*- coding: utf-8 -*-
"""
SQL 脚本生成器 — 将 LLM 输出的 JSON 字段映射表转为完整 DELETE + INSERT SQL。

职责：
  1. resolve_auto_value() — 将 LLM 标记为 "auto" 的字段按类型/约束解析为具体值
  2. generate_insert_sql() — 基于解析后的映射表生成完整 SQL

不依赖 LLM，纯粹规则驱动。
"""

import re
from typing import List, Dict, Optional

# 系统级常量（测试环境固定值）
KNOWN_CONSTANTS = {
    "bank_no": "000",
    "seller_code": "000",
}

# 日期字段识别正则
_DATE_PATTERN = re.compile(r'date|time|_dt$', re.IGNORECASE)


def _is_date_column(col: str, col_type: str) -> bool:
    """判断某列是否为日期类 int 字段"""
    return bool(_DATE_PATTERN.search(col)) and col_type.startswith(('int', 'bigint'))


def _is_varchar(col_type: str) -> bool:
    """判断列类型是否为字符串类型"""
    t = col_type.lower()
    return t.startswith(('varchar', 'char', 'text'))


def _is_int(col_type: str) -> bool:
    """判断列类型是否为整型"""
    t = col_type.lower()
    return t.startswith(('int', 'bigint', 'tinyint', 'smallint', 'mediumint'))


def _is_decimal(col_type: str) -> bool:
    """判断列类型是否为定点数"""
    return col_type.lower().startswith(('decimal', 'numeric'))


def _default_base_date() -> int:
    """返回当前年份的基准日期 YYYYMMDD"""
    import time
    return int(time.strftime('%Y') + '0501')


def resolve_auto_value(
    col: str,
    col_type: str,
    row_idx: int = 0,
    unique_indexes: list = None,
    branch_split_col: str = None,
    branch_values: tuple = None,
    base_date: int = None,
):
    """
    将 LLM 标记为 "auto" 的字段解析为具体值。

    优先级（从高到低）：
      1. 硬编码常量（KNOWN_CONSTANTS）
      2. 日期字段（int + 列名含 date/time）→ base_date + row_idx
      3. 分行过滤列 → branch_values[row_idx]
      4. UNIQUE 索引列（非主键）→ f"AUTO_{col}_{row_idx}"
      5. int/bigint → 0
      6. decimal/numeric → 0.00
      7. varchar/char/text → ' '
      8. 其他 → ''

    Returns: str, int, or float
    """
    if base_date is None:
        base_date = _default_base_date()

    # 1. 硬编码常量
    if col in KNOWN_CONSTANTS:
        return KNOWN_CONSTANTS[col]

    # 2. 日期字段
    if _is_date_column(col, col_type):
        return base_date + row_idx

    # 3. 分行过滤列
    if branch_split_col and col == branch_split_col and branch_values:
        return branch_values[row_idx] if row_idx < len(branch_values) else branch_values[0]

    # 4. UNIQUE 索引列（含主键，LLM 标记 auto 即需要唯一值）
    if unique_indexes:
        for idx in unique_indexes:
            if isinstance(idx, dict) and col in idx.get('columns', []):
                return f"AUTO_{col}_{row_idx}"

    # 5-8. 按类型给默认值
    if _is_int(col_type):
        return 0
    elif _is_decimal(col_type):
        return 0.00
    elif _is_varchar(col_type):
        return ' '
    else:
        return ''


def generate_insert_sql(
    table_name: str,
    field_mapping: dict,
    column_types: dict,
    row_count: int = 1,
    delete_where: str = None,
    unique_indexes: list = None,
    branch_split_col: str = None,
    branch_values: tuple = None,
    base_date: int = None,
) -> str:
    """
    基于 LLM 输出的 JSON 映射表生成完整 DELETE + INSERT SQL。

    Args:
        table_name: 目标表名（裸表名，无库前缀）
        field_mapping: LLM 输出 {"col": "value" 或 "auto", ...}
        column_types: 缓存 {"col": "varchar(16)", ...}
        row_count: 生成行数（1 或 2）
        delete_where: DELETE 条件（如 "bank_acc = 'A3060210020001'"）
        unique_indexes: UNIQUE 索引列表 [{name, columns}]
        branch_split_col: 分行过滤列名
        branch_values: (分行值, 总行值)
        base_date: 基准日期 YYYYMMDD

    Returns:
        完整 SQL 字符串
    """
    if base_date is None:
        base_date = _default_base_date()

    # 校验：列名必须在 column_types 中存在
    valid_mapping = {}
    for col, val in field_mapping.items():
        if col in column_types:
            valid_mapping[col] = val
        # 不在 column_types 中的列 → 丢弃（防止编造列名）

    if not valid_mapping:
        raise ValueError(f"field_mapping 中没有有效列名（均不在 column_types 中）")

    # 为每行解析值
    all_columns = list(valid_mapping.keys())
    rows = []
    for row_idx in range(row_count):
        row_values = []
        for col in all_columns:
            raw_val = valid_mapping[col]
            col_type = column_types.get(col, '')

            if raw_val is None or str(raw_val).strip() == '' or raw_val == 'auto':
                # LLM 标记为 auto 或空值 → 规则引擎解析
                resolved = resolve_auto_value(
                    col, col_type, row_idx,
                    unique_indexes=unique_indexes,
                    branch_split_col=branch_split_col,
                    branch_values=branch_values,
                    base_date=base_date,
                )
            else:
                resolved = raw_val

            # 格式化：字符串值加引号，数字不加
            if isinstance(resolved, str):
                row_values.append(f"'{resolved}'")
            elif isinstance(resolved, int):
                row_values.append(str(resolved))
            elif isinstance(resolved, float):
                # 浮点数统一保留 2 位小数，贴合 SQL decimal 语义
                row_values.append(f"{resolved:.2f}")
            else:
                row_values.append(f"'{resolved}'")

        rows.append(f"  ({', '.join(row_values)})")

    # 拼接 SQL
    col_header = ', '.join(all_columns)
    values_block = ',\n'.join(rows)

    parts = []
    if delete_where:
        parts.append(f"DELETE FROM {table_name} WHERE {delete_where};")
    parts.append(f"INSERT INTO {table_name} ({col_header}) VALUES")
    parts.append(values_block + ";")
    parts.append("COMMIT;")

    return '\n'.join(parts)


def validate_and_dry_run(
    sql_text: str,
    expected_cols: list,
    db_key: str,
    config_json_path: str,
    table_name: str = None,
    max_retries: int = 3,
) -> dict:
    """
    两阶段验证闭环：validate_insert_sql (列名合法性) + dry_run_sql (事务回滚全验证)。

    直接调用验证器核心逻辑，不走 subprocess，保证每条 INSERT 必须通过验证。

    Args:
        sql_text: 待验证的 SQL 文本
        expected_cols: 预期列名列表
        db_key: 数据库 key（如 ui_bc, ui_trust1）
        config_json_path: config.json 路径
        table_name: 表名（用于错误信息）
        max_retries: 最大重试次数

    Returns:
        {"ok": True, "validate_result": ..., "dry_run_result": ...} 或抛出异常

    Raises:
        ValueError: 列名校验失败
        RuntimeError: Dry-run 验证失败
    """
    import os
    import sys as _sys

    scripts_dir = os.path.dirname(os.path.abspath(__file__))
    if scripts_dir not in _sys.path:
        _sys.path.insert(0, scripts_dir)

    from validate_insert_sql import parse_insert_columns, validate
    from dry_run_sql import parse_statements, _is_dml, _load_db_config, _connect, dry_run_statements

    label = table_name or "<inline>"

    # ─── 阶段 1：列名校验 ───
    parsed = parse_insert_columns(sql_text)
    if not parsed["columns"]:
        raise ValueError(f"[{label}] 未从 SQL 中提取到 INSERT 列名")

    validate_result = validate(expected_cols, parsed["columns"], parsed["tables"])
    if not validate_result["ok"]:
        raise ValueError(
            f"[{label}] 列名校验失败:\n{validate_result['detail']}\n"
            f"预期列: {sorted(expected_cols)}\n实际列: {parsed['columns']}"
        )

    # ─── 阶段 2：Dry-run 事务回滚验证 ───
    statements = parse_statements(sql_text)
    dml_stmts = [(s, l) for s, l in statements if _is_dml(s)]

    if not dml_stmts:
        return {
            "ok": True,
            "validate_result": validate_result,
            "dry_run_result": {"ok": True, "total": 0, "passed": 0, "failed": 0, "errors": []},
        }

    try:
        db_cfg = _load_db_config(config_json_path, db_key)
    except Exception as e:
        raise RuntimeError(f"[{label}] 数据库配置加载失败: {e}")

    try:
        conn = _connect(db_cfg)
    except Exception as e:
        raise RuntimeError(f"[{label}] 数据库连接失败: {e}")

    try:
        dry_result = dry_run_statements(conn, dml_stmts)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    if not dry_result["ok"]:
        error_details = "\n".join(
            f"  L{err['line']}: {err['stmt_preview'][:80]}\n    → {err['error']}"
            for err in dry_result["errors"]
        )
        raise RuntimeError(
            f"[{label}] Dry-run 验证失败 ({dry_result['passed']}/{dry_result['total']} 通过):\n{error_details}"
        )

    return {"ok": True, "validate_result": validate_result, "dry_run_result": dry_result}
