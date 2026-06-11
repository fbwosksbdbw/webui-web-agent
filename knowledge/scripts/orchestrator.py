#!/usr/bin/env python3
"""
工作流编排器 — 自动管理步骤序列、文件路由、前置检查和状态显示。

替代 AI 手动读 skill.md 表格找下一步的模式。AI 只需:
  1. orchestrator.py current → 获知当前步骤和需读取的文件
  2. Read 指定的 step 文件和 gate 文件
  3. 执行步骤内容
  4. orchestrator.py done → 自动推进到下一步

用法:
  python orchestrator.py init <菜单名>       # 初始化工作流
  python orchestrator.py current             # 输出当前步骤的完整上下文
  python orchestrator.py done                # 标记当前步骤完成
  python orchestrator.py status              # 显示所有步骤状态
  python orchestrator.py retry [step_id]     # 重置某步骤（默认当前步骤）
"""

import json
import os
import sys
import subprocess

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_FILE = os.path.join(SKILL_DIR, "step_state.json")

# 导入 state_manager 的状态读取函数（避免重复实现，消除并发写入风险）
sys.path.insert(0, os.path.join(SKILL_DIR, "scripts"))
from state_manager import cmd_get_state as _load_state

STEPS = [
    {"id": "0a", "step_file": "steps/step-0a-menu-init.md",     "gate_file": None,                   "label": "菜单初始化"},
    {"id": "0b", "step_file": "steps/step-0b-browser-login.md",  "gate_file": None,                   "label": "浏览器登录"},
    {"id": "1a", "step_file": "steps/step-1a-form-scan.md",      "gate_file": "gates/gate-1a.md",     "label": "表单扫描"},
    {"id": "1b", "step_file": "steps/step-1b-log-capture.md",    "gate_file": "gates/gate-1b.md",     "label": "日志抓取"},
    {"id": "2a", "step_file": "steps/step-2a-log-analysis.md",   "gate_file": None,                   "label": "日志分析"},
    {"id": "2b", "step_file": "steps/step-2b-base-table.md",     "gate_file": None,                   "label": "基础表覆盖"},
    {"id": "2c", "step_file": "steps/step-2c-sql-generation.md", "gate_file": None,                   "label": "SQL 生成"},
    {"id": "2d", "step_file": "steps/step-2d-plan-verify.md",    "gate_file": None,                   "label": "计划验证"},
    {"id": "3",  "step_file": "steps/step-3-case-generation.md", "gate_file": "gates/gate-3.md",      "label": "用例生成"},
    {"id": "4",  "step_file": "steps/step-4-verify.md",          "gate_file": None,                   "label": "验证"},
    {"id": "5",  "step_file": "steps/step-5-report.md",          "gate_file": None,                   "label": "测试报告"},
]

_STEP_MAP = {s["id"]: s for s in STEPS}
_STEP_IDS = [s["id"] for s in STEPS]


def _run_state_manager(*args):
    """调用 state_manager.py 执行底层操作"""
    cmd = [
        sys.executable,
        os.path.join(SKILL_DIR, "scripts", "state_manager.py"),
    ] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env={**os.environ, "PYTHONIOENCODING": "utf-8", "MSYS_NO_PATHCONV": "1"})
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        sys.exit(result.returncode)
    return result.stdout.strip()


def cmd_init(menu_name):
    _run_state_manager("init", menu_name)
    print(f"[OK] 工作流已初始化: {menu_name}")
    print()
    cmd_current()


def cmd_current():
    """输出当前步骤的完整上下文（AI 需读取的文件和需执行的命令）"""
    state = _load_state()
    completed = state.get("steps_completed", [])

    # 找到第一个未完成的步骤
    current_id = None
    for step in STEPS:
        if step["id"] not in completed:
            current_id = step["id"]
            break

    if current_id is None:
        print("[DONE] 所有步骤已完成。")
        print(f"  菜单: {state.get('menu_name', '?')}")
        return

    step = _STEP_MAP[current_id]

    # 检查前置步骤
    idx = _STEP_IDS.index(current_id)
    prereqs = _STEP_IDS[:idx]
    missing = [p for p in prereqs if p not in completed]

    print(f"{'='*50}")
    print(f"📍 当前步骤: {current_id} — {step['label']}")
    print(f"{'='*50}")
    print(f"  已完成: {completed or '(无)'}")
    if missing:
        print(f"  ⚠️ 前置缺失: {missing}")
    print()

    # 输出 AI 需要读取的文件
    step_path = os.path.join(SKILL_DIR, step["step_file"]).replace("\\", "/")
    print(f"📄 必须读取:")
    print(f"   1. {step['step_file']}  — 步骤操作指令")

    if step["gate_file"]:
        gate_path = os.path.join(SKILL_DIR, step["gate_file"]).replace("\\", "/")
        print(f"   2. {step['gate_file']}  — 决策矩阵（本步骤的 Gate）")

    print()
    print(f"⚡ 执行要求:")
    print(f"   1. Read 上述文件获取完整指令")
    print(f"   2. 按步骤文件执行所有 Actions")
    print(f"   3. 完成所有 Checklist 项")
    print(f"   4. 执行: python \"{{SKILL_DIR}}/scripts/orchestrator.py\" done")


def cmd_done():
    """标记当前步骤完成，自动推进"""
    state = _load_state()
    completed = state.get("steps_completed", [])

    # 找到当前步骤
    current_id = None
    for step in STEPS:
        if step["id"] not in completed:
            current_id = step["id"]
            break

    if current_id is None:
        print("[DONE] 所有步骤已完成，无需标记。")
        return

    _run_state_manager("step_done", current_id)
    print(f"[OK] 步骤 {current_id} 已完成。")

    # 检查是否全部完成
    state = _load_state()
    completed = state.get("steps_completed", [])
    remaining = [s for s in STEPS if s["id"] not in completed]

    if not remaining:
        print()
        print("🎯 所有步骤已完成！")
        _print_context_hint(state)
        return

    print()
    print(f"⏭️  下一步: {remaining[0]['id']} — {remaining[0]['label']}")
    print(f"   执行 orchestrator.py current 查看详情。")


def _estimate_context_tokens(state):
    """估算当前菜单消耗的上下文 tokens（基于生成文件的大小）"""
    menu_name = state.get("menu_name", "")
    case_plan = {}

    # 尝试读取 case_plan.json 获取路径信息
    # 用一个简单的启发式：扫描用例目录下生成的文件
    total_bytes = 0
    file_count = 0

    # 扫描 skill 目录下的 step_state 相关文件
    # 扫描当前菜单可能生成的文件
    try:
        # 从 state 中推导路径不太可靠，用固定估算
        # 基于调研数据：每步平均 ~3000 tokens 输出（含工具返回）
        step_count = len(state.get("steps_completed", []))
        # 每个 step 文件 ~500 tokens + 输出 ~2500 tokens
        estimated_tokens = step_count * 3000
        return estimated_tokens
    except Exception:
        return 0


def _print_context_hint(state):
    """菜单完成时，输出上下文健康提示"""
    completed_count = len(state.get("steps_completed", []))
    # 基于调研数据：11 步 ≈ 30k tokens（含上下文卫生优化后 ~6.6k）
    # 使用优化后的估算值
    estimated_per_menu = 6600
    total_estimated = completed_count * estimated_per_menu

    # 阈值：Claude 典型上下文窗口 ~200k tokens
    # 安全线 60% = 120k，警告线 80% = 160k
    safe_threshold = 120000
    warn_threshold = 160000

    print()
    print(f"📊 上下文估算: ~{total_estimated // 1000}k tokens (基于 {completed_count} 步)")
    if total_estimated >= warn_threshold:
        print(f"🔴 上下文已接近上限（>{warn_threshold // 1000}k），强烈建议执行 /compact 后再处理下一个菜单")
    elif total_estimated >= safe_threshold:
        print(f"🟡 上下文偏高（>{safe_threshold // 1000}k），建议执行 /compact")
    else:
        print(f"🟢 上下文健康，可继续处理下一个菜单")


def cmd_status():
    """显示所有步骤的完成状态"""
    state = _load_state()
    completed = state.get("steps_completed", [])
    menu = state.get("menu_name", "(未初始化)")

    print(f"菜单: {menu}")
    print(f"进度: {len(completed)}/{len(STEPS)}")
    print()

    for step in STEPS:
        sid = step["id"]
        done = sid in completed
        marker = "✅" if done else "⬚"
        label = step["label"]
        gate = f" → {step['gate_file']}" if step["gate_file"] else ""
        print(f"  {marker}  {sid}  {label}{gate}")

    remaining = [s for s in STEPS if s["id"] not in completed]
    if remaining:
        print()
        print(f"下一步: orchestrator.py current")


def cmd_recover():
    """compact 后恢复上下文 — 输出当前状态的关键信息"""
    state = _load_state()
    menu = state.get("menu_name", "(未初始化)")
    completed = state.get("steps_completed", [])

    # 找当前步骤
    current_id = None
    for step in STEPS:
        if step["id"] not in completed:
            current_id = step["id"]
            break

    print("=" * 50)
    print("🔄 上下文恢复")
    print("=" * 50)
    print(f"  当前菜单: {menu}")
    print(f"  已完成: {completed or '(无)'}")
    if current_id:
        print(f"  当前步骤: {current_id} — {_STEP_MAP[current_id]['label']}")
    else:
        print(f"  状态: 所有步骤已完成")
    print()

    # 输出 case_plan.json 关键字段（如果存在）
    try:
        import glob
        # 尝试找 case_plan.json
        skill_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        case_plans = glob.glob(os.path.join(skill_dir, "**", "case_plan.json"), recursive=False)
        if not case_plans:
            # 在 testcases 目录下搜索
            project_dir = state.get("project_dir", "")
            if project_dir:
                case_plans = glob.glob(os.path.join(project_dir, "**", "case_plan.json"), recursive=True)

        if case_plans:
            with open(case_plans[0], "r", encoding="utf-8") as f:
                cp = json.load(f)
            menu_info = cp.get("menu", {})
            print(f"  case_plan 关键字段:")
            print(f"    menu.name: {menu_info.get('name', '?')}")
            print(f"    menu.code: {menu_info.get('code', '?')}")
            print(f"    menu.url: {menu_info.get('url', '?')}")
            print(f"    menu.module: {menu_info.get('module', '?')}")
            shard = cp.get("shard", {})
            print(f"    shard.db: {shard.get('db', '?')}")
            print(f"    shard.routing_db: {shard.get('routing_db', '?')}")
            gd = cp.get("gate_decisions", {})
            print(f"    gate_decisions.customer_route: {gd.get('customer_route', '?')}")
            print(f"    gate_decisions.case_count: {gd.get('case_count', '?')}")
    except Exception as e:
        print(f"  (无法读取 case_plan.json: {e})")

    print()
    print("⚡ 下一步: 执行 orchestrator.py current 继续")


def cmd_retry(step_id=None):
    """重置指定步骤（委托 state_manager），默认重置当前步骤"""
    if step_id is None:
        state = _load_state()
        completed = state.get("steps_completed", [])
        for step in STEPS:
            if step["id"] not in completed:
                step_id = step["id"]
                break
        if step_id is None:
            step_id = completed[-1] if completed else None

    if step_id is None:
        print("[ERROR] 没有可重置的步骤。")
        sys.exit(1)

    if step_id not in _STEP_MAP:
        print(f"[ERROR] 未知步骤: {step_id}")
        sys.exit(1)

    _run_state_manager("retry", step_id)
    print()
    cmd_current()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法:")
        print("  python orchestrator.py init <菜单名>")
        print("  python orchestrator.py current")
        print("  python orchestrator.py done")
        print("  python orchestrator.py status")
        print("  python orchestrator.py recover")
        print("  python orchestrator.py retry [step_id]")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "init" and len(sys.argv) >= 3:
        cmd_init(sys.argv[2])
    elif cmd == "current":
        cmd_current()
    elif cmd == "done":
        cmd_done()
    elif cmd == "status":
        cmd_status()
    elif cmd == "recover":
        cmd_recover()
    elif cmd == "retry":
        step = sys.argv[2] if len(sys.argv) >= 3 else None
        cmd_retry(step)
    else:
        print(f"[ERROR] 无效命令: {sys.argv}")
        sys.exit(1)
