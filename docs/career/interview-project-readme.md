# 面试项目 README：HUI Web 自动化测试生成 Agent

> 本文档用于求职展示和面试准备，重点说明项目背景、架构设计、技术难点、可靠性方案和可讲述成果。
>
> 隐私说明：本文档不包含联系方式、邮箱、家庭住址、证件号等个人敏感信息，也不复制私密简历原文。

## 1. 项目一句话介绍

这是一个基于 LangGraph、Playwright、pytest、Chrome CDP、Stagehand 和 MySQL 的 HUI Web 自动化测试生成 Agent，用于把财富管理后台菜单的人工自动化测试编写流程，改造成可恢复、可批量、可观测的端到端 workflow。

## 2. 项目背景

在财富管理 HUI 后台业务中，查询类菜单自动化用例通常需要人工完成以下工作：

1. 打开浏览器并登录测试环境；
2. 进入指定业务菜单；
3. 探勘页面表单、必填项、下拉框、按钮和查询条件；
4. 触发查询并抓取后端 SQL 日志；
5. 分析 SQL 表名、WHERE/JOIN 条件和分库分表线索；
6. 构造稳定测试数据并执行预埋；
7. 编写 Playwright/pytest 自动化脚本；
8. 运行测试、排查失败、修复脚本或数据；
9. 输出测试报告并沉淀运行记录。

该流程链路长、强依赖测试环境，且需要同时理解前端页面、后端日志、数据库结构和测试框架。项目目标是把上述流程沉淀为 Agent workflow，让菜单级测试用例生成具备工程化、可恢复和可复用能力。

## 3. 核心架构

项目采用“确定性 workflow + LLM 局部决策”的架构：

- **确定性 workflow：** 负责浏览器控制、CDP 交互、SSH 日志抓取、SQL dry-run、pytest 执行、状态流转和报告输出。
- **LLM 局部决策：** 只在 SQL 分类、SQL 修复、模板审查、失败诊断等需要推理的低权限节点介入。
- **状态驱动：** 通过 thread_id、checkpoint、运行日志和 case_plan 记录每次菜单任务的执行状态。
- **安全边界：** 对危险 SQL、写入范围、失败修复、回归验证和熔断策略做约束，避免 Agent 失控执行。

```text
菜单配置
  ↓
menu_init
  ↓
browser_login
  ↓
form_scan
  ↓
log_capture
  ↓
log_analysis
  ↓
table_classify
  ↓
sql_generate
  ↓
plan_verify
  ↓
case_generate
  ↓
verify
  ↓
report
```

## 4. Workflow 节点说明

| 节点 | 作用 |
| --- | --- |
| `menu_init` | 从 Excel 初始化菜单配置，生成运行目录和 case_plan |
| `browser_login` | 启动或接管 Chrome，会话登录后导航到目标菜单 |
| `form_scan` | 使用 Stagehand/CDP 扫描表单字段、必填项、下拉框和按钮 |
| `log_capture` | 基于探勘结果触发查询，通过 SSH 抓取后端 SQL 日志 |
| `log_analysis` | 解析 SQL 表名、WHERE/JOIN 字段、分片路由和系统表 |
| `table_classify` | 区分基础表、业务表、禁止预埋表，并确定目标数据库 |
| `sql_generate` | 生成 DELETE + INSERT 预埋 SQL，并执行 dry-run 校验 |
| `plan_verify` | 校验 case_plan 中字段、SQL、用例计划等必要信息 |
| `case_generate` | 基于模板生成 pytest 用例 |
| `verify` | 执行 pytest，失败时进入 repair loop 和回归验证 |
| `report` | 输出 Markdown 报告和运行结果 |

## 5. 技术难点与解决方案

### 5.1 HUI 定制组件难以稳定定位

**问题：** HUI 页面包含定制输入框、下拉框、动态必填项和操作按钮，直接用文本或全局 selector 容易误选分页器、隐藏元素或相似字段。

**方案：**

- 使用 Playwright + Chrome CDP 复用已登录会话；
- 结合 Stagehand 扫描页面结构和 HUI 组件语义；
- 提取字段 label、控件类型、必填状态、候选值和稳定 locator；
- 后续用例生成基于字段规划，而不是盲目填满所有控件。

### 5.2 测试数据不能依赖环境随机存量

**问题：** 查询类菜单如果依赖环境已有数据，测试结果不稳定，换环境后用例容易失败。

**方案：**

- 通过 SSH 增量抓取后端 SQL 日志；
- 解析真实查询 SQL 中的 WHERE/JOIN 条件；
- 结合 Excel 预设数据、schema cache、基础表快照和表路由信息生成测试数据；
- 使用 DELETE + INSERT 方式构造稳定数据；
- 通过 SQL dry-run 校验语法、字段、主键和事务语义。

### 5.3 LLM 不能直接控制高风险动作

**问题：** 如果让 LLM 直接操作数据库、浏览器和文件系统，容易产生不可控写入、危险 SQL 或错误修复。

**方案：**

- 浏览器、SSH、数据库、pytest 等动作均由确定性脚本和 workflow 节点执行；
- LLM 只输出结构化修复建议或受限 patch 动作；
- 禁止 DROP、ALTER、TRUNCATE、CREATE、GRANT、REVOKE 等危险 SQL；
- 只允许修改当前菜单登记过的 SQL 或 pytest 文件；
- SQL 修复后必须 dry-run，pytest 修复后必须先跑失败用例再跑回归。

### 5.4 长链路任务需要可恢复和可观测

**问题：** 菜单测试生成涉及页面、日志、数据库、测试框架和报告，任何一步失败都需要定位上下文。

**方案：**

- 每次菜单执行生成 thread_id；
- 记录 events.jsonl、result.json、agent.log、pytest_attempt_N.log；
- 支持 status、resume、retry；
- 单菜单失败不阻塞批量任务，共享环境失败才 batch blocked；
- 失败修复前 snapshot，失败后 restore。

## 6. CLI 能力

常用命令：

```bash
python -m hui_agent.cli doctor
python -m hui_agent.cli menus
python -m hui_agent.cli run --menu "菜单名"
python -m hui_agent.cli batch
python -m hui_agent.cli status --thread-id <thread_id>
python -m hui_agent.cli resume --thread-id <thread_id>
python -m hui_agent.cli retry --thread-id <thread_id> --from-step 2c
```

CLI 设计目标是让 Agent 不只是脚本，而是可诊断、可恢复、可批量迁移的团队工具。

## 7. MCP 扩展

项目可将页面勘测能力封装为 MCP stdio 工具服务，供外部 Agent 调用：

| Tool | 作用 |
| --- | --- |
| `scan_form` | 扫描当前 HUI 页面表单，并返回字段结构和预埋数据匹配结果 |
| `build_actions` | 根据表单字段生成确定性查询动作 |
| `check_node` | 检查 Stagehand Node.js 依赖是否可用 |

推荐架构是：LangGraph 负责任务编排，MCP 作为可复用工具层，高风险的 SQL、pytest 和修复流程继续由确定性 workflow 控制。

## 8. 项目成果

- 3 周从零交付并落地真实业务环境；
- 覆盖 56 个财富管理业务菜单；
- 生成约 2000 条 pytest 用例；
- 将查询类菜单自动化测试周期从约 1 个月压缩至 4 天；
- 推广至组内 5+ 人使用；
- 沉淀配置模板、运行文档和问题排查流程。

## 9. 2 分钟面试介绍

在实习期间，我发现财富管理 HUI 后台有 56 个以上业务菜单需要做自动化测试。传统流程需要人工探勘页面、抓 SQL 日志、分析查询条件、预埋测试数据、编写 Playwright/pytest 脚本并排查失败，整体交付周期大约一个月。

因此我独立设计了一个基于 LangGraph 的 Web UI 自动化测试生成 Agent。它把整个流程拆成菜单初始化、浏览器登录接管、Stagehand/CDP 页面探勘、SSH 日志抓取、SQL 分析、数据预埋、pytest 用例生成、验证修复和报告输出等节点。

页面侧，我使用 Playwright 和 Chrome CDP 复用已登录会话，结合 Stagehand 识别 HUI 定制组件、必填项和查询条件，生成 queryall、querysingle、querymulti、reset、preview、branch 等用例。数据侧，我通过 SSH 增量抓取后端日志，解析 SQL 表名、WHERE/JOIN 条件和分库分表线索，再结合 MySQL schema cache 和 Excel 预设数据生成 DELETE + INSERT 预埋脚本。

为了保证可靠性，我做了 SQL dry-run、单用例 pytest、全量回归 pytest 三层校验，并设计 repair loop，失败前 snapshot，失败后 restore，避免环境污染。最终项目 3 周落地，覆盖 56 个菜单，生成约 2000 条 pytest 用例，把测试周期从约 1 个月压缩到 4 天，并推广给组内 5 人以上使用。

## 10. 高频面试问答

### Q1：为什么使用 LangGraph，而不是普通脚本？

因为这个任务不是单步自动化，而是一个长链路、多状态、可失败、可恢复的 workflow。LangGraph 可以把菜单初始化、页面探勘、日志分析、SQL 生成、pytest 验证和报告输出拆成明确节点，配合 thread_id、checkpoint 和状态记录支持 resume、retry 和 batch。

### Q2：为什么不能让 LLM 全权生成和执行？

浏览器、SSH、数据库和 pytest 都是高风险动作。LLM 容易产生幻觉或错误修复，所以项目采用“确定性 workflow + LLM 局部决策”：执行动作由脚本控制，LLM 只负责 SQL 修复、模板审查、失败诊断等需要推理的部分，并且输出必须经过 dry-run 或 pytest 校验。

### Q3：如何保证生成的测试数据稳定？

不依赖环境随机存量数据，而是从真实 SQL 日志中解析查询谓词和 JOIN 条件，结合 Excel 预设值、基础表快照和 schema cache 生成预埋 SQL。执行前通过 dry-run 校验，确保字段、主键、事务语义和返回数据符合预期。

### Q4：失败后如何修复？

失败分为 SQL 失败、用例失败、环境失败和不可修复失败。SQL 失败先进入 dry-run 诊断和受限 SQL patch；pytest 失败先定位失败用例，再进行模板或断言修复。修复前保存 snapshot，修复后先跑失败用例，再跑全量回归；连续失败达到上限后熔断并输出报告。

### Q5：你在项目中最大的收获是什么？

最大的收获是理解了企业级 Agent 不能只追求“智能”，还要重视确定性、可观测、安全边界和失败恢复。尤其在数据库、测试环境和浏览器自动化场景中，LLM 应该作为局部推理能力，而不是直接执行所有高风险动作。

