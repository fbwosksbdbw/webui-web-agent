import importlib.util
import sys
import types
from pathlib import Path


def _load_dry_run_sql():
    scripts_dir = Path(__file__).resolve().parents[1] / "knowledge" / "scripts"
    spec = importlib.util.spec_from_file_location("dry_run_sql", scripts_dir / "dry_run_sql.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_load_db_config_preserves_oracle_db_type(tmp_path, monkeypatch):
    module = _load_dry_run_sql()
    project = tmp_path / "project"
    (project / "conf").mkdir(parents=True)
    (project / "conf" / "__init__.py").write_text("", encoding="utf-8")
    (project / "conf" / "config.py").write_text(
        "class Config:\n"
        "    @staticmethod\n"
        "    def get_db_config(_env):\n"
        "        return {\n"
        "            'common': {'host': '10.20.27.108', 'port': 1521, 'db_type': 'oracle'},\n"
        "            'databases': {'GYYHAUTO': {'user': 'gyyhauto', 'password': 'handsome', 'database': 'GYYHAUTO'}},\n"
        "        }\n",
        encoding="utf-8",
    )
    config_path = tmp_path / "config.json"
    config_path.write_text(
        '{"env": {"env_key": "gyyh-auto", "project_dir": "' + str(project).replace("\\", "/") + '"}}',
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(project))

    db_cfg = module._load_db_config(str(config_path), "GYYHAUTO")

    assert db_cfg["db_type"] == "oracle"
    assert db_cfg["database"] == "GYYHAUTO"


def test_connect_uses_oracledb_for_oracle_configs(monkeypatch):
    module = _load_dry_run_sql()
    calls = {}

    fake_oracledb = types.SimpleNamespace(
        connect=lambda **kwargs: calls.setdefault("connect", kwargs) or object()
    )
    monkeypatch.setitem(sys.modules, "oracledb", fake_oracledb)

    module._connect(
        {
            "db_type": "oracle",
            "host": "10.20.27.108",
            "port": 1521,
            "user": "gyyhauto",
            "password": "handsome",
            "database": "GYYHAUTO",
        }
    )

    assert calls["connect"] == {
        "user": "gyyhauto",
        "password": "handsome",
        "dsn": "10.20.27.108:1521/GYYHAUTO",
    }


def test_dry_run_oracle_skips_mysql_transaction_commands():
    module = _load_dry_run_sql()
    executed = []

    class Cursor:
        def execute(self, sql):
            executed.append(sql)
            if sql == "START TRANSACTION":
                raise AssertionError("Oracle dry-run must not use MySQL transaction syntax")

        def close(self):
            pass

    class Connection:
        _hui_db_type = "oracle"

        def cursor(self):
            return Cursor()

        def rollback(self):
            executed.append("ROLLBACK")

    result = module.dry_run_statements(Connection(), [("INSERT INTO demo(id) VALUES(1)", 1)])

    assert result["ok"] is True
    assert "START TRANSACTION" not in executed
    assert executed == ["INSERT INTO demo(id) VALUES(1)", "ROLLBACK"]
