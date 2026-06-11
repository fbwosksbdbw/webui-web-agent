#!/usr/bin/env python3
"""
纯 Python 原生 CDP 执行引擎 — 替代 playwright-cli 的全内存方案。

与框架 common/helpers.py 的选择器模式完全对齐：
  - 下拉框：.h-form-item + label 精确匹配 → .h-select-input → _find_option
  - 输入框：.h-form-item + label 精确匹配 → input:not(.h-select-input)
  - 按钮：Stagehand locator 翻译 或 get_by_role("button", name=...)

上游 Stagehand 探勘脚本生成包含 JS Locator 的 JSON，本脚本：
  1. CDP 直连已登录的 Chrome（不启动新浏览器）
  2. 将 JS Locator 语法翻译为 Python Playwright 原生 API
  3. 按字段类型分发动作（fill / click / multi-select），选择器对齐框架
  4. 等待后无缝衔接 paramiko SSH 抓取后端日志

用法:
    from cdp_executor import CDPExecutor
    with CDPExecutor() as executor:
        executor.load_fields("explore_result.json")
        executor.fill_field("客户标识", "A3060210020001")
        executor.click_button("查询")
        executor.wait_and_capture("manager.log", wait_ms=3000)
"""

import json
import logging
import os
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pathlib import Path
from typing import Dict, List, Optional

if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

from playwright.sync_api import sync_playwright, Page, Locator

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
#  1. JS → Python Locator 语法翻译器（Stagehand 探勘结果用）
# ─────────────────────────────────────────────────────────────

_JS_TO_PY_METHODS = {
    "getByRole": "get_by_role",
    "getByText": "get_by_text",
    "getByLabel": "get_by_label",
    "getByPlaceholder": "get_by_placeholder",
    "getByTestId": "get_by_test_id",
}


def _brace_aware_split(s: str, sep: str = ".") -> list:
    """按 sep 拆分，但跳过括号/引号内的内容"""
    parts, depth, current, in_str = [], 0, "", None
    i = 0
    while i < len(s):
        ch = s[i]
        if in_str:
            current += ch
            if ch == "\\" and i + 1 < len(s):
                current += s[i + 1]
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in ('"', "'"):
            in_str = ch
            current += ch
        elif ch in ("(", "[", "{"):
            depth += 1
            current += ch
        elif ch in (")", "]", "}"):
            depth -= 1
            current += ch
        elif ch == sep and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
        i += 1
    if current:
        parts.append(current)
    return parts


def _camel_to_snake(name: str) -> str:
    """camelCase → snake_case"""
    s = re.sub(r'(.)([A-Z][a-z]+)', r'\1_\2', name)
    return re.sub(r'([a-z0-9])([A-Z])', r'\1_\2', s).lower()


def _filter_replacer(match: re.Match) -> str:
    """JS filter({ hasText: 'X' }) → Python filter(has_text='X')"""
    inner = match.group(1)
    pairs = re.findall(r"(\w+)\s*:\s*(['\"])(.*?)\2", inner)
    kwargs = ", ".join(f"{_camel_to_snake(k)}={q}{v}{q}" for k, q, v in pairs)
    return f".filter({kwargs})"


def translate_locator(page: Page, js_locator_str: str) -> Locator:
    """
    将 JS Playwright Locator 语法翻译为 Python 原生 Locator 对象。

    支持:
      page.locator('.h-form-item').filter({ hasText: '客户标识' }).getByRole('textbox')
      page.locator('.class').getByText('text')
      page.locator('#id').first
    """
    expr = js_locator_str.strip().rstrip(";")

    # filter({ ... }) → filter(key='value')
    expr = re.sub(r"\.filter\(\{([^}]*)\}\)", _filter_replacer, expr)

    # camelCase → snake_case（只转换方法名）
    parts = _brace_aware_split(expr, ".")
    translated = []
    for part in parts:
        for js_name, py_name in _JS_TO_PY_METHODS.items():
            if js_name in part:
                part = part.replace(js_name, py_name)
                break
        translated.append(part)
    expr = ".".join(translated).rstrip(";").strip()

    # JS object { key: 'value' } → Python kwargs key='value' in method args
    # e.g. get_by_role('button', { name: '重置' }) → get_by_role('button', name='重置')
    expr = re.sub(r"\{([^}]*)\}", lambda m: ", ".join(
        f"{_camel_to_snake(k.strip())}={v.strip()}"
        for k, v in re.findall(r"(\w+)\s*:\s*(['\"].*?['\"])", m.group(1))
    ), expr)

    logger.debug(f"Locator 翻译: {js_locator_str} → {expr}")
    try:
        return eval(expr, {"__builtins__": {}}, {"page": page})
    except Exception as e:
        raise SyntaxError(f"翻译执行失败: {expr}\n原始: {js_locator_str}\n错误: {e}")


# ─────────────────────────────────────────────────────────────
#  2. CDP 连接管理
# ─────────────────────────────────────────────────────────────

def connect_cdp(
    cdp_url: str = None,
    target_url_fragment: Optional[str] = None,
    timeout: int = 30000,
) -> tuple:
    """CDP 直连已运行的 Chrome。只 disconnect()，绝不 close()。"""
    pw = sync_playwright().start()
    browser = pw.chromium.connect_over_cdp(cdp_url, timeout=timeout)
    contexts = browser.contexts
    if not contexts:
        raise RuntimeError(f"CDP 连接成功但无上下文: {cdp_url}")

    pages = contexts[0].pages
    if not pages:
        raise RuntimeError("CDP 连接成功但无打开的页面")

    if target_url_fragment:
        for p in pages:
            if target_url_fragment in p.url:
                logger.info(f"匹配目标页面: {p.url}")
                return pw, browser, p
        logger.warning(f"未匹配 '{target_url_fragment}'，使用最后活跃页面")

    return pw, browser, pages[-1]


# ─────────────────────────────────────────────────────────────
#  3. 框架对齐的 HiUI 组件交互层
#     选择器模式与 common/helpers.py 完全一致
# ─────────────────────────────────────────────────────────────

# HiUI 下拉框候选选择器（与框架 _find_option_in_dropdown 一致）
_DROPDOWN_SELECTORS = [
    ".h-select-dropdown li",
    ".h-select-dropdown .h-option",
    "ul.h-select-dropdown li",
    ".h-select-dropdown-list li",
]


_PAGINATION_TEXT_RE = re.compile(r"(?:^\d+\s*条/页$|共\s*\d+\s*条|\b\d+\s*/\s*\d+\b)")


def _normalize_dropdown_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())


def _is_dropdown_pagination_text(text: str) -> bool:
    return bool(_PAGINATION_TEXT_RE.search(str(text or "").strip()))


def _dropdown_option_matches(option_name: str, candidate_text: str) -> bool:
    candidate = str(candidate_text or "").strip()
    option = str(option_name or "").strip()
    if not candidate or not option or _is_dropdown_pagination_text(candidate):
        return False
    if candidate == option:
        return True
    option_code, _, option_label = option.partition(":")
    candidate_code, _, candidate_label = candidate.partition(":")
    option_norm = _normalize_dropdown_text(option)
    candidate_norm = _normalize_dropdown_text(candidate)
    if candidate_norm == option_norm:
        return True
    if option_code and candidate_code and candidate_code == option_code:
        return True
    if option_label and candidate_label and _normalize_dropdown_text(candidate_label) == _normalize_dropdown_text(option_label):
        return True
    if candidate_label and ":" not in option and _normalize_dropdown_text(candidate_label) == _normalize_dropdown_text(option):
        return True
    if ":" in option and not ":" in candidate:
        return candidate == option_code or _normalize_dropdown_text(candidate) == _normalize_dropdown_text(option_label)
    return False


def _allow_first_option_fallback(option_name: str) -> bool:
    return str(option_name or "").strip() in {"", "_", "__FIRST_OPTION__"}


def _find_option_in_dropdown(page: Page, option_name: str) -> bool:
    """
    在已打开的下拉列表中查找并点击选项。
    策略与框架 common/helpers.py._find_option_in_dropdown 完全对齐：
      1. 遍历所有可见选项，精确匹配 text_content
      2. 精确匹配失败 → has_text 子串匹配 + 文本验证
    """
    # 等待下拉框出现
    dropdown_combined = ".h-select-dropdown, ul.h-select-dropdown, .h-select-dropdown-list"
    try:
        page.wait_for_selector(dropdown_combined, state="visible", timeout=2000)
    except Exception:
        page.wait_for_timeout(500)

    # 策略1: 遍历所有可见选项，精确匹配
    for css in _DROPDOWN_SELECTORS:
        try:
            items = page.locator(css)
            cnt = items.count()
            for i in range(cnt):
                item = items.nth(i)
                if not item.is_visible():
                    continue
                text = (item.text_content(timeout=1000) or "").strip()
                if _dropdown_option_matches(option_name, text):
                    item.click(timeout=2000)
                    logger.info(f"[OK] 选择选项: {option_name} (精确: '{text}')")
                    return True
        except Exception:
            continue

    return False


def _get_first_option_in_dropdown(page: Page) -> str:
    """在已打开的下拉框中取第一个可见选项的文本。不点击，仅读取内容。"""
    for css in _DROPDOWN_SELECTORS:
        try:
            items = page.locator(css)
            cnt = items.count()
            for i in range(cnt):
                item = items.nth(i)
                if not item.is_visible():
                    continue
                text = (item.text_content(timeout=1000) or "").strip()
                if text and not _is_dropdown_pagination_text(text):
                    return text
        except Exception:
            continue
    return ""


def _select_dropdown_by_label(page: Page, label_text: str, option_name: str) -> bool:
    """
    根据 label 定位下拉框并选择选项（单选/多选兼容）。
    选择器和流程与框架 common/helpers.py._select_dropdown_by_label 对齐：
      1. .h-form-item → .h-form-item-label 精确匹配
      2. 点击 .h-select-input → 首屏查找
      3. 首屏未找到 → 搜索 + 分页查找
    """
    form_items = page.locator(".h-form-item").all()
    target_item = None

    # 精确匹配
    for item in form_items:
        label = item.locator(".h-form-item-label").first.text_content(timeout=1000)
        if label and label.strip() == label_text:
            target_item = item
            break
    # 模糊匹配回退
    if not target_item:
        for item in form_items:
            label = item.locator(".h-form-item-label").first.text_content(timeout=1000)
            if label and label_text in label and label.strip() != label_text:
                target_item = item
                break

    if target_item:
        select_input = target_item.locator(".h-select-input").first
        if select_input.count() > 0:
            try:
                select_input.click(timeout=3000)
            except Exception:
                select_input.click(force=True)
            page.wait_for_timeout(500)

            # 首屏查找
            if _find_option_in_dropdown(page, option_name):
                logger.info(f"[OK] 选择 {option_name}（label: {label_text}）")
                return True

            # 🔧 修复：搜索关键词输入到 select_input，而非下拉框内部搜索框（避免匹配分页输入框）
            search_keyword = option_name.split(":")[0] if ":" in option_name else option_name
            try:
                select_input.fill(search_keyword, timeout=2000)
            except Exception:
                select_input.type(search_keyword, delay=50)
            page.wait_for_timeout(800)

            if _find_option_in_dropdown(page, option_name):
                logger.info(f"[OK] 选择 {option_name}（搜索: {search_keyword}）")
                return True

            # 🔧 修复：分页查找 - 翻页继续查找
            for page_num in range(2, 10):  # 最多翻 10 页
                next_btn = page.locator(".h-page-next:not(.h-page-disabled)").first
                if next_btn.count() == 0:
                    break
                next_btn.click()
                page.wait_for_timeout(500)
                if _find_option_in_dropdown(page, option_name):
                    logger.info(f"[OK] 选择 {option_name}（第 {page_num} 页）")
                    return True

            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            logger.warning(f"[WARN] 下拉框 {label_text} 未找到选项 {option_name}")
            return False

    # textbox role 回退
    textbox = page.get_by_role("textbox", name=label_text)
    if textbox.count() > 0:
        try:
            textbox.click(timeout=3000)
        except Exception:
            textbox.click(force=True)
        page.wait_for_timeout(500)
        if _find_option_in_dropdown(page, option_name):
            return True
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)

    logger.error(f"[ERROR] 未找到 label='{label_text}' 的下拉框")
    return False


def _fill_input_by_label(page: Page, label_text: str, value: str) -> bool:
    """
    根据 label 定位输入框并填入值。
    选择器与框架 common/helpers.py._fill_input_by_label 对齐：
      .h-form-item → .h-form-item-label 精确匹配 → input:not([readonly]):not(.h-select-input)
    """
    form_items = page.locator(".h-form-item").all()

    # 精确匹配
    for item in form_items:
        label = item.locator(".h-form-item-label").first.text_content(timeout=1000)
        if label and label.strip() == label_text:
            input_el = item.locator("input:not([readonly]):not(.h-select-input)").first
            if input_el.count() > 0:
                try:
                    input_el.fill(value, timeout=3000)
                except Exception:
                    input_el.fill(value, force=True)
                logger.info(f"[OK] 输入 {label_text}: {value}")
                return True

    # 模糊匹配回退
    for item in form_items:
        label = item.locator(".h-form-item-label").first.text_content(timeout=1000)
        if label and label_text in label and label.strip() != label_text:
            input_el = item.locator("input:not([readonly]):not(.h-select-input)").first
            if input_el.count() > 0:
                try:
                    input_el.fill(value, timeout=3000)
                except Exception:
                    input_el.fill(value, force=True)
                logger.info(f"[OK] 输入 {label_text}: {value}（模糊匹配）")
                return True

    logger.error(f"[ERROR] 未找到 label='{label_text}' 的输入框")
    return False


def _get_textbox_value_by_label(page: Page, label_text: str) -> str:
    """
    根据 label 读取输入框/下拉框的当前值。
    与框架 common/helpers.py._get_textbox_value_by_label 对齐。
    """
    form_items = page.locator(".h-form-item").all()

    for item in form_items:
        label = item.locator(".h-form-item-label").first.text_content(timeout=1000)
        if label and label.strip() == label_text:
            # 先试下拉框
            select_input = item.locator(".h-select-input").first
            if select_input.count() > 0:
                try:
                    return select_input.input_value(timeout=2000)
                except Exception:
                    pass
            # 再试普通输入框
            input_el = item.locator("input:not([readonly]):not(.h-select-input)").first
            if input_el.count() > 0:
                try:
                    return input_el.input_value(timeout=2000)
                except Exception:
                    pass

    # 模糊匹配回退
    for item in form_items:
        label = item.locator(".h-form-item-label").first.text_content(timeout=1000)
        if label and label_text in label and label.strip() != label_text:
            select_input = item.locator(".h-select-input").first
            if select_input.count() > 0:
                try:
                    return select_input.input_value(timeout=2000)
                except Exception:
                    pass
            input_el = item.locator("input:not([readonly]):not(.h-select-input)").first
            if input_el.count() > 0:
                try:
                    return input_el.input_value(timeout=2000)
                except Exception:
                    pass

    return ""


def _click_button_by_role(page: Page, button_name: str) -> bool:
    """通过 get_by_role 点击按钮（与框架模板一致）"""
    btn = page.get_by_role("button", name=button_name)
    try:
        btn.first.wait_for(state="attached", timeout=10000)
    except Exception:
        logger.warning(f"[WARN] 按钮未找到: {button_name}")
        return False
    try:
        btn.first.click(timeout=5000)
    except Exception:
        btn.first.click(force=True)
    logger.info(f"[OK] 点击按钮: {button_name}")
    return True


# ─────────────────────────────────────────────────────────────
#  4. SSH / 本地日志抓取
# ─────────────────────────────────────────────────────────────

def capture_ssh_log(
    log_path: str,
    host: str = "127.0.0.1",
    port: int = 22,
    username: str = "",
    password: str = "",
    tail_lines: int = 200,
    grep_keyword: Optional[str] = None,
    start_line: Optional[int] = None,
) -> str:
    """通过 paramiko SSH 抓取远程服务器日志。

    start_line: 指定起始行号时，使用 tail -n +N 从该行取到末尾（增量捕获）。
    不指定时，使用 tail -n N 取最后 N 行（全量捕获）。
    """
    try:
        import paramiko
    except ImportError:
        raise ImportError("需要 paramiko: pip install paramiko")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, port=port, username=username, password=password, timeout=10)
        if start_line is not None:
            cmd = f"tail -n +{start_line} {log_path}"
        else:
            cmd = f"tail -n {tail_lines} {log_path}"
        if grep_keyword:
            cmd += f" | grep -E '{grep_keyword}'"
        _, stdout, stderr = client.exec_command(cmd, timeout=15)
        log_text = stdout.read().decode("utf-8", errors="replace")
        err = stderr.read().decode("utf-8", errors="replace")
        if err:
            logger.warning(f"SSH stderr: {err}")
        logger.info(f"[OK] SSH 日志 {log_path}: {len(log_text)} 字符")
        return log_text
    finally:
        client.close()


def capture_local_log(
    log_path: str,
    tail_lines: int = 200,
    grep_keyword: Optional[str] = None,
    start_line: Optional[int] = None,
) -> str:
    """读取本地日志文件。

    start_line: 指定起始行号时，从该行取到末尾（增量捕获）。行号从 1 开始。
    不指定时，取最后 tail_lines 行。
    """
    if not os.path.exists(log_path):
        raise FileNotFoundError(f"日志文件不存在: {log_path}")
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    if start_line is not None and start_line <= len(lines):
        tail = lines[start_line - 1:]  # 行号从 1 开始
    else:
        tail = lines[-tail_lines:] if len(lines) > tail_lines else lines
    log_text = "".join(tail)
    if grep_keyword:
        log_text = "\n".join(line for line in log_text.splitlines() if grep_keyword in line)
    logger.info(f"[OK] 本地日志 {log_path}: {len(log_text)} 字符")
    return log_text


# ─────────────────────────────────────────────────────────────
#  5. 执行引擎（主控类）
# ─────────────────────────────────────────────────────────────

class CDPExecutor:
    """
    纯 Python CDP 执行引擎，选择器模式对齐框架 common/helpers.py。

    两种定位模式：
      1. 框架模式（默认）：.h-form-item + label 精确匹配 — 与测试用例一致
      2. Stagehand 模式：JS Locator 翻译 — 用于探勘结果中的复杂定位

    用法:
        with CDPExecutor() as executor:
            executor.load_fields("explore_result.json")
            executor.fill_field("客户标识", "A3060210020001")
            executor.select_single("客户标识类型", "入账账号")
            executor.click_button("查询")
            executor.wait_and_capture("manager.log")
    """

    def __init__(
        self,
        cdp_url: str = None,
        target_url: Optional[str] = None,
        ssh_config: Optional[Dict] = None,
        timeout: int = 30000,
        config_path: Optional[str] = None,
    ):
        # 读取 config.json（如果指定了路径或需要自动发现 cdp_url）
        from skill_config import get_config
        self._presets = {}
        if config_path or cdp_url is None:
            cfg = get_config(config_path)
            if cdp_url is None:
                cdp_url = cfg.cdp_url
            self._presets = cfg._presets
            # 如果未显式指定 target_url，从 config 的 menu_url 自动填充
            if target_url is None:
                target_url = cfg.menu_url
            # 如果未显式传入 ssh_config，从 config.json 自动填充
            if not ssh_config:
                ssh_config = cfg.ssh_config
        self.cdp_url = cdp_url
        self.target_url = target_url
        self.ssh_config = ssh_config or {}
        self.timeout = timeout

        self._pw = None
        self._browser = None
        self._page: Optional[Page] = None
        self._fields: List[dict] = []
        self._field_groups: List[dict] = []
        self._locator_cache: Dict[str, Locator] = {}
        self._log_marker: Optional[int] = None  # 日志行号标记，用于增量捕获
        self._skip_conditional_groups: bool = False  # 为 True 时跳过 conditional 字段组（双路空查用）

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.disconnect()

    # ── 连接管理 ──

    def connect(self):
        self._pw, self._browser, self._page = connect_cdp(
            self.cdp_url, self.target_url, self.timeout
        )
        logger.info(f"[OK] CDP 已连接: {self._page.url}")

    def disconnect(self):
        if self._browser:
            try:
                self._browser.disconnect()
                logger.info("[OK] CDP 已断开（浏览器保持运行）")
            except Exception as e:
                logger.debug(f"断开忽略: {e}")
        if self._pw:
            self._pw.stop()
        self._browser = self._page = self._pw = None

    @property
    def page(self) -> Page:
        if not self._page:
            raise RuntimeError("未连接，先调用 connect()")
        return self._page

    def health_check(self) -> dict:
        """
        检查浏览器会话健康状态。
        返回 {"ok": bool, "issues": [...]} 。
        """
        issues = []
        try:
            if not self._page:
                issues.append("page 未连接")
                return {"ok": False, "issues": issues}

            # 检查页面是否可交互
            try:
                self._page.evaluate("document.readyState", timeout=5000)
            except Exception:
                issues.append("页面不可交互（JS 执行超时）")

            # 检查 URL 是否正常
            url = self._page.url
            if not url or url == "about:blank":
                issues.append(f"页面 URL 异常: {url}")

            # 检查是否有未关闭的弹窗遮挡
            modal_selectors = [".h-modal", ".h-dialog", ".h-message-box", "[role='dialog']"]
            for css in modal_selectors:
                try:
                    modal = self._page.locator(css).first
                    if modal.count() > 0 and modal.is_visible():
                        issues.append(f"有未关闭的弹窗: {css}")
                        break
                except Exception:
                    continue

        except Exception as e:
            issues.append(f"健康检查异常: {e}")

        return {"ok": len(issues) == 0, "issues": issues}

    def recover_session(self, target_url: str = None) -> bool:
        """
        恢复浏览器会话到可用状态。
        步骤：关闭所有弹窗 → 导航到空白页 → 重新导航到目标菜单。
        返回 True 表示恢复成功。
        """
        logger.info("[RECOVER] 开始恢复浏览器会话...")

        # 1. 关闭所有弹窗
        try:
            dismissed = self._dismiss_overlays()
            if dismissed:
                logger.info("[RECOVER] 已关闭残留弹窗")
        except Exception as e:
            logger.warning(f"[RECOVER] 关闭弹窗时出错: {e}")

        # 2. 尝试评估页面状态
        try:
            ready = self._page.evaluate("document.readyState", timeout=5000)
            logger.info(f"[RECOVER] 页面状态: {ready}")
        except Exception:
            logger.warning("[RECOVER] 页面不可交互，尝试刷新")
            try:
                self._page.reload(timeout=10000)
                self._page.wait_for_timeout(2000)
            except Exception:
                logger.error("[RECOVER] 页面刷新失败")
                return False

        # 3. 导航到目标菜单
        url = target_url or self.target_url
        if url:
            try:
                self.navigate(url, verify=True)
                logger.info(f"[RECOVER] 已重新导航到: {url}")
                return True
            except Exception as e:
                logger.error(f"[RECOVER] 导航失败: {e}")
                return False

        logger.info("[RECOVER] 无目标 URL，跳过导航")
        return True

    # ── 字段加载 ──

    def load_fields(self, source):
        """加载 Stagehand 探勘结果（JSON 文件路径 或 list[dict]）"""
        if isinstance(source, (str, Path)):
            with open(source, "r", encoding="utf-8") as f:
                self._fields = json.load(f)
            logger.info(f"[OK] 加载探勘结果: {len(self._fields)} 个字段 ← {source}")
        elif isinstance(source, list):
            self._fields = source
            logger.info(f"[OK] 加载探勘结果: {len(self._fields)} 个字段")
        else:
            raise TypeError(f"source 必须是文件路径或 list: {type(source)}")
        self._locator_cache.clear()

    def load_field_groups(self, groups: List[dict]):
        """加载字段组配置（如客户标识类型必须先于客户标识填入）"""
        self._field_groups = groups or []
        if self._field_groups:
            logger.info(f"[OK] 加载字段组: {len(self._field_groups)} 组")

    def set_skip_conditional_groups(self, skip: bool):
        """设置是否跳过 conditional 字段组。

        当 skip=True 时，标记为 conditional=True 的字段组（如客户标识组）
        不会被 _ensure_field_group 自动填入。用于双路查询的空查分支。
        """
        self._skip_conditional_groups = skip
        if skip:
            logger.info("[OK] 已启用跳过 conditional 字段组模式（双路空查）")

    def _ensure_field_group(self, field_label: str):
        """若字段属于某个 group，按 order 先填入整组字段（已填的跳过）。

        当 group 标记为 conditional=True 且 _skip_conditional_groups=True 时，
        跳过该组的自动填入（用于双路查询的空查分支）。
        """
        for group in self._field_groups:
            if field_label in group.get('fields', []):
                # conditional 字段组在跳过模式下不自动填入
                if group.get('conditional', False) and self._skip_conditional_groups:
                    logger.info(f"[GROUP] {group['name']}: conditional 组，跳过模式下不填入")
                    return
                logger.info(f"[GROUP] {group['name']}: 按顺序填入 {group['order']}")
                for label in group['order']:
                    field = self.get_field(label)
                    if not field:
                        continue
                    current_val = self.get_field_value(label)
                    if current_val and current_val.strip():
                        logger.info(f"  [SKIP] {label} 已有值: {current_val}")
                        continue
                    ftype = field.get('type', '')
                    if ftype == 'input':
                        # 从 presets 推断默认值
                        if '客户标识' in label:
                            val = self._presets.get('customer_id', '')
                            if not val:
                                raise ValueError(f"字段「{label}」需要 customer_id，但 config.json presets 中未配置")
                            self.fill_field(label, val)
                        else:
                            logger.warning(f"  [WARN] input 字段 {label} 无默认值，跳过")
                    elif ftype in ('single-select', 'multi-select'):
                        if '客户标识类型' in label:
                            val = self._presets.get('customer_id_type', '')
                            if not val:
                                raise ValueError(f"字段「{label}」需要 customer_id_type，但 config.json presets 中未配置")
                            self.select_single(label, val)
                        else:
                            logger.warning(f"  [WARN] 下拉字段 {label} 无默认选项，跳过")
                    else:
                        logger.warning(f"  [WARN] 未知类型 {ftype}: {label}")
                return

    def get_field(self, label: str) -> Optional[dict]:
        for f in self._fields:
            if f.get("label", "").strip() == label:
                return f
        return None

    def get_fields_by_type(self, field_type: str) -> List[dict]:
        return [f for f in self._fields if f.get("type") == field_type]

    # ── Locator 解析（Stagehand 模式，带缓存）──

    def resolve_locator(self, label: str) -> Locator:
        """按 label 查找字段并翻译 JS Locator（Stagehand 模式）"""
        if label in self._locator_cache:
            return self._locator_cache[label]
        field = self.get_field(label)
        if not field:
            raise KeyError(f"字段未找到: {label}，可用: {[f['label'] for f in self._fields]}")
        js_loc = field.get("locator", "")
        if not js_loc:
            raise ValueError(f"字段 '{label}' 无 locator 定义")
        locator = translate_locator(self.page, js_loc)
        self._locator_cache[label] = locator
        return locator

    # ── 业务动作（框架模式优先）──

    def fill_field(self, label: str, value: str) -> str:
        """填写输入框。value 为空或占位符时按语义生成。返回实际填入的值。
        如果字段是树选择弹窗模式（disabled input + 选择按钮），走弹窗流程。"""
        # 树选择弹窗字段：不能直接填，要走弹窗（不经过 _resolve_fill_value，值直接传到弹窗匹配）
        if self._is_tree_select_modal(label):
            return self._select_tree_modal(label, value)

        actual = self._resolve_fill_value(label, value)
        if not _fill_input_by_label(self.page, label, actual):
            # 回退：Stagehand locator
            logger.info(f"框架模式未找到 '{label}'，尝试 Stagehand locator")
            locator = self.resolve_locator(label)
            locator.fill(actual)
            logger.info(f"[OK] 输入 {label}: {actual}（Stagehand）")
        return actual

    def _resolve_fill_value(self, label: str, value: str) -> str:
        """为空/占位符的输入框按语义生成值。"""
        if value and value not in ("_", "__FIRST_OPTION__"):
            return value
        import time
        today = time.strftime('%Y%m%d')
        year = time.strftime('%Y')
        ts = str(int(time.time()))[-8:]
        if "日期" in label:
            if "起始" in label or "开始" in label:
                return f"{year}-01-01"
            if "结束" in label:
                return f"{year}-12-31"
            return f"{year}-01-01"
        if "编号" in label or "代码" in label:
            return f"AUTO_{ts}"
        if "机构" in label:
            return "000"
        if "金额" in label or "价格" in label:
            return "1000"
        if "份额" in label or "数量" in label or "笔数" in label:
            return "100"
        if "比例" in label or "费率" in label or "百分比" in label:
            return "50"
        if "名称" in label or "姓名" in label or "户名" in label:
            return f"自动化测试{today[4:]}"
        if "手机" in label or "电话" in label:
            return "13800138000"
        if "备注" in label or "说明" in label:
            return "自动化测试备注"
        return f"AUTO_{ts}"

    def _is_tree_select_modal(self, label: str) -> bool:
        """检测字段是否为树选择弹窗模式（disabled input + 选择 按钮）。"""
        form_items = self.page.locator(".h-form-item").all()
        for item in form_items:
            try:
                lbl = item.locator(".h-form-item-label").first.text_content(timeout=500).strip()
            except Exception:
                continue
            if label in lbl:
                inp = item.locator("input").first
                btn = item.locator("button.choose-btn").first
                return inp.count() > 0 and btn.count() > 0 and inp.is_disabled()
        return False

    def _select_tree_modal(self, label: str, value: str = "") -> str:
        """通过选择弹窗选择值（如交易机构）。点击 选择 → 弹窗表格选行 → 确定。
        返回实际选中的值文本。"""
        form_items = self.page.locator(".h-form-item").all()
        target_item = None
        for item in form_items:
            try:
                lbl = item.locator(".h-form-item-label").first.text_content(timeout=1000).strip()
            except Exception:
                continue
            if label in lbl:
                target_item = item
                break
        if not target_item:
            raise RuntimeError(f"未找到字段: {label}")

        choose_btn = target_item.locator("button.choose-btn").first
        if choose_btn.count() == 0:
            raise RuntimeError(f"字段 '{label}' 没有选择按钮")

        logger.info(f"[TREE-SELECT] {label}: 点击选择按钮")
        # 先确保没有残留的可见弹窗
        for _wait in range(10):
            has_visible = False
            for m in self.page.locator(".h-modal-wrap").all():
                try:
                    cls = m.get_attribute("class") or ""
                    if "h-modal-hidden" not in cls and m.is_visible():
                        has_visible = True
                        break
                except:
                    continue
            if not has_visible:
                break
            self.page.wait_for_timeout(300)
        # 用 JS click（Playwright click 对同页面多个 tree-select 按钮可能失效）
        choose_btn.evaluate("el => el.click()")
        self.page.wait_for_timeout(1000)

        # 等待弹窗出现：遍历所有 .h-modal-wrap 找到可见的那个
        modal = None
        for _attempt in range(20):
            all_modals = self.page.locator(".h-modal-wrap").all()
            for m in all_modals:
                try:
                    cls = m.get_attribute("class") or ""
                    if "h-modal-hidden" not in cls and m.is_visible():
                        modal = m
                        break
                except:
                    continue
            if modal:
                break
            self.page.wait_for_timeout(500)
        if not modal:
            raise RuntimeError("选择弹窗未出现")

        # 匹配并点击行/树节点
        selected = None
        keyword = value.split(":")[0] if ":" in value else value
        is_sentinel = keyword in ("__FIRST_OPTION__", "_", "") or keyword.startswith("AUTO_")

        # 尝试搜索框（如有）
        search_input = modal.locator('input[placeholder*="查询"], input[placeholder*="搜索"]').first
        if not is_sentinel and search_input.count() > 0 and search_input.is_visible():
            search_input.fill(keyword)
            self.page.wait_for_timeout(800)

        # 尝试 .h-tree-title（树节点）
        titles = modal.locator(".h-tree-title")
        for i in range(titles.count()):
            t = titles.nth(i)
            try:
                txt = (t.text_content(timeout=1000) or "").strip()
            except Exception:
                continue
            if is_sentinel or keyword in txt or (":" in value and value.split(":")[0] in txt):
                t.click(force=True, timeout=3000)
                selected = txt
                logger.info(f"[TREE-SELECT] {label}: 选中 '{txt}'")
                break

        # 兜底：表格式弹窗（选第一行）
        if not selected:
            row = modal.locator("table tbody tr").first
            if row.count() > 0:
                row.click(force=True, timeout=3000)
                selected = (row.text_content(timeout=2000) or "").strip().split()[0]

        if not selected:
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(500)
            raise RuntimeError("选择弹窗中未找到可选行/节点")

        # 点确定
        self.page.wait_for_timeout(300)
        ok_btn = modal.locator("button:has-text('确定')").first
        if ok_btn.count() > 0 and ok_btn.is_visible():
            ok_btn.click(force=True, timeout=3000)
            logger.info(f"[TREE-SELECT] {label}: 点击确定")
        else:
            self.page.keyboard.press("Escape")
            logger.warning(f"[TREE-SELECT] {label}: 未找到确定按钮，按 Escape 关闭")

        # 等待弹窗完全关闭
        self.page.wait_for_timeout(500)
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(500)
        for _wait in range(10):
            has_visible = False
            for m in self.page.locator(".h-modal-wrap").all():
                try:
                    cls = m.get_attribute("class") or ""
                    if "h-modal-hidden" not in cls and m.is_visible():
                        has_visible = True
                        break
                except:
                    continue
            if not has_visible:
                break
            self.page.wait_for_timeout(300)
        logger.info(f"[OK] {label}: 选择 = '{selected}'")
        return selected

    def click_button(self, label: str = "查询"):
        """点击按钮。优先 get_by_role，回退 Stagehand locator。"""
        if not _click_button_by_role(self.page, label):
            logger.info(f"get_by_role 未找到 '{label}'，尝试 Stagehand locator")
            locator = self.resolve_locator(label)
            try:
                locator.click(timeout=5000)
            except Exception:
                locator.click(force=True)
            logger.info(f"[OK] 点击: {label}（Stagehand）")

    def click_text(self, text: str):
        """点击包含指定文本的任意元素（表格行、列表项等）。"""
        locator = self.page.get_by_text(text, exact=True)
        if locator.count() > 0:
            locator.first.click(timeout=5000)
            logger.info(f"[OK] 点击文本: {text}")
            return
        # 回退：locator 部分匹配
        locator = self.page.locator(f"text={text}")
        if locator.count() > 0:
            locator.first.click(timeout=5000)
            logger.info(f"[OK] 点击文本: {text}（部分匹配）")
            return
        raise KeyError(f"未找到包含文本 '{text}' 的元素")

    def fill_by_placeholder(self, placeholder: str, value: str):
        """通过 placeholder 定位输入框并填入值（用于弹窗内的输入框）。"""
        locator = self.page.locator(f"input[placeholder='{placeholder}']").first
        locator.click(timeout=5000)
        locator.fill(value)
        logger.info(f"[OK] 输入(placeholder={placeholder}): {value}")

    def select_single(self, label: str, option: str) -> str:
        """单选下拉框。打开下拉框后尝试匹配 option，失败则选第一个。
        识别哨兵值 __FIRST_OPTION__ 直接选第一个。
        返回实际选中的选项文本。"""
        # 哨兵值：直接选第一个
        if _allow_first_option_fallback(option):
            logger.info(f"[FIRST] {label}: 哨兵值，直接选第一个选项")
            return self._select_first_option(label)

        # 尝试精确匹配
        if _select_dropdown_by_label(self.page, label, option):
            return option

        # 匹配失败：关闭已打开的下拉框，选第一个可见选项兜底
        logger.error(f"[ERROR] {label}: 未找到明确选项 '{option}'，不再回退选第一个")
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(300)
        raise ValueError(f"下拉框 {label} 未找到选项: {option}")

    def _select_first_option(self, label: str) -> str:
        """打开下拉框并选中第一个可见选项。返回选中的选项文本。
        如果所有策略失败，抛出 RuntimeError。"""
        # 先关闭可能遮挡的弹窗
        self._dismiss_overlays()

        # 策略 1: 框架模式 — .h-form-item + label 定位
        form_items = self.page.locator(".h-form-item").all()
        target_item = None
        for item in form_items:
            try:
                label_text = (item.locator(".h-form-item-label").first.text_content(timeout=1000) or "").strip()
            except Exception:
                continue
            if label_text == label:
                target_item = item
                break
        # 模糊匹配回退
        if not target_item:
            for item in form_items:
                try:
                    label_text = (item.locator(".h-form-item-label").first.text_content(timeout=1000) or "").strip()
                except Exception:
                    continue
                if label in label_text and label_text != label:
                    target_item = item
                    break

        if target_item:
            select_input = target_item.locator(".h-select-input").first
            if select_input.count() > 0:
                try:
                    select_input.click(timeout=3000)
                except Exception:
                    select_input.click(force=True)
                self.page.wait_for_timeout(500)
                first_text = _get_first_option_in_dropdown(self.page)
                if first_text:
                    if _find_option_in_dropdown(self.page, first_text):
                        logger.info(f"[OK] {label}: 选中第一个选项 '{first_text}'")
                        return first_text

        # 策略 1 未成功 → 关闭半开的下拉框，确保干净状态
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(300)

        # 策略 2: Stagehand locator 回退
        logger.info(f"框架模式无法打开 '{label}' 下拉框，尝试 Stagehand locator")
        locator = None
        try:
            locator = self.resolve_locator(label)
            try:
                locator.click(timeout=3000)
            except Exception:
                locator.click(force=True)
        except Exception:
            logger.warning(f"Stagehand 也无法定位 '{label}'")
            locator = None

        if locator is not None:
            self.page.wait_for_timeout(500)
            first_text = _get_first_option_in_dropdown(self.page)
            if first_text:
                if _find_option_in_dropdown(self.page, first_text):
                    logger.info(f"[OK] {label}: Stagehand 选中第一个选项 '{first_text}'")
                    return first_text

        # 最终重试：关闭弹窗 + 刷新后重试
        logger.info(f"'{label}' 下拉框选择失败，关闭弹窗后重试")
        self._dismiss_overlays()
        self.page.keyboard.press("Escape")
        self.page.wait_for_timeout(500)
        # 重试策略 1
        if target_item:
            select_input = target_item.locator(".h-select-input").first
            if select_input.count() > 0:
                try:
                    select_input.click(timeout=3000)
                except Exception:
                    select_input.click(force=True)
                self.page.wait_for_timeout(500)
                first_text = _get_first_option_in_dropdown(self.page)
                if first_text:
                    if _find_option_in_dropdown(self.page, first_text):
                        logger.info(f"[OK] {label}: 重试成功 '{first_text}'")
                        return first_text

        raise RuntimeError(f"无法为 '{label}' 选中任何选项：下拉框无法打开或无选项")

    def select_multi(self, label: str, options: List[str]):
        """多选下拉框：逐个选择选项，最后点空白处收起。"""
        for opt in options:
            if not _select_dropdown_by_label(self.page, label, opt):
                logger.warning(f"[WARN] 多选选项失败: {opt}")
            self.page.wait_for_timeout(300)
        self.click_outside()
        logger.info(f"[OK] 多选 {label}: {options}")

    def click_outside(self):
        """点击空白处收起弹窗/下拉框（页面中间位置）"""
        viewport = self.page.viewport_size
        cx = viewport["width"] // 2 if viewport else 500
        cy = viewport["height"] // 2 if viewport else 300
        self.page.mouse.click(cx, cy)
        self.page.wait_for_timeout(300)

    def navigate(self, menu_url: str, verify: bool = True):
        """URL 直跳导航到菜单页面。

        verify=True 时，导航后检查页面是否包含预期内容（.h-form-item / .h-page / table），
        若三者均不存在则抛出 RuntimeError，防止静默跳转到错误页面。
        """
        base = self.page.url.split("#")[0]
        self.page.goto(f"{base}#{menu_url}", wait_until="domcontentloaded")
        self.page.wait_for_timeout(3000)
        logger.info(f"[OK] 导航到: #{menu_url}")

        if verify:
            has_form = self.page.locator('.h-form-item').count() > 0
            has_page = self.page.locator('.h-page').count() > 0
            has_table = self.page.locator('table').count() > 0
            if not (has_form or has_page or has_table):
                raise RuntimeError(
                    f"导航后页面无预期内容（无 .h-form-item / .h-page / table），"
                    f"可能跳转失败。当前 URL: {self.page.url}"
                )
            logger.info(
                f"[OK] 导航验证通过: form={has_form}, page={has_page}, table={has_table}"
            )

    def login(self, username: str, password: str, login_url: str):
        """自动登录 HUI 系统。

        流程：导航到登录页 → 填写用户名/密码 → 点击登录 → 等待"欢迎" → 处理弹窗。

        Args:
            username: 登录用户名
            password: 登录密码
            login_url: 登录页面完整 URL（如 http://10.20.150.149:10622/console/#/login）

        Raises:
            RuntimeError: 登录后未检测到"欢迎"文本
        """
        logger.info(f"导航到登录页: {login_url}")
        self.page.goto(login_url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2000)

        logger.info("填写登录表单...")
        try:
            self.page.get_by_role("textbox", name="用户名").fill(username)
            self.page.get_by_role("textbox", name="密码").fill(password)
        except Exception as e:
            # 登录页可能已经不在（已登录状态）
            logger.warning(f"登录表单填写失败（可能已登录）: {e}")
            return

        logger.info("点击登录按钮...")
        self.page.get_by_role("button", name="登录").click()

        # 等待登录成功标志
        try:
            self.page.get_by_text("欢迎").wait_for(state="visible", timeout=10000)
            logger.info("[OK] 登录成功：检测到'欢迎'")
        except Exception:
            raise RuntimeError("登录失败：未检测到'欢迎'文本，请检查账号密码是否正确")

        # 处理"流程状态刷新异常"弹窗
        self._handle_login_popup()

    def _handle_login_popup(self):
        """处理登录后的「流程状态刷新异常」弹窗"""
        self.page.wait_for_load_state("networkidle")
        self.page.wait_for_timeout(2000)
        try:
            popup_text = self.page.get_by_text("流程状态刷新异常,是否继续刷新？")
            confirm_btn = self.page.get_by_role("button", name="确定")
            if popup_text.is_visible(timeout=8000):
                logger.info("检测到「流程状态刷新异常」弹窗，点击确定")
                confirm_btn.wait_for(state="visible", timeout=5000)
                confirm_btn.click()
                popup_text.wait_for(state="hidden", timeout=5000)
                logger.info("[OK] 弹窗已关闭")
        except Exception as exc:
            logger.debug(f"处理弹窗时出错（可能不存在弹窗）: {exc}")

    def _dismiss_overlays(self) -> bool:
        """关闭页面上所有可见的模态弹窗/对话框/通知。

        HUI 系统常见的弹窗模式：
          - .h-modal / .h-dialog / .h-message-box（HUI 组件）
          - .el-dialog__wrapper / .el-message-box__wrapper（Element UI）
          - 通用 role="dialog"

        策略：先找弹窗内的确认按钮（确定/取消/关闭/我知道了），找不到则按 Escape。
        返回 True 表示关闭了至少一个弹窗。
        """
        dismissed = False
        modal_selectors = [
            ".h-modal",
            ".h-dialog",
            ".h-message-box",
            ".el-dialog__wrapper",
            ".el-message-box__wrapper",
            "[role='dialog']",
        ]
        confirm_texts = ["确定", "取消", "关闭", "我知道了"]

        for css in modal_selectors:
            try:
                modal = self.page.locator(css).first
                if modal.count() == 0:
                    continue
                if not modal.is_visible():
                    continue
                # 跳过树选择弹窗（含 .h-tree 的模态框是合法交互，不是遮挡物）
                if modal.locator(".h-tree").count() > 0:
                    continue
                # 弹窗内的确认按钮
                for btn_text in confirm_texts:
                    btn = modal.locator(f"button:has-text('{btn_text}')").first
                    if btn.count() > 0 and btn.is_visible():
                        btn.click(timeout=3000)
                        self.page.wait_for_timeout(500)
                        logger.info(f"[DISMISS] 关闭弹窗（{btn_text}）: {css}")
                        dismissed = True
                        break
                else:
                    # 弹窗存在但无确认按钮 → Escape
                    self.page.keyboard.press("Escape")
                    self.page.wait_for_timeout(300)
                    logger.info(f"[DISMISS] 按 Escape 关闭弹窗: {css}")
                    dismissed = True
            except Exception:
                continue
        return dismissed

    def collapse_sidebar(self):
        """收起侧边栏（对齐框架 _collapse_sidebar）"""
        sidebar = self.page.locator(".h-sidebar-wrapper.openedSidebar")
        if sidebar.count() > 0:
            toggle = sidebar.locator(".h-sidebar-toggle")
            if toggle.count() > 0:
                toggle.first.click()
                return
            self.page.evaluate("""() => {
                const s = document.querySelector('.h-sidebar-wrapper.openedSidebar');
                if (s) { s.classList.remove('openedSidebar'); s.classList.add('closedSidebar'); }
            }""")

    def reset_form(self):
        """点击重置按钮"""
        if _click_button_by_role(self.page, "重置"):
            self.page.wait_for_timeout(500)
            logger.info("[OK] 表单已重置")
        else:
            logger.warning("[WARN] 未找到重置按钮")

    def get_record_count(self) -> int:
        """获取表格记录数（对齐框架 _get_record_count）"""
        try:
            el = self.page.wait_for_selector(".h-page", timeout=3000)
            text = el.text_content()
            m = re.search(r"共\s*(\d+)\s*条", text)
            if m:
                return int(m.group(1))
        except Exception:
            pass
        try:
            return self.page.locator("table tbody tr").count()
        except Exception:
            return 0

    def get_field_value(self, label: str) -> str:
        """读取字段当前值（对齐框架 _get_textbox_value_by_label）"""
        val = _get_textbox_value_by_label(self.page, label)
        if val:
            return val
        # 回退 Stagehand
        try:
            locator = self.resolve_locator(label)
            return locator.input_value(timeout=2000)
        except Exception:
            return ""

    # ── 批量执行 ──

    @staticmethod
    def _sort_actions_by_deps(actions: List[Dict], dependencies: Dict[str, List[str]]) -> List[Dict]:
        """根据依赖关系对 actions 排序，确保级联字段的前置字段先执行。

        Args:
            actions: 动作列表
            dependencies: 依赖关系，如 {"产品代码": ["TA代码"]} 表示产品代码依赖 TA代码

        Returns:
            排序后的 actions 列表
        """
        actions = list(actions)  # 不修改原列表
        for dep_label, prereqs in dependencies.items():
            dep_idx = next((i for i, a in enumerate(actions) if a.get('label') == dep_label), None)
            if dep_idx is None:
                continue
            prereq_indices = [i for i, a in enumerate(actions) if a.get('label') in prereqs]
            if not prereq_indices:
                continue
            max_prereq = max(prereq_indices)
            if max_prereq > dep_idx:
                # 把依赖字段移到所有前置字段之后
                dep_action = actions.pop(dep_idx)
                actions.insert(max_prereq, dep_action)
                logger.info(f"[DEPS] 调整顺序: {dep_label} 移到 {prereqs} 之后")
        return actions

    def execute_actions(self, actions: List[Dict], write_back_path: str = None):
        """
        按顺序执行动作指令列表。返回 {label: actual_value} 映射。
        若指定 write_back_path，执行完成后将实际值写回 case_plan.json。

        action 格式:
          {"action": "fill", "label": "客户标识", "value": "A3060210020001"}
          {"action": "select", "label": "TA代码", "value": "JS:嘉实基金"}
          {"action": "multi_select", "label": "TA代码", "values": ["JS:嘉实基金", "HT:华泰柏瑞"]}
          {"action": "click", "label": "查询"}
          {"action": "navigate", "url": "ifmCCxglDxfundTransCfmMenu"}
          {"action": "wait", "ms": 3000}
          {"action": "reset"}
          {"action": "click_outside"}
        """
        # 🔧 修复：依赖排序 - 级联字段（如 TA代码→产品代码）必须先选前置字段
        _DEPENDENCIES = {"产品代码": ["TA代码"]}
        actions = self._sort_actions_by_deps(actions, _DEPENDENCIES)

        actual_values = {}
        _form_actions = {"fill", "select", "multi_select"}

        # 强制清理残留弹窗（含上次运行遗留的树选择弹窗等）
        for _ in range(3):
            for btn_text in ["取消", "关闭"]:
                try:
                    btn = self.page.locator(f".h-modal-wrap button:has-text('{btn_text}')").first
                    if btn.count() > 0 and btn.is_visible():
                        btn.click(force=True, timeout=2000)
                        self.page.wait_for_timeout(500)
                except Exception:
                    pass
            self.page.keyboard.press("Escape")
            self.page.wait_for_timeout(300)

        for i, act in enumerate(actions):
            action_type = act.get("action", "")
            label = act.get("label", "")
            logger.info(f"[{i+1}/{len(actions)}] {action_type} {label}")

            # 表单交互前关闭可能遮挡的弹窗
            if action_type in _form_actions:
                self._dismiss_overlays()

            if action_type == "fill":
                self._ensure_field_group(label)
                actual = self.fill_field(label, act["value"])
                actual_values[label] = actual
            elif action_type == "fill_placeholder":
                self.fill_by_placeholder(label, act["value"])
                actual_values[label] = act["value"]
            elif action_type == "select":
                self._ensure_field_group(label)
                # tree-select 弹窗字段走 fill_field（内部自动检测 _is_tree_select_modal）
                if self._is_tree_select_modal(label):
                    actual = self.fill_field(label, act["value"])
                else:
                    actual = self.select_single(label, act["value"])
                actual_values[label] = actual
            elif action_type == "multi_select":
                self._ensure_field_group(label)
                self.select_multi(label, act["values"])
                actual_values[label] = ",".join(act["values"])
            elif action_type == "click":
                self._dismiss_overlays()
                self.page.keyboard.press("Escape")
                self.page.wait_for_timeout(200)
                self.click_button(label)
            elif action_type == "click_text":
                self.click_text(label)
            elif action_type == "navigate":
                self.navigate(act["url"])
            elif action_type == "reset":
                self.reset_form()
            elif action_type == "wait":
                self.page.wait_for_timeout(act.get("ms", 1000))
            elif action_type == "click_outside":
                self.click_outside()
            else:
                logger.warning(f"[WARN] 未知动作: {action_type}")

            self.page.wait_for_timeout(300)

        if write_back_path and actual_values:
            self._write_back(write_back_path, actual_values)
        return actual_values

    def _write_back(self, case_plan_path: str, actual_values: dict):
        """将实际填入的值写回 case_plan.json 的 form_fields.all。"""
        import json
        with open(case_plan_path, 'r', encoding='utf-8') as f:
            plan = json.load(f)

        form_fields = plan.get('form_fields', {})
        all_fields = form_fields.get('all', [])

        for field in all_fields:
            label = field.get('label', '')
            if label in actual_values:
                actual = actual_values[label]
                old_value = field.get('value', '')
                if actual != old_value:
                    field['value'] = actual
                    # 判断来源
                    old_source = field.get('value_source', '')
                    if old_source == 'preset' and actual != old_value:
                        field['value_source'] = 'first_option'
                    elif old_source in ('generated', '') and actual not in ('', '_'):
                        field['value_source'] = 'semantic'
                    logger.info(f"[WRITE-BACK] {label}: '{old_value}' => '{actual}' ({field['value_source']})")

        with open(case_plan_path, 'w', encoding='utf-8') as f:
            json.dump(plan, f, ensure_ascii=False, indent=2)
        logger.info(f"[WRITE-BACK] 已更新 {len(actual_values)} 个字段到 {case_plan_path}")

    # ── 等待与日志闭环 ──

    def mark_log_position(
        self,
        log_path: str,
        use_ssh: bool = True,
    ) -> int:
        """在操作执行前记录日志当前行数，作为增量捕获的起点。

        返回当前日志文件总行数。后续 wait_and_capture 会自动只取此行之后的新增内容。
        """
        if use_ssh:
            if not self.ssh_config:
                raise ValueError("use_ssh=True 但未配置 ssh_config")
            try:
                import paramiko
            except ImportError:
                raise ImportError("需要 paramiko: pip install paramiko")
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            try:
                client.connect(
                    self.ssh_config.get("host", "127.0.0.1"),
                    port=self.ssh_config.get("port", 22),
                    username=self.ssh_config.get("username", ""),
                    password=self.ssh_config.get("password", ""),
                    timeout=10,
                )
                _, stdout, stderr = client.exec_command(f"wc -l < {log_path}", timeout=10)
                line_count = int(stdout.read().decode("utf-8", errors="replace").strip())
                logger.info(f"[OK] 日志标记: {log_path} 当前 {line_count} 行")
                self._log_marker = line_count
                return line_count
            finally:
                client.close()
        else:
            if not os.path.exists(log_path):
                raise FileNotFoundError(f"日志文件不存在: {log_path}")
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                line_count = sum(1 for _ in f)
            logger.info(f"[OK] 日志标记: {log_path} 当前 {line_count} 行")
            self._log_marker = line_count
            return line_count

    def wait_and_capture(
        self,
        log_path: str,
        wait_ms: int = 3000,
        grep_keyword: Optional[str] = None,
        tail_lines: int = 200,
        use_ssh: bool = True,
    ) -> str:
        """等待接口响应后抓取后端日志。

        如果之前调用了 mark_log_position()，则只取增量（标记行之后的新增内容）。
        否则 fallback 取最后 tail_lines 行。
        """
        logger.info(f"等待 {wait_ms}ms 后抓取日志...")
        self.page.wait_for_timeout(wait_ms)

        start_line = None
        if self._log_marker is not None:
            start_line = self._log_marker + 1  # 从下一行开始
            logger.info(f"增量捕获: 从第 {start_line} 行开始")
            self._log_marker = None  # 单次使用，用完清除

        if use_ssh:
            if not self.ssh_config:
                raise ValueError("use_ssh=True 但未配置 ssh_config")
            return capture_ssh_log(
                log_path=log_path,
                host=self.ssh_config.get("host", "127.0.0.1"),
                port=self.ssh_config.get("port", 22),
                username=self.ssh_config.get("username", ""),
                password=self.ssh_config.get("password", ""),
                tail_lines=tail_lines,
                grep_keyword=grep_keyword,
                start_line=start_line,
            )
        return capture_local_log(log_path, tail_lines, grep_keyword, start_line=start_line)


# ─────────────────────────────────────────────────────────────
#  6. 命令行入口
# ─────────────────────────────────────────────────────────────

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="纯 Python CDP 执行引擎 — 对齐框架 helpers 选择器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 执行动作 + SSH 抓日志
  python cdp_executor.py -e result.json -a actions.json \\
      --ssh-log /opt/app/manager.log --ssh-host 10.0.0.1 --ssh-user admin

  # 测试 Locator 翻译（不连接浏览器）
  python cdp_executor.py --test-locator "page.locator('.h-form-item').filter({ hasText: '客户标识' }).getByRole('textbox')"
        """,
    )

    parser.add_argument("--config", default=None, help="config.json 路径（默认自动发现）")
    parser.add_argument("--cdp-url", default=None, help="CDP 调试地址（默认从 config.json 读取）")
    parser.add_argument("--target-url", default=None, help="目标页面 URL 片段")
    parser.add_argument("--navigate", default=None, dest="navigate_url",
                        help="导航到指定菜单路径（如 /console-dxtrust-vue/...），支持完整 URL（自动提取路径）")
    parser.add_argument("--login", action="store_true", help="自动登录 HUI 系统")
    parser.add_argument("--login-user", default=None, help="登录用户名（默认从 config.json 读取）")
    parser.add_argument("--login-pass", default=None, help="登录密码（默认从 config.json 读取）")
    parser.add_argument("--login-url", default=None, help="登录页面 URL（默认从 config.json 读取）")
    parser.add_argument("--health-check", action="store_true", help="检查浏览器会话健康状态（批量模式复用前调用）")
    parser.add_argument("--recover", action="store_true", help="恢复浏览器会话到可用状态（关闭弹窗 + 重新导航）")
    parser.add_argument("-e", "--explore", help="Stagehand 探勘结果 JSON 文件")
    parser.add_argument("-a", "--actions", help="动作指令 JSON 文件")
    parser.add_argument("--ssh-log", help="SSH 抓取的日志路径")
    parser.add_argument("--ssh-host", default="127.0.0.1")
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--ssh-user", default="")
    parser.add_argument("--ssh-pass", default="")
    parser.add_argument("--log-grep", default="(Actual SQL|当前SQL)", help="日志过滤关键词（支持两种格式）")
    parser.add_argument("--field-groups", help="字段组配置 JSON 文件")
    parser.add_argument("--skip-conditional-groups", action="store_true",
                        help="跳过 conditional 字段组的自动填入（双路查询空查分支用）")
    parser.add_argument("--write-back", help="将实际填入值写回 case_plan.json 路径")
    parser.add_argument("--save-log", help="将捕获的 SSH 日志保存到指定文件")
    parser.add_argument("--test-locator", help="测试 Locator 翻译（不执行）")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # 纯翻译测试
    if args.test_locator:
        expr = args.test_locator.strip().rstrip(";")
        expr = re.sub(r"\.filter\(\{([^}]*)\}\)", _filter_replacer, expr)
        parts = _brace_aware_split(expr, ".")
        translated = []
        for p in parts:
            for js_name, py_name in _JS_TO_PY_METHODS.items():
                if js_name in p:
                    p = p.replace(js_name, py_name)
                    break
            translated.append(p)
        print(f"原始 JS:  {args.test_locator}")
        print(f"Python:   {'.'.join(translated)}")
        return

    # 健康检查模式（批量复用浏览器前调用）
    if args.health_check:
        with CDPExecutor(
            cdp_url=args.cdp_url,
            target_url=args.target_url,
            config_path=args.config,
        ) as executor:
            result = executor.health_check()
            if result["ok"]:
                print("[OK] 浏览器会话健康")
                sys.exit(0)
            else:
                print(f"[WARN] 浏览器会话异常: {', '.join(result['issues'])}")
                sys.exit(1)

    # 恢复模式（关闭弹窗 + 重新导航）
    if args.recover:
        with CDPExecutor(
            cdp_url=args.cdp_url,
            target_url=args.navigate_url or args.target_url,
            config_path=args.config,
        ) as executor:
            ok = executor.recover_session(target_url=args.navigate_url)
            if ok:
                print("[OK] 会话恢复成功")
                sys.exit(0)
            else:
                print("[ERROR] 会话恢复失败，需要重新启动浏览器")
                sys.exit(1)

    # 正常执行（--navigate / -e / -a 至少提供一个）
    if not args.login and not args.explore and not args.actions and not args.navigate_url:
        parser.error("需要 --login、--navigate、-e（探勘结果 JSON）或 -a（动作指令 JSON）")

    ssh_config = None
    if args.ssh_user:
        ssh_config = {
            "host": args.ssh_host,
            "port": args.ssh_port,
            "username": args.ssh_user,
            "password": args.ssh_pass,
        }

    with CDPExecutor(
        cdp_url=args.cdp_url,
        target_url=args.target_url,
        ssh_config=ssh_config,
        config_path=args.config,
    ) as executor:
        if args.login:
            login_user = args.login_user
            login_pass = args.login_pass
            login_url = args.login_url
            if not login_user or not login_pass or not login_url:
                from skill_config import get_config
                cfg = get_config(args.config)
                if not login_user:
                    login_user = cfg.branch_user
                if not login_pass:
                    login_pass = cfg.branch_password
                if not login_url:
                    login_url = cfg.login_url
            executor.login(login_user, login_pass, login_url)

        if args.navigate_url:
            executor.navigate(args.navigate_url)

        if args.explore:
            executor.load_fields(args.explore)

        if args.field_groups:
            with open(args.field_groups, "r", encoding="utf-8") as f:
                executor.load_field_groups(json.load(f))

        if args.skip_conditional_groups:
            executor.set_skip_conditional_groups(True)

        if args.actions:
            if args.ssh_log:
                executor.mark_log_position(args.ssh_log)
            with open(args.actions, "r", encoding="utf-8") as f:
                actions = json.load(f)
            executor.execute_actions(actions, write_back_path=args.write_back)

        if args.ssh_log:
            log_text = executor.wait_and_capture(
                log_path=args.ssh_log,
                grep_keyword=args.log_grep,
            )
            print(log_text)
            if args.save_log:
                save_path = os.path.abspath(args.save_log)
                os.makedirs(os.path.dirname(save_path), exist_ok=True)
                with open(save_path, "w", encoding="utf-8") as f:
                    f.write(log_text)
                logger.info(f"[OK] 日志已保存: {save_path}")
        else:
            executor.page.wait_for_timeout(3000)
            print(f"记录数: {executor.get_record_count()}")


if __name__ == "__main__":
    main()
