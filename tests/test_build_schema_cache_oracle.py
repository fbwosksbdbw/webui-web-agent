import importlib.util
import sys
import types
from pathlib import Path


def _load_build_schema_cache():
    scripts_dir = Path(__file__).resolve().parents[1] / "knowledge" / "scripts"
    spec = importlib.util.spec_from_file_location("build_schema_cache_under_test", scripts_dir / "build_schema_cache.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_get_db_connection_uses_oracledb_for_oracle_config(monkeypatch):
    module = _load_build_schema_cache()
    calls = {}

    class Config:
        @staticmethod
        def get_db_config(_env_key):
            return {
                "common": {"host": "10.20.27.108", "port": 1521, "db_type": "oracle"},
                "databases": {
                    "GYYHAUTO": {"user": "gyyhauto", "password": "handsome", "database": "GYYHAUTO"}
                },
            }

    fake_config_module = types.ModuleType("conf.config")
    fake_config_module.Config = Config
    monkeypatch.setitem(sys.modules, "conf", types.ModuleType("conf"))
    monkeypatch.setitem(sys.modules, "conf.config", fake_config_module)

    fake_oracledb = types.SimpleNamespace(
        connect=lambda **kwargs: calls.setdefault("connect", kwargs) or types.SimpleNamespace()
    )
    monkeypatch.setitem(sys.modules, "oracledb", fake_oracledb)

    conn = module.get_db_connection("GYYHAUTO", env_key="gyyh-auto")

    assert conn is not None
    assert calls["connect"] == {
        "user": "gyyhauto",
        "password": "handsome",
        "dsn": "10.20.27.108:1521/GYYHAUTO",
    }


def test_get_db_connection_initializes_oracle_thick_client_from_config(monkeypatch):
    module = _load_build_schema_cache()
    calls = []

    class Config:
        @staticmethod
        def get_db_config(_env_key):
            return {
                "common": {
                    "host": "10.20.27.108",
                    "port": 1521,
                    "db_type": "oracle",
                    "oracle_client_lib_dir": r"D:\hundsun\Oracle\bin",
                },
                "databases": {
                    "GYYHAUTO": {"user": "gyyhauto", "password": "handsome", "database": "orcl"}
                },
            }

    fake_config_module = types.ModuleType("conf.config")
    fake_config_module.Config = Config
    monkeypatch.setitem(sys.modules, "conf", types.ModuleType("conf"))
    monkeypatch.setitem(sys.modules, "conf.config", fake_config_module)

    fake_oracledb = types.SimpleNamespace(
        init_oracle_client=lambda **kwargs: calls.append(("init", kwargs)),
        connect=lambda **kwargs: calls.append(("connect", kwargs)) or types.SimpleNamespace(),
    )
    monkeypatch.setitem(sys.modules, "oracledb", fake_oracledb)

    conn = module.get_db_connection("GYYHAUTO", env_key="gyyh-auto")

    assert conn is not None
    assert calls[0] == ("init", {"lib_dir": r"D:\hundsun\Oracle\bin"})
    assert calls[1][0] == "connect"


def test_describe_table_oracle_returns_describe_compatible_rows():
    module = _load_build_schema_cache()

    class Cursor:
        def __init__(self):
            self.sql = ""

        def execute(self, sql, params=None):
            self.sql = sql
            self.params = params

        def fetchall(self):
            if "USER_TAB_COLUMNS" in self.sql:
                return [
                    ("CUSTOMER_ID", "VARCHAR2", 32, None, None, "N", None),
                    ("AMOUNT", "NUMBER", None, 18, 2, "Y", "0"),
                    ("CREATED_AT", "DATE", None, None, None, "N", "SYSDATE"),
                ]
            if "USER_CONSTRAINTS" in self.sql:
                return [("CUSTOMER_ID",)]
            return []

        def close(self):
            pass

    class Connection:
        _hui_db_type = "oracle"

        def cursor(self):
            return Cursor()

    rows = module.describe_table(Connection(), "tb_customer")

    assert rows == [
        {"Field": "customer_id", "Type": "varchar2(32)", "Null": "NO", "Key": "PRI", "Default": None, "Extra": ""},
        {"Field": "amount", "Type": "number(18,2)", "Null": "YES", "Key": "", "Default": "0", "Extra": ""},
        {"Field": "created_at", "Type": "date", "Null": "NO", "Key": "", "Default": "SYSDATE", "Extra": ""},
    ]


def test_build_table_db_mapping_maps_oracle_tables_to_single_db_family(monkeypatch):
    module = _load_build_schema_cache()
    saved = {}

    class Cursor:
        def execute(self, _sql, _params=None):
            pass

        def fetchall(self):
            return [("TBCUSTOMER",), ("TBORDER1",)]

        def close(self):
            pass

    class Connection:
        _hui_db_type = "oracle"

        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(module, "get_db_connection", lambda _db, env_key=None: Connection())
    monkeypatch.setattr(module, "load_table_db_mapping", lambda force_reload=False: {})
    monkeypatch.setattr(module, "save_table_db_mapping", lambda mapping: saved.update(mapping))

    mapping = module.build_table_db_mapping(["GYYHAUTO"], env_key="gyyh-auto")

    assert mapping["tbcustomer"] == ["GYYHAUTO"]
    assert mapping["tborder"] == ["GYYHAUTO"]
    assert saved["tbcustomer"] == ["GYYHAUTO"]


def test_build_table_db_mapping_replaces_legacy_mapping_for_single_db(monkeypatch):
    module = _load_build_schema_cache()
    saved = {}

    class Cursor:
        def execute(self, _sql, _params=None):
            pass

        def fetchall(self):
            return [("TBPRODUCT",), ("TSYS_MENU",)]

        def close(self):
            pass

    class Connection:
        _hui_db_type = "oracle"

        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(module, "get_db_connection", lambda _db, env_key=None: Connection())
    monkeypatch.setattr(
        module,
        "load_table_db_mapping",
        lambda force_reload=False: {"legacy_only": ["ui_fund"], "tbproduct": ["ui_bc"]},
    )
    monkeypatch.setattr(module, "save_table_db_mapping", lambda mapping: saved.update(mapping))

    mapping = module.build_table_db_mapping(["GYYHAUTO"], env_key="gyyh-auto")

    assert mapping == {"tbproduct": ["GYYHAUTO"], "tsys_menu": ["GYYHAUTO"]}
    assert saved == mapping


def test_build_cache_passes_oracle_unique_constraints_to_schema_seed(monkeypatch):
    module = _load_build_schema_cache()
    saved_cache = {}
    saved_mapping = {}

    class Cursor:
        def __init__(self):
            self.sql = ""

        def execute(self, sql, params=None):
            self.sql = sql

        def fetchall(self):
            if "USER_TAB_COLUMNS" in self.sql:
                return [("CUSTOMER_ID", "VARCHAR2", 32, None, None, "N", None)]
            if "USER_CONSTRAINTS" in self.sql:
                return [("PK_TB_CUSTOMER", "CUSTOMER_ID"), ("UK_TB_CUSTOMER_NO", "CUSTOMER_NO")]
            return []

        def close(self):
            pass

    class Connection:
        _hui_db_type = "oracle"

        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(module, "get_db_connection", lambda _db, env_key=None: Connection())
    monkeypatch.setattr(module, "load_schema_cache", lambda: {})
    monkeypatch.setattr(module, "load_db_cache", lambda _family: {})
    monkeypatch.setattr(module, "save_schema_cache", lambda _cache: None)
    monkeypatch.setattr(module, "_save_db_family_cache", lambda family, cache: saved_cache.update({"family": family, "cache": cache}))
    monkeypatch.setattr(module, "load_table_db_mapping", lambda force_reload=False: {})
    monkeypatch.setattr(module, "save_table_db_mapping", lambda mapping: saved_mapping.update(mapping))

    module.build_cache({"GYYHAUTO": ["tb_customer"]}, env_key="gyyh-auto")

    assert saved_mapping["tb_customer"] == ["GYYHAUTO"]
    assert saved_cache["family"] == "GYYHAUTO"
    assert saved_cache["cache"]["tb_customer"]["unique_indexes"] == [
        {"name": "PK_TB_CUSTOMER", "columns": ["customer_id"]},
        {"name": "UK_TB_CUSTOMER_NO", "columns": ["customer_no"]},
    ]


def test_build_cache_auto_discovers_all_tables_for_single_db(monkeypatch):
    module = _load_build_schema_cache()
    saved_tables = []

    class Cursor:
        def __init__(self):
            self.sql = ""

        def execute(self, sql, params=None):
            self.sql = sql

        def fetchall(self):
            if "user_tables" in self.sql.lower():
                return [("TBCUSTOMER",), ("TBORDER",)]
            if "USER_TAB_COLUMNS" in self.sql:
                table_name = "CUSTOMER_ID" if self.sql else "ID"
                return [(table_name, "VARCHAR2", 32, None, None, "N", None)]
            return []

        def close(self):
            pass

    class Connection:
        _hui_db_type = "oracle"

        def cursor(self):
            return Cursor()

        def close(self):
            pass

    monkeypatch.setattr(module, "get_db_connection", lambda _db, env_key=None: Connection())
    monkeypatch.setattr(module, "load_schema_cache", lambda: {})
    monkeypatch.setattr(module, "save_schema_cache", lambda _cache: None)
    monkeypatch.setattr(module, "load_db_cache", lambda _family: {})
    monkeypatch.setattr(module, "_save_db_family_cache", lambda _family, cache: saved_tables.extend(cache.keys()))
    monkeypatch.setattr(module, "load_table_db_mapping", lambda force_reload=False: {})
    monkeypatch.setattr(module, "save_table_db_mapping", lambda _mapping: None)

    module.build_cache({"GYYHAUTO": []}, auto_discover=True, env_key="gyyh-auto")

    assert saved_tables == ["tbcustomer", "tborder"]
