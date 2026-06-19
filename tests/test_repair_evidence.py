import json
from pathlib import Path

from hui_agent.repair_evidence import collect_repair_evidence


class Completed:
    returncode = 1
    stdout = "pytest stdout"
    stderr = "pytest stderr"


class FailedPreviewCompleted:
    returncode = 1
    stdout = """
=========================== short test summary info ===========================
FAILED tests/test_FUNC_preview.py::test_FUNC_preview - AssertionError
PASSED tests/test_FUNC_queryall.py::test_FUNC_queryall
============= 1 failed, 1 passed in 1.00s =============
"""
    stderr = ""


def test_collect_repair_evidence_includes_pytest_log_sql_and_presets(tmp_path):
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    case_dir.mkdir()
    sql_dir.mkdir()
    (case_dir / "query_log.txt").write_text("当前SQL: select * from t where prd_code=?", encoding="utf-8")
    preset_sql = sql_dir / "FUNC_A.sql"
    preset_sql.write_text("INSERT INTO t (prd_code) VALUES ('11');", encoding="utf-8")
    case_plan = {
        "menu": {"name": "菜单A", "code": "FUNC_A"},
        "presets": {"prd_code": "UI_HT_P001:UI自动化HT信托产品"},
        "sql_paths": {"preset_sql": str(preset_sql)},
        "repair_attempts": [
            {
                "attempt": 1,
                "status": "patched",
                "actions": [{"type": "llm_regenerate_case", "status": "patched", "file": "test_preview.py"}],
                "restore": {
                    "status": "restored",
                    "reason": "target pytest failed",
                    "files": ["test_preview.py"],
                    "case_plan_keys": ["insert_plans"],
                },
                "diff": {
                    "files_changed": ["test_preview.py"],
                    "case_plan_keys_changed": ["insert_plans"],
                    "file_details": [
                        {
                            "file": "test_preview.py",
                            "old_exists": True,
                            "new_exists": True,
                            "old_size": 10,
                            "new_size": 20,
                        }
                    ],
                },
                "verification": {"kind": "pytest", "phase": "target", "returncode": 1, "stdout_tail": "x" * 2000},
                "llm_decision": {"failure_type": "assertion_mismatch", "confidence": 0.8, "summary": "bad assertion"},
            }
        ],
    }

    evidence = collect_repair_evidence(case_plan, case_dir, 2, Completed())

    assert evidence["attempt"] == 2
    assert evidence["menu"]["name"] == "菜单A"
    assert evidence["pytest"]["stdout_tail"] == "pytest stdout"
    assert evidence["query_log_tail"].startswith("当前SQL")
    assert evidence["presets"]["prd_code"] == "UI_HT_P001:UI自动化HT信托产品"
    assert evidence["sql_files"][0]["path"] == str(preset_sql)
    assert "VALUES ('11')" in evidence["sql_files"][0]["content_tail"]
    previous = evidence["previous_repair_attempts"][0]
    assert previous["status"] == "patched"
    assert previous["actions"][0]["type"] == "llm_regenerate_case"
    assert previous["restore"]["status"] == "restored"
    assert previous["restore"]["files"] == ["test_preview.py"]
    assert previous["diff"]["case_plan_keys_changed"] == ["insert_plans"]
    assert previous["validation"]["phase"] == "target"
    assert len(previous["validation"]["stdout_tail"]) == 800
    assert previous["llm"]["summary"] == "bad assertion"


def test_collect_repair_evidence_is_json_serializable(tmp_path):
    case_dir = tmp_path / "case"
    case_dir.mkdir()

    evidence = collect_repair_evidence({}, case_dir, 1, Completed())

    json.dumps(evidence, ensure_ascii=False)


def test_collect_repair_evidence_joins_logs_by_source(tmp_path):
    case_dir = tmp_path / "case"
    (case_dir / "sql_probe").mkdir(parents=True)
    (case_dir / "repair_logs" / "attempt_02").mkdir(parents=True)
    (case_dir / "query_log.txt").write_text("main query log", encoding="utf-8")
    (case_dir / "sql_probe" / "log_01.txt").write_text("probe one", encoding="utf-8")
    (case_dir / "sql_probe" / "log_02.txt").write_text("probe two", encoding="utf-8")
    (case_dir / "repair_logs" / "attempt_02" / "query_rerun_log.txt").write_text("rerun log", encoding="utf-8")

    evidence = collect_repair_evidence({}, case_dir, 2, Completed())

    assert evidence["logs"]["main_query"] == "main query log"
    assert "=== sql_probe/log_01.txt ===" in evidence["logs"]["sql_probe_joined"]
    assert "probe one" in evidence["logs"]["sql_probe_joined"]
    assert "=== sql_probe/log_02.txt ===" in evidence["logs"]["sql_probe_joined"]
    assert "=== repair_logs/attempt_02/query_rerun_log.txt ===" in evidence["logs"]["repair_attempt"]
    assert "rerun log" in evidence["logs"]["repair_attempt"]
    assert evidence["query_log_tail"] == "main query log"


def test_collect_repair_evidence_includes_failed_test_source(tmp_path):
    case_dir = tmp_path / "case"
    tests_dir = tmp_path / "tests"
    case_dir.mkdir()
    tests_dir.mkdir()
    preview = tests_dir / "test_FUNC_preview.py"
    queryall = tests_dir / "test_FUNC_queryall.py"
    preview.write_text("def test_FUNC_preview():\n    assert True\n", encoding="utf-8")
    queryall.write_text("def test_FUNC_queryall():\n    assert len([1]) == 1\n", encoding="utf-8")
    case_plan = {
        "generated_tests": [str(preview), str(queryall)],
    }

    evidence = collect_repair_evidence(case_plan, case_dir, 1, FailedPreviewCompleted())

    assert evidence["pytest"]["failed_nodeids"] == ["tests/test_FUNC_preview.py::test_FUNC_preview"]
    assert len(evidence["generated_tests"]) == 2
    failed = [item for item in evidence["generated_tests"] if item["is_failed"]]
    assert failed[0]["path"] == str(preview)
    assert "assert True" in failed[0]["content_tail"]
