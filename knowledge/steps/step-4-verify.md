# Step 4 — 迭代验证与熔断

## Precondition

编排器已确认步骤 0a~3 完成。所有用例文件已生成，预埋 SQL 已入库。

## Actions

### 验证循环

按 `case_plan.json` 的 `case_order` 顺序逐个验证。每个用例：

```
运行 pytest → PASSED → 下一个
            → FAILED → 读报错 → 按下方分类表修复 → 重试
                     → 连续 3 次 FAILED → 放弃，标记 SKIP，继续下一个
```

运行命令：
```bash
cd "{project_dir}" && MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 pytest "{test_case_dir}/{二级菜单}/{菜单名}/{菜单名}{目录后缀}/test_{功能代码}{文件后缀}.py" -v --tb=short 2>&1 | tail -60
```

### 错误分类与修复策略

拿到报错后，在以下 5 类中匹配，按对应策略修复：

---

**A. 选择器/定位错误**（`locator resolved to N elements` / `strict mode violation` / `Timeout waiting for`）

| 子类型 | 报错特征 | 修复 |
|--------|---------|------|
| 选择器匹配到多个元素 | `strict mode violation: locator resolved to N elements` | 加 `.first` 或用更精确的 label 文本 |
| 表格未加载 | 查询后 `_get_record_count` 持续为 0 | 数据未入库或预埋 SQL 执行失败。检查表格/分页是否为 `共 0 条` → 回步骤 2c 修正预埋 |
| 表单字段找不到 | `_fill_input_by_label` / `_select_dropdown_by_label` 超时 | 检查 label 文本是否与探勘结果一致，注意全角/半角、空格差异 |
| 操作按钮找不到 | `get_by_role("button")` 无匹配 | 改用 fallback 遍历 `["详情", "预览", "查看"]`，见 code-rules.md 第 5 条 |

---

**B. 断言失败**（`AssertionError` / `assert`）

| 子类型 | 报错特征 | 修复 |
|--------|---------|------|
| 条数断言失败 | `assert cnt > 0` 或 `assert cnt == N` 失败 | 检查 expected_counts 是否与预埋行数一致；检查是否有前端默认值（日期 20191202）混入 → 确认代码中先调了「重置」 |
| 分行断言 0 条 | branch 用例 `assert cnt == 1` 失败，实际为 0 | 分行账号权限不对或 tbclientseller2 数据缺失。检查 `branch_filter_column` 的 INSERT 是否正确 |
| 操作按钮验证失败 | `input[readonly]` 值为空 | 预埋数据缺少关键字段值（如日期为 0 导致格式化空指针），回步骤 2c 修正 |
| 重置断言失败 | 输入框值非空字符串 | 选的字段可能是下拉框而非 input。检查 `reset_test_field` 的 type 是否为 `input` |

---

**C. 超时错误**（`Timeout` / `TimeoutError`）

| 子类型 | 报错特征 | 修复 |
|--------|---------|------|
| 页面加载超时 | `Page.goto: Timeout 30000ms exceeded` | 服务器临时不可用，直接重试。连续 3 次超时才考虑排查 |
| 导航超时 | `navigate` 后页面未响应 | 确认 Chrome 未关闭、CDP 端口 9222 可连通 |
| 元素等待超时 | 查询后记录数轮询超时 | 检查页面是否仍在加载、查询条件是否命中数据、表格/分页是否渲染异常 |

---

**D. 导入/依赖错误**（`ImportError` / `ModuleNotFoundError` / `NameError`）

| 子类型 | 报错特征 | 修复 |
|--------|---------|------|
| helpers 未导入 | `NameError: name '_fill_input_by_label' is not defined` | 检查模板 import 块是否完整，对照 `assets/templates/` 中对应模板补齐 |
| Config 路径错误 | `KeyError: 'SQL_DATA_DIRS'` 或 `Config has no attribute` | 检查 `${LEVEL1}` 占位符是否已替换为实际值 |
| 预埋 SQL 路径错误 | `FileNotFoundError: *_base.sql` | 检查文件命名是否与 `{功能代码}_base.sql` 一致 |

---

**E. 数据/数据库错误**（`sqlalchemy` / `MySQL` / `ProgrammingError`）

| 子类型 | 报错特征 | 修复 |
|--------|---------|------|
| 列不存在 | `Unknown column 'xxx' in 'field list'` (Error 1054) | 编造了不存在的列名。对照 schema_cache 修正，确保每列来自 DESCRIBE 结果 |
| 表不存在 | `Table 'xxx' doesn't exist` | 表名写错或库前缀错误。检查 `table_to_db()` 路由是否正确 |
| 主键冲突 | `Duplicate entry` | DELETE 条件不完整，未覆盖所有旧数据。检查 DELETE WHERE 是否包含所有预埋数据 |

---

### 修复铁律

- **只看报错改代码，不凭猜测预防性修改**
- **一次只改一处**：改完立即重跑，通过再改下一处
- **改代码必须对照 `references/code-rules.md`** 确认不违反铁律
- **改 SQL 必须重跑预埋**：修改 INSERT 后先重跑 SQL 文件再重跑 pytest

### 熔断规则

| 条件 | 动作 |
|------|------|
| 同一文件连续 3 次 FAILED | 放弃该用例，记录到报告（标记 SKIP + 最后一次报错原因），继续下一个 |
| 连续 3 个用例被放弃 | 终止步骤 4，输出诊断摘要，等待用户介入 |

## 🔴 Output Format（上下文卫生）

1. **PASSED 用例**：输出 `✅ test_X.py PASSED (N.s)`
2. **FAILED 用例**：输出错误分类（A/B/C/D/E）+ 关键报错行 + 修复方向
3. **修复时**：输出改了什么，不输出完整 diff
4. **禁止输出**：完整 pytest traceback 原文（`--tb=short` 输出只用于诊断）

## Checklist

- [ ] 每个 pytest 文件已按 case_order 顺序单独运行
- [ ] 每个失败已按错误分类表匹配并修复
- [ ] 修复后代码已对照 code-rules.md 验证
- [ ] 未超过熔断阈值（单文件 3 次 / 累计 3 个放弃）

## Exit

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" done
```

`orchestrator.py done` 会自动将 Excel「菜单导入」中该菜单的状态标记为「已完成」。
