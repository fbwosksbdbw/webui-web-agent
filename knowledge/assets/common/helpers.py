# version: 2026-06-07
"""
共享辅助函数文件 —— 所有测试文件统一从此文件导入。
此文件从 SKILL.md 原样复制，不允许 AI 理解后重写（重写会漂移）。
修改此文件后，所有导入它的测试文件自动生效，不需要逐个修改。
"""

import logging
import re
from playwright.async_api import Page, expect


async def _collapse_sidebar(page: Page):
    """收起侧边栏，防止遮挡按钮"""
    sidebar = page.locator(".h-sidebar-wrapper.openedSidebar")
    if await sidebar.count() > 0:
        toggle_btn = sidebar.locator(".h-sidebar-toggle")
        if await toggle_btn.count() > 0:
            await toggle_btn.first.click()
            return
        await page.evaluate("""() => {
            const sidebar = document.querySelector('.h-sidebar-wrapper.openedSidebar');
            if (sidebar) {
                sidebar.classList.remove('openedSidebar');
                sidebar.classList.add('closedSidebar');
            }
        }""")


async def _get_record_count(page: Page) -> int:
    """读取当前可见分页的"共N条"记录数；分页组件不可用时回退到真实数据行数。"""
    try:
        await page.wait_for_selector(".h-page, table tbody tr", timeout=3000)
        count = await page.evaluate("""() => {
            const isVisible = el => {
                const style = window.getComputedStyle(el);
                return style && style.display !== 'none' && style.visibility !== 'hidden' && el.getClientRects().length > 0;
            };
            let hasPaginationCount = false;
            let maxCount = 0;
            for (const el of Array.from(document.querySelectorAll('.h-page')).filter(isVisible)) {
                const m = (el.textContent || '').match(/共\\s*(\\d+)\\s*条/);
                if (m) {
                    hasPaginationCount = true;
                    maxCount = Math.max(maxCount, Number(m[1] || 0));
                }
            }
            if (hasPaginationCount) return maxCount;
            const emptyTokens = ['暂无数据', '无数据', '暂无记录', '没有数据', '客户信息不存在'];
            const rows = Array.from(document.querySelectorAll('table tbody tr')).filter(row => {
                if (!isVisible(row)) return false;
                const text = (row.textContent || '').replace(/\\s+/g, '');
                return text && !emptyTokens.some(token => text.includes(token));
            }).length;
            return rows;
        }""")
        return int(count or 0)
    except Exception:
        pass
    # 回退：直接数表格行数（适用于分页组件格式不同的页面）
    try:
        rows = await page.locator("table tbody tr").all()
        count = 0
        empty_tokens = ("暂无数据", "无数据", "暂无记录", "没有数据", "客户信息不存在")
        for row in rows:
            text = (await row.text_content() or "").strip()
            if text and not any(token in text for token in empty_tokens):
                count += 1
        return count
    except Exception:
        return 0


async def _navigate_to_query_page(page: Page, nav_level1: str, nav_level2: str, nav_level3: str = None):
    """按导航路径点击菜单（路径从 config_reader.py nav 命令生成，禁止手写）"""
    await page.get_by_text(nav_level1, exact=True).click()
    await page.wait_for_timeout(1000)
    await page.get_by_text(nav_level2).click()
    await page.wait_for_timeout(1000)
    if nav_level3:
        await page.get_by_title(nav_level3, exact=True).click()
        await page.wait_for_timeout(3000)
    await _collapse_sidebar(page)


def _is_dropdown_pagination_text(text: str) -> bool:
    normalized = re.sub(r"\s+", "", str(text or ""))
    if not normalized:
        return True
    return bool(
        re.search(r"\d+条/页", normalized)
        or re.search(r"共\d+条", normalized)
        or normalized in {"上一页", "下一页", "前往", "页"}
    )


def _normalize_dropdown_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip())


def _split_option_code_label(text: str) -> tuple[str, str]:
    value = str(text or "").strip()
    if ":" in value:
        code, label = value.split(":", 1)
        return code.strip(), label.strip()
    if "：" in value:
        code, label = value.split("：", 1)
        return code.strip(), label.strip()
    return value, ""


def _dropdown_option_matches(option_name: str, candidate_text: str) -> bool:
    option = str(option_name or "").strip()
    candidate = str(candidate_text or "").strip()
    if not option or not candidate or _is_dropdown_pagination_text(candidate):
        return False
    option_norm = _normalize_dropdown_text(option)
    candidate_norm = _normalize_dropdown_text(candidate)
    option_code, option_label = _split_option_code_label(option)
    candidate_code, candidate_label = _split_option_code_label(candidate)
    if candidate_norm == option_norm:
        return True
    if option_code and candidate_code and candidate_code == option_code:
        return True
    if option_label and candidate_label and _normalize_dropdown_text(candidate_label) == _normalize_dropdown_text(option_label):
        return True
    if candidate_label and ":" not in option and _normalize_dropdown_text(candidate_label) == option_norm:
        return True
    if ":" in option and ":" not in candidate:
        return candidate == option_code or _normalize_dropdown_text(candidate) == _normalize_dropdown_text(option_label)
    return False


async def _find_option_in_dropdown(page: Page, option_name: str) -> bool:
    """在已打开的下拉列表中查找并点击指定选项；拒绝分页文本和宽松子串误匹配。"""
    dropdown_combined = ", ".join([
        ".h-select-dropdown:visible",
        ".h-select-dropdown-list:visible",
        "ul.h-select-dropdown:visible",
        ".h-dropdown-menu:visible",
    ])
    try:
        await page.wait_for_selector(dropdown_combined, state="visible", timeout=2000)
    except Exception:
        await page.wait_for_timeout(500)

    # 遍历所有可见选项，只接受精确/代码/名称匹配
    candidate_locators = [
        ".h-select-dropdown:visible li",
        ".h-select-dropdown:visible .h-option",
        "ul.h-select-dropdown:visible li",
    ]
    for css in candidate_locators:
        try:
            items = page.locator(css)
            cnt = await items.count()
            for i in range(cnt):
                item = items.nth(i)
                text = (await item.text_content() or "").strip()
                if _dropdown_option_matches(option_name, text):
                    await item.click(timeout=2000)
                    logging.info(f"✅ 已选择选项: {option_name} (matched: '{text}')")
                    return True
        except Exception:
            continue
    return False


async def _select_dropdown_by_label(page: Page, label_text: str, option_name: str) -> bool:
    """
    根据label文本定位下拉框并选择选项（单选/多选兼容，多选时不会清除已选tag）。
    未找到精确选项时返回 False，避免误点分页控件。
    """
    form_items = await page.locator(".h-form-item").all()
    target_item = None
    # 精确匹配优先
    for item in form_items:
        label = await item.locator(".h-form-item-label").first.text_content()
        if label and label.strip() == label_text:
            target_item = item
            break
    # 模糊匹配回退
    if not target_item:
        for item in form_items:
            label = await item.locator(".h-form-item-label").first.text_content()
            if label and label_text in label and label.strip() != label_text:
                target_item = item
                break

    if target_item:
        select_input = target_item.locator(".h-select-input").first
        if await select_input.count() > 0:
            try:
                await select_input.click(timeout=3000)
            except Exception:
                await select_input.click(force=True)
            await page.wait_for_timeout(500)
            if await _find_option_in_dropdown(page, option_name):
                logging.info(f"✅ 已选择 {option_name}（通过label: {label_text}）")
                return True
            # 搜索关键词只输入到当前字段的 .h-select-input，避免定位到分页搜索框
            search_keyword = option_name.split(":")[0] if ":" in option_name else option_name
            try:
                await select_input.fill(search_keyword, timeout=2000)
                await page.wait_for_timeout(800)
            except Exception:
                await select_input.type(search_keyword, delay=50)
                await page.wait_for_timeout(800)
            if await _find_option_in_dropdown(page, option_name):
                logging.info(f"✅ 已选择 {option_name}（通过搜索筛选: {search_keyword}）")
                return True
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(200)
            logging.warning(f"⚠️ 下拉框 {label_text} 中未找到选项 {option_name}")
            return False

    logging.error(f"❌ 未找到label为 {label_text} 的下拉框")
    return False


async def _fill_input_by_label(page: Page, label_text: str, value: str) -> bool:
    """
    根据label文本定位输入框并填入值（精确匹配优先，排除.h-select-input）。
    从 SKILL.md 原样复制，不允许 AI 重写。
    """
    form_items = await page.locator(".h-form-item").all()
    # 第一轮：精确匹配
    for item in form_items:
        label = await item.locator(".h-form-item-label").first.text_content()
        if label and label.strip() == label_text:
            input_el = item.locator("input:not([readonly]):not(.h-select-input)").first
            if await input_el.count() > 0:
                try:
                    await input_el.fill(value, timeout=3000)
                except Exception:
                    await input_el.fill(value, force=True)
                return True
    # 第二轮：模糊匹配（回退，排除精确匹配到的）
    for item in form_items:
        label = await item.locator(".h-form-item-label").first.text_content()
        if label and label_text in label and label.strip() != label_text:
            input_el = item.locator("input:not([readonly]):not(.h-select-input)").first
            if await input_el.count() > 0:
                try:
                    await input_el.fill(value, timeout=3000)
                except Exception:
                    await input_el.fill(value, force=True)
                return True
    return False


async def _get_textbox_value_by_label(page: Page, label_text: str) -> str:
    """
    根据label文本读取输入框/下拉框的值（精确匹配优先）。
    从 SKILL.md 原样复制，不允许 AI 重写。
    """
    form_items = await page.locator(".h-form-item").all()
    # 第一轮：精确匹配
    for item in form_items:
        label = await item.locator(".h-form-item-label").first.text_content()
        if label and label.strip() == label_text:
            select_input = item.locator(".h-select-input").first
            if await select_input.count() > 0:
                try:
                    return await select_input.input_value()
                except Exception:
                    pass
            input_el = item.locator("input:not([readonly]):not(.h-select-input)").first
            if await input_el.count() > 0:
                try:
                    return await input_el.input_value()
                except Exception:
                    pass
    # 第二轮：模糊匹配（回退）
    for item in form_items:
        label = await item.locator(".h-form-item-label").first.text_content()
        if label and label_text in label and label.strip() != label_text:
            select_input = item.locator(".h-select-input").first
            if await select_input.count() > 0:
                try:
                    return await select_input.input_value()
                except Exception:
                    pass
            input_el = item.locator("input:not([readonly]):not(.h-select-input)").first
            if await input_el.count() > 0:
                try:
                    return await input_el.input_value()
                except Exception:
                    pass
    return ""
