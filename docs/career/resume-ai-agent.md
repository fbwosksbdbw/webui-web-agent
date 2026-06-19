# AI Agent 开发方向简历版本

> 用途：面向 AI Agent 工程、LLM 应用开发、AI 工具链、Developer Productivity、智能自动化平台等岗位。
>
> 隐私说明：本文档只沉淀可公开讨论的项目与技能表达，不包含联系方式、邮箱、家庭住址、证件号等个人敏感信息。

## 求职定位

AI Agent 开发 / LLM 应用开发 / 自动化 Agent 工程实习生

## 简历摘要

计算机科学与技术本科在读，具备 Python 工具开发、LangGraph workflow 编排、Playwright/CDP 浏览器自动化、SQL 数据分析和测试自动化落地经验。实习期间独立设计并实现 HUI Web 自动化测试生成 Agent，将高风险浏览器、SSH、数据库和 pytest 操作封装为确定性工作流节点，仅在 SQL 修复、模板审查和失败诊断等环节引入 LLM 辅助决策，完成从页面探勘、数据预埋、用例生成到验证报告的闭环自动化。

## 项目经历

### 基于 LangGraph 的 HUI Web 自动化测试生成 Agent

**技术栈：** Python / LangGraph / Pydantic / Playwright / Chrome CDP / Stagehand / pytest / MySQL / MCP / Git

- 设计“确定性 workflow + LLM 局部决策”的 Agent 架构，将浏览器接管、页面探勘、SSH 日志抓取、SQL dry-run、pytest 验证等高风险动作收敛到可观测、可恢复的状态图节点中。
- 基于 LangGraph 编排菜单初始化、登录接管、表单扫描、日志捕获、SQL 分析、表分类、SQL 生成、计划校验、用例生成、验证修复和报告输出等节点，支持 thread_id 状态追踪、resume、retry 和 batch 执行。
- 使用 Pydantic 定义运行状态、菜单配置、表单字段、测试输入计划、SQL 插入计划和修复动作等结构化数据模型，提升 Agent 节点之间的数据一致性和可调试性。
- 基于 Playwright + Chrome CDP 复用已登录浏览器会话，结合 Stagehand 识别 HUI 定制化组件、必填字段、查询条件和操作按钮，为后续测试动作生成提供结构化页面上下文。
- 通过 SSH 增量抓取应用日志，解析 SQL 表名、WHERE/JOIN 条件和分片路由，结合 schema cache、基础表快照和 Excel 预设值生成可 dry-run 的测试数据预埋计划。
- 在 SQL 表分类、SQL 生成修复、pytest 模板审查和失败诊断等节点引入 LLM，配合危险 SQL 禁止、限定写入范围、dry-run 回滚、失败熔断和 snapshot/restore 控制自动化风险。
- 将 Stagehand/CDP 页面勘测能力封装为 MCP 工具服务，暴露表单扫描、动作构建和 Node 依赖检查能力，支持外部 Agent 复用底层页面探勘能力。
- 项目 3 周内落地真实业务环境，覆盖 56 个业务菜单、生成约 2000 条 pytest 用例，将查询类菜单测试周期从约 1 个月压缩至 4 天，并推广至组内 5+ 人使用。

## 实习经历

### 测试开发实习生｜金融科技企业

- 参与财富管理业务测试与自动化建设，结合日志、SQL、页面行为和接口响应定位缺陷原因，并沉淀为可复用的自动化诊断经验。
- 主导设计并独立实现 Web UI 自动化测试生成 Agent，将人工页面探勘、日志分析、数据构造、脚本生成和验证修复流程抽象为可恢复的 LangGraph workflow。
- 基于 Git 维护 Agent 代码仓库、配置模板、运行文档和排障流程，推动工具在组内复用，降低非 Agent 开发背景成员使用自动化测试生成能力的门槛。

## 专业技能

- **Agent 工程：** 熟悉 LangGraph workflow 编排、状态驱动执行、resume/retry、结构化输出、工具调用边界和自动修复闭环设计。
- **LLM 应用：** 熟悉 Claude Code、Codex 等 AI 辅助研发工具，理解 LLM 在 SQL 修复、失败诊断、模板审查等场景中的适用边界和风险控制。
- **Python 工程：** 熟悉 Python CLI 工具、Pydantic 数据建模、自动化脚本、日志处理和配置化工具开发。
- **浏览器自动化：** 熟悉 Playwright、Chrome CDP、Stagehand 页面探勘和 Web UI 自动化测试动作生成。
- **数据与测试：** 熟悉 MySQL、SQL 分析、测试数据预埋、pytest 自动化验证、接口/功能测试和缺陷定位。
- **工程化工具：** 熟悉 Git、Linux、SSH、运行日志沉淀、配置模板维护和团队工具落地。

## 面试主线

1. **为什么需要 Agent：** HUI 后台自动化测试生成链路长，涉及页面、日志、数据库、SQL、pytest 和报告，不适合单次 LLM 调用，需要可恢复 workflow。
2. **为什么不是纯 LLM：** 浏览器、数据库、SSH 和 pytest 都是高风险动作，必须由确定性节点控制；LLM 只负责需要推理的 SQL 修复、模板审查和失败诊断。
3. **如何保证可靠：** Pydantic 结构化状态、thread_id、checkpoint、SQL dry-run、snapshot/restore、失败重试、熔断、事件日志和回归验证。
4. **如何工具化：** CLI 支持 doctor、menus、run、batch、status、resume、retry；MCP 服务把页面勘测能力暴露为可复用工具。
5. **业务结果：** 真实业务环境落地，覆盖 56 个菜单，生成约 2000 条用例，将测试周期从月级压缩到天级。

