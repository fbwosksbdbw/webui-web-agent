import importlib.util
import sys
import types
from pathlib import Path


def _load_sql_extractor():
    scripts_dir = Path(__file__).resolve().parents[1] / "knowledge" / "scripts"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("sql_extractor", scripts_dir / "sql_extractor.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_timestamped_current_sql_lines_are_split_and_business_join_tables_are_kept():
    sql_extractor = _load_sql_extractor()
    log_text = """
2026-06-08 23:27:42.447 DEBUG SQL执行,当前SQL:select * from tbsequence where hs_key=? 当前参数:log_no
2026-06-08 23:27:42.454 DEBUG SQL执行,当前SQL:select a.*,c.client_name,b.client_group,b.open_flag,d.bank_acc,c.client_type from tbhisassetacc a,tbclientseller10 b,tbclient10 c,tbbankacc10 d where a.in_client_no = ? and a.in_client_no = d.in_client_no and a.in_client_no=b.in_client_no and a.in_client_no=c.in_client_no and a.prd_type='8' and a.open_branch in ('0011') and d.bank_acc = ? order by a.in_client_no 当前参数:33060210021113,A33060210021113
2026-06-08 23:27:42.460 DEBUG SQL执行,当前SQL:select tree_idx from tsys_menu where sub_trans_code = ? 当前参数:ifmCDxTrustHisAccountInfoQry
"""

    requirements = sql_extractor.extract_sql_requirements(log_text)
    physical_tables = sql_extractor.extract_physical_tables(log_text)

    assert "tbhisassetacc" in requirements["tables"]
    assert "tbclientseller10" in requirements["join_tables"]
    assert "tbclient10" in requirements["join_tables"]
    assert physical_tables["tbhisassetacc"] == ["tbhisassetacc"]
    assert physical_tables["tbclientseller"] == ["tbclientseller10"]
    assert physical_tables["tbclient"] == ["tbclient10"]
    assert physical_tables["tbbankacc"] == ["tbbankacc10"]


def test_ast_extraction_maps_where_columns_inside_derived_table_subqueries():
    sql_extractor = _load_sql_extractor()
    log_text = """
当前SQL:select cm.bank_acc, cs.seller_code
from (
  select client_no, bank_acc
  from tbclientmap10
  where bank_acc = ?
) cm
join tbclientseller10 cs on cm.client_no = cs.client_no
where cs.seller_code = ?
"""

    requirements = sql_extractor.extract_sql_requirements(log_text)

    assert "tbclientmap10" in requirements["tables"]
    assert "tbclientseller10" in requirements["join_tables"]
    assert "bank_acc" in requirements["where_columns"]["tbclientmap10"]
    assert "jo" not in requirements["where_columns"]["tbclientmap10"]
    assert "seller_code" in requirements["where_columns"]["tbclientseller10"]
    assert requirements["join_key_columns"]["tbclientmap10"] == ["client_no"]
    assert requirements["join_key_columns"]["tbclientseller10"] == ["client_no"]


def test_build_ast_diagnostics_returns_human_and_structured_views():
    sql_extractor = _load_sql_extractor()
    log_text = """
当前SQL:select a.bank_acc,b.client_name
from tbclientmap10 a
join tbclient10 b on a.client_no=b.client_no
where a.bank_acc='A1'
"""

    diagnostics = sql_extractor.build_ast_diagnostics(log_text)

    assert diagnostics["select_count"] == 1
    assert diagnostics["selects"][0]["tables"] == ["tbclientmap10"]
    assert diagnostics["selects"][0]["join_tables"] == ["tbclient10"]
    assert diagnostics["selects"][0]["where_columns"]["tbclientmap10"] == ["bank_acc"]
    assert diagnostics["selects"][0]["join_key_columns"]["tbclientmap10"] == ["client_no"]
    assert "-- raw_sql[1]" in diagnostics["text"]
    assert "-- join_key_columns" in diagnostics["text"]


def test_build_minimal_prompt_accepts_dict_column_cache_entries():
    sql_extractor = _load_sql_extractor()

    prompt = sql_extractor.build_minimal_prompt(
        table_name="tbtrustreq",
        where_columns=[{"Field": "bank_acc"}],
        required_columns=[{"Field": "serial_no"}, {"name": "apply_date"}],
        business_rule_columns=[{"field": "in_client_no"}],
    )

    assert "bank_acc" in prompt
    assert "serial_no" in prompt
    assert "apply_date" in prompt
    assert "in_client_no" in prompt


def test_user_branch_permission_table_is_classified_as_system():
    sql_extractor = _load_sql_extractor()

    result = sql_extractor._classify_by_name_rules("tbuserbranch")

    assert result["classification"] == "system"


def test_workflow_engine_tables_are_classified_as_system():
    sql_extractor = _load_sql_extractor()

    for table in (
        "tbworkflowsubtrans",
        "ACT_RU_TASK",
        "ACT_RU_EXECUTION",
        "flow_instance",
        "flow_cust_nodecfg",
    ):
        result = sql_extractor._classify_by_name_rules(table)
        assert result["classification"] == "system"


def test_classify_tables_uses_oracle_catalog(monkeypatch):
    sql_extractor = _load_sql_extractor()

    class Cursor:
        def execute(self, sql, params=None):
            self.sql = sql
            self.params = params

        def fetchone(self):
            return ("BIZTABLE", "BASE TABLE")

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    class Config:
        env_dbs = {
            "gyyh-auto": {
                "common": {"host": "10.20.27.108", "port": 1521, "db_type": "oracle"},
                "databases": {
                    "GYYHAUTO": {"user": "gyyhauto", "password": "handsome", "database": "GYYHAUTO"}
                },
            }
        }

        @staticmethod
        def get_db_config(_env):
            return Config.env_dbs["gyyh-auto"]

    fake_config_module = types.ModuleType("conf.config")
    fake_config_module.Config = Config
    monkeypatch.setitem(sys.modules, "conf", types.ModuleType("conf"))
    monkeypatch.setitem(sys.modules, "conf.config", fake_config_module)
    monkeypatch.setitem(sys.modules, "oracledb", types.SimpleNamespace(connect=lambda **_kwargs: Connection()))
    monkeypatch.setattr(sql_extractor, "resolve_shard_dbs", lambda *_args, **_kwargs: ["GYYHAUTO"])
    monkeypatch.setattr(sql_extractor, "table_to_db", lambda _table: "GYYHAUTO")

    result = sql_extractor.classify_tables({"biztable": ["BIZTABLE"]}, routing_db="GYYHAUTO", env_name="gyyh-auto")

    assert result["biztable"]["type"] == "BASE TABLE"
    assert result["biztable"]["classification"] == "business"
    assert result["biztable"]["db"] == "GYYHAUTO"


def test_describe_table_columns_uses_oracle_catalog(monkeypatch):
    sql_extractor = _load_sql_extractor()

    class Cursor:
        def execute(self, sql, params=None):
            self.sql = sql

        def fetchall(self):
            return [("CUSTOMER_ID", "VARCHAR2", 32, None, None, "N", None)]

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

        def close(self):
            pass

    class Config:
        @staticmethod
        def get_db_config(_env):
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
    monkeypatch.setitem(sys.modules, "oracledb", types.SimpleNamespace(connect=lambda **_kwargs: Connection()))

    columns = sql_extractor.describe_table_columns("tb_customer", "GYYHAUTO", env_name="gyyh-auto")

    assert columns == [
        {"Field": "customer_id", "Type": "varchar2(32)", "Null": "NO", "Key": "", "Default": None, "Extra": ""}
    ]
