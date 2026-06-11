# 会话上下文自动保存/恢复 Skill

> 在 Claude Code 上下文压缩（compact）后自动恢复关键上下文，防止 AI 跳步。

## 问题背景

HUI 自动化测试每个菜单需要执行 11 步流程，单菜单消耗约 30k tokens。批量处理 3-5 个菜单时，Claude Code 的上下文窗口会触发 compaction（压缩），导致：
- 丢失当前步骤状态
- 丢失 case_plan.json 中的关键决策
- AI 跳步或重复执行

## 解决方案

通过 Claude Code Hooks 实现：
1. **SessionEnd hook** — 会话结束时自动保存上下文到 `.claude/session_context.json`
2. **SessionStart hook (compact matcher)** — compact 后自动恢复上下文，注入 Claude 上下文

## 安装配置

### 第 1 步：添加 Hook 配置

在 `~/.claude/settings.json` 中添加 `hooks` 配置：

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$HOME/.claude/skills/hui-playwright-test/scripts/hooks/save_session_context.py\""
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "compact",
        "hooks": [
          {
            "type": "command",
            "command": "python \"$HOME/.claude/skills/hui-playwright-test/scripts/hooks/restore_session_context.py\""
          }
        ]
      }
    ]
  }
}
```

**Windows 用户**使用 PowerShell 路径：
```json
{
  "hooks": {
    "SessionEnd": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python \"%USERPROFILE%\\.claude\\skills\\hui-playwright-test\\scripts\\hooks\\save_session_context.py\""
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "compact",
        "hooks": [
          {
            "type": "command",
            "command": "python \"%USERPROFILE%\\.claude\\skills\\hui-playwright-test\\scripts\\hooks\\restore_session_context.py\""
          }
        ]
      }
    ]
  }
}
```

### 第 2 步：验证配置

运行 `/hooks` 命令查看已注册的 hooks，确认 SessionEnd 和 SessionStart (compact) 出现在列表中。

### 第 3 步：手动测试（可选）

```bash
# 测试保存脚本
cd ~/.claude/skills/hui-playwright-test
python scripts/hooks/save_session_context.py < /dev/null

# 测试恢复脚本
python scripts/hooks/restore_session_context.py

# 查看保存的上下文
cat .claude/session_context.json
```

## 工作原理

### 保存时机（SessionEnd）

当以下情况触发时自动保存：
- 正常退出 Claude Code
- 执行 `/clear` 命令
- 会话超时结束

保存的内容：
```json
{
  "saved_at": "2026-06-03T10:30:00",
  "session_type": "hui-playwright-test",
  "menu_name": "基金分红查询",
  "current_step": "2a",
  "steps_completed": ["0a", "0b", "1a", "1b"],
  "checklists": {},
  "case_plan": {
    "query_strategy": "full_condition",
    "has_required_fields": true,
    "customer_route": false,
    "case_count": 7,
    "case_types": ["branch", "headquarter", "queryall", "querysingle", "querymulti", "reset", "preview"],
    "tables": ["t_fund_dividend"],
    "expected_counts": {"branch": 2, "headquarter": 5}
  }
}
```

### 恢复时机（SessionStart + compact matcher）

当 Claude Code 执行 compact（上下文压缩）后自动触发：
1. 读取 `.claude/session_context.json`
2. 读取当前的 `step_state.json` 和 `case_plan.json`
3. 生成精简摘要，输出到 stdout
4. stdout 内容自动注入 Claude 的上下文

### 注入的上下文示例

```
============================================================
[SESSION CONTEXT RESTORED] 上下文压缩后自动恢复
============================================================

当前菜单: 基金分红查询
当前步骤: step-2a
已完成步骤: 0a, 0b, 1a, 1b
下一步: step-2a

--- case_plan 关键决策 ---
查询策略: full_condition
有必填字段: True
用例数: 7
用例类型: branch, headquarter, queryall, querysingle, querymulti, reset, preview
涉及表: t_fund_dividend
预期条数: branch:2, headquarter:5

--- 关键提醒 ---
1. 继续从当前步骤执行，不要跳步
2. 读取 step_state.json 确认实际状态
3. 读取 case_plan.json 确认决策数据
4. 遵循 skill.md 中的流程定义

============================================================
```

## 文件说明

| 文件 | 用途 |
|------|------|
| `scripts/hooks/save_session_context.py` | SessionEnd hook：保存上下文 |
| `scripts/hooks/restore_session_context.py` | SessionStart hook：恢复上下文 |
| `.claude/session_context.json` | 持久化存储（自动生成） |

## 注意事项

1. **首次使用**：需要先执行一次完整的菜单测试流程，才会生成 session_context.json
2. **上下文大小**：恢复的摘要控制在 500 tokens 以内，不会显著占用上下文窗口
3. **多菜单批量**：每个菜单测试完成后会自动更新 session_context.json
4. **手动清除**：如需重置，删除 `.claude/session_context.json` 即可
5. **Windows 编码**：脚本已处理 Windows 终端的 GBK 编码问题，自动切换为 UTF-8

## 实际测试验证

以下是在当前环境中的测试结果：

```
$ python scripts/hooks/restore_session_context.py

============================================================
[SESSION CONTEXT RESTORED] 上下文压缩后自动恢复
============================================================

当前菜单: 基金代销账户类请求流水查询
当前步骤: step-1b
已完成步骤: 0a, 0b, 1a, 1b
下一步: step-2a

--- 关键提醒 ---
1. 继续从当前步骤执行，不要跳步
2. 读取 step_state.json 确认实际状态
3. 读取 case_plan.json 确认决策数据
4. 遵循 skill.md 中的流程定义

============================================================
```

脚本成功读取了 `step_state.json` 中的实际状态，并生成了精简摘要。

## 与其他 Skill 的配合

本 skill 与 `hui-playwright-test` 主 skill 配合使用：
- 主 skill 负责测试流程编排
- 本 skill 负责在上下文压缩后恢复状态
- 两者通过 `step_state.json` 和 `case_plan.json` 共享状态
