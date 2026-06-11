# Gate 1b — 间接分行过滤 + 最终用例数（步骤 1b 执行后）

## 1b-1：检查分行过滤（从 SQL 日志分析）

**直接阅读步骤 1b 捕获的 SQL 日志原文**，判断两个问题：

**1. `has_branch_column`**：业务表是否有 `%branch%` 列（如 `open_branch`）
- 在日志的 SELECT/INSERT/WHERE 中搜索 `%branch%` 关键字
- 有 → `has_branch_column=true`（直接分行过滤）；无 → `has_branch_column=false`

**2. `has_indirect_branch_filter`**：后端是否有间接分行过滤逻辑
- 阅读日志中的 SQL WHERE 条件和 JOIN 关联
- 看是否存在按分行机构限定查询范围的逻辑（如通过 `tbclientseller2` 关联过滤）
- 关键是 SQL 的语义，不是出现了哪些表名

禁止用固定关键字做 grep 匹配来判断。不同菜单的分行过滤实现方式不同。

**过滤列决定预埋策略**：确定 `has_branch_column` 和 `has_indirect_branch_filter` 后，必须同时识别 SQL 中用于分支过滤的具体列名，写入 `case_plan.json` 的 `gate_decisions.branch_filter_column`。

## 1b-2：确定 branch_source 和 case_count

| has_branch_column | has_indirect_branch_filter | branch_source | case_count |
|:---:|:---:|---|---|
| true | — | direct | 7 |
| false | true | indirect | 7 |
| false | false | none | 5 |

写入 case_plan.json：

```json
"gate_decisions": {
  ...,
  "has_branch_column": <true/false>,
  "has_indirect_branch_filter": <true/false>,
  "branch_source": "<direct/indirect/none>",
  "branch_filter_column": "<open_branch|client_no|...>",
  "case_count": <7/5>
},
"filter_selections": {
  "single": {
    "group": "<ta|customer|account>",
    "fields": [{"label": "字段名", "value": "预埋值"}]
  },
  "multi": "ALL"
}
```

**single 字段组选择规则（按优先级，选页面上存在的第一组）：**

| 优先级 | group | 字段 | 说明 |
|:---:|---|---|---|
| 1 | ta | TA代码 | TA 多选时选第一个 TA |
| 2 | customer | 客户标识类型 + 客户标识 | 必须成对填入 |
| 3 | account | 账号类型 + 账号 | 必须成对填入（如有） |

页面没有任何标识性字段时，用 form_fields.all 中第一个下拉框。

**multi 固定为 "ALL"**：多条件查询 = 全选查询，填入 form_fields.all 中所有字段。

**重置测试字段覆盖规则**：Step 1a 中 `form_fields.reset_test_field` 是临时值。确定 form_fields.all 后必须覆盖：从 all 中选一个类型为 `input` 的字段。优先选日期输入框，其次选普通文本输入框。

**Fallback 规则**：如果 `form_fields.all` 中**所有字段都是 select 类型**（没有 input 类型），则选择第一个 select 类型字段作为 `reset_test_field`。此时重置断言应验证该字段的 placeholder 文本或空值状态（而非空字符串 `""`），参见 code-rules.md rule 7。

```json
"form_fields": {
  ...,
  "reset_test_field": {<从 filter_selections.multi 中选一个 input 类型字段，无 input 时选第一个 select>}
}
```

## 1b-3：预埋策略（preset_strategy）

写入 `preset_strategy` 到 `case_plan.json`：

```json
"preset_strategy": {
  "queryall_table": "<查询日志中查到的汇总表名>",
  "queryall_type": "<table/view>",
  "base_tables": ["<ui_bc 族的表+配置表>"],
  "biz_tables": ["<分片业务表>"],
  "preset_tables": ["<sql_extractor输出涉及的所有表>"],
  "branch_test_method": "querysingle",
  "branch_filter": {"label": "<单条件筛选字段label>", "value": "<单条件筛选值>"}
}
```

- `queryall_table`：查询日志中查到的汇总表名
- `base_tables`：ui_bc 族的表 + 配置表，写入 `_base.sql` → `ui_bc` 库
- `biz_tables`：分片业务表，写入 `.sql` → 分片 DB 库
- `preset_tables`：sql_extractor 输出涉及的所有表，全部写入，Agent 不得自行过滤
- `branch_test_method`：默认 `querysingle`

**表分类规则**：由 `sql_extractor.py classify` 命令自动分类（routing/business/system/view），Agent 不得手动判断。

**DELETE 条件与 customer_route 联动**：

| customer_route | DELETE 条件 | 原因 |
|:---:|---|---|
| true | `WHERE bank_acc = '{customer_id}'` | 客户路由模式下只查分片表，必须按客户标识清理 |
| false | `WHERE ta_code = '{ta_code}'` | 非客户路由按 TA 代码清理即可 |

**数据库路由规则**：配置表（tbdxtrustproduct / tbclientseller / tbclientmap / tbclient2）+ qry 物理表 → `_base.sql` → `ui_bc` 库。分片物理表 → `.sql` → 分片 DB 库。VIEW 表禁止生成任何 INSERT，严禁将 VIEW 的 INSERT 放到业务 SQL 中。
