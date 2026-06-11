---
name: hui-playwright-test
description: 🔴 HUI Playwright automated test case generation. Trigger when user says any of: "帮我做用例" "帮我生成用例" "给我做用例" "做用例" "生成测试" "写HUI测试" "测菜单" "做配置里的菜单" "生成自动化用例" "做自动化用例". If no menu name provided, IMMEDIATELY run `config_reader.py menus` to list unfinished menus, then start processing from the first one. Do NOT explore the project first, do NOT ask the user which menu. If user provides a menu name, immediately run `orchestrator.py init "<菜单名>"`.
agent_created: true
---

# 🔴🔴🔴 入口指令（读到这里立即执行，不要往下读了）

**收到用户指令后，你的第一条动作必须是以下其中之一，不得先探索项目、不得先读文档、不得反问用户：**

| 用户说了什么 | 你的第一条动作 |
|---|---|
| "帮我做用例" / "生成测试" / "做配置里的菜单"（无菜单名） | `config_reader.py menus` → 从第一个菜单开始 |
| "帮我做XXX的用例"（有菜单名） | `orchestrator.py init "XXX"` |

```bash
# 批量模式入口（无菜单名时立即执行）：
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/config_reader.py" menus

# 单菜单模式入口（有菜单名时立即执行）：
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" init "{菜单名}"
```

**执行完入口命令后，按下方「执行流程」章节继续。不要在执行前阅读下方内容。**

---

# HUI + Playwright 自动化测试

## 核心规则

你是一个资深的 Playwright 自动化测试开发。**必须按顺序执行全部步骤，禁止跳过、禁止询问用户选择、禁止参考其他菜单已有用例。**

**工作流由 `orchestrator.py` 自动编排。每步执行模式：**

```
orchestrator.py current → Read 指定的 step + gate 文件 → 执行 Actions → 完成 Checklist → orchestrator.py done → 循环
```

**决策数据唯一存储：`case_plan.json`**（增量更新，每步追加字段。）

**🔴 Python 命令建议加环境变量（Windows Git Bash 兼容）：**
- `MSYS_NO_PATHCONV=1` — 防止 MSYS2 自动转换 `/path` 形式的参数为 Windows 路径（仅当参数含 `/` 开头的路径时需要）
- `PYTHONIOENCODING=utf-8` — 确保 stdout/stderr 使用 UTF-8 编码（处理中文输出时需要）
- 并非所有命令都必须加，仅在涉及文件路径参数或中文输出时添加

**禁止事项（完整列表见 references/code-rules.md）：**
- 跳过任何步骤、询问用户选择、硬编码测试账号
- 🔴 **禁止任何 `python -c "..."` 内联代码。所有操作必须通过 CLI 脚本，严禁裸调内部类**
- 参考/查看 `testcases/` 下非当前菜单的任何 `.py` 文件（唯一代码来源：`assets/templates/`）
- 使用 `nth()` 或 `getByRole("textbox")` 定位表单
- 侧边栏 click 导航（必须 URL hash 直跳）
- 全表 DESCRIBE 分析、现场逆向业务字典
- 🔴 **未点重置就查**：填入任何条件前，必须点击「重置」按钮清空默认过滤条件

---

## 🚀 执行流程

### 🔴 自动批量模式（用户未指定菜单名时必须进入此模式）

**触发条件**：用户说了"帮我做用例"、"生成测试"、"做配置里的菜单"等，但**没有**给出具体菜单名。

**🔴 你收到这类指令后，第一条回复必须是执行 `config_reader.py menus`，不得反问用户任何问题。**

#### 1. 列出未完成菜单（立即执行，不要问）

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/config_reader.py" menus
```

#### 2. 对每个菜单启动子代理执行（解决上下文过长问题）

**🔴 关键：使用子代理执行每个菜单，避免上下文累积。**

**🔴🔴🔴 串行执行约束：浏览器是共享的，子代理必须串行执行，不能并行！**

- 等一个子代理完成后，再启动下一个
- 如果并行启动多个子代理，会导致浏览器冲突，数据错乱
- 每个子代理完成后会收到通知，然后继续下一个菜单

对每个菜单，执行以下流程：

```bash
# 创建任务
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/batch_runner.py" create_task "<菜单名>"
```

然后启动子代理执行：

```
Agent(
  prompt="""
你是菜单执行代理。请执行以下任务：

1. 读取任务文件: {SKILL_DIR}/tasks/{task_id}.json
2. 执行菜单的完整流程（step 0a → step 5）
3. 将执行结果写入: {SKILL_DIR}/tasks/{task_id}_result.json

执行流程：
1. 先读取任务文件，获取 menu_name
2. 执行 orchestrator.py init "{menu_name}"
3. 循环执行 orchestrator.py current → 读取 step 文件 → 执行 Actions → orchestrator.py done
4. 所有步骤完成后，写入结果文件

重要：
- 使用 MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python 命令
- SKILL_DIR = {SKILL_DIR}
- 浏览器已启动，直接复用（第一个菜单启动浏览器，后续菜单跳过）
- 如果某个步骤失败，记录错误并继续

结果文件格式：
{
  "task_id": "{task_id}",
  "menu_name": "菜单名",
  "status": "success" 或 "failed",
  "completed_steps": ["0a", "0b", ...],
  "error_message": null 或 "错误信息",
  "started_at": "开始时间",
  "completed_at": "完成时间"
}
""",
  run_in_background=true
)
```

**浏览器复用**：第一个菜单启动 Chrome 并登录（step-0b），后续菜单复用同一个浏览器。

#### 3. 等待所有子代理完成

子代理完成后会自动通知主代理。收到通知后，读取结果文件：

```bash
cat "{SKILL_DIR}/tasks/{task_id}_result.json"
```

#### 4. 汇总结果

所有子代理完成后，执行汇总：

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/batch_runner.py" summary
```

输出格式：
```
任务总数: 6
成功: 5
失败: 1

详细结果:
  ✅ 信托代销业务保有量统计报表-按产品
  ✅ 信托代销业务保有量统计报表-按机构
  ❌ 信托中间业务收入统计报表 (错误: ...)
  ✅ 信托代销交易确认汇总-按机构
  ✅ 信托代销交易确认汇总-按管理人
  ✅ 信托代销交易确认汇总-按产品
```

#### 5. 标记完成

每个菜单的子代理完成后，自动标记为已完成：

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/config_reader.py" mark_done "<菜单名>"
```

#### 6. 异常处理

某菜单失败 → 记录菜单名 + 失败步骤 → 继续下一个 → 全部完成后输出汇总报告（✅ N 个已生成 / ⚠️ M 个失败）。

#### 7. 🔴 上下文恢复（Auto-compaction 后的自救）

**所有状态都在文件里**（case_plan.json、step_state.json、config.json、tasks/*.json），auto-compaction 不会丢失任何数据。

如果发现自己丢失了上下文（不记得刚才在做什么）：

```bash
# 查看任务状态
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/batch_runner.py" status

# 或者恢复单个菜单
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" recover
```

然后按 `orchestrator.py current` 继续。

### 单菜单模式（用户明确指定了菜单名时）

当用户明确说出菜单名（如"帮我做基金分红查询的用例"），直接对该菜单执行全流程：

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" init "{菜单名}"
```

`init` 会自动输出第一步的完整上下文。

### 每步循环

**1. 查看当前步骤：**
```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" current
```
输出会指明：当前步骤 ID、需读取的 step 文件和 gate 文件。

**2. Read 指定的文件**，按其中的 Actions 执行，完成所有 Checklist 项。

**3. 标记完成：**
```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" done
```
自动标记当前步骤完成，输出下一步提示。

**4. 重复**，直到 `current` 输出 `[DONE] 所有步骤已完成`。

### 辅助命令

```bash
# 查看全流程进度
python "{SKILL_DIR}/scripts/orchestrator.py" status

# 重做当前步骤（重置后重新 current）
python "{SKILL_DIR}/scripts/orchestrator.py" retry

# 重做指定步骤（重置该步及之后所有步骤）
python "{SKILL_DIR}/scripts/orchestrator.py" retry 2c
```

---

## 步骤索引

| 步骤 | Step 文件 | Gate 文件 | 说明 |
|:---:|------|------|------|
| 0a | steps/step-0a-menu-init.md | — | 读取配置，创建 case_plan.json |
| 0b | steps/step-0b-browser-login.md | — | 浏览器登录 + CDP 导航 |
| 1a | steps/step-1a-form-scan.md | gates/gate-1a.md | Stagehand 扫描表单 |
| 1b | steps/step-1b-log-capture.md | gates/gate-1b.md | 填表触发查询 + SSH 抓日志 |
| 2a | steps/step-2a-log-analysis.md | — | 分析 SQL 日志 + 分片路由 |
| 2b | steps/step-2b-base-table.md | — | 基础表覆盖检查 |
| 2c | steps/step-2c-sql-generation.md | — | 生成业务表 INSERT |
| 2d | steps/step-2d-plan-verify.md | — | 验证 case_plan.json 完整性 |
| 3 | steps/step-3-case-generation.md | gates/gate-3.md | 逐个生成 pytest 用例 |
| 4 | steps/step-4-verify.md | — | 运行验证 + 熔断修复 |
| 5 | steps/step-5-report.md | — | 导出测试报告 |

---

## 📚 知识库

| 文件 | 内容 |
|------|------|
| [GATE.md](GATE.md) | 决策矩阵索引 |
| [references/code-rules.md](references/code-rules.md) | 16 条铁律 |
| [references/config-guide.md](references/config-guide.md) | 配置、分片、导航、预埋规则 |
| [references/case-template.md](references/case-template.md) | 模板使用说明 + 占位符映射 |
| [references/stagehand-guide.md](references/stagehand-guide.md) | Stagehand 探勘引擎 |
| [references/troubleshooting.md](references/troubleshooting.md) | 故障排除 |
| [references/report-spec.md](references/report-spec.md) | 报告格式 |
| [assets/templates/](assets/templates/) | 8 个用例模板 + case_plan.json |
