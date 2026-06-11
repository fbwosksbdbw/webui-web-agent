# HUI + Playwright 自动化测试 Skill

> 给定一个 HUI 后台菜单名，自动完成：探勘表单 → 抓取 SQL 日志 → 生成预埋数据 → 逐个生成并验证 pytest 用例 → 输出测试报告。

## 它能做什么

| 能力 | 说明 |
|------|------|
| **智能表单探勘** | 通过 Stagehand CDP 引擎自动扫描 HUI 页面，识别输入框、单选/多选下拉框、操作按钮，输出稳定的 Playwright locator |
| **SQL 日志提取** | SSH 抓取后端日志，自动提取表名、WHERE 字段、分片信息，生成极简预埋 prompt |
| **数据预埋** | 根据日志分析结果生成 INSERT 语句，支持分片表双写、配置表自动预埋 |
| **用例生成** | 基于 8 套模板，自动生成 branch / headquarter / queryall / querysingle / querymulti / reset / preview 用例 |
| **迭代验证** | 逐个运行 pytest、读报错、修代码、重试，连续 3 次失败自动熔断 |
| **测试报告** | 每个 PASSED 用例生成独立 Markdown 报告 |

## 快速开始

### 前置条件

- **Python** >= 3.8 + **Node.js** >= 18
- **Chrome** 以远程调试端口启动（见下方命令）
- 已在 `conf/config.py` 中配置好环境（登录账号、SSH、数据库）
- 已在 `config/test_config.xlsx` 的「菜单导入」sheet 中填好待测菜单

### 第 1 步：启动 Chrome（带远程调试端口）

在 cmd 或 PowerShell 中执行：

```cmd
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --remote-debugging-address=127.0.0.1
```

启动后在 Chrome 中**手动登录 HUI 系统**。Chrome 必须保持运行。

### 第 2 步：安装 Stagehand 探勘引擎（仅首次需要）

```cmd
cd /d "%USERPROFILE%\.claude\skills\hui-playwright-test\scripts\stagehand_explorer" && npm install
```

### 第 3 步：开始一个菜单的测试生成

直接对 AI 说：

```
帮我做"基金分红查询"菜单的用例
```

Skill 会自动执行 6 个步骤：

```
步骤 0a   读取配置（菜单清单、环境、预设值），创建 case_plan.json
步骤 0b   启动浏览器 + 用户登录 + CDP 导航到目标菜单
步骤 1a   Stagehand 扫描表单 → Gate 1a 判定查询策略
步骤 1b   CDP 填表触发查询 + SSH 抓日志 → Gate 1b 判定用例数和预埋策略
步骤 2a   分析 SQL 日志 → 生成 INSERT 语句
步骤 2b   基础表覆盖检查
步骤 2c   生成业务表 INSERT + 统计预期条数
步骤 2d   验证 case_plan.json 完整性
步骤 3    逐个生成 5~7 个 pytest 用例（branch 优先）
步骤 4    逐个运行验证 + 熔断修复
步骤 5    导出 Markdown 测试报告
```

### 手动测试 Stagehand 探勘引擎（可选）

验证探勘引擎是否正常工作：

```cmd
cd /d "%USERPROFILE%\.claude\skills\hui-playwright-test\scripts\stagehand_explorer" && python src\explore_wrapper.py --mode=fast
```

如果正常，会输出当前 Chrome 页面的表单字段 JSON。

## 目录结构

```
hui-playwright-test/
├── skill.md                          # 主编排文件（AI 读这个执行全流程）
├── GATE.md                           # 决策矩阵索引（指向 gates/ 下 3 个 Gate 文件）
├── gates/                            # 按需加载的 Gate 文件
│   ├── gate-1a.md                    #   字段特征 + 查询策略
│   ├── gate-1b.md                    #   分行过滤 + 用例数 + 预埋策略

│   └── gate-3.md                     #   用例生成顺序 + 输出路径
├── README.md                         # ← 你在这里
├── step_state.json                   # 运行时步骤流转状态
│
├── assets/templates/                 # 8 套用例代码模板 + 决策模板
│   ├── case_plan.json                #   决策持久化模板（单数据源）
│   ├── branch.py                     #   分行权限用例
│   ├── headquarter.py                #   总行权限用例
│   ├── queryall.py                   #   标准全量查询（有必填字段时）
│   ├── queryall_clean.py             #   纯净全量查询（无必填字段时）
│   ├── querysingle.py                #   单条件查询
│   ├── querymulti.py                 #   多条件查询
│   ├── reset.py                      #   重置验证
│   └── preview.py                    #   操作按钮预览
│
├── config/
│   ├── test_config.xlsx              # 主配置 Excel（3 个 sheet：环境配置 / 菜单导入 / 预埋数据）
│   ├── table_db_mapping.json         # 表→库映射（3560+ 条，--build-mapping 从数据库生成）
│   ├── menu_urls.json                # 菜单 URL 缓存（91 条 tree_idx 映射）
│   ├── base_tables.json              # 基础配置表预埋数据快照
│   └── cache/                        # 表结构缓存（按模块分文件）
│       ├── 信托.json                  #   信托代销表缓存
│       ├── 基金.json                  #   基金代销表缓存
│       ├── 理财.json                  #   理财代销表缓存
│       ├── 商业养老金.json            #   商业养老金表缓存
│       └── 公共库.json                #   公共库表缓存
│
├── scripts/
│   ├── config_reader.py              # 配置读取 CLI（env / menus / presets / init / compute_shard）
│   ├── state_manager.py              # 步骤流转状态管理（init / check / step_done / checklist）
│   ├── cdp_executor.py               # 纯 Python CDP 执行引擎（填表 / 点击 / 导航 / SSH 日志）
│   ├── sql_extractor.py              # SQL 日志分析器（提取 WHERE 字段 + 缓存 + 极简 prompt）
│   ├── build_schema_cache.py         # 预建 schema 缓存（DESCRIBE 常用表写入 cache/*.json）
│   ├── gen_config_excel.py           # 生成 test_config.xlsx 模板
│   ├── launch_chrome.py              # 启动 Chrome 调试模式
│   └── stagehand_explorer/           # Stagehand CDP 探勘引擎
│       ├── package.json
│       ├── tsconfig.json
│       └── src/
│           ├── explore.ts            #   核心引擎（Node.js / TypeScript，双引擎架构）
│           └── explore_wrapper.py    #   Python 包装层
│
└── references/                       # 参考文档
    ├── code-rules.md                 #   16 条铁律（代码生成规范）
    ├── case-template.md              #   用例模板使用说明
    ├── config-guide.md               #   配置管理 + 导航 + 分片规则
    ├── stagehand-guide.md            #   Stagehand CDP 探勘引擎使用指南
    ├── report-spec.md                #   测试报告格式规范
    └── troubleshooting.md            #   故障排除
```

## 核心架构：5 层分离

```
skill.md（主编排，步骤定义）
   │
   ├── GATE.md（决策矩阵索引，按步骤路由到 gates/）
   │
   ├── state_manager.py（步骤流转，前置检查）
   │
   ├── case_plan.json（决策持久化，单数据源）
   │
   └── references/（详细规则，按需查阅）
```

每步执行固定模式：
```
state_manager.py check step-N → read 当前状态 → 查阅 gates/ 对应 Gate 文件 → 执行 → 更新 case_plan.json → step_done
```

## 核心流程图

```
┌─────────────────────────────────────────────────────────────┐
│  用户指令：帮我做"XXX"菜单的用例                               │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  步骤 0a  config_reader.py init                              │
│  ├─ menus      → 读取待办菜单清单                              │
│  ├─ env        → 登录账号、SSH、数据库连接信息                   │
│  ├─ presets    → TA代码、产品代码、客户号等预设值                 │
│  └─ 创建 case_plan.json（菜单基础信息 + 路径）                  │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  步骤 0b  启动 Chrome + 用户登录                              │
│  └─ CDP 导航到目标菜单（cdp_executor.py --navigate）           │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  步骤 1  探勘                                                │
│                                                              │
│  1a: Stagehand CDP 表单扫描                                  │
│  ├─ 自动识别 HUI 组件（输入框/单选/多选下拉框）                 │
│  ├─ 读取 Vue vm.fields[].isRequired 判定必填字段              │
│  └─ → Gate 1a 判定查询策略 + 客户路由                         │
│                                                              │
│  1b: cdp_executor.py 填表触发查询 + SSH 抓日志               │
│  └─ 全条件查询 → 1 份日志                                    │
│  └─ → Gate 1b 判定用例数(5/7) + 预埋策略                     │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  步骤 2  数据预埋                                            │
│                                                              │
│  2a: sql_extractor analyze → 提取表名、WHERE、分片信息         │
│      ├─ cache/*.json 缓存命中 → 直接用                        │
│      └─ 缓存未命中 → DESCRIBE → seed_cache → 重试             │
│  2b: 基础表覆盖检查（_base.sql）                              │
│  2c: 生成业务表 INSERT（DELETE 优先 + REPLACE INTO）          │
│      └─ 按 open_branch 分组统计 → expected_counts             │
│  2d: 验证 case_plan.json 完整性                               │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  步骤 3  逐个生成用例（branch 优先）                           │
│                                                              │
│  case_count=7: branch → headquarter → queryall               │
│              → querysingle → querymulti → reset → preview    │
│  case_count=5: queryall → querysingle → querymulti           │
│              → reset → preview                               │
│                                                              │
│  代码唯一来源：assets/templates/                              │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  步骤 4  迭代验证 + 熔断                                      │
│  逐个 pytest → 读报错 → 修代码 → 重试（3次失败放弃）            │
└──────────────────────────┬──────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────┐
│  步骤 5  导出 Markdown 测试报告                                │
│  测试报告/{菜单名}/ → 每个 PASSED 用例独立报告                  │
└──────────────────────────────────────────────────────────────┘
```

## 决策矩阵

| Gate 文件 | 触发时机 | 决策内容 |
|------|---------|---------|
| [gates/gate-1a.md](gates/gate-1a.md) | 步骤 1a 完成后 | 查询策略、客户路由、必填字段、queryall 模板 |
| [gates/gate-1b.md](gates/gate-1b.md) | 步骤 1b 完成后 | 分行过滤、用例数（5/7）、预埋策略、筛选字段 |

| [gates/gate-3.md](gates/gate-3.md) | 步骤 3 执行前 | 用例生成顺序、模板选择、输出路径 |

## 18 条铁律速览

完整说明见 [references/code-rules.md](references/code-rules.md)，核心要点：

| # | 铁律 | 一句话 |
|---|------|--------|
| 1 | 表格等待 | 查询后轮询 `_get_record_count(page)`，禁用 `wait_for_selector("tbody tr")` |
| 2 | 表单精确匹配 | `label.strip() == label_text`，禁止 `in` 模糊 |
| 3 | 区分输入框/下拉框 | 输入框 `:not(.h-select-input)`，下拉框用 `_select_dropdown_by_label` |
| 10 | 依赖预埋 | SQL 必须 DELETE/INSERT，不依赖已有数据 |
| 11 | 禁用 nth() | 用 `_fill_input_by_label` 精确定位 |
| 14 | 分行/总行 mark | 分行用例必须加 `@pytest.mark.login_user("branch_user")` |
| 16 | SQL 预埋铁律 | 以查代写 + 禁止全表 DESCRIBE + 禁止编造列名 |
| 18 | Dry-Run 验证闭环 | SQL 写入前必须通过列名校验 + 事务回滚验证，任一步失败必须回退修复 |

## 用例类型说明

| 用例 | 填什么 | 断言什么 | 谁登录 |
|------|--------|---------|--------|
| **branch** | 必填字段 | 精确条数 `== N` | 分行 `@pytest.mark.login_user("branch_user")` |
| **headquarter** | 必填字段 | 精确条数 `== M` | 总行（默认） |
| **queryall** | 必填字段 | `cnt > 0` | 总行 |
| **queryall_clean** | 无业务参数 | `cnt > 0`（仅无必填字段时） | 总行 |
| **querysingle** | 必填 + 1 个筛选 | `cnt > 0` | 总行 |
| **querymulti** | 必填 + 2+ 个筛选 | `cnt > 0` | 总行 |
| **reset** | 填字段 → 点重置 | 输入框为空 | 总行 |
| **preview** | 必填 → 点操作按钮 | `input[readonly]` 有值 | 总行 |

## 关键概念

### has_required_fields（必填字段驱动查询策略）

页面无必填字段（`has_required_fields=false`）时：
- 始终执行**单路全条件查询**探勘
- queryall 用例使用**纯净模板**（不填业务参数）

页面有必填字段（`has_required_fields=true`）时：
- 填必填字段后查询
- queryall 用例使用**标准模板**（`_fill_required_fields`）

### customer_route（客户路由）

页面同时存在"客户标识类型"+"客户标识"字段时，进入客户路由模式：
- DELETE 条件用 `bank_acc` 而非 `ta_code`
- 分行/总行用例必须填入客户标识
- 断言基于分片表预埋行数

### 分行/总行权限

- 分行用户登录后，后端通过 `tbclientseller2` 关联表过滤客户号
- 探勘必须用分行账号，branch 用例第一个做
- 预埋两条不同 `open_branch` 的数据

### Stagehand CDP 探勘引擎

替代旧的 `playwright-cli eval` 表单扫描：
- CDP 接管已登录的 Chrome（不启动新浏览器）
- 双引擎架构：Engine A（Playwright CDP DOM 扫描）+ Engine B（Stagehand AI 语义增强，可选）
- 只扫描 DOM，不执行任何动作
- 自动识别 HUI 特殊组件（`.h-select-multiple` → 多选）
- 输出稳定的 Playwright 原生 locator，彻底消除短 ID 脆弱问题

详见 [references/stagehand-guide.md](references/stagehand-guide.md)

## 核心脚本

| 脚本 | 用途 |
|------|------|
| `config_reader.py` | 配置读取 CLI。`init <菜单>` 一次性输出全部配置；`compute_shard` 计算分片路由 |
| `state_manager.py` | 步骤流转管理。`init` 初始化 → `check` 前置检查 → `step_done` 标记完成 → `checklist` 检查清单 |
| `cdp_executor.py` | 纯 Python CDP 执行引擎。填表、点击、导航、SSH 日志抓取，替代旧 playwright-cli |
| `sql_extractor.py` | SQL 日志分析。`analyze` 提取表结构，`cache` 管理 schema 缓存 |
| `stagehand_explorer/` | Stagehand CDP 探勘引擎。TypeScript 核心 + Python 包装层 |

## 参考文档索引

| 文档 | 内容 |
|------|------|
| [GATE.md](GATE.md) | 决策矩阵索引（3 个 Gate 文件） |
| [gates/](gates/) | Gate 文件（按步骤按需加载） |
| [code-rules.md](references/code-rules.md) | 16 条铁律详细说明 + 代码示例 |
| [case-template.md](references/case-template.md) | 用例模板使用说明 + 占位符映射 |
| [config-guide.md](references/config-guide.md) | 配置管理 + 导航规则 + 分片规则 |
| [stagehand-guide.md](references/stagehand-guide.md) | Stagehand CDP 探勘引擎使用指南 |
| [report-spec.md](references/report-spec.md) | 测试报告格式规范 |
| [troubleshooting.md](references/troubleshooting.md) | 故障排除 |
