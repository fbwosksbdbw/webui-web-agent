# Smoke Run Guide

本文档描述从零开始运行 HUI Agent 的最小端到端流程，涵盖前置依赖、配置填写、启动命令和常见故障排查。

---

## 前置条件

### Python 环境

Python >= 3.10，安装项目及开发依赖：

```bash
python -m pip install -e ".[dev]"
```

### Node.js（可选，页面探索需要）

Node.js >= 18。如需使用 stagehand 做表单字段扫描：

```bash
cd knowledge/scripts/stagehand_explorer
npm ci
cd -
```

未安装时 step 1a（form_scan）会自动跳过 stagehand 探索，回退到 CDP 直接扫描。

### Chrome 浏览器

需要 Google Chrome（Windows 下为 `chrome.exe`），路径写入 Excel 配置的 `chrome_path` 字段。Agent 通过 `launch_chrome.py` 自动启动 Chrome 并打开 CDP 远程调试端口。

### 外部项目配置

`project_dir` 指向被测应用项目根目录（如 `F:/ifm60`）。该项目需包含 `conf/config.py`，内含数据库连接信息。Agent 通过 `env_key` + `project_dir` 定位到具体环境的 MySQL 连接参数。

---

## Excel 配置

配置文件位于 `knowledge/config/test_config.xlsx`，包含三张必须表：

### 环境配置

两列键值对（`配置项` / `配置值`），关键字段：

| 配置项 | 说明 | 示例 |
|--------|------|------|
| `cdp_url` | Chrome CDP 地址 | `http://localhost:9222` |
| `chrome_path` | Chrome 可执行文件路径 | `C:/Program Files/Google/Chrome/Application/chrome.exe` |
| `project_dir` | 被测项目根目录 | `F:/ifm60` |
| `env_key` | 环境标识 | `ifm60-trust` |
| `branch_user` | 登录用户名 | `admin01` |
| `branch_password` | 登录密码 | `123456` |
| `app_server_host` | 应用服务器 SSH 地址 | `192.168.1.100` |
| `app_server_port` | SSH 端口 | `22` |
| `app_server_user` | SSH 用户名 | `root` |
| `app_server_password` | SSH 密码 | `****` |
| `log_path_<module>` | 各模块日志目录 | `/app/logs/trust` |
| `log_file` | 日志文件名 | `catalina.out` |

### 菜单导入

定义待测试的菜单列表，关键字段：`菜单名称`、`导航一级菜单`、`导航二级菜单`、`菜单URL`、`预埋SQL文件名`、`状态`。`状态 = "已完成"` 的菜单会被自动跳过。

### 预埋数据

定义表单填充的默认值，按模块区分。关键预设字段：`customer_id`、`ta_code_1`、`prd_code`、`branch_no`、`hq_branch_no`、`query_date_start`、`query_date_end`。`适用模块` 为 `公共` 或 `所有菜单` 的条目对所有菜单生效。

---

## 最小运行命令

```bash
# 1. 安装依赖
python -m pip install -e ".[dev]"

# 2. （可选）安装 Node 依赖
cd knowledge/scripts/stagehand_explorer && npm ci && cd -

# 3. 检查环境
hui-agent doctor

# 4. 运行单个菜单
hui-agent run --menu "基金分红查询"

# 5. 批量运行所有未完成菜单
hui-agent batch
```

常用辅助命令：

```bash
# 列出所有未完成菜单
hui-agent menus

# 查看某个 thread 的运行结果
hui-agent status --thread-id <id>

# 从指定步骤重试
hui-agent retry --thread-id <id> --from-step 2b
```

---

## 环境变量

LLM 相关（可选 — 未配置时 Agent 正常运行，仅跳过 LLM 辅助的修复/审查步骤）：

| 变量 | 备选名 | 默认值 | 说明 |
|------|--------|--------|------|
| `HUI_LLM_API_KEY` | `OPENAI_API_KEY` | — | LLM API 密钥 |
| `HUI_LLM_BASE_URL` | `OPENAI_BASE_URL` | `https://api.openai.com/v1` | API 地址 |
| `HUI_LLM_MODEL` | `OPENAI_MODEL` | `gpt-4.1` | 模型名 |
| `HUI_LLM_TIMEOUT` | `OPENAI_TIMEOUT` | `180` | 请求超时（秒） |

Agent 启动时自动加载工作目录和项目根目录下的 `.env` 文件。

Agent 设置：

| 变量 | 说明 |
|------|------|
| `HUI_SKILL_ROOT` | 覆盖 skill 根目录（默认自动检测 `knowledge/`） |
| `HUI_RUNS_ROOT` | 覆盖运行输出目录（默认 `_local/runs`） |
| `HUI_CHECKPOINT_DB` | 覆盖 LangGraph checkpoint 数据库路径 |

---

## 浏览器状态

Agent 通过 `launch_chrome.py` 管理 Chrome 生命周期：

1. **启动前**：脚本会先杀掉所有已运行的 Chrome 进程（`taskkill /F /IM chrome.exe`），等待 2 秒。
2. **启动**：使用临时 profile（`<tempdir>/chrome_cdp_profile`）启动 Chrome，打开 `--remote-debugging-port`（端口从 `cdp_url` 解析）。
3. **登录**：step 0b 通过 CDP 自动填写用户名/密码并提交，等待页面出现"欢迎"标识确认登录成功。
4. **导航**：登录后自动导航到菜单 URL。

如需手动启动 Chrome（step 0a 完成后 config.json 已生成）：

```bash
py knowledge/scripts/launch_chrome.py --config <project_dir>/<test_case_dir>/<二级菜单>/<菜单名>/config.json --auto
```

---

## 数据库权限

Agent 需要访问以下 MySQL 数据库：

### 连接方式

通过 `conf/config.py`（由 `project_dir` + `env_key` 定位）获取 pymysql 连接参数。

### 所需权限

| 操作 | 用途 | 涉及数据库 |
|------|------|-----------|
| `SELECT` | 分片路由查询（`tbclientmap`）、schema 缓存（`DESCRIBE` / `SHOW TABLES`）、SQL return precheck | `ui_bc` + 业务库 |
| `INSERT` / `DELETE` | 预埋数据试执行（事务内 ROLLBACK，不持久化） | 业务库（如 `ui_trust2`, `ui_fund2`） |
| `DESCRIBE` / `SHOW TABLES` | 构建 schema 缓存，验证 SQL 列名正确性 | 所有涉及的库 |

### 业务数据库列表

根据模块不同，可能涉及：`ui_bc`、`ui_trust1/2`、`ui_fund1/2`、`ui_dxtrust*`、`ui_dxfund*`、`ui_pension*` 等。数据库名通过分片路由公式动态计算（基于 `customer_id` 后 8 位）。

### Schema 缓存刷新

当 schema 缓存过期或缺失时：

```bash
# 按模块刷新
hui-agent run --menu "菜单名" --refresh-schema-module dxfund

# 按数据库刷新
hui-agent run --menu "菜单名" --refresh-schema-db ui_fund2

# 强制刷新所有表
hui-agent run --menu "菜单名" --refresh-schema-module dxfund --refresh-schema-force
```

---

## Pipeline 步骤概览

完整流程包含 11 个步骤，按顺序执行：

| 步骤 | 名称 | 说明 |
|------|------|------|
| `0a` | menu_init | 读取 Excel 配置，初始化 case_plan |
| `0b` | browser_login | 启动 Chrome，登录系统，导航到目标菜单 |
| `1a` | form_scan | 扫描页面表单字段，匹配预埋数据 |
| `1b` | log_capture | 通过 CDP 操作表单 + SSH 抓取后台 SQL 日志 |
| `2a` | log_analysis | 分析 SQL 日志，提取 SELECT/JOIN/WHERE 信息 |
| `2b` | table_classify | 分类表（业务表/基础表/系统表），计算分片路由 |
| `2c` | sql_generate | 生成预埋 SQL，dry-run 验证，return precheck |
| `2d` | plan_verify | 验证方案完整性 |
| `3` | case_generate | 生成 pytest 测试用例 |
| `4` | verify | 运行 pytest，失败时 LLM 辅助修复 |
| `5` | report | 生成报告，标记菜单完成状态 |

---

## 常见故障排查

### Chrome 启动失败 / CDP 连接超时

确认 `chrome_path` 指向有效的 Chrome 可执行文件。`launch_chrome.py` 会先杀掉所有已运行的 Chrome 实例 — 确保没有其他重要 Chrome 窗口。CDP 端口（默认 9222）未被其他程序占用。

### SSH 认证失败

step 1b 通过 SSH 连接应用服务器抓取日志。确认 `app_server_host`、`app_server_port`、`app_server_user`、`app_server_password` 正确，服务器可达。日志路径（`log_path_<module>`）在服务器上存在。

### Schema 缓存缺失

step 2c 的 dry-run 需要 schema 缓存来验证列名。首次运行新模块时，使用 `--refresh-schema-module <module>` 预构建缓存。

### LLM Advisor 未配置

不影响正常运行。以下功能在缺少 LLM 密钥时自动跳过：SQL advisor 审查（step 2b）、SQL 修复重试（step 2c/4）、测试用例审查（step 3/4）。

### 数据库连接失败

确认外部项目的 `conf/config.py` 存在且 `env_key` 对应的数据库配置正确。Agent 使用 `pymysql` 直连 MySQL，确认网络可达、用户名密码正确、目标数据库存在。

### pytest 测试失败（step 4）

Agent 会自动尝试 LLM 辅助修复（最多 `max_attempts` 次）。如果 LLM 未配置，失败后直接记录结果。可通过 `hui-agent retry --thread-id <id> --from-step 4` 手动重试。

---

## 输出位置

运行产出物分布在两个目录体系中：

### RunStore（per-thread 运行日志）

位于 `_local/runs/`（由 `--runs-root` 控制）：

```
_local/runs/
  checkpoints.sqlite              # LangGraph checkpoint，支持 resume / retry
  {thread_id}/
    events.jsonl                  # 结构化事件流
    agent.log                     # 可读日志
    result.json                   # 最终 HUITestState 快照
    pytest_attempt_{N}.log        # 每轮 pytest 输出
```

### 菜单级产物（case_dir）

由 `config_reader.py` 写入 `{project_dir}/{TEST_CASE_DIRS[level1]}/{二级菜单}/{菜单名}/`，即 `state.case_dir`：

```
{case_dir}/
  config.json                     # 环境配置快照
  case_plan.json                  # 完整 case plan（shard、preset_strategy、insert_plans 等）
  explore_result.json             # 表单扫描结果
  query_log.txt                   # 抓取的后台 SQL 日志
  sql_analysis/
    ast_selects.sql               # 解析后的 SELECT 语句
    ast_selects_routed.sql        # 加分片路由前缀后的 SELECT
  reports/
    summary.md                    # 最终报告
```

### 预埋 SQL 和测试用例

写入路径由外部项目的 `conf/config.py` 中 `SQL_DATA_DIRS` 和 `TEST_CASE_DIRS` 映射表决定：

```
{SQL_DATA_DIRS[level1]}/{二级菜单}/{菜单名}/
  {func_code}_base.sql            # 基础预埋 SQL
  {func_code}.sql                 # 业务预埋 SQL

{TEST_CASE_DIRS[level1]}/{二级菜单}/{菜单名}/{菜单名}{_后缀}/
  test_{func_code}{_后缀}.py      # 生成的 pytest 测试用例
```
