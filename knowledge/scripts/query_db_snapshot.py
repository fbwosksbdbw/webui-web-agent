#!/usr/bin/env python3
"""查询数据库中预埋表的实际数据，用于 pytest 失败诊断。

执行预埋 SQL（事务回滚）后查询涉及表的前 N 行，输出 JSON。
不做任何持久化写入。

用法:
    python query_db_snapshot.py --config config.json --sql-file preset.sql --db ui_bc
    python query_db_snapshot.py --config config.json --tables tbtainfo tbdxcphisservicefeehz --db ui_bc
"""
import argparse
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import dry_run_sql  # noqa: E402


def connect_db(db_cfg: dict):
    if str(db_cfg.get('db_type') or 'mysql').lower() == 'oracle':
        return dry_run_sql._connect(db_cfg)

    import pymysql
    return pymysql.connect(
        host=db_cfg['host'],
        port=db_cfg['port'],
        user=db_cfg['user'],
        password=db_cfg['password'],
        database=db_cfg.get('database', db_cfg.get('db', '')),
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=10,
    )


def _connection_db_type(conn) -> str:
    return str(getattr(conn, '_hui_db_type', 'mysql') or 'mysql').lower()


def _table_ref(table: str, db_type: str) -> str:
    normalized = _normalize_table_name(table)
    if db_type == 'oracle':
        return normalized
    return f'`{normalized}`'


def get_db_config(config_path: str, db_key: str, env_override: str | None = None) -> dict:
    """从自动化项目 conf/config.py 读取数据库连接配置。"""
    try:
        return dry_run_sql._load_db_config(config_path, db_key, env_override)
    except Exception:
        if env_override:
            raise

    with open(config_path, 'r', encoding='utf-8') as f:
        config = json.load(f)
    env = config.get('env', {})
    # 直接在 env 中查找
    if db_key in env and isinstance(env[db_key], dict):
        return env[db_key]
    # 在 env_dbs 中查找
    env_dbs = env.get('env_dbs', {})
    if db_key in env_dbs:
        return env_dbs[db_key]
    # 默认
    return {
        'host': env.get('db_host', 'localhost'),
        'port': env.get('db_port', 3306),
        'user': env.get('db_user', 'root'),
        'password': env.get('db_password', ''),
        'database': db_key,
    }


def extract_tables_from_sql(sql_content: str) -> list:
    """从 SQL 内容中提取表名。"""
    tables = []
    seen = set()
    table_pattern = r'(?:`[^`]+`|\w+)(?:\s*\.\s*(?:`[^`]+`|\w+))?'
    for match in re.finditer(
        rf'(?:INSERT\s+INTO|DELETE\s+FROM|UPDATE)\s+({table_pattern})',
        sql_content,
        re.IGNORECASE,
    ):
        raw_table = match.group(1)
        table = _normalize_table_name(raw_table)
        if table not in seen:
            seen.add(table)
            tables.append(table)
    return tables


def _normalize_table_name(raw_table: str) -> str:
    parts = [part.strip().strip('`').lower() for part in re.split(r'\s*\.\s*', raw_table) if part.strip()]
    return parts[-1] if parts else raw_table.strip().strip('`').lower()


def query_table_snapshot(conn, table: str, limit: int = 5) -> dict:
    """查询表的前 N 行数据。"""
    try:
        db_type = _connection_db_type(conn)
        table_ref = _table_ref(table, db_type)
        cursor = conn.cursor()
        if db_type == 'oracle':
            cursor.execute(f'SELECT * FROM {table_ref} FETCH FIRST {limit} ROWS ONLY')
        else:
            cursor.execute(f'SELECT * FROM {table_ref} LIMIT {limit}')
        rows = cursor.fetchall()
        column_names = [
            str(item[0]).lower()
            for item in getattr(cursor, 'description', []) or []
            if item
        ]
        cursor.close()
        # Convert non-serializable types to strings
        serializable_rows = []
        for row in rows:
            row_items = row.items() if isinstance(row, dict) else zip(column_names, row)
            serializable_row = {}
            for k, v in row_items:
                if isinstance(v, (bytes, bytearray)):
                    serializable_row[k] = v.decode('utf-8', errors='replace')
                elif isinstance(v, Decimal):
                    serializable_row[k] = str(v)
                elif hasattr(v, 'isoformat'):
                    serializable_row[k] = v.isoformat()
                else:
                    serializable_row[k] = v
            serializable_rows.append(serializable_row)
        return {"table": table, "row_count": len(serializable_rows), "rows": serializable_rows}
    except Exception as e:
        return {"table": table, "error": str(e)}


def query_table_count(conn, table: str) -> dict:
    """查询表的总行数。"""
    try:
        db_type = _connection_db_type(conn)
        table_ref = _table_ref(table, db_type)
        cursor = conn.cursor()
        if db_type == 'oracle':
            cursor.execute(f'SELECT COUNT(*) cnt FROM {table_ref}')
        else:
            cursor.execute(f'SELECT COUNT(*) as cnt FROM {table_ref}')
        result = cursor.fetchone()
        cursor.close()
        if isinstance(result, dict):
            total = result.get('cnt', result.get('CNT', 0))
        elif isinstance(result, (list, tuple)):
            total = result[0] if result else 0
        else:
            total = result or 0
        return {"table": table, "total_rows": total}
    except Exception as e:
        return {"table": table, "error": str(e)}


def main():
    parser = argparse.ArgumentParser(description='查询数据库快照用于诊断')
    parser.add_argument('--config', required=True, help='config.json 路径')
    parser.add_argument('--db', default='ui_bc', help='数据库 key')
    parser.add_argument('--sql-file', help='预埋 SQL 文件路径（执行后查询涉及表）')
    parser.add_argument('--tables', nargs='+', help='直接指定要查询的表名')
    parser.add_argument('--limit', type=int, default=5, help='每表查询行数')
    parser.add_argument('--env', help='环境 key')
    args = parser.parse_args()

    db_cfg = get_db_config(args.config, args.db, env_override=args.env)

    tables = []
    if args.tables:
        tables = args.tables
    elif args.sql_file:
        with open(args.sql_file, 'r', encoding='utf-8') as f:
            sql_content = f.read()
        tables = extract_tables_from_sql(sql_content)
    else:
        print('请指定 --tables 或 --sql-file', file=sys.stderr)
        sys.exit(1)

    if not tables:
        print(json.dumps({"tables": [], "message": "no tables found"}, ensure_ascii=False))
        return

    conn = None
    try:
        conn = connect_db(db_cfg)
        results = []
        for table in tables:
            count_info = query_table_count(conn, table)
            snapshot = query_table_snapshot(conn, table, limit=args.limit)
            results.append({**count_info, **snapshot})

        output = {
            "db": args.db,
            "tables": results,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e), "db": args.db, "tables": tables}, ensure_ascii=False))
        sys.exit(1)
    finally:
        if conn:
            conn.close()


if __name__ == '__main__':
    main()
