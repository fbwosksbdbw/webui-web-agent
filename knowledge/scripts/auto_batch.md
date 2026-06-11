# 自动批量执行指南

## 触发条件

用户说"帮我做用例"、"生成测试"、"做配置里的菜单"等，且**没有**给出具体菜单名。

## 执行流程

### 1. 列出未完成菜单

```bash
MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python "{SKILL_DIR}/scripts/config_reader.py" menus
```

### 2. 对每个菜单启动子代理

```python
# 对每个菜单
for menu_name in menus:
    # 创建任务
    task_id = run(f'python "{SKILL_DIR}/scripts/batch_runner.py" create_task "{menu_name}"')

    # 启动子代理执行
    Agent(
        prompt=f"""
你是菜单执行代理。请执行以下任务：

1. 读取任务文件: {SKILL_DIR}/tasks/{task_id}.json
2. 执行菜单的完整流程（step 0a → step 5）
3. 将执行结果写入: {SKILL_DIR}/tasks/{task_id}_result.json

执行流程：
1. 先读取任务文件，获取 menu_name
2. 执行 orchestrator.py init "{{menu_name}}"
3. 循环执行 orchestrator.py current → 读取 step 文件 → 执行 Actions → orchestrator.py done
4. 所有步骤完成后，写入结果文件

重要：
- 使用 MSYS_NO_PATHCONV=1 PYTHONIOENCODING=utf-8 python 命令
- SKILL_DIR = {SKILL_DIR}
- 浏览器已启动，直接复用（第一个菜单启动浏览器，后续菜单跳过）
- 如果某个步骤失败，记录错误并继续

结果文件格式：
{{
  "task_id": "{task_id}",
  "menu_name": "菜单名",
  "status": "success" 或 "failed",
  "completed_steps": ["0a", "0b", ...],
  "error_message": null 或 "错误信息",
  "started_at": "开始时间",
  "completed_at": "完成时间"
}}
""",
        run_in_background=True
    )
```

### 3. 等待所有子代理完成

子代理完成后会自动通知主代理。

### 4. 汇总结果

```bash
python "{SKILL_DIR}/scripts/batch_runner.py" summary
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

## 注意事项

1. **浏览器复用**：第一个菜单启动浏览器后，后续菜单复用同一个浏览器
2. **🔴🔴🔴 串行执行**：浏览器是共享的，子代理必须串行执行，不能并行！等一个子代理完成后，再启动下一个。如果并行启动多个子代理，会导致浏览器冲突，数据错乱
3. **上下文隔离**：每个子代理有独立的上下文，不会累积
4. **失败处理**：某个菜单失败不影响其他菜单的执行

## 简化版（串行执行）

由于浏览器是共享的，实际上需要串行执行。简化流程：

```python
# 对每个菜单，等待完成后再执行下一个
for menu_name in menus:
    task_id = create_task(menu_name)
    result = Agent(prompt=f"执行任务 {task_id}")  # 等待完成
    print(f"{menu_name}: {result['status']}")
```

这样可以避免浏览器冲突，同时保持上下文隔离。
