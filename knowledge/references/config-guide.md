# 配置管理 + 导航 + 分片规则

> SKILL.md 步骤0-2 的补充参考。所有配置从 skill 目录的 Excel 读取，不硬编码。

## 配置文件位置

所有配置读取/写入必须以 **skill 目录** 为准：
- 配置文件：`{SKILL_DIR}/config/test_config.xlsx`
- 读取脚本：`{SKILL_DIR}/scripts/config_reader.py`
- 表→库映射：`{SKILL_DIR}/config/table_db_mapping.json` （所有表归属哪个数据库族）
- 表结构缓存：`{SKILL_DIR}/config/cache/` （按库族分文件：ui_bc.json、ui_trust.json、ui_fund.json、ui_fina.json、ui_dxcp.json 等）
- 项目目录的 `config/test_config.xlsx` 是副本，不要在项目目录读写配置

## table_db_mapping.json（表→库映射）🔴 权威数据源

每张逻辑表属于哪个数据库族（如 `ui_trust`、`ui_fund`、`ui_dxcp`），由 `config/table_db_mapping.json` 维护。

**🔴 这是判断表在哪个库的权威数据源**，所有判断都应基于此文件，不应硬编码库族列表。

**用途**：
- `sql_extractor.py table_to_db()` 查此文件确定表的数据库归属
- `sql_extractor.py resolve_shard_db()` 根据 `table_to_db()` 的返回值判断是否需要路由
- `sql_extractor.py _classify_table_module()` 根据库族推导模块（信托/基金/商业养老金/理财/公共库）
- 预埋 SQL 按库拆分文件时以此为准

**判断逻辑**：
- `table_to_db()` 返回 `ui_bc` → 不分片，直接使用 `ui_bc`
- `table_to_db()` 返回其他库族（如 `ui_trust`）→ 是业务表，需要根据 `routing_db` 路由

**数据库族→模块映射**（硬编码在 `_DB_FAMILY_TO_MODULE`）：

| 库族 | 模块 |
|------|------|
| `ui_trust` | 信托 |
| `ui_fund` | 基金 |
| `ui_dxcp` | 商业养老金 |
| `ui_fina` | 理财 |
| `ui_hstc` | 汇市通 |
| `ui_insure` | 保险 |
| `ui_per` | 个人养老 |
| `ui_bc` | 公共库 |

### 维护方式

**方式 1：命令行指定 `--dbs`**
```bash
python "{SKILL_DIR}/scripts/build_schema_cache.py" --build-mapping --dbs ui_bc ui_dxcp1 ui_dxcp2 --project-dir "F:/python_project"
```

**方式 2：`--auto` 模式（建映射 + 建缓存一步到位）**
```bash
python "{SKILL_DIR}/scripts/build_schema_cache.py" --auto --dbs ui_bc ui_dxcp1 ui_dxcp2 --project-dir "F:/python_project"
```

### 前置条件

- `conf/config.py` 的 `Config.env_dbs` 中必须有对应数据库的连接配置
- `test_config.xlsx` 的 `project_dir` 必须已填写，或命令行显式传入 `--project-dir`
- 各环境（`ifm60-trust`、`ifm60-dxfund` 等）的数据库配置分散在不同 env 中，`--build-mapping` 会自动遍历所有环境搜索

## config.json（Excel 快照）

`config_reader.py init` 在用例目录下生成 `config.json`，包含 Excel 三个 sheet 的完整快照 + 推导值：

```json
{
  "env": { ... },           // Sheet1 环境配置
  "menu": { ... },          // Sheet2 菜单导入（当前菜单行）
  "presets": { ... },       // Sheet3 预埋数据
  "computed": {
    "menu_url": "...",      // 从 menu_urls.json 推导
    "module": "...",        // 从一级菜单推导（dxfund/dxtrust/fina）
    "test_case_dir": "...", // 从 Config.TEST_CASE_DIRS 推导
    "log_path_full": "..."  // log_path_{module} + log_file，SSH 日志抓取用
  }
}
```

**🔴 后续所有步骤从 `config.json` 读取 env/presets，禁止再读 Excel。**

## 3个Sheet说明

| Sheet名 | 用途 | 谁填 |
|---------|------|------|
| **环境配置** | SSH连接+日志+环境标识（仅安装特有项，其余从 conf/config.py 读） | 👤 用户（一次性） |
| **菜单导入** | 用户录入的菜单基础信息（菜单名称+一二三级菜单） | 👤 用户（每个菜单填1行） |
| **预埋数据** | 测试数据预埋值（TA代码、产品代码、客户标识等） | 👤 用户（一次性） |

## 模块推导（log_path_module_keywords）

`config_reader.py init` 根据菜单的「导航一级菜单」自动推导业务模块（`dxfund`/`dxtrust`/`fina`），用于日志路径选择、分片路由计算等。

**推导规则**：从 Excel「环境配置」sheet 的 `log_path_module_keywords` 配置项读取，格式 `关键词:模块名`，逗号分隔。一级菜单名包含某关键词即匹配对应模块。

| 配置键 | 值示例 | 说明 |
|--------|--------|------|
| `log_path_module_keywords` | `基金:dxfund,信托:dxtrust,理财:fina` | 一级菜单关键词→模块名映射 |
| `log_path_dxfund` | `/home/.../logs/` | 基金模块日志路径 |
| `log_path_dxtrust` | `/home/.../logs/` | 信托模块日志路径 |
| `log_path_fina` | `/home/.../logs/` | 理财模块日志路径 |
| `log_file` | `lcpt-manager.log` | 日志文件名（所有模块共用） |

**新增业务模块步骤**：
1. Excel「环境配置」sheet 加 `log_path_module_keywords` 追加新模块（如 `养老金:pension`）
2. 加 `log_path_pension` 行（新模块日志路径）
3. 无需改代码

**兼容性**：`log_path_module_keywords` 为空时，默认匹配 `dxtrust`（向后兼容旧 Excel）。

## config_reader.py 命令参考

```bash
python "{SKILL_DIR}/scripts/config_reader.py" menus        # 读待办菜单列表
python "{SKILL_DIR}/scripts/config_reader.py" env           # 一次性读全部环境配置
python "{SKILL_DIR}/scripts/config_reader.py" presets       # 读全部预埋数据预设值
python "{SKILL_DIR}/scripts/config_reader.py" menu "{菜单名}"   # 某菜单详细配置
python "{SKILL_DIR}/scripts/config_reader.py" menu_url "{菜单名}"  # 菜单URL hash
python "{SKILL_DIR}/scripts/config_reader.py" init "{菜单名}"   # 一次性导出全部配置 + 写入 config.json
python "{SKILL_DIR}/scripts/config_reader.py" nav "{菜单名}"    # 生成导航函数代码
python "{SKILL_DIR}/scripts/config_reader.py" case "{菜单名}" "{类型}"  # 生成装饰器
python "{SKILL_DIR}/scripts/config_reader.py" sql "{菜单名}"    # 生成SQL路径代码
```

**⚠️ 严禁逐个 key 读取！** `env` 不带参数返回全部配置项，一次搞定。`init` 会同时写入 `config.json`。

## 预埋数据预设值（presets）

Excel「预埋数据」sheet 只维护 `字段名`、`默认值`、`说明`、`适用模块`。用户不需要在 Excel 中维护英文 key 或数据库列名；系统通过 `knowledge/config/field_semantics.json` 将中文字段名映射为内部语义键，再映射到 SQL 生成需要的数据库列。

| 内部语义键 | 说明 | 默认值 |
|--------|------|--------|
| ta_code_1 | 主TA代码 | JS |
| ta_option_1 | 主TA下拉框选项 | JS:嘉实基金 |
| ta_code_2 | 副TA代码 | HT |
| ta_option_2 | 副TA下拉框选项 | HT:HT基金 |
| prd_code | 产品代码 | 11 |
| prd_option | 产品下拉框选项（强匹配格式） | 11:AI自动化测试产品 |
| customer_id | 客户标识 | A3060210020001 |
| customer_id_type | 客户标识类型 | 0:入账账号 |
| account_type | 账号类型（如有） | 0:入账账号 |
| query_date_start | 开始日期 | 2026-01-01（示例值，应使用当前年份） |
| query_date_end | 结束日期 | 2026-12-31（示例值，应使用当前年份） |
| hq_branch_no | 总行机构编号 | 100 |
| hq_branch_name | 总行机构名称 | 总行 |
| branch_no | 分行机构编号 | 0011 |
| branch_name | 分行机构名称 | 交通银行股份有限公司0011分行 |

**⚠️ 预埋数据必须从配置读取，严禁在代码里硬编码业务值！** 生成 SQL 和用例代码时读取 `presets`；`presets` 的内部语义键由 `field_semantics.json` 从 Excel 中文字段名推导。

### 预埋字段匹配规则（form_fields.all value + value_source）

探勘到的表单字段与 Excel「预埋数据」sheet 的「字段名」列做**双向子串模糊匹配**：
- label 包含字段名（如 label="客户标识类型" 匹配字段名="客户标识类型"）
- 字段名包含 label（如 label="TA" 匹配字段名="TA代码1"）
- 优先匹配最长字段名（如「客户标识类型」优先于「客户标识」）

表单值匹配只依赖 Excel 的中文字段名；SQL 生成所需的内部语义和数据库列名映射由 `field_semantics.json` 维护。新增预埋字段时，如果它只是新的业务值，可只在 Excel 中添加一行；如果它对应新的语义或数据库列，还需要同步补充 `field_semantics.json`。

所有字段统一通过 `generate_field_values()` 获得值：
- `preset`: 匹配到 Excel 预埋数据
- `first_option`: 未匹配或 preset 不在下拉选项中，取第一个选项
- `generated`: 按字段类型自动生成（日期、编号、数值等）

选项字段 `options` 来自探勘阶段的 DOM 下拉框扫描，区分不同菜单同标签字段的差异值。

### build_actions 动作生成规则

`config_reader.py build_actions` 从 `form_fields.all` 生成 `cdp_executor -a` 动作文件：

| 字段类型 | 生成的动作 | 说明 |
|---------|----------|------|
| `input` | `fill`（value 非空时） | 空 value 的 input 跳过（避免填入无意义值） |
| `single-select` | `select`（value 或 `__FIRST_OPTION__`） | 空值字段不跳过，用哨兵值在运行时兜底选第一个 |
| `multi-select` | `multi_select`（value 非空时） | 空值多选跳过 |

**哨兵值 `__FIRST_OPTION__`**：触发 `select_single()` 跳过匹配，直接打开下拉框选第一个可见选项。用于无 preset 的 single-select 字段兜底。

**级联依赖排序**：`产品代码` 依赖 `TA代码`（必须先选 TA 再选产品，否则产品选项被 TA 变更重置）。`build_actions` 自动将依赖字段排在所有前置字段之后。

### 树选择弹窗（如交易机构）填充规则

`fill_field()` 检测到 `disabled input + button.choose-btn` 时自动走 `_select_tree_modal()`：
- 不调用 `_resolve_fill_value()`（避免"机构"→"000"等错误语义生成）
- 原始 value（含哨兵 `_`、`__FIRST_OPTION__`）直接传入弹窗匹配
- 匹配成功 → 点击行 → 确定；匹配失败/哨兵 → 兜底选第一行

## Chrome CDP 启动 + 登录

登录由 AI 全自动完成，用户无需参与。

### 启动命令

AI 通过 `launch_chrome.py --auto` 自动执行：
- `kill_chrome()` — 关闭所有 Chrome 进程
- `launch_chrome()` — 以独立临时 profile 启动 Chrome，带 `--remote-debugging-port`
- `check_cdp()` — 轮询等待 CDP 端口就绪

### 自动登录

AI 通过 `cdp_executor.py --login` 自动执行：
1. 导航到 `login_url`（从 `conf/config.py` 的 `Config.envs[env_key]["login_url"]` 读取）
2. 填写用户名/密码（`branch_user` / `branch_password`，从 `config.json` 的 `env` 字段读取）
3. 点击登录按钮
4. 等待"欢迎"文本出现（验证登录成功）
5. 处理"流程状态刷新异常"弹窗（如有）

### 分行/总行账号

从 `conf/config.py` 的 `Config.envs[env]` 读取：
- `default_user` → 总行账号（默认登录用户）
- `users["branch_user"]` → 分行账号（需在 Config 中配置）

```python
"users": {
    "qiumj001": {"password": "11111111"},      # 总行（已有）
    "branch_user": {"password": "11111111"},  # 分行（新增）
}
```

> **注意**：`branch_user` 和 `branch_password` 也必须在 Excel「环境配置」sheet 中配置，以便写入 `config.json` 供 skill 使用。

## 配置来源总览

**从 `config.json` 读取**（Step 0a 由 `config_reader.py init` 写入，后续所有步骤使用）：
- `env` — SSH 连接参数、日志路径、环境标识、`project_dir`、`branch_user`、`branch_password`、`log_path_module_keywords`（模块推导映射）
- `menu` — 菜单名称、导航一级/二级菜单、功能代码
- `presets` — TA 代码、产品代码、客户标识、日期范围、机构编号等
- `computed` — `menu_url`、`module`、`test_case_dir`

**从 `conf/config.py` 读取**（不放在 Excel 里）：
- `login_url` — 登录地址（`Config.envs[env]["login_url"]`）
- 登录账号/密码（`Config.envs[env]["default_user"]` / `Config.envs[env]["users"]`）
- 数据库连接（`Config.env_dbs[env]`）
- 按一级菜单路径映射（`Config.TEST_CASE_DIRS` / `Config.SQL_DATA_DIRS`）
- 项目根目录（`Config.root_dir`）

**从 `config.json` 的 `presets` 字段读取**：
- TA 代码/名称/选项
- 客户标识
- 产品代码
- 日期范围等

---

## 菜单导航规则

### 首选方案：URL 直跳（推荐，快 3-5 秒）

HUI 每个菜单都有固定的 URL hash 路径，存储在数据库 `tsys_menu` 表的 `tree_idx` 字段。

> 导航规则：优先用 URL hash 直跳（`page.goto(base + '#' + MENU_URL)`），禁止侧边栏 click 导航。

**获取 URL 的优先级**：
1. `config/menu_urls.json`（离线缓存，91 个信托代销菜单全覆盖，最快）
2. `config_reader.py menu_url "菜单名"`（从 Excel 菜单发现 sheet 读取）
3. 数据库查询（最后手段）

```bash
# 优先：从 menu_urls.json 读取
python "{SKILL_DIR}/scripts/config_reader.py" menu_url "{菜单名}"
```

如果返回空（Excel 和 menu_urls.json 都没有），才从数据库查询：
```python
import pymysql
conn = pymysql.connect(host=..., port=..., user=..., password=..., database='ui_bc')
cur = conn.cursor()
cur.execute("SELECT tree_idx FROM tsys_menu WHERE menu_name = '{菜单名}'")
row = cur.fetchone()
menu_url = row[0] if row else ''
conn.close()
```

代码中直接跳转：
```python
async def _navigate(page):
    base = page.url.split('#')[0]
    await page.goto(f"{base}#{menu_url}", wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    await _collapse_sidebar(page)
```

**优势**：跳过侧边栏点击，节省 5-8 秒；不依赖菜单层级猜测。

**注意**：`tree_idx` 以 `/` 开头，拼接时用 `base_url + "#" + tree_idx`。

### 备选方案：侧边栏点击（URL 未知时使用）

1. 先用最简单的方式：`page.get_by_title("菜单名", exact=True).first.click()`
2. 如果找不到，再尝试展开父菜单
3. **严禁**：直接猜四级/五级菜单路径并写死多层展开逻辑

### 导航成功验证

- 从 `page.evaluate(() => window.location.hash)` 提取 URL
- URL 最后一段就是功能代码，用于命名测试文件
- 如果 URL 始终停留在 `#/mainIndex`，说明导航失败

---

## 分片表预埋规则

### 环境配置类型

6.0标准搭建有三种配置，以现场实际情况为准：

| 配置 | pub库 | trans库 | 说明 |
|------|-------|---------|------|
| (1) | 1个 | **2个** (ui_trust1 + ui_trust2) | pub和trans分开，trans双库分片 |
| (2) | 1个 | **1个** (ui_trust1) | pub和trans分开，trans单库 |
| (3) | 1个 | pub和trans**合在一起** | 共用一个库 |

**判断方法**：查看 `conf/config.py` 中当前环境的 `databases` 字典是否有 `ui_trust2`。有=配置(1)，无=配置(2)或(3)。

### 分片路由计算（配置(1)：双trans库）

取 `in_client_no` 后8位数字（记为 `N`），计算公式（仅计算分库）：

```
trans库选择: N / 2 = 商...余数
  余数 + 1 = 库号  (0→库1即ui_trust1, 1→库2即ui_trust2)
```

**分表不再通过公式计算，改为以日志中实际出现的物理表名为准。**

### 分片路由计算（配置(2)/(3)：单trans库）

单库场景下不存在分库，仅需确定 `db_name` = `ui_trust1`（或 `ui_fund1` / `ui_fina1`）。

### 完整计算示例（仅分库）

**已知**：`in_client_no = 33060210020001`，后8位 `N = 10020001`

| 配置 | 计算过程 | 结果 |
|------|---------|------|
| (1) trans库 | `10020001 / 2 = 5010000` 余 `1` → 库2 | **ui_trust2** |
| (2)/(3) | 单库 | **ui_trust1** |

**用户示例**：`in_client_no = 2021060400000004`，后8位 `N = 00000004 = 4`
- 配置(1) trans库：`4/2=2余0` → 库1 → **ui_trust1**

### 🔴 铁律：预埋前必须确定分片库

**生成预埋 SQL 时必须根据 in_client_no 计算正确的分片库（routing_db）。分片表以日志中出现的物理表名为准，每张物理表都生成 INSERT。**

检查步骤：
1. 从 `presets` 获取 `customer_id`（bank_acc）
2. 查询 `tbclientmap` 获取对应的 `in_client_no`：`SELECT in_client_no FROM tbclientmap WHERE bank_acc = '{customer_id}'`
3. 取后8位数字，按当前环境配置计算分片库号 → `routing_db`
4. 确认 `conf/config.py` 中有对应数据库配置（如 `ui_trust2`）
5. **分片表号以日志物理表名为准**：日志中出现 `tbdxtrustshare1` 和 `tbdxtrustshare16`，两份都生成 INSERT，不做表号计算

### 🔴 shard.db 权威来源

**`shard.db` 的唯一权威数据源是 step 2b `sql_extractor.py classify` 命令输出中每张表的 `db` 字段。**

- `classify` 命令连接实际数据库查询 `information_schema.TABLES`，返回每张表的真实数据库名
- 禁止根据模块名（如 `dxfund` → `ui_fund2`）推断数据库名
- 禁止直接使用 `compute_shard` 的结果作为 `shard.db`（`compute_shard` 只提供 `routing_db`）
- 如果 `classify` 返回 `db: "ui_bc"`，则 `shard.db = "ui_bc"`，即使模块是 `dxfund`

### 确认分片路由的方法（验证）

1. SSH 查看 `manager-stdout.log`，日志有两种格式：
   - **分片表**：`Actual SQL: ds0 ::: select ...` 或 `Actual SQL: ds1 ::: select ...`
   - **非分片表**：`SQL执行,当前SQL: select ...`
2. 如果日志中 SQL 指定了具体表名（如 `tbdxtrustsquare1`），以日志中的表名为准
3. **规则**：必须按日志中的实际库和表确定插入目标，不能猜

---

## 数据预埋顺序

必须先预埋配置表（`ui_bc` 库），然后再预埋业务表！否则下拉框打开必定为空。

### 配置表数据来源：`config/base_tables.json`

**所有配置表预埋数据都从 `config/base_tables.json` 读取，禁止从数据库现场查询！**

```bash
# 读取 TA 记录
python config_reader.py base_ta JS 8      # ta_code=JS, prd_type=8(信托代销)

# 读取产品记录
python config_reader.py base_prd tbdxtrustproduct JS_110   # 表名 + 记录key

# 读取客户映射
python config_reader.py base_client A3060210020001
```

### 第1步：预埋配置表（ui_bc 库，必须先做）

| 下拉框字段 | 基金代销数据表 | 信托代销数据表 | 说明 |
|-----------|-------------|-------------|------|
| TA代码 | `tbtainfo`(prd_type='0') | `tbtainfo`(prd_type='8') | TA基础信息 |
| 产品代码 | `tbproduct` | `tbdxtrustproduct` | **不能混用！** |

**tbtainfo 的 prd_type 必须匹配当前模块**：
- 基金代销（dxfund）：`prd_type = '0'`
- 信托代销（dxtrust）：`prd_type = '8'`

**产品表必须区分模块**：
- **基金代销**：`tbproduct`（`prd_type='0'`，示例：prd_code=11, prd_name=AI自动化测试产品）
- **信托代销**：`tbdxtrustproduct`（`prd_type='8'`，示例：prd_code=110, prd_name=110）
- **⚠️ 信托代销 INSERT `tbproduct` 会导致下拉框找不到产品！** 必须用 `tbdxtrustproduct`

**产品表必须与 tbtainfo 关联**：
- 产品表 `ta_code` 必须与 `tbtainfo.ta_code` 匹配
- 产品表 `prd_code` 必须与 presets 中的 `prd_code` 一致
- 产品代码下拉框选项格式以探勘实际抓取为准，不从表数据推导

### 第2步：预埋业务表（在配置表之后）

业务表 INSERT 放在配置表 INSERT 之后执行。

### 常见错误

| 错误 | 症状 | 根因 |
|------|------|------|
| 只预埋 tbtainfo 没预埋产品表 | 产品代码下拉框为空 | 漏预埋产品配置表 |
| 信托代销用了 tbproduct | 产品代码下拉框找不到信托产品 | 产品表用错了，应该用 tbdxtrustproduct |
| 产品表 prd_type 与模块不匹配 | 下拉框出现其他模块的产品 | 基金='0'，信托='8' |
| 预埋顺序错误 | 业务表 INSERT 报错 | 应该先配置表再业务表 |

## SQL路径统一规则

**路径来源**：生成代码中直接用 `Config.SQL_DATA_DIRS[一级菜单]` 查表，不经过 case_plan.json。

```
SQL 路径 = Config.root_dir + Config.SQL_DATA_DIRS['${LEVEL1}'] + 二级菜单 + 菜单名 + 功能代码.sql
基础表   = Config.root_dir + Config.SQL_DATA_DIRS['${LEVEL1}'] + 二级菜单 + 菜单名 + 功能代码_base.sql
```

**用例路径**（从 case_plan.json `paths.test_case_dir` 读取）：
```
用例路径 = {test_case_dir}/{二级菜单}/{菜单名}/{菜单名}{目录后缀}/test_{功能代码}{文件后缀}.py
```

**禁止**硬编码 `os.path.join(Config.root_dir, "testcases", ...)`。

## 预埋SQL编写规则

### DELETE 优先（大类清理）

预埋前必须先 DELETE 同大类旧数据，再 INSERT。只用 REPLACE INTO 无法清理主键不同但属同批测试的脏数据（如前一次跑失败的残留）。

**DELETE 条件由 `customer_route` 决定（见 `gates/gate-1a.md`）：**

| customer_route | DELETE 条件 | 原因 |
|:---:|---|---|
| true | `WHERE bank_acc = '{customer_id}'` | 客户路由模式下按客户标识清理 |
| false | `WHERE ta_code = '{ta_code}'` | 非客户路由按 TA 代码清理 |

**SQL 模板：**
```sql
-- 先清空旧数据（条件由 customer_route 决定）
DELETE FROM {shard_table}
WHERE {delete_condition};

-- 再预埋新数据（DELETE 已清空，直接 INSERT，无需 REPLACE INTO）
INSERT INTO {shard_table} (...) VALUES (...);
```

DELETE 条件由 `customer_route` 决定（见 `gates/gate-1a.md` 1a-3），无需写入 case_plan。

**🔴 用 INSERT，不用 REPLACE INTO。** DELETE 已清空旧数据，REPLACE INTO 的"遇冲突自动替换"机制反而会引入"多行变一行"的风险。

### 🔴 禁止数据库前缀

SQL 文件中的表名**禁止**加数据库前缀（如 `ui_bc.tbacccfm`），只写裸表名（`tbacccfm`）。
数据库关联由 pytest fixture 的 `db_key` 参数控制（如 `get_async_db_executor(db_key="ui_bc")`），SQL 中加前缀会导致跨库执行失败。

### 日期跨度限制（前端校验）
HUI 前端校验：日期跨度不能超过 365 天，否则弹出校验报错。
- **预埋数据的日期**：如 `cfm_date`、`trans_date` 等，必须在查询日期范围内
- **查询日期范围**：起止日期跨度 ≤ 365 天（如 `YYYY-01-01` 到 `YYYY-12-31`，YYYY 为当前年份）
- presets 中 `query_date_start` 和 `query_date_end` 应设定为当前年份全年，符合要求
- 如果菜单有特殊限制（如只查近3个月），以探勘结果为准

> 完整流程：每个分片表独立生成 DELETE + REPLACE INTO，禁止跨表合并 SQL。

### 关键字段预埋

**🔴 字段名以 DESCRIBE 结果为准，不同表的字段名不同，禁止假设。**

- **日期字段**（如 `cfm_date`、`trans_date` 等，以 DESCRIBE 中 int(11) 日期类字段为准）：不能为 0，必须显式填入有效值，否则代码格式化时会报空指针
- **`open_branch`**：机构编号。**direct 场景下**（业务表有 `open_branch` 列）分行/总行权限用例必须预埋不同值；indirect 场景下可能无此列
- **`client_no`**：客户号。**indirect 场景下**（业务表无 `open_branch` 列，由 `tbclientseller2` 间接过滤）分行/总行权限用例的两条数据必须预埋不同值，确保分行只能查到 1 条
- **`bank_acc`**：银行账号。必须与 `presets.customer_id` 对应
- **`in_client_no`**：内部客户编号。**必须与 `tbclientseller2` 表中的记录一致**（关联链：`tbclientseller2.in_client_no` → `open_branch`/`client_no` → 分行权限判定）。即使日志 SQL 的 WHERE 中没有这个字段，也必须预埋，否则分行权限用例会因查不到客户经理信息而失败。`sql_extractor` 已将其设为业务规则必填列，会自动注入到 prompt 中

### int 列类型规则

**🔴 所有 `int`/`bigint` 类型列，空值必须用 `0`，禁止用 `' '`（空格字符串）。**

MySQL 的 `int` 列插入 `' '` 会报错：`Incorrect integer value: ' ' for column 'xxx'`。
- `host_date`、`host_serial`（如果也是 int）等字段常见此问题
- `varchar` 类型列空值仍用 `' '`
- `decimal` 类型列空值用 `0.00` 或 `0.000`（按精度）

### UNIQUE 索引验证（生成后检查）

**🔴 生成 INSERT 后，必须验证新行之间无 UNIQUE 冲突。**

虽然 DELETE 已清空旧数据，但新插入的多行之间仍可能冲突（如日期/序列号相同）。

验证方式：
1. 从缓存读取：`sql_extractor.py cache get <表名>` 查看 `unique_indexes` 字段
2. 遍历所有 UNIQUE 索引（排除 PRIMARY KEY），检查每行在索引列上的值是否不同
3. 常见需要不同的列：`trans_date`、`square_date`、`serial_no`、`cfm_no` 等

缓存采集方式：
1. `DESCRIBE` 表结构后，执行 `SHOW INDEX FROM <表名>`
2. 用 `parse_show_index()` 解析 UNIQUE 索引
3. 传入 `seed_cache_from_describe()` 写入缓存

### 分行/总行预埋数据条数

**当 `case_count=7`（有分行/总行用例）时，只需预埋 2 条业务数据。**

区分方式由 `branch_source` 决定（见 `gates/gate-1a.md`）：

**场景 A：`branch_source=direct`（业务表有 `open_branch` 列）**

| 行 | open_branch | client_no | 用途 |
|----|-------------|-----------|------|
| 1 | `presets.branch_no`（如 0011） | 可使用相同值 | 分行用户可见 |
| 2 | `presets.hq_branch_no`（如 100） | 可使用相同值 | 总行用户可见 |

**场景 B：`branch_source=indirect`（业务表无 `open_branch` 列，SQL 通过 `client_no in (...)` 过滤）**

| 行 | client_no | 用途 |
|----|-----------|------|
| 1 | 分行可访问的客户号（如 `33060210020001`） | 分行用户可见 |
| 2 | 分行不可访问的其他客户号（如 `33060210020002`） | 总行用户可见 |

两场景好处一致：
- DELETE + INSERT 无需担心与旧数据冲突
- 分行断言 = 1，总行断言 = 2（总行能看到全部），逻辑清晰
- 预埋数据量小，执行快

**第二行值自动派生规则：**

presets 只有一套值，第二行的值从第一行自动派生，不需要用户填两套：

| 字段类型 | 派生规则 | 示例 |
|---------|---------|------|
| 有 UNIQUE 索引的字段 | 尾号 +1 或日期 +1 天 | `SQYYYYMMDD001` → `SQYYYYMMDD002`，`YYYYMMDD` → `YYYYMMDD+1` |
| `open_branch`（direct 场景） | 分别取 `presets.branch_no` / `presets.hq_branch_no` | `0011` / `100` |
| `client_no`（indirect 场景） | 分行行号下可映射到的不同客户号 | `33060210020001` / `33060210020002` |
| 无 UNIQUE 索引的字段 | 两行共用同一个 preset 值 | `ta_code=JS`、`prd_code=11` |

常见需要递增的字段（以缓存 UNIQUE 索引为准）：`trans_date`、`square_date`、`serial_no`、`square_no`、`cfm_no` 等。

### 查询前重置规则

**🔴 所有用例查询前必须先点"重置"，清空页面默认过滤条件。**

页面可能有默认过滤条件（如默认日期范围），直接查询会因默认条件过滤导致返回 0 条或结果不准确。

适用于：queryall、querysingle、querymulti、branch、headquarter、preview。
reset 用例本身不需要（它就是测重置的）。
