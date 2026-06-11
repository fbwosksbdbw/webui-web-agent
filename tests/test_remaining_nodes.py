import json
import os
import sys
from pathlib import Path

import pytest

from hui_agent.nodes.skill_steps import (
    _case_generation_order,
    _collect_table_schemas,
    _collapse_permission_rows,
    _query_db_snapshot_for_diagnosis,
    _pytest_attempt_summary,
    _pytest_command,
    _pytest_failed_nodeids,
    _run_pytest,
    _sql_dry_run_db_key,
    build_default_step_handlers,
)
from hui_agent.insert_plan import InsertColumn, InsertPlan
from hui_agent.state import HUITestState


def _write_script(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _state(case_dir: Path) -> HUITestState:
    return HUITestState.new("菜单A").with_paths(
        config_path=str(case_dir / "config.json"),
        case_plan_path=str(case_dir / "case_plan.json"),
        case_dir=str(case_dir),
    )


def _write_schema_cache(skill: Path, payload: dict, db_family: str = "ui_bc") -> None:
    cache_dir = skill / "config" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"{db_family}.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def test_preset_sql_dry_run_uses_routing_db_when_shard_db_is_base_db():
    case_plan = {"shard": {"db": "ui_bc", "routing_db": "ui_fund2"}}

    assert _sql_dry_run_db_key("preset_sql", case_plan) == "ui_fund2"


def test_collect_table_schemas_uses_explicit_skill_root(tmp_path, monkeypatch):
    skill = tmp_path / "installed_skill"
    _write_schema_cache(
        skill,
        {
            "biztable": {
                "required_columns": ["id"],
                "all_columns": ["id", "name"],
                "column_types": {"id": "varchar(32)", "name": "varchar(64)"},
            }
        },
    )
    (tmp_path / "other_cwd").mkdir()
    monkeypatch.chdir(tmp_path / "other_cwd")

    schemas = _collect_table_schemas(
        {"preset_strategy": {"preset_tables": ["biztable"], "base_tables": []}},
        skill_root=skill,
    )

    assert schemas["biztable"]["all_columns"] == ["id", "name"]
    assert schemas["biztable"]["required_columns"] == ["id"]


def test_sql_generate_writes_insert_plan_and_sql_files(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    case_dir.mkdir()
    sql_dir.mkdir()
    (case_dir / "config.json").write_text(
        json.dumps({"presets": {"customer_id": "A1"}}),
        encoding="utf-8",
    )
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A"},
                "case_inputs": {
                    "querymulti": {
                        "fields": [
                            {"label": "客户标识", "type": "input", "value": "A1", "reason": "stable_multi"},
                            {"label": "TA代码", "type": "single-select", "value": "JS:嘉实", "reason": "stable_multi"},
                        ],
                        "excluded": {},
                    }
                },
                "preset_strategy": {
                    "base_tables": ["tbaccreq"],
                    "biz_tables": ["tbdxtrustshare"],
                },
                "shard": {
                    "db": "ui_trust2",
                    "physical_tables": {
                        "tbaccreq": ["tbaccreq"],
                        "tbdxtrustshare": ["tbdxtrustshare16"],
                    },
                },
                "log_analysis": {
                    "requirements": {
                        "where_columns": {"tbdxtrustshare": ["custom_filter"]},
                        "raw_sqls": ["select * from tbdxtrustshare where custom_filter = 'REQ_VALUE'"],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json\n"
        f"print(json.dumps({{'base_sql': {json.dumps(str(sql_dir / 'base.sql'))}, 'preset_sql': {json.dumps(str(sql_dir / 'preset.sql'))}}}))\n",
    )
    _write_schema_cache(
        skill,
        {
            "tbaccreq": {
                "required_columns": ["bank_acc"],
                "all_columns": ["bank_acc", "ta_code"],
            },
            "tbdxtrustshare16": {
                "required_columns": ["bank_acc", "ta_code"],
                "all_columns": ["bank_acc", "ta_code", "custom_filter"],
            },
        },
        "ui_trust",
    )

    result = build_default_step_handlers(skill)["2c"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert result.completed_steps == ["2c"]
    assert len(case_plan["insert_plans"]) == 2
    requirements = {item["table"]: item for item in case_plan["sql_requirements"]}
    assert requirements["tbaccreq"]["role"] == "base"
    assert requirements["tbaccreq"]["columns"][0] == {
        "column": "bank_acc",
        "purpose": "schema_required",
        "expected_value": "A1",
        "source": "presets.customer_id",
        "allow_default": True,
        "reason": "schema.required_columns",
    }
    assert requirements["tbdxtrustshare"]["physical_tables"] == ["tbdxtrustshare16"]
    assert case_plan["sql_paths"] == {
        "base_sql": Path(os.path.relpath(sql_dir / "base.sql", start=case_dir)).as_posix(),
        "preset_sql": Path(os.path.relpath(sql_dir / "preset.sql", start=case_dir)).as_posix(),
    }
    assert "INSERT INTO tbaccreq" in (sql_dir / "base.sql").read_text(encoding="utf-8")
    preset_sql = (sql_dir / "preset.sql").read_text(encoding="utf-8")
    assert "INSERT INTO tbdxtrustshare16" in preset_sql
    assert "custom_filter" in preset_sql
    assert "'REQ_VALUE'" in preset_sql


def test_sql_generate_uses_llm_permission_decision_to_enable_branch_cases(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    case_dir.mkdir()
    sql_dir.mkdir()
    (case_dir / "config.json").write_text(
        json.dumps({"presets": {"customer_id": "A1", "branch_no": "0011", "hq_branch_no": "100"}}),
        encoding="utf-8",
    )
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "账户类请求流水查询"},
                "case_inputs": {
                    "querymulti": {
                        "fields": [{"label": "客户标识", "type": "input", "value": "A1"}],
                        "excluded": {},
                    },
                    "branch": {"fields": [], "excluded": {}},
                    "headquarter": {"fields": [], "excluded": {}},
                },
                "preset_strategy": {"base_tables": [], "biz_tables": ["biztable"]},
                "shard": {"db": "ui_fund", "physical_tables": {"biztable": ["biztable1"]}},
                "log_analysis": {
                    "requirements": {
                        "where_columns": {"biztable": ["branch_no", "bank_acc"]},
                        "raw_sqls": ["select * from biztable where bank_acc='A1' and branch_no = '0011'"],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json\n"
        f"print(json.dumps({{'base_sql': {json.dumps(str(sql_dir / 'base.sql'))}, 'preset_sql': {json.dumps(str(sql_dir / 'preset.sql'))}}}))\n",
    )
    _write_schema_cache(
        skill,
        {
            "biztable1": {
                "required_columns": ["bank_acc", "branch_no"],
                "all_columns": ["bank_acc", "branch_no"],
            }
        },
        "ui_fund",
    )

    class Advisor:
        def judge(self, case_plan):
            return {
                "has_branch_permission_filter": True,
                "confidence": 0.93,
                "reason": "branch_no appears in SQL where",
                "evidence": ["branch_no"],
                "suggested_split_columns": ["branch_no"],
            }

    result = build_default_step_handlers(skill, permission_advisor=Advisor())["2c"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    sql = (sql_dir / "preset.sql").read_text(encoding="utf-8")
    assert result.completed_steps == ["2c"]
    assert case_plan["case_count"] == 7
    assert case_plan["expected_counts"] == {"branch": 1, "headquarter": 2}
    assert case_plan["permission_decision"]["source"] == "llm"
    assert case_plan["case_inputs"]["branch"]["fields"] == case_plan["case_inputs"]["querymulti"]["fields"]
    assert case_plan["case_inputs"]["headquarter"]["fields"] == case_plan["case_inputs"]["querymulti"]["fields"]
    assert case_plan["permission_query"]["source"] == "querymulti"
    assert "('A1', '0011')" in sql
    assert "('A1', '100')" in sql


def test_sql_generate_rejects_permission_cases_when_filtered_table_has_only_one_row(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    case_dir.mkdir()
    sql_dir.mkdir()
    (case_dir / "config.json").write_text(
        json.dumps({"presets": {"account": "A1", "ta_code": "HT", "branch_no": "0011", "hq_branch_no": "100"}}),
        encoding="utf-8",
    )
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "信托代销历史账户信息查询"},
                "case_inputs": {
                    "querymulti": {"fields": [{"label": "账号", "type": "input", "value": "A1"}], "excluded": {}},
                    "branch": {"fields": [], "excluded": {}},
                    "headquarter": {"fields": [], "excluded": {}},
                },
                "preset_strategy": {"base_tables": ["support_table", "biztable"], "biz_tables": []},
                "shard": {"db": "ui_bc", "physical_tables": {"support_table": ["support_table"], "biztable": ["biztable"]}},
                "log_analysis": {
                    "requirements": {
                        "where_columns": {"biztable": ["open_branch"], "support_table": ["bank_acc"]},
                        "raw_sqls": ["select * from biztable a,support_table b where a.open_branch in ('0011') and b.bank_acc='A1'"],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json\n"
        f"print(json.dumps({{'base_sql': {json.dumps(str(sql_dir / 'base.sql'))}, 'preset_sql': {json.dumps(str(sql_dir / 'preset.sql'))}}}))\n",
    )
    _write_schema_cache(
        skill,
        {
            "support_table": {
                "required_columns": ["bank_acc", "open_branch"],
                "all_columns": ["bank_acc", "open_branch"],
            },
            "biztable": {
                "required_columns": ["bank_acc", "ta_code"],
                "all_columns": ["bank_acc", "ta_code", "open_branch"],
            },
        },
        "ui_bc",
    )

    class Advisor:
        def judge(self, case_plan):
            return {
                "has_branch_permission_filter": True,
                "confidence": 0.95,
                "reason": "biztable.open_branch is in WHERE",
                "evidence": ["biztable.open_branch"],
                "suggested_split_columns": ["open_branch"],
            }

    result = build_default_step_handlers(skill, permission_advisor=Advisor())["2c"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert result.completed_steps == ["2c"]
    assert case_plan["expected_counts"] == {}
    assert "case_count" not in case_plan
    assert case_plan["permission_decision"]["has_branch_permission_filter"] is False
    assert "permission-filtered tables do not have two-row insert plans" in case_plan["permission_decision"]["reason"]


def test_sql_generate_collapses_permission_rows_when_llm_rejects_branch_cases(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    case_dir.mkdir()
    sql_dir.mkdir()
    (case_dir / "config.json").write_text(
        json.dumps({"presets": {"customer_id": "A1", "branch_no": "0011", "hq_branch_no": "100"}}),
        encoding="utf-8",
    )
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "普通产品信息查询"},
                "case_inputs": {
                    "querymulti": {
                        "fields": [{"label": "客户标识", "type": "input", "value": "A1"}],
                        "excluded": {},
                    },
                    "branch": {"fields": [], "excluded": {}},
                    "headquarter": {"fields": [], "excluded": {}},
                },
                "preset_strategy": {"base_tables": [], "biz_tables": ["biztable"]},
                "shard": {"db": "ui_fund", "physical_tables": {"biztable": ["biztable1"]}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json\n"
        f"print(json.dumps({{'base_sql': {json.dumps(str(sql_dir / 'base.sql'))}, 'preset_sql': {json.dumps(str(sql_dir / 'preset.sql'))}}}))\n",
    )
    _write_schema_cache(
        skill,
        {
            "biztable1": {
                "required_columns": ["bank_acc", "branch_no"],
                "all_columns": ["bank_acc", "branch_no"],
            }
        },
        "ui_fund",
    )

    class Advisor:
        def judge(self, case_plan):
            return {
                "has_branch_permission_filter": False,
                "confidence": 0.86,
                "reason": "No permission filter in SQL",
                "evidence": [],
                "suggested_split_columns": [],
            }

    build_default_step_handlers(skill, permission_advisor=Advisor())["2c"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    sql = (sql_dir / "preset.sql").read_text(encoding="utf-8")
    assert "case_count" not in case_plan
    assert case_plan["expected_counts"] == {}
    assert case_plan["permission_decision"]["has_branch_permission_filter"] is False
    assert "('A1', '0011')" in sql
    assert "('A1', '100')" not in sql


def test_sql_generate_rejects_branch_cases_when_branch_is_only_projected(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    case_dir.mkdir()
    sql_dir.mkdir()
    (case_dir / "config.json").write_text(
        json.dumps({"presets": {"customer_id": "A1", "branch_no": "0011", "hq_branch_no": "100"}}),
        encoding="utf-8",
    )
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "频繁交易报表"},
                "case_inputs": {
                    "querymulti": {"fields": [{"label": "客户标识", "type": "input", "value": "A1"}], "excluded": {}},
                    "branch": {"fields": [], "excluded": {}},
                    "headquarter": {"fields": [], "excluded": {}},
                },
                "preset_strategy": {"base_tables": [], "biz_tables": ["biztable"]},
                "shard": {"db": "ui_fund", "physical_tables": {"biztable": ["biztable1"]}},
                "log_analysis": {
                    "requirements": {
                        "raw_sqls": [
                            "select a.branch_no branch_sub,b.branch_no branch_red,a.bank_acc "
                            "from biztable a,biztable b where a.bank_acc=b.bank_acc"
                        ]
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json\n"
        f"print(json.dumps({{'base_sql': {json.dumps(str(sql_dir / 'base.sql'))}, 'preset_sql': {json.dumps(str(sql_dir / 'preset.sql'))}}}))\n",
    )
    _write_schema_cache(
        skill,
        {"biztable1": {"required_columns": ["bank_acc", "branch_no"], "all_columns": ["bank_acc", "branch_no"]}},
        "ui_fund",
    )

    class Advisor:
        def judge(self, case_plan):
            return {
                "has_branch_permission_filter": True,
                "confidence": 0.9,
                "reason": "branch fields are projected",
                "evidence": ["branch_sub", "branch_red"],
                "suggested_split_columns": ["branch_no"],
            }

    build_default_step_handlers(skill, permission_advisor=Advisor())["2c"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    sql = (sql_dir / "preset.sql").read_text(encoding="utf-8")
    assert "case_count" not in case_plan
    assert case_plan["expected_counts"] == {}
    assert case_plan["permission_decision"]["has_branch_permission_filter"] is False
    assert case_plan["permission_decision"]["deterministic_has_permission_filter"] is False
    assert "('A1', '0011')" in sql
    assert "('A1', '100')" not in sql


def test_collapse_permission_rows_preserves_fund_subscribe_redeem_pair():
    rows = [
        [
            InsertColumn("bank_acc", "A1", "preset"),
            InsertColumn("branch_no", "0011", "preset.branch"),
            InsertColumn("busin_code", "120", "generated.fund_report.subscribe_busin_code"),
            InsertColumn("cfm_no", "UI_AUTO_SUB_01", "generated.fund_report.cfm_no"),
        ],
        [
            InsertColumn("bank_acc", "A1", "preset"),
            InsertColumn("branch_no", "0011", "preset.branch"),
            InsertColumn("busin_code", "124", "generated.fund_report.redeem_busin_code"),
            InsertColumn("cfm_no", "UI_AUTO_RED_01", "generated.fund_report.cfm_no"),
        ],
        [
            InsertColumn("bank_acc", "A1", "preset"),
            InsertColumn("branch_no", "100", "preset.headquarter"),
            InsertColumn("busin_code", "120", "generated.fund_report.subscribe_busin_code"),
            InsertColumn("cfm_no", "UI_AUTO_SUB_02", "generated.fund_report.cfm_no"),
        ],
        [
            InsertColumn("bank_acc", "A1", "preset"),
            InsertColumn("branch_no", "100", "preset.headquarter"),
            InsertColumn("busin_code", "124", "generated.fund_report.redeem_busin_code"),
            InsertColumn("cfm_no", "UI_AUTO_RED_02", "generated.fund_report.cfm_no"),
        ],
    ]
    plan = InsertPlan(
        table="tbdxfundtranscfm",
        physical_table="tbdxfundtranscfm13",
        role="biz",
        columns=rows[0],
        rows=rows,
        delete_where="bank_acc = 'A1'",
    )

    collapsed = _collapse_permission_rows([plan])

    assert collapsed[0].rows is not None
    assert [column.value for column in collapsed[0].rows[0] if column.name == "busin_code"] == ["120"]
    assert [column.value for column in collapsed[0].rows[1] if column.name == "busin_code"] == ["124"]
    assert len(collapsed[0].rows) == 2


def test_collapse_permission_rows_preserves_tbclientmap_bank_no_variants():
    rows = [
        [
            InsertColumn("bank_no", "000", "preset.bank_no"),
            InsertColumn("bank_acc", "A1", "preset.account"),
            InsertColumn("ta_client", "TA1", "preset.ta_client"),
            InsertColumn("in_client_no", "C1", "preset.in_client_no"),
            InsertColumn("account", "A1", "preset.account"),
        ],
        [
            InsertColumn("bank_no", " 000", "preset.bank_no"),
            InsertColumn("bank_acc", "A1", "preset.account"),
            InsertColumn("ta_client", "TA1", "preset.ta_client"),
            InsertColumn("in_client_no", "C1", "preset.in_client_no"),
            InsertColumn("account", "A1", "preset.account"),
        ],
    ]
    plan = InsertPlan(
        table="tbclientmap",
        physical_table="tbclientmap",
        role="base",
        columns=rows[0],
        rows=rows,
        delete_where="bank_acc = 'A1'",
    )

    collapsed = _collapse_permission_rows([plan])

    assert collapsed[0].rows is not None
    assert [_row[0].value for _row in collapsed[0].rows] == ["000", " 000"]


def test_case_generation_order_omits_permission_cases_without_expected_counts():
    case_plan = {
        "case_inputs": {
            "branch": {"fields": []},
            "headquarter": {"fields": []},
            "queryall": {"fields": []},
            "querysingle": {"fields": []},
            "querymulti": {"fields": []},
            "reset": {"fields": []},
            "preview": {"fields": []},
        },
        "expected_counts": {},
    }

    assert _case_generation_order(case_plan) == ["queryall", "querysingle", "querymulti", "reset", "preview"]


def test_case_generation_order_generates_preview_without_probe_evidence():
    case_plan = {
        "case_inputs": {
            "queryall": {"fields": []},
            "querysingle": {"fields": []},
            "querymulti": {"fields": []},
            "reset": {"fields": []},
            "preview": {"fields": []},
        },
    }

    assert _case_generation_order(case_plan) == ["queryall", "querysingle", "querymulti", "reset", "preview"]


def test_sql_generate_fails_instead_of_writing_empty_preset_sql_when_no_insert_plan(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    case_dir.mkdir()
    sql_dir.mkdir()
    (case_dir / "config.json").write_text(json.dumps({"presets": {"customer_id": "A1"}}), encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A"},
                "case_inputs": {
                    "querymulti": {
                        "fields": [
                            {"label": "客户标识", "type": "input", "value": "A1", "reason": "stable_multi"},
                        ],
                        "excluded": {},
                    }
                },
                "preset_strategy": {
                    "base_tables": [],
                    "biz_tables": ["biztable"],
                },
                "shard": {"db": "ui_fund", "physical_tables": {"biztable": ["biztable1"]}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json\n"
        f"print(json.dumps({{'base_sql': {json.dumps(str(sql_dir / 'base.sql'))}, 'preset_sql': {json.dumps(str(sql_dir / 'preset.sql'))}}}))\n",
    )
    _write_schema_cache(
        skill,
        {
            "biztable1": {
                "required_columns": ["must_have"],
                "all_columns": ["must_have", "other_col"],
            }
        },
        "ui_fund",
    )

    # 现在 _default_value_for_column 会为 must_have 生成默认值 ""，plan 不再被 skip
    # sql_generate 应正常完成并写入 SQL（dry_run_sql.py 不存在则跳过验证）
    result = build_default_step_handlers(skill)["2c"](_state(case_dir))
    assert result.completed_steps == ["2c"]
    assert (sql_dir / "preset.sql").exists()
    # must_have 列为空字符串默认值
    assert "INSERT INTO biztable1" in (sql_dir / "preset.sql").read_text(encoding="utf-8")


def test_sql_generate_fails_when_required_match_value_would_only_be_defaulted(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    case_dir.mkdir()
    sql_dir.mkdir()
    (case_dir / "config.json").write_text(json.dumps({"presets": {}}), encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A"},
                "case_inputs": {"querymulti": {"fields": [], "excluded": {}}},
                "preset_strategy": {"base_tables": [], "biz_tables": ["biztable"]},
                "shard": {"db": "ui_fund", "physical_tables": {"biztable": ["biztable1"]}},
                "log_analysis": {
                    "requirements": {
                        "where_columns": {"biztable": ["must_have"]},
                        "raw_sqls": ["select * from biztable where must_have = ?"],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json\n"
        f"print(json.dumps({{'base_sql': {json.dumps(str(sql_dir / 'base.sql'))}, 'preset_sql': {json.dumps(str(sql_dir / 'preset.sql'))}}}))\n",
    )
    _write_schema_cache(
        skill,
        {
            "biztable1": {
                "required_columns": ["must_have"],
                "all_columns": ["must_have"],
                "column_types": {"must_have": "varchar(32)"},
            }
        },
        "ui_fund",
    )

    with pytest.raises(Exception, match="missing_requirement_values"):
        build_default_step_handlers(skill)["2c"](_state(case_dir))
    assert not (sql_dir / "preset.sql").exists()


def test_sql_generate_invokes_llm_when_no_insert_plan_can_be_generated(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    calls_file = tmp_path / "dry_run_calls.txt"
    case_dir.mkdir()
    sql_dir.mkdir()
    preset_sql = sql_dir / "preset.sql"
    (case_dir / "config.json").write_text(json.dumps({"presets": {"customer_id": "A1"}}), encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A"},
                "case_inputs": {
                    "querymulti": {
                        "fields": [{"label": "客户标识", "type": "input", "value": "A1"}],
                        "excluded": {},
                    }
                },
                "preset_strategy": {"base_tables": [], "biz_tables": ["biztable"]},
                "shard": {"db": "ui_fund", "physical_tables": {"biztable": ["biztable1"]}},
                "repair_policy": {"max_attempts": 1},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json\n"
        f"print(json.dumps({{'base_sql': {json.dumps(str(sql_dir / 'base.sql'))}, 'preset_sql': {json.dumps(str(preset_sql))}}}))\n",
    )
    _write_script(
        skill / "scripts" / "dry_run_sql.py",
        "import json, pathlib, sys\n"
        f"calls = pathlib.Path({json.dumps(str(calls_file))})\n"
        "calls.write_text((calls.read_text() if calls.exists() else '') + '1', encoding='utf-8')\n"
        "print(json.dumps({'ok': True, 'total': 1, 'passed': 1, 'failed': 0, 'errors': []}))\n",
    )
    _write_schema_cache(
        skill,
        {
            "biztable1": {
                "required_columns": ["must_have"],
                "all_columns": ["must_have", "other_col"],
            }
        },
        "ui_fund",
    )

    class Advisor:
        def diagnose(self, evidence):
            raise AssertionError(f"LLM repair should not be called when plans are generated: {evidence}")

    events = []
    result = build_default_step_handlers(skill, event_logger=events.append, repair_advisor=Advisor())["2c"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert result.completed_steps == ["2c"]
    # _default_value_for_column 为 must_have 生成了默认值 ""，plan 正常生成，不再触发 LLM 修复
    assert "INSERT INTO biztable1" in preset_sql.read_text(encoding="utf-8")
    assert case_plan["sql_validation"]["status"] == "passed"
    assert calls_file.read_text(encoding="utf-8") == "1"


def test_sql_generate_writes_unqualified_execution_sql_but_dry_runs_qualified_diagnostic_sql(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    dry_run_capture = tmp_path / "dry_run_capture.json"
    case_dir.mkdir()
    sql_dir.mkdir()
    preset_sql = sql_dir / "preset.sql"
    (skill / "config").mkdir(parents=True)
    (skill / "config" / "table_db_mapping.json").write_text(
        json.dumps({"tbdxfundtranscfm": ["ui_fund"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (case_dir / "config.json").write_text(json.dumps({"presets": {"customer_id": "A1"}}), encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A"},
                "case_inputs": {
                    "querymulti": {
                        "fields": [{"label": "客户标识", "type": "input", "value": "A1"}],
                        "excluded": {},
                    }
                },
                "preset_strategy": {"base_tables": [], "biz_tables": ["tbdxfundtranscfm"]},
                "shard": {
                    "db": "ui_fund2",
                    "physical_tables": {"tbdxfundtranscfm": ["tbdxfundtranscfm13"]},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json\n"
        f"print(json.dumps({{'base_sql': {json.dumps(str(sql_dir / 'base.sql'))}, 'preset_sql': {json.dumps(str(preset_sql))}}}))\n",
    )
    _write_script(
        skill / "scripts" / "dry_run_sql.py",
        "import json, pathlib, sys\n"
        "file_path = pathlib.Path(sys.argv[sys.argv.index('--file') + 1])\n"
        "db = sys.argv[sys.argv.index('--db') + 1]\n"
        f"pathlib.Path({json.dumps(str(dry_run_capture))}).write_text(json.dumps({{'db': db, 'sql': file_path.read_text(encoding='utf-8'), 'file': str(file_path)}}, ensure_ascii=False), encoding='utf-8')\n"
        "print(json.dumps({'ok': True, 'total': 1, 'passed': 1, 'failed': 0, 'errors': []}))\n",
    )
    _write_schema_cache(
        skill,
        {
            "tbdxfundtranscfm13": {
                "required_columns": ["bank_acc", "cfm_no"],
                "all_columns": ["bank_acc", "cfm_no"],
            }
        },
        "ui_fund",
    )

    result = build_default_step_handlers(skill)["2c"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    execution_sql = preset_sql.read_text(encoding="utf-8")
    captured = json.loads(dry_run_capture.read_text(encoding="utf-8"))
    assert result.completed_steps == ["2c"]
    assert "INSERT INTO tbdxfundtranscfm13" in execution_sql
    assert "ui_fund2.tbdxfundtranscfm13" not in execution_sql
    assert captured["db"] == "ui_fund2"
    assert "ui_fund2.tbdxfundtranscfm13" in captured["sql"]
    assert case_plan["sql_diagnostics"]["dry_run"]["preset_sql"].endswith("preset.sql")


def test_sql_generate_refreshes_missing_schema_cache_before_building_insert_plan(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    calls = tmp_path / "schema_refresh_args.json"
    case_dir.mkdir()
    sql_dir.mkdir()
    (case_dir / "config.json").write_text(
        json.dumps(
            {
                "env": {
                    "project_dir": "F:/python_project",
                    "env_key": "ifm60-dxfund",
                },
                "presets": {"customer_id": "A1"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A"},
                "case_inputs": {
                    "querymulti": {
                        "fields": [{"label": "客户标识", "type": "input", "value": "A1"}],
                        "excluded": {},
                    }
                },
                "preset_strategy": {
                    "base_tables": [],
                    "biz_tables": ["biztable"],
                },
                "shard": {"db": "ui_bc", "physical_tables": {"biztable": ["biztable1"]}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json\n"
        f"print(json.dumps({{'base_sql': {json.dumps(str(sql_dir / 'base.sql'))}, 'preset_sql': {json.dumps(str(sql_dir / 'preset.sql'))}}}))\n",
    )
    _write_script(
        skill / "scripts" / "build_schema_cache.py",
        "import json, pathlib, sys\n"
        f"pathlib.Path({json.dumps(str(calls))}).write_text(json.dumps(sys.argv[1:], ensure_ascii=False), encoding='utf-8')\n"
        "cache_dir = pathlib.Path(__file__).resolve().parents[1] / 'config' / 'cache'\n"
        "cache_dir.mkdir(parents=True, exist_ok=True)\n"
        "payload = {'biztable1': {'required_columns': ['bank_acc'], 'all_columns': ['bank_acc']}}\n"
        "(cache_dir / 'ui_bc.json').write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')\n"
        "print('schema refreshed')\n",
    )
    (skill / "config" / "cache").mkdir(parents=True, exist_ok=True)

    result = build_default_step_handlers(skill)["2c"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    args = json.loads(calls.read_text(encoding="utf-8"))
    assert result.completed_steps == ["2c"]
    assert args == [
        "--tables",
        "biztable1",
        "--db",
        "ui_bc",
        "--project-dir",
        "F:/python_project",
        "--env",
        "ifm60-dxfund",
    ]
    assert case_plan["schema_refresh"]["status"] == "success"
    assert case_plan["schema_refresh"]["tables"] == ["biztable1"]
    assert len(case_plan["insert_plans"]) == 1
    assert "INSERT INTO biztable1" in (sql_dir / "preset.sql").read_text(encoding="utf-8")


def test_sql_generate_fails_when_any_preset_table_has_no_insert_plan(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    case_dir.mkdir()
    sql_dir.mkdir()
    (case_dir / "config.json").write_text(json.dumps({"presets": {"customer_id": "A1"}}), encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A"},
                "case_inputs": {"querymulti": {"fields": [{"label": "客户标识", "value": "A1"}], "excluded": {}}},
                "preset_strategy": {"base_tables": [], "biz_tables": ["lefttable", "righttable"]},
                "shard": {
                    "db": "ui_fund",
                    "physical_tables": {"lefttable": ["lefttable"], "righttable": ["righttable"]},
                },
                "log_analysis": {
                    "requirements": {
                        "raw_sqls": ["select * from lefttable l join righttable r on l.id=r.id where l.bank_acc='A1'"],
                        "join_tables": ["righttable"],
                        "join_key_columns": {"lefttable": ["id"], "righttable": ["id"]},
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json\n"
        f"print(json.dumps({{'base_sql': {json.dumps(str(sql_dir / 'base.sql'))}, 'preset_sql': {json.dumps(str(sql_dir / 'preset.sql'))}}}))\n",
    )
    _write_schema_cache(
        skill,
        {
            "lefttable": {
                "required_columns": ["bank_acc"],
                "all_columns": ["bank_acc", "id"],
            }
        },
        "ui_fund",
    )

    with pytest.raises(Exception, match="SQL coverage gate failed"):
        build_default_step_handlers(skill)["2c"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert case_plan["sql_coverage"]["status"] == "failed"
    assert case_plan["sql_coverage"]["missing_expected_tables"][0]["table"] == "righttable"


def test_sql_generate_fails_when_return_precheck_finds_zero_rows(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    case_dir.mkdir()
    sql_dir.mkdir()
    (case_dir / "config.json").write_text(json.dumps({"presets": {"customer_id": "A1"}}), encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A"},
                "case_inputs": {"querymulti": {"fields": [{"label": "客户标识", "value": "A1"}], "excluded": {}}},
                "preset_strategy": {"base_tables": [], "biz_tables": ["biztable"]},
                "shard": {"db": "ui_fund", "physical_tables": {"biztable": ["biztable"]}},
                "log_analysis": {"requirements": {"raw_sqls": ["select * from biztable where bank_acc='A1'"]}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json\n"
        f"print(json.dumps({{'base_sql': {json.dumps(str(sql_dir / 'base.sql'))}, 'preset_sql': {json.dumps(str(sql_dir / 'preset.sql'))}}}))\n",
    )
    _write_script(
        skill / "scripts" / "dry_run_sql.py",
        "import json\n"
        "print(json.dumps({'ok': True, 'total': 1, 'passed': 1, 'failed': 0, 'errors': []}))\n",
    )
    _write_script(
        skill / "scripts" / "sql_return_precheck.py",
        "import json\n"
        "print(json.dumps({'ok': False, 'status': 'failed', 'checks': [{'sql_index': 1, 'count': 0}], 'failure_reason': 'query returned 0 rows'}))\n"
        "raise SystemExit(1)\n",
    )
    _write_schema_cache(
        skill,
        {
            "biztable": {
                "required_columns": ["bank_acc"],
                "all_columns": ["bank_acc"],
            }
        },
        "ui_fund",
    )

    with pytest.raises(Exception, match="SQL return precheck failed"):
        build_default_step_handlers(skill)["2c"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert case_plan["sql_return_precheck"]["status"] == "failed"


def test_sql_generate_repairs_dry_run_failure_with_llm_sql_rewrite(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    calls_file = tmp_path / "dry_run_calls.txt"
    case_dir.mkdir()
    sql_dir.mkdir()
    preset_sql = sql_dir / "preset.sql"
    (case_dir / "config.json").write_text(
        json.dumps({"presets": {"customer_id": "BAD_ACC"}}),
        encoding="utf-8",
    )
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A"},
                "case_inputs": {
                    "querymulti": {
                        "fields": [{"label": "客户标识", "type": "input", "value": "BAD_ACC"}],
                        "excluded": {},
                    }
                },
                "preset_strategy": {"base_tables": [], "biz_tables": ["biztable"]},
                "shard": {"db": "ui_fund", "physical_tables": {"biztable": ["biztable1"]}},
                "repair_policy": {"max_attempts": 2},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json\n"
        f"print(json.dumps({{'base_sql': {json.dumps(str(sql_dir / 'base.sql'))}, 'preset_sql': {json.dumps(str(preset_sql))}}}))\n",
    )
    _write_script(
        skill / "scripts" / "dry_run_sql.py",
        "import json, pathlib, sys\n"
        f"calls = pathlib.Path({json.dumps(str(calls_file))})\n"
        "calls.write_text((calls.read_text() if calls.exists() else '') + '1', encoding='utf-8')\n"
        "path = pathlib.Path(sys.argv[sys.argv.index('--file') + 1])\n"
        "sql = path.read_text(encoding='utf-8')\n"
        "if 'GOOD_ACC' in sql:\n"
        "    print(json.dumps({'ok': True, 'total': 1, 'passed': 1, 'failed': 0, 'errors': []}))\n"
        "    raise SystemExit(0)\n"
        "print(json.dumps({'ok': False, 'total': 1, 'passed': 0, 'failed': 1, 'errors': [{'line': 1, 'error': 'bad account', 'stmt_preview': sql[:80]}]}))\n"
        "raise SystemExit(1)\n",
    )
    _write_schema_cache(
        skill,
        {
            "biztable1": {
                "required_columns": ["bank_acc"],
                "all_columns": ["bank_acc"],
            }
        },
        "ui_fund",
    )

    class Advisor:
        def diagnose(self, evidence):
            assert evidence["trigger"] == "sql_dry_run_failed"
            assert evidence["sql_validation"]["status"] == "failed"
            return {
                "failure_type": "preset_sql_execution_failed",
                "confidence": 0.91,
                "summary": "rewrite invalid preset SQL",
                "actions": [
                    {
                        "type": "regenerate_sql",
                        "file": str(preset_sql),
                        "reason": "dry-run says BAD_ACC is invalid",
                        "content": "DELETE FROM biztable1 WHERE bank_acc = 'GOOD_ACC';\n"
                        "INSERT INTO biztable1 (bank_acc) VALUES\n"
                        "  ('GOOD_ACC');\n"
                        "COMMIT;\n",
                    }
                ],
            }

    events = []
    result = build_default_step_handlers(skill, event_logger=events.append, repair_advisor=Advisor())["2c"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert result.completed_steps == ["2c"]
    assert "GOOD_ACC" in preset_sql.read_text(encoding="utf-8")
    assert len(calls_file.read_text(encoding="utf-8")) == 2
    assert case_plan["sql_validation"]["status"] == "passed"
    assert case_plan["sql_repair_attempts"][0]["status"] == "patched"
    assert case_plan["sql_repair_attempts"][0]["actions"][0]["type"] == "llm_regenerate_sql"
    assert "sql_dry_run_finished" in [event["event"] for event in events]


def test_plan_verify_marks_complete_for_required_sections(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A", "code": "FUNC_A"},
                "case_inputs": {"queryall": {"fields": [], "excluded": {}}},
                "preset_strategy": {"base_tables": [], "biz_tables": []},
                "shard": {"db": "ui_bc"},
                "sql_paths": {"base_sql": "base.sql", "preset_sql": "preset.sql"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = build_default_step_handlers(skill)["2d"](_state(case_dir))

    assert result.completed_steps == ["2d"]


def test_case_generate_writes_pytest_files_from_case_inputs(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "config.json").write_text(
        json.dumps({"env": {"owner": "owner1", "env_key": "ifm60-dxfund"}}),
        encoding="utf-8",
    )
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A", "code": "FUNC_A", "level1": "一级", "level2": "二级", "url": "/m"},
                "case_order": ["querysingle"],
                "case_inputs": {
                    "querysingle": {
                        "fields": [
                            {"label": "客户标识", "type": "input", "value": "A1", "reason": "single_filter"}
                        ],
                        "excluded": {},
                    }
                },
                "paths": {"test_case_dir": str(tmp_path / "tests_out")},
                "shard": {"db": "ui_bc"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = build_default_step_handlers(skill)["3"](_state(case_dir))

    generated = list((tmp_path / "tests_out").rglob("test_FUNC_A_querysingle.py"))
    assert result.completed_steps == ["3"]
    assert len(generated) == 1
    generated_code = generated[0].read_text(encoding="utf-8")
    assert '_fill_input_by_label(page, "客户标识", "A1")' in generated_code
    assert 'DB_ENV_NAME = "ifm60-dxfund"' in generated_code
    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert case_plan["env_key"] == "ifm60-dxfund"


def test_case_generate_reconciles_case_inputs_from_final_insert_plan_values(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A", "code": "FUNC_A", "level1": "一级", "level2": "二级", "url": "/m"},
                "case_order": ["querymulti"],
                "case_inputs": {
                    "querymulti": {
                        "fields": [
                            {"label": "客户标识", "type": "input", "value": "OLD_ACC", "reason": "stable_multi"},
                            {"label": "产品代码", "type": "single-select", "value": "OLD_PRD:旧产品", "reason": "stable_multi"},
                            {"label": "TA代码", "type": "single-select", "value": "OLDTA:旧TA", "reason": "stable_multi"},
                        ],
                        "excluded": {},
                    }
                },
                "insert_plans": [
                    {
                        "table": "biztable",
                        "physical_table": "biztable",
                        "role": "business",
                        "columns": [
                            {"name": "bank_acc", "value": "A33060210021113", "source": "sql"},
                            {"name": "prd_code", "value": "UI_HT_P001", "source": "sql"},
                            {"name": "ta_code", "value": "HT", "source": "sql"},
                        ],
                        "delete_where": "bank_acc = 'A33060210021113'",
                    }
                ],
                "paths": {"test_case_dir": str(tmp_path / "tests_out")},
                "shard": {"db": "ui_bc"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = build_default_step_handlers(skill)["3"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    generated = list((tmp_path / "tests_out").rglob("test_FUNC_A_querymulti.py"))
    code = generated[0].read_text(encoding="utf-8")
    fields = {field["label"]: field["value"] for field in case_plan["case_inputs"]["querymulti"]["fields"]}
    assert result.completed_steps == ["3"]
    assert fields["客户标识"] == "A33060210021113"
    assert fields["产品代码"] == "UI_HT_P001"
    assert fields["TA代码"] == "HT"
    assert '_fill_input_by_label(page, "客户标识", "A33060210021113")' in code
    assert '_select_dropdown_by_label(page, "产品代码", "UI_HT_P001")' in code
    assert '_select_dropdown_by_label(page, "TA代码", "HT")' in code


def test_case_generate_reconciles_case_inputs_from_repaired_sql_file(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    case_dir.mkdir()
    sql_dir.mkdir()
    preset_sql = sql_dir / "preset.sql"
    preset_sql.write_text(
        "DELETE FROM biztable WHERE bank_acc = 'GOOD_ACC';\n"
        "INSERT INTO biztable (bank_acc, prd_code, ta_code) VALUES\n"
        "  ('GOOD_ACC', 'GOOD_PRD', 'GOOD_TA');\n",
        encoding="utf-8",
    )
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A", "code": "FUNC_A", "level1": "一级", "level2": "二级", "url": "/m"},
                "case_order": ["querymulti"],
                "case_inputs": {
                    "querymulti": {
                        "fields": [
                            {"label": "客户标识", "type": "input", "value": "BAD_ACC"},
                            {"label": "产品代码", "type": "single-select", "value": "BAD_PRD"},
                            {"label": "TA代码", "type": "single-select", "value": "BAD_TA"},
                        ],
                        "excluded": {},
                    }
                },
                "insert_plans": [
                    {
                        "table": "biztable",
                        "physical_table": "biztable",
                        "role": "business",
                        "columns": [
                            {"name": "bank_acc", "value": "OLD_ACC", "source": "stale"},
                            {"name": "prd_code", "value": "OLD_PRD", "source": "stale"},
                            {"name": "ta_code", "value": "OLD_TA", "source": "stale"},
                        ],
                        "delete_where": "bank_acc = 'OLD_ACC'",
                    }
                ],
                "sql_paths": {"preset_sql": str(preset_sql)},
                "paths": {"test_case_dir": str(tmp_path / "tests_out")},
                "shard": {"db": "ui_bc"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    build_default_step_handlers(skill)["3"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    fields = {field["label"]: field["value"] for field in case_plan["case_inputs"]["querymulti"]["fields"]}
    assert fields == {"客户标识": "GOOD_ACC", "产品代码": "GOOD_PRD", "TA代码": "GOOD_TA"}


def test_case_generate_deploys_common_helpers_to_project_dir(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    project_dir = tmp_path / "project"
    case_dir.mkdir()
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A", "code": "FUNC_A", "level1": "一级", "level2": "二级", "url": "/m"},
                "case_order": ["querysingle"],
                "case_inputs": {
                    "querysingle": {
                        "fields": [
                            {"label": "产品代码", "type": "single-select", "value": "UI_HT_P001:UI自动化HT信托产品"}
                        ],
                        "excluded": {},
                    }
                },
                "paths": {
                    "project_dir": str(project_dir),
                    "test_case_dir": str(project_dir / "tests_out"),
                },
                "shard": {"db": "ui_bc"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_script(
        skill / "scripts" / "deploy_helpers.py",
        "import pathlib, sys\n"
        "project = pathlib.Path(sys.argv[sys.argv.index('--project-dir') + 1])\n"
        "(project / 'common').mkdir(parents=True, exist_ok=True)\n"
        "(project / 'common' / 'helpers.py').write_text('# deployed helpers', encoding='utf-8')\n"
        "print('helpers deployed')\n",
    )

    result = build_default_step_handlers(skill)["3"](_state(case_dir))

    assert result.completed_steps == ["3"]
    assert (project_dir / "common" / "helpers.py").read_text(encoding="utf-8") == "# deployed helpers"


def test_case_generate_blocks_when_llm_template_review_finds_blocker(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A", "code": "FUNC_A", "level1": "一级", "level2": "二级", "url": "/m"},
                "case_order": ["reset"],
                "case_inputs": {
                    "reset": {
                        "fields": [{"label": "产品代码", "type": "single-select", "value": "P1:产品1"}],
                        "excluded": {},
                    }
                },
                "paths": {"test_case_dir": str(tmp_path / "tests_out")},
                "shard": {"db": "ui_bc"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class Reviewer:
        def review(self, payload):
            return {
                "status": "failed",
                "block_generation": True,
                "summary": "reset assertion is weak",
                "issues": [
                    {
                        "case_type": "reset",
                        "severity": "blocker",
                        "message": "reset does not assert actual control value",
                        "suggested_action": "regenerate_case",
                    }
                ],
            }

    try:
        build_default_step_handlers(skill, template_reviewer=Reviewer())["3"](_state(case_dir))
    except Exception as exc:
        message = str(exc)
    else:
        raise AssertionError("case_generate should fail on blocking template review")

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert "template review blocked generation" in message
    assert case_plan["template_review"]["status"] == "failed"
    assert case_plan["template_review"]["issues"][0]["case_type"] == "reset"


def test_verify_runs_generated_pytest_and_records_results(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    test_file = tmp_path / "test_ok.py"
    case_dir.mkdir()
    test_file.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps({"generated_tests": [str(test_file)]}, ensure_ascii=False),
        encoding="utf-8",
    )

    result = build_default_step_handlers(skill)["4"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert result.completed_steps == ["4"]
    assert case_plan["verification"]["status"] == "passed"


def test_verify_skips_preview_when_only_preview_has_no_detail_button(tmp_path, monkeypatch):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    queryall = tmp_path / "test_FUNC_queryall.py"
    preview = tmp_path / "test_FUNC_preview.py"
    case_dir.mkdir()
    queryall.write_text("def test_FUNC_queryall():\n    assert True\n", encoding="utf-8")
    preview.write_text("def test_FUNC_preview():\n    assert True\n", encoding="utf-8")
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps({"generated_tests": [str(queryall), str(preview)]}, ensure_ascii=False),
        encoding="utf-8",
    )

    class Completed:
        returncode = 1
        stdout = (
            f"{queryall}::test_FUNC_queryall PASSED [ 50%]\n"
            f"{preview}::test_FUNC_preview FAILED [100%]\n"
            "FAILED "
            f"{preview}::test_FUNC_preview - AssertionError: 未找到详情/预览/查看按钮\n"
        )
        stderr = ""

    monkeypatch.setattr("hui_agent.nodes.skill_steps.subprocess.run", lambda *args, **kwargs: Completed())

    class Advisor:
        def diagnose(self, evidence):
            raise AssertionError("preview-only missing button should not call repair advisor")

    result = build_default_step_handlers(skill, repair_advisor=Advisor())["4"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert result.completed_steps == ["4"]
    assert case_plan["verification"]["status"] == "passed"
    assert case_plan["preview_verification"]["status"] == "skipped"


def test_verify_skips_preview_after_target_rerun_when_environment_error_clears(tmp_path, monkeypatch):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    queryall = tmp_path / "test_FUNC_queryall.py"
    preview = tmp_path / "test_FUNC_preview.py"
    case_dir.mkdir()
    queryall.write_text("def test_FUNC_queryall():\n    assert True\n", encoding="utf-8")
    preview.write_text("def test_FUNC_preview():\n    assert True\n", encoding="utf-8")
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "generated_tests": [str(queryall), str(preview)],
                "repair_policy": {"max_attempts": 1},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []

    class Completed:
        def __init__(self, stdout: str):
            self.returncode = 1
            self.stdout = stdout
            self.stderr = ""

    class Advisor:
        def diagnose(self, evidence):
            return {"failure_type": "page_or_environment_error", "confidence": 0.8, "summary": "retry target", "actions": []}

    def fake_run(command, *args, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            return Completed(
                f"""
ERROR {queryall}::test_FUNC_queryall - playwright._impl._errors.TimeoutError: Page.goto: Timeout 30000ms exceeded.
FAILED {preview}::test_FUNC_preview - AssertionError: 未找到详情/预览/查看按钮
"""
            )
        return Completed(
            f"""
PASSED {queryall}::test_FUNC_queryall
FAILED {preview}::test_FUNC_preview - AssertionError: 未找到详情/预览/查看按钮
"""
        )

    monkeypatch.setattr("hui_agent.nodes.skill_steps.subprocess.run", fake_run)

    result = build_default_step_handlers(skill, repair_advisor=Advisor())["4"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert result.completed_steps == ["4"]
    assert len(calls) == 2
    assert case_plan["verification"]["status"] == "passed"
    assert case_plan["preview_verification"]["status"] == "skipped"


def test_verify_does_not_skip_preview_when_button_exists_but_detail_assertion_fails(tmp_path, monkeypatch):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    queryall = tmp_path / "test_FUNC_queryall.py"
    preview = tmp_path / "test_FUNC_preview.py"
    case_dir.mkdir()
    queryall.write_text("def test_FUNC_queryall():\n    assert True\n", encoding="utf-8")
    preview.write_text("def test_FUNC_preview():\n    assert True\n", encoding="utf-8")
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "generated_tests": [str(queryall), str(preview)],
                "repair_policy": {"max_attempts": 1},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class Completed:
        returncode = 1
        stdout = (
            f"{queryall}::test_FUNC_queryall PASSED [ 50%]\n"
            f"{preview}::test_FUNC_preview FAILED [100%]\n"
            "FAILED "
            f"{preview}::test_FUNC_preview - AssertionError: 详情面板未展开或详情内容为空\n"
        )
        stderr = ""

    monkeypatch.setattr("hui_agent.nodes.skill_steps.subprocess.run", lambda *args, **kwargs: Completed())
    calls = []

    class Advisor:
        def diagnose(self, evidence):
            calls.append(evidence)
            return {
                "failure_type": "preview_detail_assertion_failed",
                "confidence": 0.9,
                "summary": "预览按钮存在，但详情面板断言失败，应修复点击/等待/详情读取逻辑",
                "actions": [{"type": "mark_unfixable", "reason": "test-only"}],
            }

    result = build_default_step_handlers(skill, repair_advisor=Advisor())["4"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert result.completed_steps == ["4"]
    assert "preview_verification" not in case_plan
    assert any(str(item).endswith("test_FUNC_preview.py") for item in case_plan["generated_tests"])
    assert case_plan["verification"]["status"] == "failed"
    assert calls


def test_query_db_snapshot_for_diagnosis_collects_base_and_preset_sql(tmp_path):
    skill = tmp_path / "skill"
    scripts = skill / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "query_db_snapshot.py").write_text("placeholder", encoding="utf-8")
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    config = case_dir / "config.json"
    config.write_text(json.dumps({"env": {"env_key": "env1"}}, ensure_ascii=False), encoding="utf-8")
    base_sql = case_dir / "base.sql"
    preset_sql = case_dir / "preset.sql"
    base_sql.write_text("INSERT INTO base_table(id) VALUES('1');", encoding="utf-8")
    preset_sql.write_text("INSERT INTO biz_table(id) VALUES('1');", encoding="utf-8")
    case_plan = {
        "sql_paths": {"base_sql": "base.sql", "preset_sql": "preset.sql"},
        "shard": {"db": "ui_biz"},
    }

    class Runner:
        scripts_dir = scripts

        def __init__(self):
            self.calls = []

        def run_text(self, script, args, timeout=None):
            self.calls.append((script, list(args), timeout))
            sql_file = Path(args[args.index("--sql-file") + 1])
            return json.dumps({"db": args[args.index("--db") + 1], "tables": [{"table": sql_file.stem}]})

    runner = Runner()

    snapshot = _query_db_snapshot_for_diagnosis(case_plan, case_dir, str(config), runner)

    assert set(snapshot["snapshots"]) == {"base_sql", "preset_sql"}
    assert snapshot["snapshots"]["base_sql"]["db"] == "ui_bc"
    assert snapshot["snapshots"]["preset_sql"]["db"] == "ui_biz"
    assert [call[1][call[1].index("--sql-file") + 1] for call in runner.calls] == [
        str(base_sql),
        str(preset_sql),
    ]


def test_verify_repairs_preset_sql_mismatch_and_retries_pytest(tmp_path, monkeypatch):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    test_file = tmp_path / "test_generated.py"
    case_dir.mkdir()
    sql_dir.mkdir()
    test_file.write_text("def test_generated():\n    assert True\n", encoding="utf-8")
    preset_sql = sql_dir / "FUNC_A.sql"
    preset_sql.write_text(
        "DELETE FROM tbdxtrustproduct WHERE prd_code = '11';\n"
        "INSERT INTO tbdxtrustproduct (prd_code, ta_code, prd_name) VALUES ('11', 'JS', 'AI自动化测试产品');\n",
        encoding="utf-8",
    )
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "generated_tests": [str(test_file)],
                "sql_paths": {"preset_sql": str(preset_sql), "base_sql": str(sql_dir / "FUNC_A_base.sql")},
                "presets": {
                    "prd_code": "UI_HT_P001:UI自动化HT信托产品",
                    "ta_code": "HT:HT基金",
                    "prd_name": "UI自动化HT信托产品",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []

    class Completed:
        def __init__(self, returncode: int):
            self.returncode = returncode
            self.stdout = "failed first" if returncode else "passed after repair"
            self.stderr = ""

    def fake_run(*args, **kwargs):
        calls.append(args)
        return Completed(1 if len(calls) == 1 else 0)

    monkeypatch.setattr("hui_agent.nodes.skill_steps.subprocess.run", fake_run)

    result = build_default_step_handlers(skill)["4"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    repaired_sql = preset_sql.read_text(encoding="utf-8")
    assert result.completed_steps == ["4"]
    assert len(calls) == 2
    assert case_plan["verification"]["status"] == "passed"
    assert case_plan["repair_attempts"][0]["actions"][0]["type"] == "patch_sql"
    assert "UI_HT_P001" in repaired_sql
    assert "HT" in repaired_sql
    assert "UI自动化HT信托产品" in repaired_sql
    assert "'11'" not in repaired_sql


def test_verify_reruns_failed_cases_before_full_regression(tmp_path, monkeypatch):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    bad_test = tmp_path / "test_bad.py"
    ok_test = tmp_path / "test_ok.py"
    case_dir.mkdir()
    bad_test.write_text("def test_bad():\n    assert False\n", encoding="utf-8")
    ok_test.write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "generated_tests": [str(bad_test), str(ok_test)],
                "repair_policy": {"max_attempts": 2},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []

    class Completed:
        def __init__(self, returncode: int, stdout: str):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    class Advisor:
        def diagnose(self, evidence):
            return {"failure_type": "assertion_mismatch", "confidence": 0.8, "summary": "fix bad", "actions": []}

    def fake_run(command, *args, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            return Completed(
                1,
                f"""
=========================== short test summary info ===========================
FAILED {bad_test}::test_bad - AssertionError
PASSED {ok_test}::test_ok
============= 1 failed, 1 passed in 1.00s =============
""",
            )
        return Completed(0, "1 passed in 0.10s")

    monkeypatch.setattr("hui_agent.nodes.skill_steps.subprocess.run", fake_run)

    result = build_default_step_handlers(skill, repair_advisor=Advisor())["4"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert result.completed_steps == ["4"]
    assert len(calls) == 3
    assert str(bad_test) in calls[0] and str(ok_test) in calls[0]
    assert calls[1] == [sys.executable, "-m", "pytest", f"{bad_test}::test_bad", "-q"]
    assert str(bad_test) in calls[2] and str(ok_test) in calls[2]
    phases = [attempt["phase"] for attempt in case_plan["verification_attempts"]]
    assert phases == ["full", "target", "regression"]
    assert case_plan["verification"]["status"] == "passed"
    assert case_plan["verification"]["phase"] == "regression"


def test_verify_targets_error_nodeids_and_accepts_environment_flake_after_all_cases_pass_once(tmp_path, monkeypatch):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    queryall = tmp_path / "test_FUNC_queryall.py"
    querymulti = tmp_path / "test_FUNC_querymulti.py"
    case_dir.mkdir()
    queryall.write_text("def test_FUNC_queryall():\n    assert True\n", encoding="utf-8")
    querymulti.write_text("def test_FUNC_querymulti():\n    assert True\n", encoding="utf-8")
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "generated_tests": [str(queryall), str(querymulti)],
                "repair_policy": {"max_attempts": 2},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []

    class Completed:
        def __init__(self, returncode: int, stdout: str):
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ""

    class Advisor:
        def diagnose(self, evidence):
            return {"failure_type": "page_or_environment_error", "confidence": 0.9, "summary": "login timeout", "actions": []}

    def fake_run(command, *args, **kwargs):
        calls.append(command)
        if len(calls) == 1:
            return Completed(
                1,
                f"""
=========================== short test summary info ===========================
PASSED {queryall}::test_FUNC_queryall
ERROR {querymulti}::test_FUNC_querymulti - playwright._impl._errors.TimeoutError: Page.goto: Timeout 30000ms exceeded.
============= 1 passed, 1 error in 1.00s =============
""",
            )
        if len(calls) == 2:
            return Completed(
                0,
                f"""
=========================== short test summary info ===========================
PASSED {querymulti}::test_FUNC_querymulti
============= 1 passed in 0.50s =============
""",
            )
        return Completed(
            1,
            f"""
=========================== short test summary info ===========================
PASSED {querymulti}::test_FUNC_querymulti
ERROR {queryall}::test_FUNC_queryall - playwright._impl._errors.TimeoutError: Page.goto: Timeout 30000ms exceeded.
Call log:
  - navigating to "http://host/console/#/login", waiting until "load"
============= 1 passed, 1 error in 1.00s =============
""",
        )

    monkeypatch.setattr("hui_agent.nodes.skill_steps.subprocess.run", fake_run)

    result = build_default_step_handlers(skill, repair_advisor=Advisor())["4"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert result.completed_steps == ["4"]
    assert calls[1] == [sys.executable, "-m", "pytest", f"{querymulti}::test_FUNC_querymulti", "-q"]
    assert case_plan["verification"]["status"] == "passed"
    assert case_plan["verification"]["qualification"] == "all_cases_passed_across_attempts"


def test_verify_circuit_breaks_after_three_failed_repair_attempts(tmp_path, monkeypatch):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    test_file = tmp_path / "test_generated.py"
    case_dir.mkdir()
    test_file.write_text("def test_generated():\n    assert False\n", encoding="utf-8")
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "generated_tests": [str(test_file)],
                "sql_paths": {},
                "presets": {"prd_code": "UI_HT_P001:UI自动化HT信托产品"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []

    class Completed:
        returncode = 1
        stdout = "always failing"
        stderr = ""

    def fake_run(*args, **kwargs):
        calls.append(args)
        return Completed()

    monkeypatch.setattr("hui_agent.nodes.skill_steps.subprocess.run", fake_run)

    verify_state = build_default_step_handlers(skill)["4"](_state(case_dir))
    report_state = build_default_step_handlers(skill)["5"](verify_state)

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    report = (case_dir / "reports" / "summary.md").read_text(encoding="utf-8")
    assert verify_state.completed_steps == ["4"]
    assert report_state.status == "failed"
    assert report_state.failure_reason == "pytest verification failed after repair attempts"
    assert len(calls) == 4
    assert len(case_plan["repair_attempts"]) == 3
    assert case_plan["repair_attempts"][-1]["status"] == "no_action"
    assert case_plan["verification"]["status"] == "failed"
    assert case_plan["verification"]["attempt"] == 3
    assert "- 验证状态: failed" in report


def test_verify_records_llm_repair_decision_when_advisor_is_available(tmp_path, monkeypatch):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    test_file = tmp_path / "test_generated.py"
    case_dir.mkdir()
    test_file.write_text("def test_generated():\n    assert False\n", encoding="utf-8")
    (case_dir / "query_log.txt").write_text("当前SQL: select * from t where prd_code=?", encoding="utf-8")
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "generated_tests": [str(test_file)],
                "repair_policy": {"max_attempts": 1},
                "presets": {"prd_code": "UI_HT_P001:UI自动化HT信托产品"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    seen = {}
    events = []

    class Completed:
        returncode = 1
        stdout = "pytest failed"
        stderr = ""

    class Advisor:
        def diagnose(self, evidence):
            seen["evidence"] = evidence
            return {
                "failure_type": "preset_sql_mismatch",
                "confidence": 0.82,
                "summary": "SQL data does not match query preset",
                "actions": [],
            }

    monkeypatch.setattr("hui_agent.nodes.skill_steps.subprocess.run", lambda *args, **kwargs: Completed())

    build_default_step_handlers(skill, event_logger=events.append, repair_advisor=Advisor())["4"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    attempt = case_plan["repair_attempts"][0]
    assert seen["evidence"]["query_log_tail"].startswith("当前SQL")
    assert seen["evidence"]["presets"]["prd_code"] == "UI_HT_P001:UI自动化HT信托产品"
    assert attempt["llm_decision"]["failure_type"] == "preset_sql_mismatch"
    assert attempt["llm_decision"]["confidence"] == 0.82
    event_names = [event["event"] for event in events]
    assert "pytest_finished" in event_names
    assert "llm_repair_started" in event_names
    assert "llm_repair_finished" in event_names
    assert "repair_attempt_recorded" in event_names


def test_verify_applies_llm_patch_sql_action_before_retry(tmp_path, monkeypatch):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    test_file = tmp_path / "test_generated.py"
    case_dir.mkdir()
    sql_dir.mkdir()
    preset_sql = sql_dir / "preset.sql"
    preset_sql.write_text("INSERT INTO t(prd_code) VALUES ('11');\n", encoding="utf-8")
    test_file.write_text("def test_generated():\n    assert False\n", encoding="utf-8")
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "generated_tests": [str(test_file)],
                "sql_paths": {"preset_sql": str(preset_sql)},
                "repair_policy": {"max_attempts": 2},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []

    class Completed:
        def __init__(self, returncode: int):
            self.returncode = returncode
            self.stdout = "failed first" if returncode else "passed after llm patch"
            self.stderr = ""

    class Advisor:
        def diagnose(self, evidence):
            return {
                "failure_type": "preset_sql_mismatch",
                "confidence": 0.9,
                "summary": "replace stale product code",
                "actions": [
                    {
                        "type": "patch_sql",
                        "file": str(preset_sql),
                        "reason": "product code should match case data",
                        "changes": [{"old": "'11'", "new": "'UI_HT_P001'"}],
                    }
                ],
            }

    def fake_run(*args, **kwargs):
        calls.append(args)
        return Completed(1 if len(calls) == 1 else 0)

    monkeypatch.setattr("hui_agent.nodes.skill_steps.subprocess.run", fake_run)

    result = build_default_step_handlers(skill, repair_advisor=Advisor())["4"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert result.completed_steps == ["4"]
    assert len(calls) == 2
    assert "UI_HT_P001" in preset_sql.read_text(encoding="utf-8")
    assert case_plan["repair_attempts"][0]["actions"][0]["type"] == "llm_patch_sql"
    assert case_plan["verification"]["status"] == "passed"


def test_verify_rejects_llm_patch_sql_that_breaks_sql_syntax(tmp_path, monkeypatch):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    test_file = tmp_path / "test_generated.py"
    case_dir.mkdir()
    sql_dir.mkdir()
    preset_sql = sql_dir / "preset.sql"
    original_sql = "DELETE FROM t WHERE id = '1';\nINSERT INTO t(id) VALUES ('1');\n"
    preset_sql.write_text(original_sql, encoding="utf-8")
    test_file.write_text("def test_generated():\n    assert False\n", encoding="utf-8")
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "generated_tests": [str(test_file)],
                "sql_paths": {"preset_sql": str(preset_sql)},
                "repair_policy": {"max_attempts": 1},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class Completed:
        returncode = 1
        stdout = "failed"
        stderr = ""

    class Advisor:
        def diagnose(self, evidence):
            return {
                "failure_type": "preset_sql_execution_failed",
                "confidence": 0.9,
                "summary": "bad broad replacement",
                "actions": [
                    {
                        "type": "patch_sql",
                        "file": str(preset_sql),
                        "reason": "bad broad replacement",
                        "changes": [{"old": " ", "new": "0.000"}],
                    }
                ],
            }

    monkeypatch.setattr("hui_agent.nodes.skill_steps.subprocess.run", lambda *args, **kwargs: Completed())

    build_default_step_handlers(skill, repair_advisor=Advisor())["4"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    action = case_plan["repair_attempts"][0]["actions"][0]
    assert action["type"] == "llm_patch_sql"
    assert action["status"] == "skipped"
    assert "does not contain DML" in action["reason"]
    assert preset_sql.read_text(encoding="utf-8") == original_sql


def test_verify_allows_llm_to_regenerate_sql_without_preset_reverting(tmp_path, monkeypatch):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    test_file = tmp_path / "test_generated.py"
    case_dir.mkdir()
    sql_dir.mkdir()
    preset_sql = sql_dir / "preset.sql"
    preset_sql.write_text("INSERT INTO t(bank_acc) VALUES ('BAD_ACC');\n", encoding="utf-8")
    test_file.write_text("def test_generated():\n    assert False\n", encoding="utf-8")
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "generated_tests": [str(test_file)],
                "sql_paths": {"preset_sql": str(preset_sql)},
                "presets": {"customer_id": "BAD_ACC"},
                "repair_policy": {"max_attempts": 2},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []

    class Completed:
        def __init__(self, returncode: int):
            self.returncode = returncode
            self.stdout = "failed first" if returncode else "passed after sql rewrite"
            self.stderr = ""

    class Advisor:
        def diagnose(self, evidence):
            return {
                "failure_type": "preset_sql_execution_failed",
                "confidence": 0.9,
                "summary": "rewrite sql after pytest failure",
                "actions": [
                    {
                        "type": "regenerate_sql",
                        "file": str(preset_sql),
                        "reason": "BAD_ACC does not match database",
                        "content": "DELETE FROM t WHERE bank_acc = 'GOOD_ACC';\n"
                        "INSERT INTO t(bank_acc) VALUES ('GOOD_ACC');\n"
                        "COMMIT;\n",
                    }
                ],
            }

    def fake_run(*args, **kwargs):
        calls.append(args)
        return Completed(1 if len(calls) == 1 else 0)

    monkeypatch.setattr("hui_agent.nodes.skill_steps.subprocess.run", fake_run)

    result = build_default_step_handlers(skill, repair_advisor=Advisor())["4"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert result.completed_steps == ["4"]
    assert "GOOD_ACC" in preset_sql.read_text(encoding="utf-8")
    assert "BAD_ACC" not in preset_sql.read_text(encoding="utf-8")
    assert case_plan["repair_attempts"][0]["actions"][0]["type"] == "llm_regenerate_sql"
    assert case_plan["verification"]["status"] == "passed"


def test_verify_rejects_llm_regenerate_sql_with_mismatched_insert_values(tmp_path, monkeypatch):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    test_file = tmp_path / "test_generated.py"
    case_dir.mkdir()
    sql_dir.mkdir()
    preset_sql = sql_dir / "preset.sql"
    original_sql = "INSERT INTO t(id, name) VALUES ('1', 'old');\n"
    preset_sql.write_text(original_sql, encoding="utf-8")
    test_file.write_text("def test_generated():\n    assert False\n", encoding="utf-8")
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "generated_tests": [str(test_file)],
                "sql_paths": {"preset_sql": str(preset_sql)},
                "repair_policy": {"max_attempts": 1},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class Completed:
        returncode = 1
        stdout = "failed"
        stderr = ""

    class Advisor:
        def diagnose(self, evidence):
            return {
                "failure_type": "preset_sql_execution_failed",
                "confidence": 0.9,
                "summary": "bad regenerated sql",
                "actions": [
                    {
                        "type": "regenerate_sql",
                        "file": str(preset_sql),
                        "reason": "bad regenerated sql",
                        "content": "INSERT INTO t(id, name) VALUES ('1');\n",
                    }
                ],
            }

    monkeypatch.setattr("hui_agent.nodes.skill_steps.subprocess.run", lambda *args, **kwargs: Completed())

    build_default_step_handlers(skill, repair_advisor=Advisor())["4"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    action = case_plan["repair_attempts"][0]["actions"][0]
    assert action["type"] == "llm_regenerate_sql"
    assert action["status"] == "skipped"
    assert "value count" in action["reason"]
    assert preset_sql.read_text(encoding="utf-8") == original_sql


def test_verify_applies_llm_regenerate_case_action_before_retry(tmp_path, monkeypatch):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    test_file = tmp_path / "test_preview.py"
    case_dir.mkdir()
    test_file.write_text("def test_preview():\n    assert True\n", encoding="utf-8")
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "generated_tests": [str(test_file)],
                "repair_policy": {"max_attempts": 2},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    calls = []
    new_content = (
        "def test_preview():\n"
        "    rows = [1]\n"
        "    assert len(rows) == 1\n"
    )

    class Completed:
        def __init__(self, returncode: int):
            self.returncode = returncode
            self.stdout = "failed first" if returncode else "passed after regenerate"
            self.stderr = ""

    class Advisor:
        def diagnose(self, evidence):
            return {
                "failure_type": "assertion_mismatch",
                "confidence": 0.88,
                "summary": "preview assertion is fake",
                "actions": [
                    {
                        "type": "regenerate_case",
                        "file": str(test_file),
                        "case_type": "preview",
                        "reason": "replace fake assertion",
                        "content": new_content,
                    }
                ],
            }

    def fake_run(*args, **kwargs):
        calls.append(args)
        return Completed(1 if len(calls) == 1 else 0)

    monkeypatch.setattr("hui_agent.nodes.skill_steps.subprocess.run", fake_run)

    result = build_default_step_handlers(skill, repair_advisor=Advisor())["4"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert result.completed_steps == ["4"]
    assert len(calls) == 2
    assert test_file.read_text(encoding="utf-8") == new_content
    assert case_plan["repair_attempts"][0]["actions"][0]["type"] == "llm_regenerate_case"
    assert case_plan["verification"]["status"] == "passed"


def test_verify_repairs_duplicate_insert_keys_before_next_attempt(tmp_path, monkeypatch):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    test_file = tmp_path / "test_generated.py"
    case_dir.mkdir()
    sql_dir.mkdir()
    base_sql = sql_dir / "base.sql"
    test_file.write_text("def test_generated():\n    assert False\n", encoding="utf-8")
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "generated_tests": [str(test_file)],
                "repair_policy": {"max_attempts": 1},
                "sql_paths": {"base_sql": str(base_sql), "preset_sql": str(sql_dir / "preset.sql")},
                "insert_plans": [
                    {
                        "table": "tbvirbankaccmap",
                        "physical_table": "tbvirbankaccmap",
                        "role": "base",
                        "columns": [
                            {"name": "virtual_bank_acc", "value": "V1", "source": "test"},
                            {"name": "bank_no", "value": "000", "source": "test"},
                            {"name": "in_client_no", "value": "C1", "source": "test"},
                            {"name": "open_branch", "value": "0011", "source": "test"},
                        ],
                        "delete_where": "virtual_bank_acc = 'V1' AND bank_no = '000' AND in_client_no = 'C1'",
                        "rows": [
                            [
                                {"name": "virtual_bank_acc", "value": "V1", "source": "test"},
                                {"name": "bank_no", "value": "000", "source": "test"},
                                {"name": "in_client_no", "value": "C1", "source": "test"},
                                {"name": "open_branch", "value": "0011", "source": "test"},
                            ],
                            [
                                {"name": "virtual_bank_acc", "value": "V1", "source": "test"},
                                {"name": "bank_no", "value": "000", "source": "test"},
                                {"name": "in_client_no", "value": "C1", "source": "test"},
                                {"name": "open_branch", "value": "100", "source": "test"},
                            ],
                        ],
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class Completed:
        returncode = 1
        stdout = "ERROR (1062, \"Duplicate entry 'V1-000-C1' for key 'PRIMARY'\")"
        stderr = ""

    monkeypatch.setattr("hui_agent.nodes.skill_steps.subprocess.run", lambda *args, **kwargs: Completed())

    build_default_step_handlers(skill)["4"](_state(case_dir))

    case_plan = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))
    assert case_plan["repair_attempts"][0]["actions"][0]["type"] == "patch_duplicate_insert_keys"
    assert case_plan["insert_plans"][0]["rows"] is None
    sql = base_sql.read_text(encoding="utf-8")
    # repair 路径从旧 insert_plans 重建 SQL（dict 中无 db 字段时不加库前缀）
    assert sql.count("INSERT INTO tbvirbankaccmap") == 1
    assert "'100'" not in sql


def test_pytest_attempt_summary_records_passed_and_failed_tests():
    class Completed:
        returncode = 1
        stdout = """
=========================== short test summary info ===========================
PASSED path/to/test_FUNC_queryall.py::test_FUNC_queryall
PASSED path/to/test_FUNC_reset.py::test_FUNC_reset
FAILED path/to/test_FUNC_branch.py::test_FUNC_branch
FAILED path/to/test_FUNC_headquarter.py::test_FUNC_headquarter
============= 2 failed, 2 passed, 4 warnings in 10.00s =============
"""
        stderr = ""

    summary = _pytest_attempt_summary(1, Completed())

    assert summary["summary"] == "2 failed, 2 passed, 4 warnings in 10.00s"
    assert summary["passed_tests"] == ["test_FUNC_queryall", "test_FUNC_reset"]
    assert summary["failed_tests"] == ["test_FUNC_branch", "test_FUNC_headquarter"]


def test_pytest_failed_nodeids_includes_errors_but_ignores_root_errors():
    class Completed:
        stdout = """
=========================== short test summary info ===========================
FAILED path/to/test_FUNC_querymulti.py::test_FUNC_querymulti - AssertionError
ERROR path/to/test_FUNC_preview.py::test_FUNC_preview - playwright TimeoutError
ERROR root:login
ERROR root:login.py:60 - login helper line
============= 1 failed, 2 errors in 10.00s =============
"""
        stderr = ""

    assert _pytest_failed_nodeids(Completed(), fallback=["fallback.py"]) == [
        "path/to/test_FUNC_querymulti.py::test_FUNC_querymulti",
        "path/to/test_FUNC_preview.py::test_FUNC_preview",
    ]


def test_pytest_command_passes_menu_env_to_framework():
    command = _pytest_command(["test_generated.py"], stream=False, env_name="ifm60-dxfund")

    assert "--env=ifm60-dxfund" in command


def test_run_pytest_streams_case_results_and_output_lines(tmp_path):
    test_file = tmp_path / "test_streaming.py"
    test_file.write_text(
        "def test_ok():\n"
        "    print('ok body log')\n"
        "    assert True\n\n"
        "def test_bad():\n"
        "    print('bad body log')\n"
        "    assert False\n",
        encoding="utf-8",
    )
    events = []

    completed = _run_pytest([str(test_file)], event_logger=events.append, attempt_no=1)

    assert completed.returncode == 1
    assert "ok body log" in completed.stdout
    assert "bad body log" in completed.stdout
    assert any(event["event"] == "pytest_log_line" and event["line"] for event in events)
    case_events = [event for event in events if event["event"] == "pytest_case_result"]
    assert {event["case"]: event["status"] for event in case_events} == {
        "test_ok": "PASSED",
        "test_bad": "FAILED",
    }


def test_report_writes_summary_markdown(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A"},
                "verification": {"status": "passed", "passed": 1, "failed": 0},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = build_default_step_handlers(skill)["5"](_state(case_dir))

    report = case_dir / "reports" / "summary.md"
    assert result.completed_steps == ["5"]
    assert "菜单A" in report.read_text(encoding="utf-8")


def test_report_includes_failed_repair_attempt_summary(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A"},
                "verification": {"status": "failed", "returncode": 1},
                "repair_attempts": [
                    {
                        "attempt": 1,
                        "status": "patched",
                        "actions": [{"type": "patch_sql"}],
                        "pytest": {"returncode": 1},
                        "llm_decision": {"summary": "SQL data mismatch"},
                    },
                    {"attempt": 2, "status": "no_action", "actions": []},
                    {"attempt": 3, "status": "no_action", "actions": []},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    build_default_step_handlers(skill)["5"](_state(case_dir))

    text = (case_dir / "reports" / "summary.md").read_text(encoding="utf-8")
    assert "- 修复尝试次数: 3" in text
    assert "第1轮: status=patched, pytest=1, llm=SQL data mismatch, actions=patch_sql" in text
    assert "- 最后修复状态: no_action" in text
