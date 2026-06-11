# HUI 测试代码生成铁律（19条）

> 写用例代码时必须逐条遵守。决策类规则已迁移至 `gates/`，配置类规则见 `config-guide.md`。

## 1. 表格加载等待

禁止 `wait_for_selector("table tbody tr")`（空表格也会触发）。查询后必须轮询 `_get_record_count(page)`，直到记录数大于 0 或达到用例等待上限。全量查询可使用更长等待，并定期输出等待日志。

## 2. 表单精准匹配

表单定位**优先**通过 label 文本**精确匹配**（`label.strip() == label_text`）。精确匹配失败时可回退到 `in` 模糊匹配（`label_text in label`），但必须注意"客户标识"会误匹配"客户标识类型"的风险。`cdp_executor.py` 和 `common/helpers.py` 的选择器逻辑已实现精确匹配优先 + 模糊匹配回退的策略。

## 3. 区分输入框与下拉框

填写普通输入框用 `:not(.h-select-input)` 排除下拉框；操作下拉框调用 `_select_dropdown_by_label`。

## 4. 弹窗数据读取

详情面板数据在 `<input readonly>` 中，`text_content()` 读不到。必须用 `page.evaluate()` 获取 `input.value`，或使用 Playwright 的 `input_value()` 方法（底层等价）。`cdp_executor.py` 的 `_get_textbox_value_by_label` 已正确使用 `input_value()` 读取。

## 5. 操作按钮 fallback

不同页面操作按钮文本不同（"详情"/"预览"/"查看"）。严禁硬编码，必须用 `for btn_text in ["详情", "预览", "查看"]` 遍历点击。禁止 `text=详情` 选择器（可能匹配面包屑）。

## 6. 历史类查询日期

菜单带"历史"二字时默认日期可能为旧年份（如 2019 年），代码中必须主动输入为**当前年份**范围（动态获取，如 `datetime.now().year`）。

## 7. 多选重置断言

验证重置功能时只验证普通输入框是否为空字符串 `""`。不要验证多选下拉框（重置后值可能非空）。

**select 类型 fallback**：当 `reset_test_field` 是 select 类型（所有字段都是下拉框，无 input 可选时），重置后验证该字段的 placeholder 文本（如"请选择"）或 `input.value` 为空字符串，而非验证 `.h-select-input` 的 `text_content()`。

## 8. 单条件断言

验证表格内容用 `page.locator("table tbody").get_by_text("预期值").count() > 0`，不要读 `table.first`（只能读到表头）。

## 9. 复用辅助函数

只能从 `common.helpers` import，严禁在用例文件中重写这些逻辑。

## 10. 依赖预埋不依赖已有数据

即使数据库当前有数据，也必须预制 `DELETE/INSERT` 完整预埋。测试环境数据随时可能被清空。原先有的文件就覆盖，严禁以"文件已存在"为由跳过生成。

## 11. 严禁 nth() / getByRole("textbox")

HUI 页面 input 数量极多（100+），这些选择器几乎必然误匹配。必须用 `_fill_input_by_label(page, "label文本", "值")`。

## 12. 结果预览验证

必须用 fallback 遍历 `["详情", "预览", "查看"]` 点击展开面板，然后用 `page.evaluate` 读 `input[readonly]` 的值验证有数据。

## 13. 断言必须验证实际内容

不能只数行数（占位行也算），必须验证表格含预埋数据标识或分页 `共 N 条` 中 N > 0。

## 14. 分行/总行用 @pytest.mark.login_user

总行用例不加 mark，使用 `default_user`。分行用例必须加 `@pytest.mark.login_user("branch_user")` 装饰器（不能只写在注释里）。断言精确：总行 `== M`，分行 `== N`。

## 15. 日期跨度 ≤ 365 天

HUI 前端校验日期跨度不能超过 365 天。预埋数据日期必须在查询范围内。presets 中 `query_date_start/end` 应设为**当前年份**全年（动态获取）。

## 16. SQL 预埋铁律（以查代写 + 禁止全表 DESCRIBE + 禁止编造列名）

流程：`sql_extractor.analyze_log_for_preset()` → 检查 schema_cache → 命中用缓存，未命中只对缺失表 `DESCRIBE` + `seed_cache_from_describe()` → `build_minimal_prompt()` 生成仅含必填列+WHERE 相关列的 INSERT。

**INSERT 的每一列名必须来自 DESCRIBE 结果或 schema_cache**，禁止凭记忆/凭其他表结构/凭经验推测任何列名。缓存 miss 且未执行 DESCRIBE 的表，**禁止为其生成 SQL**。

严禁 `DESCRIBE` 全表获取几十个字段。`seed_cache_from_describe()` 只返回 NOT NULL+无默认值的列（通常 5-10 个）。日期字段必须显式填入有效值（如 `YYYYMMDD` 格式的当前日期），不能为 0。

- `build_minimal_prompt()` 接受 `unique_indexes` 和 `column_comments` 参数，prompt 中会展示所有 UNIQUE 键约束和列注释语义
- Prompt 第 1 条约束已增强：`仅对上述 N 个字段赋值；所有 UNIQUE 键的列组合在每行 INSERT 中必须不同`

## 17. 系统配置表禁止 REPLACE INTO

以下为平台级系统配置表，数据已存在于数据库，测试**只读不写**：`tbtrans`、`tsys_menu`、`tsys_user`、`tbsysarg`、`tbtainfo`、`tbclientmanager`、`tbproduct`。**严禁对这些表生成 REPLACE INTO**，否则用部分列覆盖会清空其他列数据。测试需要这些表的数据时，只验证数据存在即可，不写入。若极端情况下确实需要写入，必须先 `DESCRIBE` 获取完整列清单，用全列 `REPLACE INTO`。

## 18. 🔴 Dry-Run 验证闭环（SQL 写入前必做）

每条生成的 SQL 必须通过两阶段验证才能写入文件：

```
生成 SQL → validate_insert_sql.py (列名合法性) → dry_run_sql.py (事务回滚全验证) → 写入 .sql
```

`validate_insert_sql.py` 只校验列名是否在预期清单中。`dry_run_sql.py` 在独立事务中执行每条 DML 后立即 ROLLBACK，覆盖语法、表存在、列名、数据类型四维验证。两个脚本任一个 exit ≠ 0 都必须回退修复，最多重试 3 次。

## 19. 🔴 点击按钮前关闭弹出层

日期/下拉等控件填入值后，其弹出层可能遮挡页面底部的按钮（查询、重置、预览等）。必须在点任何按钮前先点击页面空白处关闭弹出层：

```python
# 点击页面空白处，关闭日期控件等弹出层，避免遮挡按钮
await page.mouse.click(10, 10)
await page.wait_for_timeout(500)
```

此规则已固化到所有模板的 `_click_query_and_wait()` 和 `reset.py` 模板中。

## 20. 🔴🔴🔴 用例失败必须排查根因，禁止直接改断言

**当 pytest 用例失败时，必须按以下流程排查根因，禁止直接修改断言来"修复"问题。**

### 排查流程

**第一步：从日志提取查询条件**

```bash
grep "当前SQL" query_log.txt | grep "<业务表名>" | head -1
```

把每个条件列出来，逐条检查。

**第二步：检查预埋数据**

```sql
SELECT * FROM <业务表>
WHERE ta_code = '<ta_code>' AND prd_code = '<prd_code>';
```

把预埋数据的值列出来，与查询条件逐条对比。

**第三步：去数据库实际验证**

```sql
-- 检查关联表的实际值
SELECT branch_no, internal_branch, branch_level
FROM tbbranch
WHERE branch_no IN ('<branch_no>', '<hq_branch_no>');

SELECT prd_code, prd_manager
FROM tbdxtrustproduct
WHERE prd_code = '<prd_code>';
```

**重点**：不要假设字段值，要去数据库查实际值。

**第四步：逐步去掉条件定位问题**

```sql
-- 先只保留最基本的条件
SELECT count(*) FROM <业务表> a
WHERE a.ta_code = '<ta_code>' AND a.prd_code = '<prd_code>';

-- 逐步添加其他条件，找出哪个条件导致查不到数据
-- 加上 JOIN
-- 加上 internal_branch 过滤
-- 加上其他条件
```

### 禁止的做法

- ❌ 直接修改断言（如 `assert cnt == 1` 改为 `assert cnt >= 0`）
- ❌ 猜测问题原因并修改代码
- ❌ 跳过排查直接标记用例为 SKIP

### 正确的做法

- ✅ 按照上述流程排查根因
- ✅ 修复预埋数据或查询条件
- ✅ 重新运行用例验证
