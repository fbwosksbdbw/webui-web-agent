#!/usr/bin/env python3
"""通用 Skill 状态管理器。只做步骤流转追踪，决策数据统一存 case_plan.json。"""

import json
import os
import shutil
import sys

STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "step_state.json")
BACKUP_FILE = STATE_FILE + ".bak"

STEP_PREREQUISITES = {
    "step-0a": [],
    "step-0b": ["0a"],
    "step-1a": ["0a", "0b"],
    "step-1b": ["0a", "0b", "1a"],
    "step-2a": ["0a", "0b", "1a", "1b"],
    "step-2b": ["0a", "0b", "1a", "1b", "2a"],
    "step-2c": ["0a", "0b", "1a", "1b", "2a", "2b"],
    "step-2d": ["0a", "0b", "1a", "1b", "2a", "2b", "2c"],
    "step-3":  ["0a", "0b", "1a", "1b", "2a", "2b", "2c", "2d"],
    "step-4":  ["0a", "0b", "1a", "1b", "2a", "2b", "2c", "2d", "3"],
    "step-5":  ["0a", "0b", "1a", "1b", "2a", "2b", "2c", "2d", "3", "4"],
}


# ─── 文件锁（Windows msvcrt / POSIX fcntl） ───

def _lock_file(f, exclusive=False):
    """对文件加锁。exclusive=True 为排他锁（写），False 为共享锁（读）。"""
    try:
        if sys.platform == 'win32':
            import msvcrt
            # msvcrt.locking 需要指定字节范围，LK_NBLCK 为非阻塞锁
            mode = msvcrt.LK_NBLCK if not exclusive else msvcrt.LK_LOCK
            try:
                msvcrt.locking(f.fileno(), mode, 1)
            except OSError:
                # 锁被占用，等待后重试
                import time
                for _ in range(50):
                    time.sleep(0.05)
                    try:
                        msvcrt.locking(f.fileno(), mode, 1)
                        return
                    except OSError:
                        continue
                print("[WARN] 文件锁等待超时，继续执行（可能并发写入）", file=sys.stderr)
        else:
            import fcntl
            flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            fcntl.flock(f.fileno(), flag)
    except ImportError:
        # 锁不可用时静默跳过（降级为无锁模式）
        pass


def _unlock_file(f):
    """释放文件锁。"""
    try:
        if sys.platform == 'win32':
            import msvcrt
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            except OSError:
                pass
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except ImportError:
        pass


# ─── 备份与恢复 ───

def _backup_state():
    """保存前备份当前状态文件。"""
    if os.path.exists(STATE_FILE):
        try:
            shutil.copy2(STATE_FILE, BACKUP_FILE)
        except Exception:
            pass


def _try_restore_from_backup():
    """JSONDecodeError 时尝试从备份恢复。返回 (state, restored_bool)。"""
    if os.path.exists(BACKUP_FILE):
        try:
            with open(BACKUP_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            # 恢复成功，写回主文件
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
            print(f"[WARN] 状态文件损坏，已从备份恢复: {BACKUP_FILE}", file=sys.stderr)
            return state, True
        except Exception:
            pass
    return None, False


# ─── 核心读写 ───

def _load():
    if not os.path.exists(STATE_FILE):
        return {"menu_name": "", "current_step": "", "steps_completed": [], "checklists": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            _lock_file(f, exclusive=False)
            try:
                return json.load(f)
            finally:
                _unlock_file(f)
    except json.JSONDecodeError as e:
        print(f"[ERROR] 状态文件损坏: {STATE_FILE}\n   {e}", file=sys.stderr)
        # 尝试从备份恢复
        state, restored = _try_restore_from_backup()
        if restored:
            return state
        print(f"[FATAL] 备份恢复也失败，请手动检查: {STATE_FILE}", file=sys.stderr)
        sys.exit(1)


def _save(state):
    _backup_state()
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        _lock_file(f, exclusive=True)
        try:
            json.dump(state, f, ensure_ascii=False, indent=2)
        finally:
            _unlock_file(f)


def cmd_init(menu_name):
    state = {
        "menu_name": menu_name,
        "current_step": "0a",
        "steps_completed": [],
        "checklists": {},
    }
    _save(state)
    print(f"[OK] 状态已初始化: {menu_name}")


def cmd_check(step):
    state = _load()
    prereqs = STEP_PREREQUISITES.get(step)
    if prereqs is None:
        print(f"[ERROR] 未知步骤: {step}")
        sys.exit(1)
    missing = [s for s in prereqs if s not in state["steps_completed"]]
    if missing:
        print(f"[STOP] 前置步骤未完成: {', '.join(missing)}")
        print(f"   已完成: {state['steps_completed']}")
        sys.exit(1)
    print(f"[OK] 前置状态检查通过: {step}")


def cmd_step_done(step):
    state = _load()
    short = step.replace("step-", "") if step.startswith("step-") else step
    if short not in state["steps_completed"]:
        state["steps_completed"].append(short)
    state["current_step"] = short
    _save(state)
    print(f"[OK] 步骤 {short} 已完成")

    # step 4 完成后自动标记菜单为已完成
    if short == '4' and state.get('menu_name'):
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from config_reader import ConfigReader
            cfg = ConfigReader()
            cfg.mark_done(state['menu_name'])
        except Exception as e:
            print(f"[WARN] 自动标记已完成失败: {e}", file=sys.stderr)


def cmd_checklist(name):
    state = _load()
    state["checklists"][name] = True
    _save(state)
    print(f"[OK] 检查清单 {name} 已标记完成")


def cmd_retry(step):
    """重置指定步骤及之后所有已完成步骤"""
    state = _load()
    step_key = step if step.startswith("step-") else f"step-{step}"
    short = step_key.replace("step-", "")

    if step_key not in STEP_PREREQUISITES:
        print(f"[ERROR] 未知步骤: {step}")
        sys.exit(1)

    step_ids = list(STEP_PREREQUISITES.keys())
    idx = step_ids.index(step_key)
    affected_shorts = [k.replace("step-", "") for k in step_ids[idx:]]

    removed = [s for s in affected_shorts if s in state["steps_completed"]]
    state["steps_completed"] = [s for s in state["steps_completed"] if s not in affected_shorts]
    state["current_step"] = short
    _save(state)

    print(f"[OK] 已重置步骤 {short} 及之后的 {len(removed)} 个步骤")


def cmd_get_state():
    """返回当前状态字典（供 orchestrator.py 导入使用，避免重复代码）"""
    return _load()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: state_manager.py <init|check|step_done|checklist|retry> [参数...]")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "init" and len(sys.argv) >= 3:
        cmd_init(sys.argv[2])
    elif cmd == "check" and len(sys.argv) >= 3:
        cmd_check(sys.argv[2])
    elif cmd == "step_done" and len(sys.argv) >= 3:
        cmd_step_done(sys.argv[2])
    elif cmd == "checklist" and len(sys.argv) >= 3:
        cmd_checklist(sys.argv[2])
    elif cmd == "retry" and len(sys.argv) >= 3:
        cmd_retry(sys.argv[2])
    else:
        print(f"[ERROR] 无效命令: {sys.argv}")
        sys.exit(1)
