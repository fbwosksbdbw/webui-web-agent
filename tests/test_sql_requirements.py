from hui_agent.sql_requirements import build_sql_requirements


def test_sql_requirements_classifies_match_join_and_schema_required_columns():
    case_plan = {
        "form_fields": {
            "all": [
                {"label": "客户编号", "value": "A33060210021113"},
                {"label": "产品代码", "value": "UI_HT_P001"},
            ]
        },
        "presets": {
            "ta_code": "HT",
            "bank_no": "000",
        },
        "preset_strategy": {
            "base_tables": [],
            "biz_tables": ["biztable", "jointable"],
        },
        "shard": {
            "physical_tables": {
                "biztable": ["biztable13"],
                "jointable": ["jointable"],
            }
        },
        "log_analysis": {
            "requirements": {
                "where_columns": {
                    "biztable": ["bank_acc", "prd_code", "ta_code"],
                },
                "join_key_columns": {
                    "biztable": ["manager_id"],
                    "jointable": ["manager_id"],
                },
                "raw_sqls": [
                    (
                        "select * from biztable b join jointable j on b.manager_id=j.manager_id "
                        "where b.prd_code = ? and b.ta_code='HT' 当前参数:UI_HT_P001"
                    )
                ],
            }
        },
    }
    schema_cache = {
        "biztable13": {
            "required_columns": ["bank_acc", "serial_no"],
            "all_columns": ["bank_acc", "prd_code", "ta_code", "manager_id", "serial_no"],
        },
        "jointable": {
            "required_columns": ["manager_id", "manager_name"],
            "all_columns": ["manager_id", "manager_name"],
        },
    }

    requirements = build_sql_requirements(case_plan, schema_cache=schema_cache)

    by_table = {item.table: item for item in requirements}
    assert by_table["biztable"].physical_tables == ["biztable13"]
    biz_columns = {column.column: column for column in by_table["biztable"].columns}
    assert biz_columns["bank_acc"].purpose == "required_match"
    assert biz_columns["bank_acc"].expected_value == "A33060210021113"
    assert biz_columns["bank_acc"].source == "form_fields.客户编号"
    assert not biz_columns["bank_acc"].allow_default
    assert biz_columns["prd_code"].expected_value == "UI_HT_P001"
    assert biz_columns["prd_code"].source == "log_analysis.literal.prd_code"
    assert biz_columns["ta_code"].expected_value == "HT"
    assert biz_columns["manager_id"].purpose == "join_key"
    assert not biz_columns["manager_id"].allow_default
    assert biz_columns["serial_no"].purpose == "schema_required"
    assert biz_columns["serial_no"].allow_default

    join_columns = {column.column: column for column in by_table["jointable"].columns}
    assert join_columns["manager_id"].purpose == "join_key"
    assert not join_columns["manager_id"].allow_default
    assert join_columns["manager_name"].purpose == "schema_required"
    assert join_columns["manager_name"].allow_default


def test_sql_requirements_can_be_serialized_to_case_plan_shape():
    case_plan = {
        "presets": {"customer_id": "A1"},
        "preset_strategy": {"base_tables": [], "biz_tables": ["biztable"]},
        "shard": {"physical_tables": {"biztable": ["biztable"]}},
        "log_analysis": {"requirements": {"where_columns": {"biztable": ["bank_acc"]}}},
    }
    schema_cache = {
        "biztable": {
            "required_columns": ["bank_acc"],
            "all_columns": ["bank_acc"],
        }
    }

    requirements = build_sql_requirements(case_plan, schema_cache=schema_cache)

    assert [item.to_dict() for item in requirements] == [
        {
            "table": "biztable",
            "physical_tables": ["biztable"],
            "role": "business",
            "columns": [
                {
                    "column": "bank_acc",
                    "purpose": "required_match",
                    "expected_value": "A1",
                    "source": "presets.customer_id",
                    "allow_default": False,
                    "reason": "where_columns",
                }
            ],
            "source_sql": [],
        }
    ]


def test_sql_requirements_use_in_predicate_literals_as_trusted_values():
    case_plan = {
        "preset_strategy": {"base_tables": ["tbdxfundtranscfmqry"], "biz_tables": []},
        "shard": {"physical_tables": {"tbdxfundtranscfmqry": ["tbdxfundtranscfmqry"]}},
        "log_analysis": {
            "requirements": {
                "where_columns": {"tbdxfundtranscfmqry": ["busin_code", "prd_code"]},
                "raw_sqls": [
                    "select * from tbdxfundtranscfmqry a,tbdxfundtranscfmqry b "
                    "where a.busin_code in ('120','122','130','139') "
                    "and b.busin_code in ('124','142') "
                    "and a.prd_code='cycleset01'"
                ],
            }
        },
    }
    schema_cache = {
        "tbdxfundtranscfmqry": {
            "required_columns": [],
            "all_columns": ["busin_code", "prd_code"],
        }
    }

    requirements = build_sql_requirements(case_plan, schema_cache=schema_cache)

    columns = {column.column: column for column in requirements[0].columns}
    assert columns["busin_code"].expected_value == "120"
    assert columns["busin_code"].source == "log_analysis.literal.busin_code"
    assert columns["prd_code"].expected_value == "cycleset01"
