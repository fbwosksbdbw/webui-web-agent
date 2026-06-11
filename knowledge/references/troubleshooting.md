# 故障排除指南

## 1. 测试用例执行耗时

测试用例的执行时间取决于 fixture 的 scope 配置。如果使用 `scope="session"` 或 `scope="class"`，浏览器和登录状态可在多个用例间复用。如果使用 `scope="function"`，每个用例会重新创建浏览器并登录。

- **规则**：不要浪费时间排查"为什么这么慢"，先确认 fixture scope 配置。
- **规则**：调试阶段建议只跑单个用例验证，确认 querysingle 通过后再逐个跑其他的。

## 2. 浏览器资源管理

- 使用 CDP 接管已登录 Chrome 时（`cdp_executor.py`），只 `disconnect()` 不 `close()`，浏览器保持运行。
- 如果浏览器状态异常（页面卡死、弹窗未关闭），可使用 `cdp_executor.py --health-check` 检查，`--recover` 恢复。
- 连续失败时考虑重启 Chrome：`launch_chrome.py --auto`。

## 3. Playwright login 间歇性超时

- 登录超时（`Page.goto: Timeout 30000ms exceeded`）通常是间歇性的，可能原因包括：服务器临时负载高、网络抖动、CDP 连接不稳定。
- **规则**：遇到超时直接重试即可，不要反复排查。连续 3 次失败再考虑其他原因（检查 Chrome 是否存活、CDP 端口是否可连通）。

## 4. SSH 连接

连接参数从 `config.json` 的 `env` 字段读取（详见 [config-guide.md](config-guide.md)）。
禁止使用 `sshpass` 命令行或系统 `ssh` 命令，必须用 `paramiko`。

## 5. 常见 pytest 错误模式

| 错误 | 原因 | 对策 |
|------|------|------|
| `Page.goto: Timeout 30000ms exceeded` | 服务器临时不可用或 CDP 连接不稳定 | 直接重试，连续 3 次失败检查 Chrome 是否存活 |
| `strict mode violation: locator resolved to N elements` | 选择器太宽泛 | 加 `.first` 或用更精确的选择器 |
| `表格未找到预埋数据` | 分片库插入位置错误 | SSH 查日志确认：分片表搜 `Actual SQL: ds0/ds1`，非分片表搜 `当前SQL:` |
| `详情面板未显示xxx` | 字段名硬编码错误 | 改用通用验证（检查 `input[readonly]` 有值即可） |
| `text=详情` 或 `text=预览` 找不到元素 | 操作按钮文本因页面而异 | 用 `.h-omc-table-btn:visible` + fallback 遍历 |
| dry-run `[1054] Unknown column` | 编造了不存在的列名 | 回查 DESCRIBE/cache 确认列名，每列必须来自缓存 |
| dry-run `[1062] Duplicate entry` | 多行 INSERT 的 UNIQUE 列值重复 | 对 UNIQUE 索引列的第二行做递增（日期+1 或尾号+1） |
| dry-run `[1366] Incorrect integer value` | int 列误填空格字符串 | int/bigint 空值用 `0`，不能用 `' '` |

## 6. 查询无数据排查流程

当测试用例查询返回 0 条数据时，按以下步骤排查：

### 6.1 核心思路：日志 → 预埋 → 数据库 三方对比

**不要猜测问题，要根据实际数据对比。**

### 6.2 第一步：从日志提取查询条件

从 `query_log.txt` 中找到业务 SQL，提取所有 WHERE 条件：

```bash
grep "当前SQL" query_log.txt | grep "<业务表名>" | head -1
```

把每个条件列出来，逐条检查。

### 6.3 第二步：检查预埋数据

```sql
SELECT * FROM <业务表> 
WHERE ta_code = '<ta_code>' AND prd_code = '<prd_code>';
```

把预埋数据的值列出来，与查询条件逐条对比。

### 6.4 第三步：去数据库实际验证

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

### 6.5 第四步：逐步去掉条件定位问题

```sql
-- 先只保留最基本的条件
SELECT count(*) FROM <业务表> a
WHERE a.ta_code = '<ta_code>' AND a.prd_code = '<prd_code>';

-- 逐步添加其他条件，找出哪个条件导致查不到数据
-- 加上 JOIN
-- 加上 internal_branch 过滤
-- 加上其他条件
```

### 6.6 常见问题模式

- **关联表字段为空**：如 `tbdxtrustproduct.prd_manager` 为空，导致 JOIN 失败
- **branch_no/branch_level 不匹配**：预埋数据的 branch_no 与 tbbranch 表中的值不一致
- **查询条件与预埋数据不一致**：如查询条件用分行的 internal_branch，但预埋数据用了总行的
