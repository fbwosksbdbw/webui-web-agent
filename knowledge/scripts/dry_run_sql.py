#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
SQL Dry-Run 验证器 — 事务回滚模式验证 SQL 语法和完整性。

对一份 SQL 文件中的 INSERT/DELETE/REPLACE/UPDATE 按顺序在同一事务中执行后统一 ROLLBACK，
验证: 语法正确、表存在、列名有效、数据类型匹配。不持久化任何数据。

这是 SQL 生成闭环的最后一道防线:
  LLM 生成 SQL → validate_insert_sql.py (列名校验) → dry_run_sql.py (事务回滚验证) → 写入文件 → 步骤4实际执行

用法:
    python dry_run_sql.py --file <sql文件> --db <数据库key> [--config <config.json>]
    python dry_run_sql.py --sql "INSERT INTO t VALUES (1)" --db ui_bc --config config.json

退出码:
    0 — 所有语句通过验证
    1 — 存在语法/表/列/类型错误
    2 — 连接失败或配置错误
"""

import os
import re
import sys
import json
import argparse

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# DML 关键字前缀（需要验证的语句）
_DML_PREFIXES = ('INSERT', 'DELETE', 'REPLACE', 'UPDATE')

# 跳过验证的语句前缀
_SKIP_PREFIXES = ('COMMIT', 'ROLLBACK', 'SET ', 'USE ', 'START ', 'BEGIN',
                  'EXPLAIN', 'ALTER ', 'CREATE ', 'DROP ', 'TRUNCATE',
                  'GRANT ', 'REVOKE ', 'FLUSH ', 'SHOW ', 'DESC ', 'DESCRIBE ')


def parse_statements(sql_text: str) -> list:
    """
    解析 SQL 文本为独立 DML 语句列表。

    处理多行语句、行内多语句(分号分隔)、字符串内分号、行注释(--)、块注释(/* */)。
    返回 [(statement, line_number), ...]
    """
    # 移除块注释
    sql_text = re.sub(r'/\*.*?\*/', '', sql_text, flags=re.DOTALL)

    statements = []
    stmt_chars = []          # 当前语句字符缓冲
    in_string = False
    string_char = None
    line_number = 0
    stmt_start_line = 1

    def _flush():
        """将当前缓冲区的语句输出"""
        nonlocal stmt_start_line
        stmt = ''.join(stmt_chars).strip()
        stmt_chars.clear()
        if not stmt or _is_skip_statement(stmt):
            return
        statements.append((stmt, stmt_start_line))

    for raw_line in sql_text.split('\n'):
        line_number += 1
        stripped = raw_line.strip()

        # 跳过纯注释行
        if stripped.startswith('--') and not in_string:
            continue
        if not stripped and not in_string:
            # 空行：如果当前有未完成语句，视为语句结束
            if stmt_chars and not in_string:
                _flush()
            continue

        # 记录语句起始行
        if not stmt_chars:
            stmt_start_line = line_number

        # 移除行注释部分（不在字符串内时）
        line = raw_line
        if not in_string:
            comment_idx = line.find('--')
            if comment_idx >= 0:
                before = line[:comment_idx]
                if before.count("'") % 2 == 0 and before.count('"') % 2 == 0:
                    line = before.rstrip()

        # 逐字处理，跟踪字符串状态
        i = 0
        while i < len(line):
            ch = line[i]
            if in_string:
                stmt_chars.append(ch)
                if ch == '\\' and i + 1 < len(line):
                    i += 1
                    stmt_chars.append(line[i])
                elif ch == string_char:
                    in_string = False
                    string_char = None
            else:
                if ch == ';':
                    _flush()
                elif ch in ("'", '"'):
                    in_string = True
                    string_char = ch
                    stmt_chars.append(ch)
                else:
                    stmt_chars.append(ch)
            i += 1

        # 行末加空格分隔（非字符串内），保持跨行语句可读
        if stmt_chars and not in_string and stmt_chars[-1] != ' ':
            stmt_chars.append(' ')

    # 处理最后一条未以分号结尾的语句
    if stmt_chars:
        _flush()

    return statements


def _is_skip_statement(stmt: str) -> bool:
    """判断语句是否应跳过（非 DML 语句不验证）"""
    upper = stmt.upper().lstrip()
    return any(upper.startswith(p) for p in _SKIP_PREFIXES)


def _is_dml(stmt: str) -> bool:
    """判断是否为需要验证的 DML 语句"""
    upper = stmt.upper().lstrip()
    return any(upper.startswith(p) for p in _DML_PREFIXES)


def _load_db_config(config_json_path: str, db_key: str, env_override: str = None) -> dict:
    """从 config.json 和 conf/config.py 加载数据库连接信息"""
    if not os.path.exists(config_json_path):
        raise FileNotFoundError(f"config.json 不存在: {config_json_path}")

    with open(config_json_path, 'r', encoding='utf-8') as f:
        config_data = json.load(f)

    env = config_data.get('env', {})
    env_key = env_override if env_override else env.get('env_key', '')
    project_dir = env.get('project_dir', '')

    if not env_key:
        # 从一级菜单自动推导 module → resolve env
        menu = config_data.get('menu', {})
        level1 = menu.get('导航一级菜单', '')
        if level1:
            if '基金' in level1:
                module = 'dxfund'
            elif '信托' in level1:
                module = 'dxtrust'
            elif '理财' in level1:
                module = 'fina'
            else:
                module = 'dxtrust'
            from skill_config import resolve_env
            env_key = resolve_env(module, project_dir)

    if not env_key:
        raise ValueError("无法确定 env_key：config.json 中缺少 env.env_key 且无法从一级菜单推导")
    if not project_dir:
        raise ValueError("config.json 中缺少 env.project_dir")
    if not os.path.isdir(project_dir):
        raise ValueError(f"project_dir 不存在: {project_dir}")

    # 从项目 conf/config.py 读取数据库配置
    if project_dir not in sys.path:
        sys.path.insert(0, project_dir)
    _drop_stale_project_config_modules(project_dir)

    try:
        from conf.config import Config
    except ImportError as e:
        raise ImportError(
            f"无法导入 conf/config.py (project_dir={project_dir}): {e}"
        )

    try:
        db_config = Config.get_db_config(env_key)
    except ValueError as e:
        raise ValueError(f"环境 {env_key} 不在 Config.env_dbs 中: {e}")

    if db_key not in db_config.get('databases', {}):
        available = list(db_config.get('databases', {}).keys())
        raise ValueError(f"数据库 key '{db_key}' 不在环境 {env_key} 中。可用: {available}")

    db_info = db_config['databases'][db_key]
    common = db_config.get('common', {})
    db_type = (
        db_info.get('db_type')
        or common.get('db_type')
        or env.get('db_type')
        or 'mysql'
    )

    return {
        'host': common.get('host', ''),
        'port': common.get('port', 3306),
        'user': db_info.get('user', ''),
        'password': db_info.get('password', ''),
        'database': db_info.get('database', db_key),
        'db_type': str(db_type).lower(),
        'charset': 'utf8mb4',
        'autocommit': common.get('autocommit', True),
    }


def _drop_stale_project_config_modules(project_dir: str) -> None:
    project_root = os.path.abspath(project_dir)
    for module_name in ('conf.config', 'conf'):
        module = sys.modules.get(module_name)
        if not module:
            continue
        module_file = getattr(module, '__file__', '')
        if not module_file or not os.path.abspath(module_file).startswith(project_root):
            sys.modules.pop(module_name, None)


def _connect(db_cfg: dict):
    """建立数据库连接"""
    db_type = str(db_cfg.get('db_type') or 'mysql').lower()
    if db_type == 'oracle':
        try:
            import oracledb
        except ImportError:
            raise ImportError("请安装 oracledb: pip install oracledb")

        dsn = db_cfg.get('dsn') or f"{db_cfg['host']}:{db_cfg['port']}/{db_cfg['database']}"
        conn = oracledb.connect(
            user=db_cfg['user'],
            password=db_cfg['password'],
            dsn=dsn,
        )
        try:
            conn._hui_db_type = db_type
        except Exception:
            pass
        return conn

    try:
        import pymysql
    except ImportError:
        raise ImportError("请安装 pymysql: pip install pymysql")

    conn = pymysql.connect(
        host=db_cfg['host'],
        port=db_cfg['port'],
        user=db_cfg['user'],
        password=db_cfg['password'],
        database=db_cfg['database'],
        charset=db_cfg.get('charset', 'utf8mb4'),
        connect_timeout=10,
    )
    try:
        conn._hui_db_type = db_type
    except Exception:
        pass
    return conn


def dry_run_statements(conn, statements: list) -> dict:
    """
    在同一事务中按顺序执行 DML 语句并统一回滚。

    返回:
        {"ok": True/False, "total": int, "passed": int, "failed": int,
         "errors": [{"line": int, "stmt_preview": str, "error": str}]}
    """
    result = {"ok": True, "total": 0, "passed": 0, "failed": 0, "errors": []}
    cursor = conn.cursor()
    db_type = str(getattr(conn, '_hui_db_type', 'mysql') or 'mysql').lower()
    uses_mysql_transaction_commands = db_type == 'mysql'

    # 禁用外键检查（session 级别，避免父表数据不存在导致误报）
    if uses_mysql_transaction_commands:
        try:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        except Exception:
            pass

    transaction_active = False
    if uses_mysql_transaction_commands:
        try:
            cursor.execute("START TRANSACTION")
            transaction_active = True
        except Exception:
            transaction_active = False

    for stmt, line_num in statements:
        if not _is_dml(stmt):
            continue

        result["total"] += 1
        stmt_preview = stmt[:120] + ('...' if len(stmt) > 120 else '')

        try:
            if uses_mysql_transaction_commands and not transaction_active:
                cursor.execute("START TRANSACTION")
                transaction_active = True
            cursor.execute(stmt)
            result["passed"] += 1
            print(f"  ✓ L{line_num}: {stmt_preview}")

        except Exception as e:
            conn.rollback()
            transaction_active = False
            result["failed"] += 1
            result["ok"] = False
            # 提取 MySQL 错误码（增加 error_code 字段，用于结构化输出）
            error_code = None
            if hasattr(e, 'args') and len(e.args) >= 2:
                err_code = str(e.args[0])
                err_msg = f"[{err_code}] {e.args[1]}"
                error_code = err_code
            else:
                err_msg = str(e)

            result["errors"].append({
                "line": line_num,
                "stmt_preview": stmt_preview,
                "error": err_msg,
                "error_code": error_code,
            })
            print(f"  ✗ L{line_num}: {stmt_preview}")
            print(f"    ERROR: {err_msg}")

            # 分类错误，提示修复方向
            if '1054' in str(e.args[0]) if hasattr(e, 'args') else '':
                print(f"    ↳ 未知列名 → 检查列名是否来自 DESCRIBE/cache")
            elif '1146' in str(e.args[0]) if hasattr(e, 'args') else '':
                print(f"    ↳ 表不存在 → 检查表名和数据库路由")
            elif '1064' in str(e.args[0]) if hasattr(e, 'args') else '':
                print(f"    ↳ 语法错误 → 检查 SQL 语法")
            elif '1062' in str(e.args[0]) if hasattr(e, 'args') else '':
                print(f"    ↳ 主键/唯一键冲突 → 检查 UNIQUE 索引列的值是否重复")
            elif '1366' in str(e.args[0]) if hasattr(e, 'args') else '':
                print(f"    ↳ 数据类型不匹配 → 检查 int 列是否误填了字符串")
            elif '1452' in str(e.args[0]) if hasattr(e, 'args') else '':
                print(f"    ↳ 外键约束 → 检查父表数据是否存在")

    if transaction_active or not uses_mysql_transaction_commands:
        conn.rollback()

    # 恢复外键检查
    if uses_mysql_transaction_commands:
        try:
            cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        except Exception:
            pass

    cursor.close()
    return result


def _validate_sql_text(sql_text: str, label: str, db_key: str, config_json_path: str, env_override: str = None) -> dict:
    """
    验证 SQL 文本中的所有 DML 语句（通用入口，文件和行内共用）。

    完整闭环:
      1. 解析 SQL 文本为独立语句
      2. 连接目标数据库
      3. 逐条在事务中执行并回滚
      4. 输出结果报告
    """
    if not sql_text.strip():
        print(f"  ⚠ SQL 内容为空，无需验证")
        return {"ok": True, "total": 0, "passed": 0, "failed": 0, "errors": []}

    statements = parse_statements(sql_text)
    dml_stmts = [(s, l) for s, l in statements if _is_dml(s)]
    non_dml = len(statements) - len(dml_stmts)

    print(f"\n🔍 SQL Dry-Run 验证")
    print(f"  来源: {label}")
    print(f"  目标库: {db_key}")
    print(f"  语句: {len(statements)} 条 (DML: {len(dml_stmts)}, 跳过: {non_dml})")
    print()

    if not dml_stmts:
        print("  ⚠ 没有需要验证的 DML 语句")
        return {"ok": True, "total": 0, "passed": 0, "failed": 0, "errors": []}

    # 连接数据库
    try:
        db_cfg = _load_db_config(config_json_path, db_key, env_override)
    except Exception as e:
        return {
            "ok": False,
            "total": len(dml_stmts), "passed": 0, "failed": len(dml_stmts),
            "errors": [{"line": 0, "stmt_preview": "", "error": f"配置加载失败: {e}"}]
        }

    print(f"  连接: {db_cfg['host']}:{db_cfg['port']}/{db_cfg['database']} (user={db_cfg['user']})")

    try:
        conn = _connect(db_cfg)
    except Exception as e:
        return {
            "ok": False,
            "total": len(dml_stmts), "passed": 0, "failed": len(dml_stmts),
            "errors": [{"line": 0, "stmt_preview": "", "error": f"数据库连接失败: {e}"}]
        }

    try:
        result = dry_run_statements(conn, dml_stmts)
    finally:
        try:
            conn.close()
        except Exception:
            pass

    # 打印汇总
    print()
    if result["ok"]:
        print(f"✅ 全部通过: {result['passed']}/{result['total']} 条语句验证成功（均已回滚）")
    else:
        print(f"❌ 验证失败: {result['passed']}/{result['total']} 通过, {result['failed']} 失败")
        print()
        print("  失败详情:")
        for err in result["errors"]:
            print(f"    L{err['line']}: {err['stmt_preview'][:100]}")
            print(f"    → {err['error']}")

    return result


def validate_sql_file(sql_path: str, db_key: str, config_json_path: str, env_override: str = None) -> dict:
    """验证 SQL 文件中的所有 DML 语句（文件入口）"""
    if not os.path.exists(sql_path):
        return {
            "ok": False,
            "total": 0, "passed": 0, "failed": 0,
            "errors": [{"line": 0, "stmt_preview": "", "error": f"SQL 文件不存在: {sql_path}"}]
        }

    with open(sql_path, 'r', encoding='utf-8') as f:
        sql_text = f.read()

    return _validate_sql_text(sql_text, label=sql_path, db_key=db_key, config_json_path=config_json_path, env_override=env_override)


def validate_sql_text(sql_text: str, db_key: str, config_json_path: str, env_override: str = None) -> dict:
    """验证行内 SQL 语句（行内入口）"""
    return _validate_sql_text(sql_text, label="<inline>", db_key=db_key, config_json_path=config_json_path, env_override=env_override)


def main():
    parser = argparse.ArgumentParser(
        description="SQL Dry-Run 验证器 — 事务回滚模式验证 SQL",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python dry_run_sql.py --file ../sql_data/xxx/菜单.sql --db ui_trust1 --config config.json
  python dry_run_sql.py --file ../sql_data/xxx/菜单_base.sql --db ui_bc --config config.json
  python dry_run_sql.py --sql "INSERT INTO t VALUES (1)" --db ui_bc --config config.json
        """
    )
    parser.add_argument('--file', type=str, help='SQL 文件路径')
    parser.add_argument('--sql', type=str, help='直接传入 SQL 语句')
    parser.add_argument('--db', type=str, required=True, help='数据库 key (如 ui_bc, ui_trust1)')
    parser.add_argument('--config', type=str, required=True, help='config.json 路径（必填）')
    parser.add_argument('--env', type=str, help='覆盖 config.json 中的 env_key（用于跨环境验证）')
    parser.add_argument('--json', action='store_true', help='以 JSON 格式输出结果（机器可读）')

    args = parser.parse_args()

    if not args.file and not args.sql:
        print("错误: 必须指定 --file 或 --sql", file=sys.stderr)
        sys.exit(2)

    config_json_path = args.config
    if not os.path.exists(config_json_path):
        print(f"错误: config.json 不存在: {config_json_path}", file=sys.stderr)
        sys.exit(2)

    env_override = getattr(args, 'env', None)
    if args.sql:
        result = validate_sql_text(args.sql, args.db, config_json_path, env_override)
    else:
        result = validate_sql_file(args.file, args.db, config_json_path, env_override)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))

    sys.exit(0 if result["ok"] else 1)


if __name__ == '__main__':
    main()
