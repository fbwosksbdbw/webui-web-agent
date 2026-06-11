#!/usr/bin/env python3
"""
Stagehand CDP 探勘引擎 — Python 包装层

提供与 hui-playwright-test 现有流程的对接接口。
调用底层 Node.js Stagehand 探勘脚本，解析 JSON 输出。

用法:
    # 作为模块导入（cdp_url 从 config.json 自动读取）
    from explore_wrapper import explore_page, ExploreField

    # 快速扫描
    fields = explore_page(mode="fast")

    # AI 增强扫描
    fields = explore_page(mode="deep")

    # 命令行使用
    python explore_wrapper.py --mode=fast
"""

import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass, asdict

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
from pathlib import Path
from typing import List, Optional, Literal

# 确保 scripts/ 目录在 sys.path 中（skill_config.py 所在位置）
_scripts_dir = str(Path(__file__).parent.parent.parent)
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

# ═══════════════════════════════════════════════════════════════
#  数据模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class ExploreField:
    """探勘输出的单个字段"""
    label: str
    type: Literal["input", "single-select", "multi-select", "button"]
    locator: str
    is_required: bool = False
    conditional_required_by: List[str] = None
    options: List[str] = None

    def __post_init__(self):
        if self.conditional_required_by is None:
            self.conditional_required_by = []
        if self.options is None:
            self.options = []

    def to_dict(self) -> dict:
        return asdict(self)


# ═══════════════════════════════════════════════════════════════
#  核心 API
# ═══════════════════════════════════════════════════════════════

# 探勘脚本所在目录
_EXPLORER_DIR = Path(__file__).parent.parent


def explore_page(
    cdp_url: str = None,
    target_url: str = None,
    mode: Literal["fast", "deep"] = "fast",
    llm_model: str = "gpt-4o",
    timeout: int = 60000,
    verbose: bool = False,
) -> List[ExploreField]:
    """
    探勘当前页面，返回所有表单字段和操作按钮。

    Args:
        cdp_url: Chrome CDP 调试地址（默认从 config.json 读取）
        target_url: 目标页面 URL 片段，优先匹配含此片段的页面
        mode:    扫描模式
                 - "fast": 纯 DOM 扫描，零 AI 开销（推荐日常使用）
                 - "deep": DOM + Stagehand AI 增强（需 LLM API Key）
        llm_model: Stagehand 使用的 LLM 模型（仅 deep 模式生效）
        timeout:   连接超时毫秒数
        verbose:   是否输出详细日志

    Returns:
        List[ExploreField]: 探勘结果列表

    Raises:
        RuntimeError: 探勘失败
        FileNotFoundError: Node.js 或依赖未安装
    """
    from skill_config import get_config
    cfg = get_config()

    if cdp_url is None:
        cdp_url = cfg.cdp_url
    if target_url is None:
        target_url = cfg.menu_url

    node_path = cfg.node_path if hasattr(cfg, 'node_path') else ''

    cmd = _build_command(cdp_url, target_url, mode, llm_model, timeout, verbose, output=None, node_path=node_path)

    # 使用 Popen 绕过 Python 3.10 subprocess.run 的 f_filename 兼容性问题
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(_EXPLORER_DIR),
            env=_build_node_env(_EXPLORER_DIR),
        )
    except FileNotFoundError:
        raise FileNotFoundError(
            "Node.js 未安装或不在 PATH 中。请安装 Node.js >= 18"
        )

    try:
        stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout // 1000 + 30)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError(f"探勘超时（{timeout}ms）")

    stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
    stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

    class _Result:
        pass
    result = _Result()
    result.returncode = proc.returncode
    result.stdout = stdout
    result.stderr = stderr

    if result.returncode != 0:
        stderr = result.stderr.strip() if result.stderr else ""
        raise RuntimeError(f"探勘失败 (exit {result.returncode}):\n{stderr}")

    # stdout 是 JSON，stderr 是日志
    if verbose and result.stderr:
        print(result.stderr, file=sys.stderr)

    stdout = result.stdout.strip()
    if not stdout:
        raise RuntimeError("探勘脚本无输出")

    try:
        raw_fields = json.loads(stdout)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"JSON 解析失败: {e}\n原始输出:\n{stdout[:500]}")

    # 适配 Node.js 输出的 camelCase → Python snake_case
    normalized = []
    for f in raw_fields:
        if "isRequired" in f:
            f["is_required"] = f.pop("isRequired")
        if "conditionalRequiredBy" in f:
            f["conditional_required_by"] = f.pop("conditionalRequiredBy")
        normalized.append(f)
    return [ExploreField(**f) for f in normalized]


def explore_page_to_json(
    cdp_url: str = None,
    target_url: str = None,
    mode: Literal["fast", "deep"] = "fast",
    llm_model: str = "gpt-4o",
    timeout: int = 60000,
    output_file: Optional[str] = None,
    verbose: bool = False,
) -> str:
    """
    探勘当前页面，返回 JSON 字符串。

    同 explore_page()，但输出为 JSON 字符串，可直接写入文件。
    """
    fields = explore_page(cdp_url, target_url, mode, llm_model, timeout, verbose)
    json_str = json.dumps(
        [f.to_dict() for f in fields],
        ensure_ascii=False,
        indent=2,
    )
    if output_file:
        Path(output_file).write_text(json_str, encoding="utf-8")
    return json_str


def explore_page_to_file(
    output_file: str,
    cdp_url: str = None,
    target_url: str = None,
    mode: Literal["fast", "deep"] = "fast",
    llm_model: str = "gpt-4o",
    timeout: int = 60000,
    verbose: bool = False,
) -> List[ExploreField]:
    """
    探勘当前页面并将结果写入 JSON 文件。

    Args:
        output_file: 输出文件路径
        其余参数同 explore_page()

    Returns:
        List[ExploreField]: 探勘结果
    """
    fields = explore_page(cdp_url, target_url, mode, llm_model, timeout, verbose)
    json_str = json.dumps(
        [f.to_dict() for f in fields],
        ensure_ascii=False,
        indent=2,
    )
    Path(output_file).write_text(json_str, encoding="utf-8")
    return fields


# ═══════════════════════════════════════════════════════════════
#  与 hui-playwright-test 集成的辅助函数
# ═══════════════════════════════════════════════════════════════

def get_form_fields(fields: List[ExploreField]) -> List[ExploreField]:
    """过滤出表单字段（不含按钮）"""
    return [f for f in fields if f.type != "button"]


def get_buttons(fields: List[ExploreField]) -> List[ExploreField]:
    """过滤出操作按钮"""
    return [f for f in fields if f.type == "button"]


def get_multi_select_fields(fields: List[ExploreField]) -> List[ExploreField]:
    """过滤出多选下拉框"""
    return [f for f in fields if f.type == "multi-select"]


def get_single_select_fields(fields: List[ExploreField]) -> List[ExploreField]:
    """过滤出单选下拉框"""
    return [f for f in fields if f.type == "single-select"]


def get_input_fields(fields: List[ExploreField]) -> List[ExploreField]:
    """过滤出文本输入框"""
    return [f for f in fields if f.type == "input"]


def is_ta_multi_select(fields: List[ExploreField]) -> bool:
    """
    判断 TA 代码下拉框是否为多选。

    在 hui-playwright-test 流程中，这决定了多条件查询策略：
    - 多选 TA → querymulti 使用多选模板（variant 3a）
    - 单选 TA → querymulti 使用单选模板（variant 3b）
    """
    ta_fields = [f for f in fields if "TA" in f.label or "ta" in f.label.lower()]
    return any(f.type == "multi-select" for f in ta_fields)


def has_query_button(fields: List[ExploreField]) -> bool:
    """检查是否存在查询按钮"""
    return any(f.label == "查询" for f in fields)


def has_reset_button(fields: List[ExploreField]) -> bool:
    """检查是否存在重置按钮"""
    return any(f.label == "重置" for f in fields)


def has_preview_button(fields: List[ExploreField]) -> bool:
    """检查是否存在预览/详情按钮"""
    return any(f.label in ("预览", "详情", "查看") for f in fields)


def has_required_fields(fields: List[ExploreField]) -> bool:
    """检查页面是否有必填字段（任一字段 is_required=True 或 conditional_required_by 非空）"""
    return any(f.is_required or f.conditional_required_by for f in fields)


# ═══════════════════════════════════════════════════════════════
#  Preset 匹配与日期修正
# ═══════════════════════════════════════════════════════════════

_DATE_LABEL_KEYWORDS = ('起息日', '截止日', '到期日', '计息日', '起始日', '结束日')


def _is_date_label(label: str) -> bool:
    """判断标签是否为日期类字段。

    匹配规则：
    - 包含 "日期"（如 "查询日期起始"）
    - 包含常见日期后缀（如 "产品起息日-截至"、"产品截止日-起始"）
    """
    if '日期' in label:
        return True
    return any(kw in label for kw in _DATE_LABEL_KEYWORDS)


def fix_date_types(fields: List[dict]) -> List[dict]:
    """修正日期字段类型：含\"日期\"且 type=select 的改为 input。

    HUI 日期选择器内部虽有 .h-select-input class，但实际是 input 框，
    应用 fill 而非 select。
    """
    for f in fields:
        label = f.get('label', '') if isinstance(f, dict) else getattr(f, 'label', '')
        ftype = f.get('type', '') if isinstance(f, dict) else getattr(f, 'type', '')
        if _is_date_label(label) and ftype in ('single-select', 'select'):
            if isinstance(f, dict):
                f['type'] = 'input'
            else:
                f.type = 'input'
    return fields


def _match_preset_value(label: str, preset_fields: dict) -> str:
    """Fuzzy match a form label to preset_fields, returning the preset value or ''."""
    best_match_len = 0
    best_value = ''
    best_exact = False
    for key, info in preset_fields.items():
        field_name = info.get('name', '') if isinstance(info, dict) else ''
        if not field_name:
            continue
        if label == field_name:
            if not best_exact or len(field_name) > best_match_len:
                best_value = info.get('value', '') if isinstance(info, dict) else ''
                best_exact = True
                best_match_len = len(field_name)
            continue
        if best_exact:
            continue
        if field_name in label or label in field_name:
            if len(field_name) > best_match_len:
                best_value = info.get('value', '') if isinstance(info, dict) else ''
                best_match_len = len(field_name)
    return best_value


_PAGINATION_PATTERN = re.compile(r'^\d+\s*条/页$')


def _is_pagination_options(opts: list) -> bool:
    """全部选项都是分页数据（N 条/页）→ 不可信，视为空 options。"""
    return len(opts) > 0 and all(
        _PAGINATION_PATTERN.match(o or '') for o in opts
    )


def generate_field_values(
    explored_fields: list,
    preset_fields: dict,
) -> dict:
    """为所有探勘字段生成统一值。不再有空值跳过——全字段有值。

    值生成规则:
      1. 模糊匹配 Excel preset 成功 → 使用 preset 值
      2. 下拉框（有 options）:
         a. preset 在 options 中 → 使用 preset
         b. preset 不匹配或不存在 → options[0]
      3. 输入框:
         a. 含"日期" → 按起止/类型生成
         b. 含"金额/价格/份额/数量/笔数" → "1"
         c. 编号/代码/账号/标识类 → 生成编号
         d. 其他 → "_"

    Args:
        explored_fields: 探勘结果列表，每元素含 label, type, is_required, options
        preset_fields: {key: {name, value}} 映射

    Returns:
        {"all": [...], "required": [...], "unmatched": []}
        每项: {label, type, is_required, options, value, value_source}
    """
    from datetime import datetime, timedelta

    today = datetime.now()
    date_start = today - timedelta(days=365)  # 往前推一年
    date_end = today
    date_start_str = date_start.strftime('%Y-%m-%d')
    date_end_str = date_end.strftime('%Y-%m-%d')

    # 关键词 → 列名缩写映射（用于生成编号前缀）
    _ID_LABEL_PATTERNS = [
        ('申请编号', 'AP'),
        ('流水号', 'SQ'),
        ('序列号', 'SN'),
        ('编号', 'NO'),
        ('账号', 'ACCT'),
        ('代码', 'CD'),
        ('标识', 'ID'),
        ('单号', 'OD'),
        ('合同号', 'CON'),
        ('协议号', 'AGR'),
        ('手机', 'PH'),
        ('电话', 'PH'),
    ]
    _NUMBER_LABELS = ['金额', '价格', '份额', '数量', '笔数', '比例', '费率', '百分比']
    _NAME_LABELS = ['名称', '姓名', '户名']
    _REMARK_LABELS = ['备注', '说明', '描述']

    def _gen_date_val(lbl: str) -> str:
        """Generate date value based on label semantics."""
        if '开始' in lbl or '起始' in lbl:
            return date_start_str
        elif '结束' in lbl or '截止' in lbl or '终止' in lbl:
            return date_end_str
        elif '日期' in lbl:
            # 起止之间的中间值
            mid = date_start + (date_end - date_start) / 2
            return mid.strftime('%Y-%m-%d')
        return today.strftime('%Y-%m-%d')

    def _gen_default_val(lbl: str) -> str:
        """Generate default value for non-date input fields."""
        for kw, prefix in _ID_LABEL_PATTERNS:
            if kw in lbl:
                return f"{prefix}{today.strftime('%Y%m%d')}001"
        for kw in _NUMBER_LABELS:
            if kw in lbl:
                if '比例' in lbl or '费率' in lbl or '百分比' in lbl:
                    return "50"
                return "1000"
        for kw in _NAME_LABELS:
            if kw in lbl:
                return f"自动化测试{today.strftime('%m%d%H%M')}"
        for kw in _REMARK_LABELS:
            if kw in lbl:
                return "自动化测试备注"
        return f"AUTO_{today.strftime('%m%d%H%M%S')}"

    all_fields = []
    required = []
    unmatched = []

    for f in explored_fields:
        label = f.label if hasattr(f, 'label') else f.get('label', '')
        ftype = f.type if hasattr(f, 'type') else f.get('type', '')
        is_req = f.is_required if hasattr(f, 'is_required') else f.get('is_required', False)
        options = f.options if hasattr(f, 'options') else f.get('options', [])

        if ftype == 'button':
            continue

        if is_req:
            required.append(label)

        preset_value = _match_preset_value(label, preset_fields)

        if ftype in ('single-select', 'multi-select'):
            # ─── 下拉框 ───
            opts = options or []
            opts_polluted = _is_pagination_options(opts)
            if opts_polluted:
                opts = []
            # 级联下拉的探勘 options 可能来自前置字段切换前的旧列表。
            # Excel 预设是业务数据源，存在 preset 时优先信任 preset。
            if preset_value:
                value = preset_value
                value_source = 'preset'
            elif opts:
                value = opts[0]
                value_source = 'first_option'
            else:
                value = '__FIRST_OPTION__'
                value_source = 'first_option'
        elif ftype == 'tree-select':
            # ─── 树选择框（如机构代码）：直接选第一个，不自己造数 ───
            value = '__FIRST_OPTION__'
            value_source = 'first_option'
        else:
            # ─── 输入框 ───
            if preset_value:
                value = preset_value
                value_source = 'preset'
            elif _is_date_label(label):
                value = _gen_date_val(label)
                value_source = 'generated'
            else:
                value = _gen_default_val(label)
                value_source = 'generated'

        field_entry = {
            'label': label,
            'type': ftype,
            'is_required': is_req,
            'options': options,
            'value': value,
            'value_source': value_source,
        }
        all_fields.append(field_entry)

    # 自动排除的字段类型：
    # 1. tree-select 类型的字段（如机构代码）
    # 2. 不能重复的字段（如编号、流水号、申请编号等）
    _NON_REUSABLE_KEYWORDS = ['编号', '流水号', '序列号', '单号', '合同号', '协议号']
    excluded_fields = []
    for f in all_fields:
        # 排除 tree-select 类型
        if f['type'] == 'tree-select':
            excluded_fields.append(f['label'])
            continue
        # 排除不能重复的字段
        for kw in _NON_REUSABLE_KEYWORDS:
            if kw in f['label']:
                excluded_fields.append(f['label'])
                break

    return {
        'all': all_fields,
        'required': required,
        'unmatched': unmatched,
        'excluded_fields': excluded_fields,
    }


def match_presets_to_fields(
    explored_fields: list,
    preset_fields: dict,
) -> dict:
    """Backward-compatible wrapper for generate_field_values."""
    return generate_field_values(explored_fields, preset_fields)


def explore_and_match(
    config_json: str,
    mode: str = "fast",
    llm_model: str = "gpt-4o",
    timeout: int = 60000,
    output_file: str = None,
    verbose: bool = False,
) -> dict:
    """探索页面 → 日期修正 → 生成字段值，返回 form_fields 结构。

    Args:
        config_json: config.json 路径（含 cdp_url、menu_url、preset_fields）
        其他参数同 explore_page()

    Returns:
        generate_field_values 的返回值: {all, required, unmatched}
    """
    from skill_config import get_config
    cfg = get_config(config_json)

    fields = explore_page(
        cdp_url=cfg.cdp_url,
        target_url=cfg.menu_url,
        mode=mode,
        llm_model=llm_model,
        timeout=timeout,
        verbose=verbose,
    )

    field_dicts = [f.to_dict() for f in fields]
    fix_date_types(field_dicts)
    result = generate_field_values(field_dicts, cfg.preset_fields)

    if output_file:
        json_str = json.dumps(result, ensure_ascii=False, indent=2)
        Path(output_file).write_text(json_str, encoding="utf-8")
        # stats by value_source
        sources = {}
        for fv in result['all']:
            src = fv.get('value_source', 'unknown')
            sources[src] = sources.get(src, 0) + 1
        stats_str = ' | '.join(f"{k}: {v}" for k, v in sorted(sources.items()))
        print(
            f"字段值生成完成 | 总计: {len(result['all'])} | {stats_str}",
            file=sys.stderr,
        )

    return result


# ═══════════════════════════════════════════════════════════════
#  内部工具
# ═══════════════════════════════════════════════════════════════

def _build_command(
    cdp_url: str,
    target_url: Optional[str],
    mode: str,
    llm_model: str,
    timeout: int,
    verbose: bool,
    output: Optional[str],
    node_path: str = None,
) -> list:
    """构建 Node.js 探勘脚本的命令行参数"""
    # 查找 Node.js
    node_cmd = _find_node(node_path)
    script = _EXPLORER_DIR / "src" / "explore.ts"
    ts_node_register = _find_ts_node_register(_EXPLORER_DIR)

    if not script.exists():
        raise FileNotFoundError(f"探勘脚本不存在: {script}")

    cmd = [
        node_cmd,
        "--require", str(ts_node_register),
        str(script),
        f"--cdp-url={cdp_url}",
        f"--mode={mode}",
        f"--llm-model={llm_model}",
        f"--timeout={timeout}",
    ]

    if target_url:
        cmd.append(f"--target-url={target_url}")

    if verbose:
        cmd.append("--verbose")

    if output:
        cmd.append(f"--output={output}")

    return cmd


def _find_stagehand_node_modules(explorer_dir: Path = None) -> Path:
    """Find node_modules with ts-node for this Stagehand project."""
    explorer_dir = Path(explorer_dir or _EXPLORER_DIR)
    node_modules = explorer_dir / "node_modules"
    if (node_modules / "ts-node").is_dir():
        return node_modules

    raise FileNotFoundError(
        "找不到 Stagehand Node 依赖 ts-node/register。"
        f"请在 {_EXPLORER_DIR} 运行 npm ci。"
    )


def _find_ts_node_register(explorer_dir: Path = None) -> Path:
    """Return the local ts-node/register preload file for this Stagehand project."""
    node_modules = _find_stagehand_node_modules(explorer_dir)
    register = node_modules / "ts-node" / "register" / "index.js"
    if register.is_file():
        return register
    raise FileNotFoundError(f"找不到本地 ts-node/register 入口: {register}")


def _build_node_env(explorer_dir: Path = None) -> dict:
    """Build Node environment pinned to this Stagehand project."""
    env = os.environ.copy()
    explorer_dir = Path(explorer_dir or _EXPLORER_DIR)
    node_modules = _find_stagehand_node_modules(Path(explorer_dir or _EXPLORER_DIR))
    existing = env.get("NODE_PATH", "")
    env["NODE_PATH"] = (
        str(node_modules)
        if not existing
        else str(node_modules) + os.pathsep + existing
    )
    env["TS_NODE_PROJECT"] = str(explorer_dir / "tsconfig.json")
    return env


def _find_node(node_path: str = None) -> str:
    """查找 Node.js 可执行文件。node_path 优先，否则搜索 PATH。"""
    # 1. 配置的路径优先
    if node_path:
        if os.path.isfile(node_path):
            return node_path
        # 可能是目录，尝试追加 node / node.exe
        for name in ["node", "node.exe"]:
            full = os.path.join(node_path, name)
            if os.path.isfile(full):
                return full

    # 2. 搜索 PATH
    for name in ["node", "node.exe"]:
        for path_dir in os.environ.get("PATH", "").split(os.pathsep):
            full = os.path.join(path_dir, name)
            if os.path.isfile(full):
                return full

    raise FileNotFoundError(
        "找不到 Node.js。请在 Excel 环境配置中设置 node_path，或确保 Node.js >= 18 在 PATH 中。"
    )


# ═══════════════════════════════════════════════════════════════
#  命令行入口
# ═══════════════════════════════════════════════════════════════

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Stagehand CDP 探勘引擎 — Python 包装层",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 快速扫描（推荐日常使用）
  python explore_wrapper.py --mode=fast

  # AI 增强扫描
  python explore_wrapper.py --mode=deep --llm-model=claude-sonnet-4-20250514

  # 输出到文件
  python explore_wrapper.py --mode=fast -o result.json
        """,
    )

    parser.add_argument(
        "--config", default=None,
        help="config.json 路径 (默认自动发现)",
    )
    parser.add_argument(
        "--cdp-url", default=None,
        help="CDP 调试地址 (默认从 config.json 读取)",
    )
    parser.add_argument(
        "--mode", choices=["fast", "deep"], default="fast",
        help="扫描模式: fast=纯DOM, deep=DOM+AI (默认: fast)",
    )
    parser.add_argument(
        "--llm-model", default="gpt-4o",
        help="Stagehand LLM 模型 (默认: gpt-4o)",
    )
    parser.add_argument(
        "--timeout", type=int, default=60000,
        help="连接超时毫秒数 (默认: 60000)",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="输出 JSON 文件路径",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true",
        help="详细日志",
    )
    parser.add_argument(
        "--check-node", action="store_true",
        help="仅检查 Node.js 是否可用（不执行扫描），退出码 0=OK 1=未找到",
    )

    args = parser.parse_args()

    # --check-node 模式：仅验证 Node.js 可用性，不执行扫描
    if args.check_node:
        try:
            from skill_config import get_config
            _cfg = get_config(args.config)
            _node = _cfg.node_path if hasattr(_cfg, 'node_path') else ''
            found = _find_node(_node)
            node_modules = _find_stagehand_node_modules(_EXPLORER_DIR)
            print(f"[OK] Node.js 可用: {found}", file=sys.stderr)
            print(f"[OK] Stagehand 依赖可用: {node_modules}", file=sys.stderr)
            sys.exit(0)
        except FileNotFoundError as e:
            print(f"[ERROR] {e}", file=sys.stderr)
            sys.exit(1)

    try:
        cdp_url = args.cdp_url
        target_url = None
        if cdp_url is None:
            from skill_config import get_config
            cfg = get_config(args.config)
            cdp_url = cfg.cdp_url
            target_url = cfg.menu_url

        fields = explore_page(
            cdp_url=cdp_url,
            target_url=target_url,
            mode=args.mode,
            llm_model=args.llm_model,
            timeout=args.timeout,
            verbose=args.verbose,
        )

        # 输出统计
        stats = {
            "total": len(fields),
            "inputs": sum(1 for f in fields if f.type == "input"),
            "single_selects": sum(1 for f in fields if f.type == "single-select"),
            "multi_selects": sum(1 for f in fields if f.type == "multi-select"),
            "buttons": sum(1 for f in fields if f.type == "button"),
            "required": sum(1 for f in fields if f.is_required),
            "conditional_required": sum(1 for f in fields if f.conditional_required_by),
        }
        print(
            f"探勘完成 | 总计: {stats['total']} | "
            f"输入框: {stats['inputs']} | 单选: {stats['single_selects']} | "
            f"多选: {stats['multi_selects']} | 按钮: {stats['buttons']} | "
            f"必填: {stats['required']} | 关联必填: {stats['conditional_required']}",
            file=sys.stderr,
        )

        # 输出 JSON
        field_dicts = [f.to_dict() for f in fields]
        fix_date_types(field_dicts)
        json_str = json.dumps(
            field_dicts,
            ensure_ascii=False,
            indent=2,
        )

        if args.output:
            Path(args.output).write_text(json_str, encoding="utf-8")
            print(f"结果已写入: {args.output}", file=sys.stderr)
        else:
            print(json_str)

    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
