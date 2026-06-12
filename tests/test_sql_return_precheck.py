import importlib.util
import json
from pathlib import Path


def _load_sql_return_precheck():
    scripts_dir = Path(__file__).resolve().parents[1] / "knowledge" / "scripts"
    spec = importlib.util.spec_from_file_location("sql_return_precheck", scripts_dir / "sql_return_precheck.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeCursor:
    def __init__(self):
        self.executed = []

    def execute(self, sql):
        self.executed.append(sql)

    def fetchone(self):
        last = self.executed[-1]
        if "'100'" in last:
            return (2,)
        if "'0011'" in last:
            return (1,)
        return (0,)

    def close(self):
        pass


class FakeConnection:
    def __init__(self):
        self.cursor_obj = FakeCursor()
        self.rollback_called = False
        self.closed = False

    def cursor(self):
        return self.cursor_obj

    def rollback(self):
        self.rollback_called = True

    def close(self):
        self.closed = True


def test_return_precheck_reports_branch_and_headquarter_counts(tmp_path, monkeypatch):
    module = _load_sql_return_precheck()
    conn = FakeConnection()
    monkeypatch.setattr(module.dry_run_sql, "_load_db_config", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(module.dry_run_sql, "_connect", lambda *_args, **_kwargs: conn)
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    preset_sql = case_dir / "preset.sql"
    preset_sql.write_text(
        "DELETE FROM biztable WHERE bank_acc='A1';\nINSERT INTO biztable(bank_acc,open_branch) VALUES('A1','0011');\n",
        encoding="utf-8",
    )
    config_path = case_dir / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    case_plan_path = case_dir / "case_plan.json"
    case_plan_path.write_text(
        json.dumps(
            {
                "presets": {"branch_no": "0011", "hq_branch_no": "100"},
                "expected_counts": {"branch": 1, "headquarter": 2},
                "sql_paths": {"preset_sql": "preset.sql"},
                "log_analysis": {
                    "requirements": {
                        "raw_sqls": ["select * from biztable where bank_acc='A1' and open_branch in ('0011')"]
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = module.run_precheck(case_plan_path=case_plan_path, config_path=config_path, db_key="ui_fund")

    assert result["status"] == "passed"
    assert result["permission_checks"]["branch"]["actual"] == 1
    assert result["permission_checks"]["branch"]["expected"] == 1
    assert result["permission_checks"]["headquarter"]["actual"] == 2
    assert result["permission_checks"]["headquarter"]["expected"] == 2
    assert any("'100'" in sql for sql in conn.cursor_obj.executed)


def test_return_precheck_diagnoses_query_predicate_value_mismatches():
    module = _load_sql_return_precheck()
    case_plan = {
        "insert_plans": [
            {
                "table": "biztable",
                "physical_table": "biztable",
                "columns": [
                    {"name": "bank_acc", "value": "A1", "source": "form_fields.客户标识"},
                    {"name": "prd_code", "value": "P1", "source": "presets.prd_code"},
                ],
            }
        ]
    }

    diagnostics = module._diagnose_return_check_failure(
        "select * from biztable where bank_acc='A2' and prd_code='P1' and ta_code='HT'",
        case_plan,
    )

    assert diagnostics == {
        "tables": ["biztable"],
        "predicate_checks": [
            {
                "table": "biztable",
                "column": "bank_acc",
                "query_value": "A2",
                "insert_value": "A1",
                "status": "mismatch",
                "source": "form_fields.客户标识",
            },
            {
                "table": "biztable",
                "column": "prd_code",
                "query_value": "P1",
                "insert_value": "P1",
                "status": "matched",
                "source": "presets.prd_code",
            },
            {
                "table": "biztable",
                "column": "ta_code",
                "query_value": "HT",
                "insert_value": "",
                "status": "missing_insert_value",
                "source": "",
            },
        ],
        "summary": "bank_acc mismatch query=A2 insert=A1; ta_code missing_insert_value query=HT",
    }


def test_return_precheck_diagnostics_do_not_match_missing_empty_insert_value():
    module = _load_sql_return_precheck()
    case_plan = {"insert_plans": [{"table": "biztable", "columns": []}]}

    diagnostics = module._diagnose_return_check_failure(
        "select * from biztable where ta_code=''",
        case_plan,
    )

    assert diagnostics["predicate_checks"][0]["status"] == "missing_insert_value"


def test_return_precheck_diagnostics_use_table_aliases_for_duplicate_columns():
    module = _load_sql_return_precheck()
    case_plan = {
        "insert_plans": [
            {
                "table": "lefttable",
                "columns": [{"name": "bank_acc", "value": "L1", "source": "form_fields.左客户"}],
            },
            {
                "table": "righttable",
                "columns": [{"name": "bank_acc", "value": "R1", "source": "form_fields.右客户"}],
            },
        ]
    }

    diagnostics = module._diagnose_return_check_failure(
        "select * from lefttable l join righttable r on l.client_no=r.client_no "
        "where r.bank_acc='R2' and l.bank_acc='L1'",
        case_plan,
    )

    assert diagnostics["predicate_checks"] == [
        {
            "table": "righttable",
            "column": "bank_acc",
            "query_value": "R2",
            "insert_value": "R1",
            "status": "mismatch",
            "source": "form_fields.右客户",
        },
        {
            "table": "lefttable",
            "column": "bank_acc",
            "query_value": "L1",
            "insert_value": "L1",
            "status": "matched",
            "source": "form_fields.左客户",
        },
    ]


def test_return_precheck_diagnostics_include_comma_join_table_aliases():
    module = _load_sql_return_precheck()
    case_plan = {
        "insert_plans": [
            {
                "table": "lefttable",
                "columns": [{"name": "bank_acc", "value": "L1", "source": "form_fields.左客户"}],
            },
            {
                "table": "righttable",
                "columns": [{"name": "bank_acc", "value": "R1", "source": "form_fields.右客户"}],
            },
        ]
    }

    diagnostics = module._diagnose_return_check_failure(
        "select l.bank_acc,r.bank_acc from lefttable l, righttable r "
        "where r.bank_acc='R2'",
        case_plan,
    )

    assert diagnostics["tables"] == ["lefttable", "righttable"]
    assert diagnostics["predicate_checks"][0]["table"] == "righttable"


def test_return_precheck_attaches_diagnostics_when_query_returns_zero(tmp_path, monkeypatch):
    module = _load_sql_return_precheck()
    conn = FakeConnection()
    monkeypatch.setattr(module.dry_run_sql, "_load_db_config", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(module.dry_run_sql, "_connect", lambda *_args, **_kwargs: conn)
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "preset.sql").write_text(
        "DELETE FROM biztable WHERE bank_acc='A1';\n"
        "INSERT INTO biztable(bank_acc,prd_code) VALUES('A1','P1');\n",
        encoding="utf-8",
    )
    config_path = case_dir / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    case_plan_path = case_dir / "case_plan.json"
    case_plan_path.write_text(
        json.dumps(
            {
                "sql_paths": {"preset_sql": "preset.sql"},
                "insert_plans": [
                    {
                        "table": "biztable",
                        "columns": [
                            {"name": "bank_acc", "value": "A1", "source": "form_fields.客户标识"},
                            {"name": "prd_code", "value": "P1", "source": "presets.prd_code"},
                        ],
                    }
                ],
                "log_analysis": {
                    "requirements": {
                        "raw_sqls": ["select * from biztable where bank_acc='A2' and prd_code='P1'"]
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = module.run_precheck(case_plan_path=case_plan_path, config_path=config_path, db_key="ui_fund")

    assert result["status"] == "failed"
    diagnostics = result["checks"][0]["diagnostics"]
    assert diagnostics["summary"] == "bank_acc mismatch query=A2 insert=A1"
    assert diagnostics["predicate_checks"][0] == {
        "table": "biztable",
        "column": "bank_acc",
        "query_value": "A2",
        "insert_value": "A1",
        "status": "mismatch",
        "source": "form_fields.客户标识",
    }


def test_headquarter_permission_sql_expands_branch_in_predicate():
    module = _load_sql_return_precheck()

    sql, reason = module._permission_role_sql(
        "select * from biztable where open_branch in ('0011') and bank_acc='A1'",
        "headquarter",
        {"branch_no": "0011", "hq_branch_no": "100"},
    )

    assert reason == ""
    assert "open_branch in ('0011', '100')" in sql


def test_return_precheck_supports_additional_permission_columns(tmp_path, monkeypatch):
    module = _load_sql_return_precheck()
    conn = FakeConnection()
    monkeypatch.setattr(module.dry_run_sql, "_load_db_config", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(module.dry_run_sql, "_connect", lambda *_args, **_kwargs: conn)
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "preset.sql").write_text(
        "DELETE FROM biztable WHERE bank_acc='A1';\n"
        "INSERT INTO biztable(bank_acc,org_no,seller_branch,belong_branch,branch_code,open_org) "
        "VALUES('A1','0011','0011','0011','0011','0011');\n",
        encoding="utf-8",
    )
    config_path = case_dir / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    case_plan_path = case_dir / "case_plan.json"
    case_plan_path.write_text(
        json.dumps(
            {
                "presets": {"branch_no": "0011", "hq_branch_no": "100"},
                "expected_counts": {"branch": 1, "headquarter": 2},
                "sql_paths": {"preset_sql": "preset.sql"},
                "log_analysis": {
                    "requirements": {
                        "raw_sqls": [
                            "select * from biztable where org_no='0011' and seller_branch='0011' "
                            "and belong_branch='0011' and branch_code='0011' and open_org='0011'"
                        ]
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = module.run_precheck(case_plan_path=case_plan_path, config_path=config_path, db_key="ui_fund")

    assert result["status"] == "passed"
    hq_sql = result["permission_checks"]["headquarter"]["sql"]
    for column in ("org_no", "seller_branch", "belong_branch", "branch_code", "open_org"):
        assert f"{column}='100'" in hq_sql
    assert any("org_no='100'" in sql and "open_org='100'" in sql for sql in conn.cursor_obj.executed)


def test_return_precheck_derives_permission_counts_from_projected_branch_aliases(tmp_path, monkeypatch):
    module = _load_sql_return_precheck()
    conn = FakeConnection()
    monkeypatch.setattr(module.dry_run_sql, "_load_db_config", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(module.dry_run_sql, "_connect", lambda *_args, **_kwargs: conn)
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "preset.sql").write_text(
        "DELETE FROM biztable WHERE bank_acc='A1';\nINSERT INTO biztable(bank_acc,branch_no) VALUES('A1','0011');\n",
        encoding="utf-8",
    )
    config_path = case_dir / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    case_plan_path = case_dir / "case_plan.json"
    case_plan_path.write_text(
        json.dumps(
            {
                "presets": {"branch_no": "0011", "hq_branch_no": "100"},
                "expected_counts": {"branch": 1, "headquarter": 2},
                "sql_paths": {"preset_sql": "preset.sql"},
                "log_analysis": {
                    "requirements": {
                        "raw_sqls": [
                            "select a.branch_no branch_sub,b.branch_no branch_red,a.bank_acc "
                            "from biztable a,biztable b where a.bank_acc=b.bank_acc and a.bank_acc='0011'"
                        ]
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = module.run_precheck(case_plan_path=case_plan_path, config_path=config_path, db_key="ui_fund")

    assert result["status"] == "passed"
    assert result["permission_checks"]["branch"]["status"] == "passed"
    assert result["permission_checks"]["headquarter"]["status"] == "passed"
    branch_sql = result["permission_checks"]["branch"]["sql"]
    hq_sql = result["permission_checks"]["headquarter"]["sql"]
    assert "hui_permission_sub" in branch_sql
    assert "branch_sub = '0011'" in branch_sql
    assert "branch_red = '0011'" in branch_sql
    assert "branch_sub = branch_red" in hq_sql
    assert "branch_sub IN ('0011', '100')" in hq_sql


def test_return_precheck_substitutes_current_params_before_counting(tmp_path, monkeypatch):
    module = _load_sql_return_precheck()
    conn = FakeConnection()
    monkeypatch.setattr(module.dry_run_sql, "_load_db_config", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(module.dry_run_sql, "_connect", lambda *_args, **_kwargs: conn)
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "preset.sql").write_text(
        "DELETE FROM biztable WHERE bank_acc='A1';\nINSERT INTO biztable(bank_acc,open_branch) VALUES('A1','0011');\n",
        encoding="utf-8",
    )
    config_path = case_dir / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    case_plan_path = case_dir / "case_plan.json"
    case_plan_path.write_text(
        json.dumps(
            {
                "sql_paths": {"preset_sql": "preset.sql"},
                "log_analysis": {
                    "requirements": {
                        "raw_sqls": [
                            "select * from biztable where bank_acc=? and open_branch=? 当前参数:A1,0011"
                        ]
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = module.run_precheck(case_plan_path=case_plan_path, config_path=config_path, db_key="ui_fund")

    assert result["status"] == "passed"
    assert result["checks"][0]["status"] == "passed"
    assert result["checks"][0]["sql"].endswith("open_branch='0011'")
    assert any("bank_acc='A1'" in sql and "open_branch='0011'" in sql for sql in conn.cursor_obj.executed)


def test_return_precheck_executes_base_and_business_sql_on_single_db(tmp_path, monkeypatch):
    module = _load_sql_return_precheck()
    connections = {}
    load_calls = []

    def fake_load(_config_path, db_key, _env_override=None):
        load_calls.append(db_key)
        return {"database": db_key}

    def fake_connect(db_cfg):
        conn = FakeConnection()
        connections[db_cfg["database"]] = conn
        return conn

    monkeypatch.setattr(module.dry_run_sql, "_load_db_config", fake_load)
    monkeypatch.setattr(module.dry_run_sql, "_connect", fake_connect)
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "base.sql").write_text(
        "DELETE FROM tbclientmap WHERE bank_acc='A1';\n"
        "INSERT INTO tbclientmap(bank_acc) VALUES('A1');\n",
        encoding="utf-8",
    )
    (case_dir / "preset.sql").write_text(
        "DELETE FROM biztable WHERE bank_acc='A1';\n"
        "INSERT INTO biztable(bank_acc) VALUES('A1');\n",
        encoding="utf-8",
    )
    config_path = case_dir / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    case_plan_path = case_dir / "case_plan.json"
    case_plan_path.write_text(
        json.dumps(
            {
                "sql_paths": {"base_sql": "base.sql", "preset_sql": "preset.sql"},
                "preset_strategy": {"base_tables": ["tbclientmap"], "biz_tables": ["biztable"]},
                "log_analysis": {
                    "requirements": {
                            "raw_sqls": ["select * from biztable where bank_acc='A1' and open_branch='0011'"]
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = module.run_precheck(case_plan_path=case_plan_path, config_path=config_path, db_key="GYYHAUTO")

    assert result["status"] == "passed"
    assert load_calls[:1] == ["GYYHAUTO"]
    assert "ui_bc" not in connections
    assert any("tbclientmap" in sql and "ui_bc.tbclientmap" not in sql for sql in connections["GYYHAUTO"].cursor_obj.executed)
    assert any("biztable" in sql for sql in connections["GYYHAUTO"].cursor_obj.executed)


def test_return_precheck_keeps_join_tables_unqualified_for_single_db_project():
    module = _load_sql_return_precheck()
    case_plan = {
        "preset_strategy": {
            "base_tables": ["tbdxfundprdbankacc"],
            "biz_tables": ["tbdxfundpayment"],
            "no_insert_tables": ["tbtainfo"],
        }
    }
    sql = (
        "select * from tbdxfundpayment a "
        "left join tbdxfundprdbankacc d on a.prd_code=d.prd_code "
        ",tbtainfo b "
        "where a.prd_code='cycleset01'"
    )

    qualified = module._qualify_cross_db_tables(sql, case_plan)

    assert "from tbdxfundpayment a" in qualified
    assert "join tbdxfundprdbankacc d" in qualified
    assert ",tbtainfo b" in qualified


def test_return_precheck_does_not_qualify_base_tables_for_single_db():
    module = _load_sql_return_precheck()
    case_plan = {
        "env": {"single_db_key": "GYYHAUTO"},
        "preset_strategy": {
            "base_tables": ["tbclientmap"],
            "biz_tables": ["biztable"],
        },
    }
    sql = "select * from biztable b join tbclientmap c on b.bank_acc=c.bank_acc"

    qualified = module._qualify_cross_db_tables(sql, case_plan)

    assert qualified == sql


def test_execute_preset_dml_oracle_skips_mysql_session_commands(tmp_path):
    module = _load_sql_return_precheck()
    sql_file = tmp_path / "preset.sql"
    sql_file.write_text(
        "DELETE FROM biztable WHERE id='1';\nINSERT INTO biztable(id) VALUES('1');\n",
        encoding="utf-8",
    )
    executed = []

    class Cursor:
        def execute(self, sql):
            executed.append(sql)

    module._execute_preset_dml(Cursor(), [sql_file], db_type="oracle")

    assert executed == [
        "DELETE FROM biztable WHERE id='1'",
        "INSERT INTO biztable(id) VALUES('1')",
    ]


def test_candidate_selects_skip_system_table_queries_before_limiting():
    module = _load_sql_return_precheck()
    case_plan = {
        "log_analysis": {
            "requirements": {
                "raw_sqls": [
                    "select * from tbsequence where hs_key=? 当前参数:log_no",
                    "select * from tsys_menu where sub_trans_code=? 当前参数:FUNC",
                    "select * from tsys_user where user_id=? 当前参数:u1",
                    "select * from biztable where bank_acc=? 当前参数:A1",
                ]
            }
        }
    }

    selects = module._candidate_selects(case_plan)

    assert selects == ["select * from biztable where bank_acc='A1'"]


def test_candidate_selects_prioritize_precise_business_permission_query():
    module = _load_sql_return_precheck()
    case_plan = {
        "preset_strategy": {"biz_tables": ["tbdxtrustfrozen", "tbdxtrusteleccontract"]},
        "log_analysis": {
            "requirements": {
                "raw_sqls": [
                    "select a.* from tbdxtrustfrozen a where a.frozen_branch in ('0011') "
                    "and a.bank_acc='A1' and a.ta_code='HT' and a.prd_code='P1'",
                    "select count(*) num from tbdxtrusteleccontract a where a.branch_no in ('0011') "
                    "and a.in_client_no='C1' and a.bank_acc='A1'",
                    "select a.* from tbdxtrustfrozen a where a.frozen_branch in ('0011') "
                    "and a.bank_acc='A1' and a.ta_code='HT' and a.trans_code='100500' and a.prd_code='P1' "
                    "and a.frozen_date >= 20250610 and a.frozen_date <= 20260610 "
                    "and a.frozen_cause='0' and a.cfm_date >= 20250610 and a.cfm_date <= 20260610 "
                    "and a.frozen_status in('0')",
                ]
            }
        },
    }

    selects = module._candidate_selects(case_plan)

    assert selects[0].startswith("select a.* from tbdxtrustfrozen")
    assert "trans_code='100500'" in selects[0]
    assert "frozen_date >= 20250610" in selects[0]


def test_count_sql_keeps_backend_count_query_value():
    module = _load_sql_return_precheck()

    count_sql, skip_reason = module._count_sql("select count(*) num from biztable where bank_acc='A1'")

    assert skip_reason == ""
    assert count_sql == "select count(*) num from biztable where bank_acc='A1'"


def test_count_sql_replaces_duplicate_projection_with_constant():
    module = _load_sql_return_precheck()

    count_sql, skip_reason = module._count_sql(
        "select a.*,c.client_name,b.client_name "
        "from tbhisassetacc a,tbclient c,tbclientseller b "
        "where a.in_client_no=c.in_client_no and a.in_client_no=b.in_client_no"
    )

    assert skip_reason == ""
    assert count_sql.startswith("SELECT COUNT(*) AS cnt FROM (SELECT 1 FROM")
    assert "a.*,c.client_name" not in count_sql


def test_count_sql_preserves_derived_table_select_with_order_by():
    module = _load_sql_return_precheck()

    raw_sql = (
        "select * from (select prd_code,prd_name from tbdxtrustproduct "
        "where prd_code like '%UI_HT_P001%' order by prd_code asc) a"
    )

    count_sql, skip_reason = module._count_sql(raw_sql)

    assert skip_reason == ""
    assert count_sql == f"SELECT COUNT(*) AS cnt FROM ({raw_sql}) hui_return_precheck_sub"


def test_count_sql_skips_named_parameter_placeholders():
    module = _load_sql_return_precheck()

    count_sql, skip_reason = module._count_sql(
        "select * from tbparam where ta_code = @taCode and param_id = @paramId"
    )

    assert count_sql == ""
    assert skip_reason == "named_placeholder_sql"


def test_permission_precheck_uses_single_db_for_base_only_permission_sql(tmp_path, monkeypatch):
    module = _load_sql_return_precheck()
    connections = {}

    def fake_load(_config_path, db_key, _env_override=None):
        return {"database": db_key}

    def fake_connect(db_cfg):
        conn = FakeConnection()
        connections[db_cfg["database"]] = conn
        return conn

    monkeypatch.setattr(module.dry_run_sql, "_load_db_config", fake_load)
    monkeypatch.setattr(module.dry_run_sql, "_connect", fake_connect)
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "base.sql").write_text(
        "DELETE FROM tbhisassetacc WHERE bank_acc='A1';\n"
        "INSERT INTO tbhisassetacc(bank_acc,open_branch) VALUES('A1','0011');\n",
        encoding="utf-8",
    )
    config_path = case_dir / "config.json"
    config_path.write_text("{}", encoding="utf-8")
    case_plan_path = case_dir / "case_plan.json"
    case_plan_path.write_text(
        json.dumps(
            {
                "presets": {"branch_no": "0011", "hq_branch_no": "100"},
                "expected_counts": {"branch": 1, "headquarter": 2},
                "preset_strategy": {"base_tables": ["tbhisassetacc"], "biz_tables": []},
                "sql_paths": {"base_sql": "base.sql"},
                "log_analysis": {
                    "requirements": {
                        "raw_sqls": ["select * from tbhisassetacc where bank_acc='A1' and open_branch in ('0011')"]
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = module.run_precheck(case_plan_path=case_plan_path, config_path=config_path, db_key="GYYHAUTO")

    assert result["permission_checks"]["branch"]["status"] == "passed"
    assert "ui_bc" not in connections
    assert any(
        "tbhisassetacc" in sql
        and "ui_bc.tbhisassetacc" not in sql
        and "open_branch in ('0011')" in sql
        for sql in connections["GYYHAUTO"].cursor_obj.executed
    )
