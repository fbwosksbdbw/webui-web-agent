# Step 2c — 生成业务表 INSERT

## Precondition

编排器已确认步骤 0a~2b 完成。

## Actions

### 🔴 SQL 文件路径（生成前必须先获取）

所有 SQL 文件路径必须通过脚本命令获取，**禁止 Agent 自行拼接**：

```bash
python "{SKILL_DIR}/scripts/config_reader.py" sql_paths "<菜单名>"
```

输出示例：
```json
{
  "base_sql": "testcases/.../数据预埋/查询管理/{菜单名}/{code}_base.sql",
  "preset_sql": "testcases/.../数据预埋/查询管理/{菜单名}/{code}.sql"
}
```

`{code}` = `case_plan.json` 的 `menu.code`。生成的 SQL 必须写入 `sql_paths` 返回的路径。

### 🔴 SQL 文件命名（生成前必读）

所有 SQL 文件使用固定命名，写入 `sql_paths` 输出的目录：

```
{sql_data_dir}/{二级菜单}/{菜单名}/{FUNC_CODE}_base.sql   — 基础表（ui_bc，REPLACE INTO）
{sql_data_dir}/{二级菜单}/{菜单名}/{FUNC_CODE}.sql        — 业务表（ui_trust*，DELETE + INSERT）
{sql_data_dir}/{二级菜单}/{菜单名}/{FUNC_CODE}_trust.sql  — 多库表的业务库 SQL（如果表同时存在于 ui_bc 和 ui_trust）
```

`FUNC_CODE` = `case_plan.json` 的 `menu.code`。`{sql_data_dir}` 由 `sql_paths` 命令返回。**禁止 Agent 自行拼接路径**，**禁止写入 `case_dir`（用例目录）。**

### 🔴 多库表预埋（一个表存在于多个库）

**当一个表同时存在于多个库时（如 `tbdxtrustproduct` 在 `["ui_bc", "ui_trust"]`），需要在每个库都预埋相同的数据。**

`split_tables_by_db()` 会自动将多库表放入所有库的分组中，`_build_sql_file_paths()` 会为每个库生成独立的 SQL 文件路径。

**生成规则**：
1. `split_tables_by_db()` 返回的 `tables_by_db` 中，多库表会出现在多个库的列表中
2. 为每个库独立生成 DELETE + INSERT（或 REPLACE INTO），数据内容完全一致
3. `_base.sql` 中的路由表用 REPLACE INTO，业务库 SQL 用 DELETE + INSERT

**示例**：`tbdxtrustproduct` 同时在 `ui_bc` 和 `ui_trust`
- `tables_by_db` = `{'ui_bc': [..., 'tbdxtrustproduct', ...], 'ui_trust': [..., 'tbdxtrustproduct', ...]}`
- `{FUNC_CODE}_base.sql`（ui_bc）：`REPLACE INTO tbdxtrustproduct (...) VALUES (...)`
- `{FUNC_CODE}_trust.sql`（ui_trust）：`DELETE FROM tbdxtrustproduct WHERE ...; INSERT INTO tbdxtrustproduct (...) VALUES (...)`

### DELETE 优先

生成 INSERT 之前，必须先生成 DELETE 清空旧数据。

**🔴 DELETE 条件（精确匹配）**：
- DELETE 语句必须精确匹配即将插入的数据，不能大范围删除
- DELETE 的 WHERE 条件应该与 INSERT 的关键字段一致
- 示例：
  - 如果 INSERT 的数据是 `ta_code='HT' AND sum_date=20260101 AND branch_no='0011'`
  - 那么 DELETE 应该是 `DELETE FROM table WHERE ta_code='HT' AND sum_date=20260101 AND branch_no='0011'`
  - 而不是 `DELETE FROM table WHERE ta_code='HT'`（范围太大）

**DELETE 条件来源**：
- 从 `form_fields.all` 中提取关键字段的值
- 从 INSERT 语句中提取 WHERE 条件字段
- 组合成精确的 DELETE 条件

**基础表 `_base.sql`**：routing 表（tbclientseller 等）用 REPLACE INTO 自然覆盖即可
**DELETE 和 INSERT 放同一 SQL 文件，保证原子性**

### SQL 生成前置检查

```bash
python "{SKILL_DIR}/scripts/sql_extractor.py" cache list
```

确认 `preset_tables` 中所有表都已缓存（含 `column_types` 字段）。如有 missing 或缺少 `column_types`，先 `DESCRIBE <表名>` → `seed_cache_from_describe()` → 重新确认。未完成此步骤禁止生成任何 INSERT。

**缓存按库族分文件**（如 `ui_bc.json`、`ui_trust.json`、`ui_fund.json`），查找逻辑：
1. `get_cached_table(table_name)` 先通过 `table_to_dbs()` 获取表所属库族列表
2. 依次从各库族缓存文件中查找
3. 未命中时回退到旧的按模块缓存文件（向后兼容）
4. `cache list` 支持按库族名（如 `ui_trust`）或模块名（如 `信托`）过滤

### 🔴 物理表驱动生成（关键改动）

**不再使用 `compute_shard` 计算单表号，改为严格按日志中出现的物理表名生成 INSERT。**

从 `case_plan.json` 读取 `shard.physical_tables`：

```json
"shard": {
  "db": "ui_trust2",
  "physical_tables": {
    "tbdxtrusthissharechg": ["tbdxtrusthissharechg1", "tbdxtrusthissharechg16"],
    "tbclientseller": ["tbclientseller2", "tbclientseller16"],
    "tbclientmap": ["tbclientmap"]
  }
}
```

**生成规则**：

1. **遍历 `preset_tables` 中每张逻辑表**，查 `physical_tables` 获取其物理表名列表
2. **每张物理表独立生成 DELETE + INSERT**：
   - `tbdxtrusthissharechg` 有 2 个物理表 → 生成 2 组 DELETE + INSERT（分别针对 `tbdxtrusthissharechg1` 和 `tbdxtrusthissharechg16`）
   - `tbclientmap` 只有 1 个物理表 → 生成 1 组 REPLACE INTO
3. **同一逻辑表的所有物理表 INSERT 内容完全一致**（保证任意分片都能查到数据）
4. **基础表 `_base.sql` 同理**：`tbclientseller` 在 `physical_tables` 中有几个物理表就生成几个 REPLACE INTO

### LLM 辅助生成 INSERT

对 `preset_tables` 中每张逻辑表调用 `build_minimal_prompt()`，prompt 中已包含物理表清单。

**WHERE 字段来源**：`req["where_columns"]`（来自 step-2a 的 `analyze_log_for_preset()` 输出）。

**新增参数 `form_values`**：来自 step-1a 的 `form_fields.all`，LLM 自动将表单 label（如"客户标识"）语义匹配到 SQL 列名（如 `bank_acc`）。替代旧的硬编码 `field_mappings`。

```python
import sys, os
sys.path.insert(0, os.path.join(r"{SKILL_DIR}", 'scripts'))
from sql_extractor import build_minimal_prompt, get_cached_required_cols, get_cached_column_types, get_expected_columns

# where_cols 来自 step-2a 的 analyze_log_for_preset() 输出

form_fields_all = case_plan.get("form_fields", {}).get("all", [])

for table in preset_tables:
    prompt = build_minimal_prompt(
        table_name=table,
        where_columns=where_cols.get(table, []),
        required_columns=get_cached_required_cols(table),
        presets=presets,
        form_values=form_fields_all,    # NEW: LLM 自行做 label→SQL列 语义匹配
        shard_info=shard_info,
        column_types=get_cached_column_types(table),
        physical_tables=physical_tables.get(table),
    )
    # 将 prompt 发给 LLM，LLM 对每张物理表输出独立的 DELETE + INSERT
```

### 双路 SQL 生成（`customer_route = "both"` 时）

当 `gate_decisions.customer_route = "both"` 时，使用 step-2a 生成的 `dual_write_prompt` 替代逐表 `build_minimal_prompt()`：

```python
import sys, os
sys.path.insert(0, os.path.join(r"{SKILL_DIR}", 'scripts'))
from sql_extractor import build_dual_write_prompt, extract_dual_route_requirements

# dual_write_prompt 已由 step-2a 的 analyze_dual_route_for_preset() 生成并保存
# 从 case_plan.json 读取或重新从两路日志生成
dual_result = case_plan.get("dual_result", {})
dual_write_prompt = case_plan.get("dual_write_prompt", "")

if not dual_write_prompt:
    # 若未保存，从两路日志重新生成
    empty_log = open(r"{case_dir}/query_log_empty.txt", encoding='utf-8').read()
    param_log = open(r"{case_dir}/query_log_param.txt", encoding='utf-8').read()
    dual_result = extract_dual_route_requirements(empty_log, param_log)
    dual_write_prompt = build_dual_write_prompt(
        dual_result,
        presets=presets,
        form_values=form_fields_all,
        physical_tables=physical_tables,
    )

# 将 dual_write_prompt 发给 LLM，LLM 一次性生成所有表的 DELETE + INSERT
# dual_write_prompt 已包含 merged_tables、merged_where、物理表清单等全部信息
```

> 双路 SQL 生成使用 `build_dual_write_prompt()` 替代逐表 `build_minimal_prompt()`，一次性覆盖空查和带参查两路的所有表。

### 🔴 分行/总行预埋数据生成（case_count=7 时）

**当 `case_count=7`（有分行/总行用例）时，必须同时生成分行和总行的预埋数据。**

从 `config.json` 的 `presets` 中读取分行/总行配置：
- 分行：`branch_no`（如 '0011'）、`internal_branch`（如 '1102'）
- 总行：`hq_branch_no`（如 '100'）、`hq_internal_branch`（如 '11'）

**生成规则**：
1. **第一条数据**：使用分行的 `branch_no` 和 `internal_branch`
2. **第二条数据**：使用总行的 `hq_branch_no` 和 `hq_internal_branch`
3. **其他字段**（如 `ta_code`、`prd_code`、`client_type`、`sum_date`）两条数据可以相同

**示例**：
```sql
INSERT INTO <表名> (branch_no, internal_branch, ta_code, prd_code, ...) VALUES
('0011', '1102', 'HT', 'UI_HT_P001', ...),  -- 分行数据
('100', '11', 'HT', 'UI_HT_P001', ...);     -- 总行数据
```

**原因**：查询条件会按 `internal_branch like '<internal_branch>%'` 过滤，分行数据匹配分行查询，总行数据匹配总行查询。

### 🔴🔴🔴 预埋值对齐铁律（最高优先级）

**预埋 SQL 的值必须与 `form_fields.all` 中的 `value` 完全一致。**

`form_fields.all` 是 step-1a 生成的权威数据源，包含每个字段的 `value`。step-2c 生成 INSERT 时：
- WHERE 条件字段的值 → 必须来自 `form_fields.all` 对应字段的 `value`
- 业务表的关联字段（如 `frozen_serial`、`bank_acc`）→ 必须与 `form_fields.all` 中的值匹配
- **禁止手动编造值**，即使看起来"更合理"

**原因**：step-3 的 querymulti 用例会从 `form_fields.all` 取值填入表单。如果预埋数据用了不同的值，查询条件 `WHERE frozen_serial = 'SQ...'` 无法匹配预埋数据 `frozen_serial = 'FZ...'`，导致查询返回 0 条。

**示例**：
```
form_fields.all → 冻结流水号 value = "SQ20260603001"
预埋 SQL → frozen_serial = 'SQ20260603001'  ✅ 正确
预埋 SQL → frozen_serial = 'FZ20260603001'  ❌ 错误（与 form_fields 不一致）
```

### 🔴 列名校验（每张表必做）

LLM 生成 INSERT 后，**必须**通过 `validate_insert_sql.py` 校验列名是否与预期一致，**禁止跳过**。

预期列清单由 `get_expected_columns()` 计算，与 `build_minimal_prompt()` 的 prompt 完全一致：

```python
expected_cols = get_expected_columns(
    where_columns=where_cols.get(table, []),
    required_columns=get_cached_required_cols(table),
    business_rule_columns=biz_cols,
)
```

```bash
# 方法1: 写入临时文件再校验（推荐，跨平台通用，避免 PowerShell stdin 卡死）
echo "{LLM生成的SQL}" > tmp_validate.sql
python "{SKILL_DIR}/scripts/validate_insert_sql.py" --expected-cols "col1,col2,col3,..." --file tmp_validate.sql

# 方法2: 管道方式（仅 Bash/MSYS2 环境可用）
echo "{LLM生成的SQL}" | python "{SKILL_DIR}/scripts/validate_insert_sql.py" --expected-cols "col1,col2,col3,..."
```

**Windows/PowerShell 用户请使用方法1**，PowerShell 的管道不会正确发送 EOF，会导致 `sys.stdin.read()` 永久阻塞。

- exit 0 → 全部列合法，继续下一张表
- exit 1 → 输出具体差异（额外列/缺少列），**将错误信息反馈给 LLM 重新生成，只修非法列，最多重试 3 次**
- 3 次重试仍失败 → 停止，输出完整错误信息让用户介入

### 🔴 Dry-Run 事务回滚验证（列名校验通过后必做）

**列名校验通过后，必须先通过 `dry_run_sql.py` 的事务回滚验证，才能将 SQL 写入文件。** 这是 SQL 生成闭环的最后一道防线：

```
LLM 生成 SQL → validate_insert_sql.py (列名合法性) → dry_run_sql.py (语法+表+类型) → 写入 .sql 文件
```

**验证原理**：每条 DML 语句在独立事务中执行后立即 ROLLBACK，验证语法正确、表存在、列名有效、数据类型匹配。**不持久化任何数据。**

```bash
# 基础表 SQL 验证（ui_bc 库）
python "{SKILL_DIR}/scripts/dry_run_sql.py" --file "{sql_paths.base_sql}" --db ui_bc --config "{config_json_path}"

# 业务表 SQL 验证（分片库，db_key 从 case_plan.json 的 shard.db 获取）
python "{SKILL_DIR}/scripts/dry_run_sql.py" --file "{sql_paths.preset_sql}" --db "{shard_db_key}" --config "{config_json_path}"
```

**错误分类与修复**：

| MySQL 错误码 | 含义 | 修复方向 |
|:---:|---|---|
| 1054 | 未知列名 | 列名未在 DESCRIBE/cache 中出现，回退检查 `build_minimal_prompt` 的 prompt |
| 1146 | 表不存在 | 表名错误或数据库路由错误，检查 `table_to_db()` 和物理表名 |
| 1064 | SQL 语法错误 | SQL 语法有误，检查 VALUES 列表格式 |
| 1062 | 主键/唯一键冲突 | UNIQUE 索引列值重复，检查多行数据的唯一键值是否做了递增 |
| 1366 | 数据类型不匹配 | int 列误填了字符串（如 `' '`），检查空值规则 |
| 1452 | 外键约束失败 | 父表数据不存在（已禁用 FK 检查，通常不会出现） |

### 🔴 重试闭环（Dry-Run 失败时的自动化修复）

当 `dry_run_sql.py --json` 返回失败时，执行重试流程：

1. **解析错误**：对 result.errors 中每个失败用 `parse_mysql_error()` 解析
2. **构建重试 prompt**：用 `build_retry_prompt(table, failed_sql, parsed_error, attempt)` 生成修正 prompt
3. **反馈 LLM 重新生成**：将 prompt 发给 LLM，获取修正后的 DELETE + INSERT
4. **重新验证**：修正后的 SQL 再次通过 `validate_insert_sql.py` + `dry_run_sql.py --json`
5. **最多重试 2 轮**，超过则标记 `HUMAN_REQUIRED` 写入 case_plan.json

```python
from sql_extractor import parse_mysql_error, build_retry_prompt

for attempt in range(1, 3):
    # dry_run 失败后
    err = dry_result["errors"][0]
    parsed = parse_mysql_error(err["error"])
    retry_prompt = build_retry_prompt(table, failed_sql, parsed, attempt)
    # ... 将 retry_prompt 发给 LLM 获取修正 SQL
    # ... 重新 dry_run，通过则 break
else:
    # 2 轮重试仍失败
    case_plan["failed_tables"][table] = {
        "reason": "SQL_VALIDATION_FAILED",
        "last_error": parsed,
        "sql": failed_sql,
    }
```

**错误码 → 修复方向速查：**

| MySQL 错误码 | 含义 | 修复方向 |
|:---:|---|---|
| 1054 | 未知列名 | 列名未在 DESCRIBE/cache 中出现，回退检查 prompt |
| 1146 | 表不存在 | 表名错误或数据库路由错误 |
| 1064 | SQL 语法错误 | 检查 SQL 语法 |
| 1062 | 主键/唯一键冲突 | UNIQUE 索引列值重复，每行唯一键组合必须不同 |
| 1366 | 数据类型不匹配 | int 列误填了字符串 |
| 1452 | 外键约束失败 | 父表数据不存在 |

- **exit 0** → 所有语句通过验证，继续写入文件
- **exit 1** → 进入重试闭环，最多 2 轮
- **exit 2** → 数据库连接失败，检查 config
- 2 轮重试仍失败 → 标记 `HUMAN_REQUIRED`，不阻塞其他表

### 预埋生成铁律

1. **必须覆盖 `preset_tables` 中所有表** — VIEW 也要生成 INSERT
2. **🔴 以 `physical_tables` 为准，不调用 `compute_shard`** — 日志里出现哪些物理表就 INSERT 哪些
3. **🔴 多库表必须在所有库都预埋** — 如果 `table_to_dbs(table)` 返回多个库（如 `["ui_bc", "ui_trust"]`），必须为每个库都生成 SQL，数据内容完全一致
4. **断言条数从预埋 SQL 计算**：
   - 生成 INSERT 后，按 `gate_decisions.branch_filter_column` 分组统计行数
   - `branch_source=direct`：按 `open_branch` 分组
   - `branch_source=indirect`：按 `client_no` 分组
   - 分行预期 = 过滤条件为分行的行数
   - 总行预期 = 所有 INSERT 行总数（总行预期 >= 分行预期）
   - 写入 `expected_counts`

## Checklist

- [ ] 已执行 `config_reader.py sql_paths` 获取正确的 SQL 文件路径
- [ ] 已执行 `sql_extractor.py cache list`，所有表结构均已缓存（含 `column_types`，无 missing）
- [ ] 所有列名均来自缓存/DESCRIBE，无编造列
- [ ] 🔴 每张表的 INSERT 已通过 `validate_insert_sql.py` 列名校验（exit 0）
- [ ] 🔴 每个 SQL 文件已通过 `dry_run_sql.py` 事务回滚验证（exit 0）：语法+表+列+类型全部正确
- [ ] SQL 文件已写入 `sql_paths` 返回的正确路径
- [ ] preset_tables 中所有 business 表均已生成 DELETE + INSERT（一张不少）
- [ ] preset_tables 中所有 routing 表均已生成 REPLACE INTO → `_base.sql`
- [ ] 🔴 每张逻辑表的 `physical_tables` 中所有物理表名均已生成独立的 DELETE + INSERT
- [ ] 🔴 多库表（`table_to_dbs()` 返回多个库）已在所有库都生成 SQL，数据内容一致
- [ ] 🔴 system/view 分类的表**未生成任何 INSERT**
- [ ] DELETE 语句已按 `customer_route` 决定条件清空旧数据
- [ ] （`customer_route="both"` 时）已使用 `build_dual_write_prompt()` 生成双路 SQL，覆盖空查+带参查两路所有表
- [ ] 每个 NOT NULL 列的值有明确来源
- [ ] 日期跨度 ≤ 365 天
- [ ] （case_count=7 时）过滤列分行/总行各有足够数据，已统计预期条数
- [ ] （branch_source=indirect 时）`tbclientseller` 所有物理分片均已覆盖
- [ ] （case_count=5 时）跳过 branch/headquarter 相关检查，`expected_counts` 不需要填
- [ ] 🔴 SQL 文件写入路径是 `sql_paths` 输出的路径（非 case_dir）
- [ ] （dry_run 失败时）已调用 `parse_mysql_error()` + `build_retry_prompt()` 进行重试
- [ ] （2 轮重试仍失败时）已将失败信息写入 case_plan.json 的 `failed_tables` 字段

### 更新 case_plan.json

case_count=7 时写入 `expected_counts`：
```json
"expected_counts": {
  "branch": <分行行数>,
  "headquarter": <总行行数>
}
```

case_count=5 时 `expected_counts` 留空。

## 🔴 Output Format（上下文卫生）

1. **SQL 直接写入文件**：用 Write 工具写入 `sql_paths` 返回的路径
2. **允许输出**：每张表的验证结果（PASS/FAIL）、列数、行数、expected_counts
3. **重试时允许输出**：错误摘要、修复方向
4. **禁止输出**：完整 INSERT/DELETE 语句、validate/dry_run 完整输出、重试 prompt 原文

## Exit

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" done
```
