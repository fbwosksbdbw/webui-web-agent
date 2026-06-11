# Gate 3 — 用例生成顺序、模板选择和输出路径（步骤 3 执行前）

查阅 `case_plan.json` 中的 `case_count` 和 `has_required_fields`。

## 3-0：用例执行顺序

| case_count | 执行顺序 |
|-----------|---------|
| 7 | branch → headquarter → queryall → querysingle → querymulti → reset → preview |
| 5 | queryall → querysingle → querymulti → reset → preview |

| has_required_fields | queryall 模板 |
|:---:|---|
| false | queryall_clean.py |
| true | queryall.py |

**branch 必须第一个做**：探勘用分行账号，branch 断言条数直接从探勘结果获取。

## 3-0b：分行/总行查询策略

当 `has_customer_fields=true` 时，branch.py / headquarter.py 只填客户标识类型 + 客户标识，不填其他过滤条件。分行断言 = 1，总行断言 = 2。

当 `has_customer_fields=false` 时，填入必填字段或全选查询。

预埋只需 2 条（见 Gate 1a-3）。

## 3-1：querymulti 去重规则

querymulti 先调 `_fill_required_fields` 再调 `_fill_all_fields`。`_fill_all_fields` 必须排除 `_fill_required_fields` 已填入的字段（含 field_groups 连带）。生成时先列出 required 实际填的字段集合，从 `form_fields.all` 中排除后再生成 `_fill_all_fields` 代码。

## 3-2：输出路径和文件命名

| 模板 | 目录后缀 | 文件后缀 |
|------|---------|---------|
| branch.py | `_分行权限` | `_branch` |
| headquarter.py | `_总行权限` | `_headquarter` |
| queryall.py / queryall_clean.py | `_全量查询` | `_queryall` |
| querysingle.py | `_单条件查询` | `_querysingle` |
| querymulti.py | `_多条件查询` | `_querymulti` |
| reset.py | `_条件重置` | `_reset` |
| preview.py | `_结果预览` | `_preview` |

**路径公式：**
```
用例: {test_case_dir}/{二级菜单}/{菜单名}/{菜单名}{目录后缀}/test_{功能代码}{文件后缀}.py
SQL:  {test_case_dir}/{二级菜单}/{菜单名}/{功能代码}.sql
基础表: {test_case_dir}/{二级菜单}/{菜单名}/{功能代码}_base.sql
case_plan: {test_case_dir}/{二级菜单}/{菜单名}/case_plan.json
```

**路径来源（Excel 驱动，不硬编码）：**
- `project_dir` = Excel「环境配置」sheet 的 `project_dir` 行
- `test_case_dir` = `project_dir` + `TEST_CASE_DIRS[一级菜单]`（完整绝对路径）
- SQL 路径 = `test_case_dir` + 功能代码.sql，模板中直接引用

**生成前必须创建子目录：**
```bash
mkdir -p "{test_case_dir}/{二级菜单}/{菜单名}/{菜单名}_分行权限"
mkdir -p "{test_case_dir}/{二级菜单}/{菜单名}/{菜单名}_总行权限"
# ... 其余用例类型同理
```

## 写入 case_plan.json

```json
"case_order": [<按上表顺序填入>],
"paths": {
  "project_dir": "<env.project_dir>",
  "test_case_dir": "<computed>",
  "templates": <Gate 3-1 固定映射表>
}
```
