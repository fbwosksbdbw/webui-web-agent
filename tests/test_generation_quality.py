import json
import os
from pathlib import Path

from hui_agent.fill_code import render_pytest_case
from hui_agent.insert_plan import build_insert_plans, load_base_tables, render_sql
from hui_agent.nodes.skill_steps import build_default_step_handlers
from hui_agent.state import HUITestState


def test_insert_plan_uses_only_schema_columns_and_skips_unmappable_tables():
    case_plan = {
        "case_inputs": {
            "querymulti": {
                "fields": [
                    {"label": "TA代码", "type": "single-select", "value": "HT:HT基金"},
                    {"label": "产品代码", "type": "single-select", "value": "UI_HT_P001:UI自动化HT信托产品"},
                ],
            }
        },
        "presets": {
            "ta_code": "HT:HT基金",
            "prd_code": "UI_HT_P001:UI自动化HT信托产品",
            "prd_name": "UI自动化HT信托产品",
            "bank_no": "000",
        },
        "preset_strategy": {
            "base_tables": ["tbprdtemplate", "tbdxtrustprdcardallow"],
            "biz_tables": [],
        },
        "shard": {
            "physical_tables": {
                "tbprdtemplate": ["tbprdtemplate"],
                "tbdxtrustprdcardallow": ["tbdxtrustprdcardallow"],
            }
        },
    }
    schema_cache = {
        "tbprdtemplate": {
            "required_columns": ["template_code", "bank_no"],
            "all_columns": ["template_code", "template_name", "prd_type", "bank_no"],
        },
        "tbdxtrustprdcardallow": {
            "required_columns": ["prd_code"],
            "all_columns": ["prd_code", "ta_code", "card_types", "reserve"],
        },
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)

    # tbprdtemplate 的 required_columns 含 template_code（无 preset 值）和 bank_no（preset="000"）
    # _columns_from_context 会为 template_code 生成默认值 ""，所以不再 missing，plan 正常生成
    assert [plan.physical_table for plan in plans] == ["tbprdtemplate", "tbdxtrustprdcardallow"]
    sql = render_sql(plans)
    # base 表带 ui_bc 库前缀
    assert "INSERT INTO ui_bc.tbprdtemplate" in sql
    assert "INSERT INTO ui_bc.tbdxtrustprdcardallow" in sql
    # tbprdtemplate: template_code="" 被 by_name 过滤，bank_no="000" 是有效的非空值 → DELETE WHERE bank_no = '000' 是正确的
    assert "DELETE FROM ui_bc.tbprdtemplate WHERE bank_no = '000'" in sql
    assert skipped == []


def test_insert_plan_uses_sql_where_literal_constants_for_insert_columns():
    case_plan = {
        "presets": {
            "account": "A33060210021113",
            "in_client_no": "33060210021113",
            "bank_no": "000",
            "ta_code": "HT",
            "ta_client": "TA33060210021113",
        },
        "preset_strategy": {"base_tables": ["tbhisassetacc"], "biz_tables": []},
        "shard": {"physical_tables": {"tbhisassetacc": ["tbhisassetacc"]}},
        "log_analysis": {
            "requirements": {
                "where_columns": {"tbhisassetacc": ["in_client_no", "prd_type", "open_branch"]},
                "raw_sqls": [
                    "select * from tbhisassetacc a where a.in_client_no = ? and a.prd_type='8' and a.open_branch in ('0011')"
                ],
            }
        },
    }
    schema_cache = {
        "tbhisassetacc": {
            "required_columns": ["in_client_no", "bank_no", "ta_code"],
            "all_columns": ["close_date", "in_client_no", "bank_no", "ta_code", "prd_type", "open_branch", "ta_client"],
        }
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)
    sql = render_sql(plans)

    assert skipped == []
    assert "prd_type" in sql
    assert "'8'" in sql
    assert "open_branch" in sql
    assert "'0011'" in sql


def test_insert_plan_hydrates_current_params_and_numeric_range_constants():
    case_plan = {
        "presets": {"bank_no": "000"},
        "preset_strategy": {"base_tables": [], "biz_tables": ["tbdxfundpayment"]},
        "log_analysis": {
            "requirements": {
                "raw_sqls": [
                    (
                        "select * from tbdxfundpayment a where "
                        "a.prd_code = ? and a.prd_manager = ? and a.ta_code = ? "
                        "and a.square_date>=20260101 and a.square_date<=20260130 "
                        "当前参数:cycleset01,111,WEBUI01"
                    )
                ]
            }
        },
    }
    schema_cache = {
        "tbdxfundpayment": {
            "required_columns": ["area_id", "table_num", "clear_date", "prd_code", "ta_code", "curr_type", "trans_date"],
            "all_columns": [
                "area_id",
                "table_num",
                "clear_date",
                "prd_code",
                "ta_code",
                "prd_manager",
                "curr_type",
                "trans_date",
                "square_date",
                "bank_no",
            ],
            "column_types": {
                "area_id": "int(11)",
                "clear_date": "int(11)",
                "trans_date": "int(11)",
                "square_date": "int(11)",
            },
        }
    }

    plans = build_insert_plans(case_plan, schema_cache=schema_cache)

    values = {column.name: column for column in plans[0].columns}
    assert values["prd_code"].value == "cycleset01"
    assert values["prd_manager"].value == "111"
    assert values["ta_code"].value == "WEBUI01"
    assert values["square_date"].value == "20260101"
    assert values["square_date"].source == "log_analysis.literal.square_date"


def test_business_insert_plan_prefers_routing_db_when_shard_db_is_base_db():
    case_plan = {
        "preset_strategy": {"base_tables": [], "biz_tables": ["tbdxfundpayment"]},
        "shard": {"db": "ui_bc", "routing_db": "ui_fund2"},
        "presets": {"prd_code": "cycleset01"},
    }
    schema_cache = {
        "tbdxfundpayment": {
            "required_columns": ["prd_code"],
            "all_columns": ["prd_code"],
        }
    }

    plans = build_insert_plans(
        case_plan,
        schema_cache=schema_cache,
        table_db_mapping={"tbdxfundpayment": "ui_fund"},
    )

    assert plans[0].db == "ui_fund2"


def test_multi_db_mapping_prefers_current_routing_family():
    from hui_agent.insert_plan import _resolve_plan_db

    mapping = {"tbdictmap": ["ui_dxcp", "ui_bc", "ui_trust", "ui_fund"]}

    assert _resolve_plan_db("tbdictmap", "business", mapping, routing_db="ui_trust2") == "ui_trust2"
    assert _resolve_plan_db("tbdictmap", "business", mapping, routing_db="ui_fund1") == "ui_fund1"


def test_no_insert_product_table_with_query_predicates_is_promoted_to_base_insert():
    case_plan = {
        "presets": {"prd_code": "cycleset01", "ta_code": "WEBUI01", "bank_no": "000"},
        "preset_strategy": {
            "base_tables": [],
            "biz_tables": ["tbdxfundpayment"],
            "no_insert_tables": ["tbdxfundproduct"],
        },
        "log_analysis": {
            "requirements": {
                "raw_sqls": [
                    (
                        "select * from tbdxfundpayment a,tbdxfundproduct c "
                        "where a.ta_code=c.ta_code and a.prd_manager=c.prd_manager "
                        "and a.prd_code=c.prd_code and c.prd_type='0' and c.prd_manager='111'"
                    )
                ]
            }
        },
    }
    schema_cache = {
        "tbdxfundpayment": {
            "required_columns": ["prd_code"],
            "all_columns": ["prd_code"],
        },
        "tbdxfundproduct": {
            "required_columns": ["prd_code"],
            "all_columns": ["prd_code", "ta_code", "bank_no", "prd_manager", "prd_type", "prd_name", "nav"],
            "column_types": {"nav": "decimal(18,8)"},
        },
    }

    plans = build_insert_plans(case_plan, schema_cache=schema_cache)

    product_plan = next(plan for plan in plans if plan.table == "tbdxfundproduct")
    values = {column.name: column.value for column in product_plan.columns}
    assert product_plan.role == "base"
    assert values["prd_code"] == "cycleset01"
    assert values["ta_code"] == "WEBUI01"
    assert values["prd_manager"] == "111"
    assert values["prd_type"] == "0"
    assert values["nav"] == "0.00"


def test_historical_asset_account_close_date_uses_query_end_date_not_zero():
    case_plan = {
        "presets": {
            "in_client_no": "33060210021113",
            "bank_no": "000",
            "ta_code": "HT",
            "ta_client": "TA33060210021113",
            "query_date_end": "2026-01-30",
        },
        "preset_strategy": {"base_tables": ["tbhisassetacc"], "biz_tables": []},
        "shard": {"physical_tables": {"tbhisassetacc": ["tbhisassetacc"]}},
        "log_analysis": {
            "requirements": {
                "where_columns": {"tbhisassetacc": ["in_client_no", "prd_type", "open_branch"]},
                "raw_sqls": [
                    "select * from tbhisassetacc a where a.prd_type='8' and a.open_branch in ('0011')"
                ],
            }
        },
    }
    schema_cache = {
        "tbhisassetacc": {
            "required_columns": ["close_date", "in_client_no", "bank_no", "ta_code"],
            "all_columns": ["close_date", "in_client_no", "bank_no", "ta_code", "prd_type", "open_branch", "ta_client"],
            "column_types": {"close_date": "varchar(8)"},
        }
    }

    plans = build_insert_plans(case_plan, schema_cache=schema_cache)

    values = {column.name: column.value for column in plans[0].columns}
    assert values["close_date"] == "20260130"


def test_insert_plan_never_generates_system_table_inserts():
    case_plan = {
        "case_inputs": {"querymulti": {"fields": [{"label": "客户标识", "type": "input", "value": "A1"}]}},
        "preset_strategy": {"base_tables": ["tsys_menu"], "biz_tables": ["tbsequence"]},
        "shard": {"physical_tables": {"tsys_menu": ["tsys_menu"], "tbsequence": ["tbsequence"]}},
    }
    schema_cache = {
        "tsys_menu": {"required_columns": ["menu_id"], "all_columns": ["menu_id", "menu_name"]},
        "tbsequence": {"required_columns": ["seq_name"], "all_columns": ["seq_name", "seq_value"]},
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)

    assert plans == []
    assert [item["reason"] for item in skipped] == ["system_table", "system_table"]
    assert "INSERT INTO tsys_menu" not in render_sql(plans)


def test_insert_plan_never_generates_workflow_table_inserts():
    case_plan = {
        "case_inputs": {"querymulti": {"fields": [{"label": "客户标识", "type": "input", "value": "A1"}]}},
        "preset_strategy": {
            "base_tables": ["tbworkflowsubtrans", "ACT_RU_TASK"],
            "biz_tables": ["ACT_RU_EXECUTION", "flow_instance", "flow_cust_nodecfg"],
        },
        "shard": {
            "physical_tables": {
                "tbworkflowsubtrans": ["tbworkflowsubtrans"],
                "ACT_RU_TASK": ["ACT_RU_TASK"],
                "ACT_RU_EXECUTION": ["ACT_RU_EXECUTION"],
                "flow_instance": ["flow_instance"],
                "flow_cust_nodecfg": ["flow_cust_nodecfg"],
            }
        },
    }
    schema_cache = {
        "tbworkflowsubtrans": {"required_columns": ["id"], "all_columns": ["id"]},
        "ACT_RU_TASK": {"required_columns": ["ID_"], "all_columns": ["ID_"]},
        "ACT_RU_EXECUTION": {"required_columns": ["ID_"], "all_columns": ["ID_"]},
        "flow_instance": {"required_columns": ["id"], "all_columns": ["id"]},
        "flow_cust_nodecfg": {"required_columns": ["id"], "all_columns": ["id"]},
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)

    assert plans == []
    assert [item["reason"] for item in skipped] == ["system_table"] * 5
    sql = render_sql(plans)
    assert "tbworkflowsubtrans" not in sql
    assert "ACT_RU_TASK" not in sql
    assert "flow_instance" not in sql


def test_insert_plan_uses_base_table_template_when_available():
    case_plan = {
        "presets": {
            "ta_code": "HT:HT基金",
            "prd_code": "UI_HT_P001:UI自动化HT信托产品",
            "prd_name": "UI自动化HT信托产品",
            "bank_no": "000",
        },
        "preset_strategy": {
            "base_tables": ["tbdxtrustproduct"],
            "biz_tables": [],
        },
        "shard": {"physical_tables": {"tbdxtrustproduct": ["tbdxtrustproduct"]}},
    }
    schema_cache = {
        "tbdxtrustproduct": {
            "required_columns": ["prd_code"],
            "all_columns": ["prd_code", "bank_no", "ta_code", "prd_type", "prd_name", "income_date"],
        },
    }
    base_tables = {
        "tbdxtrustproduct": {
            "records": {
                "HT_UI_HT_P001": {
                    "prd_code": "UI_HT_P001",
                    "bank_no": "000",
                    "ta_code": "HT",
                    "prd_type": "8",
                    "prd_name": "UI自动化HT信托产品",
                    "income_date": "20210506",
                    "nonexistent": "must be ignored",
                }
            }
        }
    }

    plans, skipped = build_insert_plans(
        case_plan,
        schema_cache=schema_cache,
        base_tables=base_tables,
        include_skipped=True,
    )

    assert skipped == []
    assert [column.name for column in plans[0].columns] == [
        "prd_code",
        "bank_no",
        "ta_code",
        "prd_type",
        "prd_name",
        "income_date",
    ]
    assert "nonexistent" not in render_sql(plans)


def test_insert_plan_normalizes_blank_decimal_values_from_base_template():
    case_plan = {
        "presets": {"prd_code": "UI_HT_P001", "ta_code": "HT", "bank_no": "000"},
        "preset_strategy": {"base_tables": ["tbdxtrustproduct"], "biz_tables": []},
        "shard": {"physical_tables": {"tbdxtrustproduct": ["tbdxtrustproduct"]}},
    }
    schema_cache = {
        "tbdxtrustproduct": {
            "required_columns": ["prd_code"],
            "all_columns": ["prd_code", "bank_no", "ta_code", "unusual_red_fee", "psub_unit", "version"],
        },
    }
    base_tables = {
        "tbdxtrustproduct": {
            "records": {
                "HT_UI_HT_P001": {
                    "prd_code": "UI_HT_P001",
                    "bank_no": "000",
                    "ta_code": "HT",
                    "unusual_red_fee": " ",
                    "psub_unit": " ",
                    "version": " ",
                }
            }
        }
    }

    plans, skipped = build_insert_plans(
        case_plan,
        schema_cache=schema_cache,
        base_tables=base_tables,
        include_skipped=True,
    )

    assert skipped == []
    values = {column.name: column.value for column in plans[0].columns}
    assert values["unusual_red_fee"] == "0"
    assert values["psub_unit"] == "0"
    assert values["version"] == "0"
    assert "' '" not in render_sql(plans)


def test_insert_plan_derives_account_request_required_columns_from_form_fields():
    case_plan = {
        "presets": {
            "customer_id": "A33060210021113",
            "customer_id_type": "0:入账账号",
            "ta_code": "WEBUI01:测试TA01",
        },
        "form_fields": {
            "all": [
                {"label": "系统流水号", "type": "input", "value": "SQ20260607001"},
                {"label": "客户标识", "type": "input", "value": "A33060210021113"},
                {"label": "TA代码", "type": "multi-select", "value": "WEBUI01:测试TA01"},
            ],
            "excluded_fields": ["系统流水号"],
        },
        "case_inputs": {
            "querymulti": {
                "fields": [
                    {"label": "客户标识", "type": "input", "value": "A33060210021113"},
                    {"label": "TA代码", "type": "multi-select", "value": "WEBUI01:测试TA01"},
                ]
            }
        },
        "preset_strategy": {
            "base_tables": ["tbvirbankaccmap", "tbaccreq", "tbaccreqext"],
            "biz_tables": [],
        },
        "shard": {
            "physical_tables": {
                "tbvirbankaccmap": ["tbvirbankaccmap"],
                "tbaccreq": ["tbaccreq"],
                "tbaccreqext": ["tbaccreqext"],
            }
        },
    }
    schema_cache = {
        "tbvirbankaccmap": {
            "required_columns": ["bank_acc", "virtual_bank_acc"],
            "all_columns": ["bank_acc", "virtual_bank_acc", "ta_code"],
        },
        "tbaccreq": {
            "required_columns": ["serial_no"],
            "all_columns": ["serial_no", "bank_acc", "ta_code"],
        },
        "tbaccreqext": {
            "required_columns": ["serial_no"],
            "all_columns": ["serial_no", "actor_name"],
        },
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)

    assert skipped == []
    by_table = {
        plan.table: {column.name: column.value for column in plan.columns}
        for plan in plans
    }
    assert by_table["tbvirbankaccmap"]["bank_acc"] == "A33060210021113"
    assert by_table["tbvirbankaccmap"]["virtual_bank_acc"] == "V33060210021113"
    assert by_table["tbaccreq"]["serial_no"] == "SQ20260607001"
    assert by_table["tbaccreqext"]["serial_no"] == "SQ20260607001"


def test_insert_plan_delete_where_prefers_schema_required_key_columns():
    case_plan = {
        "presets": {
            "customer_id": "A33060210021113",
            "bank_no": "000",
            "in_client_no": "33060210021113",
        },
        "preset_strategy": {"base_tables": ["tbvirbankaccmap"], "biz_tables": []},
        "shard": {"physical_tables": {"tbvirbankaccmap": ["tbvirbankaccmap"]}},
    }
    schema_cache = {
        "tbvirbankaccmap": {
            "required_columns": ["virtual_bank_acc", "bank_no", "in_client_no"],
            "all_columns": ["virtual_bank_acc", "bank_no", "bank_acc", "in_client_no", "open_branch"],
        },
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)

    assert skipped == []
    assert plans[0].delete_where == (
        "virtual_bank_acc = 'V33060210021113' "
        "AND bank_no = '000' "
        "AND in_client_no = '33060210021113'"
    )
    assert (
        "DELETE FROM ui_bc.tbvirbankaccmap WHERE virtual_bank_acc = 'V33060210021113' "
        "AND bank_no = '000' "
        "AND in_client_no = '33060210021113';"
    ) in render_sql(plans)


def test_permission_rows_do_not_duplicate_identity_when_split_column_is_not_key():
    case_plan = {
        "case_count": 7,
        "presets": {
            "customer_id": "A33060210021113",
            "bank_no": "000",
            "in_client_no": "33060210021113",
            "branch_no": "0011",
            "hq_branch_no": "100",
        },
        "preset_strategy": {"base_tables": ["tbvirbankaccmap"], "biz_tables": []},
        "shard": {"physical_tables": {"tbvirbankaccmap": ["tbvirbankaccmap"]}},
    }
    schema_cache = {
        "tbvirbankaccmap": {
            "required_columns": ["virtual_bank_acc", "bank_no", "in_client_no"],
            "all_columns": ["virtual_bank_acc", "bank_no", "bank_acc", "in_client_no", "open_branch"],
        },
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)

    assert skipped == []
    assert len(plans[0].rows) == 1
    sql = render_sql(plans)
    assert sql.count("('V33060210021113', '000', 'A33060210021113', '33060210021113', '0011')") == 1
    assert "'100'" not in sql


def test_permission_rows_make_headquarter_serial_identity_distinct():
    case_plan = {
        "case_count": 7,
        "form_fields": {
            "all": [
                {"label": "系统流水号", "value": "SQ20260607001"},
                {"label": "客户标识", "value": "A33060210021113"},
            ]
        },
        "presets": {
            "branch_no": "0011",
            "hq_branch_no": "100",
        },
        "preset_strategy": {"base_tables": ["tbaccreq"], "biz_tables": []},
        "shard": {"physical_tables": {"tbaccreq": ["tbaccreq"]}},
    }
    schema_cache = {
        "tbaccreq": {
            "required_columns": ["serial_no"],
            "all_columns": ["serial_no", "bank_acc", "branch_no", "open_branch"],
        },
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)

    assert skipped == []
    assert len(plans[0].rows) == 2
    assert [[column.value for column in row if column.name == "serial_no"][0] for row in plans[0].rows] == [
        "SQ20260607001",
        "SQ20260607002",
    ]
    assert [[column.value for column in row if column.name == "branch_no"][0] for row in plans[0].rows] == [
        "0011",
        "100",
    ]
    assert [[column.value for column in row if column.name == "open_branch"][0] for row in plans[0].rows] == [
        "0011",
        "100",
    ]
    assert (
        "DELETE FROM ui_bc.tbaccreq WHERE (serial_no = 'SQ20260607001') "
        "OR (serial_no = 'SQ20260607002');"
    ) in render_sql(plans)


def test_permission_rows_can_distinguish_historical_asset_account_by_close_date():
    case_plan = {
        "case_count": 7,
        "case_inputs": {
            "querymulti": {
                "fields": [
                    {"label": "账号", "type": "input", "value": "A1"},
                ]
            }
        },
        "presets": {
            "account": "A1",
            "bank_no": "000",
            "ta_code": "HT",
            "branch_no": "0011",
            "hq_branch_no": "100",
            "query_date_end": "2026-01-30",
        },
        "preset_strategy": {"base_tables": [], "biz_tables": ["tbhisassetacc"]},
        "shard": {"physical_tables": {"tbhisassetacc": ["tbhisassetacc"]}},
    }
    schema_cache = {
        "tbhisassetacc": {
            "required_columns": ["close_date", "bank_acc", "bank_no", "ta_code"],
            "all_columns": ["close_date", "bank_acc", "bank_no", "ta_code", "open_branch"],
        }
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)

    assert skipped == []
    assert len(plans[0].rows) == 2
    rows = [{column.name: column.value for column in row} for row in plans[0].rows]
    assert [row["open_branch"] for row in rows] == ["0011", "100"]
    assert rows[0]["close_date"] != rows[1]["close_date"]
    assert plans[0].delete_where == "bank_acc = 'A1' AND bank_no = '000' AND ta_code = 'HT'"


def test_customer_support_tables_delete_by_join_keys_to_avoid_duplicate_joins():
    case_plan = {
        "presets": {
            "customer_id": "A1",
            "in_client_no": "IC1",
            "bank_no": "000",
        },
        "preset_strategy": {"base_tables": ["tbclientseller", "tbbankacc"], "biz_tables": []},
        "shard": {"physical_tables": {"tbclientseller": ["tbclientseller10"], "tbbankacc": ["tbbankacc10"]}},
    }
    schema_cache = {
        "tbclientseller10": {
            "required_columns": ["in_client_no", "bank_no", "seller_code"],
            "all_columns": ["in_client_no", "bank_no", "seller_code", "client_no", "open_branch"],
        },
        "tbbankacc10": {
            "required_columns": ["bank_no", "bank_acc"],
            "all_columns": ["bank_no", "bank_acc", "ta_client", "in_client_no", "open_branch"],
        },
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)

    assert skipped == []
    delete_by_table = {plan.physical_table: plan.delete_where for plan in plans}
    assert delete_by_table["tbclientseller10"] == "in_client_no = 'IC1'"
    assert delete_by_table["tbbankacc10"] == "bank_acc = 'A1' AND in_client_no = 'IC1'"


def test_generated_query_pytest_has_real_count_assertion():
    case_plan = {
        "menu": {"code": "FUNC_A", "url": "/menu"},
        "case_inputs": {
            "querymulti": {
                "fields": [
                    {"label": "客户标识", "type": "input", "value": "A1"},
                ]
            }
        },
        "insert_plans": [{"table": "t", "columns": [{"name": "id", "value": "1"}]}],
    }

    code = render_pytest_case(case_plan, "querymulti")

    assert "assert True" not in code
    assert "_click_query_and_wait" in code
    assert "cnt = await _click_query_and_wait(page)" in code
    assert "assert cnt > 0" in code


def test_generated_query_waits_for_positive_record_count_in_slow_queries():
    case_plan = {
        "menu": {"code": "FUNC_A", "url": "/menu"},
        "case_inputs": {"queryall": {"fields": []}},
        "insert_plans": [{"table": "t", "columns": [{"name": "id", "value": "1"}]}],
    }

    code = render_pytest_case(case_plan, "queryall")

    assert "count > 0" in code
    assert "for _ in range(300)" in code
    assert "count = await _get_record_count(page)" in code
    assert "wait_for_function" not in code
    assert "return !!m;" not in code


def test_generated_queryall_uses_longer_record_count_wait():
    case_plan = {
        "menu": {"code": "FUNC_A", "url": "/menu"},
        "case_inputs": {"queryall": {"fields": []}},
        "insert_plans": [{"table": "t", "columns": [{"name": "id", "value": "1"}]}],
    }

    queryall_code = render_pytest_case(case_plan, "queryall")
    querysingle_code = render_pytest_case(
        {
            **case_plan,
            "case_inputs": {"querysingle": {"fields": [{"label": "账号", "type": "input", "value": "A1"}]}},
        },
        "querysingle",
    )

    assert "for _ in range(300)" in queryall_code
    assert "logging.info(\"等待查询结果中，已等待%s秒\", waited)" in queryall_code
    assert "for _ in range(60)" in querysingle_code


def test_generated_reset_pytest_asserts_field_is_cleared():
    case_plan = {
        "menu": {"code": "FUNC_A", "url": "/menu"},
        "case_inputs": {
            "reset": {
                "fields": [
                    {"label": "客户标识", "type": "input", "value": "A1"},
                ]
            }
        },
    }

    code = render_pytest_case(case_plan, "reset")

    assert "assert True" not in code
    assert "_input_value_by_label" in code
    assert 'assert value == ""' in code


def test_generated_reset_pytest_handles_select_field_and_asserts_actual_control_value():
    case_plan = {
        "menu": {"code": "FUNC_A", "url": "/menu"},
        "case_inputs": {
            "reset": {
                "fields": [
                    {"label": "产品代码", "type": "single-select", "value": "UI_HT_P001:UI自动化HT信托产品"},
                ]
            }
        },
    }

    code = render_pytest_case(case_plan, "reset")

    assert "_select_dropdown_by_label" in code
    assert "_control_value_by_label" in code
    assert 'value = await _control_value_by_label(page, "产品代码")' in code
    assert 'assert value == ""' in code
    assert "查询按钮不存在" not in code


def test_generated_preview_pytest_clicks_preview_button_and_asserts_detail_panel():
    case_plan = {
        "menu": {"code": "FUNC_A", "url": "/menu"},
        "case_inputs": {
            "preview": {
                "fields": [
                    {"label": "产品代码", "type": "single-select", "value": "UI_HT_P001:UI自动化HT信托产品"},
                ]
            }
        },
        "insert_plans": [{"table": "t", "columns": [{"name": "id", "value": "1"}]}],
    }

    code = render_pytest_case(case_plan, "preview")

    assert "await _click_preview_button(page)" in code
    assert "detail_val = await _first_readonly_detail_value(page)" in code
    assert 'assert detail_val' in code
    assert "结果预览查询为空" not in code


def test_generated_pytest_is_valid_python_for_non_ascii_values():
    case_plan = {
        "menu": {"code": "FUNC_A", "url": "/menu"},
        "case_inputs": {
            "querysingle": {
                "fields": [
                    {"label": "产品代码", "type": "single-select", "value": "UI_HT_P001:UI自动化HT信托产品"},
                ]
            }
        },
        "insert_plans": [{"table": "t", "columns": [{"name": "id", "value": "1"}]}],
    }

    compile(render_pytest_case(case_plan, "querysingle"), "generated.py", "exec")


def test_generated_pytest_does_not_embed_first_option_or_pagination_js_helpers():
    case_plan = {
        "menu": {"code": "FUNC_A", "url": "/menu"},
        "case_inputs": {
            "querysingle": {
                "fields": [
                    {"label": "产品状态", "type": "single-select", "value": "__FIRST_OPTION__"},
                    {"label": "产品代码", "type": "single-select", "value": "P1:产品1"},
                ]
            }
        },
        "insert_plans": [{"table": "t", "columns": [{"name": "id", "value": "1"}]}],
    }

    code = render_pytest_case(case_plan, "querysingle")

    assert "_select_first_option_by_label" not in code
    assert "PAGINATION_JS" not in code
    assert "wait_for_function" not in code
    assert "产品状态" not in code
    assert '_select_dropdown_by_label(page, "产品代码", "P1:产品1")' in code
    compile(code, "generated.py", "exec")


def test_generated_pytest_uses_project_config_sql_paths(tmp_path):
    test_root = tmp_path / "testcases" / "综合理财平台6.0" / "信托代销"
    sql_dir = test_root / "数据预埋" / "查询管理" / "菜单A"
    test_path = test_root / "查询管理" / "菜单A" / "菜单A_全量查询" / "test_FUNC_A_queryall.py"
    base_sql = sql_dir / "FUNC_A_base.sql"
    preset_sql = sql_dir / "FUNC_A.sql"
    case_plan = {
        "menu": {"name": "菜单A", "code": "FUNC_A", "level1": "信托代销", "level2": "查询管理", "url": "/menu"},
        "case_inputs": {"queryall": {"fields": []}},
        "sql_paths": {"base_sql": str(base_sql), "preset_sql": str(preset_sql)},
        "insert_plans": [{"table": "t", "columns": [{"name": "id", "value": "1"}]}],
    }

    code = render_pytest_case(case_plan, "queryall", output_path=test_path)

    assert str(tmp_path) not in code
    assert "from conf.config import Config" in code
    assert 'PROJECT_ROOT = getattr(Config, "python_dir", None) or Config.root_dir' in code
    assert "Config.SQL_DATA_DIRS[" in code
    assert "信托代销" in code
    assert "查询管理" in code
    assert "菜单A" in code
    assert "FUNC_A_base.sql" in code
    assert "HUI_PRESET_SQL" not in code
    assert "__file__" not in code
    compile(code, "generated.py", "exec")


def test_generated_pytest_passes_menu_env_to_db_executor():
    case_plan = {
        "env_key": "ifm60-dxfund",
        "menu": {"name": "菜单A", "code": "FUNC_A", "level1": "基金代销", "level2": "报表管理", "url": "/menu"},
        "case_inputs": {"queryall": {"fields": []}},
        "shard": {"db": "ui_fund2"},
        "insert_plans": [{"table": "t", "columns": [{"name": "id", "value": "1"}]}],
    }

    code = render_pytest_case(case_plan, "queryall")

    assert 'DB_ENV_NAME = "ifm60-dxfund"' in code
    assert 'PRESET_DB_KEY = "ui_fund2"' in code
    assert "get_async_db_executor(env_name=DB_ENV_NAME, db_key=BASE_DB_KEY, dbtype=\"mysql\")" in code
    assert "get_async_db_executor(env_name=DB_ENV_NAME, db_key=PRESET_DB_KEY, dbtype=\"mysql\")" in code
    compile(code, "generated.py", "exec")


def test_generated_pytest_matches_skill_template_metadata():
    case_plan = {
        "owner": "owner1",
        "menu": {"name": "菜单A", "code": "FUNC_A", "level1": "一级", "level2": "二级", "url": "/menu"},
        "case_inputs": {"queryall": {"fields": []}},
        "insert_plans": [{"table": "t", "columns": [{"name": "id", "value": "1"}]}],
    }

    code = render_pytest_case(case_plan, "queryall")

    assert "import os, logging, allure, pytest" in code
    assert "from framework.utils.prettyAllure import hierarchical_case" in code
    assert '@hierarchical_case("一级", "二级", "菜单A", "菜单A_全量查询")' in code
    assert '@allure.description("菜单A - 全量查询")' in code
    assert '@pytest.mark.case_source("AI")' in code
    assert '@allure.label("owner", "owner1")' in code
    compile(code, "generated.py", "exec")


def test_branch_and_headquarter_pytests_use_exact_expected_counts():
    case_plan = {
        "menu": {"code": "FUNC_A", "url": "/menu"},
        "case_inputs": {
            "branch": {"fields": []},
            "headquarter": {"fields": []},
        },
        "expected_counts": {"branch": 1, "headquarter": 2},
        "insert_plans": [{"table": "t", "columns": [{"name": "id", "value": "1"}]}],
    }

    branch_code = render_pytest_case(case_plan, "branch")
    hq_code = render_pytest_case(case_plan, "headquarter")

    assert '@pytest.mark.login_user("branch_user")' in branch_code
    assert "assert cnt == 1" in branch_code
    assert '@pytest.mark.login_user("branch_user")' not in hq_code
    assert "assert cnt == 2" in hq_code


def test_branch_pytests_prefer_permission_precheck_expected_counts():
    case_plan = {
        "menu": {"code": "FUNC_A", "url": "/menu"},
        "case_inputs": {
            "branch": {"fields": []},
            "headquarter": {"fields": []},
        },
        "expected_counts": {"branch": 1, "headquarter": 2},
        "sql_return_precheck": {
            "permission_checks": {
                "branch": {"expected": 3, "actual": 3, "status": "passed"},
                "headquarter": {"expected": 4, "actual": 4, "status": "passed"},
            }
        },
        "insert_plans": [{"table": "t", "columns": [{"name": "id", "value": "1"}]}],
    }

    branch_code = render_pytest_case(case_plan, "branch")
    hq_code = render_pytest_case(case_plan, "headquarter")

    assert "assert cnt == 3" in branch_code
    assert "assert cnt == 4" in hq_code


def test_case_generate_uses_skill_directory_suffixes(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    test_root = tmp_path / "tests_out"
    case_dir.mkdir()
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A", "code": "FUNC_A", "level1": "一级", "level2": "二级", "url": "/m"},
                "case_order": ["branch", "headquarter", "queryall", "querysingle", "querymulti", "reset", "preview"],
                "case_inputs": {name: {"fields": [], "excluded": {}} for name in [
                    "branch", "headquarter", "queryall", "querysingle", "querymulti", "reset", "preview"
                ]},
                    "paths": {"test_case_dir": str(test_root)},
                    "expected_counts": {"branch": 1, "headquarter": 2},
                    "shard": {"db": "ui_bc"},
                },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(case_dir / "config.json"),
        case_plan_path=str(case_dir / "case_plan.json"),
        case_dir=str(case_dir),
    )
    result = build_default_step_handlers(skill)["3"](state)

    generated = json.loads((case_dir / "case_plan.json").read_text(encoding="utf-8"))["generated_tests"]
    expected = [
        test_root / "二级" / "菜单A" / "菜单A_分行权限" / "test_FUNC_A_branch.py",
        test_root / "二级" / "菜单A" / "菜单A_总行权限" / "test_FUNC_A_headquarter.py",
        test_root / "二级" / "菜单A" / "菜单A_全量查询" / "test_FUNC_A_queryall.py",
        test_root / "二级" / "菜单A" / "菜单A_单条件查询" / "test_FUNC_A_querysingle.py",
        test_root / "二级" / "菜单A" / "菜单A_多条件查询" / "test_FUNC_A_querymulti.py",
        test_root / "二级" / "菜单A" / "菜单A_条件重置" / "test_FUNC_A_reset.py",
        test_root / "二级" / "菜单A" / "菜单A_结果预览" / "test_FUNC_A_preview.py",
    ]

    assert result.completed_steps == ["3"]
    assert generated == [Path(os.path.relpath(path, start=case_dir)).as_posix() for path in expected]
    assert all(path.exists() for path in expected)
    assert not any(path.is_absolute() for path in map(Path, generated))


def test_case_generate_removes_stale_generated_case_files(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    test_root = tmp_path / "tests_out"
    case_dir.mkdir()
    legacy_branch = test_root / "二级" / "菜单A" / "test_FUNC_A_branch.py"
    legacy_hq = test_root / "二级" / "菜单A" / "test_FUNC_A_headquarter.py"
    stale_subdir_branch = test_root / "二级" / "菜单A" / "菜单A_分行权限" / "test_FUNC_A_branch.py"
    handwritten = test_root / "二级" / "菜单A" / "test_FUNC_A_custom.py"
    legacy_branch.parent.mkdir(parents=True)
    stale_subdir_branch.parent.mkdir(parents=True)
    legacy_branch.write_text("# old branch\n", encoding="utf-8")
    legacy_hq.write_text("# old hq\n", encoding="utf-8")
    stale_subdir_branch.write_text("# old branch in generated dir\n", encoding="utf-8")
    handwritten.write_text("# keep me\n", encoding="utf-8")
    (case_dir / "config.json").write_text("{}", encoding="utf-8")
    (case_dir / "case_plan.json").write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A", "code": "FUNC_A", "level2": "二级", "url": "/m"},
                "case_inputs": {
                    "queryall": {"fields": [], "excluded": {}},
                    "querysingle": {"fields": [], "excluded": {}},
                },
                "paths": {"test_case_dir": str(test_root)},
                "shard": {"db": "ui_bc"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(case_dir / "config.json"),
        case_plan_path=str(case_dir / "case_plan.json"),
        case_dir=str(case_dir),
    )
    build_default_step_handlers(skill)["3"](state)

    assert not legacy_branch.exists()
    assert not legacy_hq.exists()
    assert not stale_subdir_branch.exists()
    assert handwritten.exists()


def test_branch_permissions_insert_plan_generates_two_rows_and_expected_counts():
    case_plan = {
        "case_order": ["branch", "headquarter"],
        "case_inputs": {
            "querymulti": {
                "fields": [
                    {"label": "客户标识", "type": "input", "value": "A1"},
                ],
            }
        },
        "presets": {
            "customer_id": "A1",
            "branch_no": "0011",
            "hq_branch_no": "100",
            "internal_branch": "1102",
            "hq_internal_branch": "11",
        },
        "preset_strategy": {"base_tables": [], "biz_tables": ["biztable"]},
        "shard": {"physical_tables": {"biztable": ["biztable1"]}},
    }
    schema_cache = {
        "biztable1": {
            "required_columns": ["bank_acc", "open_branch"],
            "all_columns": ["bank_acc", "open_branch", "internal_branch"],
        }
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)

    assert skipped == []
    assert len(plans[0].rows) == 2
    assert [[column.value for column in row if column.name == "open_branch"][0] for row in plans[0].rows] == [
        "0011",
        "100",
    ]
    sql = render_sql(plans)
    assert "('A1', '0011', '1102')" in sql
    assert "('A1', '100', '11')" in sql


def test_branch_permissions_insert_plan_supports_branch_no_split_column():
    case_plan = {
        "case_count": 7,
        "case_inputs": {
            "querymulti": {
                "fields": [
                    {"label": "客户标识", "type": "input", "value": "A1"},
                ],
            }
        },
        "presets": {
            "customer_id": "A1",
            "branch_no": "0011",
            "hq_branch_no": "100",
        },
        "preset_strategy": {"base_tables": [], "biz_tables": ["biztable"]},
        "shard": {"physical_tables": {"biztable": ["biztable1"]}},
    }
    schema_cache = {
        "biztable1": {
            "required_columns": ["bank_acc", "branch_no"],
            "all_columns": ["bank_acc", "branch_no"],
        }
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)

    assert skipped == []
    assert len(plans[0].rows) == 2
    assert [[column.value for column in row if column.name == "branch_no"][0] for row in plans[0].rows] == [
        "0011",
        "100",
    ]


def test_branch_permissions_insert_plan_supports_additional_permission_columns():
    case_plan = {
        "case_count": 7,
        "case_inputs": {
            "querymulti": {
                "fields": [
                    {"label": "客户标识", "type": "input", "value": "A1"},
                ],
            }
        },
        "presets": {
            "customer_id": "A1",
            "branch_no": "0011",
            "hq_branch_no": "100",
        },
        "preset_strategy": {"base_tables": [], "biz_tables": ["biztable"]},
        "shard": {"physical_tables": {"biztable": ["biztable1"]}},
    }
    schema_cache = {
        "biztable1": {
            "required_columns": ["bank_acc", "org_no"],
            "all_columns": ["bank_acc", "org_no", "seller_branch", "belong_branch", "branch_code", "open_org"],
        }
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)

    assert skipped == []
    assert len(plans[0].rows) == 2
    for column in ("org_no", "seller_branch", "belong_branch", "branch_code", "open_org"):
        assert [[item.value for item in row if item.name == column][0] for row in plans[0].rows] == [
            "0011",
            "100",
        ]


def test_insert_plan_does_not_treat_customer_mapping_tables_as_system_tables():
    case_plan = {
        "case_inputs": {"querymulti": {"fields": [{"label": "客户标识", "type": "input", "value": "A1"}]}},
        "preset_strategy": {"base_tables": [], "biz_tables": ["tbclientmap"]},
        "shard": {"physical_tables": {"tbclientmap": ["tbclientmap"]}},
    }
    schema_cache = {
        "tbclientmap": {
            "required_columns": ["bank_acc"],
            "all_columns": ["bank_acc", "in_client_no"],
        }
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)

    assert skipped == []
    assert [plan.table for plan in plans] == ["tbclientmap"]


def test_insert_plan_uses_tbclientmap_base_table_reference_record():
    case_plan = {
        "case_inputs": {
            "querymulti": {
                "fields": [
                    {"label": "客户标识", "type": "input", "value": "A33060210021113"},
                ]
            }
        },
        "presets": {
            "customer_id": "A33060210021113",
            "bank_no": "000",
            "ta_client": "TA33060210021113",
            "in_client_no": "33060210021113",
            "client_no": "33060210021113",
            "account": "A33060210021113",
        },
        "preset_strategy": {"base_tables": ["tbclientmap"], "biz_tables": []},
        "shard": {"physical_tables": {"tbclientmap": ["tbclientmap"]}},
    }
    schema_cache = {
        "tbclientmap": {
            "required_columns": ["bank_no", "ta_client", "in_client_no", "account"],
            "all_columns": ["bank_no", "bank_acc", "ta_client", "in_client_no", "client_type", "client_no", "account"],
        }
    }
    base_tables = {
        "tbclientmap": {
            "records": {
                "default_customer": {
                    "bank_no": "000",
                    "bank_acc": "A33060210021113",
                    "ta_client": "TA33060210021113",
                    "in_client_no": "33060210021113",
                    "client_type": "0",
                    "client_no": "33060210021113",
                    "account": "A33060210021113",
                }
            }
        }
    }

    plans = build_insert_plans(case_plan, schema_cache=schema_cache, base_tables=base_tables)

    assert len(plans) == 1
    assert {column.name for column in plans[0].columns} == set(schema_cache["tbclientmap"]["all_columns"])
    assert all(column.source == "base_tables.tbclientmap" for column in plans[0].columns)


def test_tbclientmap_insert_plan_adds_spaced_bank_no_variant_for_branch_lookup():
    case_plan = {
        "presets": {
            "customer_id": "A33060210021113",
            "bank_no": "000",
            "ta_client": "TA33060210021113",
            "in_client_no": "33060210021113",
            "client_no": "33060210021113",
            "account": "A33060210021113",
        },
        "preset_strategy": {"base_tables": ["tbclientmap"], "biz_tables": []},
        "shard": {"physical_tables": {"tbclientmap": ["tbclientmap"]}},
    }
    schema_cache = {
        "tbclientmap": {
            "required_columns": ["bank_no", "ta_client", "in_client_no", "account"],
            "all_columns": ["bank_no", "bank_acc", "ta_client", "in_client_no", "client_type", "client_no", "account"],
        }
    }
    base_tables = {
        "tbclientmap": {
            "records": {
                "default_customer": {
                    "bank_no": "000",
                    "bank_acc": "A33060210021113",
                    "ta_client": "TA33060210021113",
                    "in_client_no": "33060210021113",
                    "client_type": "0",
                    "client_no": "33060210021113",
                    "account": "A33060210021113",
                }
            }
        }
    }

    plans = build_insert_plans(case_plan, schema_cache=schema_cache, base_tables=base_tables)

    rows = [{column.name: column.value for column in row} for row in plans[0].rows]
    assert [row["bank_no"] for row in rows] == ["000", " 000"]
    assert "bank_no = ' 000'" in plans[0].delete_where


def test_insert_plan_uses_tbclient_base_table_reference_record_for_customer_lookup():
    case_plan = {
        "presets": {
            "in_client_no": "33060210021113",
            "customer_id_type": "0",
        },
        "preset_strategy": {"base_tables": ["tbclient"], "biz_tables": []},
        "shard": {"physical_tables": {"tbclient": ["tbclient10"]}},
    }
    schema_cache = {
        "tbclient10": {
            "required_columns": ["in_client_no"],
            "all_columns": ["in_client_no", "client_type", "id_type", "id_code", "short_name", "client_name", "acc_status"],
        }
    }
    base_tables = {
        "tbclient": {
            "records": {
                "default_customer": {
                    "in_client_no": "33060210021113",
                    "client_type": "0",
                    "id_type": "0",
                    "id_code": "33060210021113",
                    "short_name": "UI自动化客户",
                    "client_name": "UI自动化客户",
                    "acc_status": "0",
                }
            }
        }
    }

    plans = build_insert_plans(case_plan, schema_cache=schema_cache, base_tables=base_tables)

    assert len(plans) == 1
    assert plans[0].physical_table == "tbclient10"
    assert {column.name for column in plans[0].columns} == set(schema_cache["tbclient10"]["all_columns"])


def test_generated_record_count_does_not_count_empty_table_placeholder_rows():
    case_plan = {
        "menu": {"code": "FUNC_A", "url": "/menu"},
        "case_inputs": {"branch": {"fields": []}},
        "expected_counts": {"branch": 1},
        "insert_plans": [{"table": "t", "columns": [{"name": "id", "value": "1"}]}],
    }

    code = render_pytest_case(case_plan, "branch")

    assert "Math.max(count, rows)" not in code
    assert "count = await _get_record_count(page)" in code
    assert "wait_for_function" not in code


def test_permission_insert_plan_generates_non_empty_cfm_no_for_required_confirmation_tables():
    case_plan = {
        "case_count": 7,
        "case_inputs": {
            "querymulti": {
                "fields": [
                    {"label": "客户标识", "type": "input", "value": "A1"},
                ],
            }
        },
        "presets": {
            "customer_id": "A1",
            "branch_no": "0011",
            "hq_branch_no": "100",
        },
        "preset_strategy": {"base_tables": [], "biz_tables": ["tbdxfundtranscfm"]},
        "shard": {"physical_tables": {"tbdxfundtranscfm": ["tbdxfundtranscfm13"]}},
    }
    schema_cache = {
        "tbdxfundtranscfm13": {
            "required_columns": ["bank_acc", "open_branch", "cfm_no"],
            "all_columns": ["bank_acc", "open_branch", "cfm_no"],
        }
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)

    assert skipped == []
    assert len(plans) == 1
    assert len(plans[0].rows) == 2
    for row in plans[0].rows:
        values = {column.name: column.value for column in row}
        assert values["cfm_no"]


def test_fund_frequent_report_insert_plan_generates_subscribe_and_redeem_pairs():
    case_plan = {
        "case_count": 7,
        "case_inputs": {
            "querymulti": {
                "fields": [
                    {"label": "客户标识", "type": "input", "value": "A33060210021113"},
                    {"label": "开始日期", "type": "input", "value": "2025-06-10"},
                    {"label": "结束日期", "type": "input", "value": "2026-01-30"},
                ],
            }
        },
        "presets": {
            "customer_id": "A33060210021113",
            "in_client_no": "33060210021113",
            "ta_code": "WEBUI01",
            "prd_code": "cycleset01",
            "branch_no": "0011",
            "hq_branch_no": "100",
            "bank_no": "000",
        },
        "preset_strategy": {"base_tables": [], "biz_tables": ["tbdxfundtranscfm"]},
        "shard": {"physical_tables": {"tbdxfundtranscfm": ["tbdxfundtranscfm13"]}},
        "log_analysis": {
            "requirements": {
                "raw_sqls": [
                    "select * from tbdxfundtranscfm13 a,tbdxfundtranscfm13 b "
                    "where a.in_client_no=b.in_client_no and a.prd_code=b.prd_code "
                    "and a.busin_code in ('120','122','130','139') and b.busin_code in ('124','142') "
                    "and a.trans_date >= 20250610 and b.trans_date <= 20260130"
                ]
            }
        },
    }
    schema_cache = {
        "tbdxfundtranscfm13": {
            "required_columns": ["ta_code", "cfm_date", "cfm_no", "bank_no"],
            "all_columns": [
                "ta_code",
                "cfm_date",
                "cfm_no",
                "trans_date",
                "busin_code",
                "branch_no",
                "open_branch",
                "in_client_no",
                "client_type",
                "bank_no",
                "bank_acc",
                "prd_code",
            ],
        }
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)

    assert skipped == []
    assert len(plans[0].rows) == 4
    rows = [{column.name: column.value for column in row} for row in plans[0].rows]
    assert [row["busin_code"] for row in rows] == ["120", "124", "120", "124"]
    assert {row["trans_date"] for row in rows} == {"20250610", "20250620"}
    assert [row["open_branch"] for row in rows] == ["0011", "0011", "100", "100"]
    assert len({row["cfm_no"] for row in rows}) == 4


def test_business_insert_plan_keeps_execution_sql_unqualified_and_diagnostic_sql_qualified():
    case_plan = {
        "case_inputs": {
            "querymulti": {
                "fields": [
                    {"label": "客户标识", "type": "input", "value": "A1"},
                ],
            }
        },
        "preset_strategy": {"base_tables": [], "biz_tables": ["tbdxfundtranscfm"]},
        "shard": {
            "db": "ui_fund1",
            "physical_tables": {"tbdxfundtranscfm": ["tbdxfundtranscfm13"]},
        },
    }
    schema_cache = {
        "tbdxfundtranscfm13": {
            "required_columns": ["bank_acc", "cfm_no"],
            "all_columns": ["bank_acc", "cfm_no"],
        }
    }

    plans = build_insert_plans(
        case_plan,
        schema_cache=schema_cache,
        table_db_mapping={"tbdxfundtranscfm": "ui_fund"},
    )

    assert plans[0].db == "ui_fund1"
    sql = render_sql(plans, include_db_prefix=False)
    diagnostic_sql = render_sql(plans, include_db_prefix=True)
    assert "INSERT INTO tbdxfundtranscfm13" in sql
    assert "ui_fund1.tbdxfundtranscfm13" not in sql
    assert "ui_fund1.tbdxfundtranscfm13" in diagnostic_sql
    assert "ui_fund.tbdxfundtranscfm13" not in diagnostic_sql


def test_business_insert_plan_prefers_numbered_physical_table_over_observed_logical_alias():
    case_plan = {
        "case_inputs": {
            "querymulti": {
                "fields": [{"label": "客户标识", "type": "input", "value": "A33060210021113"}],
            }
        },
        "presets": {
            "customer_id": "A33060210021113",
            "in_client_no": "33060210021113",
            "ta_code": "HT",
            "prd_code": "UI_HT_P001",
        },
        "preset_strategy": {"base_tables": [], "biz_tables": ["tbdxtrusttransreq"]},
        "shard": {
            "db": "ui_trust2",
            "physical_tables": {
                "tbdxtrusttransreq": ["tbdxtrusttransreq13", "tbdxtrusttransreq"],
            },
        },
    }
    schema_cache = {
        "tbdxtrusttransreq13": {
            "required_columns": ["serial_no", "bank_acc"],
            "all_columns": ["serial_no", "bank_acc", "in_client_no", "ta_code", "prd_code"],
        },
        "tbdxtrusttransreq": {
            "required_columns": ["serial_no", "bank_acc"],
            "all_columns": ["serial_no", "bank_acc", "in_client_no", "ta_code", "prd_code"],
        },
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)

    assert skipped == []
    assert [plan.physical_table for plan in plans] == ["tbdxtrusttransreq13"]
    sql = render_sql(plans, include_db_prefix=False)
    assert "INSERT INTO tbdxtrusttransreq13" in sql
    assert "INSERT INTO tbdxtrusttransreq (" not in sql


def test_frozen_insert_plan_maps_freeze_fields_from_form_fields():
    case_plan = {
        "case_count": 7,
        "form_fields": {
            "all": [
                {"label": "冻结流水号", "type": "input", "value": "SQ20260610001"},
                {"label": "解冻流水号", "type": "input", "value": "SQ20260610002"},
                {"label": "客户标识", "type": "input", "value": "A33060210021113"},
                {"label": "产品代码", "type": "single-select", "value": "UI_HT_P001"},
                {"label": "冻结解冻状态", "type": "single-select", "value": "0:冻结申请中"},
                {"label": "冻结日期-结束", "type": "input", "value": "2026-06-10"},
                {"label": "确认日期-结束", "type": "input", "value": "2026-06-10"},
                {"label": "冻结原因", "type": "single-select", "value": "0:司法冻结"},
                {"label": "交易代码", "type": "single-select", "value": "100500:主机资金入账差错补处理"},
                {"label": "TA代码", "type": "single-select", "value": "HT"},
            ]
        },
        "case_inputs": {"branch": {"fields": []}, "headquarter": {"fields": []}},
        "presets": {
            "customer_id": "A33060210021113",
            "in_client_no": "33060210021113",
            "branch_no": "0011",
            "hq_branch_no": "100",
            "bank_no": "000",
        },
        "preset_strategy": {"base_tables": [], "biz_tables": ["tbdxtrustfrozen"]},
        "shard": {"db": "ui_trust2", "physical_tables": {"tbdxtrustfrozen": ["tbdxtrustfrozen"]}},
    }
    schema_cache = {
        "tbdxtrustfrozen": {
            "required_columns": ["frozen_serial"],
            "all_columns": [
                "frozen_serial",
                "unfrozen_serial",
                "bank_acc",
                "ta_code",
                "prd_code",
                "frozen_status",
                "frozen_date",
                "cfm_date",
                "frozen_cause",
                "trans_code",
                "frozen_branch",
            ],
        }
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)

    assert skipped == []
    assert len(plans[0].rows) == 2
    rows = [{column.name: column.value for column in row} for row in plans[0].rows]
    assert {row["frozen_serial"] for row in rows} == {"SQ20260610001", "SQ20260610002"}
    assert {row["unfrozen_serial"] for row in rows} == {"SQ20260610002"}
    assert {row["frozen_branch"] for row in rows} == {"0011", "100"}
    assert {row["frozen_date"] for row in rows} == {"20260610"}
    assert {row["cfm_date"] for row in rows} == {"20260610"}
    assert {row["frozen_cause"] for row in rows} == {"0"}
    assert {row["frozen_status"] for row in rows} == {"0"}
    assert {row["trans_code"] for row in rows} == {"100500"}


def test_elec_contract_insert_plan_generates_required_contract_no_and_permission_rows():
    case_plan = {
        "case_count": 7,
        "form_fields": {
            "all": [
                {"label": "customer_id", "type": "input", "value": "A33060210021113"},
                {"label": "product_code", "type": "single-select", "value": "UI_HT_P001"},
                {"label": "ta_code", "type": "single-select", "value": "HT"},
            ]
        },
        "case_inputs": {"branch": {"fields": []}, "headquarter": {"fields": []}},
        "presets": {
            "customer_id": "A33060210021113",
            "in_client_no": "33060210021113",
            "branch_no": "0011",
            "hq_branch_no": "100",
            "bank_no": "000",
            "prd_code": "UI_HT_P001",
            "ta_code": "HT",
        },
        "preset_strategy": {"base_tables": [], "biz_tables": ["tbdxtrusteleccontract"]},
        "shard": {"db": "ui_trust2", "physical_tables": {"tbdxtrusteleccontract": ["tbdxtrusteleccontract"]}},
    }
    schema_cache = {
        "tbdxtrusteleccontract": {
            "required_columns": ["contract_no"],
            "all_columns": [
                "contract_no",
                "in_client_no",
                "ta_code",
                "prd_code",
                "bank_no",
                "branch_no",
                "bank_acc",
            ],
            "column_types": {"contract_no": "varchar(32)"},
        }
    }

    plans, skipped = build_insert_plans(case_plan, schema_cache=schema_cache, include_skipped=True)

    assert skipped == []
    assert len(plans[0].rows) == 2
    rows = [{column.name: column.value for column in row} for row in plans[0].rows]
    assert {row["contract_no"] for row in rows} == {"UI_AUTO_0001", "UI_AUTO_0002"}
    assert {row["branch_no"] for row in rows} == {"0011", "100"}
    assert {row["bank_acc"] for row in rows} == {"A33060210021113"}
    assert {row["in_client_no"] for row in rows} == {"33060210021113"}
    assert plans[0].delete_where == "(contract_no = 'UI_AUTO_0001') OR (contract_no = 'UI_AUTO_0002')"


def test_insert_plan_propagates_join_key_values_from_left_table_to_join_table():
    case_plan = {
        "presets": {"prd_code": "UI_HT_P001:UI自动化HT信托产品"},
        "preset_strategy": {"base_tables": [], "biz_tables": ["tbdxtrustproduct", "tbprdmanager"]},
        "shard": {
            "physical_tables": {
                "tbdxtrustproduct": ["tbdxtrustproduct"],
                "tbprdmanager": ["tbprdmanager"],
            }
        },
        "log_analysis": {
            "requirements": {
                "join_key_columns": {
                    "tbdxtrustproduct": ["prd_manager"],
                    "tbprdmanager": ["prd_manager"],
                }
            }
        },
    }
    schema_cache = {
        "tbdxtrustproduct": {
            "required_columns": ["prd_code", "prd_manager"],
            "all_columns": ["prd_code", "prd_manager", "prd_name"],
        },
        "tbprdmanager": {
            "required_columns": ["prd_manager"],
            "all_columns": ["prd_manager", "manager_name"],
        },
    }
    base_tables = {
        "tbdxtrustproduct": {
            "records": {
                "ui": {
                    "prd_code": "UI_HT_P001",
                    "prd_manager": "MGR_UI",
                    "prd_name": "UI自动化HT信托产品",
                }
            }
        }
    }

    plans, skipped = build_insert_plans(
        case_plan,
        schema_cache=schema_cache,
        base_tables=base_tables,
        include_skipped=True,
    )

    assert skipped == []
    manager_plan = next(plan for plan in plans if plan.table == "tbprdmanager")
    values = {column.name: column for column in manager_plan.columns}
    assert values["prd_manager"].value == "MGR_UI"
    assert values["prd_manager"].source == "join_key.tbdxtrustproduct.prd_manager"


def test_insert_plan_uses_base_records_to_fill_trust_daily_required_columns():
    case_plan = {
        "presets": {
            "prd_code": "UI_HT_P001:UI自动化HT信托产品",
            "ta_code": "HT:HT基金",
            "bank_no": "000",
        },
        "preset_strategy": {
            "base_tables": ["tbdxtrustprddaily", "tbdxtrustannualizedrate"],
            "biz_tables": [],
        },
        "shard": {
            "physical_tables": {
                "tbdxtrustprddaily": ["tbdxtrustprddaily"],
                "tbdxtrustannualizedrate": ["tbdxtrustannualizedrate"],
            }
        },
    }
    schema_cache = {
        "tbdxtrustprddaily": {
            "required_columns": ["bank_no", "iss_date", "cfm_date", "prd_code", "nav_type"],
            "all_columns": ["bank_no", "iss_date", "cfm_date", "prd_code", "ta_code", "nav_type", "nav"],
        },
        "tbdxtrustannualizedrate": {
            "required_columns": ["bank_no", "ta_code", "prd_code", "iss_date"],
            "all_columns": ["bank_no", "ta_code", "prd_code", "iss_date", "week_clientrate"],
        },
    }
    base_tables = {
        "tbdxtrustprddaily": {
            "records": {
                "HT_UI_HT_P001": {
                    "bank_no": "000",
                    "iss_date": "20260401",
                    "cfm_date": "20260401",
                    "prd_code": "UI_HT_P001",
                    "ta_code": "HT",
                    "nav_type": "0",
                    "nav": "1.00000000",
                }
            }
        },
        "tbdxtrustannualizedrate": {
            "records": {
                "HT_UI_HT_P001": {
                    "bank_no": "000",
                    "ta_code": "HT",
                    "prd_code": "UI_HT_P001",
                    "iss_date": "20260401",
                    "week_clientrate": "0.01000000",
                }
            }
        },
    }

    plans, skipped = build_insert_plans(
        case_plan,
        schema_cache=schema_cache,
        base_tables=base_tables,
        include_skipped=True,
    )

    assert skipped == []
    assert {plan.table for plan in plans} == {"tbdxtrustprddaily", "tbdxtrustannualizedrate"}
    daily_values = {column.name: column.value for column in next(plan for plan in plans if plan.table == "tbdxtrustprddaily").columns}
    assert daily_values["iss_date"] == "20260401"
    assert daily_values["cfm_date"] == "20260401"
    assert daily_values["nav_type"] == "0"


def test_agent_base_tables_include_trust_market_required_reference_records():
    base_tables = load_base_tables(Path(__file__).resolve().parents[1] / "knowledge")

    daily = base_tables["tbdxtrustprddaily"]["records"]["HT_UI_HT_P001"]
    annualized = base_tables["tbdxtrustannualizedrate"]["records"]["HT_UI_HT_P001"]

    for column in ("bank_no", "iss_date", "cfm_date", "prd_code", "nav_type"):
        assert daily[column] != ""
    for column in ("bank_no", "ta_code", "prd_code", "iss_date"):
        assert annualized[column] != ""
