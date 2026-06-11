# Step 2a — 分析日志

## Precondition

编排器已确认步骤 0a~1b 完成。

## Actions

### 1. 运行分析

日志文件由 step 1b 通过 `--save-log` 自动保存到用例目录，**禁止重查**。

根据 `gate_decisions.customer_route` 决定分析方式：

#### 单路分析（`customer_route = true` 或 `customer_route = false`）

直接使用已保存的日志：

```bash
python "{SKILL_DIR}/scripts/sql_extractor.py" analyze "{case_dir}/query_log.txt"
```

> 日志已在 step 1b 抓取时通过 `--save-log` 写入 `{case_dir}/query_log.txt`。直接分析即可，无需重新查询。

分析输出中包含：
- `cached_tables`（有缓存）/ `missing_tables`（缺缓存）
- **`physical_tables`**（日志中实际路由到的物理表名 → 按逻辑表分组）

#### 双路分析（`customer_route = "both"` 时）

使用两份日志调用 `analyze_dual_route_for_preset()`：

```python
import sys, os
sys.path.insert(0, os.path.join(r"{SKILL_DIR}", 'scripts'))
from sql_extractor import analyze_dual_route_for_preset

result = analyze_dual_route_for_preset(
    empty_query_log=open(r"{case_dir}/query_log_empty.txt", encoding='utf-8').read(),
    param_query_log=open(r"{case_dir}/query_log_param.txt", encoding='utf-8').read(),
    presets=presets,
    form_values=form_fields_all,
    menu_context=menu_context,
)
```

或通过命令行：

```bash
python "{SKILL_DIR}/scripts/sql_extractor.py" analyze_dual "{case_dir}/query_log_empty.txt" "{case_dir}/query_log_param.txt"
```

`analyze_dual_route_for_preset` 输出中包含：
- `dual_result`：`extract_dual_route_requirements()` 的合并结果（merged_tables、merged_where 等）
- `dual_write_prompt`：合并后的双写 prompt（供 step-2c 使用）
- `cached_tables` / `missing_tables`：缓存状态
- `physical_tables`：合并后的物理表名（空查 + 带参查的并集）
- `tables_by_db`：按数据库分组的表名

> 双路分析自动将两份日志的表名取并集，WHERE 条件合并，确保预埋 SQL 覆盖全量和单例路由。

> 以下步骤 2、3 及缓存检查流程对单路/双路均适用。双路时 `physical_tables` 来自 `analyze_dual_route_for_preset` 的输出。

### 2. 判断是否需要分片路由

从 analyze 输出拿到 `physical_tables`（逻辑表名列表），检查其中是否包含分片业务表：

```python
import sys, os
sys.path.insert(0, os.path.join(r"{SKILL_DIR}", 'scripts'))
from sql_extractor import table_to_db

# 🔴 权威数据源：table_db_mapping.json
# table_to_db() 优先查 table_db_mapping.json，未命中降级到前缀规则
# 返回 'ui_bc' → 不分片；返回其他库族 → 是业务表，需要路由
has_shard_tables = any(
    table_to_db(t) != 'ui_bc'
    for t in physical_tables
)
```

- **`has_shard_tables = True`** → 需要分片路由，继续步骤 3
- **`has_shard_tables = False`** → 所有表都在 `ui_bc`，跳过步骤 3，直接设 `routing_db = ""`，`shard_db = "ui_bc"`

### 3. 计算分片路由（仅当有分片业务表时）

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/config_reader.py" compute_shard {module}
```
将输出的 `db_name` 写入 case_plan.json 的 `shard.routing_db`。

如果 Excel 预埋数据中缺少 `customer_id`，`compute_shard` 会报错。此时应检查预埋数据配置。

**🔴 `shard.db` 的权威来源是 step 2b 的 `classify` 命令输出（每张表的 `db` 字段）。** 本步骤只做初步判断，最终以 `classify` 为准。

初步判断逻辑（仅用于决定是否需要 compute_shard）：
- `has_shard_tables = True` → 执行 compute_shard 获取 routing_db，`shard.db` 最终由 step 2b 的 `classify` 输出确定
- `has_shard_tables = False` → `routing_db = ""`，`shard.db = "ui_bc"`

**禁止根据模块名猜测数据库名。`classify` 命令的 `db` 字段就是权威数据。**

### 🔴 物理表名提取（写入 case_plan.json）

从分析输出的 `physical_tables` 字段写入 case_plan.json：

```json
"shard": {
  "db": "<库名>",
  "routing_db": "<分片路由库名，全ui_bc表时为空字符串>",
  "physical_tables": {
    "tbdxtrusthissharechg": ["tbdxtrusthissharechg1", "tbdxtrusthissharechg16"],
    "tbclientseller": ["tbclientseller2", "tbclientseller16"],
    "tbclientmap": ["tbclientmap"],
    "tbtainfo": ["tbtainfo"]
  }
}
```

- `routing_db`：从 `compute_shard` 得到的分片路由库名（如 `ui_trust2`）。**全 ui_bc 表时为空字符串 `""`**
- `db`：对业务表运行 `resolve_shard_db(table, routing_db)` 得到的实际库名，用于 pytest fixture `db_key`
  - `ui_bc` 表（如 tbacccfm）→ `db = "ui_bc"`（忽略 routing_db）
  - 分片表（如 tbdxtrusthissharechg）→ `db = routing_db`（如 `"ui_trust2"`）
- `physical_tables`：从日志直接提取，逻辑表名 → 物理表名列表（不依赖计算，以日志为准）
- 🔴 删除旧的 `shard.table` 字段，改用 `shard.physical_tables`

### 缓存检查流程（cache first, DB only on miss）

1. 查看 `missing_tables` — 如果为空，所有表都有缓存，直接跳到步骤 2b
2. 如果有 `missing_tables`，对每个缺失表执行 DESCRIBE 并一键 seed 缓存：
   ```bash
   python "{SKILL_DIR}/scripts/sql_extractor.py" cache seed <表名> <DESCRIBE输出JSON>
   ```
   `cache seed` 内部调用 `seed_cache_from_describe()` → `filter_describe_result()`，一步完成过滤+写入。
3. 重新运行 analyze，确认 `missing_tables` 为空

🔴 严禁跳过缓存检查直接全表 DESCRIBE。缓存命中的表直接用。

## 🔴 Output Format（上下文卫生）

1. **允许输出**：表数量、cached/missing 分布、routing_db、分片判断结果
2. **禁止输出**：analyze 完整 JSON 原文、完整物理表名列表（已写入 case_plan.json）

## Checklist

- [ ] analyze 已运行，cached_tables / missing_tables 已明确
- [ ] （`customer_route="both"` 时）`analyze_dual_route_for_preset` 已运行，两路日志已合并分析
- [ ] missing_tables 已全部 seed 缓存，重新 analyze 确认无 miss
- [ ] 已判断是否有分片业务表（`table_to_db(t) != 'ui_bc'`），有则执行 compute_shard，无则 routing_db = ""
- [ ] `shard.routing_db` 已写入 case_plan.json（全 ui_bc 表时为空字符串）
- [ ] `shard.db` 由 step 2b 的 `classify` 命令输出确定（不自行推断）
- [ ] 🔴 `shard.physical_tables` 已写入 case_plan.json（从 analyze 输出的 `physical_tables` 提取）
- [ ] 🔴 旧的 `shard.table` 字段已删除
- [ ] （`customer_route="both"` 时）`dual_write_prompt` 已保存，供 step-2c 使用

## Exit

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" done
```
