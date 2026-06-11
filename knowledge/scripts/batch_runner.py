#!/usr/bin/env python3
"""
批量菜单执行器 - 支持子代理并行执行

用法:
  # 创建任务
  python batch_runner.py create_task "菜单名"

  # 执行任务（子代理调用）
  python batch_runner.py run_task "任务ID"

  # 查询状态
  python batch_runner.py status

  # 汇总结果
  python batch_runner.py summary
"""

import os
import sys
import json
import time
from datetime import datetime

# 确保 scripts/ 目录在 sys.path 中
SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(SKILL_DIR, 'scripts')
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

TASKS_DIR = os.path.join(SKILL_DIR, 'tasks')


def ensure_tasks_dir():
    """确保任务目录存在"""
    os.makedirs(TASKS_DIR, exist_ok=True)


def create_task(menu_name: str) -> str:
    """创建任务文件，返回任务ID"""
    ensure_tasks_dir()

    # 生成任务ID
    task_id = f"task_{int(time.time())}_{hash(menu_name) % 10000}"

    # 获取配置路径
    from config_reader import ConfigReader
    config = ConfigReader()
    result = config.init_menu(menu_name)
    paths = result['paths']

    task = {
        "task_id": task_id,
        "menu_name": menu_name,
        "created_at": datetime.now().isoformat(),
        "status": "pending",  # pending, running, success, failed
        "config_path": os.path.join(
            paths['project_dir'],
            paths['test_case_dir'],
            result['menu'].get('导航二级菜单', '查询管理'),
            menu_name,
            'config.json'
        ),
        "case_plan_path": os.path.join(
            paths['project_dir'],
            paths['test_case_dir'],
            result['menu'].get('导航二级菜单', '查询管理'),
            menu_name,
            'case_plan.json'
        ),
        "completed_steps": [],
        "error_message": None,
        "result_path": None
    }

    # 写入任务文件
    task_path = os.path.join(TASKS_DIR, f"{task_id}.json")
    with open(task_path, 'w', encoding='utf-8') as f:
        json.dump(task, f, ensure_ascii=False, indent=2)

    print(f"[OK] 任务已创建: {task_id}")
    print(f"  菜单: {menu_name}")
    print(f"  任务文件: {task_path}")

    return task_id


def run_task(task_id: str) -> dict:
    """执行任务（子代理调用）"""
    ensure_tasks_dir()

    # 读取任务文件
    task_path = os.path.join(TASKS_DIR, f"{task_id}.json")
    if not os.path.exists(task_path):
        return {"error": f"任务文件不存在: {task_path}"}

    with open(task_path, 'r', encoding='utf-8') as f:
        task = json.load(f)

    menu_name = task['menu_name']

    # 更新状态为 running
    task['status'] = 'running'
    task['started_at'] = datetime.now().isoformat()
    save_task(task)

    try:
        # 导入 orchestrator
        from orchestrator import main as orchestrator_main

        # 执行 init
        sys.argv = ['orchestrator.py', 'init', menu_name]
        orchestrator_main()

        # 循环执行每个步骤
        steps = ['0a', '0b', '1a', '1b', '2a', '2b', '2c', '2d', '3', '4', '5']
        for step in steps:
            # 检查当前步骤
            sys.argv = ['orchestrator.py', 'current']
            # 这里需要捕获输出来判断当前步骤
            # 简化实现：直接执行 done 来推进步骤
            sys.argv = ['orchestrator.py', 'done']
            try:
                orchestrator_main()
                task['completed_steps'].append(step)
                save_task(task)
            except Exception as e:
                # 步骤失败，记录错误
                task['error_message'] = f"步骤 {step} 失败: {str(e)}"
                task['status'] = 'failed'
                save_task(task)
                return task

        # 所有步骤完成
        task['status'] = 'success'
        task['completed_at'] = datetime.now().isoformat()
        save_task(task)

    except Exception as e:
        task['status'] = 'failed'
        task['error_message'] = str(e)
        save_task(task)

    return task


def save_task(task: dict):
    """保存任务状态"""
    task_path = os.path.join(TASKS_DIR, f"{task['task_id']}.json")
    with open(task_path, 'w', encoding='utf-8') as f:
        json.dump(task, f, ensure_ascii=False, indent=2)


def get_status() -> list:
    """获取所有任务状态"""
    ensure_tasks_dir()

    tasks = []
    for filename in os.listdir(TASKS_DIR):
        if filename.endswith('.json'):
            task_path = os.path.join(TASKS_DIR, filename)
            with open(task_path, 'r', encoding='utf-8') as f:
                task = json.load(f)
                tasks.append(task)

    return tasks


def get_summary() -> dict:
    """获取执行摘要"""
    tasks = get_status()

    summary = {
        "total": len(tasks),
        "pending": sum(1 for t in tasks if t['status'] == 'pending'),
        "running": sum(1 for t in tasks if t['status'] == 'running'),
        "success": sum(1 for t in tasks if t['status'] == 'success'),
        "failed": sum(1 for t in tasks if t['status'] == 'failed'),
        "tasks": []
    }

    for task in tasks:
        summary['tasks'].append({
            "task_id": task['task_id'],
            "menu_name": task['menu_name'],
            "status": task['status'],
            "completed_steps": task.get('completed_steps', []),
            "error_message": task.get('error_message')
        })

    return summary


def main():
    if len(sys.argv) < 2:
        print("用法:")
        print("  python batch_runner.py create_task <菜单名>")
        print("  python batch_runner.py run_task <任务ID>")
        print("  python batch_runner.py status")
        print("  python batch_runner.py summary")
        return

    cmd = sys.argv[1]

    if cmd == 'create_task':
        if len(sys.argv) < 3:
            print("请指定菜单名")
            return
        create_task(sys.argv[2])

    elif cmd == 'run_task':
        if len(sys.argv) < 3:
            print("请指定任务ID")
            return
        result = run_task(sys.argv[2])
        print(json.dumps(result, ensure_ascii=False, indent=2))

    elif cmd == 'status':
        tasks = get_status()
        print(f"任务总数: {len(tasks)}")
        for task in tasks:
            print(f"  {task['task_id']}: {task['menu_name']} - {task['status']}")

    elif cmd == 'summary':
        summary = get_summary()
        print(json.dumps(summary, ensure_ascii=False, indent=2))

    else:
        print(f"未知命令: {cmd}")


if __name__ == '__main__':
    main()
