# HUI Playwright Test Framework - Project Rules

## Project Overview
- **Framework**: Playwright + pytest + paramiko
- **Domain**: Automated E2E testing for a Vue-based management console
- **Architecture**: Code generation from Excel config -> templates -> test cases
- **Default Mode**: Headless Chromium via CDP (Chrome DevTools Protocol)
- **Scope**: Function-level browser isolation (each test creates fresh browser + login)

---

## 1. Core Workflow Rules

### 1.1 Three-Phase Workflow (MANDATORY)
1. **Generate** → `generate.py` (code generation)
2. **Validate** → `dry_run.py` (SQL validation)
3. **Execute** → `run.py` (test execution)

### 1.2 Single Test First
- **ALWAYS** test `querysingle` first before generating other test types
- Debug with single test: `pytest tests/test_querysingle.py -k "test_case_name"`
- Do NOT run all 7 tests until querysingle passes

### 1.3 Prerequisites Before Test Execution
- Run `playwright-cli close` before every pytest session
- Wait 3-5 seconds after closing playwright-cli
- NEVER run playwright-cli and pytest simultaneously
- Verify Chrome is running with CDP enabled (port 9222)

---

## 2. Technical Constraints (IMMUTABLE)

### 2.1 Browser & Login Overhead
- **DO NOT OPTIMIZE**: Each test creates new browser + performs full login (~40-50 sec)
- 7 tests × 45 sec ≈ 5-6 minutes just for login overhead
- This is framework design, not a bug
- Fixture scope: `browser` and `base_page` are `scope="function"`

### 2.2 Navigation Strategy
- **USE**: Hash-based navigation `page.goto(f"{base}#{menu_url}", wait_until="domcontentloaded")`
- **AVOID**: Sidebar click-based navigation (slow, fragile)
- **ALWAYS**: Wait 3000ms after goto, then collapse sidebar

### 2.3 Login Timeout Behavior
- `Page.goto: Timeout 30000ms exceeded` is INTERMITTENT
- Cause: Server occasionally slow loading external resources (fonts, styles)
- `curl` returning 200 confirms server is fine
- **RULE**: Retry on timeout, do NOT debug. Only investigate after 3 consecutive failures

### 2.4 SSH Constraints
- **USE**: `paramiko` library only
- **FORBIDDEN**: `sshpass`, system `ssh` command
- Config source: `config.json` → `env` field

---

## 3. Code Generation Rules

### 3.1 Config-Driven Architecture
- Menu paths: `config.json` → `menus` field
- Test data: `config.json` → `cases` array
- DB config: `config.json` → `env.db_write` / `env.db_read`
- Menu URLs: Auto-generated via `config_reader.py` → `build_menu_map()`

### 3.2 Template System
- **7 template files** in `assets/templates/`:
  - `branch.py`, `headquarter.py` - Hierarchical cases
  - `preview.py`, `queryall.py`, `querysingle.py`, `querymulti.py` - Query cases
  - `reset.py` - Condition reset
- Templates use `${VAR}` placeholder syntax
- Generated tests go to `tests/` directory

### 3.3 SQL Generation
- **Cache-driven**: `cache/schema_cache.json` contains all table schemas
- **CRITICAL**: Every column name MUST come from cache/DESCRIBE, NEVER fabricate
- **CRITICAL**: Every enum value MUST come from database DISTINCT query
- **CRITICAL**: Every FK value MUST come from actual table data
- **Auto-correction**: SQL Generator self-checks for `[1054]`, `[1062]`, `[1366]` errors

### 3.4 Data Safety Boundaries
- **FORBIDDEN**: `UPDATE`, `DELETE`, `ALTER`, `TRUNCATE`, `DROP` in production
- **ALLOWED**: `SELECT` (read), `INSERT` (write with explicit approval)
- **MANDATORY**: Confirm before all write operations
- **MANDATORY**: Verify WHERE clause before DELETE/UPDATE

---

## 4. Test Architecture Rules

### 4.1 pytest Structure
```
tests/
  conftest.py          # Shared fixtures (browser, page, login)
  test_branch.py       # Branch operations
  test_headquarter.py  # HQ operations
  test_preview.py      # Preview operations
  test_queryall.py     # Query all
  test_querysingle.py  # Query single (TEST FIRST)
  test_querymulti.py   # Query multi
  test_reset.py        # Condition reset
```

### 4.2 Fixture Scoping
```python
@pytest.fixture(scope="function")  # NOT "session" or "class"
def browser():
    # Fresh browser per test

@pytest.fixture(scope="function")
def base_page(browser):
    # Fresh page + login per test
```

### 4.3 Locator Strategy (Priority Order)
1. Role-based: `page.get_by_role("button", name="查询")`
2. Text-based: `page.get_by_text("查询")`
3. Label-based: `page.get_by_label("用户名")`
4. CSS (last resort): `page.locator(".h-omc-table-btn:visible")`

### 4.4 Wait Strategy
- **After navigation**: `await page.wait_for_timeout(3000)`
- **After click**: `await page.wait_for_timeout(1000-2000)`
- **For elements**: `await page.wait_for_selector(".h-page", timeout=3000)`
- **Default timeout**: 30000ms (Playwright default)

---

## 5. Error Handling Rules

### 5.1 Common Error Patterns

| Error | Cause | Solution |
|-------|-------|----------|
| `Page.goto: Timeout 30000ms exceeded` | playwright-cli conflict or server temp unavailable | Close playwright-cli, retry |
| `strict mode violation: locator resolved to N elements` | Selector too broad | Add `.first` or use more specific selector |
| `表格未找到预埋数据` | Wrong shard insertion | Check logs: `Actual SQL: ds0/ds1` for sharded tables |
| `详情面板未显示xxx` | Hardcoded field name error | Use generic validation (`input[readonly]` has value) |
| `text=详情` or `text=预览` not found | Button text varies by page | Use `.h-omc-table-btn:visible` + fallback |
| `[1054] Unknown column` | Fabricated column name | Check DESCRIBE/cache |
| `[1062] Duplicate entry` | Duplicate UNIQUE values | Increment date+1 or suffix+1 |
| `[1366] Incorrect integer value` | Int column filled with space string | Use `0` for int nulls, not `' '` |

### 5.2 Retry Strategy
- **Timeout errors**: Retry immediately, up to 3 times
- **Element not found**: Wait 2-3 seconds, retry
- **Network errors**: Check connectivity, retry after 5 seconds

### 5.3 Debugging Workflow
1. **Single test**: `pytest tests/test_querysingle.py -k "test_name" -v`
2. **Check logs**: `tail -20 logs/error/latest.log`
3. **Verify DB**: `SELECT * FROM table WHERE id = 'xxx'`
4. **Check menu**: `SELECT * FROM sys_menu WHERE menu_name LIKE '%关键词%'`
5. **Check config**: `cat config.json | jq '.cases[] | select(.menu_name=="xxx")'`

---

## 6. Database Rules

### 6.1 Connection Config
```json
{
  "env": {
    "db_write": {"host": "...", "port": 3306, "user": "...", "password": "..."},
    "db_read": {"host": "...", "port": 3306, "user": "...", "password": "..."}
  }
}
```

### 6.2 Query Patterns
- **Sharded tables**: Check `Actual SQL: ds0` or `Actual SQL: ds1` in logs
- **Non-sharded tables**: Check `当前SQL:` in logs
- **Menu queries**: `SELECT * FROM sys_menu WHERE menu_name = 'xxx'`
- **Schema queries**: `DESCRIBE table_name` or use `cache/schema_cache.json`

### 6.3 Data Isolation
- Each test should use unique test data (avoid conflicts)
- Use timestamps or UUIDs for test data uniqueness
- Clean up test data after test completion (if applicable)

---

## 7. File Organization Rules

### 7.1 Directory Structure
```
hui-playwright-test/
├── assets/                  # Template files and common helpers
│   ├── templates/           # 7 test templates
│   └── common/              # Shared utilities (helpers.py, nav.py, sql_utils.py)
├── cache/                   # Schema cache (auto-generated)
├── config/                  # Runtime configs, scripts
│   ├── fetch_menu.py        # Menu fetching
│   └── gen_*_config.py      # Config generation
├── docs/                    # Documentation (READ FIRST)
├── fixtures/                # Test fixtures
├── logs/                    # Test execution logs
├── references/              # Key docs, guidelines, troubleshooting
├── scripts/                 # Core framework code
│   ├── code_generator.py    # Code generation engine
│   ├── config_reader.py     # Config reading and SQL analysis
│   ├── dry_run.py           # SQL validation
│   ├── generate.py          # Test case generation
│   ├── orchestrator.py      # Workflow orchestration
│   ├── run.py               # Test execution
│   ├── sql_generator.py     # SQL generation
│   └── sql_validator.py     # SQL validation
├── sql/                     # Generated SQL files
├── tests/                   # Generated test files
├── config.json              # Main config file
├── conftest.py              # pytest configuration
├── generate.py              # Generate entry point
├── dry_run.py               # Validation entry point
├── run.py                   # Execution entry point
└── .cursor/rules/           # Project rules (this file)
```

### 7.2 Critical Files (DO NOT DELETE)
- `config.json` - Main configuration
- `cache/schema_cache.json` - Database schema cache
- `conftest.py` - pytest fixtures
- `assets/common/*.py` - Shared utilities
- `assets/templates/*.py` - Test templates

### 7.3 Generated Files (SAFE TO REGENERATE)
- `tests/test_*.py` - Generated test files
- `sql/*.sql` - Generated SQL files
- `logs/` - Execution logs

---

## 8. Performance Rules

### 8.1 Expected Timing
- Single test: 40-50 seconds (login + navigation + execution)
- Full suite (7 tests): 5-6 minutes minimum
- Code generation: < 30 seconds
- SQL validation: < 10 seconds

### 8.2 Optimization Points
- **CAN optimize**: SQL query speed, element wait times, navigation path
- **CANNOT optimize**: Login overhead (framework design), browser startup
- **SHOULD NOT**: Run all tests during debugging (use single test)

### 8.3 Resource Management
- Close browser after each test (automatic with function scope)
- Clean up temporary files after test suite
- Rotate logs to prevent disk space issues

---

## 9. Security Rules

### 9.1 Credential Management
- **NEVER** hardcode credentials in test files
- **USE**: `config.json` → `env` field for all credentials
- **ROTATE**: Credentials periodically

### 9.2 Database Access
- **PRINCIPLE OF LEAST PRIVILEGE**: Read-only by default
- **WRITE ACCESS**: Only when explicitly configured and approved
- **AUDIT**: Log all write operations

### 9.3 Network Access
- **ALLOWED**: Target test environment only
- **FORBIDDEN**: Production environment access from tests
- **REQUIRED**: VPN or secure connection for remote environments

---

## 10. Documentation Rules

### 10.1 Required Reading Order
1. `docs/README.md` - Project overview
2. `docs/guidelines/PROJECT_RULES.md` - This file
3. `docs/guidelines/SQL_GENERATION_RULES.md` - SQL rules
4. `docs/guidelines/TEST_COVERAGE_STRATEGY.md` - Coverage strategy
5. `docs/technical/ARCHITECTURE.md` - Technical architecture

### 10.2 Documentation Updates
- **WHEN**: After any significant change
- **WHAT**: Update relevant docs, not just code comments
- **WHERE**: `docs/` directory for user-facing, code comments for developer-facing

### 10.3 Knowledge Capture
- Document all workarounds and their reasons
- Record all environment-specific configurations
- Maintain troubleshooting guide with real solutions

---

## 11. AI Assistant Rules (Cursor/Claude)

### 11.1 Mandatory Checks
- **BEFORE any action**: Read `config.json` and understand current state
- **BEFORE SQL operations**: Verify against `cache/schema_cache.json`
- **BEFORE test generation**: Confirm menu exists in config
- **BEFORE write operations**: Get explicit user approval

### 11.2 Forbidden Actions
- Fabricate column names, enum values, or FK values
- Assume database structure without verification
- Execute destructive SQL without confirmation
- Modify framework files without understanding impact
- Skip validation steps to save time

### 11.3 Required Actions
- Use data-driven approach (query before assume)
- Self-check generated SQL for common errors
- Log all significant actions
- Provide clear feedback on what was done and why

### 11.4 Communication Rules
- **Language**: Match user's language (Chinese/English)
- **Be specific**: Show exact commands, file paths, line numbers
- **Be honest**: Say "I don't know" when uncertain
- **Be proactive**: Suggest improvements, not just fix problems

---

## 12. Version Control Rules

### 12.1 Commit Strategy
- **Frequency**: After each logical unit of work
- **Message**: Clear, descriptive, reference issue numbers
- **Scope**: One feature/fix per commit

### 12.2 Branch Strategy
- **Main**: Stable, production-ready
- **Develop**: Integration branch
- **Feature**: For new features
- **Hotfix**: For critical fixes

### 12.3 Code Review
- **REQUIRED**: For all changes to framework files
- **FOCUS**: Logic, security, performance
- **TOOLS**: GitHub/GitLab PR/MR process

---

## Quick Reference Commands

```bash
# Generate test cases
python generate.py --menu "菜单名称" --type querysingle

# Validate SQL
python dry_run.py --file sql/querysingle_xxx.sql

# Run single test
pytest tests/test_querysingle.py -k "test_case_name" -v

# Run all tests
pytest tests/ -v

# Check logs
tail -20 logs/error/latest.log

# Verify database
mysql -h host -u user -p -e "SELECT * FROM table WHERE id='xxx'"

# Close playwright-cli (REQUIRED before pytest)
playwright-cli close
sleep 3
pytest ...
```

---

## Document Version
- **Version**: 1.0
- **Last Updated**: 2026-03-24
- **Author**: Claude Code
- **Status**: Active
