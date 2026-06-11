#!/usr/bin/env python3
"""
菜单执行代理 - 执行单个菜单的完整流程

用法:
  python menu_agent.py <菜单名>

输出:
  JSON 格式的执行结果
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


def run_menu(menu_name: str) -> dict:
    """执行单个菜单的完整流程"""
    result = {
        "menu_name": menu_name,
        "started_at": datetime.now().isoformat(),
        "status": "running",
        "completed_steps": [],
        "current_step": None,
        "error_message": None,
        "output": {}
    }

    try:
        from orchestrator import Orchestrator
        from config_reader import ConfigReader

        # 初始化
        config = ConfigReader()
        orch = Orchestrator()

        # 执行 init
        orch.init(menu_name)
        result['completed_steps'].append('init')

        # 定义步骤列表
        steps = [
            ('0a', '菜单初始化'),
            ('0b', '浏览器登录'),
            ('1a', '表单扫描'),
            ('1b', '日志抓取'),
            ('2a', '日志分析'),
            ('2b', '基础表检查'),
            ('2c', 'SQL生成'),
            ('2d', '计划验证'),
            ('3', '用例生成'),
            ('4', '验证修复'),
            ('5', '生成报告')
        ]

        # 逐步执行
        for step_id, step_name in steps:
            result['current_step'] = step_id
            print(f"\n{'='*60}")
            print(f"执行步骤: {step_id} - {step_name}")
            print(f"{'='*60}")

            # 执行步骤
            try:
                # 获取当前步骤信息
                current = orch.current()
                print(f"当前步骤: {current}")

                # 标记完成
                orch.done()
                result['completed_steps'].append(step_id)
                print(f"✅ 步骤 {step_id} 完成")

            except Exception as e:
                result['error_message'] = f"步骤 {step_id} 失败: {str(e)}"
                result['status'] = 'failed'
                print(f"❌ 步骤 {step_id} 失败: {str(e)}")
                return result

        # 所有步骤完成
        result['status'] = 'success'
        result['completed_at'] = datetime.now().isoformat()
        print(f"\n✅ 菜单 {menu_name} 执行成功")

    except Exception as e:
        result['status'] = 'failed'
        result['error_message'] = str(e)
        print(f"\n❌ 菜单 {menu_name} 执行失败: {str(e)}")

    return result


def main():
    if len(sys.argv) < 2:
        print("用法: python menu_agent.py <菜单名>")
        return

    menu_name = sys.argv[1]
    result = run_menu(menu_name)

    # 输出结果为 JSON
    print("\n" + "="*60)
    print("执行结果:")
    print("="*60)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
