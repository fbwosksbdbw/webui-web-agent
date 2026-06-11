# Stagehand CDP 探勘引擎使用指南

> 替代旧 `playwright-cli eval` 表单扫描的底层引擎。通过 CDP 接管已登录的 Chrome，输出稳定的 Playwright 原生 locator。

## 1. 解决了什么问题

| 旧方案（playwright-cli eval） | 新方案（Stagehand CDP） |
|---|---|
| 生成脆弱短 ID（如 `e18`） | 输出稳定的 Playwright locator 字符串 |
| 无法精准解析 HUI 组件 | DOM 级特征识别 `.h-select-multiple` / `.h-tag` |
| 依赖 snapshot 的临时编号 | `page.locator('.h-form-item').filter({hasText: '...'})` |
| 单引擎，容易遗漏 | 双引擎：DOM 扫描 + AI 语义增强（可选） |

## 2. 前置条件

### 2.1 Chrome 必须以远程调试端口启动

在 cmd 或 PowerShell 中执行：

```cmd
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

然后在 Chrome 中**手动登录** HUI 系统（用分行账号 `branch_user`），**导航到目标菜单页面**。

> 验证 CDP 是否生效：浏览器访问 `http://localhost:9222/json/version`，看到 JSON 即成功。

### 2.2 安装依赖（仅首次需要）

```cmd
cd /d "%USERPROFILE%\.claude\skills\hui-playwright-test\scripts\stagehand_explorer" && npm install
```

## 3. 使用方式

### 3.1 Python 调用（推荐，与流程集成）

```python
import sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "stagehand_explorer", "src"))
from explore_wrapper import (
    explore_page,
    is_ta_multi_select,
    has_preview_button,
    get_form_fields,
    get_buttons,
    get_multi_select_fields,
)

# 快速扫描（零 AI 开销，推荐日常使用）
fields = explore_page(cdp_url="http://localhost:9222", mode="fast")

# 判断 TA 多选/单选 → 决定 querymulti 模板
if is_ta_multi_select(fields):
    print("TA 是多选 → 使用 querymulti 模板 3a")
else:
    print("TA 是单选 → 使用 querymulti 模板 3b")

# 检查操作按钮
if has_preview_button(fields):
    print("有操作按钮 → 生成 preview 用例")

# 获取 locator 字符串用于生成用例代码
for f in get_form_fields(fields):
    print(f"// {f.label} ({f.type})")
    print(f"await {f.locator}.fill('...');")
```

### 3.2 命令行调用

```cmd
:: 快速模式（推荐日常使用）
python "%USERPROFILE%\.claude\skills\hui-playwright-test\scripts\stagehand_explorer\src\explore_wrapper.py" --mode=fast

:: 输出到文件
python "%USERPROFILE%\.claude\skills\hui-playwright-test\scripts\stagehand_explorer\src\explore_wrapper.py" --mode=fast -o explore_result.json

:: AI 增强模式（需 OPENAI_API_KEY，可选）
python "%USERPROFILE%\.claude\skills\hui-playwright-test\scripts\stagehand_explorer\src\explore_wrapper.py" --mode=deep
```

### 3.3 Node.js 直接调用

```cmd
cd /d "%USERPROFILE%\.claude\skills\hui-playwright-test\scripts\stagehand_explorer"
npx ts-node src/explore.ts --mode=fast
npx ts-node src/explore.ts --mode=fast --output=result.json
```

## 4. 输出格式

```json
[
  {
    "label": "客户标识",
    "type": "single-select",
    "locator": "page.locator('.h-form-item').filter({ hasText: '客户标识' }).locator('.h-select-input')",
    "isRequired": false,
    "conditionalRequiredBy": ["客户标识类型"]
  },
  {
    "label": "客户标识类型",
    "type": "single-select",
    "locator": "page.locator('.h-form-item').filter({ hasText: '客户标识类型' }).locator('.h-select-input')",
    "isRequired": true,
    "conditionalRequiredBy": []
  },
  {
    "label": "产品代码",
    "type": "single-select",
    "locator": "page.locator('.h-form-item').filter({ hasText: '产品代码' }).locator('.h-select-input')",
    "isRequired": false,
    "conditionalRequiredBy": []
  },
  {
    "label": "TA代码",
    "type": "multi-select",
    "locator": "page.locator('.h-form-item').filter({ hasText: 'TA代码' }).locator('.h-select-multiple')",
    "isRequired": false,
    "conditionalRequiredBy": []
  },
  {
    "label": "查询",
    "type": "button",
    "locator": "page.getByRole('button', { name: '查询' })",
    "isRequired": false,
    "conditionalRequiredBy": []
  }
]
```

## 5. HUI 组件类型判定规则

Stagehand 引擎内部自动检测 HUI DOM 特征：

```
.h-select-multiple 存在              → multi-select
.h-select 存在 + .h-tag 存在        → multi-select
.h-select / .h-cascader / .h-tree-select  → single-select
.h-date-picker / .h-range-picker          → single-select
.h-time-picker                            → single-select
.h-checkbox-group                         → multi-select
.h-radio-group                            → single-select
input:disabled + button.choose-btn        → tree-select-modal
input:not(.h-select-input) / textarea     → input
```

### tree-select-modal（树选择弹窗，如交易机构）

特征：form-item 内input被 `disabled` 且旁边有 `button.choose-btn`（"选择"按钮）。

交互流程：
1. 点击"选择"按钮 → 弹窗（`.h-modal-wrap` 内嵌 `.h-tree` 或 table）
2. 弹窗含搜索框（`input[placeholder*="查询"]`）+ 树节点（`.h-tree-title`）或表格行
3. 点击匹配行 → 点击"确定"关闭弹窗
4. 选中值写入 disabled input（格式如 `0011:xx银行上海分行`）

**填充规则**：`fill_field()` 自动检测此模式，委托给 `_select_tree_modal()`：
- preset 值 → 在弹窗内搜索匹配 → 点击 → 确定
- 哨兵值（`_`、`__FIRST_OPTION__`、空）→ 直接选第一个节点/行
- preset 值未匹配到 → 兜底选第一行

**注意**：树选择弹窗不经过 `_resolve_fill_value()` 语义生成（避免"机构"→"000"等错误值），原始 value 直接传入弹窗匹配。

## 5a. 可见性过滤

探勘引擎只输出**当前可见**的表单字段。过滤规则：

- 使用 `getBoundingClientRect()` 检测，`width=0 && height=0` 视为不可见
- 不依赖 `display:none` 或 `visibility:hidden`（HUI 表单中这两个属性不可靠）
- 条件隐藏的字段（如通过展开/收起控制的区域）在收起时不会被扫出

## 5b. 必填检测机制

必填状态从 **Vue 组件内部读取**，而非 CSS class：

```
el-form.__vue__.fields[i].isRequired  → true/false
```

`isRequired` 是 Vue 响应式数据，反映组件的实际必填状态（包含动态规则）。CSS class `.h-form-item-required` 和 label 中的 `*` 作为回退（Vue 实例读取失败时使用）。

## 5c. 关联必填探测

当字段 A 填值后字段 B 才变必填时，探勘引擎自动探测此关系：

- 为每个 `isRequired=true` 的字段临时设置占位值
- `$nextTick` 后检查其他字段的 `isRequired` 是否变为 `true`
- 立即恢复原值（无副作用）
- 探测结果写入 `conditionalRequiredBy` 字段

安全措施：
- 检查触发字段是否有 watcher → 有则跳过该字段的探测
- 全程 try/catch 包裹，探测失败不影响整体扫描

## 6. Python 辅助函数速查

| 函数 | 说明 |
|------|------|
| `explore_page(cdp_url, mode)` | 执行探勘，返回 `List[ExploreField]` |
| `get_form_fields(fields)` | 过滤出表单字段（不含按钮） |
| `get_buttons(fields)` | 过滤出操作按钮 |
| `get_multi_select_fields(fields)` | 过滤出多选下拉框 |
| `get_single_select_fields(fields)` | 过滤出单选下拉框 |
| `get_input_fields(fields)` | 过滤出文本输入框 |
| `is_ta_multi_select(fields)` | 判断 TA 代码是否为多选（决定 querymulti 策略） |
| `has_query_button(fields)` | 是否有查询按钮 |
| `has_reset_button(fields)` | 是否有重置按钮 |
| `has_preview_button(fields)` | 是否有预览/详情按钮（决定是否生成 preview 用例） |

## 7. 设计约束实现

| 约束 | 实现方式 |
|---|---|
| ① CDP 接管已登录 Chrome | CDP `connectOverCDP(wsEndpoint)` 接管已登录 Chrome，`finally` 中只 `disconnect()` 不 `close()` |
| ② 以扫描为主，交互为辅 | 引擎 A（fast 模式）通过 `page.evaluate()` 纯 DOM 扫描，零 AI 开销；引擎 B（deep 模式）使用 Stagehand `extract()` AI 语义增强，可能涉及有限的页面交互 |
| ③ 强制识别 HUI 组件 | 检测 `.h-select-multiple` → multi-select，`.h-tag` + `.h-select` → multi-select |
| ④ 只吐原生 Locator | 所有 locator 均为 Playwright 原生字符串，严禁 XPath/短 ID |

> **注意**：`cdp_executor.py`（步骤 1b 使用）负责表单交互（填写、点击、导航），Stagehand 引擎（步骤 1a 使用）主要负责扫描表单结构。两者职责分离。

## 8. 与现有流程的关系

```
步骤 1a（本文件）: Stagehand 表单扫描 → explore_result.json
     ↓
步骤 1b: cdp_executor.py 表单交互 → SSH 日志抓取
     ↓
步骤 2: sql_extractor 分析日志 → 预埋 SQL
     ↓
步骤 3: 根据 explore_result.json 中的 type/locator 生成用例代码
```

**Stagehand 负责"看"（扫描表单结构），cdp_executor.py 负责"做"（填写表单、触发查询、抓日志）。**

## 9. 故障排除

### Stagehand 未安装
```
Error: Cannot find module '@browserbasehq/stagehand'
```
解决：
```cmd
cd /d "%USERPROFILE%\.claude\skills\hui-playwright-test\scripts\stagehand_explorer" && npm install
```

### CDP 连接失败
```
Error: 连接超时
```
解决：
1. 确认 Chrome 已启动且带 `--remote-debugging-port=9222`
2. 浏览器访问 `http://localhost:9222/json/version`，确认返回 JSON
3. 确认 Chrome 中有已打开的页面（非 about:blank）

### 扫描结果为空
解决：
1. 确认 Chrome 中已登录且停留在目标菜单页面
2. 确认页面已完成加载（无 loading 状态）
3. 尝试 `--mode=deep` 使用 AI 增强扫描

---

## 10. 表单交互

表单交互由 `cdp_executor.py` 完成，步骤 1b 通过动作文件 JSON 驱动填表和查询。详见 `steps/step-1b-log-capture.md`。
