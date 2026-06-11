# Step 1a — 表单扫描 + 查询策略判定

## Precondition

编排器已确认步骤 0a~0b 完成。

## Actions

`{case_dir}` = `{paths.project_dir}/{paths.test_case_dir}/{menu.level2}/{menu.name}`
`{config_json}` = `{case_dir}/config.json`

### 运行探勘

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/stagehand_explorer/src/explore_wrapper.py" --mode=fast --config "{config_json}" -o "{case_dir}/explore_result.json"
```

### 判定查询策略

从探勘结果确认字段特征后，查阅 `gates/gate-1a.md`：
- 探勘引擎自动从 Vue 组件 `vm.fields[].isRequired` 读取必填状态，并探测关联必填（`conditionalRequiredBy`）
- `isRequired=true` 或 `conditionalRequiredBy` 非空 → `has_required_fields=true`
- **同时检查页面是否含"客户标识类型"+"客户标识"字段** → `has_customer_fields` → `customer_route` → 影响 DELETE 条件、断言来源、分行/总行用例结构
- **始终使用单路全条件查询探勘**，不再区分 dual/single
- `queryall_template`：`has_required_fields=false` → `queryall_clean.py`，否则 `queryall.py`

### 更新 case_plan.json

写入 `gate_decisions` 和 `form_fields`（具体字段见 `gates/gate-1a.md`）。

**form_fields.all 组装**：将探勘到的每个表单元素与 config.json presets 匹配，为所有字段生成值。

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/config_reader.py" match_presets "{case_dir}/explore_result.json"
```

输出为带 `value` / `value_source` / `options` 的 form_fields 结构。所有字段均有值：
- `value_source=preset`：匹配到 Excel 预埋数据
- `value_source=first_option`：下拉框取第一项（preset 不匹配或不存在）
- `value_source=generated`：按字段类型自动生成（日期、编号、数值等）
直接写入 case_plan.json。

### 日期类型修正

检查 `explore_result.json`，label 含"日期"且 type 为 `single-select` 的字段，修正 type 为 `input`。**同时修正 case_plan.json 中 form_fields.all 对应字段的 type**，确保 step-1b 生成动作文件时使用正确的类型。

### 排除字段配置

检查 `form_fields.all` 中的字段类型，将以下类型的字段自动加入 `excluded_fields`：
- `tree-select`（如机构代码）

这些字段在勘测时需要填写（确保日志完整），但在用例中不需要选择（使用系统默认值）。

在 `case_plan.json` 中添加 `excluded_fields` 字段：
```json
{
  "form_fields": {
    "all": [...],
    "required": [...],
    "excluded_fields": ["机构代码"]
  }
}
```

## 🔴 Output Format（上下文卫生）

1. **explore_result.json 完整内容不复述**（可达 3000+ tokens），但可以输出字段名列表、类型分布
2. **match_presets 输出不复述**完整 JSON，但可以输出：field_count、required_count、customer_route、各字段 value_source 分布
3. **允许输出**：gate 决策结论、queryall_template 选择、关键字段发现
4. **禁止输出**：explore_result.json 原文、form_fields.all 完整结构、options 列表

## Checklist

- [ ] 探勘完成，explore_result.json 已生成
- [ ] 已查阅 gates/gate-1a.md，queryall_template / customer_route 已判定
- [ ] gate_decisions + form_fields 已写入 case_plan.json
- [ ] 日期类型已修正
- [ ] 所有字段均已赋值，value_source 分布合理

## Exit

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" done
```
