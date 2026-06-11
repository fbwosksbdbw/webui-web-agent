# -*- coding: utf-8 -*-
"""
部署 helpers.py 到项目 common/ 目录。

用法:
    python deploy_helpers.py --project-dir F:/python_project
    python deploy_helpers.py --check   # 仅检查，不实际部署
"""
import os
import re
import sys
import shutil
from datetime import date

SKILL_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(SKILL_DIR, 'assets', 'common', 'helpers.py')

_VERSION_RE = re.compile(r'# version:\s*(\d{4}-\d{2}-\d{2})')


def _read_version(filepath: str):
    """从文件第一行读取版本日期，返回 (date对象, 原始版本号) 或 (None, None)"""
    if not os.path.isfile(filepath):
        return None, None
    with open(filepath, 'r', encoding='utf-8') as f:
        first_line = f.readline()
    m = _VERSION_RE.search(first_line)
    if m:
        try:
            return date.fromisoformat(m.group(1)), m.group(1)
        except ValueError:
            return None, None
    return None, None


def deploy(project_dir: str, dry_run: bool = False) -> dict:
    """
    部署 helpers.py 到 {project_dir}/common/。
    返回 {"action": "created"|"updated"|"skipped"|"error", "detail": str}
    """
    if not os.path.isfile(SOURCE):
        return {"action": "error", "detail": f"源文件不存在: {SOURCE}"}

    target_dir = os.path.join(project_dir, 'common')
    target_file = os.path.join(target_dir, 'helpers.py')
    init_file = os.path.join(target_dir, '__init__.py')

    src_version, src_ver_str = _read_version(SOURCE)
    if src_version is None:
        return {"action": "error", "detail": f"源文件缺少版本标记 (# version: YYYY-MM-DD): {SOURCE}"}

    # 确保目标目录存在
    if not dry_run:
        os.makedirs(target_dir, exist_ok=True)
        if not os.path.isfile(init_file):
            with open(init_file, 'w', encoding='utf-8') as f:
                f.write('# common 业务公共包\n')

    # 检查目标文件
    if os.path.isfile(target_file):
        tgt_version, _ = _read_version(target_file)
        if tgt_version is not None and tgt_version >= src_version:
            return {"action": "skipped", "detail": f"目标版本 {tgt_version} >= 源版本 {src_version}，跳过"}
        # 需要更新：备份
        if not dry_run:
            backup = target_file + '.bak'
            shutil.copy2(target_file, backup)

    # 复制
    if not dry_run:
        shutil.copy2(SOURCE, target_file)

    return {"action": "updated" if os.path.isfile(target_file) else "created",
            "detail": f"版本 {src_ver_str} → {target_file}"}


def main():
    project_dir = None
    check_only = False
    for i, arg in enumerate(sys.argv):
        if arg == '--project-dir' and i + 1 < len(sys.argv):
            project_dir = sys.argv[i + 1]
        elif arg == '--check':
            check_only = True

    if not project_dir:
        # 尝试从 config.json 读取
        try:
            from skill_config import get_config
            project_dir = get_config().project_dir
        except Exception:
            print("用法: python deploy_helpers.py --project-dir <DIR>")
            sys.exit(1)

    if check_only:
        src_version, src_ver_str = _read_version(SOURCE)
        target_file = os.path.join(project_dir, 'common', 'helpers.py')
        tgt_version, tgt_ver_str = _read_version(target_file)
        print(f"源版本: {src_ver_str or '无'}")
        print(f"目标版本: {tgt_ver_str or '不存在'}")
        if src_version and (tgt_version is None or src_version > tgt_version):
            print("状态: 需要部署")
            sys.exit(1)
        else:
            print("状态: 已是最新")
            sys.exit(0)

    result = deploy(project_dir)
    print(result["detail"])
    if result["action"] == "error":
        sys.exit(1)


if __name__ == '__main__':
    main()
