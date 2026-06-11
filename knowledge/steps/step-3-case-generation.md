# Step 3 — 生成用例

## Precondition

编排器已确认步骤 0a~2d 完成。

## Actions

### 1. 读取 case_plan.json

所有生成决策从该文件获取，不依赖 Agent 记忆。

### 2. 创建目录结构（生成代码前必须完成）

从 `case_plan.json` 读取 `case_order`、`paths.test_case_dir`（完整绝对路径）、`menu.level2`、`menu.name`，为每个用例创建子目录：

```
{test_case_dir}/{二级菜单}/{菜单名}/
├── {菜单名}_分行权限/test_{功能代码}_branch.py
├── {菜单名}_总行权限/test_{功能代码}_headquarter.py
├── {菜单名}_全量查询/test_{功能代码}_queryall.py
├── {菜单名}_单条件查询/test_{功能代码}_querysingle.py
├── {菜单名}_多条件查询/test_{功能代码}_querymulti.py
├── {菜单名}_条件重置/test_{功能代码}_reset.py
└── {菜单名}_结果预览/test_{功能代码}_preview.py
```

文件命名格式：`test_{功能代码}{文件后缀}.py`，功能代码从 case_plan.json 的 `menu.code` 获取，后缀见 `gates/gate-3.md` 映射表。禁止平铺在菜单根目录。

目录命名格式：`{菜单名}{目录后缀}`，禁止只用后缀。示例：`信托代销交易类确认流水查询_分行权限` ✅ | `_分行权限` ❌

### 3. 生成前 gate

1. 预埋 SQL 已通过步骤 2 检查清单
2. 已读取 `case_plan.json`
3. 已读取对应模板文件（`assets/templates/` 下对应的 .py 文件）
4. 已创建所有用例子目录

### 4. 代码生成

查阅 `gates/gate-3.md` 确定模板顺序。代码只能来自 `assets/templates/`。模板占位符见 `references/case-template.md`。

**branch 必须第一个生成**，执行顺序严格按 case_plan.json 的 `case_order`。

### branch/headquarter 全字段填充规则

branch.py 和 headquarter.py 模板的 `_fill_all_fields(page)` 函数从 `form_fields.all` 自动生成，**排除 `excluded_fields` 中的字段**。每个 value 非空的字段都生成对应填入代码：
- `input` → `_fill_input_by_label(page, "label", "value")`
- `single-select` → `_select_dropdown_by_label(page, "label", "value")`
- `multi-select` → `_select_dropdown_by_label(page, "label", "value")`
- ~~`tree-select`~~ → 不生成代码（排除）

### 字段顺序规则

**生成代码时，必须保证字段的填入顺序**：
- 如果要填"产品代码"，必须先填"TA代码"
- 如果要填"客户标识"，必须先填"客户标识类型"
- 其他字段按 form_fields.all 的顺序填入

**原因**：某些字段的选项依赖于其他字段的选择（如产品代码的选项依赖于TA代码）。

### querymulti 全字段填充去重规则

querymulti 先调用 `_fill_required_fields` 再调用 `_fill_all_fields`。`_fill_all_fields` 必须排除 `_fill_required_fields` 已填入的字段（含 field_groups 连带），禁止重复。

1. 确定 `_fill_required_fields` 实际填入的所有字段（含 field_groups 连带）
2. 从 `form_fields.all` 中排除这些字段
3. 剩余字段生成 `_fill_all_fields` 代码

```python
# ✅ 正确：_fill_required_fields 填客户标识组，_fill_all_fields 跳过
async def _fill_required_fields(page):
    await _select_dropdown_by_label(page, "客户标识类型", "0:入账账号")
    await _fill_input_by_label(page, "客户标识", PRESET_CUSTOMER_ID)

async def _fill_all_fields(page):
    await _select_dropdown_by_label(page, "产品代码", "11:AI自动化测试产品")
    await _fill_input_by_label(page, "起始日期", "YYYY-01-01")  # YYYY = 当前年份

# ❌ 错误：_fill_all_fields 重复填入 required 已填字段
```

### field_groups 强制绑定规则

生成 `_fill_required_fields`、`_fill_filter_fields`、`_fill_all_fields` 时，必须检查 `case_plan.json` 的 `field_groups`。如果当前函数涉及的任何字段属于某个 field_group，则该 group 的所有字段必须按 order 顺序一起生成，即使其他字段的 value 为空或不属于 required。

### 排除字段处理

生成 `_fill_all_fields`、`_fill_filter_fields` 等函数时，从 `form_fields.all` 中排除 `excluded_fields` 中的字段。

**处理规则**：
1. 读取 `case_plan.json` 的 `form_fields.excluded_fields`
2. 生成代码时，从 `form_fields.all` 中排除这些字段
3. 排除的字段仍然保留在 `form_fields.all` 中（勘测时需要），但不生成对应的填入代码

**excluded_fields 包含的字段类型**：
- `tree-select` 类型的字段（如机构代码）
- 不能重复的字段（如编号、流水号、申请编号、序列号、单号、合同号、协议号）

**示例**：
- `form_fields.all` 包含：[客户标识类型, 客户标识, TA代码, 产品代码, 开始日期, 结束日期, 申请编号, 机构代码]
- `excluded_fields` = ["申请编号", "机构代码"]
- 生成代码时排除申请编号和机构代码，只处理：[客户标识类型, 客户标识, TA代码, 产品代码, 开始日期, 结束日期]

### reset 测试值选择规则

重置用例的 `${RESET_TEST_VALUE}` 展开时：
1. 取 `case_plan.form_fields.reset_test_field.value`
2. 如果 value 非空 → 使用该值（字段自身预设值，日期字段会是日期，文本字段会是文本）
3. 如果 value 为空 → 回退到 `config.json.presets.customer_id`（即 `${CUSTOMER_ID}`）

这样确保日期框填入日期、文本框填入文本，不会出现客户ID填入日期框的问题。

## 🔴 Output Format（上下文卫生）

1. **模板按需读取**：一次读一个模板，生成对应用例后读下一个
2. **生成代码直写文件**：用 Write 工具写入
3. **允许输出**：每个用例文件的文件名、行数、关键决策（如哪些字段被选为 reset_test_field）
4. **禁止输出**：完整模板内容、完整生成代码

## Checklist

- [ ] 目录结构已创建
- [ ] 所有模板文件已读取
- [ ] branch 第一个生成，顺序按 case_order
- [ ] querymulti _fill_all_fields 已排除 required 字段
- [ ] field_groups 绑定规则已检查

## Exit

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" done
```
