from __future__ import annotations

import json
from typing import Any


_SENTINEL_VALUES = {"__FIRST_OPTION__", "_", "", "AUTO_"}


def _is_sentinel(value: str) -> bool:
    """判断值是否为占位符哨兵值（不应出现在生成的测试代码中）。"""
    if value in _SENTINEL_VALUES:
        return True
    return value.startswith("AUTO_")


def generate_fill_code(fields: list[dict[str, Any]]) -> str:
    lines = []
    for field in fields:
        label = field.get("label", "")
        value = str(field.get("value", ""))
        field_type = field.get("type", "")
        if not label:
            continue
        if _is_sentinel(value):
            # 哨兵值不生成填充动作；最终用例应优先使用探勘回填或 preset 中的明确值。
            continue
        if field_type == "input":
            lines.append(f"    await _fill_input_by_label(page, {_py_str(label)}, {_py_str(value)})")
        elif field_type in {"single-select", "multi-select"}:
            lines.append(f"    await _select_dropdown_by_label(page, {_py_str(label)}, {_py_str(value)})")
    return "\n".join(lines) or "    pass"


def render_pytest_case(
    case_plan: dict[str, Any],
    case_type: str,
    output_path: object | None = None,
    case_plan_dir: object | None = None,
) -> str:
    menu = case_plan.get("menu", {})
    case_input = case_plan.get("case_inputs", {}).get(case_type, {})
    fill_code = generate_fill_code(case_input.get("fields", []))
    reset_assert = _render_reset_assert(case_input.get("fields", []))
    body = _render_test_body(case_plan, case_type, fill_code, reset_assert, _has_preset_data(case_plan))
    func_code = menu.get("code", "generated")
    menu_url = menu.get("url", "")
    level1 = menu.get("level1", "")
    level2 = menu.get("level2", "")
    menu_name = menu.get("name", "")
    owner = case_plan.get("owner", "") or case_plan.get("menu", {}).get("owner", "")
    shard_db = case_plan.get("shard", {}).get("db", "ui_bc")
    env_key = case_plan.get("env_key", "")
    decorators = _render_test_decorators(case_type)
    query_wait_seconds = 300 if case_type == "queryall" else 60
    return f'''# -*- coding: utf-8 -*-
import os, logging, allure, pytest
from playwright.async_api import Page
from conf.config import Config
from framework.utils.async_sqlalchemy_executor import get_async_db_executor
from framework.utils.prettyAllure import hierarchical_case
from common.helpers import _collapse_sidebar, _fill_input_by_label, _get_record_count, _select_dropdown_by_label


MENU_URL = {_py_str(menu_url)}
PROJECT_ROOT = getattr(Config, "python_dir", None) or Config.root_dir
PRESET_SQL_BASE = os.path.join(
    PROJECT_ROOT, Config.SQL_DATA_DIRS[{_py_str(str(level1))}], {_py_str(str(level2))}, {_py_str(str(menu_name))}, {_py_str(str(func_code) + "_base.sql")}
)
PRESET_SQL = os.path.join(
    PROJECT_ROOT, Config.SQL_DATA_DIRS[{_py_str(str(level1))}], {_py_str(str(level2))}, {_py_str(str(menu_name))}, {_py_str(str(func_code) + ".sql")}
)
BASE_DB_KEY = "ui_bc"
PRESET_DB_KEY = {_py_str(str(shard_db or "ui_bc"))}
DB_ENV_NAME = {_py_str(str(env_key or ""))} or getattr(Config, "env_name", None)


@pytest.fixture
async def db_setup_base():
    if not PRESET_SQL_BASE:
        yield None
        return
    async with get_async_db_executor(env_name=DB_ENV_NAME, db_key=BASE_DB_KEY, dbtype="mysql") as executor:
        await executor.execute_sql_files([PRESET_SQL_BASE])
        yield executor


@pytest.fixture
async def db_setup():
    if not PRESET_SQL:
        yield None
        return
    async with get_async_db_executor(env_name=DB_ENV_NAME, db_key=PRESET_DB_KEY, dbtype="mysql") as executor:
        await executor.execute_sql_files([PRESET_SQL])
        yield executor


async def _navigate(page: Page):
    if MENU_URL:
        base = page.url.split("#")[0]
        await page.goto(f"{{base}}#{{MENU_URL}}", wait_until="domcontentloaded")
        await page.wait_for_timeout(3000)
        await _collapse_sidebar(page)


async def _click_query_and_wait(page: Page) -> int:
    await page.mouse.click(10, 10)
    await page.wait_for_timeout(500)
    await page.get_by_role("button", name="查询").click()
    await page.wait_for_timeout(2000)
    count = 0
    for _ in range({query_wait_seconds}):
        count = await _get_record_count(page)
        if count > 0:
            return count
        waited = _ + 1
        if waited % 15 == 0:
            logging.info("等待查询结果中，已等待%s秒", waited)
        await page.wait_for_timeout(1000)
    logging.warning("等待查询结果超时")
    return count


async def _input_value_by_label(page: Page, label: str) -> str:
    item = page.locator(".h-form-item").filter(has_text=label).first
    for selector in ("input:not([type=hidden])", "textarea"):
        control = item.locator(selector).first
        if await control.count() > 0:
            return await control.input_value()
    return ""


async def _control_value_by_label(page: Page, label: str) -> str:
    item = page.locator(".h-form-item").filter(has_text=label).first
    for selector in (".h-select-input", "input:not([type=hidden])", "textarea"):
        control = item.locator(selector).first
        if await control.count() > 0:
            value = await control.input_value()
            return (value or "").strip()
    return ""


async def _click_preview_button(page: Page) -> None:
    for keyword in ("详情", "预览", "查看"):
        candidates = page.locator(
            ".h-omc-table-btn:visible, button:visible, a:visible, span:visible"
        ).filter(has_text=keyword)
        if await candidates.count() > 0:
            await candidates.first.click(force=True)
            return
    raise AssertionError("未找到详情/预览/查看按钮")


async def _first_readonly_detail_value(page: Page) -> str:
    await page.wait_for_timeout(2000)
    return await page.evaluate("""() => {{
        const controls = Array.from(document.querySelectorAll('input[readonly], textarea[readonly]'));
        for (const control of controls) {{
            const value = (control.value || '').trim();
            if (value) return value;
        }}
        const panels = Array.from(document.querySelectorAll('.h-dialog, .h-modal, .h-drawer'));
        const visible = panels.find(el => {{
            const style = window.getComputedStyle(el);
            return style && style.display !== 'none' && style.visibility !== 'hidden' && el.getClientRects().length > 0;
        }});
        return visible ? (visible.textContent || '').trim() : '';
    }}""")

@hierarchical_case({_py_str(str(level1))}, {_py_str(str(level2))}, {_py_str(str(menu_name))}, {_py_str(str(menu_name) + _case_dir_suffix(case_type))})
@allure.description({_py_str(_case_description(menu_name, case_type))})
@allure.label("status", "启用")
@pytest.mark.case_source("AI")
@allure.label("owner", {_py_str(str(owner))})
{decorators}@pytest.mark.asyncio
async def test_{func_code}_{case_type}(page: Page, db_setup_base, db_setup):
    await _navigate(page)
{body}
'''


def _py_str(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _has_preset_data(case_plan: dict[str, Any]) -> bool:
    return bool(case_plan.get("insert_plans") or case_plan.get("sql_paths"))


def _render_test_body(
    case_plan: dict[str, Any],
    case_type: str,
    fill_code: str,
    reset_assert: str,
    has_preset_data: bool,
) -> str:
    lines = [
        '    await page.get_by_role("button", name="重置").click()',
        "    await page.wait_for_timeout(1000)",
    ]
    if fill_code.strip() != "pass":
        lines.append(fill_code)
    if case_type == "reset":
        lines.extend(
            [
                '    await page.get_by_role("button", name="重置").click()',
                "    await page.wait_for_timeout(1000)",
                reset_assert,
            ]
        )
        return "\n".join(line for line in lines if line)
    lines.append("    cnt = await _click_query_and_wait(page)")
    if case_type == "preview":
        lines.extend(
            [
                '    assert cnt > 0, f"查询结果为空，无法测试预览，实际{cnt}条"',
                "    await _click_preview_button(page)",
                "    detail_val = await _first_readonly_detail_value(page)",
                '    assert detail_val, "详情面板未展开或详情内容为空"',
                '    logging.info("详情面板有数据: %s", detail_val[:50])',
            ]
        )
        return "\n".join(lines)
    if case_type == "branch":
        expected = _expected_count_for_case(case_plan, "branch", 1)
        lines.append(f'    assert cnt == {expected}, f"分行应看到{expected}条，实际{{cnt}}条"')
    elif case_type == "headquarter":
        expected = _expected_count_for_case(case_plan, "headquarter", 2)
        lines.append(f'    assert cnt == {expected}, f"总行应看到{expected}条，实际{{cnt}}条"')
    elif has_preset_data:
        lines.append(f'    assert cnt > 0, f"{_case_type_label(case_type)}为空，预期至少1条，实际{{cnt}}条"')
    else:
        lines.append('    assert cnt >= 0, f"查询记录数读取失败，实际{cnt}条"')
    lines.append(f'    logging.info("{_case_type_label(case_type)}共%s条", cnt)')
    return "\n".join(lines)


def _expected_count_for_case(case_plan: dict[str, Any], case_type: str, default: int) -> int:
    permission_check = (
        case_plan.get("sql_return_precheck", {})
        .get("permission_checks", {})
        .get(case_type, {})
    )
    if (
        isinstance(permission_check, dict)
        and permission_check.get("status") == "passed"
        and permission_check.get("actual") == permission_check.get("expected")
    ):
        try:
            return int(permission_check.get("expected"))
        except (TypeError, ValueError):
            pass
    try:
        return int(case_plan.get("expected_counts", {}).get(case_type, default))
    except (TypeError, ValueError):
        return default


def _render_reset_assert(fields: list[dict[str, Any]]) -> str:
    for field in fields:
        if field.get("type") in {"input", "single-select", "multi-select"} and field.get("label"):
            label = str(field["label"])
            return (
                f"    value = await _control_value_by_label(page, {_py_str(label)})\n"
                f'    assert value == "", f"{label}重置后未清空，实际值: {{value}}"'
            )
    return '    assert await page.get_by_role("button", name="查询").count() > 0, "查询按钮不存在"'


def _render_test_decorators(case_type: str) -> str:
    if case_type == "branch":
        return '@pytest.mark.login_user("branch_user")\n'
    return ""


def _case_dir_suffix(case_type: str) -> str:
    return {
        "branch": "_分行权限",
        "headquarter": "_总行权限",
        "queryall": "_全量查询",
        "querysingle": "_单条件查询",
        "querymulti": "_多条件查询",
        "reset": "_条件重置",
        "preview": "_结果预览",
    }.get(case_type, f"_{case_type}")


def _case_description(menu_name: str, case_type: str) -> str:
    return {
        "branch": f"{menu_name} - 分行权限（分行账号只能看到本行数据）",
        "headquarter": f"{menu_name} - 总行权限（总行账号看到全部权限数据）",
        "queryall": f"{menu_name} - 全量查询",
        "querysingle": f"{menu_name} - 单条件查询",
        "querymulti": f"{menu_name} - 多条件查询",
        "reset": f"{menu_name} - 条件重置（填写后重置，验证清空）",
        "preview": f"{menu_name} - 结果预览",
    }.get(case_type, f"{menu_name} - {case_type}")


def _case_type_label(case_type: str) -> str:
    return {
        "queryall": "全量查询",
        "querysingle": "单条件查询",
        "querymulti": "多条件查询",
        "preview": "结果预览查询",
        "branch": "分行权限查询",
        "headquarter": "总行权限查询",
    }.get(case_type, case_type)
