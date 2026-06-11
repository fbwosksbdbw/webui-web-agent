# Step 1b — 表单交互 + 日志抓取

## Precondition

编排器已确认步骤 0a~1a 完成。

## Actions

`{case_dir}` = `{paths.project_dir}/{paths.test_case_dir}/{menu.level2}/{menu.name}`
`{config_json}` = `{case_dir}/config.json`

查阅 `gates/gate-1b.md` 判定：是否存在分行过滤列、branch_source 、branch_filter_column。

### 日志定位机制

`cdp_executor.py` 在每次查询前通过 SSH 执行 `wc -l` 记录日志当前行号（`mark_log_position`），查询后用 `tail -n +{标记行}` 只取本次查询产生的新增日志。每份日志精确对应一次查询。

### 查询策略

根据 `gate_decisions.customer_route` 决定查询方式：

#### 单路查询（`customer_route = true` 或 `customer_route = false`）

先点「重置」，填入所有 value 非空字段，点「查询」→ 增量抓日志 → `query_log.txt`。

每次查询前都必须点「重置」（HUI 可能有前端默认值如日期 20191202）。

#### 双路查询（`customer_route = "both"`）

当 `has_customer_fields=true` 且 `customer_fields_required=false` 时，执行两次查询：

**第一次（空查）**：先点「重置」，填入所有 value 非空字段，**但跳过客户标识类型和客户标识字段**，点「查询」→ 增量抓日志 → `query_log_empty.txt`。

**第二次（带参查）**：先点「重置」，填入所有 value 非空字段（**包含**客户标识类型和客户标识），点「查询」→ 增量抓日志 → `query_log_param.txt`。

> 两份日志分别保存，供 step-2a 的 `analyze_dual_route_for_preset()` 合并分析。

### 动作文件组装规则

`cdp_executor.py` 通过 `-a` 参数接收动作指令 JSON。

**🔴 action 类型推导（必须按以下顺序）：**

1. **以 `explore_result.json` 的 `type` 字段为准**（不是 form_fields.all 的 type）
2. **先应用日期修正规则**：label 含"日期"且 type 为 `single-select` → 视为 `input`
3. 再按 type 映射 action：

| explore_result.json type | action | value 格式 |
|:---:|:---:|:---:|
| `input` | `"fill"` | `"value": "字符串"` |
| `single-select` | `"select"` | `"value": "选项文本"` |
| `tree-select` | `"select"` | `"value": "选项文本或__FIRST_OPTION__"` |
| `multi-select` | `"multi_select"` | `"values": ["值1", "值2"]`（数组） |

🔴 **multi_select 动作必须用 `values`（数组），不是 `value`（字符串）。** cdp_executor 的 `select_multi` 方法读取 `act["values"]`，写错 key 会报 `KeyError: 'values'`。

**动作文件**（先重置，再从 form_fields.all 取所有 value 非空字段，type 从 explore_result.json 查）：
```json
[
  {"action": "click", "label": "重置"},
  {"action": "select", "label": "客户标识类型", "value": "0:入账账号"},
  {"action": "fill", "label": "客户标识", "value": "A3060210020001"},
  {"action": "select", "label": "TA代码", "value": "JS:嘉实基金"},
  {"action": "select", "label": "产品代码", "value": "11:AI自动化测试产品"},
  {"action": "fill", "label": "开始日期", "value": "YYYY-01-01"},
  {"action": "fill", "label": "结束日期", "value": "YYYY-12-31"},
  {"action": "select", "label": "交易机构", "value": "__FIRST_OPTION__"},
  {"action": "fill", "label": "客户经理名称", "value": "自动化测试0603"},
  {"action": "click", "label": "查询"}
]
```

**多选字段示例**（当 explore_result.json type=`multi-select` 时）：
```json
  {"action": "multi_select", "label": "TA代码", "values": ["JS", "HT"]}
```

> TA代码 在不同菜单可能是 single-select 或 multi-select，取决于探勘结果的 type 字段。action 类型必须与探勘结果一致。

所有字段均参与表单填充（value 由 step-1a 的 match_presets 提供）。

### 双路动作文件组装（`customer_route = "both"` 时）

需要组装两个动作文件：

**`actions_query_empty.json`**（空查 — 跳过客户标识字段）：
```json
[
  {"action": "click", "label": "重置"},
  {"action": "select", "label": "TA代码", "value": "JS:嘉实基金"},
  {"action": "fill", "label": "开始日期", "value": "YYYY-01-01"},
  {"action": "fill", "label": "结束日期", "value": "YYYY-12-31"},
  {"action": "click", "label": "查询"}
]
```

**`actions_query_param.json`**（带参查 — 包含客户标识字段）：
```json
[
  {"action": "click", "label": "重置"},
  {"action": "select", "label": "客户标识类型", "value": "0:入账账号"},
  {"action": "fill", "label": "客户标识", "value": "A3060210020001"},
  {"action": "select", "label": "TA代码", "value": "JS:嘉实基金"},
  {"action": "fill", "label": "开始日期", "value": "YYYY-01-01"},
  {"action": "fill", "label": "结束日期", "value": "YYYY-12-31"},
  {"action": "click", "label": "查询"}
]
```

组装规则：从 `form_fields.all` 取所有 value 非空字段。空查时排除 `field_groups` 中标记为 `conditional` 的字段组（客户标识类型、客户标识）。

### 执行命令

从 config.json 提取 SSH 配置和日志路径：
- SSH 配置：`env.app_server_host`, `env.app_server_port`, `env.app_server_user`, `env.app_server_password`
- 日志路径：`computed.log_path_full`（已由 `config_reader.py init` 根据 `computed.module` 自动拼接 `log_path_{module} + log_file`，禁止自行拼接）

#### 单路查询执行

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/cdp_executor.py" \
  --config "{config_json}" \
  -e "{explore_result_json}" \
  -a actions_query.json \
  --write-back "{case_plan_json}" \
  --ssh-log {computed.log_path_full} --ssh-host {env.app_server_host} --ssh-port {env.app_server_port} \
  --ssh-user {env.app_server_user} --ssh-pass {env.app_server_password} \
  --log-grep "(Actual SQL|当前SQL)" \
  --save-log "{case_dir}/query_log.txt"
```

> `--save-log` 将捕获的 SQL 日志自动保存到用例目录，供 step 2a 直接分析，无需重查。

#### 双路查询执行（`customer_route = "both"` 时）

依次执行两次 cdp_executor，分别保存日志：

```bash
# 第一次：空查（不填客户标识字段，--skip-conditional-groups 防止 _ensure_field_group 自动补填）
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/cdp_executor.py" \
  --config "{config_json}" \
  -e "{explore_result_json}" \
  -a actions_query_empty.json \
  --skip-conditional-groups \
  --write-back "{case_plan_json}" \
  --ssh-log {computed.log_path_full} --ssh-host {env.app_server_host} --ssh-port {env.app_server_port} \
  --ssh-user {env.app_server_user} --ssh-pass {env.app_server_password} \
  --log-grep "(Actual SQL|当前SQL)" \
  --save-log "{case_dir}/query_log_empty.txt"

# 第二次：带参查（填入客户标识字段）
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/cdp_executor.py" \
  --config "{config_json}" \
  -e "{explore_result_json}" \
  -a actions_query_param.json \
  --write-back "{case_plan_json}" \
  --ssh-log {computed.log_path_full} --ssh-host {env.app_server_host} --ssh-port {env.app_server_port} \
  --ssh-user {env.app_server_user} --ssh-pass {env.app_server_password} \
  --log-grep "(Actual SQL|当前SQL)" \
  --save-log "{case_dir}/query_log_param.txt"
```

## 🔴 Output Format（上下文卫生）

1. **SQL 日志内容保留**：gate-1b 需要语义阅读 SQL，允许在上下文中保留 query_log.txt 内容
2. **允许输出**：gate 决策结论、branch_source、case_count、日志行数
3. **禁止输出**：cdp_executor 操作输出原文

## Checklist

- [ ] 已通过 CDP 执行实际查询操作
- [ ] 已 SSH 抓取后端 SQL 日志
- [ ] 已阅读 SQL 日志原文，判断是否存在分行级数据过滤（禁止关键字 grep，必须读 SQL 语义）
- [ ] 已确认 TA 多选/单选、产品代码格式、操作按钮文本
- [ ] （`customer_route="both"` 时）已执行两次查询，分别保存 `query_log_empty.txt` 和 `query_log_param.txt`

## Exit

更新 case_plan.json 的 `gate_decisions`、`filter_selections`、`preset_strategy`。

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" done
```
