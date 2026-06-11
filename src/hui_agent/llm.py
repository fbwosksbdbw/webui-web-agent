from __future__ import annotations

import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field, ValidationError, field_validator


class JSONLLMClient(Protocol):
    def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        ...


class RepairAction(BaseModel):
    type: Literal["patch_sql", "regenerate_sql", "regenerate_case", "mark_unfixable"]
    file: str = ""
    case_type: str = ""
    reason: str = ""
    old_text: str = ""
    new_text: str = ""
    content: str = ""
    changes: list[dict[str, Any]] = Field(default_factory=list)


class RepairDecision(BaseModel):
    failure_type: Literal[
        "preset_sql_mismatch",
        "preset_sql_execution_failed",
        "database_data_missing",
        "case_input_mismatch",
        "assertion_mismatch",
        "page_or_environment_error",
        "unknown",
    ] = "unknown"
    confidence: float = 0
    summary: str = ""
    evidence: list[str] = Field(default_factory=list)
    actions: list[RepairAction] = Field(default_factory=list)

    @field_validator("actions", mode="before")
    @classmethod
    def _filter_allowed_actions(cls, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        allowed = {"patch_sql", "regenerate_sql", "regenerate_case", "mark_unfixable"}
        return [
            item
            for item in value
            if isinstance(item, dict) and item.get("type") in allowed
        ]


class PermissionDecision(BaseModel):
    has_branch_permission_filter: bool = False
    confidence: float = 0
    reason: str = ""
    evidence: list[str] = Field(default_factory=list)
    suggested_split_columns: list[str] = Field(default_factory=list)


class SQLDecision(BaseModel):
    preset_tables: list[str] = Field(default_factory=list)
    base_tables: list[str] = Field(default_factory=list)
    no_insert_tables: list[str] = Field(default_factory=list)
    join_key_columns: dict[str, list[str]] = Field(default_factory=dict)
    risks: list[str] = Field(default_factory=list)
    confidence: float = 0
    reason: str = ""


class TemplateIssue(BaseModel):
    case_type: str = ""
    severity: Literal["info", "warning", "blocker"] = "warning"
    message: str = ""
    suggested_action: Literal["none", "regenerate_case", "manual_review"] = "none"


class TemplateReviewDecision(BaseModel):
    status: Literal["passed", "failed"] = "passed"
    block_generation: bool = False
    summary: str = ""
    issues: list[TemplateIssue] = Field(default_factory=list)


class StructuredOutputRunnable:
    def __init__(self, client: JSONLLMClient, *, system: str, schema: type[BaseModel]) -> None:
        self.client = client
        self.system = system
        self.schema = schema

    def invoke(self, evidence: dict[str, Any]) -> dict[str, Any]:
        raw = self.client.complete_json(
            system=self.system,
            user=json.dumps(evidence, ensure_ascii=False, indent=2),
        )
        try:
            model = self.schema.model_validate(raw)
        except ValidationError:
            model = self.schema()
        return model.model_dump()


@dataclass(frozen=True)
class OpenAICompatibleLLMClient:
    api_key: str
    model: str
    base_url: str = "https://api.openai.com/v1"
    timeout: int = 180

    @classmethod
    def from_env(cls, model: str | None = None) -> "OpenAICompatibleLLMClient | None":
        api_key = os.getenv("HUI_LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not api_key:
            return None
        return cls(
            api_key=api_key,
            model=model or os.getenv("HUI_LLM_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4.1",
            base_url=(os.getenv("HUI_LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1").rstrip("/"),
            timeout=_env_int("HUI_LLM_TIMEOUT", _env_int("OPENAI_TIMEOUT", 180)),
        )

    def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        return json.loads(content)


class LLMRepairAdvisor:
    def __init__(self, client: JSONLLMClient) -> None:
        self.runnable = StructuredOutputRunnable(client, system=_REPAIR_SYSTEM_PROMPT, schema=RepairDecision)

    def diagnose(self, evidence: dict[str, Any]) -> dict[str, Any]:
        return self.runnable.invoke(_compact_evidence(evidence))


class LLMPermissionAdvisor:
    def __init__(self, client: JSONLLMClient) -> None:
        self.runnable = StructuredOutputRunnable(client, system=_PERMISSION_SYSTEM_PROMPT, schema=PermissionDecision)

    def judge(self, case_plan: dict[str, Any]) -> dict[str, Any]:
        return self.runnable.invoke(_compact_permission_case_plan(case_plan))


class LLMSQLAdvisor:
    def __init__(self, client: JSONLLMClient) -> None:
        self.runnable = StructuredOutputRunnable(client, system=_SQL_SYSTEM_PROMPT, schema=SQLDecision)

    def judge(self, case_plan: dict[str, Any]) -> dict[str, Any]:
        return self.runnable.invoke(_compact_sql_case_plan(case_plan))


class LLMCaseTemplateReviewer:
    def __init__(self, client: JSONLLMClient) -> None:
        self.runnable = StructuredOutputRunnable(client, system=_TEMPLATE_REVIEW_SYSTEM_PROMPT, schema=TemplateReviewDecision)

    def review(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.runnable.invoke(_compact_template_review_payload(payload))


def build_repair_advisor_from_env(model: str | None = None) -> LLMRepairAdvisor | None:
    client = OpenAICompatibleLLMClient.from_env(model=model)
    return LLMRepairAdvisor(client) if client else None


def build_permission_advisor_from_env(model: str | None = None) -> LLMPermissionAdvisor | None:
    client = OpenAICompatibleLLMClient.from_env(model=model)
    return LLMPermissionAdvisor(client) if client else None


def build_sql_advisor_from_env(model: str | None = None) -> LLMSQLAdvisor | None:
    client = OpenAICompatibleLLMClient.from_env(model=model)
    return LLMSQLAdvisor(client) if client else None


def build_template_reviewer_from_env(model: str | None = None) -> LLMCaseTemplateReviewer | None:
    client = OpenAICompatibleLLMClient.from_env(model=model)
    return LLMCaseTemplateReviewer(client) if client else None


def load_env_file(path: str | Path) -> None:
    env_path = Path(path)
    if not env_path.exists() or not env_path.is_file():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = _strip_env_quotes(value.strip())
        if key and key not in os.environ:
            os.environ[key] = value


def _strip_env_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


def _compact_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    compact = dict(evidence)
    compact["query_log_tail"] = str(compact.get("query_log_tail", ""))[-6000:]
    compact["previous_repair_attempts"] = compact.get("previous_repair_attempts", [])[-3:]
    compact["sql_files"] = [
        {
            **item,
            "content_tail": str(item.get("content_tail", ""))[-8000:],
        }
        for item in compact.get("sql_files", [])
        if isinstance(item, dict)
    ]
    compact["generated_tests"] = [
        {
            **item,
            "content_tail": str(item.get("content_tail", ""))[-12000:],
        }
        for item in compact.get("generated_tests", [])
        if isinstance(item, dict)
    ][:12]
    return compact


def _compact_permission_case_plan(case_plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "menu": case_plan.get("menu", {}),
        "form_fields": {
            "all": [
                {
                    "label": field.get("label"),
                    "type": field.get("type"),
                    "is_required": field.get("is_required"),
                }
                for field in case_plan.get("form_fields", {}).get("all", [])
                if isinstance(field, dict)
            ][:80],
        },
        "log_analysis": {
            "requirements": case_plan.get("log_analysis", {}).get("requirements", {}),
            "filtered_system_tables": case_plan.get("log_analysis", {}).get("filtered_system_tables", []),
        },
        "preset_strategy": case_plan.get("preset_strategy", {}),
        "shard": case_plan.get("shard", {}),
        "insert_plans": [
            {
                "table": plan.get("table"),
                "physical_table": plan.get("physical_table"),
                "role": plan.get("role"),
                "columns": [
                    column.get("name")
                    for column in plan.get("columns", [])
                    if isinstance(column, dict)
                ],
                "has_permission_rows": bool(plan.get("rows")),
            }
            for plan in case_plan.get("insert_plans", [])
            if isinstance(plan, dict)
        ][:40],
        "insert_plan_skipped": case_plan.get("insert_plan_skipped", [])[:40],
    }


def _compact_sql_case_plan(case_plan: dict[str, Any]) -> dict[str, Any]:
    return {
        "menu": case_plan.get("menu", {}),
        "log_analysis": case_plan.get("log_analysis", {}),
        "preset_strategy": case_plan.get("preset_strategy", {}),
        "shard": case_plan.get("shard", {}),
        "case_inputs": case_plan.get("case_inputs", {}),
        "insert_plan_skipped": case_plan.get("insert_plan_skipped", [])[:60],
    }


def _compact_template_review_payload(payload: dict[str, Any]) -> dict[str, Any]:
    tests = []
    for item in payload.get("generated_tests", []):
        if not isinstance(item, dict):
            continue
        tests.append(
            {
                "case_type": item.get("case_type", ""),
                "path": item.get("path", ""),
                "content_tail": str(item.get("content", ""))[-12000:],
            }
        )
    return {
        "menu": payload.get("menu", {}),
        "case_inputs": payload.get("case_inputs", {}),
        "expected_counts": payload.get("expected_counts", {}),
        "generated_tests": tests[:12],
    }


_REPAIR_SYSTEM_PROMPT = """你是 HUI Playwright 自动化测试修复诊断器。
根据证据判断 pytest 失败原因，并只返回 JSON。
不要在 summary/evidence 等说明字段中粘贴 SQL 文件全文；只有 regenerate_sql.content 字段允许返回完整 SQL 文件内容。
不要编造数据库结果。
你必须优先输出可执行修复动作；无法给出可执行补丁时，返回 mark_unfixable 并说明缺少什么证据。

🔴 诊断流程（必须按顺序交叉验证）：
1. 看 pytest 失败信息 → 确定哪个断言失败、期望值和实际值
2. 看生成的用例代码 → 确定用例的查询条件和断言逻辑
3. 看预埋 SQL 文件 → 确定 INSERT 了什么数据
4. 看 db_snapshot（数据库实际数据）→ 确认数据是否真的写入了、值是否正确
5. 看 query_log（后端 SQL 日志）→ 确认后端实际执行了什么查询
6. 交叉对比：用例断言 vs SQL 预埋 vs 数据库实际数据 vs 后端查询，找到不一致的环节

🔴 重要约束：
- table_schemas 包含表的实际列名，SQL 中的列名必须来自 table_schemas.all_columns
- db_snapshot 包含预埋后数据库的实际数据，用它验证 INSERT 是否生效
- 如果 db_snapshot 中表为空或数据不对，说明预埋 SQL 有问题
- 如果 db_snapshot 数据正确但 pytest 断言失败，说明用例代码或查询条件有问题
- patch_sql 只有在 old/new 能命中 SQL 文件内容时才会被执行
- regenerate_sql 必须返回完整 SQL content，只能包含 DELETE/INSERT/COMMIT 等 DML
- regenerate_case 必须返回完整 pytest 文件 content；禁止 assert True

允许的 failure_type 包括:
- preset_sql_mismatch
- preset_sql_execution_failed
- database_data_missing
- case_input_mismatch
- assertion_mismatch
- page_or_environment_error
- unknown

返回格式:
{
  "failure_type": "preset_sql_mismatch",
  "confidence": 0.0,
  "summary": "一句话说明",
  "evidence": ["证据1", "证据2"],
    "actions": [
    {
      "type": "patch_sql",
      "file": "相对或绝对 SQL 文件路径",
      "reason": "为什么要改",
      "old_text": "SQL 文件中必须存在的原文本，适合整段替换时填写",
      "new_text": "替换后的文本",
      "changes": [
        {"table": "表名", "column": "字段", "old": "旧值", "new": "新值"}
      ]
    },
    {
      "type": "regenerate_sql",
      "file": "相对或绝对 SQL 文件路径",
      "reason": "为什么要重写 SQL",
      "content": "完整 SQL 文件内容，必须可 dry-run 通过，禁止 DROP/ALTER/TRUNCATE 等结构或权限操作"
    },
    {
      "type": "regenerate_case",
      "file": "相对或绝对 pytest 文件路径",
      "case_type": "queryall/querysingle/querymulti/reset/preview/branch/headquarter",
      "reason": "为什么要重写",
      "content": "完整 pytest 文件内容，必须是可运行代码，禁止 assert True"
    }
  ]
}
"""


_PERMISSION_SYSTEM_PROMPT = """你是 HUI 菜单 SQL 权限过滤判断器。
根据菜单、表结构、SQL where/join 字段和预埋计划，判断这个菜单是否存在“分行/总行权限过滤”。
只返回 JSON，不要编造数据库结果。

判断为 true 的典型证据：
- SQL where/join 中出现 branch_no、open_branch、org_no、bank_no、机构、分支机构等权限字段；
- 表结构和预埋计划有可区分分行/总行的字段；
- 菜单语义是流水、请求、账户、客户等会按机构隔离的数据查询。

判断为 false 的典型证据：
- 只是普通产品、字典、系统参数或无机构过滤的基础信息查询；
- 只有产品/TA/日期/客户条件，没有任何机构权限字段；
- 没有可生成分行/总行差异数据的字段。

返回格式:
{
  "has_branch_permission_filter": true,
  "confidence": 0.0,
  "reason": "一句话说明",
  "evidence": ["证据1", "证据2"],
  "suggested_split_columns": ["branch_no"]
}
"""


_SQL_SYSTEM_PROMPT = """你是 HUI SQL 预埋判断器。
根据 SQL 分析、表分类、分片映射、case_inputs 和跳过原因，判断预埋策略是否可靠。
只返回 JSON，不要编造数据库结果，不要输出 SQL 全文。

返回格式:
{
  "preset_tables": ["业务表"],
  "base_tables": ["公共基础表"],
  "no_insert_tables": ["系统表或不应预埋表"],
  "join_key_columns": {"表名": ["字段"]},
  "risks": ["风险说明"],
  "confidence": 0.0,
  "reason": "一句话说明"
}
"""


_TEMPLATE_REVIEW_SYSTEM_PROMPT = """你是 HUI pytest 模板审查器。
审查生成用例是否符合模板和可验证性规则，只返回 JSON。
必须重点检查:
- 禁止 assert True 或无意义断言；
- reset 必须先填条件，再点击重置，并断言控件实际清空；
- preview 必须查询出数据后点击详情/预览/查看，并断言详情内容；
- 分行/总行必须使用精确记录数断言；
- SQL 路径必须来自 conf.config.Config，不得硬编码本机绝对路径；
- 下拉字段必须调用 common.helpers 的安全选择函数。

返回格式:
{
  "status": "passed",
  "block_generation": false,
  "summary": "一句话说明",
  "issues": [
    {
      "case_type": "reset",
      "severity": "blocker",
      "message": "问题说明",
      "suggested_action": "regenerate_case"
    }
  ]
}
"""
