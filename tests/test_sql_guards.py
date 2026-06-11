from hui_agent.sql_guards import evaluate_insert_plan_coverage


def test_user_branch_permission_table_does_not_fail_coverage_when_unclassified():
    case_plan = {
        "preset_strategy": {"base_tables": ["tbaccreq"], "biz_tables": [], "no_insert_tables": []},
        "shard": {"physical_tables": {"tbaccreq": ["tbaccreq"]}},
        "log_analysis": {
            "requirements": {
                "where_columns": {
                    "tbaccreq": ["bank_acc", "branch_no"],
                    "tbuserbranch": ["user_id"],
                },
                "join_key_columns": {},
                "join_tables": [],
            }
        },
    }

    class Plan:
        table = "tbaccreq"
        physical_table = "tbaccreq"

    result = evaluate_insert_plan_coverage(case_plan, [Plan()], [])

    assert result["status"] == "passed"
    assert result["observed_missing_strategy"] == []


def test_workflow_engine_tables_do_not_fail_coverage_when_unclassified():
    case_plan = {
        "preset_strategy": {"base_tables": ["tbaccreq"], "biz_tables": [], "no_insert_tables": []},
        "shard": {"physical_tables": {"tbaccreq": ["tbaccreq"]}},
        "log_analysis": {
            "requirements": {
                "where_columns": {
                    "tbaccreq": ["bank_acc", "branch_no"],
                    "tbworkflowsubtrans": ["serial_no"],
                    "ACT_RU_TASK": ["PROC_INST_ID_"],
                    "ACT_RU_EXECUTION": ["ID_"],
                    "flow_instance": ["flow_id"],
                    "flow_cust_nodecfg": ["node_id"],
                },
                "join_key_columns": {},
                "join_tables": [],
            }
        },
    }

    class Plan:
        table = "tbaccreq"
        physical_table = "tbaccreq"

    result = evaluate_insert_plan_coverage(case_plan, [Plan()], [])

    assert result["status"] == "passed"
    assert result["observed_missing_strategy"] == []


def test_coverage_prefers_numbered_physical_table_over_observed_logical_alias():
    case_plan = {
        "preset_strategy": {"base_tables": [], "biz_tables": ["tbdxtrusttransreq"], "no_insert_tables": []},
        "shard": {
            "physical_tables": {
                "tbdxtrusttransreq": ["tbdxtrusttransreq13", "tbdxtrusttransreq"],
            }
        },
        "log_analysis": {"requirements": {"where_columns": {"tbdxtrusttransreq": ["bank_acc"]}}},
    }

    class Plan:
        table = "tbdxtrusttransreq"
        physical_table = "tbdxtrusttransreq13"

    result = evaluate_insert_plan_coverage(case_plan, [Plan()], [])

    assert result["status"] == "passed"
    assert result["missing_expected_tables"] == []
