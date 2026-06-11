# 用例模板使用说明

> 代码模板文件位于 [assets/templates/](../assets/templates/)，每个模板都是**完全自包含**的 `.py` 文件。生成时读取模板 + 替换占位符即可，不做拼接。

## config.json（Excel 配置快照）

Step 0a 由 `config_reader.py init` 写入，包含 `env`、`menu`、`presets`、`computed` 四个字段。后续步骤读取 env/presets 从此文件获取，不再重复读 Excel。

输出路径：`{test_case_dir}/{二级菜单}/{菜单名}/config.json`

## case_plan.json（探勘决策持久化）

所有生成决策的唯一数据源。步骤 0a~2c 增量写入，步骤 2d 验证完整性，步骤 3 直接读取。

模板路径：`assets/templates/case_plan.json`
输出路径：`{test_case_dir}/{二级菜单}/{菜单名}/case_plan.json`（`test_case_dir` 从 `config_reader.py init` 的 `paths` 字段获取）

各占位符来源详见 skill.md 各步骤和 GATE.md 各 Gate。

### preset_strategy（Gate 1b-3 写入）

预埋策略字段，由 Gate 1b-3 决定后写入 `case_plan.json`：

| 字段 | 类型 | 说明 |
|------|------|------|
| `queryall_table` | string | 查询日志中查到的汇总表名 |
| `queryall_type` | string | `table` 或 `view` |
| `base_tables` | list | ui_bc 族的表 + 配置表 → `_base.sql` → `ui_bc` |
| `biz_tables` | list | 分片业务表 → `.sql` → 分片 DB |
| `preset_tables` | list | sql_extractor 输出涉及的所有表（base+biz），**全部写入不得过滤** |
| `branch_test_method` | string | 默认 `querysingle`（分行用单条件查更可靠） |
| `branch_filter` | object | 分行测试用的单条件筛选 `{label, value}` |

### field_groups（config_reader.py 配置级绑定）

`config_reader.py init_menu()` 输出中包含 `field_groups`，声明字段间的填写顺序依赖：

```json
"field_groups": [
  {
    "name": "客户标识组",
    "fields": ["客户标识类型", "客户标识"],
    "order": ["客户标识类型", "客户标识"]
  }
]
```

`cdp_executor.py` 在执行 `fill`/`select` 动作时自动检测字段所属 group，按 `order` 依次填入整组（已填过的跳过）。

**级联依赖**：下拉框选项可能依赖前置字段（如 `产品代码` 选项随 `TA代码` 变化）。通过 `field_groups` 声明即可：
```json
"field_groups": [
  {"name": "TA产品组", "fields": ["TA代码", "产品代码"], "order": ["TA代码", "产品代码"]}
]
```

**`__FIRST_OPTION__` 哨兵值**：无 preset 的 single-select 字段使用此值，触发 `select_single()` 跳过匹配直接选第一个可见选项。`build_actions` 自动为空值 single-select 生成此哨兵。

## 模板文件清单

| 文件 | 用途 | 说明 |
|------|------|------|
| [branch.py](../assets/templates/branch.py) | 分行权限 | 完全自包含。`@pytest.mark.login_user("branch_user")` + 精确断言 |
| [headquarter.py](../assets/templates/headquarter.py) | 总行权限 | 完全自包含。不加 mark + 精确断言 |
| [queryall.py](../assets/templates/queryall.py) | 全量查询(有必填字段) | 完全自包含。填必填字段后查询 |
| [queryall_clean.py](../assets/templates/queryall_clean.py) | 全量查询(无必填字段) | 完全自包含。空表单直接查 |
| [querysingle.py](../assets/templates/querysingle.py) | 单条件查询 | 完全自包含。标识性字段组（TA代码/客户标识组/账号组） |
| [querymulti.py](../assets/templates/querymulti.py) | 多条件查询 | 完全自包含。`_fill_all_fields()` 填入所有字段后查询 |
| [reset.py](../assets/templates/reset.py) | 条件重置 | 完全自包含。字段名从 case_plan 填入 |
| [preview.py](../assets/templates/preview.py) | 结果预览 | 完全自包含。按钮 fallback 遍历（code-rules 第5条） |

## 使用方式

1. 读取 `case_plan.json`（所有占位符值从该文件提取）
2. 读取对应模板文件
3. 用 `str.replace()` 替换所有 `${PLACEHOLDER}` 占位符
4. 写入目标路径

## 占位符说明

| 占位符 | 来源 | 说明 |
|--------|------|------|
| `${LEVEL1}` | case_plan.menu.level1 | 一级菜单名 |
| `${LEVEL2}` | case_plan.menu.level2 | 二级菜单名 |
| `${MENU_NAME}` | case_plan.menu.name | 菜单名称 |
| `${FUNC_CODE}` | case_plan.menu.code | 功能代码 |
| `${MENU_URL}` | case_plan.menu.url | 菜单 hash 路由 |
| `${SHARD_DB_KEY}` | case_plan.shard.db | 业务表数据库 key（来自 `table_to_db()`，如 `ui_trust1`、`ui_bc`），8 个模板的 `db_setup` fixture 使用 |
| `${CUSTOMER_ID}` | config.json.presets.customer_id | 预埋客户号 |
| `${TA_OPTION_1}` | config.json.presets.ta_option_1 | 主 TA 下拉选项（可选，TA 多选时用） |
| `${EXPECTED_BRANCH}` | case_plan.expected_counts.branch | 预期分行条数 |
| `${EXPECTED_HQ}` | case_plan.expected_counts.headquarter | 预期总行条数 |
| `${REQUIRED_FIELDS_CODE}` | 生成时展开 | 必填字段填充代码（从 form_fields.required 生成） |
| `${ALL_FIELDS_CODE}` | 生成时展开 | 全字段填充代码（从 form_fields.all 生成，排除 required 已填） |
| `${FILTER_FIELDS_CODE}` | 生成时展开 | 分行/总行筛选代码（从 filter_selections.single 生成） |
| `${SINGLE_FILTER}` | 生成时展开 | querysingle 单条件查询代码（从 filter_selections.single 生成） |
| `${RESET_TEST_FIELD}` | case_plan.form_fields.reset_test_field.label | 重置测试字段 label |
| `${RESET_TEST_VALUE}` | case_plan.form_fields.reset_test_field.value（空则回退 `${CUSTOMER_ID}`） | 重置测试填入值，优先用字段自身值 |
| `${OWNER}` | config.json.env.owner（→ Excel 环境配置 owner） | 用例负责人，用于 `@allure.label("owner", ...)` |
| `${EXCLUDED_FIELDS}` | case_plan.form_fields.excluded_fields | 需要在用例中排除的字段列表（如机构代码、不能重复的字段） |

## 用例差异速查

| 用例 | 填写字段 | 备注 | 登录用户 |
|------|---------|------|---------|
| branch | `_fill_filter_fields()`（客户标识类型+客户标识） | 填后关闭弹出层→点击查询 | branch_user |
| headquarter | `_fill_filter_fields()`（客户标识类型+客户标识） | 填后关闭弹出层→点击查询 | 默认(总行) |
| queryall | `_fill_required_fields()` | 填后关闭弹出层→点击查询 | 默认 |
| queryall_clean | 无(空表单) | 关闭弹出层→点击查询 | 默认 |
| querysingle | 标识性字段组（TA代码/客户标识组/账号组） | 填后关闭弹出层→点击查询 | 默认 |
| querymulti | `_fill_all_fields()`（form_fields.all 所有字段） | 填后关闭弹出层→点击查询 | 默认 |
| reset | 填 `${RESET_TEST_FIELD}` | 填后关闭弹出层→点击重置 | 默认 |
| preview | `${REQUIRED_FIELDS_CODE}` | 填后关闭弹出层→点击查询→fallback 遍历 | 默认 |

## 目录与文件命名规范

详见 `gates/gate-3.md`，路径公式和文件后缀表以 Gate 3 为准。
