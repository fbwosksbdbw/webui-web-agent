# Gate 1a — 表单字段特征 + 查询策略判定（步骤 1a 执行后）

从 Stagehand 探勘结果确认字段特征，判定查询策略。

## 1a-1：字段特征

| 判断项 | 状态 key | 如何判断 |
|--------|---------|---------|
| 页面是否含客户标识字段 | has_customer_fields | Stagehand 结果中同时存在"客户标识类型"和"客户标识"两个字段 |
| 客户标识字段是否必填 | customer_fields_required | 探勘结果中"客户标识类型"或"客户标识"任一 `isRequired=true` 或 `conditionalRequiredBy` 非空 → true |
| TA 下拉框是否多选 | ta_multi_select | `is_ta_multi_select(fields)` 返回 true |
| 页面是否有必填字段 | has_required_fields | `has_required_fields(fields)` — `isRequired=true` 或 `conditionalRequiredBy` 非空均视为必填 |

## 1a-2：查询策略判定

依据 `has_required_fields` 决定模板，**始终使用单路全条件查询探勘**：

| has_required_fields | queryall_template | 说明 |
|:---:|---|---|
| false | queryall_clean.py | 无必填校验 → 单路全选查询 |
| true | queryall.py | 有必填字段 → 填必填字段查询 |

所有场景统一使用单路全条件查询探勘，不再区分 dual/single。

## 1a-3：客户路由判定

`customer_route` 为三态值，由 `has_customer_fields` 和 `customer_fields_required` 共同决定：

| has_customer_fields | customer_fields_required | customer_route | 分行/总行查询方式 | 说明 |
|:---:|:---:|:---:|:---:|---|
| true | true | `true` | 单条件查询（只填客户标识类型+客户标识） | DELETE 用 `bank_acc`，断言基于分片表预埋行数 |
| true | false | `both` | 双路查询（先空查再带参查） | 执行两次查询，合并两路日志；DELETE 用 `bank_acc` |
| false | — | `false` | 必填字段查询 | DELETE 用 `ta_code` |

**`customer_route = "both"` 的含义**：
- 第一次查询（空查）：不填客户标识字段，只填其他必填字段 → 捕获全局汇总表路由
- 第二次查询（带参查）：填入客户标识类型+客户标识 → 捕获分片物理表路由
- 两路日志合并后分析，确保预埋 SQL 同时覆盖全量和单例路由

**分行/总行预埋数据规则（case_count=7 时）：**

| 场景 | branch_source | 过滤列 | 两行数据必须不同的列 | 分行预期 | 总行预期 |
|:---:|:---:|:---|---|---|---|
| 业务表有 branch 列 | direct | `open_branch` | `open_branch`（0011 / 100） | 1 | 2 |
| 业务表无 branch 列，通过 tbclientseller2 间接过滤 | indirect | `client_no` | `client_no` | 1 | 2 |

**direct 场景**：第1行 `open_branch = presets.branch_no`，第2行 `open_branch = presets.hq_branch_no`，`client_no` 两行可相同。

**indirect 场景**：第1行 `client_no = 分行可访问的客户号`，第2行 `client_no = 分行不可访问的值`。tbclientseller2 的 INSERT 必须包含 `branch_filter_column` 列。

两场景均只需预埋 **2 条**业务数据。

## 写入 case_plan.json

```json
"gate_decisions": {
  "has_required_fields": <true/false>,
  "has_customer_fields": <true/false>,
  "customer_fields_required": <true/false>,
  "customer_route": <true/false/"both">,
  "ta_multi_select": <true/false>,
  "queryall_template": "<queryall_clean/queryall>"
},
"form_fields": {
  "all": [
    {"label": "字段名", "type": "input|single-select|multi-select", "is_required": false, "options": ["下拉选项1", "下拉选项2"], "value": "填入值", "value_source": "preset|first_option|generated"}
  ],
  "required": [<从Stagehand结果提取 is_required=true 的字段>],
  "reset_test_field": {<选第一个普通输入框>},
  "preview_buttons": [<从页面操作按钮提取>]
}
```

**form_fields.all 组装规则**：探勘到的每个表单元素都必须记录。`value` 由 `generate_field_values()` 统一生成，`value_source` 标明值来源（preset/first_option/generated）。`options` 来自探勘阶段抓取的下拉选项。
