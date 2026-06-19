# 测试开发方向简历版本

> 用途：面向测试开发、SDET、QA Automation、测试平台开发、金融科技测试开发等岗位。
>
> 隐私说明：本文档只沉淀可公开讨论的项目与技能表达，不包含联系方式、邮箱、家庭住址、证件号等个人敏感信息。

## 求职定位

测试开发 / SDET / 自动化测试平台开发实习生

## 简历摘要

计算机科学与技术本科在读，具备金融业务测试、Web UI 自动化、接口测试、SQL 数据分析和测试工具开发经验。实习期间围绕财富管理 HUI 后台 56+ 业务菜单自动化建设痛点，独立设计并落地基于 LangGraph、Playwright、pytest 和 MySQL 的 Web UI 自动化测试生成 Agent，将页面探勘、SQL 日志分析、测试数据预埋、pytest 用例生成、验证修复与报告输出串联为可恢复、可批量执行的工作流，推动查询类菜单自动化测试交付周期从月级压缩至天级。

## 项目经历

### 基于 LangGraph 的 HUI Web 自动化测试生成 Agent

**技术栈：** Python / LangGraph / Playwright / pytest / Chrome CDP / Stagehand / MySQL / Pydantic / Git / Linux / SSH

- 面向财富管理业务 56+ HUI 菜单自动化测试痛点，独立设计并落地全链路测试生成 Agent，覆盖页面探勘、SQL 日志分析、测试数据预埋、pytest 用例生成、验证修复与报告输出。
- 基于 LangGraph 编排菜单初始化、浏览器登录接管、Stagehand/CDP 表单扫描、日志抓取、SQL 分析、用例生成和 pytest 验证等节点，支持单菜单/批量执行、失败重试、状态追踪和运行日志沉淀。
- 基于 Playwright + Chrome CDP 复用已登录会话，自动识别 HUI 定制组件、必填字段、查询条件和操作按钮，生成 queryall、querysingle、querymulti、reset、preview、branch 等 pytest 用例模板。
- 通过 SSH 增量抓取后端日志，解析 SQL 表名、WHERE/JOIN 条件和分库分表线索，结合 MySQL schema cache、基础表快照和 Excel 预设数据生成 DELETE + INSERT 测试数据预埋脚本。
- 构建 SQL dry-run、target pytest、regression pytest 多层校验与 repair loop，失败前自动 snapshot、失败后 restore，降低生成脚本后的人工排查成本和环境污染风险。
- 3 周内从零交付并落地真实业务环境，覆盖 56 个业务菜单、生成约 2000 条 pytest 用例，将查询类菜单测试周期从约 1 个月压缩至 4 天，并推广至组内 5+ 人使用。

## 实习经历

### 测试开发实习生｜金融科技企业

- 参与财富管理业务功能与接口测试，基于等价类、边界值和业务场景设计测试用例，结合日志、SQL、接口响应和前端报错定位缺陷原因并跟踪闭环。
- 针对 HUI 菜单自动化用例编写成本高、依赖页面探勘和测试数据预埋的问题，主导设计并独立开发 Web UI 自动化测试生成 Agent，将自动化脚本交付从月级压缩至天级。
- 维护自动化测试仓库、Excel 配置模板、运行文档和问题排查流程，支持组内成员复用生成工具并降低 Playwright/pytest 脚本编写门槛。

## 专业技能

- **编程语言：** 熟悉 Python 自动化脚本、CLI 工具开发、数据处理与工程化重构；具备 Java 基础，理解面向对象、集合、异常机制和多线程常见场景。
- **测试开发：** 熟悉 pytest、Playwright、Chrome CDP、接口测试和 Web UI 自动化测试流程，能够结合页面行为、日志、SQL 和断言结果定位自动化失败原因。
- **数据库与后端：** 熟悉 MySQL、常用 SQL、事务、索引和测试数据预埋场景；了解 Spring Boot、Spring MVC、MyBatis/MyBatis-Plus 基础。
- **工程化工具：** 熟悉 Git、Linux 常用命令、SSH 远程交互、配置化工具维护、测试报告沉淀和自动化运行文档编写。
- **AI 辅助研发：** 熟悉 Claude Code、Codex 等 AI 辅助研发工具，理解结构化输出、自动修复闭环和 LLM 在自动化测试生成场景中的安全边界设计。

## 面试主线

1. **业务痛点：** 人工写 HUI 查询类菜单自动化用例，需要页面探勘、SQL 分析、数据预埋、脚本调试和失败排查，周期长且强依赖环境。
2. **技术方案：** 用 LangGraph 把自动化测试生成拆成确定性节点，用 Playwright/CDP 接管浏览器，用 Stagehand 探勘页面，用 SSH 抓日志，用 MySQL schema cache 生成预埋 SQL。
3. **可靠性设计：** SQL dry-run、pytest 单用例验证、全量回归、snapshot/restore、失败重试、熔断和日志沉淀。
4. **业务结果：** 3 周落地，覆盖 56 个菜单，生成约 2000 条用例，将查询类菜单测试周期从约 1 个月压缩至 4 天。

