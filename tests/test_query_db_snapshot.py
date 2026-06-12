import importlib.util
import json
from decimal import Decimal
from pathlib import Path


def _load_query_db_snapshot():
    scripts_dir = Path(__file__).resolve().parents[1] / "knowledge" / "scripts"
    spec = importlib.util.spec_from_file_location("query_db_snapshot", scripts_dir / "query_db_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_snapshot_db_config_uses_project_config_loader(tmp_path, monkeypatch):
    module = _load_query_db_snapshot()
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps({"env": {"env_key": "ifm60-dxfund", "project_dir": "F:/project"}}, ensure_ascii=False),
        encoding="utf-8",
    )
    calls = []

    def fake_load_db_config(config_json_path, db_key, env_override=None):
        calls.append((config_json_path, db_key, env_override))
        return {
            "host": "10.20.150.200",
            "port": 3306,
            "user": "fund_user",
            "password": "secret",
            "database": "ui_fund2",
        }

    monkeypatch.setattr(module.dry_run_sql, "_load_db_config", fake_load_db_config)

    db_cfg = module.get_db_config(str(config_path), "ui_fund2", env_override="ifm60-dxfund")

    assert calls == [(str(config_path), "ui_fund2", "ifm60-dxfund")]
    assert db_cfg["host"] == "10.20.150.200"
    assert db_cfg["database"] == "ui_fund2"


def test_extract_tables_from_sql_supports_qualified_names():
    module = _load_query_db_snapshot()

    tables = module.extract_tables_from_sql(
        "DELETE FROM ui_fund2.tbdxfundtranscfm13 WHERE cfm_no='1';\n"
        "INSERT INTO `ui_fund2`.`tbdxfundtranscfmqry`(id) VALUES('1');\n"
        "UPDATE tbclientmap SET x='1';"
    )

    assert tables == ["tbdxfundtranscfm13", "tbdxfundtranscfmqry", "tbclientmap"]


def test_table_snapshot_serializes_decimal_values():
    module = _load_query_db_snapshot()

    class Cursor:
        def execute(self, _sql):
            pass

        def fetchall(self):
            return [{"amount": Decimal("12.30")}]

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

    snapshot = module.query_table_snapshot(Connection(), "biztable")

    assert snapshot["rows"] == [{"amount": "12.30"}]


def test_snapshot_connect_db_reuses_dry_run_oracle_connection(monkeypatch):
    module = _load_query_db_snapshot()
    calls = {}

    def fake_connect(db_cfg):
        calls["db_cfg"] = db_cfg
        return object()

    monkeypatch.setattr(module.dry_run_sql, "_connect", fake_connect)

    conn = module.connect_db({"db_type": "oracle", "database": "GYYHAUTO"})

    assert conn is not None
    assert calls["db_cfg"] == {"db_type": "oracle", "database": "GYYHAUTO"}
