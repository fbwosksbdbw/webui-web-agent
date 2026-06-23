# HUI Playwright Agent 完整架构文档

> 本文档面向完全不了解本项目的读者，从零开始解释这个 Agent 是什么、为什么要做、怎么做、每个模块干什么。

---

## 目录

1. [这个项目要解决什么问题](#1-这个项目要解决什么问题)
2. [核心思路：怎么解决](#2-核心思路怎么解决)
3. [整体架构图](#3-整体架构图)
4. [11 个步骤详解](#4-11-个步骤详解)
5. [核心模块逐一拆解](#5-核心模块逐一拆解)
6. [数据流：数据怎么在模块之间流动](#6-数据流数据怎么在模块之间流动)
7. [关键数据结构](#7-关键数据结构)
8. [LLM 在哪里介入](#8-llm-在哪里介入)
9. [外部依赖：Agent 调用了哪些外部工具](#9-外部依赖agent-调用了哪些外部工具)
10. [CLI 命令怎么用](#10-cli-命令怎么用)
11. [运行日志和排障](#11-运行日志和排障)
12. [当前存在的问题](#12-当前存在的问题)
13. [改进方案](#13-改进方案)

---

## 1. 这个项目要解决什么问题

### 1.1 背景

有一个银行理财平台的后台管理系统（叫 HUI），它有**几百个菜单页面**。每个菜单页面通常长这样：

```
┌─────────────────────────────────────────────────┐
│  [产品代码] [下拉选择]   [TA代码] [下拉选择]      │
│  [客户标识类型] [下拉]   [客户标识] [输入框]      │
│  [开始日期] [日期选择]   [结束日期] [日期选择]    │
│                                    [查询] [重置]  │
├─────────────────────────────────────────────────┤
│  查询结果表格（分页显示）                         │
│  ┌──────┬──────┬──────┬──────┐                  │
│  │ 列1  │ 列2  │ 列3  │ 详情 │                  │
│  ├──────┼──────┼──────┼──────┤                  │
│  │ ...  │ ...  │ ...  │ [查看]│                  │
│  └──────┴──────┴──────┴──────┘                  │
└─────────────────────────────────────────────────┘
```

测试团队需要为每个菜单编写自动化测试用例，验证：
- 全量查询（不填条件，点查询）能不能出结果
- 单条件查询（只填一个字段）能不能正确过滤
- 多条件查询（填多个字段）能不能正确过滤
- 重置按钮能不能清空所有输入
- 点详情/预览能不能弹出详情面板
- 分行账号和总行账号看到的数据是否不同（权限隔离）

### 1.2 手动做这件事有多痛苦

假设一个菜单需要 5 个测试用例，300 个菜单就是 1500 个用例。每个用例需要：
1. 打开浏览器，登录，导航到菜单
2. 填写表单字段
3. 点查询
4. 检查结果
5. 写断言代码
6. 还要准备测试数据（SQL 预埋）

手动做这些事情，一个熟练工程师一个菜单可能需要 1-2 小时。300 个菜单就是几百人天。

### 1.3 Agent 的目标

**自动化完成上述全部工作**：从"打开菜单页面"到"生成可运行的 pytest 测试用例"，全程自动。

---

## 2. 核心思路：怎么解决

### 2.1 设计哲学

本项目采用 **"确定性 workflow + 少量 LLM 决策"** 的设计：

- **确定性部分**（占 90%+）：用 Python 脚本和状态机控制主流程。浏览器操作、SQL 解析、代码生成、pytest 运行都是确定性的——给定相同输入，产出相同结果。
- **LLM 部分**（占 ~10%）：只在需要"推理判断"的场景使用 LLM（大语言模型，如 GPT-4）。比如：
  - 这个 SQL 报错了，怎么修？
  - 这个菜单有没有分行/总行权限过滤？
  - 生成的 pytest 代码有没有写 `assert True` 这种无效断言？

### 2.2 类比理解

可以把这个 Agent 想象成一条**工厂流水线**：

```
原材料（Excel 配置）
    ↓
第 1 站：读取配置，初始化（menu_init）
    ↓
第 2 站：启动浏览器，登录系统（browser_login）
    ↓
第 3 站：扫描页面表单（form_scan）
    ↓
第 4 站：填充表单，抓取 SQL 日志（log_capture）
    ↓
第 5 站：分析 SQL 日志（log_analysis）
    ↓
第 6 站：分类涉及的数据库表（table_classify）
    ↓
第 7 站：生成测试数据 SQL（sql_generate）
    ↓
第 8 站：校验计划完整性（plan_verify）
    ↓
第 9 站：生成 pytest 测试代码（case_generate）
    ↓
第 10 站：运行测试，失败就修复（verify）
    ↓
第 11 站：输出报告（report）
    ↓
成品（可运行的 pytest 用例 + 测试数据）
```

每一步都依赖上一步的产出。如果某一步失败了，可以从中断的地方恢复（resume），或者从指定步骤重跑（retry）。

---

## 3. 整体架构图

```
┌──────────────────────────────────────────────────────────────────────┐
│                          用户（测试工程师）                            │
│                     通过命令行 (CLI) 与 Agent 交互                     │
└─────────────────────────────┬────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    CLI 入口 (src/hui_agent/cli.py)                    │
│                                                                      │
│  子命令: menus | run | batch | status | resume | retry               │
│                                                                      │
│  职责:                                                               │
│  · 解析命令行参数                                                     │
│  · 加载 .env 环境变量（LLM API key 等）                               │
│  · 构建 LLM Advisor（4 种）                                          │
│  · 构建 ScriptRunner（脚本执行器）                                    │
│  · 构建 RunStore（日志存储）                                          │
│  · 调用 Workflow 引擎执行步骤                                         │
│  · 输出执行结果 JSON                                                  │
└─────────────────────────────┬────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│                 Workflow 引擎 (src/hui_agent/graph.py)                │
│                                                                      │
│  两种模式:                                                            │
│                                                                      │
│  模式 A: HUIWorkflow（纯 Python）                                     │
│    · 简单的 for 循环遍历 11 个步骤                                    │
│    · 不依赖 LangGraph，作为回退方案                                   │
│                                                                      │
│  模式 B: LangGraph StateGraph                                        │
│    · 支持 SQLite checkpoint（断点持久化）                              │
│    · 每个步骤是 graph 的一个 node                                     │
│    · 线性边连接: START → 0a → 0b → ... → 5 → END                    │
│                                                                      │
│  选择逻辑: 优先用模式 B，如果 langgraph 包没装则回退到模式 A           │
└─────────────────────────────┬────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│               状态模型 (src/hui_agent/state.py)                       │
│                                                                      │
│  HUITestState — 不可变数据类（frozen dataclass）                      │
│                                                                      │
│  贯穿整个流程，每次步骤完成后返回一个新的 state 对象                   │
│  （类似 React/Redux 的 reducer 模式：旧状态 + 动作 = 新状态）         │
│                                                                      │
│  关键字段:                                                            │
│  · thread_id       — 本次执行的唯一 ID                                │
│  · menu_name       — 当前处理的菜单名                                 │
│  · current_step    — 当前步骤编号（如 "2c"）                          │
│  · status          — 状态：running/success/failed/skipped/blocked     │
│  · completed_steps — 已完成的步骤列表 ["0a", "0b", "1a"]              │
│  · config_path     — 菜单配置文件路径                                 │
│  · case_plan_path  — 用例计划文件路径（核心数据文件）                  │
│  · case_dir        — 菜单生成物目录                                   │
│  · errors          — 错误记录列表                                     │
│  · failure_scope   — 失败范围：menu/environment/config                │
└─────────────────────────────┬────────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────────┐
│           步骤处理器 (src/hui_agent/nodes/skill_steps.py)             │
│                                                                      │
│  SkillStepHandlers 类 — 整个 Agent 的核心，~4000 行                   │
│  包含 11 个方法，每个方法对应流水线的一个步骤                          │
│                                                                      │
│  每个步骤的通用模式:                                                  │
│  1. 从 state 中读取需要的路径和数据                                   │
│  2. 调用 ScriptRunner 执行外部脚本                                    │
│  3. 读取/修改 case_plan.json                                          │
│  4. 返回新的 state（标记该步骤完成）                                   │
│                                                                      │
│  如果发生异常:                                                        │
│  · MenuFailure     → 标记为 failed，跳过该菜单                        │
│  · EnvironmentFailure → 标记为 blocked，阻断整个批量                  │
│  · ConfigFailure   → 标记为 failed，跳过该菜单                        │
└─────────────────────────────┬────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
          ▼                   ▼                   ▼
┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐
│  ScriptRunner    │ │   LLM Advisors   │ │   RunStore       │
│  (脚本执行器)     │ │   (LLM 顾问)     │ │   (日志存储)      │
│                  │ │                  │ │                  │
│  调用 knowledge/ │ │  4 种顾问:       │ │  events.jsonl    │
│  scripts/ 下的   │ │  · repair        │ │  agent.log       │
│  Python 脚本     │ │  · permission    │ │  result.json     │
│                  │ │  · sql           │ │  pytest_*.log    │
│  通过 subprocess │ │  · template      │ │                  │
│  执行子进程      │ │                  │ │  存储在           │
│                  │ │  通过 OpenAI     │ │  _local/runs/<thread_id>/│
│                  │ │  兼容协议调用    │ │  目录下           │
└──────────────────┘ └──────────────────┘ └──────────────────┘
```

---

## 4. 11 个步骤详解

### 步骤 0a: menu_init（菜单初始化）

**做什么**：从 Excel 配置文件中读取指定菜单的信息，生成两个核心文件。

**输入**：
- 菜单名称（来自 CLI `--menu` 参数）
- Excel 文件 `knowledge/config/test_config.xlsx` 的 `菜单导入` sheet

**输出**：
- `config.json` — 菜单的完整配置（环境信息、菜单 URL、预设数据等）
- `case_plan.json` — 用例计划文件（初始模板，后续步骤不断丰富它）

**调用的脚本**：
```
config_reader.py init "菜单名" --force-completed
```

**case_plan.json 初始结构**：
```json
{
  "menu": {
    "name": "基金分红报告",
    "code": "ifmCDyglDxfundHkbbMenu",
    "url": "/console-dxtrust-vue/...",
    "level1": "综合理财",
    "level2": "基金业务"
  },
  "env_key": "sit",
  "owner": "张三",
  "form_fields": {"all": [], "required": [], "excluded_fields": []},
  "case_inputs": {},
  "preset_strategy": {},
  "shard": {},
  "paths": {
    "project_dir": "F:/python_project",
    "test_case_dir": "F:/python_project/testcases/综合理财平台6.0/..."
  }
}
```

---

### 步骤 0b: browser_login（浏览器登录）

**做什么**：启动 Chrome 浏览器，登录 HUI 系统，导航到目标菜单页面。

**为什么需要这一步**：
- HUI 是一个 Web 系统，需要通过浏览器访问
- 使用 Chrome DevTools Protocol (CDP) 远程控制浏览器
- 需要先登录才能访问菜单

**流程**：
1. `launch_chrome.py --auto` — 启动 Chrome 并开启 CDP 远程调试端口
2. 等待 CDP 端点就绪（轮询 `/json/version`）
3. `cdp_executor.py --login --navigate <菜单URL>` — 执行登录脚本，导航到菜单
4. `explore_wrapper.py --check-node` — 检查页面 DOM 是否加载完成

**如果失败**：抛出 `EnvironmentFailure`（Chrome 连不上、登录失败等），阻断整个批量。

---

### 步骤 1a: form_scan（表单扫描）

**做什么**：用 AI 浏览器自动化工具（Stagehand）扫描菜单页面，识别所有表单字段。

**扫描结果示例**：
```json
{
  "all": [
    {
      "label": "产品代码",
      "type": "single-select",
      "value": "ASO001:测试产品",
      "options": ["ASO001:测试产品", "BSO002:另一个产品"],
      "is_required": true
    },
    {
      "label": "TA代码",
      "type": "single-select",
      "value": "ASO001",
      "is_required": true
    },
    {
      "label": "开始日期",
      "type": "input",
      "value": "20250101",
      "is_required": false
    },
    {
      "label": "结束日期",
      "type": "input",
      "value": "20251231",
      "is_required": false
    },
    {
      "label": "查询",
      "type": "button",
      "is_required": false
    }
  ],
  "required": [
    {"label": "产品代码", ...},
    {"label": "TA代码", ...}
  ],
  "excluded_fields": [
    {"label": "查询", "reason": "button"}
  ]
}
```

**然后**：用 `CaseInputPlanner`（`case_inputs.py`）规划每种测试用例需要填哪些字段：

| 用例类型 | 填哪些字段 | 目的 |
|---------|-----------|------|
| queryall | 只填必填字段 | 验证全量查询 |
| querysingle | 必填 + 一个业务字段 | 验证单条件过滤 |
| querymulti | 必填 + 多个业务字段 | 验证多条件过滤 |
| reset | 填一些字段，然后点重置 | 验证重置清空 |
| preview | 填字段出结果，点详情 | 验证详情面板 |
| branch | 同 querymulti | 分行权限测试 |
| headquarter | 同 querymulti | 总行权限测试 |

**调用的脚本**：
```
explore_wrapper.py --mode=fast --config config.json -o explore_result.json
config_reader.py match_presets explore_result.json
```

---

### 步骤 1b: log_capture（日志抓取）

**做什么**：在页面上按规划的字段组合填写表单、点击查询，同时通过 SSH 连接后端服务器抓取 SQL 日志。

**为什么需要这一步**：
- 我们需要知道后端实际执行了什么 SQL（SELECT 语句）
- 这些 SQL 告诉我们涉及哪些数据库表、用了哪些 WHERE 条件
- 这是后续生成测试数据（INSERT SQL）的关键依据

**流程**：
1. 根据 `case_inputs.querymulti` 的字段组合，生成页面操作序列（actions）
2. 执行 CDP 操作：填写字段 → 点查询 → 等待结果
3. 同时通过 SSH tail 后端日志文件，grep 包含 `Actual SQL` 或 `当前SQL` 的行
4. 保存到 `query_log.txt`

**SQL Probe（探针查询）**：
除了基本的 querymulti 字段组合，还会尝试多种变体：
- survey_full_stable：用所有稳定字段查询
- bound_group:with/without：客户标识类型的有/无对比
- 其他字段变体：每个非必填字段单独加一次

每个变体都会产生一份 SQL 日志，最后合并到 `query_log.txt`。

**调用的脚本**：
```
config_reader.py build_actions fields.json
cdp_executor.py --config config.json -e explore_result.json -a actions.json --save-log query_log.txt --ssh-log /path/to/log --ssh-host ... --ssh-port ... --log-grep "(Actual SQL|当前SQL)"
```

---

### 步骤 2a: log_analysis（日志分析）

**做什么**：解析 `query_log.txt` 中的 SQL 语句，提取结构化信息。

**提取的信息**：
```json
{
  "physical_tables": {
    "tbdxfundproduct": ["tbdxfundproduct"],
    "tbdxfundpayment": ["tbdxfundpayment1", "tbdxfundpayment2"],
    "tbtainfo": ["tbtainfo"]
  },
  "requirements": {
    "tables": ["tbdxfundproduct", "tbdxfundpayment", "tbtainfo"],
    "where_columns": {
      "tbdxfundproduct": ["prd_code", "ta_code"],
      "tbdxfundpayment": ["prd_code", "trans_date"]
    },
    "join_key_columns": {
      "tbdxfundproduct": ["prd_code"],
      "tbdxfundpayment": ["prd_code"]
    },
    "raw_sqls": [
      "SELECT * FROM tbdxfundproduct WHERE prd_code = ? AND ta_code = ?",
      "SELECT * FROM tbdxfundpayment WHERE prd_code = ? AND trans_date >= ?"
    ]
  },
  "cached_tables": ["tbtainfo"],
  "missing_tables": ["tbdxfundproduct"],
  "describe_needed": ["tbdxfundpayment"]
}
```

**关键概念 — 分片表**：
数据库可能对大表做分片，比如 `tbdxfundpayment` 实际上分为 `tbdxfundpayment1`、`tbdxfundpayment2` 等。分析器需要识别出这些分片表的逻辑名（去掉数字后缀）。

**调用的脚本**：
```
sql_extractor.py analyze query_log.txt
```

---

### 步骤 2b: table_classify（表分类）

**做什么**：对步骤 2a 识别出的所有表进行分类，确定哪些需要预埋测试数据。

**分类结果**：

| 分类 | 含义 | 示例 | 处理方式 |
|------|------|------|---------|
| base | 基础表（公共数据） | tbtainfo, tbclientmap | 从 `base_tables.json` 读取模板记录 |
| business | 业务表 | tbdxfundpayment | 根据表 schema + 日志值生成 INSERT |
| system | 系统表 | tsys_menu, tbsequence | 跳过，不做预埋 |
| view | 数据库视图 | v_xxx | 跳过，不做预埋 |

**分片路由**：
根据日志中的分片信息，确定数据应该插入到哪个分片库。比如日志显示查询了 `tbdxfundpayment1`，那预埋数据也要插入 `tbdxfundpayment1`。

**可选 LLM 辅助**：
`LLMSQLAdvisor` 可以帮助判断哪些表应该预埋、哪些不应该。但它不是必须的——没有 LLM 时用确定性规则。

**调用的脚本**：
```
config_reader.py compute_shard module_name --json
sql_extractor.py classify case_plan.json --env sit --project-dir ...
```

---

### 步骤 2c: sql_generate（SQL 生成）

**做什么**：为每个需要预埋的表生成 `DELETE + INSERT` SQL 语句。

**这是最复杂的步骤之一**，核心逻辑在 `insert_plan.py` 的 `build_insert_plans()` 函数。

**生成过程**：
1. **加载表结构**：从 `schema_cache.json` 读取每个表的列名、必填列、主键
2. **构建值上下文**：从多个来源收集字段值
   - `presets`（Excel 预设数据，如产品代码、TA 代码）
   - `base_tables.json`（基础表模板记录）
   - `form_fields`（表单扫描的实际值）
   - `case_inputs`（用例规划的字段值）
   - `raw_sqls`（日志中的 SQL 字面量）
3. **为每个表生成 InsertPlan**：
   - 匹配 base_tables 模板记录（按产品代码、TA 代码等打分选最佳匹配）
   - 补齐缺失的必填列（按列类型生成默认值）
   - 生成 DELETE WHERE 子句（精确定位要删除的旧数据）
   - 可选：生成分行/总行两行数据（权限测试）

**生成的 SQL 示例**：
```sql
DELETE FROM tbtainfo WHERE ta_code = 'ASO001';
INSERT INTO tbtainfo (ta_code, ta_name, ta_short_name, status, ...)
VALUES ('ASO001', '测试TA', '测试', '1', ...);

DELETE FROM tbdxfundproduct WHERE prd_code = 'FUND001';
INSERT INTO tbdxfundproduct (prd_code, ta_code, prd_name, status, ...)
VALUES ('FUND001', 'ASO001', '测试基金', '1', ...);
```

**Dry-run 验证**：
SQL 生成后，在事务中执行（执行完回滚），验证：
- 语法是否正确
- 是否有重复主键
- 列名是否与表 schema 匹配

如果 dry-run 失败，触发修复循环（最多 3 轮）：
1. LLM 诊断失败原因
2. 执行修复动作（patch_sql / regenerate_sql）
3. 重新 dry-run

**调用的脚本**：
```
dry_run_sql.py --file xxx.sql --db ui_fund1 --config config.json --json
build_schema_cache.py --tables tbdxfundproduct tbdxfundpayment --db ui_fund1 --force
```

---

### 步骤 2d: plan_verify（计划校验）

**做什么**：检查 `case_plan.json` 是否包含所有必要字段。

**检查项**：
- `menu.name` 和 `menu.code` 是否非空
- `case_inputs` 是否存在
- `preset_strategy` 是否存在
- `shard.db` 是否非空
- `sql_paths.base_sql` 和 `sql_paths.preset_sql` 是否非空

如果缺少任何必要字段，抛出 `MenuFailure`。

---

### 步骤 3: case_generate（用例生成）

**做什么**：根据 `case_plan.json` 中的信息，生成 pytest 测试代码文件。

**生成逻辑在 `fill_code.py` 的 `render_pytest_case()` 函数中**。

**生成的 pytest 文件结构**：
```python
# -*- coding: utf-8 -*-
import os, logging, allure, pytest
from playwright.async_api import Page
from conf.config import Config
from framework.utils.async_sqlalchemy_executor import get_async_db_executor
from common.helpers import _collapse_sidebar, _fill_input_by_label, _get_record_count, _select_dropdown_by_label

MENU_URL = "/console-dxtrust-vue/..."
PRESET_SQL_BASE = os.path.join(PROJECT_ROOT, ...)
PRESET_SQL = os.path.join(PROJECT_ROOT, ...)

@pytest.fixture
async def db_setup_base():
    """执行基础表预埋 SQL"""
    async with get_async_db_executor(...) as executor:
        await executor.execute_sql_files([PRESET_SQL_BASE])
        yield executor

@pytest.fixture
async def db_setup():
    """执行业务表预埋 SQL"""
    async with get_async_db_executor(...) as executor:
        await executor.execute_sql_files([PRESET_SQL])
        yield executor

async def _navigate(page: Page):
    """导航到菜单页面"""
    await page.goto(f"{{base}}#{{MENU_URL}}")
    await page.wait_for_timeout(3000)
    await _collapse_sidebar(page)

@allure.label("owner", "张三")
@pytest.mark.asyncio
async def test_ifmCDyglDxfundHkbbMenu_queryall(page: Page, db_setup_base, db_setup):
    """基金分红报告 - 全量查询"""
    await _navigate(page)
    await page.get_by_role("button", name="重置").click()
    await page.wait_for_timeout(1000)
    # 只填必填字段（queryall 的规则）
    await _select_dropdown_by_label(page, "产品代码", "ASO001")
    await _select_dropdown_by_label(page, "TA代码", "ASO001")
    cnt = await _click_query_and_wait(page)
    assert cnt > 0, f"全量查询为空，预期至少1条，实际{cnt}条"
    logging.info("全量查询共%s条", cnt)
```

**每种用例类型生成一个文件**：
- `test_xxx_queryall.py`
- `test_xxx_querysingle.py`
- `test_xxx_querymulti.py`
- `test_xxx_reset.py`
- `test_xxx_preview.py`
- `test_xxx_branch.py`（如果有权限过滤）
- `test_xxx_headquarter.py`（如果有权限过滤）

**可选：LLM 模板审查**：
`LLMCaseTemplateReviewer` 检查生成的代码是否符合规范：
- 不能有 `assert True`（无效断言）
- reset 用例必须先填条件再重置再断言清空
- preview 用例必须查询出结果再点详情
- 下拉字段必须调用安全选择函数

---

### 步骤 4: verify（验证）

**做什么**：运行 pytest，检查用例是否通过。失败时进入修复循环。

**这是最复杂的步骤**，因为需要处理各种失败场景。

**执行流程**：
```
运行全量 pytest（所有生成的用例）
    │
    ├── 全部通过 → 完成 ✓
    │
    └── 有失败 → 进入修复循环（最多 3 轮）
         │
         ├── 收集失败证据
         │   · pytest 输出（哪个断言失败、期望值 vs 实际值）
         │   · SQL 文件内容
         │   · 数据库快照（INSERT 后实际数据是什么）
         │   · 后端 SQL 日志
         │
         ├── LLM Repair Advisor 诊断
         │   · 分析失败原因类型（见下表）
         │   · 给出修复动作建议
         │
         ├── 执行修复动作
         │   · patch_sql — 替换 SQL 文件中的特定文本
         │   · regenerate_sql — 重写整个 SQL 文件
         │   · regenerate_case — 重写整个 pytest 文件
         │   · mark_unfixable — 标记为无法自动修复
         │
         ├── 重跑失败的用例
         │   ├── 通过 → 跑全量回归
         │   │   ├── 通过 → 完成 ✓
         │   │   └── 失败 → 继续下一轮修复
         │   └── 失败 → 继续下一轮修复
         │
         └── 3 轮都失败 → 标记为 failed
```

**LLM 诊断的失败类型**：

| failure_type | 含义 |
|-------------|------|
| preset_sql_mismatch | 预埋 SQL 的值与实际查询条件不匹配 |
| preset_sql_execution_failed | 预埋 SQL 执行报错（如重复主键） |
| database_data_missing | 数据库中没有预埋的数据 |
| case_input_mismatch | 用例的输入字段值与实际不符 |
| assertion_mismatch | 断言逻辑错误（期望值不对） |
| page_or_environment_error | 页面加载超时、登录过期等环境问题 |
| unknown | 无法判断 |

**特殊处理**：
1. **preview 自动跳过**：如果只有 preview 用例失败（原因是页面没有详情/预览按钮），而其他用例都通过了，自动移除 preview 用例并标记为跳过。
2. **环境 flake 容忍**：如果所有用例在某次尝试中都通过了，但另一次因为页面超时失败（`Page.goto: Timeout`），标记为"环境抖动"，视为通过。

---

### 步骤 5: report（报告）

**做什么**：生成 `summary.md` 报告文件。

**报告内容示例**：
```markdown
# 基金分红报告 执行报告

- 菜单编码: ifmCDyglDxfundHkbbMenu
- 验证状态: passed
- 生成用例数: 5
- SQL 预置计划数: 3
- pytest 返回码: 0
- 修复尝试次数: 1
- SQL patch 轮次: 1
- 最后修复状态: patched

## 修复过程
- 第1轮: status=patched, pytest=1, llm=preset_sql_mismatch, actions=llm_patch_sql
```

---

## 5. 核心模块逐一拆解

### 5.1 state.py — 状态模型

```python
@dataclass(frozen=True)  # frozen=True 意味着创建后不能修改
class HUITestState:
    thread_id: str                    # 唯一 ID，如 "hui-5c748bb773f1"
    menu_name: str                    # 菜单名
    current_step: str = "0a"          # 当前步骤
    status: RunStatus = "running"     # 运行状态
    completed_steps: list[str] = ...  # 已完成步骤
    config_path: str = ""             # config.json 路径
    case_plan_path: str = ""          # case_plan.json 路径
    case_dir: str = ""                # 菜单生成物目录
    errors: list[dict] = ...          # 错误记录
    failure_scope: FailureScope = None  # 失败范围
    failure_reason: str = None        # 失败原因
```

**为什么用 frozen dataclass？**
因为状态是不可变的——每次"修改"实际上是创建一个新对象。这保证了：
- 线程安全（不需要加锁）
- 可以安全地在 LangGraph checkpoint 中序列化/反序列化
- 方便调试（任何时候都可以保存快照）

**状态转换方法**：
```python
# 标记步骤完成
state.mark_completed("0a")
# 返回新状态：completed_steps=["0a"], current_step="0b", status="running"

# 记录失败
state.record_failure("2c", "SQL dry-run failed", scope="menu")
# 返回新状态：status="failed", failure_scope="menu", failure_reason="SQL dry-run failed"

# 从指定步骤重试
state.retry_from("2c")
# 返回新状态：completed_steps=["0a","0b","1a","1b","2a","2b"], current_step="2c", status="retrying"
```

### 5.2 graph.py — Workflow 引擎

**模式 A：纯 Python Workflow**
```python
class HUIWorkflow:
    def invoke(self, state):
        current = state
        for step in self.step_order:  # 遍历 ["0a", "0b", ..., "5"]
            if step in current.completed_steps:
                continue  # 跳过已完成的步骤（用于 resume）
            handler = self.step_handlers[step]  # 获取步骤处理函数
            current = handler(current)  # 执行步骤
            if current.status in {"skipped", "failed", "blocked"}:
                return current  # 失败则停止
        return current.merge(current_step="DONE", status="success")
```

**模式 B：LangGraph StateGraph**
```python
graph = StateGraph(dict)
for step in order:
    graph.add_node(step, handler)  # 每个步骤是一个 node
# 添加线性边
graph.add_edge(START, "0a")
graph.add_edge("0a", "0b")
graph.add_edge("0b", "1a")
# ...
graph.add_edge("5", END)
return graph.compile(checkpointer=sqlite_checkpointer)
```

LangGraph 模式的好处是支持 **SQLite checkpoint**：每执行完一个步骤，状态自动保存到 SQLite 数据库。如果进程崩溃，可以从 checkpoint 恢复。

### 5.3 cli.py — 命令行入口

**run 命令的完整流程**：
```python
def main():
    args = parse_args()
    load_env_files()           # 加载 .env
    runner = ScriptRunner()    # 脚本执行器
    store = RunStore()         # 日志存储

    if args.command == "run":
        state = HUITestState.new(args.menu)           # 创建初始状态
        store.write_event(state.thread_id, "START", "run_started", ...)
        result = _invoke_runtime(state, ...)           # 执行 workflow
        store.write_result(result)                     # 保存最终状态
        print(json.dumps(result.to_dict()))            # 输出 JSON
```

**_invoke_runtime 函数**：
```python
def _invoke_runtime(state, skill_root, checkpoint_db, store):
    # 1. 构建 LLM advisor
    repair_advisor = build_repair_advisor_from_env()
    permission_advisor = build_permission_advisor_from_env()
    sql_advisor = build_sql_advisor_from_env()
    template_reviewer = build_template_reviewer_from_env()

    # 2. 构建步骤处理器
    handlers = build_default_step_handlers(
        skill_root,
        event_logger=script_event_logger,
        repair_advisor=repair_advisor,
        permission_advisor=permission_advisor,
        sql_advisor=sql_advisor,
        template_reviewer=template_reviewer,
    )

    # 3. 包装日志
    handlers = _wrap_step_handlers_for_logging(handlers, store, runtime_context)

    # 4. 优先用 LangGraph，回退用纯 Python
    try:
        with make_sqlite_checkpointer(checkpoint_db) as checkpointer:
            graph = build_langgraph(step_handlers=handlers, checkpointer=checkpointer)
            result = graph.invoke(state.to_dict(), config)
        return HUITestState.from_dict(result)
    except RuntimeError:
        workflow = build_workflow(step_handlers=handlers)
        return workflow.invoke(state)
```

### 5.4 script_runner.py — 脚本执行器

**职责**：调用 `knowledge/scripts/` 目录下的 Python 脚本。

**工作方式**：
```python
class ScriptRunner:
    def __init__(self, skill_root, event_logger=None):
        self.scripts_dir = Path(skill_root) / "scripts"

    def run_text(self, script_name, args):
        # 构建命令
        script_path = self.scripts_dir / script_name
        command = [sys.executable, str(script_path), *args]

        # 执行子进程
        result = subprocess.run(
            command,
            cwd=str(self.skill_root),
            env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            text=True, capture_output=True, encoding="utf-8"
        )

        # 检查结果
        if result.returncode != 0:
            raise ScriptError(command, result.returncode, result.stdout, result.stderr)

        return result.stdout.strip()

    def run_json(self, script_name, args):
        text = self.run_text(script_name, args)
        return parse_last_json_value(text)  # 从输出中提取最后一个 JSON 对象
```

**为什么用子进程而不是直接 import？**
- 脚本可能有依赖冲突（比如 Stagehand 需要 Node.js 环境）
- 脚本可能需要不同的工作目录
- 子进程天然隔离，一个脚本崩溃不影响主进程
- 方便超时控制

### 5.5 llm.py — LLM 顾问层

**4 种 Advisor 的职责**：

**LLMRepairAdvisor**（修复诊断）：
- 触发时机：pytest 失败或 SQL dry-run 失败时
- 输入：失败日志 + SQL 文件内容 + DB 快照 + 用例代码 + 表结构
- 输出：失败类型 + 修复动作列表
- 这是最重要的 advisor，直接影响用例能否通过

**LLMPermissionAdvisor**（权限判断）：
- 触发时机：SQL 生成后
- 输入：表单字段 + SQL where/join 条件 + 预埋计划
- 输出：是否有分行/总行权限过滤
- 决定是否生成 branch/headquarter 测试用例

**LLMSQLAdvisor**（SQL 策略）：
- 触发时机：表分类后
- 输入：SQL 分析结果 + 表分类结果
- 输出：哪些表需要预埋、哪些不需要
- 辅助确定性规则，处理边界情况

**LLMCaseTemplateReviewer**（模板审查）：
- 触发时机：用例生成后
- 输入：生成的 pytest 代码
- 输出：是否符合规范、是否有无效断言
- 防止生成低质量用例

**LLM 调用方式**：
```python
class OpenAICompatibleLLMClient:
    def complete_json(self, system, user):
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0,
            "response_format": {"type": "json_object"},
        }
        # 用 urllib 发 HTTP 请求（不用 requests 库，减少依赖）
        request = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
        content = body["choices"][0]["message"]["content"]
        return json.loads(content)  # 强制返回 JSON
```

### 5.6 insert_plan.py — SQL 预埋引擎

这是技术含量最高的模块之一，负责为每个表生成精确的 INSERT 语句。

**核心函数 `build_insert_plans()` 的逻辑**：
```
对于每个需要预埋的表:
    1. 查找表的物理名（可能有分片后缀）
    2. 加载表 schema（列名、必填列、主键）
    3. 收集该表的字段值（从 presets/base_tables/form_fields/case_inputs）
    4. 如果是基础表 → 从 base_templates 选最佳匹配记录
    5. 补齐缺失的必填列（按类型生成默认值）
    6. 如果需要权限测试 → 生成两行数据（分行/总行）
    7. 生成 DELETE WHERE 子句
    8. 组装成 InsertPlan 对象
```

**值来源优先级**：
```
1. presets（Excel 配置）         — 最可靠，人工维护
2. base_tables.json（模板记录）  — 基础表的标准数据
3. form_fields（表单实际值）     — 来自页面扫描
4. case_inputs（用例规划值）     — 来自 CaseInputPlanner
5. raw_sqls（SQL 字面量）        — 来自后端日志
6. 生成的默认值                  — 按列类型推断（数字→0，日期→2025-01-01）
```

### 5.7 case_inputs.py — 用例输入规划器

**职责**：决定每种测试用例应该填哪些表单字段。

**核心类 `CaseInputPlanner`**：
```python
class CaseInputPlanner:
    def plan(self):
        required = self._required_closure()    # 必填字段 + 字段组闭包
        excluded = self._excluded()            # 排除的字段（tree-select、流水号等）
        single = self._single_filter()         # 单条件查询的字段
        stable_multi = self._stable_multi_fields()  # 多条件查询的字段
        reset = self._reset_field()            # 重置测试的字段
        preview = stable_multi or required     # 预览测试的字段

        return {
            "queryall": CaseInput("全量查询", required, excluded),
            "querysingle": CaseInput("单条件查询", required + single, excluded),
            "querymulti": CaseInput("多条件查询", stable_multi, excluded),
            "reset": CaseInput("条件重置", reset, excluded),
            "preview": CaseInput("结果预览", preview, excluded),
            "branch": CaseInput("分行权限", stable_multi, excluded),
            "headquarter": CaseInput("总行权限", stable_multi, excluded),
        }
```

**字段组闭包**：
如果字段 A 和字段 B 是一个绑定组（比如"客户标识类型"和"客户标识"），选了 A 就必须选 B。`_close_groups` 方法负责这个闭包逻辑。

### 5.8 fill_code.py — 代码生成器

**职责**：根据 case_plan 和用例类型，生成完整的 pytest 文件内容。

**生成的代码特点**：
- 使用 Playwright 异步 API 控制浏览器
- 使用项目的 common helpers（`_fill_input_by_label`、`_select_dropdown_by_label`）
- 使用 allure 报告框架
- SQL 预埋通过 pytest fixture 实现
- 断言基于实际查询记录数

### 5.9 sql_guards.py — SQL 覆盖率门控

**职责**：检查生成的 INSERT 计划是否覆盖了所有应该预埋的表。

**检查逻辑**：
1. 从 `preset_strategy` 获取应该预埋的表列表（base_tables + biz_tables）
2. 从 `insert_plans` 获取实际生成了计划的表
3. 对比差异，如果有遗漏则标记为 failed

### 5.10 repair_evidence.py — 修复证据收集

**职责**：为 LLM Repair Advisor 收集诊断所需的全部证据。

**收集的证据**：
```python
{
    "attempt": 1,
    "menu": {...},
    "pytest": {
        "returncode": 1,
        "stdout_tail": "...（pytest 输出的最后 4000 字符）",
        "stderr_tail": "...",
        "failed_nodeids": ["test_xxx_queryall"]
    },
    "query_log_tail": "...（SQL 日志的最后 8000 字符）",
    "presets": {"prd_code": "FUND001", "ta_code": "ASO001"},
    "case_inputs": {...},
    "sql_files": [
        {"kind": "base_sql", "path": "...", "content_tail": "..."},
        {"kind": "preset_sql", "path": "...", "content_tail": "..."}
    ],
    "generated_tests": [
        {"path": "...", "case_type": "queryall", "is_failed": true, "content_tail": "..."}
    ],
    "table_schemas": {
        "tbtainfo": {"all_columns": ["ta_code", "ta_name", ...]}
    },
    "db_snapshot": {
        "snapshots": {
            "base_sql": {"tbtainfo": [{"ta_code": "ASO001", ...}]},
            "preset_sql": {"tbdxfundproduct": [{"prd_code": "FUND001", ...}]}
        }
    }
}
```

### 5.11 data_catalog.py — 数据目录策略

**职责**：定义数据访问策略的边界。这个类更像是一个"策略声明"而不是实际执行器。

**核心原则**：
- schema 缓存是可复用的知识
- 数据库是刷新和验证来源
- **禁止**依赖数据库中已有的业务数据
- 所有测试数据必须由 InsertPlan 生成

### 5.12 failure_policy.py — 失败策略

**三种失败范围**：
```python
FailureScope = Literal["menu", "environment", "config"]

class MenuFailure(WorkflowFailure):
    scope = "menu"          # 单个菜单的问题，跳过继续

class EnvironmentFailure(WorkflowFailure):
    scope = "environment"   # 环境问题，阻断整个批量

class ConfigFailure(WorkflowFailure):
    scope = "config"        # 配置问题，跳过继续
```

### 5.13 run_store.py — 运行日志存储

**职责**：持久化每次执行的事件、日志和结果。

**存储结构**：
```
_local/runs/<thread_id>/
├── events.jsonl          # 结构化事件流（每行一个 JSON）
│   格式: {"timestamp": "...", "thread_id": "...", "step": "2c", "event": "sql_dry_run_started", "payload": {...}}
│
├── agent.log             # 人类可读日志
│   格式: 2026-06-10T17:07:29 [2c] sql_dry_run_started - attempt=1 kind=preset_sql file=xxx.sql
│
├── result.json           # 最终状态快照（完整的 HUITestState JSON）
│
└── pytest_attempt_1.log  # pytest 的完整输出
```

### 5.14 checkpoint.py — LangGraph 断点

**职责**：创建 LangGraph 的 SQLite checkpointer，支持断点恢复。

```python
@contextmanager
def make_sqlite_checkpointer(path):
    from langgraph.checkpoint.sqlite import SqliteSaver
    saver = SqliteSaver.from_conn_string(str(path))
    with saver as s:
        yield s
```

---

## 6. 数据流：数据怎么在模块之间流动

### 6.1 完整数据流图

```
                         ┌─────────────────┐
                         │  Excel 配置文件   │
                         │  test_config.xlsx │
                         └────────┬────────┘
                                  │ 0a: menu_init
                                  ▼
                    ┌─────────────────────────────┐
                    │  config.json                  │
                    │  (菜单配置、环境信息、预设数据) │
                    └─────────────┬───────────────┘
                                  │
                                  ▼
                    ┌─────────────────────────────┐
                    │  case_plan.json (初始版)      │
                    │  (菜单信息、路径、空的表单字段) │
                    └─────────────┬───────────────┘
                                  │ 0b: browser_login
                                  │ (Chrome 启动、登录、导航)
                                  │
                                  │ 1a: form_scan
                                  ▼
                    ┌─────────────────────────────┐
                    │  explore_result.json          │
                    │  (页面表单字段扫描结果)        │
                    └─────────────┬───────────────┘
                                  │
                                  ▼
                    ┌─────────────────────────────┐
                    │  case_plan.json (+form_fields │
                    │  +case_inputs)                │
                    │  (表单字段 + 用例输入规划)      │
                    └─────────────┬───────────────┘
                                  │ 1b: log_capture
                                  ▼
                    ┌─────────────────────────────┐
                    │  query_log.txt                │
                    │  (后端 SQL 日志)               │
                    └─────────────┬───────────────┘
                                  │ 2a: log_analysis
                                  ▼
                    ┌─────────────────────────────┐
                    │  case_plan.json (+log_analysis │
                    │  +shard.physical_tables)      │
                    │  (SQL 分析结果、物理表映射)     │
                    └─────────────┬───────────────┘
                                  │ 2b: table_classify
                                  ▼
                    ┌─────────────────────────────┐
                    │  case_plan.json (+preset_     │
                    │  strategy +shard.db)          │
                    │  (表分类结果、目标数据库)       │
                    └─────────────┬───────────────┘
                                  │ 2c: sql_generate
                                  ▼
                    ┌─────────────────────────────┐
                    │  xxx_base.sql                 │
                    │  (基础表预埋 SQL)              │
                    │                               │
                    │  xxx.sql                      │
                    │  (业务表预埋 SQL)              │
                    │                               │
                    │  case_plan.json (+insert_plans │
                    │  +sql_paths +permission_decision)│
                    └─────────────┬───────────────┘
                                  │ 2d: plan_verify
                                  │ (完整性检查)
                                  │
                                  │ 3: case_generate
                                  ▼
                    ┌─────────────────────────────┐
                    │  test_xxx_queryall.py         │
                    │  test_xxx_querysingle.py      │
                    │  test_xxx_querymulti.py       │
                    │  test_xxx_reset.py            │
                    │  test_xxx_preview.py          │
                    │  (pytest 测试用例文件)          │
                    │                               │
                    │  case_plan.json (+generated_  │
                    │  tests +template_review)      │
                    └─────────────┬───────────────┘
                                  │ 4: verify
                                  ▼
                    ┌─────────────────────────────┐
                    │  pytest 运行结果               │
                    │  (passed/failed + 修复记录)    │
                    │                               │
                    │  case_plan.json (+verification │
                    │  +repair_attempts)            │
                    └─────────────┬───────────────┘
                                  │ 5: report
                                  ▼
                    ┌─────────────────────────────┐
                    │  reports/summary.md           │
                    │  (最终报告)                    │
                    └─────────────────────────────┘
```

### 6.2 case_plan.json 的演化

case_plan.json 是贯穿整个流程的核心文件，每一步都会往里面写入新数据：

```
0a 初始化:  menu, env_key, owner, paths, empty form_fields
    ↓
1a 扫描后:  +form_fields (all/required/excluded), +case_inputs, +presets
    ↓
1b 抓取后:  +case_inputs (值更新为实际值), +sql_probe
    ↓
2a 分析后:  +log_analysis (requirements/physical_tables)
    ↓
2b 分类后:  +preset_strategy (base_tables/biz_tables/no_insert_tables), +shard.db
    ↓
2c 生成后:  +insert_plans, +sql_paths, +sql_validation, +permission_decision
    ↓
2d 校验后:  +plan_verification
    ↓
3  生成后:  +generated_tests, +template_review
    ↓
4  验证后:  +verification, +verification_attempts, +repair_attempts
    ↓
5  报告后:  +reports
```

---

## 7. 关键数据结构

### 7.1 InsertPlan（SQL 预埋计划）

```python
@dataclass(frozen=True)
class InsertPlan:
    table: str                    # 逻辑表名，如 "tbdxfundproduct"
    physical_table: str           # 物理表名，如 "tbdxfundproduct1"（可能有分片后缀）
    role: str                     # "base" 或 "business"
    columns: list[InsertColumn]   # 列列表
    delete_where: str             # DELETE 的 WHERE 子句
    rows: list[list[InsertColumn]] | None  # 多行数据（权限测试时有两行）
    db: str                       # 目标数据库，如 "ui_fund1"

@dataclass(frozen=True)
class InsertColumn:
    name: str     # 列名，如 "prd_code"
    value: str    # 值，如 "FUND001"
    source: str   # 值来源，如 "presets.prd_code" 或 "generated.required_default"
```

### 7.2 TableSchema（表结构）

```python
@dataclass(frozen=True)
class TableSchema:
    all_columns: list[str]        # 所有列名
    required_columns: list[str]   # 必填列（NOT NULL + 无默认值）
    primary_keys: list[str] | None  # 主键列
    column_types: dict[str, str] | None  # 列类型映射，如 {"prd_code": "varchar(20)"}
```

### 7.3 CaseInput（用例输入）

```python
@dataclass(frozen=True)
class CaseInput:
    purpose: str                  # 用例目的，如 "验证全量查询"
    fields: list[PlannedField]    # 需要填写的字段列表
    excluded: dict[str, str]      # 排除的字段及原因

@dataclass(frozen=True)
class PlannedField:
    label: str      # 字段标签，如 "产品代码"
    type: str       # 字段类型，如 "single-select"
    value: str      # 填入的值，如 "ASO001:测试产品"
    reason: str     # 选择原因，如 "required" 或 "stable_multi"
```

### 7.4 LLM 输出结构

**RepairDecision（修复诊断）**：
```python
class RepairDecision(BaseModel):
    failure_type: str       # "preset_sql_mismatch" 等
    confidence: float       # 0.0 ~ 1.0
    summary: str            # 一句话说明
    evidence: list[str]     # 证据列表
    actions: list[RepairAction]  # 修复动作列表

class RepairAction(BaseModel):
    type: str        # "patch_sql" / "regenerate_sql" / "regenerate_case" / "mark_unfixable"
    file: str        # 目标文件路径
    reason: str      # 为什么要改
    old_text: str    # patch_sql: 要替换的原文本
    new_text: str    # patch_sql: 替换后的新文本
    content: str     # regenerate_sql/regenerate_case: 完整的新文件内容
    changes: list    # 变更明细
```

---

## 8. LLM 在哪里介入

```
┌─────────────────────────────────────────────────────────────┐
│                    完整流程中的 LLM 介入点                     │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  0a menu_init      ─── 无 LLM                               │
│  0b browser_login  ─── 无 LLM                               │
│  1a form_scan      ─── 无 LLM（Stagehand 内部可能用 AI）     │
│  1b log_capture    ─── 无 LLM                               │
│  2a log_analysis   ─── 无 LLM                               │
│  2b table_classify ─── 可选: LLMSQLAdvisor 辅助分类          │
│  2c sql_generate   ─── 可选: LLMPermissionAdvisor 判断权限   │
│                       必须: LLMRepairAdvisor 修复 dry-run 失败│
│  2d plan_verify    ─── 无 LLM                               │
│  3  case_generate  ─── 可选: LLMCaseTemplateReviewer 审查    │
│  4  verify         ─── 必须: LLMRepairAdvisor 修复 pytest 失败│
│  5  report         ─── 无 LLM                               │
│                                                             │
│  "必须" = 没有 LLM 则该功能不可用                             │
│  "可选" = 没有 LLM 则用确定性规则回退                         │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

---

## 9. 外部依赖：Agent 调用了哪些外部工具

### 9.1 knowledge/scripts/ 下的脚本

| 脚本 | 用途 | 被哪个步骤调用 |
|------|------|--------------|
| `config_reader.py` | Excel 配置读取、菜单初始化、构建操作序列 | 0a, 1a, 1b, 2b, 2c |
| `launch_chrome.py` | 启动 Chrome CDP | 0b |
| `cdp_executor.py` | CDP 浏览器操作（登录、导航、表单填充、日志抓取） | 0b, 1b |
| `explore_wrapper.py` | Stagehand 表单扫描 | 0b, 1a |
| `sql_extractor.py` | SQL 日志解析、表分类 | 2a, 2b |
| `dry_run_sql.py` | SQL dry-run 验证 | 2c |
| `build_schema_cache.py` | 表结构缓存刷新 | 2c |
| `query_db_snapshot.py` | 数据库快照查询（用于诊断） | 4 |
| `deploy_helpers.py` | 部署 common helpers 到目标项目 | 3 |

### 9.2 外部服务

| 服务 | 用途 | 连接方式 |
|------|------|---------|
| Chrome (CDP) | 浏览器自动化 | HTTP/WebSocket 到 `cdp_url` |
| 应用日志服务器 | 抓取后端 SQL 日志 | SSH（paramiko） |
| 数据库 | SQL dry-run、schema 查询、数据快照 | SQLAlchemy（通过目标项目的 conf/config.py） |
| LLM API | 修复诊断、权限判断等 | HTTP（urllib）到 OpenAI 兼容端点 |

### 9.3 Stagehand（AI 浏览器自动化）

位于 `knowledge/scripts/stagehand_explorer/`，是一个 Node.js 项目：
- 用 AI 理解页面结构
- 自动识别表单字段（label、type、options）
- 生成 `explore_result.json`

---

## 10. CLI 命令怎么用

### 10.1 安装

```powershell
cd <agent_root>
python -m pip install -e ".[dev]"
```

### 10.2 查看未完成菜单

```powershell
python -m hui_agent.cli menus
```
输出：每行一个菜单名（过滤掉了 `状态=已完成` 的）。

### 10.3 执行单个菜单

```powershell
python -m hui_agent.cli run --menu "基金分红报告"
```
输出：最终状态 JSON，包含 thread_id、status、completed_steps 等。

### 10.4 批量执行

```powershell
python -m hui_agent.cli batch
```
- 从 Excel 读取所有未完成菜单
- 串行执行每个菜单
- 单菜单失败 → skipped，继续下一个
- 环境失败 → blocked，整个批量停止
- 输出汇总 JSON

### 10.5 查看状态

```powershell
python -m hui_agent.cli status --thread-id hui-5c748bb773f1
```

### 10.6 恢复执行

```powershell
python -m hui_agent.cli resume --thread-id hui-5c748bb773f1
```
从中断的步骤继续执行。

### 10.7 从指定步骤重跑

```powershell
python -m hui_agent.cli retry --thread-id hui-5c748bb773f1 --from-step 2c
python -m hui_agent.cli resume --thread-id hui-5c748bb773f1
```
`retry` 只回退状态，`resume` 才真正执行。

---

## 11. 运行日志和排障

### 11.1 日志文件位置

```
<agent_root>/_local/runs/<thread_id>/
├── events.jsonl          # 结构化事件（机器可读）
├── agent.log             # 文本日志（人类可读）
├── result.json           # 最终状态
└── pytest_attempt_N.log  # pytest 输出
```

### 11.2 排障顺序

1. **控制台输出**：实时看 stderr 输出（`[thread_id] [step] event: message`）
2. **agent.log**：看每个步骤的开始/结束/失败
3. **events.jsonl**：看详细的 payload（LLM 调用结果、SQL 验证详情等）
4. **case_plan.json**：看当前菜单的完整状态
5. **SQL 文件**：看生成的 INSERT 语句是否有问题
6. **query_log.txt**：看抓取的后端 SQL 日志

### 11.3 关键事件

| 事件 | 含义 |
|------|------|
| `step_started` | 步骤开始 |
| `step_finished` | 步骤完成 |
| `step_failed` | 步骤失败 |
| `script_started` | 子脚本开始执行 |
| `script_finished` | 子脚本执行完成 |
| `script_failed` | 子脚本执行失败 |
| `llm_sql_repair_started` | LLM SQL 修复开始 |
| `llm_sql_repair_finished` | LLM SQL 修复完成（含耗时） |
| `llm_repair_started` | LLM 用例修复开始 |
| `llm_repair_finished` | LLM 用例修复完成 |
| `sql_dry_run_started` | SQL dry-run 开始 |
| `sql_dry_run_finished` | SQL dry-run 完成 |
| `pytest_started` | pytest 运行开始 |
| `pytest_finished` | pytest 运行完成 |
| `pytest_case_result` | 单个用例的结果 |

---

## 12. 当前存在的问题

### 12.1 严重问题

| # | 问题 | 影响 | 位置 |
|---|------|------|------|
| 1 | verify 步骤失败后仍标记为 completed | 数据准确性：失败的菜单被误认为成功 | `skill_steps.py:760-762` |
| 2 | LLM 调用无重试机制 | 网络抖动导致修复失败 | `llm.py:145-148` |
| 3 | LangGraph 回退静默吞异常 | 用户不知道为什么 checkpoint 没生效 | `cli.py:503-505` |
| 4 | resume 不清理半写文件 | 恢复后可能读到不完整的中间文件 | `cli.py:116-124` |

### 12.2 代码质量

| # | 问题 | 影响 | 位置 |
|---|------|------|------|
| 5 | skill_steps.py 4085 行 | 极难维护和测试 | `nodes/skill_steps.py` |
| 6 | `_is_product_table` 运算符优先级 bug | 逻辑判断错误 | `insert_plan.py:182-183` |
| 7 | `_is_system_table` 重复定义 3 处 | 新增系统表需改 3 个文件 | skill_steps/insert_plan/sql_guards |
| 8 | `_logical_table_name` 重复定义 2 处 | 同上 | insert_plan/sql_guards |
| 9 | `_parse_last_json_value` 重复定义 2 处 | 同上 | script_runner/skill_steps |
| 10 | agent_config.yaml 的 failure_policy 未使用 | 配置浪费 | `settings.py` |
| 11 | datetime.utcnow() 已弃用 | Python 3.12+ 警告 | `skill_steps.py:4013` |

---

## 13. 改进方案

### Phase 1: 紧急修复

1. **修复 verify 失败状态**：第 760-762 行改为 `state.record_failure("4", ...)`
2. **LLM 调用添加重试**：指数退避，最多 3 次
3. **resume 前清理中间文件**：检查 SQL 文件完整性，清理不完整的 pytest 日志

### Phase 2: 架构重构

4. **拆分 skill_steps.py**：按步骤拆成独立文件，公共函数提取到 helpers/
5. **统一工具函数**：创建 `hui_agent/common/` 目录，消除重复定义
6. **添加 doctor 命令**：一键检查环境健康状态

### Phase 3: 功能增强

7. **充分利用 agent_config.yaml**：读取 failure_policy、分模型配置
8. **批量执行进度条**：实时显示 `[3/50] Processing: 菜单名`
9. **事件格式化重构**：if-elif 链改为映射表

### Phase 4: 质量保障

10. **添加集成测试**：Mock 外部依赖，测试完整流程
11. **提高 skill_steps.py 测试覆盖**：为每个步骤 handler 编写单元测试
12. **回归测试**：基于已有 log 文件创建测试用例
