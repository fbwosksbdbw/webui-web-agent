# -*- coding: utf-8 -*-
"""
校验 INSERT SQL 中的列名是否与预期一致。

用法:
    echo "INSERT INTO t (a, b, c) VALUES (1,2,3);" | python validate_insert_sql.py --expected-cols "a,b,c"
    python validate_insert_sql.py --expected-cols "a,b,c" --file insert.sql

退出码:
    0 — 所有列合法
    1 — 存在非法列或缺少列
"""
import re
import sys

_INSERT_COLS_RE = re.compile(
    r'(?:INSERT|REPLACE)\s+(?:INTO|into)\s+(?:`?\w+`?\.)?`?(\w+)`?\s*\(([^)]+)\)',
    re.IGNORECASE
)


def parse_insert_columns(sql_text: str) -> dict:
    """
    从 INSERT 语句中提取表名和列清单。
    返回 {"table": "tbdxtrustprddaily", "columns": ["col1", "col2", ...]} 或 {"table": None, "columns": []}
    """
    tables = []
    all_cols = []
    for m in _INSERT_COLS_RE.finditer(sql_text):
        t = m.group(1)
        cols_str = m.group(2)
        cols = [c.strip().strip('`') for c in cols_str.split(',') if c.strip()]
        tables.append(t)
        all_cols.extend(cols)
    return {
        "tables": sorted(set(tables)),
        "columns": sorted(set(all_cols)),  # 按字母序比对，避免顺序差异
    }


def validate(expected: list, actual: list, table_names: list = None) -> dict:
    """
    比对预期列和实际列。
    返回 {"ok": bool, "extra": [...], "missing": [...], "detail": str}
    """
    exp_set = set(expected)
    act_set = set(actual)
    extra = sorted(act_set - exp_set)
    missing = sorted(exp_set - act_set)

    table_info = f" ({', '.join(table_names)})" if table_names else ""

    if not extra and not missing:
        return {
            "ok": True,
            "extra": [],
            "missing": [],
            "detail": f"OK{table_info}: {len(actual)} 列，全部合法",
        }

    lines = [f"列名校验失败{table_info}:"]
    if extra:
        lines.append(f"  [+] 额外列（不在预期中）: {', '.join(extra)}")
        lines.append(f"       预期: {', '.join(sorted(exp_set))}")
    if missing:
        lines.append(f"  [-] 缺少列（在预期中但未生成）: {', '.join(missing)}")
    return {
        "ok": False,
        "extra": extra,
        "missing": missing,
        "detail": "\n".join(lines),
    }


def main():
    verify_select = '--verify-select' in sys.argv
    expected_cols_str = None
    file_path = None
    for i, arg in enumerate(sys.argv):
        if arg == '--expected-cols' and i + 1 < len(sys.argv):
            expected_cols_str = sys.argv[i + 1]
        elif arg == '--file' and i + 1 < len(sys.argv):
            file_path = sys.argv[i + 1]

    if not expected_cols_str:
        print("用法: validate_insert_sql.py --expected-cols 'col1,col2,...' [--file FILE] [--verify-select]", file=sys.stderr)
        sys.exit(2)

    expected = [c.strip() for c in expected_cols_str.split(',') if c.strip()]

    if file_path:
        with open(file_path, 'r', encoding='utf-8') as f:
            sql_text = f.read()
    else:
        sql_text = sys.stdin.read()

    if not sql_text.strip():
        print("错误: 输入为空", file=sys.stderr)
        sys.exit(2)

    parsed = parse_insert_columns(sql_text)
    if not parsed["columns"]:
        print("错误: 未从输入中提取到 INSERT 列名", file=sys.stderr)
        sys.exit(2)

    result = validate(expected, parsed["columns"], parsed["tables"])
    print(result["detail"])

    if verify_select and parsed["columns"] and parsed["tables"]:
        where_cols = [c for c in expected if c in parsed["columns"]]
        if where_cols:
            table_name = parsed["tables"][0] if parsed["tables"] else 'UNKNOWN'
            select_sql = f"SELECT * FROM {table_name} WHERE {' AND '.join(f'{c}=<value>' for c in where_cols[:5])} LIMIT 1"
            print(f"\n--verify-select SQL (替换 <value> 为实际值):")
            print(select_sql)

    sys.exit(0 if result["ok"] else 1)


if __name__ == '__main__':
    main()
