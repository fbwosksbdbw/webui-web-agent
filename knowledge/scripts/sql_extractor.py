# -*- coding: utf-8 -*-
"""
SQL 日志分析器 — 以查代写提速（v2: 支持双路探勘与双写预埋）

从 SSH 日志中提取后端真实查询的表名 + WHERE 条件字段，
结合表结构缓存（只保留 NOT NULL + 无默认值的列），
生成极简的 INSERT 所需列清单，避免 DESCRIBE 全表喂给大模型。

v2 新增:
  - extract_dual_route_requirements(): 双路探勘日志合并（无必填字段双路探勘专用）
  - build_dual_write_prompt(): 针对多表生成同一笔测试数据的 INSERT 语句
  - analyze_dual_route_for_preset(): 双路一站式分析入口

用法:
    # 1. 分析单路日志文本
    from scripts.sql_extractor import extract_sql_requirements, build_minimal_prompt
    result = extract_sql_requirements(log_text)
    prompt = build_minimal_prompt(result["tables"][0], result["where_columns"], ...)

    # 2. 双路探勘（无必填字段时）
    from scripts.sql_extractor import extract_dual_route_requirements, build_dual_write_prompt
    dual = extract_dual_route_requirements(empty_log, param_log)
    prompt = build_dual_write_prompt(dual, presets={...})

    # 3. 命令行
    python sql_extractor.py analyze <日志文本文件>
    python sql_extractor.py analyze_dual <空查日志> <带参日志>
    python sql_extractor.py cache list
    python sql_extractor.py cache get <表名>
    python sql_extractor.py filter_describe <DESCRIBE输出JSON文件>
"""

import os
import re
import sys
import json
from typing import List, Dict, Optional, Tuple, Any
from module_registry import normalize_module

try:
    import sqlglot
    from sqlglot import exp
except Exception:  # noqa: BLE001 - sqlglot is an enhancement; regex fallback remains available.
    sqlglot = None
    exp = None

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(SKILL_DIR, 'config', 'cache')

MAPPING_PATH = os.path.join(SKILL_DIR, 'config', 'table_db_mapping.json')
_table_db_mapping_cache = None


def load_table_db_mapping(force_reload: bool = False) -> dict:
    """加载表→库族映射。模块级缓存，首次加载后复用。

    值统一为列表格式（如 ["ui_trust", "ui_fund"]），兼容旧版字符串格式。
    """
    global _table_db_mapping_cache
    if _table_db_mapping_cache is not None and not force_reload:
        return _table_db_mapping_cache
    if os.path.exists(MAPPING_PATH):
        with open(MAPPING_PATH, 'r', encoding='utf-8') as f:
            raw = json.load(f)
        # 迁移旧格式：字符串值 → 列表值
        migrated = {}
        for k, v in raw.items():
            if isinstance(v, str):
                migrated[k] = [v]
            else:
                migrated[k] = v
        _table_db_mapping_cache = migrated
    else:
        _table_db_mapping_cache = {}
    return _table_db_mapping_cache


def save_table_db_mapping(mapping: dict):
    """保存表→库族映射到 JSON 文件，同时刷新模块缓存。"""
    global _table_db_mapping_cache
    os.makedirs(os.path.dirname(MAPPING_PATH), exist_ok=True)
    with open(MAPPING_PATH, 'w', encoding='utf-8') as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    _table_db_mapping_cache = mapping


# 业务规则必填列：in_client_no 必须与 tbclientseller2 关联（用于分行权限判定）
BUSINESS_RULE_COLUMNS = ["in_client_no"]

# 表名→模块缓存文件映射
_MODULE_CACHE_FILES = {
    '信托': os.path.join(CACHE_DIR, '信托.json'),
    '基金': os.path.join(CACHE_DIR, '基金.json'),
    '理财': os.path.join(CACHE_DIR, '理财.json'),
    '商业养老金': os.path.join(CACHE_DIR, '商业养老金.json'),
    '个人养老金': os.path.join(CACHE_DIR, '个人养老金.json'),
    '公共库': os.path.join(CACHE_DIR, '公共库.json'),
}

_DB_FAMILY_TO_MODULE = {
    'ui_trust': '信托',
    'ui_fund': '基金',
    'ui_dxcp': '商业养老金',
    'ui_fina': '理财',
    'ui_hstc': '汇市通',
    'ui_insure': '保险',
    'ui_per': '个人养老金',
}

# 库族→缓存文件映射（新：按库族分文件）
_DB_FAMILY_CACHE_FILES = {
    'ui_bc': os.path.join(CACHE_DIR, 'ui_bc.json'),
    'ui_trust': os.path.join(CACHE_DIR, 'ui_trust.json'),
    'ui_fund': os.path.join(CACHE_DIR, 'ui_fund.json'),
    'ui_fina': os.path.join(CACHE_DIR, 'ui_fina.json'),
    'ui_dxcp': os.path.join(CACHE_DIR, 'ui_dxcp.json'),
    'ui_hstc': os.path.join(CACHE_DIR, 'ui_hstc.json'),
    'ui_insure': os.path.join(CACHE_DIR, 'ui_insure.json'),
    'ui_per': os.path.join(CACHE_DIR, 'ui_per.json'),
}

# 模块→主库族映射（向后兼容用）。商业养老金运行模块是 dxcp；
# 个人养老金运行模块是 per，表族为 tbpension%。
_MODULE_TO_DB_FAMILY = {
    '信托': 'ui_trust',
    '基金': 'ui_fund',
    '理财': 'ui_fina',
    '商业养老金': 'ui_dxcp',
    '个人养老金': 'ui_per',
    '汇市通': 'ui_hstc',
    '保险': 'ui_insure',
    '公共库': 'ui_bc',
}

def _classify_table_module(table_name: str) -> str:
    """根据 table_db_mapping.json 中的库族判断所属模块，无映射时回退到公共库。"""
    family = table_to_db(table_name)
    return _DB_FAMILY_TO_MODULE.get(family, '公共库')

def _get_cache_path_for_table(table_name: str) -> str:
    """获取某表对应的缓存文件路径（按库族）"""
    family = table_to_db(table_name)
    return _DB_FAMILY_CACHE_FILES.get(family, os.path.join(CACHE_DIR, f'{family}.json'))

def _load_module_cache(module: str) -> dict:
    """加载指定模块的缓存（向后兼容）。优先从新的库族缓存加载。"""
    family = _MODULE_TO_DB_FAMILY.get(module)
    if family:
        cache = load_db_cache(family)
        if cache:
            return cache
    # 回退到旧的模块缓存文件
    path = _MODULE_CACHE_FILES.get(module, '')
    if path and os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}

def _save_module_cache(module: str, cache: dict):
    """保存指定模块的缓存（向后兼容）。优先保存到新的库族缓存文件。"""
    family = _MODULE_TO_DB_FAMILY.get(module)
    if family:
        _save_db_family_cache(family, cache)
        return
    # 回退到旧的模块缓存文件
    path = _MODULE_CACHE_FILES.get(module, '')
    if not path:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def _strip_db_suffix(db_name: str) -> str:
    """去掉数据库名末尾的数字分片后缀。ui_trust1 → ui_trust, ui_bc → ui_bc"""
    return re.sub(r'\d+$', '', db_name)


def load_db_cache(db_name: str) -> dict:
    """加载指定库族的缓存。db_name 可含分片号（如 ui_trust1），自动归一化为库族名。"""
    family = _strip_db_suffix(db_name)
    path = _DB_FAMILY_CACHE_FILES.get(family, '')
    if not path:
        path = os.path.join(CACHE_DIR, f'{family}.json')
    if os.path.exists(path):
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def _save_db_family_cache(family: str, cache: dict):
    """保存指定库族的缓存（原子写入）"""
    path = _DB_FAMILY_CACHE_FILES.get(family, '')
    if not path:
        path = os.path.join(CACHE_DIR, f'{family}.json')
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp_path = path + '.tmp'
    with open(tmp_path, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)
    os.replace(tmp_path, path)


def get_cached_table(table_name: str) -> dict:
    """从缓存获取某表的完整条目。按库族优先查找，兼容旧的按模块缓存。"""
    normalized = normalize_table_name(table_name)
    dbs = table_to_dbs(normalized)
    for db in dbs:
        cache = load_db_cache(db)
        resolved = _resolve_cached_table_name(normalized, cache)
        if resolved in cache:
            return cache[resolved]
    # 兜底：遍历旧模块缓存
    for module in _MODULE_CACHE_FILES:
        cache = _load_module_cache(module)
        resolved = _resolve_cached_table_name(normalized, cache)
        if resolved in cache:
            return cache[resolved]
    return {}


def _resolve_cached_table_name(table_name: str, cache: dict) -> str:
    """如果 table_name 不在缓存中，尝试匹配物理表名（逻辑名+分片号）。

    例如：tbdxtrusttranscfm → tbdxtrusttranscfm1（取第一个匹配的物理表）。
    分片表物理名之间 schema 相同，取任意一个即可。
    """
    if table_name in cache:
        return table_name
    # 尝试找以 table_name 为前缀且后接数字的物理表名
    import re
    pattern = re.compile(rf'^{re.escape(table_name)}\d+$')
    for key in cache:
        if pattern.match(key):
            return key
    return table_name
# ============================================================

# 前缀规则：兜底用。table_to_db() 优先查 table_db_mapping.json，未命中才走此规则。
_TABLE_DB_RULES = [
    # 兜底规则：仅当 table_db_mapping.json 中查不到时使用
    ('tsys_', '', 'ui_bc'),
]
_single_db_key_cache = None


def _configured_single_db_key() -> str:
    """Return the configured single database key, if the current project uses one DB."""
    global _single_db_key_cache
    if _single_db_key_cache is not None:
        return _single_db_key_cache
    _single_db_key_cache = ''
    try:
        from openpyxl import load_workbook

        xlsx_path = os.path.join(SKILL_DIR, 'config', 'test_config.xlsx')
        if not os.path.exists(xlsx_path):
            return _single_db_key_cache
        wb = load_workbook(xlsx_path, data_only=True)
        try:
            ws = wb['环境配置']
            for row in ws.iter_rows(min_row=2, values_only=True):
                if row and row[0] == 'single_db_key' and row[1]:
                    _single_db_key_cache = str(row[1]).strip()
                    break
        finally:
            wb.close()
    except Exception:
        _single_db_key_cache = ''
    return _single_db_key_cache


def table_to_db(table_name: str) -> str:
    """
    返回表所属数据库**族**（不含分片号）。
    优先查 table_db_mapping.json，未命中则降级到 _TABLE_DB_RULES，
    都不匹配返回 'ui_bc'。

    注意：mapping 值为列表（一个表可能属于多个库）。
    当表同时存在于 ui_bc 和业务库时，优先返回业务库（更具体的库族）。
    如需获取所有库族，使用 table_to_dbs()。
    """
    single_db_key = _configured_single_db_key()
    if single_db_key:
        return single_db_key

    t = table_name.lower()

    # 第一优先：映射文件（值为列表，优先返回非 ui_bc 的业务库族）
    mapping = load_table_db_mapping()
    if t in mapping:
        v = mapping[t]
        if isinstance(v, list) and v:
            # 优先返回非 ui_bc 的库族（更具体的业务库）
            for db in v:
                if db != 'ui_bc':
                    return db
            return v[0]
        return v

    # 第二优先：硬编码兜底规则
    for prefix, suffix, db in _TABLE_DB_RULES:
        if t.startswith(prefix.lower()):
            if suffix and suffix.lower() in t:
                return db
            elif not suffix:
                return db

    return 'ui_bc'


def table_to_dbs(table_name: str) -> list:
    """
    返回表所属的所有数据库族列表（一个表可能存在于多个库）。
    优先查 table_db_mapping.json，未命中则降级到 _TABLE_DB_RULES，
    都不匹配返回 ['ui_bc']。
    """
    single_db_key = _configured_single_db_key()
    if single_db_key:
        return [single_db_key]

    t = table_name.lower()

    # 第一优先：映射文件（值为列表）
    mapping = load_table_db_mapping()
    if t in mapping:
        v = mapping[t]
        if isinstance(v, list):
            return v
        return [v]

    # 第二优先：硬编码兜底规则
    for prefix, suffix, db in _TABLE_DB_RULES:
        if t.startswith(prefix.lower()):
            if suffix and suffix.lower() in t:
                return [db]
            elif not suffix:
                return [db]

    return ['ui_bc']


def resolve_shard_db(table_name: str, routing_db: str = None) -> str:
    """
    结合 table_to_db() 的库族和 compute_shard 的分片路由，返回表的实际数据库 key。

    判断逻辑（基于 table_db_mapping.json 的权威数据）：
    - table_to_db() 返回 'ui_bc' → 不分片，直接返回 'ui_bc'
    - table_to_db() 返回其他库族（如 'ui_trust'、'ui_fund' 等）→ 是业务表，需要根据 routing_db 路由

    注意：一个表可能属于多个库族。此函数返回单个库族（向后兼容）。
    如需获取所有库族，使用 resolve_shard_dbs()。
    """
    single_db_key = _configured_single_db_key()
    if single_db_key:
        return single_db_key

    # 🔧 修复：先归一化表名（如 tbdxfundshare13 → tbdxfundshare），再查映射
    normalized = normalize_table_name(table_name)
    family = table_to_db(normalized)
    if family == 'ui_bc':
        return 'ui_bc'
    # 业务表：有 routing_db 就用 routing_db，否则回退到族名
    if routing_db:
        return routing_db
    return family


def resolve_shard_dbs(table_name: str, routing_db: str = None) -> list:
    """
    结合 table_to_dbs() 的库族列表和 compute_shard 的分片路由，返回表的所有实际数据库 key 列表。

    一个表可能同时存在于多个库（如 ui_bc 和 ui_trust），此函数返回所有库族。
    用于 SQL 预埋时为每个库都生成 INSERT 语句。
    """
    single_db_key = _configured_single_db_key()
    if single_db_key:
        return [single_db_key]

    # 🔧 修复：先归一化表名（如 tbdxfundshare13 → tbdxfundshare），再查映射
    normalized = normalize_table_name(table_name)
    families = table_to_dbs(normalized)
    result = []
    for family in families:
        if family == 'ui_bc':
            result.append('ui_bc')
        elif routing_db:
            result.append(routing_db)
        else:
            result.append(family)
    return result


def get_business_tables_db_map(physical_tables: dict, routing_db: str = None) -> dict:
    """
    对 physical_tables 中每张逻辑表运行 resolve_shard_db()，返回 {逻辑表: 实际db} 映射。
    用于确定 shard.db 和 SQL 文件分派。

    注意：此函数返回单库映射（向后兼容）。如需多库映射，使用 get_business_tables_dbs_map()。
    """
    return {t: resolve_shard_db(t, routing_db) for t in physical_tables}


def get_business_tables_dbs_map(physical_tables: dict, routing_db: str = None) -> dict:
    """
    对 physical_tables 中每张逻辑表运行 resolve_shard_dbs()，返回 {逻辑表: [实际db列表]} 映射。
    用于确定 SQL 预埋时需要写入哪些库。
    """
    return {t: resolve_shard_dbs(t, routing_db) for t in physical_tables}


def validate_sql_db_consistency(physical_tables: dict, shard_db: str, routing_db: str = None) -> list:
    """
    校验每张表的 resolve_shard_dbs() 结果包含 shard.db。
    返回不一致的 (表名, 期望db列表) 列表（空列表 = 全部一致）。
    """
    mismatches = []
    for table_name in physical_tables:
        expected_dbs = resolve_shard_dbs(table_name, routing_db)
        if shard_db not in expected_dbs:
            mismatches.append((table_name, expected_dbs))
    return mismatches


def split_tables_by_db(tables: list) -> Dict[str, list]:
    """
    将表名列表按数据库分组。一个表如果属于多个库，会在每个库的分组中都出现。
    返回: {'ui_bc': ['t1', 't2'], 'ui_trust': ['t1', 't3'], ...}

    示例：tbdxtrustproduct 同时存在于 ui_bc 和 ui_trust，
    则返回 {'ui_bc': [..., 'tbdxtrustproduct', ...], 'ui_trust': [..., 'tbdxtrustproduct', ...]}
    """
    result = {}
    for t in tables:
        dbs = table_to_dbs(t)
        for db in dbs:
            result.setdefault(db, []).append(t)
    return result


# ============================================================
# 表分类（通过 MySQL 查询 TABLE vs VIEW，消除主观判断）
# ============================================================

_SYSTEM_TABLE_PREFIXES = (
    'tsys_', 'act_', 'flow_', 'tbsysarg', 'tbproduct', 'tbrecommender',
    'tblogs', 'tbtrans', 'tbtainfo', 'tbclientmanager',
    'tbdxtrustproduct', 'tbsequence', 'tbdxfundproduct',
    'tbdxcpproduct', 'tbinsureproduct', 'tbprdmanager',
    'tbworkflow',
    'tbbranch',  # 机构表（系统配置表，禁止预埋）
    'tbparam',   # 参数表（系统配置表，禁止预埋）
)

# 精确表名：不允许预埋（不走前缀匹配，在 _is_system_table 中额外判断）
_SYSTEM_TABLE_EXACT = frozenset({
    'tbuserbranch',  # 用户-分支权限辅助表，参与权限过滤但不由菜单用例预埋
})


def _get_db_config_for_classify(env_name: str = 'ifm60-trust', project_dir: str = None) -> tuple:
    """加载数据库配置，返回 (common, databases)。project_dir 优先，否则回退到 SKILL_DIR 推导。"""
    import sys as _sys
    if project_dir and os.path.isdir(project_dir):
        project_root = project_dir
    else:
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(SKILL_DIR)))
    _sys.path.insert(0, project_root)
    from conf.config import Config
    db_config = Config.get_db_config(env_name)
    return db_config['common'], db_config['databases']


def _db_type(common: dict, db_info: dict) -> str:
    return str(db_info.get('db_type') or common.get('db_type') or 'mysql').lower()


def _connect_configured_db(common: dict, db_info: dict, db_name: str):
    db_type = _db_type(common, db_info)
    if db_type == 'oracle':
        try:
            import oracledb
        except ImportError:
            raise ImportError("请安装 oracledb: pip install oracledb")
        database = db_info.get('database', db_name)
        dsn = db_info.get('dsn') or f"{common['host']}:{common.get('port', 1521)}/{database}"
        conn = oracledb.connect(user=db_info['user'], password=db_info['password'], dsn=dsn)
        try:
            conn._hui_db_type = db_type
        except Exception:
            pass
        return conn

    import pymysql
    conn = pymysql.connect(
        host=common['host'], port=common['port'],
        user=db_info['user'], password=db_info['password'],
        database=db_info.get('database', db_name),
        autocommit=True,
    )
    try:
        conn._hui_db_type = db_type
    except Exception:
        pass
    return conn


def _fetch_table_catalog_row(cur, db_type: str, database: str, table_name: str):
    if db_type == 'oracle':
        cur.execute(
            """
            SELECT table_name, 'BASE TABLE' AS table_type
            FROM USER_TABLES
            WHERE table_name = :table_name
            UNION ALL
            SELECT view_name, 'VIEW' AS table_type
            FROM USER_VIEWS
            WHERE view_name = :table_name
            """,
            {"table_name": _oracle_identifier(table_name)},
        )
        return cur.fetchone()

    cur.execute(
        "SELECT TABLE_NAME, TABLE_TYPE FROM information_schema.TABLES "
        "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s",
        (database, table_name),
    )
    return cur.fetchone()


def _resolve_actual_db(db_family: str, databases: dict) -> str:
    """将 db 族名（如 ui_trust）解析为 config 中实际存在的 key（如 ui_trust2）。"""
    if db_family in databases:
        return db_family
    for cfg_db in databases:
        if cfg_db.startswith(db_family):
            return cfg_db
    return db_family


def classify_tables(physical_tables: dict, routing_db: str = None,
                    env_name: str = None, project_dir: str = None, module: str = None) -> dict:
    """
    对 physical_tables 中每张逻辑表，通过 MySQL information_schema 查询 TABLE vs VIEW，
    结合系统表前缀规则，输出确定性分类。

    支持多库表：一个表如果属于多个库（如 ui_bc 和 ui_trust），会在每个库都检查，
    结果中 db 字段为主库（第一个非 ui_bc 的库），all_dbs 字段包含所有库。

    返回:
        {table_name: {
            "type": "BASE TABLE" | "VIEW" | "ERROR",
            "classification": "business" | "system" | "routing" | "view" | "error",
            "db": "主库（向后兼容）",
            "all_dbs": ["ui_bc", "ui_trust"],  # 所有库列表（多库表新增）
            "reason": "分类原因"
        }, ...}
    """
    # 兼容 list 格式：["table1", "table2"] → {"table1": ["table1"], "table2": ["table2"]}
    if isinstance(physical_tables, list):
        physical_tables = {t: [t] for t in physical_tables}
    result = {}

    # 🔧 修复：根据 module 推导 env_name，避免默认使用 ifm60-trust
    if not env_name and module:
        module = normalize_module(module)
        _MODULE_TO_ENV = {
            'dxfund': 'ifm60-dxfund',
            'dxtrust': 'ifm60-trust',
            'fina': 'ifm60-fina',
            'dxcp': 'ifm60-dxcp',
            'hstc': 'ifm60-hstc',
            'insure': 'ifm60-insure',
            'per': 'ifm60-per',
        }
        env_name = _MODULE_TO_ENV.get(module, 'ifm60-trust')

    common, databases = _get_db_config_for_classify(env_name, project_dir)

    # 按实际数据库分组（多库表会在多个组中出现）
    db_groups: Dict[str, list] = {}
    table_all_dbs: Dict[str, list] = {}  # 记录每个表的所有 db
    for logical_name in physical_tables:
        db_families = resolve_shard_dbs(logical_name, routing_db)
        for db_family in db_families:
            actual_db = _resolve_actual_db(db_family, databases)
            db_groups.setdefault(actual_db, []).append(logical_name)
            table_all_dbs.setdefault(logical_name, [])
            if actual_db not in table_all_dbs[logical_name]:
                table_all_dbs[logical_name].append(actual_db)

    # 跟踪已分类的表（多库表可能在多个 db 组中，避免重复覆盖）
    classified: Dict[str, dict] = {}

    for db_name, table_names in db_groups.items():
        if db_name not in databases:
            for t in table_names:
                if t not in classified:
                    classified[t] = _classify_by_name_rules(t)
            continue

        db_info = databases[db_name]
        try:
            db_type = _db_type(common, db_info)
            conn = _connect_configured_db(common, db_info, db_name)
            cur = conn.cursor()

            for logical_name in table_names:
                # 已分类的表跳过（多库表取第一个分类结果）
                if logical_name in classified:
                    continue

                # 系统表规则优先（不查 MySQL）
                if _is_system_table(logical_name):
                    classified[logical_name] = {
                        'type': 'TABLE',
                        'classification': 'system',
                        'db': db_name,
                        'reason': '系统配置表，禁止预埋',
                    }
                    continue

                # 查物理表名：取 physical_tables 中第一个物理名
                phys_names = physical_tables.get(logical_name, [logical_name])
                check_name = phys_names[0] if phys_names else logical_name
                db_database = db_info.get('database', db_name)

                row = _fetch_table_catalog_row(cur, db_type, db_database, check_name)

                if row is None:
                    # 多库表：在一个库未找到不一定是错误，继续检查其他库
                    if len(table_all_dbs.get(logical_name, [])) > 1:
                        continue
                    classified[logical_name] = {
                        'type': 'UNKNOWN',
                        'classification': 'error',
                        'db': db_name,
                        'reason': f'{check_name} 在 {db_database} 中未找到',
                    }
                    continue

                table_type = row[1]  # 'BASE TABLE' or 'VIEW'
                if table_type.upper() == 'VIEW':
                    classified[logical_name] = {
                        'type': 'VIEW',
                        'classification': 'view',
                        'db': db_name,
                        'reason': '视图，禁止预埋',
                    }
                else:
                    family = table_to_db(logical_name)
                    if family == 'ui_bc':
                        classified[logical_name] = {
                            'type': 'BASE TABLE',
                            'classification': 'routing',
                            'db': db_name,
                            'reason': '基础路由表 → REPLACE INTO (_base.sql)',
                        }
                    else:
                        classified[logical_name] = {
                            'type': 'BASE TABLE',
                            'classification': 'business',
                            'db': db_name,
                            'reason': '业务表 → DELETE + INSERT (.sql)',
                        }

            try:
                cur.close()
            except Exception:
                pass
            conn.close()
        except Exception as exc:
            for t in table_names:
                if t not in classified:
                    classified[t] = {
                        'type': 'ERROR',
                        'classification': 'error',
                        'db': db_name,
                        'reason': f'数据库连接失败: {exc}',
                    }

    # 为每个表添加 all_dbs 字段
    for table_name, info in classified.items():
        info['all_dbs'] = table_all_dbs.get(table_name, [info['db']])

    result = classified
    return result


def describe_table_columns(table_name: str, db_name: str, env_name: str = None, project_dir: str = None) -> list[dict]:
    """读取表字段并返回 DESCRIBE 兼容结构。"""
    common, databases = _get_db_config_for_classify(env_name or 'ifm60-trust', project_dir)
    actual_db = _resolve_actual_db(db_name, databases)
    if actual_db not in databases:
        raise ValueError(f"数据库 {db_name} 不在配置中（可用: {list(databases.keys())}）")

    db_info = databases[actual_db]
    db_type = _db_type(common, db_info)
    conn = _connect_configured_db(common, db_info, actual_db)
    cur = conn.cursor()
    try:
        if db_type == 'oracle':
            cur.execute(
                """
                SELECT column_name, data_type, data_length, data_precision, data_scale, nullable, data_default
                FROM USER_TAB_COLUMNS
                WHERE table_name = :table_name
                ORDER BY column_id
                """,
                {"table_name": _oracle_identifier(table_name)},
            )
            rows = cur.fetchall()
            return [_oracle_describe_row(row) for row in rows if _row_value(row, 'COLUMN_NAME', 0)]

        cur.execute(f'DESCRIBE `{table_name}`')
        rows = cur.fetchall()
        return [
            {
                'Field': row[0],
                'Type': row[1],
                'Null': row[2],
                'Key': row[3],
                'Default': row[4],
                'Extra': row[5],
            }
            for row in rows
        ]
    finally:
        try:
            cur.close()
        finally:
            conn.close()


def _oracle_describe_row(row) -> dict:
    return {
        "Field": _cache_identifier(_row_value(row, 'COLUMN_NAME', 0)),
        "Type": _oracle_column_type(row),
        "Null": "YES" if str(_row_value(row, 'NULLABLE', 5) or '').upper() == 'Y' else "NO",
        "Key": "",
        "Default": _row_value(row, 'DATA_DEFAULT', 6),
        "Extra": "",
    }


def _oracle_column_type(row) -> str:
    data_type = str(_row_value(row, 'DATA_TYPE', 1) or '').lower()
    data_length = _row_value(row, 'DATA_LENGTH', 2)
    data_precision = _row_value(row, 'DATA_PRECISION', 3)
    data_scale = _row_value(row, 'DATA_SCALE', 4)
    if data_type in ('varchar2', 'nvarchar2', 'char', 'nchar', 'raw') and data_length:
        return f"{data_type}({data_length})"
    if data_type == 'number':
        if data_precision is not None and data_scale is not None:
            return f"number({data_precision},{data_scale})"
        if data_precision is not None:
            return f"number({data_precision})"
    return data_type


def _row_value(row, key: str, index: int):
    if isinstance(row, dict):
        return row.get(key) if key in row else row.get(key.lower())
    if isinstance(row, (list, tuple)) and len(row) > index:
        return row[index]
    return None


def _oracle_identifier(name: str) -> str:
    return str(name or '').strip().strip('"').upper()


def _cache_identifier(name: str) -> str:
    return str(name or '').strip().strip('"').lower()


def _is_system_table(table_name: str) -> bool:
    """检查表名是否为系统配置表（前缀匹配 + 精确匹配）。"""
    t = table_name.lower()
    # 精确匹配：先检查完整表名（如 tbclient）
    if t in _SYSTEM_TABLE_EXACT:
        return True
    # 前缀匹配
    for prefix in _SYSTEM_TABLE_PREFIXES:
        if t.startswith(prefix):
            return True
    return False


def _classify_by_name_rules(table_name: str) -> dict:
    """纯名称规则分类（无法连接数据库时的回退）。"""
    if _is_system_table(table_name):
        return {
            'type': 'TABLE',
            'classification': 'system',
            'db': '',
            'reason': '系统配置表（前缀匹配），禁止预埋',
        }
    for suffix in ('qry', 'shareqry', 'squareqry'):
        if suffix in table_name.lower():
            return {
                'type': 'UNKNOWN',
                'classification': 'view',
                'db': '',
                'reason': f'名称含 {suffix}，可能是视图（本地回退，未查 MySQL）',
            }
    return {
        'type': 'UNKNOWN',
        'classification': 'business',
        'db': table_to_db(table_name),
        'reason': '名称规则回退，未确认',
    }


# ============================================================
# 0.5 数据库前缀剥离
# ============================================================

def strip_db_prefix(table_ref: str) -> str:
    """
    剥离 SQL 中的数据库前缀，只返回裸表名。
    例: 'ui_bc.tbacccfm' → 'tbacccfm'
         'tbacccfm' → 'tbacccfm'
    """
    if '.' in table_ref:
        return table_ref.split('.')[-1]
    return table_ref


# ============================================================
# 1. 日志 SQL 提取器
# ============================================================

def extract_sql_requirements(log_text: str) -> dict:
    """
    从 SSH 日志文本中提取 SQL 需求信息。

    支持两种日志格式：
    - 非分片:  SQL执行,当前SQL:select * from tbdxfundtranscfmqry where serial_no=? and ta_code=?
    - 分片:    Actual SQL: ds0 ::: select ... from tbshare0 where in_client_no=? and seller_code=?

    返回:
    {
        "tables":        ["tbdxfundtranscfmqry", ...],          # 所有涉及的表名
        "where_columns": {"tbdxfundtranscfmqry": ["serial_no", "ta_code", ...]},  # WHERE 字段
        "join_tables":   ["tbdxfundtransreq", ...],             # JOIN 关联表
        "shard_info":    {"ds": "ds0", "db": "ui_trust1"},      # 分片信息(可选)
        "raw_sqls":      ["select ...", ...],                   # 原始 SQL 列表
        "join_key_columns": {"tbdxtrusthissharechg16": ["in_client_no", ...]},  # JOIN ON 列(不含 WHERE)
    }
    """
    result = {
        "tables": [],
        "where_columns": {},
        "join_tables": [],
        "shard_info": {},
        "raw_sqls": [],
        "join_key_columns": {},
    }

    # 提取所有 SQL 语句（两种格式）
    sqls = _extract_sqls_from_log(log_text)
    if not sqls:
        return result

    for sql_info in sqls:
        sql = sql_info["sql"]
        shard = sql_info.get("shard")  # ds0 / ds1 / None
        result["raw_sqls"].append(sql)

        # 记录分片信息
        if shard and not result["shard_info"]:
            result["shard_info"]["ds"] = shard

        ast_requirements = _extract_sql_requirements_ast(sql)
        if ast_requirements:
            _merge_sql_requirements(result, ast_requirements)
            continue

        # 提取主表名
        main_table = _extract_main_table(sql)
        if main_table and main_table not in result["tables"]:
            result["tables"].append(main_table)

        # 提取 JOIN 表
        join_tables = _extract_join_tables(sql)
        for jt in join_tables:
            if jt not in result["join_tables"] and jt not in result["tables"]:
                result["join_tables"].append(jt)

        # 🔧 修复：提取子查询中的表（如 WHERE ... IN (SELECT ... FROM tbhissasetacc)）
        for sub_table in _extract_implicit_join_tables(sql):
            if sub_table not in result["tables"] and sub_table not in result["join_tables"]:
                result["join_tables"].append(sub_table)

        # 提取 WHERE 条件字段（按归属表分组）
        where_by_table = _extract_where_columns(sql)
        for table_name, cols in where_by_table.items():
            # 无别名的字段归到主表
            if table_name == '_unmapped':
                target = main_table
            else:
                target = table_name
            if not target:
                continue
            existing = result["where_columns"].get(target, [])
            for col in cols:
                if col not in existing:
                    existing.append(col)
            result["where_columns"][target] = existing

        # 提取 JOIN ON 条件字段: 先捕获为 join_key_columns(保留 JOIN 键标记),
        # 再合并进 where_columns(仍参与必填列判定)
        join_on_by_table = _extract_join_on_columns(sql)
        for table_name, cols in join_on_by_table.items():
            if table_name == '_unmapped':
                target = main_table
            else:
                target = table_name
            if not target:
                continue

            # 写入 join_key_columns (保留 JOIN 键标记，用于后续 prompt 提示跨表一致性)
            jk_existing = result["join_key_columns"].get(target, [])
            for col in cols:
                if col not in jk_existing:
                    jk_existing.append(col)
            result["join_key_columns"][target] = jk_existing

            # 合并进 where_columns (原有逻辑不变)
            existing = result["where_columns"].get(target, [])
            for col in cols:
                if col not in existing:
                    existing.append(col)
            result["where_columns"][target] = existing

    return result


def _merge_sql_requirements(result: dict, extracted: dict) -> None:
    for table in extracted.get("tables", []):
        if table not in result["tables"]:
            result["tables"].append(table)
    for table in extracted.get("join_tables", []):
        if table not in result["join_tables"] and table not in result["tables"]:
            result["join_tables"].append(table)
    for key in ("where_columns", "join_key_columns"):
        for table, cols in extracted.get(key, {}).items():
            existing = result[key].get(table, [])
            for col in cols:
                if col not in existing:
                    existing.append(col)
            result[key][table] = existing
    for table, cols in extracted.get("join_key_columns", {}).items():
        existing = result["where_columns"].get(table, [])
        for col in cols:
            if col not in existing:
                existing.append(col)
        result["where_columns"][table] = existing


def build_ast_diagnostics(log_text: str) -> dict:
    """Build human-readable and structured AST diagnostics for captured SELECT SQLs."""
    selects = []
    for index, sql_info in enumerate(_extract_sqls_from_log(log_text), start=1):
        raw_sql = sql_info["sql"]
        item = {
            "index": index,
            "raw_sql": raw_sql,
            "normalized_sql": _normalize_sql_with_ast(raw_sql),
            "tables": [],
            "join_tables": [],
            "where_columns": {},
            "join_key_columns": {},
            "diagnostics": [],
        }
        extracted = _extract_sql_requirements_ast(raw_sql)
        if extracted:
            item.update(extracted)
        else:
            item["diagnostics"].append("sqlglot_parse_failed_or_unavailable")
            main_table = _extract_main_table(raw_sql)
            if main_table:
                item["tables"] = [main_table]
            item["join_tables"] = _extract_join_tables(raw_sql) + _extract_implicit_join_tables(raw_sql)
            item["where_columns"] = _extract_where_columns(raw_sql)
            item["join_key_columns"] = _extract_join_on_columns(raw_sql)
        selects.append(item)
    diagnostics = {
        "select_count": len(selects),
        "selects": selects,
    }
    diagnostics["text"] = _render_ast_diagnostics_text(selects)
    return diagnostics


def _normalize_sql_with_ast(sql: str) -> str:
    if sqlglot is None:
        return _sanitize_sql_for_ast(sql)
    try:
        return sqlglot.parse_one(_sanitize_sql_for_ast(sql), read="mysql").sql(dialect="mysql")
    except Exception:
        try:
            return sqlglot.parse_one(_sanitize_sql_for_ast(sql)).sql(dialect="mysql")
        except Exception:
            return _sanitize_sql_for_ast(sql)


def _render_ast_diagnostics_text(selects: list[dict]) -> str:
    blocks = []
    for item in selects:
        index = item.get("index", "?")
        blocks.append(
            "\n".join(
                [
                    f"-- raw_sql[{index}]",
                    str(item.get("raw_sql", "")),
                    "",
                    f"-- normalized_sql[{index}]",
                    str(item.get("normalized_sql", "")),
                    "",
                    "-- tables",
                    "-- main: " + ", ".join(item.get("tables", []) or ["(none)"]),
                    "-- joins: " + ", ".join(item.get("join_tables", []) or ["(none)"]),
                    "",
                    "-- where_columns",
                    _format_columns_comment(item.get("where_columns", {})),
                    "",
                    "-- join_key_columns",
                    _format_columns_comment(item.get("join_key_columns", {})),
                    "",
                    "-- diagnostics",
                    "-- " + ", ".join(item.get("diagnostics", []) or ["ok"]),
                ]
            )
        )
    return "\n\n".join(blocks).strip() + ("\n" if blocks else "")


def _format_columns_comment(columns_by_table: dict) -> str:
    if not columns_by_table:
        return "-- (none)"
    return "\n".join(
        f"-- {table}: {', '.join(cols) if cols else '(none)'}"
        for table, cols in columns_by_table.items()
    )


def _extract_sql_requirements_ast(sql: str) -> Optional[dict]:
    """Use sqlglot AST extraction for table/column ownership when available."""
    if sqlglot is None or exp is None:
        return None
    try:
        parsed = sqlglot.parse_one(_sanitize_sql_for_ast(sql), read="mysql")
    except Exception:
        try:
            parsed = sqlglot.parse_one(_sanitize_sql_for_ast(sql))
        except Exception:
            return None
    extracted = {
        "tables": [],
        "join_tables": [],
        "where_columns": {},
        "join_key_columns": {},
    }
    for select in parsed.find_all(exp.Select):
        _extract_select_requirements_ast(select, extracted)
    return extracted if extracted["tables"] or extracted["join_tables"] else None


def _sanitize_sql_for_ast(sql: str) -> str:
    value = str(sql or "").strip().rstrip(";")
    value = re.sub(r"\s+当前参数:.*$", "", value, flags=re.IGNORECASE | re.DOTALL)
    return value


def _extract_select_requirements_ast(select: Any, extracted: dict) -> None:
    alias_map, main_table, join_tables = _ast_select_sources(select)
    if main_table:
        _append_unique(extracted["tables"], main_table)
    for table in join_tables:
        if table and table not in extracted["tables"]:
            _append_unique(extracted["join_tables"], table)

    where = select.args.get("where")
    if where is not None:
        for column in where.find_all(exp.Column):
            table = _resolve_ast_column_table(column, alias_map, main_table)
            if table:
                _append_column(extracted["where_columns"], table, column.name)

    for join in select.args.get("joins") or []:
        on_expr = join.args.get("on")
        if on_expr is None:
            continue
        for column in on_expr.find_all(exp.Column):
            table = _resolve_ast_column_table(column, alias_map, main_table)
            if table:
                _append_column(extracted["join_key_columns"], table, column.name)


def _ast_select_sources(select: Any) -> tuple[dict[str, str], str, list[str]]:
    alias_map: dict[str, str] = {}
    main_table = ""
    join_tables: list[str] = []

    from_expr = select.args.get("from")
    if from_expr is None:
        from_expr = select.args.get("from_")
    if from_expr is not None:
        main_table = _ast_source_table(from_expr.this)
        _record_ast_source_alias(alias_map, from_expr.this, main_table)

    for join in select.args.get("joins") or []:
        table = _ast_source_table(join.this)
        if table:
            join_tables.append(table)
        _record_ast_source_alias(alias_map, join.this, table)

    return alias_map, main_table, join_tables


def _ast_source_table(source: Any) -> str:
    if source is None:
        return ""
    if isinstance(source, exp.Table):
        return strip_db_prefix(source.name)
    if isinstance(source, exp.Subquery):
        inner = source.this
        if isinstance(inner, exp.Select):
            inner_from = inner.args.get("from") or inner.args.get("from_")
            if inner_from is not None:
                return _ast_source_table(inner_from.this)
    return ""


def _record_ast_source_alias(alias_map: dict[str, str], source: Any, table: str) -> None:
    if not table or source is None:
        return
    aliases = {table.lower()}
    alias = getattr(source, "alias_or_name", "") or ""
    if alias:
        aliases.add(str(alias).lower())
    if isinstance(source, exp.Table):
        name = getattr(source, "name", "") or ""
        if name:
            aliases.add(str(name).lower())
    for alias_name in aliases:
        alias_map[alias_name] = table


def _resolve_ast_column_table(column: Any, alias_map: dict[str, str], main_table: str) -> str:
    table_alias = str(column.table or "").lower()
    if table_alias:
        return alias_map.get(table_alias, "")
    if len(set(alias_map.values())) == 1:
        return next(iter(alias_map.values()))
    return main_table


def _append_unique(items: list, value: str) -> None:
    if value and value not in items:
        items.append(value)


def _append_column(columns_by_table: dict, table: str, column: str) -> None:
    if not table or not column:
        return
    existing = columns_by_table.setdefault(table, [])
    if column not in existing:
        existing.append(column)


def _extract_sqls_from_log(log_text: str) -> list:
    """
    从日志文本中提取所有 SQL 语句。
    返回 [{"sql": "...", "shard": "ds0"|None}, ...]

    支持多行 SQL：SELECT 可能跨多行（JOIN、WHERE、子查询等），
    直到遇到下一个日志标记（Actual SQL: / 当前SQL:）或文本结束。
    """
    results = []

    # 格式1: 分片日志 — Actual SQL: ds0 ::: select ...（可能跨多行）
    # 捕获直到下一个 "Actual SQL:" 或 "当前SQL:" 或文本结束
    pattern_shard = re.compile(
        r'Actual\s+SQL:\s*(ds\d+)\s*:::\s*(select\s+.*?)(?=\n[^\n]*(?:Actual\s+SQL:|当前SQL:)|\Z)',
        re.IGNORECASE | re.DOTALL
    )
    for m in pattern_shard.finditer(log_text):
        results.append({"sql": m.group(2).strip(), "shard": m.group(1)})

    # 格式2: 非分片日志 — SQL执行,当前SQL:select ...（可能跨多行）
    # 捕获直到下一个日志标记或文本结束
    pattern_normal = re.compile(
        r'(?:SQL执行,)?当前SQL:\s*(select\s+.*?)(?=\n[^\n]*(?:Actual\s+SQL:|当前SQL:)|\Z)',
        re.IGNORECASE | re.DOTALL
    )
    for m in pattern_normal.finditer(log_text):
        sql = m.group(1).strip()
        # 避免重复（分片日志可能同时匹配）
        if not any(sql in r["sql"] or r["sql"] in sql for r in results):
            results.append({"sql": sql, "shard": None})

    return results


def _extract_main_table(sql: str) -> Optional[str]:
    """从 SELECT 语句中提取主表名（FROM 后面的第一个表）"""
    # 匹配 FROM tablename [别名]，支持 db.table 格式
    m = re.search(
        r'\bFROM\s+(?:`?(\w+)`?\.)?`?(\w+)`?(?:\s+(?:AS\s+)?(\w+))?',
        sql, re.IGNORECASE
    )
    if m:
        # m.group(1) = 可能的 db 前缀, m.group(2) = 表名
        return m.group(2)
    return None


def _extract_join_tables(sql: str) -> List[str]:
    """从 SELECT 语句中提取 JOIN 的关联表"""
    tables = []
    # 匹配 JOIN tablename 或 JOIN db.tablename
    for m in re.finditer(
        r'\bJOIN\s+(?:`?(\w+)`?\.)?`?(\w+)`?',
        sql, re.IGNORECASE
    ):
        table = m.group(2)
        if table and table not in tables:
            tables.append(table)
    return tables


def _extract_implicit_join_tables(sql: str) -> List[str]:
    """从隐式 JOIN（逗号分隔）中提取表名。

    处理: FROM table1 a, table2 b, table3 c WHERE ...
    注意: 不包含主表（FROM 后面第一个表），避免重复。
    支持嵌套查询：递归提取子查询中的表名。
    """
    tables = []

    # 🔧 修复：递归提取子查询中的表名
    # 匹配所有子查询：(select ... from ...)
    # 使用括号深度匹配来正确处理嵌套括号
    def _find_subqueries(s):
        """提取所有顶层子查询（括号内的 SELECT 语句）"""
        results = []
        i = 0
        while i < len(s):
            if s[i] == '(':
                # 找到匹配的右括号
                depth = 1
                j = i + 1
                while j < len(s) and depth > 0:
                    if s[j] == '(':
                        depth += 1
                    elif s[j] == ')':
                        depth -= 1
                    j += 1
                # 提取括号内的内容
                inner = s[i+1:j-1].strip()
                # 检查是否是 SELECT 语句
                if inner.upper().startswith('SELECT'):
                    results.append(inner)
                i = j
            else:
                i += 1
        return results

    for subquery in _find_subqueries(sql):
        # 递归提取子查询中的隐式 JOIN 表
        for t in _extract_implicit_join_tables(subquery):
            if t not in tables:
                tables.append(t)
        # 也提取子查询的主表
        main = _extract_main_table(subquery)
        if main and main not in tables:
            tables.append(main)
        # 🔧 修复：子查询中的显式 JOIN 表也需要提取（如 LEFT JOIN tbhissasetacc）
        for jt in _extract_join_tables(subquery):
            if jt not in tables:
                tables.append(jt)

    # 匹配 FROM 子句：FROM table1 [alias1], table2 [alias2], ... WHERE/ORDER/GROUP/LIMIT
    from_match = re.search(
        r'\bFROM\s+(.+?)(?:\bWHERE\b|\bORDER\b|\bGROUP\b|\bLIMIT\b|\bHAVING\b|$)',
        sql, re.IGNORECASE | re.DOTALL
    )
    if not from_match:
        return tables

    from_clause = from_match.group(1).strip()

    # 用逗号分割，提取每个表名
    # 注意：需要排除子查询（括号内的内容）
    parts = []
    depth = 0
    current = ""
    for char in from_clause:
        if char == '(':
            depth += 1
            current += char
        elif char == ')':
            depth -= 1
            current += char
        elif char == ',' and depth == 0:
            parts.append(current.strip())
            current = ""
        else:
            current += char
    if current.strip():
        parts.append(current.strip())

    # 从每个部分提取表名（跳过第一个，那是主表）
    for i, part in enumerate(parts):
        if i == 0:
            continue  # 跳过主表
        # 跳过子查询（以括号开头的）
        if part.startswith('('):
            continue
        # 匹配 tablename [alias] 或 db.tablename [alias]
        m = re.match(
            r'(?:`?(\w+)`?\.)?`?(\w+)`?(?:\s+(?:AS\s+)?(\w+))?',
            part, re.IGNORECASE
        )
        if m:
            table = m.group(2)
            if table and table.upper() not in ('SELECT', 'WHERE', 'AND', 'OR', 'ON'):
                tables.append(table)

    return tables


def _extract_alias_table_map(sql: str) -> Dict[str, str]:
    """
    从 SELECT 语句中提取表别名 → 真实表名的映射。
    例: FROM tbdxtrustshare1 a JOIN tbclient2 b ON ...
        → {'a': 'tbdxtrustshare1', 'b': 'tbclient2'}
    """
    alias_map = {}

    # 匹配 FROM/JOIN 后的表名和别名: FROM tablename [AS] alias
    pattern = re.compile(
        r'\b(?:FROM|JOIN)\s+(?:`?(\w+)`?\.)?`?(\w+)`?(?:\s+(?:AS\s+)?(\w+))?',
        re.IGNORECASE
    )
    for m in pattern.finditer(sql):
        table = m.group(2)
        alias = m.group(3)
        if table and alias:
            # 排除 SQL 关键字被误识别为别名
            if alias.upper() not in ('ON', 'WHERE', 'AND', 'OR', 'SET', 'LEFT', 'RIGHT',
                                      'INNER', 'OUTER', 'CROSS', 'NATURAL', 'GROUP', 'ORDER',
                                      'HAVING', 'LIMIT', 'UNION', 'SELECT', 'AS'):
                alias_map[alias.lower()] = table

    return alias_map


def _extract_where_columns(sql: str) -> Dict[str, List[str]]:
    """
    从 SELECT 语句中提取 WHERE 条件里的字段名，按归属表分组。
    支持: col=?, col>=?, col<=?, col IN (?), col LIKE ? 等

    返回: {'tbdxtrustshare1': ['open_branch'], 'tbclient2': ['status'], '_unmapped': ['col']}
    """
    columns = {}  # table_name → [col1, col2, ...]

    # 找到 WHERE 子句
    where_match = re.search(r'\bWHERE\b(.+?)(?:\bORDER\b|\bGROUP\b|\bLIMIT\b|\bHAVING\b|$)',
                            sql, re.IGNORECASE | re.DOTALL)
    if not where_match:
        return columns

    where_clause = where_match.group(1)

    # 获取别名映射
    alias_map = _extract_alias_table_map(sql)

    # 提取所有 "表别名.字段名 操作符 ?" 的模式
    col_pattern = re.compile(
        r'(?:`?(\w+)`?\.)?`?(\w+)`?\s*(?:=|>=|<=|>|<|!=|<>|LIKE|IN|IS)\s*',
        re.IGNORECASE
    )
    for m in col_pattern.finditer(where_clause):
        alias = m.group(1)  # 表别名（可能为 None）
        col = m.group(2)    # 字段名
        if not col or col.upper() in ('AND', 'OR', 'NOT', 'NULL', 'SELECT'):
            continue

        # 通过别名解析真实表名
        if alias:
            table_name = alias_map.get(alias.lower(), '_unmapped')
        else:
            table_name = '_unmapped'

        if table_name not in columns:
            columns[table_name] = []
        if col not in columns[table_name]:
            columns[table_name].append(col)

    return columns


def _extract_join_on_columns(sql: str) -> Dict[str, List[str]]:
    """
    从 JOIN ON 子句中提取列引用，按归属表分组。

    当前 WHERE 提取只覆盖过滤条件，但 JOIN 表的关联列（如 ON a.in_client_no = b.client_no）
    同样决定预埋数据能否被查到。此函数补齐 ON 子句中缺失的列。

    返回: {'tbclientseller2': ['client_no', 'status'], 'tbclientmap': ['prd_code'], ...}
    """
    columns = {}
    alias_map = _extract_alias_table_map(sql)

    # 定位 ON 子句: ON ... 直到下一个 JOIN / WHERE / ORDER / GROUP / LIMIT
    on_pattern = re.compile(
        r'\bON\b\s+(.+?)(?=\bJOIN\b|\bLEFT\b|\bRIGHT\b|\bINNER\b|\bOUTER\b|\bCROSS\b|\bWHERE\b|\bORDER\b|\bGROUP\b|\bLIMIT\b|\bHAVING\b|$)',
        re.IGNORECASE | re.DOTALL,
    )

    # ON 子句内匹配 alias.col（不要求 operator，因为 RHS 可能也是列名）
    col_ref = re.compile(
        r'`?(\w+)`?\.`?(\w+)`?',
        re.IGNORECASE,
    )

    _KEYWORDS = frozenset(
        ['AND', 'OR', 'NOT', 'NULL', 'ON', 'JOIN', 'SELECT', 'WHERE',
         'LEFT', 'RIGHT', 'INNER', 'OUTER', 'CROSS', 'NATURAL', 'STRAIGHT',
         'GROUP', 'ORDER', 'HAVING', 'LIMIT', 'UNION', 'AS', 'SET', 'IS']
    )

    for on_match in on_pattern.finditer(sql):
        on_clause = on_match.group(1)
        for cm in col_ref.finditer(on_clause):
            alias = cm.group(1)
            col = cm.group(2)
            if not col or col.upper() in _KEYWORDS or alias.upper() in _KEYWORDS:
                continue

            table_name = alias_map.get(alias.lower(), '_unmapped')

            if table_name not in columns:
                columns[table_name] = []
            if col not in columns[table_name]:
                columns[table_name].append(col)

    return columns


# ============================================================
# 1.1 物理表名提取 + 归一化
# ============================================================

_NORMALIZING_TABLES = set()

def normalize_table_name(physical_name: str) -> str:
    """
    剥离分片后缀，从物理表名还原逻辑表名。

    判断优先级：
      1. 尝试剥离尾部数字 → base 名匹配已知分片表前缀 → 返回 base（分片物理表）
      2. 尝试剥离尾部数字 → base 名在缓存中 → 返回 base（分片物理表）
      3. 名称本身在缓存中 → 返回原样（逻辑表自带数字后缀，如 tbclient2）
      4. 无法剥离或无匹配 → 返回原样
    """
    # 防止递归：如果正在规范化这个表名，直接返回原样
    if physical_name in _NORMALIZING_TABLES:
        return physical_name

    _NORMALIZING_TABLES.add(physical_name)
    try:
        _SHARD_TABLE_PREFIXES = [
            'tbdxtrusthissharechg',
            'tbdxtrustshare',
            'tbdxtrusttransreq',
            'tbdxtrusttranscfm',
            'tbdxtrustsquare',
            'tbdxfundhissharechg',
            'tbdxfundshare',
            'tbdxfundtransreq',
            'tbdxfundtranscfm',
            'tbdxfundsquare',
            'tbfinahissharechg',
            'tbfinashare',
            'tbfinatransreq',
            'tbfinatranscfm',
            'tbfinasquare',
            'tbpensionhissharechg',
            'tbpensionshare',
            'tbpensiontransreq',
            'tbpensiontranscfm',
            'tbpensionsquare',
            'tbclientseller',
            'tbclient',
            'tbclientmap',
        ]

        # 尝试剥离尾部数字
        m = re.match(r'^(.+?[^\d])(\d+)$', physical_name)
        if m:
            base = m.group(1)

            # 优先：base 名匹配已知分片表前缀 → 确认是分片后缀
            for prefix in _SHARD_TABLE_PREFIXES:
                if base == prefix or base.startswith(prefix):
                    return base

            # 次优：base 名在缓存中 → 确认是分片后缀
            if get_cached_required_cols(base) is not None or get_cached_column_types(base) is not None:
                return base

        # 名称本身在缓存中 → 逻辑表（如 tbclient2），原样返回
        if get_cached_required_cols(physical_name) is not None or get_cached_column_types(physical_name) is not None:
            return physical_name

        return physical_name
    finally:
        _NORMALIZING_TABLES.discard(physical_name)


def extract_physical_tables(log_text: str) -> dict:
    """
    从日志 SQL 中提取所有物理表名（含分片后缀），按逻辑表名分组。

    返回:
    {
        "tbdxtrusthissharechg": ["tbdxtrusthissharechg1", "tbdxtrusthissharechg16"],
        "tbclientseller": ["tbclientseller2", "tbclientseller16"],
        "tbclientmap": ["tbclientmap"],
        "tbtainfo": ["tbtainfo"],
    }
    """
    sqls = _extract_sqls_from_log(log_text)
    physical_tables = {}

    for sql_info in sqls:
        sql = sql_info["sql"]

        # 提取主表
        main_table = _extract_main_table(sql)
        if main_table:
            logical = normalize_table_name(main_table)
            physical_tables.setdefault(logical, [])
            if main_table not in physical_tables[logical]:
                physical_tables[logical].append(main_table)

        # 提取显式 JOIN 表
        for jt in _extract_join_tables(sql):
            logical = normalize_table_name(jt)
            physical_tables.setdefault(logical, [])
            if jt not in physical_tables[logical]:
                physical_tables[logical].append(jt)

        # 🔧 修复：提取隐式 JOIN 表（逗号分隔，如 FROM table1 a, table2 b, table3 c）
        for jt in _extract_implicit_join_tables(sql):
            logical = normalize_table_name(jt)
            physical_tables.setdefault(logical, [])
            if jt not in physical_tables[logical]:
                physical_tables[logical].append(jt)

    return physical_tables


def merge_physical_tables(*pt_dicts: dict) -> dict:
    """
    合并多份 physical_tables 结果（双路探勘时用）。
    同一逻辑表下的物理表名取并集，保持插入顺序。
    """
    merged = {}
    for pt in pt_dicts:
        if not pt:
            continue
        for logical, physical_list in pt.items():
            merged.setdefault(logical, [])
            for p in physical_list:
                if p not in merged[logical]:
                    merged[logical].append(p)
    return merged


# ============================================================
# 1.5 双路探勘日志合并（无必填字段双路探勘专用）
# ============================================================

def extract_dual_route_requirements(
    empty_query_log: str,
    param_query_log: str,
) -> dict:
    """
    双路探勘日志合并 — 无必填字段时使用。

    问题背景：
      之前 queryall.py 为了绕过必填项校验，混入了单例查询参数（如客户号），
      导致底层触发 Hash 分片路由（查 share1 等物理表），未能覆盖全局汇总表（如 shareqry）。

    解决方案（配置前置）：
      探勘时执行两次查询，分别抓取日志：
      1. 空查日志（empty_query_log）：不填任何具体业务条件，直接点查询
         → 抓取全局汇总表（带 qry 后缀等，如 shareqry）
      2. 带参日志（param_query_log）：填入具体查询条件
         → 抓取分片物理表（如 share1、share2）

    两张日志的表名取并集，确保预埋时双写覆盖全量和单例的独立路由。

    参数:
        empty_query_log:  空查操作产生的 SSH 日志文本
        param_query_log:  带参查询操作产生的 SSH 日志文本

    返回:
    {
        "empty_query":       extract_sql_requirements(empty_query_log) 的结果,
        "param_query":       extract_sql_requirements(param_query_log) 的结果,
        "merged_tables":     ["shareqry", "share1", ...],       # 两路表名并集
        "merged_where":      {"shareqry": [...], "share1": [...]},  # 合并 WHERE 字段
        "merged_shard_info": {"ds": "ds0"},                     # 分片信息（取带参的）
        "merged_join_key_columns": {"tba": ["client_no"], ...}, # 合并的 JOIN 键列
        "route_summary": {
            "empty_query_tables":  ["shareqry"],               # 空查独有
            "param_query_tables":  ["share1"],                 # 带参独有
            "shared_tables":       ["tbtainfo", ...],          # 两路共有的
        }
    }
    """
    empty_req = extract_sql_requirements(empty_query_log)
    param_req = extract_sql_requirements(param_query_log)

    # 合并表名（保留顺序：空查在前，带参补充）
    merged_tables = list(empty_req["tables"])
    for t in param_req["tables"]:
        if t not in merged_tables:
            merged_tables.append(t)

    # JOIN 表也合并
    for t in empty_req.get("join_tables", []):
        if t not in merged_tables:
            merged_tables.append(t)
    for t in param_req.get("join_tables", []):
        if t not in merged_tables:
            merged_tables.append(t)

    # 合并 WHERE 字段
    merged_where = {}
    for table, cols in empty_req.get("where_columns", {}).items():
        merged_where.setdefault(table, []).extend(
            c for c in cols if c not in merged_where.get(table, [])
        )
    for table, cols in param_req.get("where_columns", {}).items():
        merged_where.setdefault(table, []).extend(
            c for c in cols if c not in merged_where.get(table, [])
        )

    # 合并 JOIN 键列 (保留跨表一致性标记)
    merged_join_key_columns = {}
    for table, cols in empty_req.get("join_key_columns", {}).items():
        merged_join_key_columns.setdefault(table, []).extend(
            c for c in cols if c not in merged_join_key_columns.get(table, [])
        )
    for table, cols in param_req.get("join_key_columns", {}).items():
        merged_join_key_columns.setdefault(table, []).extend(
            c for c in cols if c not in merged_join_key_columns.get(table, [])
        )

    # 分片信息取带参的（更准确）
    merged_shard = param_req.get("shard_info", {}) or empty_req.get("shard_info", {})

    # 路由分类
    empty_only = set(empty_req["tables"]) - set(param_req["tables"])
    param_only = set(param_req["tables"]) - set(empty_req["tables"])
    shared = set(empty_req["tables"]) & set(param_req["tables"])

    return {
        "empty_query": empty_req,
        "param_query": param_req,
        "merged_tables": merged_tables,
        "merged_where": merged_where,
        "merged_shard_info": merged_shard,
        "merged_join_key_columns": merged_join_key_columns,
        "route_summary": {
            "empty_query_tables": sorted(empty_only),
            "param_query_tables": sorted(param_only),
            "shared_tables": sorted(shared),
        },
    }


# ============================================================
# 2. DESCRIBE 结果过滤器
# ============================================================

def filter_describe_result(describe_rows: list) -> dict:
    """
    过滤 DESCRIBE tablename 的结果，只保留必填列。

    输入: DESCRIBE 的行列表，每行是 dict 或 tuple
          dict 格式: {"Field": "col_name", "Null": "NO", "Default": None, "Key": "PRI", ...}
          tuple 格式: (field, type, null, key, default, extra) — MySQL 标准 DESCRIBE 输出

    返回:
    {
        "required_columns": ["col1", "col2", ...],   # NOT NULL + 无默认值
        "nullable_columns": ["col3", ...],            # 允许 NULL 或有默认值
        "primary_keys": ["col1", ...],                # 主键
        "all_columns": ["col1", "col2", "col3", ...], # 全部列
    }
    """
    required = []
    nullable = []
    pks = []
    all_cols = []
    col_types = {}

    for row in describe_rows:
        if isinstance(row, dict):
            field = row.get('Field', '')
            null_ok = row.get('Null', 'YES').upper() == 'YES'
            default = row.get('Default')
            key = row.get('Key', '')
            col_type = row.get('Type', '')
        elif isinstance(row, (list, tuple)) and len(row) >= 4:
            field = row[0]
            null_ok = str(row[2]).upper() == 'YES'
            default = row[4] if len(row) > 4 else None
            key = row[3] if len(row) > 3 else ''
            col_type = str(row[1])
        else:
            continue

        if not field:
            continue

        all_cols.append(field)
        col_types[field] = col_type
        if key == 'PRI':
            pks.append(field)

        # 必填 = NOT NULL 且（无默认值 或 是主键）
        # 主键即使有占位默认值（如 " "）也必须预埋，否则数据无意义
        is_no_default = default is None or str(default).upper() in ('NONE', 'NULL', '')
        is_required = not null_ok and (is_no_default or key == 'PRI')
        if is_required:
            required.append(field)
        else:
            nullable.append(field)

    return {
        "required_columns": required,
        "nullable_columns": nullable,
        "primary_keys": pks,
        "all_columns": all_cols,
        "column_types": col_types,
    }


# 日期字段识别正则（列名含 date/time 且类型为 int/bigint）
_DATE_PATTERN = re.compile(r'date|time|_dt$', re.IGNORECASE)

def is_date_column(col: str, col_type: str) -> bool:
    """判断某列是否为日期类 int 字段（如 cfm_date, trans_date, modi_time）"""
    return bool(_DATE_PATTERN.search(col)) and col_type.startswith(('int', 'bigint'))


# ============================================================
# 3. 表结构缓存管理
# ============================================================

def load_schema_cache(module: str = None) -> dict:
    """加载表结构缓存。module=None 时加载所有库族合并"""
    if module:
        # 先尝试按库族加载（新方式）
        family = _MODULE_TO_DB_FAMILY.get(module)
        if family:
            return load_db_cache(family)
        return _load_module_cache(module)
    single_db_key = _configured_single_db_key()
    if single_db_key:
        return load_db_cache(single_db_key)

    merged = {}
    # 新方式：按库族加载
    for family in _DB_FAMILY_CACHE_FILES:
        merged.update(load_db_cache(family))
    # 兼容旧方式：按模块加载（仅补充新缓存中不存在的表）
    for mod in _MODULE_CACHE_FILES:
        mod_cache = _load_module_cache(mod)
        for k, v in mod_cache.items():
            if k not in merged:
                merged[k] = v
    return merged


def save_schema_cache(cache: dict, module: str = None):
    """保存表结构缓存。module=None 时按表的库族自动分发"""
    if module:
        family = _MODULE_TO_DB_FAMILY.get(module)
        if family:
            _save_db_family_cache(family, cache)
        else:
            _save_module_cache(module, cache)
        return
    # 按表的库族分组后分别保存
    by_family = {}
    for table_name, info in cache.items():
        family = table_to_db(table_name)
        by_family.setdefault(family, {})[table_name] = info
    for family, tables in by_family.items():
        if tables:
            _save_db_family_cache(family, tables)


def get_cached_required_cols(table_name: str) -> Optional[List[str]]:
    """从缓存获取某表的必填列（已过滤后的 NOT NULL + 无默认值）"""
    entry = get_cached_table(table_name)
    if entry:
        cols = _normalize_column_name_list(entry.get("required_columns"))
        return cols if cols else None
    return None


def update_schema_cache(table_name: str, required_columns: list, all_columns: list = None, unique_indexes: list = None, column_types: dict = None, column_comments: dict = None):
    """更新某表的缓存（含时间戳），自动路由到对应库族缓存文件"""
    from datetime import date
    family = table_to_db(table_name)
    cache = load_db_cache(family)
    required_columns = _normalize_column_name_list(required_columns)
    all_columns = _normalize_column_name_list(all_columns or required_columns)
    entry = {
        "required_columns": required_columns,
        "all_columns": all_columns or required_columns,
        "cached_at": str(date.today()),
    }
    if unique_indexes:
        entry["unique_indexes"] = unique_indexes
    elif table_name in cache and "unique_indexes" in cache[table_name]:
        entry["unique_indexes"] = cache[table_name]["unique_indexes"]
    if column_types:
        entry["column_types"] = column_types
    elif table_name in cache and "column_types" in cache[table_name]:
        entry["column_types"] = cache[table_name]["column_types"]
    if column_comments:
        entry["column_comments"] = column_comments
    elif table_name in cache and "column_comments" in cache[table_name]:
        entry["column_comments"] = cache[table_name]["column_comments"]
    cache[table_name] = entry
    _save_db_family_cache(family, cache)


def seed_cache_from_describe(table_name: str, describe_rows: list, unique_indexes: list = None):
    """用 DESCRIBE 结果 seed 缓存（一次性写入），可选传入 UNIQUE 索引"""
    filtered = filter_describe_result(describe_rows)
    update_schema_cache(table_name, filtered["required_columns"], filtered["all_columns"], unique_indexes, column_types=filtered.get("column_types"))
    return filtered


def get_cached_unique_indexes(table_name: str) -> Optional[list]:
    """从缓存获取某表的 UNIQUE 索引列表"""
    entry = get_cached_table(table_name)
    return entry.get("unique_indexes") if entry else None


def get_cached_column_comments(table_name: str) -> Optional[dict]:
    """从缓存获取某表的列注释映射 {col: comment}"""
    entry = get_cached_table(table_name)
    comments = entry.get("column_comments") if entry else None
    return comments if comments else None


def enrich_cache(
    table_name: str,
    db_name: str,
    conn=None,
    env_name: str = None,
    project_dir: str = None,
) -> dict:
    """
    补全缓存中的 unique_indexes 和 column_comments。

    table_name: 逻辑表名（如 tbdxtrusttranscfm）
    db_name: 数据库名（如 ui_trust2）
    conn: 可复用已有连接（传入 None 则自动建立）
    env_name / project_dir: DB 配置参数（conn 为 None 时使用）

    返回: {"unique_indexes": [...], "column_comments": {...}}
    """
    own_conn = conn is None
    if own_conn:
        import pymysql
        common, databases = _get_db_config_for_classify(env_name or 'ifm60-trust', project_dir)
        actual_db = _resolve_actual_db(db_name, databases)
        if actual_db not in databases:
            return {'unique_indexes': [], 'column_comments': {}, 'error': f'db {actual_db} not in config'}
        db_info = databases[actual_db]
        database = db_info.get('database', actual_db)
        conn = pymysql.connect(
            host=common['host'], port=common['port'],
            user=db_info['user'], password=db_info['password'],
            database=database, autocommit=True,
        )

    try:
        cur = conn.cursor()
        cur.execute(f'SHOW INDEX FROM `{table_name}`')
        index_rows = cur.fetchall()
        unique_indexes = parse_show_index(index_rows)

        cur.execute(
            "SELECT COLUMN_NAME, COLUMN_COMMENT FROM information_schema.COLUMNS "
            "WHERE TABLE_NAME = %s AND TABLE_SCHEMA = %s AND COLUMN_COMMENT != ''",
            (table_name, database if own_conn else conn.db.decode() if isinstance(conn.db, bytes) else str(conn.db)),
        )
        comment_rows = cur.fetchall()
        column_comments = _normalize_column_comments(comment_rows)
        cur.close()
    finally:
        if own_conn:
            conn.close()

    # 写入缓存：按库族查找表所在的缓存文件
    family = table_to_db(table_name)
    cache = load_db_cache(family)
    resolved = _resolve_cached_table_name(table_name, cache)
    if resolved in cache:
        cache[resolved]['unique_indexes'] = unique_indexes
        cache[resolved]['column_comments'] = column_comments
        _save_db_family_cache(family, cache)
    else:
        # 兜底：遍历旧模块缓存
        for module in _MODULE_CACHE_FILES:
            mod_cache = _load_module_cache(module)
            resolved = _resolve_cached_table_name(table_name, mod_cache)
            if resolved in mod_cache:
                mod_cache[resolved]['unique_indexes'] = unique_indexes
                mod_cache[resolved]['column_comments'] = column_comments
                _save_module_cache(module, mod_cache)
                break

    return {'unique_indexes': unique_indexes, 'column_comments': column_comments}


def _normalize_column_comments(rows: list) -> dict:
    """将 SELECT COLUMN_NAME,COLUMN_COMMENT 的结果转为 {col: comment}"""
    result = {}
    for row in rows:
        if isinstance(row, dict):
            name = row.get('COLUMN_NAME', '')
            comment = row.get('COLUMN_COMMENT', '')
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            name = row[0]
            comment = row[1]
        else:
            continue
        if name and comment:
            result[name] = comment
    return result


def get_cached_column_types(table_name: str) -> Optional[dict]:
    """从缓存获取某表的列类型映射 {col: mysql_type}"""
    entry = get_cached_table(table_name)
    return entry.get("column_types") if entry else None


def parse_show_index(index_rows: list) -> list:
    """将 SHOW INDEX 结果解析为 UNIQUE 索引列表（按 Key_name 分组聚合列名）"""
    if not index_rows:
        return []
    groups = {}
    for row in index_rows:
        if isinstance(row, dict):
            key_name = row.get('Key_name', '')
            col_name = row.get('Column_name', '')
            non_unique = row.get('Non_unique', 1)
        elif isinstance(row, (list, tuple)) and len(row) >= 5:
            key_name = row[2]
            col_name = row[4]
            non_unique = row[1]
        else:
            continue
        if int(non_unique) != 0:
            continue
        if key_name not in groups:
            groups[key_name] = []
        groups[key_name].append(col_name)
    return [{"name": k, "columns": v} for k, v in groups.items()]


# ============================================================
# 3.5 字段清单合并
# ============================================================

def get_expected_columns(
    where_columns: list,
    required_columns: list = None,
    business_rule_columns: list = None,
) -> list:
    """合并 WHERE + 必填 + 业务规则字段，去重（与 build_minimal_prompt 内部一致）。"""
    where_columns = _normalize_column_name_list(where_columns)
    required_columns = _normalize_column_name_list(required_columns)
    business_rule_columns = _normalize_column_name_list(business_rule_columns)
    all_needed = list(where_columns)
    if business_rule_columns:
        for col in business_rule_columns:
            if col not in all_needed:
                all_needed.append(col)
    if required_columns:
        for col in required_columns:
            if col not in all_needed:
                all_needed.append(col)
    return all_needed


def _normalize_column_name_list(columns) -> list:
    """兼容缓存中的字符串列名和 DESCRIBE 风格 dict 列对象。"""
    if not columns:
        return []
    if isinstance(columns, dict):
        columns = [columns]
    if not isinstance(columns, list):
        return [str(columns)] if str(columns).strip() else []
    result = []
    for col in columns:
        name = ""
        if isinstance(col, str):
            name = col
        elif isinstance(col, dict):
            name = col.get("field") or col.get("Field") or col.get("name") or col.get("column_name") or col.get("COLUMN_NAME") or ""
        else:
            name = str(col)
        name = str(name or "").strip()
        if name and name not in result:
            result.append(name)
    return result


def parse_mysql_error(err_msg: str) -> dict:
    """
    解析 MySQL 错误消息，提取结构化信息。

    返回:
        {"code": "1062", "type": "DUPLICATE_ENTRY", "detail": "...", "fix_hint": "..."}
        或 {"code": None, "type": "UNKNOWN", "detail": err_msg, "fix_hint": ""}
    """
    code = None
    match = re.search(r'\[(\d{4})\]', err_msg)
    if match:
        code = match.group(1)

    handlers = {
        '1062': _parse_duplicate_entry,
        '1452': _parse_fk_constraint,
        '1265': _parse_data_truncated,
        '1366': _parse_incorrect_int,
        '1054': _parse_unknown_column,
        '1146': _parse_table_not_exists,
        '1064': _parse_syntax_error,
    }

    if code and code in handlers:
        parsed = handlers[code](err_msg)
        parsed['code'] = code
        return parsed
    return {
        'code': code,
        'type': 'UNKNOWN',
        'detail': err_msg[:200],
        'fix_hint': '',
    }


def _parse_duplicate_entry(msg: str) -> dict:
    """Duplicate entry 'JS-001-20260101' for key 'PRIMARY'"""
    key_match = re.search(r"for key '([^']+)'", msg)
    val_match = re.search(r"Duplicate entry '([^']+)'", msg)
    key_name = key_match.group(1) if key_match else 'UNKNOWN'
    conflicted_value = val_match.group(1) if val_match else '（未知）'
    return {
        'type': 'DUPLICATE_ENTRY',
        'detail': f"唯一键 '{key_name}' 冲突，重复值: {conflicted_value}",
        'fix_hint': f"唯一键 '{key_name}' 的列组合在每行 INSERT 中必须不同。建议对日期/序号列逐行递增，确保所有 UNIQUE KEY 列组合互异。",
        'key_name': key_name,
        'conflicted_value': conflicted_value,
    }


def _parse_fk_constraint(msg: str) -> dict:
    """Cannot add or update a child row: a foreign key constraint fails"""
    fk_match = re.search(r"CONSTRAINT `([^`]+)`", msg)
    fk_name = fk_match.group(1) if fk_match else 'UNKNOWN'
    return {
        'type': 'FK_CONSTRAINT',
        'detail': f"外键约束 '{fk_name}' 失败，引用的父表数据不存在",
        'fix_hint': f"检查外键列的值在父表中是否存在，或确保父表先于子表预埋。",
        'fk_name': fk_name,
    }


def _parse_data_truncated(msg: str) -> dict:
    """Data truncated for column 'xxx' at row N"""
    col_match = re.search(r"for column '([^']+)'", msg)
    col_name = col_match.group(1) if col_match else 'UNKNOWN'
    return {
        'type': 'DATA_TRUNCATED',
        'detail': f"列 '{col_name}' 数据被截断（值过长或类型不匹配）",
        'fix_hint': f"列 '{col_name}' 的值超出类型允许范围，请检查值的长度/精度是否与列类型匹配。",
        'column_name': col_name,
    }


def _parse_incorrect_int(msg: str) -> dict:
    """Incorrect integer value: 'xxx' for column 'col'"""
    col_match = re.search(r"for column '([^']+)'", msg)
    val_match = re.search(r"Incorrect integer value:\s*'([^']*)'", msg)
    col_name = col_match.group(1) if col_match else 'UNKNOWN'
    actual_value = val_match.group(1) if val_match else '（未知）'
    return {
        'type': 'INCORRECT_INT',
        'detail': f"列 '{col_name}' 收到了非整数类型值 '{actual_value}'",
        'fix_hint': f"列 '{col_name}' 要求整数值，不要用空字符串 '' 或字符串代替 0。int 列空值填 0。",
        'column_name': col_name,
        'actual_value': actual_value,
    }


def _parse_unknown_column(msg: str) -> dict:
    """Unknown column 'xxx' in 'field list'"""
    col_match = re.search(r"Unknown column '([^']+)'", msg)
    col_name = col_match.group(1) if col_match else 'UNKNOWN'
    return {
        'type': 'UNKNOWN_COLUMN',
        'detail': f"表中不存在列 '{col_name}'",
        'fix_hint': f"列 '{col_name}' 不在表结构中，已从 INSERT 中移除。请检查是否拼写错误。",
        'column_name': col_name,
    }


def _parse_table_not_exists(msg: str) -> dict:
    """Table 'xxx.xxx' doesn't exist"""
    tbl_match = re.search(r"Table '([^']+)'", msg)
    tbl_name = tbl_match.group(1) if tbl_match else 'UNKNOWN'
    return {
        'type': 'TABLE_NOT_EXISTS',
        'detail': f"表 '{tbl_name}' 不存在",
        'fix_hint': f"检查表名和数据库是否正确，确认物理表名是否来自 case_plan.json 的 physical_tables。",
        'table_name': tbl_name,
    }


def _parse_syntax_error(msg: str) -> dict:
    """You have an error in your SQL syntax"""
    return {
        'type': 'SYNTAX_ERROR',
        'detail': msg[:200],
        'fix_hint': "检查 SQL 语法：VALUES 子句格式、括号匹配、逗号分隔是否正确。",
    }


# ============================================================
# 4. 极简 Prompt 生成器
# ============================================================

def build_minimal_prompt(
    table_name: str,
    where_columns: list,
    required_columns: list = None,
    presets: dict = None,
    form_values: list = None,        # NEW: 表单填值 [{label, value, value_source, ...}]
    join_tables: list = None,
    shard_info: dict = None,
    business_rule_columns: list = None,
    column_types: dict = None,
    physical_tables: list = None,
    unique_indexes: list = None,      # NEW
    column_comments: dict = None,     # NEW
    join_key_columns: list = None,     # NEW: 当前表的 JOIN 键列列表
) -> str:
    """
    生成给大模型的极简 INSERT prompt（单表）。

    参数:
        table_name:            目标表名
        where_columns:         日志中提取的 WHERE 条件字段
        required_columns:      已知的 NOT NULL + 无默认值列（来自缓存或过滤后的 DESCRIBE）
        presets:               预设测试数据 {"ta_code": "JS", "customer_id": "A3060210020001", ...}
        form_values:           表单填值列表 [{label, value, value_source, ...}]，LLM 自行做 label→SQL列语义匹配
        join_tables:           JOIN 关联表（需一起预埋）
        shard_info:            分片信息 {"ds": "ds0", "db": "ui_trust1"}
        business_rule_columns: 业务规则必填列（即使不在 WHERE 中也必须预埋，如 in_client_no）
        column_types:          列类型映射 {"col": "varchar(32)", ...}（来自缓存，用于决定值格式）
        join_key_columns:      当前表的 JOIN 键列列表（参与跨表关联，需确保在相关表中值一致）
    """
    where_columns = _normalize_column_name_list(where_columns)
    required_columns = _normalize_column_name_list(required_columns)
    business_rule_columns = _normalize_column_name_list(business_rule_columns)
    join_key_columns = _normalize_column_name_list(join_key_columns)
    # 合并 WHERE 字段 + 必填字段 + 业务规则字段，去重
    all_needed = get_expected_columns(where_columns, required_columns, business_rule_columns)

    # 构建列类型说明（让 LLM 知道每列该填什么格式的值）
    type_lines = ""
    if column_types:
        _type_map = {}
        for col in all_needed:
            ct = column_types.get(col, '')
            if ct:
                _type_map[col] = ct
        if _type_map:
            type_lines = "\n**列类型（决定值格式）:**\n"
            for col, ct in _type_map.items():
                hint = ""
                cl = ct.lower()
                if cl.startswith(('int', 'bigint', 'tinyint', 'smallint')):
                    if is_date_column(col, ct):
                        hint = " → 填 YYYYMMDD 整数（如 20260101）"
                    else:
                        hint = " → 填整数"
                elif cl.startswith(('varchar', 'char')):
                    hint = " → 填字符串，加引号"
                elif cl.startswith(('decimal', 'numeric', 'double', 'float')):
                    hint = " → 填数值，不加引号"
                elif cl.startswith(('date', 'datetime', 'timestamp')):
                    hint = " → 填日期/时间格式"
                type_lines += f"  - `{col}`: {ct}{hint}\n"

    # 构建 form_values 映射说明（替换旧的硬编码 field_mappings）
    form_value_lines = ""
    if form_values:
        items = [fv for fv in form_values if fv.get('value')]
        if items:
            form_value_lines = "\n**表单填值（全选查询实际填入的值，LLM 自行将 label 匹配到 SQL 列）:**\n"
            form_value_lines += "\n".join(
                f"| {fv['label']} | {fv['value']} | {fv.get('value_source', '')} |"
                for fv in items
            )

    # 分片信息
    shard_line = ""
    if shard_info:
        shard_line = f"\n分片路由: {shard_info.get('ds', '?')}，数据库由 db_key 关联，SQL 中禁止加库前缀。"

    # 物理表清单（来自日志实际路由，比计算分片更可靠）
    physical_line = ""
    if physical_tables:
        pts = [f"`{p}`" for p in physical_tables]
        physical_line = f"\n\n**[!!] 物理表清单（来自日志实际路由，必须全部覆盖）:**\n  {', '.join(pts)}\n  >> 每张物理表都需要生成独立的 DELETE + INSERT 语句，内容完全相同。"

    # JOIN 表提醒
    join_line = ""
    if join_tables:
        join_line = f"\n注意: 该查询 JOIN 了以下表，预埋时需同步 INSERT: {', '.join(join_tables)}"

    # 缓存状态提示
    cache_hint = ""
    if required_columns:
        cache_hint = "\n（以下必填列来自表结构缓存，已过滤：仅 NOT NULL + 无默认值）"
    else:
        cache_hint = "\n（[WARN] 未找到该表的缓存，仅使用 WHERE 字段。如 INSERT 报错，需先 DESCRIBE 建缓存。）"

    # 唯一索引约束 section（新增）
    unique_section = ""
    if unique_indexes:
        lines = []
        for idx in unique_indexes:
            cols = ', '.join(idx.get('columns', []))
            idx_name = (idx.get('name') or '').strip()
            tag = 'PRIMARY KEY' if idx_name.upper() == 'PRIMARY' else f"UNIQUE KEY {idx_name}"
            lines.append(f"  {tag} ({cols})")
        if lines:
            unique_section = "\n**联合唯一键约束（每行 INSERT 的组合必须不同）:**\n" + "\n".join(lines)

    # 字段语义提示 section（新增，仅输出有注释的列）
    comment_section = ""
    if column_comments:
        lines = []
        for col in sorted(all_needed):
            if col in column_comments:
                ct = column_types.get(col, '') if column_types else ''
                ct_hint = f" ({ct})" if ct else ""
                lines.append(f"  {col}{ct_hint} — \"{column_comments[col]}\"")
        if lines:
            comment_section = "\n**字段语义提示（来自数据库列注释）:**\n" + "\n".join(lines)

    # JOIN 键列 section (标记跨表关联列，确保 LLM 为相关表生成一致的值)
    join_key_section = ""
    if join_key_columns:
        join_key_section = "\n**跨表 JOIN 键字段（这些列参与多表关联，确保在相关表中值一致）:**\n  " + ', '.join(join_key_columns)

    # 业务规则列说明
    biz_rule_section = ""
    if business_rule_columns:
        biz_rule_section = f"""
业务规则必填列（即使不出现在 WHERE 中也必须预埋）:
  {', '.join(business_rule_columns)}
  [WARN] in_client_no 必须与 tbclientseller2 表中的记录一致，否则分行权限用例会失败。
"""

    prompt = f"""## 数据预埋任务

目标表: `{table_name}`{shard_line}{physical_line}{join_line}

### 必须预埋的字段（{len(all_needed)} 列）{cache_hint}

WHERE 查询条件字段（后端实际查询）:
  {', '.join(where_columns)}
{biz_rule_section}
{"NOT NULL + 无默认值的必填列:" if required_columns else "（无额外必填列信息）"}
  {', '.join(required_columns) if required_columns else '（待确定）'}
{type_lines}
### 合并后的完整字段清单:
  {', '.join(all_needed)}

{join_key_section}{unique_section}{comment_section}
{form_value_lines if form_value_lines else "  （无 form_values）"}

### 要求:
1. 仅对上述 {len(all_needed)} 个字段赋值；所有 UNIQUE 键的列组合在每行 INSERT 中必须不同，尤其注意 PRIMARY KEY
2. 其余列由数据库默认值处理，严禁添加未列出的字段
3. 使用 DELETE + INSERT 预埋模式（DELETE 在前，INSERT 在后）
4. 日期字段格式: YYYYMMDD 整数（如 20260101），必须在查询日期范围内
5. 空字符串字段用 ''，空格字段用 ' '，数值 0 直接填 0
6. income_date 等关键字段不能为 0（会报空指针）
7. **in_client_no 必须与 tbclientseller2 记录一致**（用于分行权限关联）
"""

    return prompt


def build_retry_prompt(
    table_name: str,
    failed_sql: str,
    parsed_error: dict,
    attempt: int,
) -> str:
    """
    构建 SQL 验证失败后的重试 prompt。

    table_name: 目标表名
    failed_sql: 失败的那轮 SQL 原文（用于上下文）
    parsed_error: parse_mysql_error() 返回的结构化错误
    attempt: 当前重试轮次（1 或 2）

    返回: 带错误上下文的修正 prompt
    """
    error_type = parsed_error.get('type', 'UNKNOWN')
    detail = parsed_error.get('detail', '')
    fix_hint = parsed_error.get('fix_hint', '')

    prompt = f"""## 第 {attempt} 轮 INSERT 生成失败 — 请修正后重新生成

### 失败信息

目标表: `{table_name}`
MySQL 报错类型: {error_type}
错误详情: {detail}

**修正方向:**
  {fix_hint}

### 失败语句（需检查修正）:
```sql
{failed_sql[:2000]}
```

### 要求:
1. 仅保留合法列，修正上述错误后重新生成 DELETE + INSERT
2. 确保所有 UNIQUE KEY 列组合在每行中不同，PRIMARY KEY 不可重复
3. 其余约束同上一轮的字段清单要求

仅输出修正后的 DELETE + INSERT，不要输出任何其他内容。
"""
    return prompt


# ============================================================
# 4.5 双写 Prompt 生成器（无必填字段双路探勘专用）
# ============================================================

def build_dual_write_prompt(
    dual_result: dict,
    presets: dict = None,
    form_values: list = None,        # NEW
    physical_tables: dict = None,
) -> str:
    """
    针对双路探勘结果，生成"多表双写"的 INSERT prompt。

    核心规则：
      当探勘日志中同时抓到了 share1（分片物理表）和 shareqry（全局汇总表）时，
      必须针对同一笔测试数据，同时生成这两张（或多张）表的插入语句，
      确保底座数据支撑全量和单例的独立路由。

    参数:
        dual_result: extract_dual_route_requirements() 的返回值
        presets:     预设测试数据
        form_values: 表单填值列表

    返回: 完整的多表双写 prompt（给大模型）
    """
    merged_tables = dual_result.get("merged_tables", [])
    merged_where = dual_result.get("merged_where", {})
    merged_join_key_columns = dual_result.get("merged_join_key_columns", {})
    shard_info = dual_result.get("merged_shard_info", {})
    route_summary = dual_result.get("route_summary", {})

    if not merged_tables:
        return "## 错误：双路探勘未提取到任何表名，请检查日志。"

    # 业务规则必填列
    # 构建每张表的详细信息
    table_sections = []
    all_table_prompts = []
    for table in merged_tables:
        where_cols = merged_where.get(table, [])
        required_cols = get_cached_required_cols(table)

        # 检查业务规则列
        biz_cols = [c for c in BUSINESS_RULE_COLUMNS
                    if c not in where_cols
                    and (not required_cols or c not in required_cols)]

        # 计算这张表来自哪条路
        if table in route_summary.get("empty_query_tables", []):
            route_tag = "【空查独有 → 全局汇总表】"
        elif table in route_summary.get("param_query_tables", []):
            route_tag = "【带参独有 → 分片物理表】"
        else:
            route_tag = "【两路共有】"

        # 合并该表需要的所有字段
        all_needed = get_expected_columns(where_cols, required_cols, biz_cols if biz_cols else None)

        section = f"""
#### 表: `{table}` {route_tag}
  WHERE 字段: {', '.join(where_cols) if where_cols else '（无）'}
  必填列: {', '.join(required_cols) if required_cols else '（无缓存，仅用 WHERE 字段）'}
  业务规则列: {', '.join(biz_cols) if biz_cols else '（无）'}
  合并字段: {', '.join(all_needed) if all_needed else '（无）'}
"""
        table_sections.append(section)

        # 为每张表生成独立的 minimal prompt
        table_prompt = build_minimal_prompt(
            table_name=table,
            where_columns=where_cols,
            required_columns=required_cols,
            presets=presets or {},
            form_values=form_values,
            shard_info=shard_info,
            business_rule_columns=biz_cols if biz_cols else None,
            column_types=get_cached_column_types(table),
            physical_tables=physical_tables.get(table) if physical_tables else None,
            unique_indexes=get_cached_unique_indexes(table),
            column_comments=get_cached_column_comments(table),
            join_key_columns=merged_join_key_columns.get(table),
        )
        all_table_prompts.append(table_prompt)

    # 分片信息
    shard_line = ""
    if shard_info:
        shard_line = f"\n分片路由: {shard_info.get('ds', '?')}，数据库由 db_key 关联，SQL 中禁止加库前缀。"

    # 物理表汇总（来自日志）
    physical_summary = ""
    if physical_tables:
        pt_lines = []
        for logical_name, phys_list in physical_tables.items():
            pts = ", ".join(f"`{p}`" for p in phys_list)
            pt_lines.append(f"  - `{logical_name}` → {pts}")
        physical_summary = "\n\n### [!!] 物理表映射（来自日志实际路由，按此生成 INSERT）\n" + "\n".join(pt_lines)
        physical_summary += "\n  >> 每张物理表独立生成 DELETE + INSERT，同一逻辑表的数据内容完全相同。"

    # 路由摘要
    summary_lines = []
    if route_summary.get("empty_query_tables"):
        summary_lines.append(f"  - 空查独有（全局汇总表，全量查询覆盖）: {', '.join(route_summary['empty_query_tables'])}")
    if route_summary.get("param_query_tables"):
        summary_lines.append(f"  - 带参独有（分片物理表，单例查询覆盖）: {', '.join(route_summary['param_query_tables'])}")
    if route_summary.get("shared_tables"):
        summary_lines.append(f"  - 两路共有: {', '.join(route_summary['shared_tables'])}")
    summary_text = "\n".join(summary_lines)

    # Form Values 映射（替换旧的硬编码 field_mappings）
    form_value_lines = ""
    if form_values:
        items = [fv for fv in form_values if fv.get('value')]
        if items:
            form_value_lines = "\n**表单填值（全选查询实际填入的值，LLM 自行将 label 匹配到 SQL 列）:**\n"
            form_value_lines += "\n".join(
                f"| {fv['label']} | {fv['value']} | {fv.get('value_source', '')} |"
                for fv in items
            )

    prompt = f"""## 双写预埋任务（无必填字段双路探勘）

### 背景
该菜单无必填校验，探勘时执行了双路查询：
1. 空查（不填业务条件）→ 触发全局汇总表路由
2. 带参查询 → 触发分片物理表路由

为确保全量查询和单例查询都能查到数据，**必须对同一笔测试数据同时 INSERT 所有涉及的表**。

### 路由摘要（{len(merged_tables)} 张表）{shard_line}
{summary_text}{physical_summary}

### 各表详细信息
{''.join(table_sections)}

{form_value_lines if form_value_lines else "  （无 form_values）"}

### [!!] 核心要求:
1. **同一笔测试数据**：所有表使用相同的业务标识（如 in_client_no、ta_code、prd_code）
2. **全部表都要 INSERT**：{len(merged_tables)} 张表，一张都不能少
3. **DELETE + INSERT 模式**：每张表先 DELETE 再 INSERT，避免主键冲突
4. **分片表处理**：如有分片路由（{shard_info.get('ds', '无')}），数据库由 db_key 关联，SQL 中禁止加库前缀
5. 日期字段格式: YYYYMMDD 整数（如 20260101），必须在查询日期范围内
6. income_date 等关键字段不能为 0（会报空指针）
7. **in_client_no 必须与 tbclientseller2 记录一致**（用于分行权限关联）

### 各表独立 Prompt 参考:
{''.join(chr(10) + p for p in all_table_prompts)}
"""

    return prompt


# ============================================================
# 4.9 SQL 文件路径生成器
# ============================================================

# 数据库后缀映射
_DB_SUFFIX = {
    'ui_bc': '',
    'ui_trust1': '_trust',
    'ui_trust2': '_trust2',
    'ui_fund1': '_fund',
    'ui_fund2': '_fund2',
    'ui_fina1': '_fina',
    'ui_fina2': '_fina2',
    'ui_dxcp1': '_dxcp',
    'ui_dxcp2': '_dxcp2',
    'ui_hstc1': '_hstc',
    'ui_hstc2': '_hstc2',
    'ui_insure1': '_insure',
    'ui_insure2': '_insure2',
    'ui_per1': '_per',
    'ui_per2': '_per2',
}


def _build_sql_file_paths(tables_by_db: dict, menu_context: dict = None) -> dict:
    """
    根据数据库分组和菜单上下文，生成每组表对应的 SQL 文件路径模板。

    返回: {
        'sql_file_paths': {
            'ui_bc':     '{sql_data_dir_{module}}/{二级菜单}/{菜单名}/{功能代码}.sql',
            'ui_trust1': '{sql_data_dir_{module}}/{二级菜单}/{菜单名}/{功能代码}_trust.sql',
        },
        'sql_file_paths_base': '{sql_data_dir_{module}}/{二级菜单}/{菜单名}/{功能代码}_base.sql',
    }
    """
    if not menu_context:
        return {}

    def _k(*keys):
        """取第一个非空值，优先英文键（兼容 case_plan.json），fallback 中文键"""
        for k in keys:
            v = menu_context.get(k, '')
            if v:
                return v
        return ''

    module = _k('module')
    sub_menu = _k('level2', '二级菜单')
    menu_name = _k('name', '菜单名')
    func_code = _k('code', '功能代码')

    if not all([sub_menu, menu_name, func_code]):
        return {}

    paths = {}
    for db_name in tables_by_db:
        suffix = _DB_SUFFIX.get(db_name, f'_{db_name}')
        paths[db_name] = f'{{sql_data_dir_{module}}}/{sub_menu}/{menu_name}/{func_code}{suffix}.sql'

    base_path = f'{{sql_data_dir_{module}}}/{sub_menu}/{menu_name}/{func_code}_base.sql'

    return {
        'sql_file_paths': paths,
        'sql_file_paths_base': base_path,
    }


# ============================================================
# 5. 一站式分析：日志文本 → 完整预埋上下文
# ============================================================

def analyze_log_for_preset(log_text: str, presets: dict = None, form_values: list = None, menu_context: dict = None) -> dict:
    """
    一站式分析：从单路日志文本提取需求 → 查缓存 → 生成 prompt。

    参数:
        log_text:      SSH 日志文本
        presets:       预设测试数据
        menu_context:  菜单上下文（可选），用于生成 SQL 文件路径。支持中英文键：
            {
                "module": "dxtrust",            # 模块: dxtrust / dxfund / fina
                "level2": "查询管理",           # 或 "二级菜单"（中文 fallback）
                "name": "信托代销...",          # 或 "菜单名"
                "code": "ifmCDxTrustCltShareChg", # 或 "功能代码"
            }

    返回:
    {
        "requirements": {...},           # extract_sql_requirements 的结果
        "cached_tables": ["t1", ...],    # 已有缓存的表
        "missing_tables": ["t2", ...],   # 缺缓存的表（需 DESCRIBE）
        "prompts": {"t1": "...", ...},   # 每张表的极简 prompt
        "describe_needed": ["t2", ...],  # 需要执行 DESCRIBE 的表
        "tables_by_db": {...},           # 表→数据库分组
        "sql_file_paths": {...},         # 数据库→SQL 文件路径（需 menu_context）
        "physical_tables": {...},        # 日志中的物理表名，按逻辑表分组
    }
    """
    req = extract_sql_requirements(log_text)

    # 提前提取物理表名（后续 prompt 循环要用）
    physical_tables = extract_physical_tables(log_text)

    cached_tables = []
    missing_tables = []
    prompts = {}
    describe_needed = []

    all_tables = req["tables"] + req["join_tables"]
    for table in all_tables:
        required_cols = get_cached_required_cols(table)
        where_cols = req["where_columns"].get(table, [])
        shard = req["shard_info"]

        # 检查业务规则列是否已在 WHERE 或缓存中（避免重复添加）
        biz_cols = [c for c in BUSINESS_RULE_COLUMNS
                    if c not in where_cols
                    and (not required_cols or c not in required_cols)]

        if required_cols:
            cached_tables.append(table)
        else:
            missing_tables.append(table)
            # JOIN 表即使无 WHERE 也可能需要预埋（如 tbclientmap 通过主表关联）
            if where_cols or table in req["join_tables"]:
                describe_needed.append(table)

        # 无论是否有缓存，都生成 prompt（缓存缺失时会提示）
        prompts[table] = build_minimal_prompt(
            table_name=table,
            where_columns=where_cols,
            required_columns=required_cols,
            presets=presets or {},
            form_values=form_values,
            join_tables=[jt for jt in req["join_tables"] if jt != table],
            shard_info=shard,
            business_rule_columns=biz_cols if biz_cols else None,
            physical_tables=physical_tables.get(table) if physical_tables else None,
            unique_indexes=get_cached_unique_indexes(table),
            column_comments=get_cached_column_comments(table),
            join_key_columns=req["join_key_columns"].get(table),
        )

    # 按数据库分组（跨库 SQL 拆分用）
    all_involved = req["tables"] + req["join_tables"]
    tables_by_db = split_tables_by_db(all_involved)

    # 生成 SQL 文件路径模板（需 menu_context）
    path_info = _build_sql_file_paths(tables_by_db, menu_context)

    return {
        "requirements": req,
        "cached_tables": cached_tables,
        "missing_tables": missing_tables,
        "prompts": prompts,
        "describe_needed": describe_needed,
        "tables_by_db": tables_by_db,
        "sql_file_paths": path_info.get('sql_file_paths', {}),
        "sql_file_paths_base": path_info.get('sql_file_paths_base', ''),
        "physical_tables": physical_tables,
        "ast_diagnostics": build_ast_diagnostics(log_text),
    }


def analyze_dual_route_for_preset(
    empty_query_log: str,
    param_query_log: str,
    presets: dict = None,
    form_values: list = None,
    menu_context: dict = None,
) -> dict:
    """
    双路一站式分析：从两路日志提取需求 → 合并表名 → 查缓存 → 生成双写 prompt。

    返回:
    {
        "dual_result":        {...},           # extract_dual_route_requirements 的结果
        "dual_write_prompt":  "...",           # 合并后的双写 prompt（给大模型生成 INSERT）
        "cached_tables":      ["t1", ...],     # 已有缓存的表
        "missing_tables":     ["t2", ...],     # 缺缓存的表
        "describe_needed":    ["t2", ...],     # 需要执行 DESCRIBE 的表
    }
    """
    dual = extract_dual_route_requirements(empty_query_log, param_query_log)

    cached_tables = []
    missing_tables = []
    describe_needed = []

    # 收集所有 JOIN 表（双路合并后的）
    all_join_tables = set()
    for req in [dual.get("empty_query", {}), dual.get("param_query", {})]:
        for jt in req.get("join_tables", []):
            all_join_tables.add(jt)

    for table in dual["merged_tables"]:
        required_cols = get_cached_required_cols(table)
        where_cols = dual["merged_where"].get(table, [])

        if required_cols:
            cached_tables.append(table)
        else:
            missing_tables.append(table)
            # JOIN 表即使无 WHERE 也可能需要预埋
            if where_cols or table in all_join_tables:
                describe_needed.append(table)

    # 合并两路日志的物理表名
    empty_pt = extract_physical_tables(empty_query_log)
    param_pt = extract_physical_tables(param_query_log)
    physical_tables = merge_physical_tables(empty_pt, param_pt)

    # 生成双写 prompt
    dw_prompt = build_dual_write_prompt(dual, presets=presets, form_values=form_values, physical_tables=physical_tables)

    # 按数据库分组
    tables_by_db = split_tables_by_db(dual["merged_tables"])

    # SQL 文件路径
    path_info = _build_sql_file_paths(tables_by_db, menu_context)

    return {
        "dual_result": dual,
        "dual_write_prompt": dw_prompt,
        "cached_tables": cached_tables,
        "missing_tables": missing_tables,
        "describe_needed": describe_needed,
        "tables_by_db": tables_by_db,
        "sql_file_paths": path_info.get('sql_file_paths', {}),
        "sql_file_paths_base": path_info.get('sql_file_paths_base', ''),
        "physical_tables": physical_tables,
    }


# ============================================================
# 6. 命令行入口
# ============================================================

def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('--help', '-h', 'help'):
        print("用法:")
        print("  python sql_extractor.py analyze <日志文本文件>              — 单路分析")
        print("  python sql_extractor.py analyze_stdin                      — 从 stdin 读取日志")
        print("  python sql_extractor.py analyze_dual <空查日志> <带参日志>   — 双路分析（无必填字段）")
        print("  python sql_extractor.py cache list                         — 列出缓存中所有表")
        print("  python sql_extractor.py cache get <表名>                   — 查看某表缓存")
        print("  python sql_extractor.py cache add <表名> <JSON>            — 手动添加缓存")
        print("  python sql_extractor.py cache clear                        — 清空缓存")
        print("  python sql_extractor.py cache stale                        — 列出超过30天的缓存")
        print("  python sql_extractor.py filter_describe <文件路径>           — 过滤 DESCRIBE 结果（从文件）")
        print("  python sql_extractor.py filter_describe --json '<JSON>'     — 过滤 DESCRIBE 结果（从字符串）")
        print("  python sql_extractor.py cache seed <表名> <DESCRIBE文件> [SHOW_INDEX文件] — 从 DESCRIBE seed 缓存（可选UNIQUE索引）")
        print("  python sql_extractor.py physical_tables <日志文件>    — 提取日志中的物理表名")
        print("  python sql_extractor.py classify <physical_tables_JSON|case_plan.json> [--routing-db DB] [--env ENV] [--project-dir DIR]")
        print("                                                         — 通过 MySQL 查询 TABLE/VIEW，输出表分类")
        print("  python sql_extractor.py resolve-shard <表名> [--routing-db DB]")
        print("                                                         — 查询表的路由库（纯本地，不需要DB连接）")
        print("  python sql_extractor.py describe <表名> --db <数据库> [--env ENV] [--project-dir DIR]")
        print("                                                         — 执行 MySQL DESCRIBE，输出列信息 JSON")
        print("  python sql_extractor.py has-shard-tables --case-plan <case_plan.json>")
        print("                                                         — 判断 physical_tables 中是否有分片业务表")
        print("  python sql_extractor.py shard-db --case-plan <case_plan.json>")
        print("                                                         — 从 physical_tables 计算 shard_db（排除系统表）")
        print("  python sql_extractor.py build-prompts --case-plan <case_plan.json> --where-cols '<JSON>'")
        print("                                                         — 对每张 preset_table 生成 LLM prompt")
        print("  python sql_extractor.py expected-columns <表名> --case-plan <case_plan.json> --where-cols '<JSON>'")
        print("                                                         — 获取某张表的预期 INSERT 列清单")
        sys.exit(0 if sys.argv[1] in ('--help', '-h', 'help') else 1)

    cmd = sys.argv[1]

    if cmd == 'analyze':
        if len(sys.argv) < 3:
            print("请指定日志文件路径"); sys.exit(1)
        with open(sys.argv[2], 'r', encoding='utf-8') as f:
            log_text = f.read()
        result = analyze_log_for_preset(log_text)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == 'analyze_stdin':
        log_text = sys.stdin.read()
        result = analyze_log_for_preset(log_text)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == 'analyze_dual':
        if len(sys.argv) < 4:
            print("用法: python sql_extractor.py analyze_dual <空查日志文件> <带参日志文件>")
            sys.exit(1)
        with open(sys.argv[2], 'r', encoding='utf-8') as f:
            empty_log = f.read()
        with open(sys.argv[3], 'r', encoding='utf-8') as f:
            param_log = f.read()
        result = analyze_dual_route_for_preset(empty_log, param_log)
        # 输出时排除过长的 dual_write_prompt，用单独字段标记
        output = {
            "dual_result": result["dual_result"],
            "cached_tables": result["cached_tables"],
            "missing_tables": result["missing_tables"],
            "describe_needed": result["describe_needed"],
            "physical_tables": result["physical_tables"],
            "dual_write_prompt_preview": result["dual_write_prompt"][:500] + "...",
            "dual_write_prompt_length": len(result["dual_write_prompt"]),
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        # 完整 prompt 写入临时文件
        prompt_file = os.path.join(SKILL_DIR, 'config', '_dual_write_prompt.txt')
        with open(prompt_file, 'w', encoding='utf-8') as f:
            f.write(result["dual_write_prompt"])
        print(f"\n完整双写 prompt 已写入: {prompt_file}")

    elif cmd == 'cache':
        if len(sys.argv) < 3:
            print("子命令: list [库族|模块] / get <表名> / add <表名> <JSON> / clear [库族|模块] / stale"); sys.exit(1)
        sub = sys.argv[2]
        if sub == 'list':
            # 可选参数：库族名（如 ui_trust）或模块名（如 信托）
            if len(sys.argv) >= 4:
                name = sys.argv[3]
                # 优先按库族加载
                cache = load_db_cache(name)
                if not cache:
                    # 回退到按模块加载
                    cache = _load_module_cache(name)
                if not cache:
                    print(f"{name} 缓存为空")
                else:
                    print(f"{name} ({len(cache)} 表):")
                    for table, info in cache.items():
                        cols = info.get("required_columns", [])
                        if not isinstance(cols, list):
                            cols = []
                        print(f"  {table}: {len(cols)} 必填列 — {', '.join(cols[:8])}{'...' if len(cols) > 8 else ''}")
            else:
                # 显示所有库族概览
                for family in _DB_FAMILY_CACHE_FILES:
                    cache = load_db_cache(family)
                    print(f"  {family}: {len(cache)} 表")
        elif sub == 'get':
            if len(sys.argv) < 4:
                print("请指定表名"); sys.exit(1)
            table = sys.argv[3]
            entry = get_cached_table(table)
            if entry:
                family = table_to_db(table)
                print(f"[{family}] {table}:")
                print(json.dumps(entry, ensure_ascii=False, indent=2))
            else:
                print(f"缓存中未找到表: {table}")
        elif sub == 'add':
            if len(sys.argv) < 5:
                print("用法: cache add <表名> <required_columns_JSON>"); sys.exit(1)
            table = sys.argv[3]
            cols = json.loads(sys.argv[4])
            update_schema_cache(table, cols)
            family = table_to_db(table)
            print(f"[OK] 已添加缓存到 {family}.json: {table} ({len(cols)} 必填列)")
        elif sub == 'seed':
            if len(sys.argv) < 5:
                print("用法: cache seed <表名> <DESCRIBE结果JSON文件> [SHOW_INDEX结果JSON文件]"); sys.exit(1)
            table = sys.argv[3]
            describe_file = sys.argv[4]
            with open(describe_file, 'r', encoding='utf-8') as f:
                describe_rows = json.load(f)
            unique_indexes = None
            if len(sys.argv) >= 6:
                index_file = sys.argv[5]
                with open(index_file, 'r', encoding='utf-8') as f:
                    index_rows = json.load(f)
                unique_indexes = parse_show_index(index_rows)
            seed_cache_from_describe(table, describe_rows, unique_indexes)
            family = table_to_db(table)
            idx_info = f", {len(unique_indexes)} UNIQUE索引" if unique_indexes else ""
            print(f"[OK] 已从 DESCRIBE seed 缓存到 {family}.json: {table}{idx_info}")
        elif sub == 'clear':
            if len(sys.argv) >= 4:
                name = sys.argv[3]
                # 优先按库族清空
                family = _strip_db_suffix(name)
                if family in _DB_FAMILY_CACHE_FILES:
                    _save_db_family_cache(family, {})
                    print(f"[OK] {family} 缓存已清空")
                else:
                    # 回退到按模块清空
                    _save_module_cache(name, {})
                    print(f"[OK] {name} 缓存已清空")
            else:
                for family in _DB_FAMILY_CACHE_FILES:
                    _save_db_family_cache(family, {})
                print("[OK] 所有库族缓存已清空")
        elif sub == 'stale':
            from datetime import date, timedelta
            threshold = date.today() - timedelta(days=30)
            stale = []
            for family in _DB_FAMILY_CACHE_FILES:
                cache = load_db_cache(family)
                for table, info in cache.items():
                    cached_at = info.get("cached_at", "")
                    if cached_at:
                        try:
                            d = date.fromisoformat(cached_at)
                            if d < threshold:
                                stale.append(f"  [{family}] {table}: cached {cached_at} (>30天)")
                        except ValueError:
                            stale.append(f"  [{family}] {table}: cached {cached_at} (日期格式异常)")
                    else:
                        stale.append(f"  [{family}] {table}: 无 cached_at 字段")
            if stale:
                print(f"以下 {len(stale)} 个表的缓存超过30天，建议刷新:")
                print("\n".join(stale))
            else:
                print("[OK] 所有缓存均在30天内")
        elif sub == 'enrich':
            target_table = None
            enrich_all = False
            module_filter = None
            i = 3
            while i < len(sys.argv):
                if sys.argv[i] == '--all':
                    enrich_all = True; i += 1
                elif sys.argv[i] == '--module' and i + 1 < len(sys.argv):
                    module_filter = sys.argv[i + 1]; i += 2
                else:
                    target_table = sys.argv[i]; i += 1

            if not enrich_all and not target_table:
                print("用法: python sql_extractor.py cache enrich <表名> [--module 信托]")
                print("      python sql_extractor.py cache enrich --all")
                sys.exit(1)

            if enrich_all:
                all_tables = list(load_schema_cache().keys())
                if module_filter:
                    # 支持按库族或模块名过滤
                    family = _strip_db_suffix(module_filter)
                    if family in _DB_FAMILY_CACHE_FILES:
                        all_tables = [t for t in all_tables if table_to_db(t) == family]
                    else:
                        all_tables = [t for t in all_tables if _classify_table_module(t) == module_filter]
            else:
                all_tables = [target_table]

            success = 0
            skipped = 0
            for tbl in all_tables:
                db_family = table_to_db(tbl)
                try:
                    result = enrich_cache(tbl, db_family)
                    if 'error' in result:
                        print(f"  ⚠ {tbl}: {result['error']}")
                        skipped += 1
                    else:
                        ui_count = len(result.get('unique_indexes', []))
                        cm_count = len(result.get('column_comments', {}))
                        print(f"  ✓ {tbl}: {ui_count} UNIQUE索引, {cm_count} 条列注释")
                        success += 1
                except Exception as e:
                    print(f"  ✗ {tbl}: {e}")
                    skipped += 1
            print(f"\n完成: {success} 成功, {skipped} 跳过/失败")

        else:
            print(f"未知子命令: {sub}")
            sys.exit(1)

    elif cmd == 'physical_tables':
        if len(sys.argv) < 3:
            print("请指定日志文件路径"); sys.exit(1)
        with open(sys.argv[2], 'r', encoding='utf-8') as f:
            log_text = f.read()
        result = extract_physical_tables(log_text)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == 'filter_describe':
        if len(sys.argv) < 3:
            print("请指定 DESCRIBE 结果 JSON 文件或使用 --json '<JSON>'"); sys.exit(1)
        if sys.argv[2] == '--json':
            if len(sys.argv) < 4:
                print("请在 --json 后提供 JSON 字符串"); sys.exit(1)
            rows = json.loads(sys.argv[3])
        else:
            with open(sys.argv[2], 'r', encoding='utf-8') as f:
                rows = json.load(f)
        result = filter_describe_result(rows)
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == 'classify':
        if len(sys.argv) < 3:
            print("用法: python sql_extractor.py classify <physical_tables_JSON|case_plan.json> [--routing-db DB] [--env ENV] [--project-dir DIR]")
            sys.exit(1)

        # Parse physical_tables from file or inline JSON
        input_arg = sys.argv[2]
        if os.path.exists(input_arg):
            with open(input_arg, 'r', encoding='utf-8') as f:
                data = json.load(f)
            # Support both case_plan.json (extract shard.physical_tables) and raw tables
            if isinstance(data, dict) and 'shard' in data:
                physical_tables = data['shard'].get('physical_tables', {})
                routing_db = data['shard'].get('routing_db', data['shard'].get('db', ''))
            elif isinstance(data, dict) and 'physical_tables' in data:
                physical_tables = data['physical_tables']
                routing_db = data.get('routing_db', data.get('db', ''))
            else:
                physical_tables = data
                routing_db = ''
        else:
            try:
                physical_tables = json.loads(input_arg)
            except json.JSONDecodeError as e:
                print(f"JSON 解析失败: {e}")
                print("提示: 请检查 JSON 格式，或传入文件路径（如 case_plan.json）")
                sys.exit(1)
            routing_db = ''

        # Parse optional --routing-db, --env, --project-dir, --module flags
        i = 3
        env_name = None  # 🔧 修复：不再默认 ifm60-trust，由 module 推导
        project_dir = None
        module = None
        while i < len(sys.argv):
            if sys.argv[i] == '--routing-db' and i + 1 < len(sys.argv):
                routing_db = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == '--env' and i + 1 < len(sys.argv):
                env_name = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == '--module' and i + 1 < len(sys.argv):
                module = sys.argv[i + 1]
                i += 2
            elif sys.argv[i] == '--project-dir' and i + 1 < len(sys.argv):
                project_dir = sys.argv[i + 1]
                i += 2
            else:
                i += 1

        if not physical_tables:
            print(json.dumps({'error': 'physical_tables 为空'}, ensure_ascii=False))
            sys.exit(1)

        result = classify_tables(physical_tables, routing_db, env_name, project_dir, module=module)
        print(json.dumps(result, ensure_ascii=False, indent=2))

        # Summary line
        summary = {}
        for t, info in result.items():
            cat = info.get('classification', 'unknown')
            summary[cat] = summary.get(cat, 0) + 1
        print(f"\n分类汇总: {summary}", file=sys.stderr)

    elif cmd == 'resolve-shard':
        if len(sys.argv) < 3:
            print("用法: python sql_extractor.py resolve-shard <表名> [--routing-db DB]")
            sys.exit(1)
        table = sys.argv[2]
        routing_db = None
        if len(sys.argv) >= 5 and sys.argv[3] == '--routing-db':
            routing_db = sys.argv[4]
        families = table_to_dbs(table)
        resolved = resolve_shard_db(table, routing_db)
        all_resolved = resolve_shard_dbs(table, routing_db)
        print(json.dumps({
            'table': table,
            'families': families,
            'resolved_db': resolved,
            'all_resolved_dbs': all_resolved,
            'routing_db': routing_db,
        }, ensure_ascii=False, indent=2))

    elif cmd == 'describe':
        if len(sys.argv) < 4:
            print("用法: python sql_extractor.py describe <表名> --db <数据库> [--env ENV] [--project-dir DIR]")
            sys.exit(1)
        table_name = sys.argv[2]
        db_name = None
        env_name = None
        project_dir = None
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == '--db' and i + 1 < len(sys.argv):
                db_name = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == '--env' and i + 1 < len(sys.argv):
                env_name = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == '--project-dir' and i + 1 < len(sys.argv):
                project_dir = sys.argv[i + 1]; i += 2
            else:
                i += 1
        if not db_name:
            print("错误: 需要 --db 参数指定数据库名")
            sys.exit(1)

        try:
            common, databases = _get_db_config_for_classify(env_name or 'ifm60-trust', project_dir)
            actual_db = _resolve_actual_db(db_name, databases)
            columns = describe_table_columns(table_name, db_name, env_name=env_name, project_dir=project_dir)
        except Exception as e:
            print(json.dumps({'error': str(e), 'table': table_name, 'db': db_name}, ensure_ascii=False))
            sys.exit(1)

        print(json.dumps({'table': table_name, 'db': actual_db, 'columns': columns, 'count': len(columns)},
                         ensure_ascii=False, indent=2, default=str))

    elif cmd == 'shard-db':
        case_plan_path = None
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == '--case-plan' and i + 1 < len(sys.argv):
                case_plan_path = sys.argv[i + 1]; i += 2
            else:
                i += 1
        if not case_plan_path or not os.path.exists(case_plan_path):
            print(json.dumps({'error': '需要 --case-plan 参数指定 case_plan.json 路径'}, ensure_ascii=False))
            sys.exit(1)
        with open(case_plan_path, 'r', encoding='utf-8') as f:
            cp = json.load(f)
        physical_tables = cp.get('shard', {}).get('physical_tables', {})
        routing_db = cp.get('shard', {}).get('routing_db', '') or cp.get('shard', {}).get('db', '')
        db_map = get_business_tables_db_map(physical_tables, routing_db)
        business_dbs = set(db for t, db in db_map.items() if not _is_system_table(t))
        shard_db = business_dbs.pop() if len(business_dbs) == 1 else (sorted(business_dbs)[0] if business_dbs else 'ui_bc')
        print(json.dumps({
            'shard_db': shard_db,
            'db_map': db_map,
            'business_dbs': sorted(business_dbs) if business_dbs else [shard_db],
            'routing_db': routing_db,
        }, ensure_ascii=False, indent=2))

    elif cmd == 'build-prompts':
        case_plan_path = None
        where_cols_raw = None
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == '--case-plan' and i + 1 < len(sys.argv):
                case_plan_path = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == '--where-cols' and i + 1 < len(sys.argv):
                where_cols_raw = sys.argv[i + 1]; i += 2
            else:
                i += 1
        if not case_plan_path or not os.path.exists(case_plan_path):
            print(json.dumps({'error': '需要 --case-plan 参数指定 case_plan.json 路径'}, ensure_ascii=False))
            sys.exit(1)
        with open(case_plan_path, 'r', encoding='utf-8') as f:
            cp = json.load(f)
        where_cols = json.loads(where_cols_raw) if where_cols_raw else {}
        presets = cp.get('presets', cp.get('preset_strategy', {}))
        physical_tables = cp.get('shard', {}).get('physical_tables', {})
        shard_db = cp.get('shard', {}).get('db', '')
        preset_tables = cp.get('preset_strategy', {}).get('preset_tables', [])
        if not preset_tables:
            biz = cp.get('preset_strategy', {}).get('biz_tables', [])
            base = cp.get('preset_strategy', {}).get('base_tables', [])
            preset_tables = biz + base
        prompts = []
        for table in preset_tables:
            prompt = build_minimal_prompt(
                table_name=table,
                where_columns=where_cols.get(table, []),
                required_columns=get_cached_required_cols(table),
                presets=presets,
                shard_info={'db': shard_db},
                column_types=get_cached_column_types(table),
                physical_tables=physical_tables.get(table),
            )
            prompts.append({'table': table, 'prompt': prompt})
        print(json.dumps(prompts, ensure_ascii=False, indent=2))

    elif cmd == 'expected-columns':
        if len(sys.argv) < 3:
            print("用法: python sql_extractor.py expected-columns <表名> --case-plan <case_plan.json> --where-cols '<JSON>'")
            sys.exit(1)
        table_name = sys.argv[2]
        case_plan_path = None
        where_cols_raw = None
        i = 3
        while i < len(sys.argv):
            if sys.argv[i] == '--case-plan' and i + 1 < len(sys.argv):
                case_plan_path = sys.argv[i + 1]; i += 2
            elif sys.argv[i] == '--where-cols' and i + 1 < len(sys.argv):
                where_cols_raw = sys.argv[i + 1]; i += 2
            else:
                i += 1
        if not case_plan_path or not os.path.exists(case_plan_path):
            print(json.dumps({'error': '需要 --case-plan 参数指定 case_plan.json 路径'}, ensure_ascii=False))
            sys.exit(1)
        with open(case_plan_path, 'r', encoding='utf-8') as f:
            cp = json.load(f)
        where_cols = json.loads(where_cols_raw) if where_cols_raw else {}
        biz_tables = cp.get('preset_strategy', {}).get('biz_tables', [])
        biz_cols = ['in_client_no'] if table_name in biz_tables else []
        expected = get_expected_columns(
            where_columns=where_cols.get(table_name, []),
            required_columns=get_cached_required_cols(table_name),
            business_rule_columns=biz_cols,
        )
        print(json.dumps({'table': table_name, 'expected_columns': expected}, ensure_ascii=False, indent=2))

    else:
        print(f"未知命令: {cmd}")
        sys.exit(1)


if __name__ == '__main__':
    main()
