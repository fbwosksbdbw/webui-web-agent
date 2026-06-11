import json
import os

from hui_agent.llm import (
    LLMCaseTemplateReviewer,
    LLMPermissionAdvisor,
    LLMRepairAdvisor,
    LLMSQLAdvisor,
    OpenAICompatibleLLMClient,
    build_repair_advisor_from_env,
    load_env_file,
)


class FakeClient:
    def __init__(self):
        self.calls = []

    def complete_json(self, *, system: str, user: str) -> dict:
        self.calls.append({"system": system, "user": user})
        return {
            "failure_type": "preset_sql_mismatch",
            "confidence": 0.91,
            "summary": "SQL preset does not match query log",
            "actions": [{"type": "patch_sql"}],
        }


def test_llm_repair_advisor_sends_structured_evidence_and_returns_decision():
    client = FakeClient()
    advisor = LLMRepairAdvisor(client)
    evidence = {
        "pytest": {"stdout_tail": "assert cnt == 1", "stderr_tail": ""},
        "query_log_tail": "当前SQL: select * from t where prd_code=? 当前参数:UI_HT_P001",
        "presets": {"prd_code": "UI_HT_P001:UI自动化HT信托产品"},
        "sql_files": [{"path": "FUNC_A.sql", "content_tail": "VALUES ('11')"}],
    }

    decision = advisor.diagnose(evidence)

    assert decision["failure_type"] == "preset_sql_mismatch"
    assert decision["confidence"] == 0.91
    assert "只返回 JSON" in client.calls[0]["system"]
    sent = json.loads(client.calls[0]["user"])
    assert sent["query_log_tail"].startswith("当前SQL")
    assert sent["sql_files"][0]["path"] == "FUNC_A.sql"


def test_llm_permission_advisor_judges_branch_filter_from_structured_case_plan():
    class PermissionClient(FakeClient):
        def complete_json(self, *, system: str, user: str) -> dict:
            self.calls.append({"system": system, "user": user})
            return {
                "has_branch_permission_filter": True,
                "confidence": 0.88,
                "reason": "SQL uses branch_no as a permission filter",
                "evidence": ["where branch_no = ?"],
                "suggested_split_columns": ["branch_no"],
            }

    client = PermissionClient()
    advisor = LLMPermissionAdvisor(client)
    case_plan = {
        "menu": {"name": "账户类请求流水查询"},
        "log_analysis": {
            "requirements": {
                "where_columns": {"tbfundacctreq": ["branch_no", "bank_acc"]},
                "raw_sqls": ["select * from tbfundacctreq where branch_no=? and bank_acc=?"],
            }
        },
        "shard": {"physical_tables": {"tbfundacctreq": ["tbfundacctreq16"]}},
        "insert_plans": [
            {"table": "tbfundacctreq", "columns": [{"name": "branch_no", "value": "0011"}]},
        ],
    }

    decision = advisor.judge(case_plan)

    assert decision["has_branch_permission_filter"] is True
    assert decision["confidence"] == 0.88
    assert "权限过滤" in client.calls[0]["system"]
    sent = json.loads(client.calls[0]["user"])
    assert sent["menu"]["name"] == "账户类请求流水查询"
    assert "branch_no" in sent["log_analysis"]["requirements"]["where_columns"]["tbfundacctreq"]


def test_llm_sql_advisor_returns_validated_table_decision_with_safe_defaults():
    class SQLClient(FakeClient):
        def complete_json(self, *, system: str, user: str) -> dict:
            self.calls.append({"system": system, "user": user})
            return {
                "preset_tables": ["biztable"],
                "no_insert_tables": ["tsys_menu"],
                "base_tables": [],
                "join_key_columns": {"biztable": ["client_no"]},
                "risks": ["system table should not be preset"],
                "confidence": 0.83,
                "reason": "SQL contains one business table and one system table",
            }

    client = SQLClient()
    advisor = LLMSQLAdvisor(client)

    decision = advisor.judge({"menu": {"name": "菜单A"}, "log_analysis": {"requirements": {"raw_sqls": ["select"]}}})

    assert decision["preset_tables"] == ["biztable"]
    assert decision["no_insert_tables"] == ["tsys_menu"]
    assert decision["confidence"] == 0.83
    assert "SQL 预埋判断器" in client.calls[0]["system"]


def test_llm_template_reviewer_returns_blocking_issues_as_structured_output():
    class TemplateClient(FakeClient):
        def complete_json(self, *, system: str, user: str) -> dict:
            self.calls.append({"system": system, "user": user})
            return {
                "status": "failed",
                "block_generation": True,
                "summary": "reset case has weak assertion",
                "issues": [
                    {
                        "case_type": "reset",
                        "severity": "blocker",
                        "message": "reset does not assert field value",
                        "suggested_action": "regenerate_case",
                    }
                ],
            }

    client = TemplateClient()
    reviewer = LLMCaseTemplateReviewer(client)

    review = reviewer.review(
        {
            "menu": {"name": "菜单A"},
            "generated_tests": [{"case_type": "reset", "path": "test_reset.py", "content": "assert True"}],
        }
    )

    assert review["status"] == "failed"
    assert review["block_generation"] is True
    assert review["issues"][0]["severity"] == "blocker"
    assert "pytest 模板审查器" in client.calls[0]["system"]


def test_llm_repair_advisor_filters_actions_to_allowed_schema():
    class RepairClient(FakeClient):
        def complete_json(self, *, system: str, user: str) -> dict:
            self.calls.append({"system": system, "user": user})
            return {
                "failure_type": "assertion_mismatch",
                "confidence": 0.77,
                "summary": "case assertion is too strict",
                "evidence": ["pytest failed"],
                "actions": [
                    {"type": "regenerate_case", "case_type": "preview", "reason": "missing preview click"},
                    {"type": "regenerate_sql", "file": "preset.sql", "content": "INSERT INTO t(id) VALUES ('1');"},
                    {"type": "run_shell", "command": "del *"},
                ],
            }

    advisor = LLMRepairAdvisor(RepairClient())

    decision = advisor.diagnose({"pytest": {"stdout_tail": "failed"}})

    assert [action["type"] for action in decision["actions"]] == ["regenerate_case", "regenerate_sql"]
    assert decision["actions"][0]["case_type"] == "preview"


def test_llm_repair_advisor_receives_generated_test_source():
    class RepairClient(FakeClient):
        def complete_json(self, *, system: str, user: str) -> dict:
            self.calls.append({"system": system, "user": user})
            return {"failure_type": "assertion_mismatch", "confidence": 0.8, "summary": "bad assert", "actions": []}

    client = RepairClient()
    advisor = LLMRepairAdvisor(client)

    advisor.diagnose(
        {
            "generated_tests": [
                {
                    "path": "test_preview.py",
                    "case_type": "preview",
                    "is_failed": True,
                    "content_tail": "def test_preview():\n    assert True\n",
                }
            ]
        }
    )

    payload = json.loads(client.calls[0]["user"])
    assert payload["generated_tests"][0]["is_failed"] is True
    assert "assert True" in payload["generated_tests"][0]["content_tail"]


def test_llm_repair_prompt_allows_regenerate_sql_content_without_conflict():
    client = FakeClient()
    advisor = LLMRepairAdvisor(client)

    advisor.diagnose({"sql_files": [{"path": "preset.sql", "content_tail": "INSERT INTO t VALUES ('1');"}]})

    system = client.calls[0]["system"]
    assert "不要直接输出 SQL 文件全文" not in system
    assert "regenerate_sql 必须返回完整 SQL content" in system
    assert "只有 regenerate_sql.content" in system


def test_openai_compatible_client_is_disabled_without_api_key(monkeypatch):
    monkeypatch.delenv("HUI_LLM_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    assert OpenAICompatibleLLMClient.from_env() is None
    assert build_repair_advisor_from_env() is None


def test_openai_compatible_client_uses_configurable_timeout(monkeypatch):
    monkeypatch.setenv("HUI_LLM_API_KEY", "key")
    monkeypatch.setenv("HUI_LLM_MODEL", "model")
    monkeypatch.setenv("HUI_LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("HUI_LLM_TIMEOUT", "240")

    client = OpenAICompatibleLLMClient.from_env()

    assert client is not None
    assert client.timeout == 240


def test_load_env_file_sets_missing_values_without_overriding_existing(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "HUI_LLM_API_KEY=file-key\n"
        "HUI_LLM_MODEL=file-model\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("HUI_LLM_API_KEY", raising=False)
    monkeypatch.setenv("HUI_LLM_MODEL", "existing-model")

    load_env_file(env_file)

    assert os.getenv("HUI_LLM_API_KEY") == "file-key"
    assert os.getenv("HUI_LLM_MODEL") == "existing-model"
