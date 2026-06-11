# -*- coding: utf-8 -*-
import os, logging, allure, pytest
from playwright.async_api import Page
from conf.config import Config
from framework.utils.async_sqlalchemy_executor import get_async_db_executor
from framework.utils.prettyAllure import hierarchical_case
from common.helpers import (
    _collapse_sidebar, _get_record_count,
    _select_dropdown_by_label, _fill_input_by_label
)

PRESET_SQL_BASE = os.path.join(
    Config.root_dir, Config.SQL_DATA_DIRS['${LEVEL1}'], '${LEVEL2}', '${MENU_NAME}', '${FUNC_CODE}_base.sql'
)
PRESET_SQL = os.path.join(
    Config.root_dir, Config.SQL_DATA_DIRS['${LEVEL1}'], '${LEVEL2}', '${MENU_NAME}', '${FUNC_CODE}.sql'
)
PRESET_CUSTOMER_ID = "${CUSTOMER_ID}"

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


async def _fill_required_fields(page):
${REQUIRED_FIELDS_CODE}


async def _click_query_and_wait(page):
    # 关闭日期控件等弹出层，避免遮挡查询按钮
    await page.mouse.click(10, 10)
    await page.wait_for_timeout(500)
    await page.get_by_role("button", name="查询").click()
    await page.wait_for_timeout(2000)
    count = 0
    for _ in range(15):
        count = await _get_record_count(page)
        if count > 0:
            return count
        await page.wait_for_timeout(1000)
    logging.warning("等待查询结果超时")
    return count


@hierarchical_case("${LEVEL1}", "${LEVEL2}", "${MENU_NAME}", "${MENU_NAME}_全量查询")
@allure.description("${MENU_NAME} - 最少必填查询（填必填字段后查询）")
@allure.label("status", "启用")
@pytest.mark.case_source("AI")
@allure.label("owner", "${OWNER}")
@pytest.mark.asyncio
async def test_${FUNC_CODE}_queryall(page: Page, db_setup_base, db_setup):
    await _navigate(page)
    await page.get_by_role("button", name="重置").click()
    await page.wait_for_timeout(1000)
    await _fill_required_fields(page)
    cnt = await _click_query_and_wait(page)
    assert cnt > 0, f"全量查询为空，预期至少1条，实际{cnt}条"
    logging.info(f"全量查询共{cnt}条")
