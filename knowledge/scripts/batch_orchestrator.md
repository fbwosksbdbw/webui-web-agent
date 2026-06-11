# 批量菜单执行编排指南

## 架构

```
主代理（当前会话）
  │
  ├── Agent 1: 菜单A ──→ 独立上下文 ──→ 写入 result_A.json
  ├── Agent 2: 菜单B ──→ 独立上下文 ──→ 写入 result_B.json
  └── Agent 3: 菜单C ──→ 独立上下文 ──→ 写入 result_C.json
  │
  └── 读取所有 result_*.json，汇总报告
```

## 通信机制

### 任务文件（主代理 → 子代理）
路径: `{SKILL_DIR}/tasks/{task_id}.json`

```json
{
  "task_id": "task_1234567890_1234",
  "menu_name": "信托代销业务保有量统计报表-按产品",
  "config_path": "...",
  "case_plan_path": "...",
  "status": "pending"
}
```

### 结果文件（子代理 → 主代理）
路径: `{SKILL_DIR}/tasks/{task_id}_result.json`

```json
{
  "task_id": "task_1234567890_1234",
  "menu_name": "信托代销业务保有量统计报表-按产品",
  "status": "success",
  "completed_steps": ["0a", "0b", "1a", "1b", "2a", "2b", "2c", "2d", "3", "4", "5"],
  "error_message": null,
  "started_at": "2026-06-04T19:00:00",
  "completed_at": "2026-06-04T19:30:00"
}
```

## 执行流程

### 1. 主代理：列出未完成菜单

```bash
python "{SKILL_DIR}/scripts/config_reader.py" menus
```

### 2. 主代理：为每个菜单创建任务

```bash
python "{SKILL_DIR}/scripts/batch_runner.py" create_task "菜单名"
```

### 3. 主代理：启动子代理执行任务

对每个任务，使用 Agent 工具：

```
Agent(
  prompt="执行菜单任务: {task_id}",
  subagent_type="claude"
)
```

子代理的 prompt 内容：

```
你是菜单执行代理。请执行以下任务：

1. 读取任务文件: {SKILL_DIR}/tasks/{task_id}.json
2. 执行菜单的完整流程（step 0a → step 5）
3. 将执行结果写入: {SKILL_DIR}/tasks/{task_id}_result.json

执行命令:
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" init "{菜单名}"
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" current
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/orchestrator.py" done

完成后写入结果文件。
```

### 4. 主代理：等待所有子代理完成

子代理完成后会自动通知主代理。

### 5. 主代理：汇总结果

```bash
python "{SKILL_DIR}/scripts/batch_runner.py" summary
```

## 注意事项

1. **浏览器复用**：第一个菜单启动浏览器后，后续菜单复用同一个浏览器
2. **并发控制**：由于浏览器是共享的，菜单需要串行执行，不能并行
3. **状态恢复**：子代理失败后，可以通过 orchestrator.py recover 恢复状态
4. **上下文隔离**：每个子代理有独立的上下文，不会相互干扰

## 简化方案

由于浏览器是共享的，实际上菜单需要串行执行。简化方案：

1. 主代理创建所有任务
2. 主代理依次启动子代理（每次只启动一个）
3. 子代理执行完成后，主代理启动下一个
4. 所有子代理完成后，主代理汇总结果

这样既解决了上下文问题，又避免了浏览器冲突。
