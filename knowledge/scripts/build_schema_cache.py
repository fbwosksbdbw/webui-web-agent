# -*- coding: utf-8 -*-
"""
预建 schema_cache.json — 一次性 DESCRIBE 所有常用业务表，写入缓存。
后续 sql_extractor.analyze_log_for_preset() 直接读缓存，无需再 DESCRIBE。

用法：
  python build_schema_cache.py                    # 构建所有常用表
  python build_schema_cache.py --tables t1 t2     # 仅补充指定表
  python build_schema_cache.py --clear            # 清空缓存后重建
"""
import sys, os, json, argparse, re

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(SKILL_DIR, 'scripts'))

def _find_project_root(project_dir: str = None) -> str:
    """查找项目根目录（conf/config.py 所在位置）。project_dir 优先，否则回退搜索。"""
    if project_dir and os.path.isdir(project_dir) and os.path.exists(os.path.join(project_dir, 'conf', 'config.py')):
        return project_dir

    # 🔧 修复：从 Excel 环境配置中读取 project_dir
    try:
        from config_reader import ConfigReader
        config = ConfigReader()
        excel_project_dir = config.get_env('project_dir', '')
        if excel_project_dir and os.path.isdir(excel_project_dir) and os.path.exists(os.path.join(excel_project_dir, 'conf', 'config.py')):
            return excel_project_dir
    except Exception:
        pass

    _search_roots = [
        os.path.abspath(os.path.join(SKILL_DIR, '..', '..')),
        os.path.abspath(os.path.join(SKILL_DIR, '..')),
        os.path.join(os.path.abspath(os.path.join(SKILL_DIR, '..')), 'python_project'),
    ]
    for _candidate in _search_roots:
        if os.path.exists(os.path.join(_candidate, 'conf', 'config.py')):
            return _candidate
    return ''

from sql_extractor import (
    load_schema_cache, save_schema_cache,
    seed_cache_from_describe, filter_describe_result,
    _load_module_cache, load_table_db_mapping, save_table_db_mapping,
    load_db_cache, _DB_FAMILY_CACHE_FILES,
)

# 常用表清单（配置表 + 业务表）
DEFAULT_TABLES = {
    'ui_bc': [
        'tbtainfo', 'tbproduct', 'tbdxtrustproduct',
        'tbclientseller2', 'tbclientmap', 'tbclient2',
        'tsys_user', 'tsys_menu',
    ],
    'ui_trust1': [
        'tbdxtrustshare1', 'tbdxtrustshareextend1',
    ],
    'ui_fund1': [
        'tbsubscribe1', 'tbapplyredeem1',
    ],
    'ui_fina1': [
        'tbfinasharedetail1', 'tbfinasharedetail2',
    ],
    'ui_dxcp1': [
        'tbdxcptransreq1', 'tbdxcpshare1',
    ],
    'ui_hstc1': [],
    'ui_insure1': [],
    'ui_per1': [
        'tbpensiontransreq1', 'tbpensionshare1',
    ],
}

# 表前缀→模块映射（用于自动发现）
TABLE_PREFIX_MODULE_MAP = {
    'tbdxtrust': '信托',
    'tbdxfund': '基金',
    'tbfina': '理财',
    'tbhisfina': '理财',
    'tbpension': '个人养老金',
    'tbhispension': '个人养老金',
    'tblcdxpension': '个人养老金',
}


def get_db_connection(db_name: str, env_key: str = None):
    """从 Config.env_dbs 获取数据库连接

    Args:
        db_name: 数据库名称（如 ui_trust1, ui_bc）
        env_key: 环境标识（如 ifm60-trust）。为 None 时自动搜索所有环境。
    """
    try:
        import pymysql
        from conf.config import Config

        if env_key:
            # 使用指定的环境
            env_db = Config.get_db_config(env_key)
            if not env_db:
                return None
            common = env_db['common']
            db_cfg = env_db['databases'].get(db_name, {})
            if not db_cfg:
                return None
            return pymysql.connect(
                host=common['host'], port=common['port'],
                user=db_cfg['user'], password=db_cfg['password'],
                database=db_cfg.get('database', db_name),
                charset='utf8mb4'
            )
        else:
            # 自动搜索所有环境
            for env_name, env_db in Config.env_dbs.items():
                common = env_db.get('common', {})
                db_cfg = env_db.get('databases', {}).get(db_name, {})
                if db_cfg:
                    return pymysql.connect(
                        host=common['host'], port=common['port'],
                        user=db_cfg['user'], password=db_cfg['password'],
                        database=db_cfg.get('database', db_name),
                        charset='utf8mb4'
                    )
            return None
    except Exception as e:
        print(f"  连接 {db_name} 失败: {e}")
        return None


def describe_table(conn, table_name: str) -> list:
    """执行 DESCRIBE 并返回行列表"""
    cursor = conn.cursor()
    cursor.execute(f'DESCRIBE `{table_name}`')
    rows = cursor.fetchall()
    cursor.close()
    return rows


def discover_tables_by_prefix(conn, prefixes: list) -> list:
    """从数据库中发现匹配指定前缀的表"""
    cursor = conn.cursor()
    cursor.execute('SHOW TABLES')
    all_tables = [row[0] for row in cursor.fetchall()]
    cursor.close()

    matched = []
    for table in all_tables:
        for prefix in prefixes:
            if table.startswith(prefix):
                matched.append(table)
                break
    return sorted(matched)


def _base_table_name(table_name: str) -> str:
    """去掉表名末尾的数字分片后缀。tbdxtrustshare1 → tbdxtrustshare"""
    return re.sub(r'\d+$', '', table_name)


def _db_family(db_name: str) -> str:
    """去掉数据库名末尾的数字分片后缀。ui_trust1 → ui_trust, ui_bc → ui_bc"""
    return re.sub(r'\d+$', '', db_name)


def build_table_db_mapping(db_names: list = None, env_key: str = None):
    """从数据库 SHOW TABLES 构建表→库族映射并写入 JSON。

    同一个表如果存在于多个库，会记录所有库（用列表存储）。

    Args:
        db_names: 数据库名列表。None 时使用 DEFAULT_TABLES 的 keys。
        env_key: 环境标识（如 ifm60-trust）。为 None 时自动搜索所有环境。
    """
    if db_names is None:
        db_names = list(DEFAULT_TABLES.keys())

    mapping = load_table_db_mapping(force_reload=True)
    new_count = 0
    append_count = 0

    for db_name in db_names:
        conn = get_db_connection(db_name, env_key=env_key)
        if not conn:
            print(f"  跳过 {db_name}（连接失败）")
            continue

        family = _db_family(db_name)
        cursor = conn.cursor()
        cursor.execute('SHOW TABLES')
        tables = [row[0] for row in cursor.fetchall()]
        cursor.close()
        conn.close()

        for table in tables:
            base = _base_table_name(table)
            if base not in mapping:
                # 新表：创建列表
                mapping[base] = [family]
                new_count += 1
                print(f"  {base} → {family}")
            else:
                # 已存在：确保值是列表，追加新的库族（去重）
                existing = mapping[base]
                if isinstance(existing, str):
                    existing = [existing]
                    mapping[base] = existing
                if family not in existing:
                    existing.append(family)
                    append_count += 1
                    print(f"  {base} → {family}（追加，现为 {existing}）")

    save_table_db_mapping(mapping)
    print(f"\n映射完成：新增 {new_count} 条，追加 {append_count} 条，总计 {len(mapping)} 条")
    return mapping


def build_cache(tables_by_db: dict = None, clear: bool = False, auto_discover: bool = False, force: bool = False, env_key: str = None):
    """主流程：遍历表清单，DESCRIBE 并写入缓存

    Args:
        tables_by_db: {数据库名: [表名列表]}。为 None 时使用 DEFAULT_TABLES
        clear: 是否清空缓存后重建
        auto_discover: 是否自动发现匹配前缀的表
        env_key: 环境标识（如 ifm60-trust）。为 None 时自动搜索所有环境。
    """
    if tables_by_db is None:
        tables_by_db = DEFAULT_TABLES

    if clear:
        save_schema_cache({})
        print("已清空所有模块缓存")

    cache = load_schema_cache()
    cached_before = set(cache.keys())

    total_added = 0
    total_skipped = 0

    for db_name, tables in tables_by_db.items():
        print(f"\n=== 数据库: {db_name} ===")
        conn = get_db_connection(db_name, env_key=env_key)
        if not conn:
            print(f"  跳过（连接失败）")
            continue

        # 自动发现模式：从数据库中查找匹配前缀的表
        if auto_discover:
            # 根据数据库名推断应该查找哪些前缀
            prefixes = []
            if 'trust' in db_name:
                prefixes = ['tbdxtrust']
            elif 'fund' in db_name:
                prefixes = ['tbdxfund']
            elif 'fina' in db_name:
                prefixes = ['tbfina', 'tbhisfina']
            elif 'dxcp' in db_name:
                prefixes = ['tbdxcp']
            elif 'hstc' in db_name:
                prefixes = ['tbhstc']
            elif 'insure' in db_name:
                prefixes = ['tbinsure']
            elif 'per' in db_name:
                prefixes = ['tbpension', 'tbhispension', 'tblcdxpension']
            elif db_name == 'ui_bc':
                # 公共库不自动发现，使用默认列表
                pass

            if prefixes:
                discovered = discover_tables_by_prefix(conn, prefixes)
                print(f"  自动发现 {len(discovered)} 个表（前缀: {prefixes}）")
                tables = discovered

        for table in tables:
            if table in cache and not clear and not force:
                print(f"  {table}: 已缓存，跳过")
                total_skipped += 1
                continue

            try:
                rows = describe_table(conn, table)
                result = seed_cache_from_describe(table, rows)
                print(f"  {table}: {len(result['required_columns'])} 必填列, {len(result['all_columns'])} 总列")
                total_added += 1
            except Exception as e:
                print(f"  {table}: DESCRIBE 失败 - {e}")

        conn.close()

    print(f"\n完成：新增 {total_added} 表，跳过 {total_skipped} 表")
    # 显示各库族缓存统计
    for family in _DB_FAMILY_CACHE_FILES:
        cache = load_db_cache(family)
        if cache:
            print(f"  {family}: {len(cache)} 表")


def main():
    parser = argparse.ArgumentParser(description='预建表结构缓存（按库族分文件）')
    parser.add_argument('--tables', nargs='+', help='仅处理指定表（需同时指定 --db）')
    parser.add_argument('--db', default='ui_bc', help='目标数据库（默认 ui_bc）')
    parser.add_argument('--clear', action='store_true', help='清空缓存后重建')
    parser.add_argument('--list', action='store_true', help='列出当前缓存内容')
    parser.add_argument('--auto', action='store_true', help='自动发现并缓存所有模块的表')
    parser.add_argument('--dbs', nargs='+', help='指定要自动发现的数据库列表（配合 --auto 使用）')
    parser.add_argument('--force', action='store_true', help='强制覆盖已缓存的表（不跳过）')
    parser.add_argument('--build-mapping', action='store_true',
                        help='构建/更新 table_db_mapping.json')
    parser.add_argument('--project-dir', help='项目根目录（conf/config.py 所在位置）')
    parser.add_argument('--env', default=None, dest='env_key',
                        help='环境标识（如 ifm60-trust），不指定则自动搜索所有环境')
    args = parser.parse_args()

    # 查找并注册项目根目录
    _proj_root = _find_project_root(getattr(args, 'project_dir', None))
    if _proj_root and _proj_root not in sys.path:
        sys.path.insert(0, _proj_root)

    if args.list:
        # 显示各库族缓存统计
        for family in _DB_FAMILY_CACHE_FILES:
            cache = load_db_cache(family)
            if cache:
                print(f"  {family}: {len(cache)} 表")
        return

    if args.auto:
        # 自动发现模式
        if args.dbs:
            dbs_to_discover = args.dbs
        else:
            dbs_to_discover = list(DEFAULT_TABLES.keys())
            print(f"  数据库来源: DEFAULT_TABLES ({len(dbs_to_discover)} 个)")
        # 先建映射（--auto 默认连带更新）
        print("=== 构建表→库映射 ===")
        build_table_db_mapping(dbs_to_discover)  # 不传 env_key，自动搜所有环境
        tables_by_db = {db: [] for db in dbs_to_discover}
        build_cache(tables_by_db, clear=args.clear, auto_discover=True, force=args.force, env_key=args.env_key)
    elif args.tables:
        tables_by_db = {args.db: args.tables}
        build_cache(tables_by_db, clear=args.clear, env_key=args.env_key)
    elif args.build_mapping:
        dbs = args.dbs if args.dbs else list(DEFAULT_TABLES.keys())
        print(f"=== 构建表→库映射（{len(dbs)} 个数据库）===")
        build_table_db_mapping(dbs)  # 不传 env_key，自动搜所有环境
    else:
        build_cache(clear=args.clear, env_key=args.env_key)


if __name__ == '__main__':
    main()
