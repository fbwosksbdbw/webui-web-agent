# HUI Playwright LangGraph Agent 使用手册

webui 自动化 web 端。

HUI Playwright LangGraph Agent 是一个面向 HUI 后台菜单自动化测试生成的 Python CLI 工具。它把原有 HUI Playwright skill 封装成可恢复、可批量、可观测的 LangGraph workflow，用于自动完成菜单探勘、SQL 日志分析、预埋数据生成、pytest 用例生成、验证修复和报告输出。

本项目定位为 **确定性 workflow + 少量 LLM 决策节点**。浏览器、CDP、SSH、数据库、SQL dry-run 和 pytest 均属于高风险执行环节，因此主流程由确定性脚本和状态图控制，LLM 仅用于 SQL 判断、SQL 修复、模板审查和失败诊断等需要推理的场景。

## 适用场景

本工具适用于已有 HUI 自动化测试工程的团队。使用方需要具备以下基础条件：

- 可访问的 HUI 测试环境。
- 可连接的应用日志服务器。
- 可连接的数据库环境。
- 已配置好的自动化项目，通常包含 `conf/config.py`。
- 可运行 pytest 的 Python 自动化测试环境。
- 已维护的菜单配置 Excel。

Agent 负责生成和验证用例，但不替代业务自动化项目。生成出来的 pytest 用例会通过 Excel 中的 `project_dir` 定位自动化项目，并使用该项目下的 `conf/config.py`、公共 helper、SQL 执行器和 pytest 配置。

## 目录结构

```text
hui-playwright-agent/
├── README.md                         # 团队使用手册
├── pyproject.toml                    # Python 包配置，安装后提供 hui-agent 命令
├── agent_config.yaml                 # Agent 默认运行配置
├── .env.example                      # LLM 配置模板，不包含真实密钥
├── src/hui_agent/                    # LangGraph、CLI、状态模型、节点实现
├── knowledge/                        # 内置 skill 知识库和脚本资产
│   ├── config/test_config.xlsx       # 环境、菜单、预埋数据配置
│   ├── config/base_tables.json       # 基础表、产品表、参考数据
│   ├── config/schema_cache.json      # 表结构缓存
│   ├── scripts/                      # CDP、Stagehand、SQL、配置读取脚本
│   └── README.md                     # 原 skill 资产说明
├── runs/                             # 执行事件、日志、状态和 checkpoint
└── tests/                            # 单元测试和集成测试
```

## 安装

### Python 依赖

要求 Python 3.10 或以上。

```powershell
cd <agent_root>
python -m pip install -e ".[dev]"
```

安装后可使用：

```powershell
hui-agent menus
```

如果命令未加入 PATH，可使用模块方式：

```powershell
python -m hui_agent.cli menus
```

### Node / Stagehand 依赖

Stagehand 探勘脚本位于：

```text
knowledge/scripts/stagehand_explorer
```

首次部署时执行：

```powershell
cd <agent_root>\knowledge\scripts\stagehand_explorer
npm install
```

本项目是独立项目，Stagehand 依赖应安装在当前项目的 `knowledge/scripts/stagehand_explorer/node_modules` 下，不应依赖其他目录中的 `node_modules`。

## 配置

### Excel 配置

默认配置文件：

```text
knowledge/config/test_config.xlsx
```

主要 sheet：

| Sheet | 作用 |
|------|------|
| `环境配置` | Chrome/CDP、日志服务器、日志路径、`project_dir`、账号、数据库环境等 |
| `菜单导入` | 待执行菜单列表，`状态=已完成` 的菜单会被跳过 |
| `预埋数据` | TA、产品、客户、账号、机构等稳定测试数据 |

`预埋数据` sheet 只维护业务可读的中文字段名、默认值、说明和适用模块，不需要维护数据库英文列名或内部英文 key。中文字段名到内部语义、数据库列名的映射集中维护在 `knowledge/config/field_semantics.json`。

`菜单导入` 建议维护这些列：

| 列名 | 作用 |
|------|------|
| `状态` | `已完成` 会被跳过；空值或 `未完成` 可执行 |
| `菜单名称` | CLI `--menu` 使用的菜单名 |
| `导航一级菜单` / `导航二级菜单` / `导航三级菜单` | 用于定位菜单、生成用例目录和 Allure 层级 |
| `owner` | 生成 pytest 的 `@allure.label("owner", "...")`；也兼容 `负责人`、`维护人`、`责任人` |

详情/预览按钮不靠探勘阶段的空表页面判断。Agent 会先生成结果预览用例；如果验证时其他用例均成功，唯独预览用例因为找不到 `详情/预览/查看` 按钮失败，则自动判定该菜单没有详情按钮，移除预览用例并记录为跳过。

关键配置项：

- `project_dir`：自动化项目根目录。生成的 pytest 用例运行时会从此目录读取 `conf/config.py`。
- `env_key`：自动化项目中的环境标识，需要能在 `conf/config.py` 中找到。
- `cdp_url`：Chrome 远程调试地址。
- `chrome_path`：Chrome 可执行文件路径。
- `log_path_*` 和 `log_file`：应用日志目录和日志文件名。
- `app_server_*`：日志服务器 SSH 连接信息。

### LLM 配置

复制 `.env.example` 为 `.env`：

```powershell
Copy-Item .env.example .env
```

配置示例：

```text
HUI_LLM_API_KEY=<api-key>
HUI_LLM_BASE_URL=https://<openai-compatible-host>/v1
HUI_LLM_MODEL=<model-name>
```

`.env` 加载顺序：

- `<agent_root>/.env`
- 当前命令执行目录下的 `.env`

真实 API key 不应提交到版本库，也不应随项目包直接分发。

### Agent 配置

默认配置文件：

```text
agent_config.yaml
```

当前 CLI 主要通过命令参数和 `.env` 控制运行。`agent_config.yaml` 用于记录默认运行目录、checkpoint、模型和重试策略，供后续扩展和团队统一配置使用。

## CLI 命令

### 列出未完成菜单

```powershell
python -m hui_agent.cli menus
```

读取 `knowledge/config/test_config.xlsx` 的 `菜单导入` sheet，并过滤 `状态=已完成` 的菜单。

### 执行单个菜单

```powershell
python -m hui_agent.cli run --menu "菜单名"
```

执行完成后输出状态 JSON。常用字段：

| 字段 | 说明 |
|------|------|
| `thread_id` | 本次执行唯一 ID |
| `status` | `success`、`skipped`、`blocked`、`running` |
| `completed_steps` | 已完成步骤 |
| `current_step` | 当前步骤 |
| `case_dir` | 菜单生成物目录 |
| `failure_scope` | 失败范围，可能是 `menu`、`config`、`environment` |
| `failure_reason` | 失败原因 |

### 批量执行菜单

```powershell
python -m hui_agent.cli batch
```

批量执行规则：

- 从 Excel 读取未完成菜单。
- 每个菜单生成独立 `thread_id`。
- 菜单串行执行，不并发。
- 单菜单失败时记录为 `skipped`，继续后续菜单。
- 共享环境失败时记录为 `blocked`，批量停止。

v1 不支持并发执行菜单。Chrome/CDP、SSH 日志和数据库均为共享资源，并发执行需要独立 Chrome profile、独立 CDP port 和浏览器租约机制。

### 查看状态

```powershell
python -m hui_agent.cli status --thread-id <thread_id>
```

### 恢复执行

```powershell
python -m hui_agent.cli resume --thread-id <thread_id>
```

### 从指定步骤重跑

```powershell
python -m hui_agent.cli retry --thread-id <thread_id> --from-step 2c
python -m hui_agent.cli resume --thread-id <thread_id>
```

`retry` 只负责回退状态；继续执行需要再调用 `resume`。

## Workflow 步骤

| 步骤 | 节点 | 说明 |
|------|------|------|
| `0a` | `menu_init` | 从 Excel 初始化菜单，写入 `config.json` 和 `case_plan.json` |
| `0b` | `browser_login` | 启动/连接 Chrome，登录并导航到菜单 |
| `1a` | `form_scan` | Stagehand/CDP 扫描表单字段，生成字段摘要和 `case_inputs` |
| `1b` | `log_capture` | 按规划字段触发查询，抓取后端 SQL 日志 |
| `2a` | `log_analysis` | 分析 SQL 日志，提取表、where、join、分片和系统表 |
| `2b` | `table_classify` | 分类基础表、业务表、禁止预埋表，确定目标库 |
| `2c` | `sql_generate` | 生成预埋 SQL，执行 dry-run，必要时触发 LLM 修复 |
| `2d` | `plan_verify` | 校验 `case_plan.json` 必要信息 |
| `3` | `case_generate` | 生成 pytest 用例 |
| `4` | `verify` | 运行 pytest，失败时修复并回归验证 |
| `5` | `report` | 输出报告 |

## LLM 介入点

LLM 不是主执行器，只在以下场景介入：

- SQL 表分类和预埋策略判断。
- 分行/总行过滤判断。
- `insert_plans` 为空或缺必填字段时的 SQL 生成修复。
- SQL dry-run 失败后的 SQL 修复。
- pytest 模板审查。
- pytest 失败后的用例或 SQL 修复建议。

支持的修复动作：

| 动作 | 说明 |
|------|------|
| `patch_sql` | 替换当前菜单 SQL 文件中的指定文本 |
| `regenerate_sql` | 重写当前菜单 `case_plan.sql_paths` 内的 SQL 文件 |
| `regenerate_case` | 重写当前菜单生成的 pytest 文件 |
| `mark_unfixable` | 记录无法自动修复以及缺失证据 |

安全边界：

- 只允许修改当前菜单登记过的 SQL 或 pytest 文件。
- `regenerate_sql` 禁止 `DROP`、`ALTER`、`TRUNCATE`、`CREATE`、`GRANT`、`REVOKE` 等危险语句。
- SQL 修复后必须重新 dry-run。
- pytest 修复后先跑失败用例，再跑全量回归。
- 连续失败达到上限后熔断并记录失败报告。

## SQL 预埋策略

SQL 生成优先使用确定性数据：

- Excel `预埋数据` sheet 中的稳定测试值。
- 表单探勘生成的 `case_inputs`。
- `base_tables.json` 中维护的基础表、产品表、行情表参考记录。
- `schema_cache.json` 和 `config/cache/*.json` 中的表结构缓存。
- 当前菜单真实日志中的 where/join 字段和物理表路由。

步骤 2c 会额外把 SQL 预埋需求写入 `case_plan.sql_requirements`。该字段记录每张表、物理表、关键列、值来源、字段用途和是否允许默认值；`InsertPlan` 会优先使用其中已有明确值的列，缺失关键值的失败策略仍由后续验证和修复流程处理。

原则：

- 不依赖环境中随机存在的业务数据。
- 系统表不做预埋，例如 `tsys_menu`、`tbsequence`。
- 产品表、TA 表、行情表等基础依赖应在 `base_tables.json` 中维护完整参考记录。
- 表结构优先使用缓存；缓存缺失时再查数据库刷新。
- 预埋 SQL 必须通过事务回滚 dry-run。

## 用例生成规则

用例生成基于 `CaseInputPlan`，不等于全字段填充。

| 用例 | 规则 |
|------|------|
| `queryall` | 无必填字段时尽量空条件查询；有必填字段时只填必填 |
| `querysingle` | 使用一个稳定业务条件 |
| `querymulti` | 使用多个稳定业务条件，不强制填满所有字段 |
| `reset` | 先填条件，再点击重置，再断言控件清空 |
| `preview` | 先查询出结果，再点击预览/查看/详情，并断言详情内容 |
| `branch` | 有分行过滤时生成，使用分行账号，做精确条数断言 |
| `headquarter` | 有分行过滤时生成，使用总行账号，做精确条数断言 |

绑定字段规则：

- `客户标识类型 + 客户标识` 是绑定组。
- `账号类型 + 账号` 是绑定组。
- 如果绑定组初始均非必填，探勘阶段会尽量覆盖 with/without 两类日志。
- 一旦选择类型后标识字段动态变为必填，用例生成必须按绑定组闭合。

## 输出文件

菜单生成物通常位于：

```text
<project_dir>/testcases/综合理财平台6.0/<一级菜单>/<二级菜单>/<菜单名>/
├── config.json
├── case_plan.json
├── query_log.txt
├── explore_result.json
├── generated_tests/
└── reports/summary.md
```

SQL 预埋文件通常位于：

```text
<project_dir>/testcases/综合理财平台6.0/<一级菜单>/数据预埋/<二级菜单>/<菜单名>/
```

Agent 运行日志位于：

```text
<agent_root>/runs/<thread_id>/
├── events.jsonl
├── result.json
├── agent.log
└── pytest_attempt_N.log
```

排障建议查看顺序：

1. 控制台实时日志。
2. `<agent_root>/runs/<thread_id>/agent.log`。
3. `<agent_root>/runs/<thread_id>/events.jsonl`。
4. 当前菜单的 `case_plan.json`。
5. 当前菜单 SQL 文件和 `query_log.txt`。

## 常见问题

### `hui_agent.cli` 不是命令

错误示例：

```powershell
hui_agent.cli retry ...
```

正确方式：

```powershell
python -m hui_agent.cli retry --thread-id <thread_id> --from-step 2c
```

或安装后使用：

```powershell
hui-agent retry --thread-id <thread_id> --from-step 2c
```

### Stagehand 报 `Cannot find module 'ts-node/register'`

原因通常是当前项目的 Node 依赖未安装。处理方式：

```powershell
cd <agent_root>\knowledge\scripts\stagehand_explorer
npm install
```

### SQL dry-run 重复主键

当前 dry-run 会把整份 SQL 放在同一个事务中顺序执行，最后统一 rollback。`DELETE -> INSERT` 在 dry-run 中应保持真实执行语义。

仍出现重复主键时，重点检查：

- `DELETE WHERE` 是否覆盖主键或唯一索引。
- `base_tables.json` 中参考记录的主键是否完整。
- LLM 修复动作是否为 `patched`，还是被 `skipped`。
- `case_plan.sql_repair_attempts` 和控制台 `repair_action_detail`。

### `no insert plans generated`

常见原因：

- 表结构缓存缺失。
- `base_tables.json` 缺少该表参考记录。
- 必填列无法从 presets、case_inputs 或 base_tables 推导。

该类错误会优先尝试 LLM SQL 修复。若未配置 LLM，或 LLM 未返回可执行动作，菜单会失败并记录原因。

### LLM 调用不可见

控制台应出现以下事件：

```text
llm_sql_repair_started
llm_sql_repair_finished ... elapsed_ms=...
llm_repair_started
llm_repair_finished ... elapsed_ms=...
```

没有这些事件时，优先检查 `.env` 是否存在、key 是否有效、base URL 是否可访问。

### 下拉框选到分页框

生成用例应使用公共 helper 的安全下拉选择函数，并按 label 精确定位字段。避免使用页面全局文本搜索或模糊匹配。

### 用例出现 `assert True`

`assert True` 属于无效断言。应检查：

- `case_plan.template_review`
- 对应生成的 pytest 文件
- verify 阶段的 `repair_attempts`

## 团队迁移检查清单

分发给其他团队或其他环境前，建议完成以下检查：

- `knowledge/config/test_config.xlsx` 已替换为目标环境配置。
- Excel `project_dir` 指向目标自动化项目根目录。
- 目标自动化项目存在 `conf/config.py`。
- `conf/config.py` 包含目标 `env_key`、登录地址、数据库配置和 SQL 路径配置。
- `knowledge/config/base_tables.json` 包含目标模块所需基础表、产品表、TA 表、行情表参考记录。
- `knowledge/config/schema_cache.json` 和 `knowledge/config/cache/*.json` 覆盖常用表结构。
- `knowledge/scripts/stagehand_explorer/node_modules` 已安装。
- `.env` 已由使用方本地配置，且未包含在分发包中。
- Chrome/CDP、SSH、数据库网络连通。
- 分行账号、总行账号、客户号、产品号等预埋数据已按目标环境调整。

敏感信息处理要求：

- 不随项目分发真实 API key。
- 不随项目分发真实生产密码。
- 不在 README 或代码中写死个人机器绝对路径。
- Excel 可以保留字段结构，敏感值由使用方在本地填写。

## 开发验证

修改 Agent 后建议执行：

```powershell
python -m pytest -q
```

测试覆盖范围包括：

- CLI 命令和运行日志格式。
- 状态序列化、resume、retry。
- 表单字段规划。
- SQL 预埋计划生成。
- base table 参考数据。
- SQL dry-run 事务语义。
- LLM structured-output 行为。
- pytest 失败修复和回归验证。

## 快速命令索引

```powershell
cd <agent_root>
python -m pip install -e ".[dev]"
python -m hui_agent.cli menus
python -m hui_agent.cli run --menu "菜单名"
python -m hui_agent.cli status --thread-id <thread_id>
python -m hui_agent.cli retry --thread-id <thread_id> --from-step 2c
python -m hui_agent.cli resume --thread-id <thread_id>
python -m hui_agent.cli batch
```
