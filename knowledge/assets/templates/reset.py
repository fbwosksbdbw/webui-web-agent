# -*- coding: utf-8 -*-
import os, logging, allure, pytest
from playwright.async_api import Page
from conf.config import Config
from framework.utils.async_sqlalchemy_executor import get_async_db_executor
from framework.utils.prettyAllure import hierarchical_case
from common.helpers import (
    _collapse_sidebar, _get_record_count,
    _fill_input_by_label, _get_textbox_value_by_label
)

PRESET_SQL_BASE = os.path.join(
    Config.root_dir, Config.SQL_DATA_DIRS['${LEVEL1}'], '${LEVEL2}', '${MENU_NAME}', '${FUNC_CODE}_base.sql'
)
PRESET_SQL = os.path.join(
    Config.root_dir, Config.SQL_DATA_DIRS['${LEVEL1}'], '${LEVEL2}', '${MENU_NAME}', '${FUNC_CODE}.sql'
)
RESET_TEST_VALUE = "${RESET_TEST_VALUE}"

MENU_URL = "${MENU_URL}"


@pytest.fixture
async def db_setup_base():
    async with get_async_db_executor(db_key="ui_bc", dbtype="mysql") as executor:
        await executor.execute_sql_files([PRESET_SQL_BASE])
        yield executor


@pytest.fixture
async def db_setup():
    async with get_async_db_executor(db_key="${SHARD_DB_KEY}", dbtype="mysql") as executor:
        await executor.execute_sql_files([PRESET_SQL])
        yield executor


async def _navigate(page):
    base = page.url.split('#')[0]
    await page.goto(f"{base}#{MENU_URL}", wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)
    await _collapse_sidebar(page)


@hierarchical_case("${LEVEL1}", "${LEVEL2}", "${MENU_NAME}", "${MENU_NAME}_条件重置")
@allure.description("${MENU_NAME} - 条件重置（填写后重置，验证清空）")
@allure.label("status", "启用")
@pytest.mark.case_source("AI")
@allure.label("owner", "${OWNER}")
@pytest.mark.asyncio
async def test_${FUNC_CODE}_reset(page: Page, db_setup_base, db_setup):
    await _navigate(page)

    await _fill_input_by_label(page, "${RESET_TEST_FIELD}", RESET_TEST_VALUE)
    await page.wait_for_timeout(500)

    # 点击页面空白处，关闭日期控件等弹出层，避免遮挡重置按钮
    await page.mouse.click(10, 10)
    await page.wait_for_timeout(500)

    await page.get_by_role("button", name="重置").click()
    await page.wait_for_timeout(2000)

    val = await _get_textbox_value_by_label(page, "${RESET_TEST_FIELD}")
    assert val == "", f"${RESET_TEST_FIELD}未重置，当前值: '{val}'"
    logging.info("条件重置验证通过")
