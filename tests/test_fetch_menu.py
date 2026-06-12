import importlib.util
import json
import sys
import types
from pathlib import Path


def _load_fetch_menu():
    config_dir = Path(__file__).resolve().parents[1] / "knowledge" / "config"
    spec = importlib.util.spec_from_file_location("fetch_menu_under_test", config_dir / "fetch_menu.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_fetch_menu_uses_oracle_connection_from_project_config(tmp_path, monkeypatch):
    module = _load_fetch_menu()
    project = tmp_path / "project"
    (project / "conf").mkdir(parents=True)
    (project / "conf" / "__init__.py").write_text("", encoding="utf-8")
    (project / "conf" / "config.py").write_text(
        "class Config:\n"
        "    @staticmethod\n"
        "    def get_db_config(_env):\n"
        "        return {\n"
        "            'common': {'host': '10.20.27.108', 'port': 1521, 'db_type': 'oracle'},\n"
        "            'databases': {'GYYHAUTO': {'user': 'gyyhauto', 'password': 'handsome', 'database': 'orcl'}},\n"
        "        }\n",
        encoding="utf-8",
    )
    calls = {}
    fake_oracledb = types.SimpleNamespace(
        connect=lambda **kwargs: calls.setdefault("connect", kwargs) or types.SimpleNamespace()
    )
    monkeypatch.setitem(sys.modules, "oracledb", fake_oracledb)

    db_cfg = module.load_db_config(str(project), "gyyh-auto", "GYYHAUTO")
    conn = module.connect_db(db_cfg)

    assert conn is not None
    assert calls["connect"] == {
        "user": "gyyhauto",
        "password": "handsome",
        "dsn": "10.20.27.108:1521/orcl",
    }


def test_fetch_menu_initializes_oracle_thick_client_from_env(monkeypatch):
    module = _load_fetch_menu()
    calls = []
    fake_oracledb = types.SimpleNamespace(
        init_oracle_client=lambda **kwargs: calls.append(("init", kwargs)),
        connect=lambda **kwargs: calls.append(("connect", kwargs)) or types.SimpleNamespace(),
    )
    monkeypatch.setitem(sys.modules, "oracledb", fake_oracledb)
    monkeypatch.setenv("ORACLE_CLIENT_LIB_DIR", r"D:\hundsun\Oracle\bin")

    module.connect_db({
        "db_type": "oracle",
        "host": "10.20.27.108",
        "port": 1521,
        "database": "orcl",
        "user": "gyyhauto",
        "password": "handsome",
    })

    assert calls[0] == ("init", {"lib_dir": r"D:\hundsun\Oracle\bin"})
    assert calls[1][0] == "connect"


def test_fetch_menu_rows_filters_ifmcounter_and_serializes_tuple_rows(tmp_path):
    module = _load_fetch_menu()
    executed = []

    class Cursor:
        description = [("MENU_CODE",), ("MENU_NAME",), ("TREE_IDX",), ("REMARK",)]

        def execute(self, sql):
            executed.append(sql)

        def fetchall(self):
            return [
                ("M1", "菜单A", "/console/a/", None),
                ("M2", "ifmcounter菜单", "/console/ifmcounter/", None),
            ]

        def close(self):
            pass

    class Connection:
        def cursor(self):
            return Cursor()

    rows = module.fetch_menu_rows(Connection())
    output = tmp_path / "menu_urls.json"
    module.write_menu_urls(rows, output)

    assert executed == ["SELECT * FROM tsys_menu"]
    assert rows == [{"menu_code": "M1", "menu_name": "菜单A", "tree_idx": "/console/a/", "remark": None}]
    assert json.loads(output.read_text(encoding="utf-8")) == rows
