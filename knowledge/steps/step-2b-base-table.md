# Step 2b — 基础表分配

## Precondition

编排器已确认步骤 0a~2a 完成。

## Actions

### 日志驱动分派（替代固定 checklist）

从 `case_plan.json` 读取 `shard.physical_tables` 和 `paths.project_dir`，调用脚本命令获取确定性分类：

```bash
python "{SKILL_DIR}/scripts/sql_extractor.py" classify "{case_plan.json路径}" --env "{env_key}" --project-dir "{project_dir}" --module "{module}"
```

🔴 **必须传入 `--module` 参数**，否则会默认使用 `ifm60-trust` 环境，导致基金/理财等模块的分片表无法正确识别。

**该命令自动完成：**
1. 对每张逻辑表，连 MySQL 查 `information_schema.TABLES` 确认 TABLE vs VIEW
2. 按 `_SYSTEM_TABLE_PREFIXES` 规则识别系统配置表
3. 按 `table_to_db()` 分派 ui_bc（routing）vs 业务库（business）
4. 输出分类结果（business / system / routing / view）

**禁止 Agent 手动判断表的 TABLE/VIEW 类型或分类**。必须通过此命令获取。

分派结果示例：

| 逻辑表 | table_to_db（族） | MySQL TABLE_TYPE | 分类 | 归属 |
|--------|-------------|------|------|------|
| `tbclientseller` | `ui_bc` | BASE TABLE | routing | `_base.sql` |
| `tbclientmap` | `ui_bc` | BASE TABLE | routing | `_base.sql` |
| `tbdxtrusttranscfmqry` | `ui_bc` | BASE TABLE | routing | `_base.sql` |
| `tbdxtrusttranscfm` | `ui_trust` | BASE TABLE | business | 业务 SQL |
| `tbsysarg` | `ui_bc` | — | system | 禁止预埋 |
| `tsys_menu` | `ui_bc` | — | system | 禁止预埋 |
| `tbtainfo` | `ui_bc` | — | system | 禁止预埋 |
| `tbproduct` | `ui_bc` | — | system | 禁止预埋 |

### 🔴 只处理日志中出现的表

- **日志里出现的** → skill 按 `table_to_db` 分派，生成 INSERT
- **日志里没出现的**（如用户额外加的 `tbclient2`、`tbdxtrustproduct`）→ 用户自己维护，skill 不管
- `tbclientseller` 在日志里 → 自然会被 `table_to_db` 分到 `ui_bc` → 进入 `_base.sql`

### 表分类（由 `classify` 命令输出，禁止 Agent 手动判断）

| 分类 | 说明 | SQL 操作 |
|------|------|---------|
| **system** | 系统配置表（匹配 `_SYSTEM_TABLE_PREFIXES`）。平台级数据，禁止预埋。 | **禁止生成任何 INSERT** |
| **view** | MySQL 确认的 VIEW 表。 | **禁止生成任何 INSERT** |
| **routing** | ui_bc 族的 BASE TABLE。客户号→分片映射，依赖测试客户号。 | `REPLACE INTO` → `_base.sql` |
| **business** | 业务库族（ui_trust/ui_fund/ui_fina）的 BASE TABLE。查询目标表。 | `DELETE + INSERT` → `业务 .sql` |


### 生成规则

1. **system / view**：**🔴 禁止生成任何 INSERT**。记录到 `case_plan.json` 的 `preset_strategy.no_insert_tables` 中。
2. **routing**（classify 输出 `classification=routing`）：用 `REPLACE INTO` 配合物理表名，写入 `_base.sql`。必须有完整列清单（来自 schema cache）。
3. **business**（classify 输出 `classification=business`）：用 `DELETE + INSERT` 配合物理表名，写入业务 `.sql`。必须有完整列清单。
4. 分片表覆盖：`physical_tables` 里有几个物理表就生成几份 INSERT

### 更新 case_plan.json

基于 classify 命令输出，更新 `preset_strategy`：

```json
"preset_strategy": {
  "base_tables": ["<classify输出为routing的表名>"],
  "biz_tables": ["<classify输出为business的表名>"],
  "no_insert_tables": ["<classify输出为system/view的表名>"]
}
```

`base_tables` 和 `biz_tables` 写入 `preset_tables`。所有路径通过 `sql_paths` 命令获取。

### 🔴🔴🔴 shard.db 权威来源（最高优先级）

**`shard.db` 必须从 `classify` 命令输出中获取，禁止自行推断！**

**判断逻辑**：
1. 执行 `classify` 命令，获取每张表的分类结果
2. 检查 `classification` 字段：
   - `system` → 系统表，禁止预埋
   - `routing` → 路由表，进入 `_base.sql`
   - `business` → 业务表，进入业务 `.sql`
3. **`shard.db` 的取值规则**：
   - 有 `business` 表 → `shard.db` = business 表的 `db` 字段值
   - 无 `business` 表（全是 system/routing）→ `shard.db = "ui_bc"`

**🔴 禁止根据模块名猜测数据库名！**

| 错误做法 | 正确做法 |
|---------|---------|
| 模块是 `dxfund` → `shard.db = "ui_fund2"` ❌ | 从 `classify` 输出获取 `shard.db` ✅ |
| 模块是 `dxtrust` → `shard.db = "ui_trust2"` ❌ | 从 `classify` 输出获取 `shard.db` ✅ |

**示例**：
```bash
python sql_extractor.py classify case_plan.json --env ifm60-dxfund --project-dir F:/python_project
```

输出：
```json
{
  "tbdxfundproduct": {
    "classification": "system",
    "db": "ui_bc"
  },
  "tbdxfundshareqry": {
    "classification": "routing",
    "db": "ui_bc"
  }
}
```

→ 无 business 表 → `shard.db = "ui_bc"`

## 🔴 Output Format（上下文卫生）

1. **允许输出**：分类统计（N routing, M business, K system, J view）、shard.db
2. **禁止输出**：classify 完整 JSON 原文、逐表分类详情（已写入 case_plan.json）

## Checklist

- [ ] 已执行 `sql_extractor.py classify` 命令，获得每张表的确定分类
- [ ] 🔴 分类为 `system` 的表**未生成任何 INSERT**，仅在 case_plan.json 的 `no_insert_tables` 中记录
- [ ] 🔴 分类为 `view` 的表**未生成任何 INSERT**，仅在 case_plan.json 的 `no_insert_tables` 中记录
- [ ] 分类为 `routing` 的表已确定进入 `_base.sql`
- [ ] 分类为 `business` 的表已确定进入业务 `.sql`
- [ ] 已调用 `config_reader.py sql_paths` 获得正确的 SQL 文件路径
- [ ] 不再按固定清单（tbtainfo/产品表/tbclientseller2）逐项检查
- [ ] 🔴🔴🔴 `shard.db` 已从 `classify` 命令输出获取，未根据模块名自行推断
- [ ] 🔴 如果无 business 表，`shard.db` 已设为 `"ui_bc"`

## Exit

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" done
```
