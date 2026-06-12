import json
from pathlib import Path

from hui_agent.failure_policy import EnvironmentFailure
from hui_agent.nodes.skill_steps import _apply_computed_shard_route, build_default_step_handlers
from hui_agent.state import HUITestState


def _write_script(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def test_computed_shard_route_adds_public_customer_table_when_clientmap_is_used(tmp_path):
    skill = tmp_path / "skill"
    (skill / "scripts").mkdir(parents=True)
    (skill / "scripts" / "config_reader.py").write_text("", encoding="utf-8")

    class Runner:
        scripts_dir = skill / "scripts"

        def run_json(self, *_args, **_kwargs):
            return {"db_name": "ui_fund2", "pub_table_num": 10, "trans_table_num": 13}

    case_plan = {
        "shard": {"physical_tables": {"tbclientmap": ["tbclientmap"]}},
        "preset_strategy": {"base_tables": ["tbclientmap"], "biz_tables": []},
    }

    _apply_computed_shard_route(case_plan, {"module": "dxfund"}, Runner())

    assert case_plan["shard"]["physical_tables"]["tbclient"] == ["tbclient10"]
    assert "tbclient" in case_plan["preset_strategy"]["base_tables"]


def test_menu_init_writes_minimal_case_plan(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    config_path = case_dir / "config.json"
    config = {
        "env": {
            "owner": "owner1",
            "env_key": "gyyh-auto",
            "single_db_key": "GYYHAUTO",
            "db_type": "oracle",
            "project_dir": "F:/project",
        },
        "menu": {"菜单名称": "菜单A", "导航一级菜单": "一级", "导航二级菜单": "二级"},
        "computed": {"menu_url": "/menu/a", "module": "dxtrust", "func_code": "FUNC_A"},
        "field_groups": [{"name": "客户标识组", "fields": ["客户标识类型", "客户标识"]}],
    }
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json, pathlib, sys\n"
        f"path = pathlib.Path({json.dumps(str(config_path))})\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        f"path.write_text({json.dumps(json.dumps(config, ensure_ascii=False))}, encoding='utf-8')\n"
        "print(f'config.json 已写入: {path}')\n",
    )

    state = build_default_step_handlers(skill)["0a"](HUITestState.new("菜单A"))

    case_plan = json.loads(Path(state.case_plan_path).read_text(encoding="utf-8"))
    assert state.completed_steps == ["0a"]
    assert case_plan["menu"]["name"] == "菜单A"
    assert case_plan["menu"]["code"] == "FUNC_A"
    assert case_plan["env"]["single_db_key"] == "GYYHAUTO"
    assert case_plan["env"]["db_type"] == "oracle"
    assert case_plan["env_key"] == "gyyh-auto"
    assert case_plan["paths"]["project_dir"] == "F:/project"
    assert case_plan["field_groups"][0]["name"] == "客户标识组"


def test_menu_init_owner_from_menu_overrides_env(tmp_path):
    """Owner from menu sheet should take priority over env sheet."""
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    config_path = case_dir / "config.json"
    config = {
        "env": {"owner": "env_owner"},
        "menu": {"菜单名称": "菜单A", "导航一级菜单": "一级", "导航二级菜单": "二级", "owner": "menu_owner"},
        "computed": {"menu_url": "/menu/a", "module": "dxtrust", "func_code": "FUNC_A"},
        "field_groups": [],
    }
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json, pathlib, sys\n"
        f"path = pathlib.Path({json.dumps(str(config_path))})\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        f"path.write_text({json.dumps(json.dumps(config, ensure_ascii=False))}, encoding='utf-8')\n"
        "print(f'config.json 已写入: {path}')\n",
    )

    state = build_default_step_handlers(skill)["0a"](HUITestState.new("菜单A"))
    case_plan = json.loads(Path(state.case_plan_path).read_text(encoding="utf-8"))

    assert case_plan["owner"] == "menu_owner"


def test_menu_init_owner_accepts_chinese_owner_column(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    config_path = case_dir / "config.json"
    config = {
        "env": {"owner": "env_owner"},
        "menu": {"菜单名称": "菜单A", "导航一级菜单": "一级", "导航二级菜单": "二级", "负责人": "menu_owner_cn"},
        "computed": {"menu_url": "/menu/a", "module": "dxtrust", "func_code": "FUNC_A"},
        "field_groups": [],
    }
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json, pathlib, sys\n"
        f"path = pathlib.Path({json.dumps(str(config_path))})\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        f"path.write_text({json.dumps(json.dumps(config, ensure_ascii=False))}, encoding='utf-8')\n"
        "print(f'config.json 已写入: {path}')\n",
    )

    state = build_default_step_handlers(skill)["0a"](HUITestState.new("菜单A"))
    case_plan = json.loads(Path(state.case_plan_path).read_text(encoding="utf-8"))

    assert case_plan["owner"] == "menu_owner_cn"


def test_menu_init_owner_falls_back_to_env(tmp_path):
    """When menu has no owner, fall back to env owner."""
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    config_path = case_dir / "config.json"
    config = {
        "env": {"owner": "env_owner"},
        "menu": {"菜单名称": "菜单A", "导航一级菜单": "一级", "导航二级菜单": "二级"},
        "computed": {"menu_url": "/menu/a", "module": "dxtrust", "func_code": "FUNC_A"},
        "field_groups": [],
    }
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json, pathlib, sys\n"
        f"path = pathlib.Path({json.dumps(str(config_path))})\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        f"path.write_text({json.dumps(json.dumps(config, ensure_ascii=False))}, encoding='utf-8')\n"
        "print(f'config.json 已写入: {path}')\n",
    )

    state = build_default_step_handlers(skill)["0a"](HUITestState.new("菜单A"))
    case_plan = json.loads(Path(state.case_plan_path).read_text(encoding="utf-8"))

    assert case_plan["owner"] == "env_owner"


def test_menu_init_forces_completed_menu_config_export(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    config_path = case_dir / "config.json"
    config = {
        "env": {"owner": "owner1"},
        "menu": {"菜单名称": "菜单A", "导航一级菜单": "一级", "导航二级菜单": "二级"},
        "computed": {"menu_url": "/menu/a", "module": "dxtrust", "func_code": "FUNC_A"},
        "field_groups": [],
    }
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json, pathlib, sys\n"
        "if '--force-completed' not in sys.argv:\n"
        "    print('[SKIP] 该菜单已完成，跳过: 菜单A')\n"
        "    raise SystemExit(0)\n"
        f"path = pathlib.Path({json.dumps(str(config_path))})\n"
        "path.parent.mkdir(parents=True, exist_ok=True)\n"
        f"path.write_text({json.dumps(json.dumps(config, ensure_ascii=False))}, encoding='utf-8')\n"
        "print(f'config.json 已写入: {path}')\n",
    )

    state = build_default_step_handlers(skill)["0a"](HUITestState.new("菜单A"))

    assert state.config_path == str(config_path)
    assert Path(state.case_plan_path).exists()


def test_browser_login_runs_launch_login_and_node_check(tmp_path):
    skill = tmp_path / "skill"
    calls = tmp_path / "calls.jsonl"
    case_dir = tmp_path / "case"
    config_path = case_dir / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"computed": {"menu_url": "/menu/a"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    recorder = (
        "import json, pathlib, sys\n"
        f"p = pathlib.Path({json.dumps(str(calls))})\n"
        "with p.open('a', encoding='utf-8') as f: f.write(json.dumps(sys.argv[1:], ensure_ascii=False)+'\\n')\n"
    )
    _write_script(skill / "scripts" / "launch_chrome.py", recorder)
    _write_script(skill / "scripts" / "cdp_executor.py", recorder)
    _write_script(skill / "scripts" / "stagehand_explorer" / "src" / "explore_wrapper.py", recorder)

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(config_path),
        case_dir=str(case_dir),
        case_plan_path=str(case_dir / "case_plan.json"),
    )

    result = build_default_step_handlers(skill)["0b"](state)

    calls_text = calls.read_text(encoding="utf-8")
    assert result.completed_steps == ["0b"]
    assert '"--auto"' in calls_text
    assert '"--login"' in calls_text
    assert '"--navigate"' in calls_text
    assert '"--check-node"' in calls_text


def test_browser_login_stops_when_chrome_launch_reports_cdp_timeout(tmp_path):
    skill = tmp_path / "skill"
    calls = tmp_path / "calls.jsonl"
    case_dir = tmp_path / "case"
    config_path = case_dir / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "env": {"cdp_url": "http://localhost:9222"},
                "computed": {"menu_url": "/menu/a"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    _write_script(
        skill / "scripts" / "launch_chrome.py",
        "print('CDP 启动超时 (15s)')\nprint('启动失败，请手动执行：')\n",
    )
    _write_script(
        skill / "scripts" / "cdp_executor.py",
        "import pathlib\n"
        f"pathlib.Path({json.dumps(str(calls))}).write_text('cdp called', encoding='utf-8')\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(config_path),
        case_dir=str(case_dir),
        case_plan_path=str(case_dir / "case_plan.json"),
    )

    try:
        build_default_step_handlers(skill)["0b"](state)
    except Exception as exc:
        message = str(exc)
    else:
        raise AssertionError("browser_login should fail when CDP launch times out")

    assert "CDP" in message
    assert not calls.exists()


def test_form_scan_writes_explore_result_and_case_inputs(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    config_path = case_dir / "config.json"
    case_plan_path = case_dir / "case_plan.json"
    case_dir.mkdir(parents=True)
    config_path.write_text("{}", encoding="utf-8")
    case_plan_path.write_text(
        json.dumps({"form_fields": {}, "field_groups": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    fields = [
        {"label": "客户标识", "type": "input", "value": "A1", "is_required": True},
        {"label": "交易机构", "type": "tree-select", "value": "__FIRST_OPTION__", "is_required": False},
    ]
    _write_script(
        skill / "scripts" / "stagehand_explorer" / "src" / "explore_wrapper.py",
        "import json, pathlib, sys\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])\n"
        f"out.write_text({json.dumps(json.dumps(fields, ensure_ascii=False))}, encoding='utf-8')\n",
    )
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json, sys\n"
        f"print({json.dumps(json.dumps({'all': fields, 'required': ['客户标识'], 'excluded_fields': ['交易机构']}, ensure_ascii=False))})\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(config_path),
        case_dir=str(case_dir),
        case_plan_path=str(case_plan_path),
    )

    result = build_default_step_handlers(skill)["1a"](state)

    case_plan = json.loads(case_plan_path.read_text(encoding="utf-8"))
    assert result.completed_steps == ["1a"]
    assert (case_dir / "explore_result.json").exists()
    assert case_plan["form_fields"]["required"] == ["客户标识"]
    assert "querymulti" in case_plan["case_inputs"]
    assert case_plan["case_inputs"]["querymulti"]["excluded"]["交易机构"] == "tree_select_unstable"


def test_form_scan_fails_fast_when_no_fields_are_found(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    config_path = case_dir / "config.json"
    case_plan_path = case_dir / "case_plan.json"
    case_dir.mkdir(parents=True)
    config_path.write_text("{}", encoding="utf-8")
    case_plan_path.write_text(json.dumps({"form_fields": {}, "field_groups": []}), encoding="utf-8")
    _write_script(
        skill / "scripts" / "stagehand_explorer" / "src" / "explore_wrapper.py",
        "import pathlib, sys\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('-o') + 1])\n"
        "out.write_text('[]', encoding='utf-8')\n",
    )
    _write_script(
        skill / "scripts" / "config_reader.py",
        "print('{\"all\": [], \"required\": [], \"excluded_fields\": []}')\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(config_path),
        case_dir=str(case_dir),
        case_plan_path=str(case_plan_path),
    )

    try:
        build_default_step_handlers(skill)["1a"](state)
    except EnvironmentFailure as exc:
        assert "0 fields" in str(exc)
    else:
        raise AssertionError("form_scan should fail when no fields are found")


def test_log_capture_builds_actions_and_saves_query_log(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    config_path = case_dir / "config.json"
    case_plan_path = case_dir / "case_plan.json"
    explore_path = case_dir / "explore_result.json"
    config_path.write_text(
        json.dumps(
            {
                "env": {
                    "app_server_host": "host",
                    "app_server_port": "22",
                    "app_server_user": "user",
                    "app_server_password": "pass",
                },
                "computed": {"log_path_full": "/app/log"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    case_plan_path.write_text(
        json.dumps(
            {"form_fields": {"all": [{"label": "客户标识", "type": "input", "value": "A1"}]}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    explore_path.write_text("[]", encoding="utf-8")
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json, sys\n"
        "print(json.dumps([{'action':'fill','label':'客户标识','value':'A1'},{'action':'click','label':'查询'}], ensure_ascii=False))\n",
    )
    _write_script(
        skill / "scripts" / "cdp_executor.py",
        "import pathlib, sys\n"
        "log = pathlib.Path(sys.argv[sys.argv.index('--save-log') + 1])\n"
        "log.write_text('当前SQL: select * from tbclientmap where bank_acc=?', encoding='utf-8')\n"
    )

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(config_path),
        case_dir=str(case_dir),
        case_plan_path=str(case_plan_path),
    )

    result = build_default_step_handlers(skill)["1b"](state)

    assert result.completed_steps == ["1b"]
    assert (case_dir / "actions_query.json").exists()
    actions = json.loads((case_dir / "actions_query.json").read_text(encoding="utf-8"))
    assert actions[0]["action"] == "reset"
    assert (case_dir / "query_log.txt").read_text(encoding="utf-8").startswith("当前SQL")


def test_log_capture_fails_when_captured_sql_log_is_empty(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    config_path = case_dir / "config.json"
    case_plan_path = case_dir / "case_plan.json"
    explore_path = case_dir / "explore_result.json"
    config_path.write_text(json.dumps({"env": {}, "computed": {}}, ensure_ascii=False), encoding="utf-8")
    case_plan_path.write_text(
        json.dumps(
            {"form_fields": {"all": [{"label": "客户标识", "type": "input", "value": "A1"}]}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    explore_path.write_text("[]", encoding="utf-8")
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json\n"
        "print(json.dumps([{'action':'fill','label':'客户标识','value':'A1'},{'action':'click','label':'查询'}], ensure_ascii=False))\n",
    )
    _write_script(
        skill / "scripts" / "cdp_executor.py",
        "import pathlib, sys\n"
        "log = pathlib.Path(sys.argv[sys.argv.index('--save-log') + 1])\n"
        "log.write_text('', encoding='utf-8')\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(config_path),
        case_dir=str(case_dir),
        case_plan_path=str(case_plan_path),
    )

    try:
        build_default_step_handlers(skill)["1b"](state)
    except Exception as exc:
        message = str(exc)
    else:
        raise AssertionError("log_capture should fail when query_log.txt is empty")

    assert "SQL log is empty" in message


def test_log_capture_uses_planned_querymulti_fields_instead_of_all_fields(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    config_path = case_dir / "config.json"
    case_plan_path = case_dir / "case_plan.json"
    explore_path = case_dir / "explore_result.json"
    captured_path = case_dir / "captured_fields.json"
    config_path.write_text(json.dumps({"env": {}, "computed": {}}, ensure_ascii=False), encoding="utf-8")
    case_plan_path.write_text(
        json.dumps(
            {
                "form_fields": {
                    "all": [
                        {"label": "产品起息日-截至", "type": "single-select", "value": "__FIRST_OPTION__"},
                        {"label": "TA代码", "type": "single-select", "value": "HT:HT基金"},
                    ]
                },
                "case_inputs": {
                    "querymulti": {
                        "fields": [
                            {"label": "TA代码", "type": "single-select", "value": "HT:HT基金", "reason": "stable_multi"}
                        ],
                        "excluded": {},
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    explore_path.write_text("[]", encoding="utf-8")
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json, pathlib, sys\n"
        "fields = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
        f"pathlib.Path({json.dumps(str(captured_path))}).write_text(json.dumps(fields, ensure_ascii=False), encoding='utf-8')\n"
        "print(json.dumps([{'action':'select','label':f['label'],'value':f['value']} for f in fields] + [{'action':'click','label':'查询'}], ensure_ascii=False))\n",
    )
    _write_script(
        skill / "scripts" / "cdp_executor.py",
        "import pathlib, sys\n"
        "log = pathlib.Path(sys.argv[sys.argv.index('--save-log') + 1])\n"
        "log.write_text('当前SQL: select 1', encoding='utf-8')\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(config_path),
        case_dir=str(case_dir),
        case_plan_path=str(case_plan_path),
    )

    result = build_default_step_handlers(skill)["1b"](state)

    captured = json.loads(captured_path.read_text(encoding="utf-8"))
    assert result.completed_steps == ["1b"]
    assert [field["label"] for field in captured] == ["TA代码"]
    actions = json.loads((case_dir / "actions_query.json").read_text(encoding="utf-8"))
    assert actions[0]["action"] == "reset"


def test_log_capture_runs_non_blocking_sql_probe_for_extra_stable_fields(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    config_path = case_dir / "config.json"
    case_plan_path = case_dir / "case_plan.json"
    explore_path = case_dir / "explore_result.json"
    captured_dir = case_dir / "captured"
    captured_dir.mkdir()
    config_path.write_text(json.dumps({"env": {}, "computed": {}}, ensure_ascii=False), encoding="utf-8")
    case_plan_path.write_text(
        json.dumps(
            {
                "form_fields": {
                    "all": [
                        {"label": "TA代码", "type": "single-select", "value": "HT:HT基金", "options": []},
                        {"label": "产品代码", "type": "single-select", "value": "P1:产品1", "options": []},
                        {"label": "产品名称", "type": "input", "value": "产品1", "options": []},
                        {"label": "产品状态", "type": "single-select", "value": "__FIRST_OPTION__", "options": []},
                        {"label": "风险等级", "type": "single-select", "value": "R1:低", "options": []},
                    ]
                },
                "case_inputs": {
                    "querymulti": {
                        "fields": [
                            {"label": "TA代码", "type": "single-select", "value": "HT:HT基金", "reason": "stable_multi"},
                            {"label": "产品代码", "type": "single-select", "value": "P1:产品1", "reason": "stable_multi"},
                        ],
                        "excluded": {},
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    explore_path.write_text("[]", encoding="utf-8")
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json, pathlib, sys\n"
        "fields = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
        "labels = [f['label'] for f in fields]\n"
        "print(json.dumps([{'action':'select' if f['type'].endswith('select') else 'fill','label':f['label'],'value':f['value']} for f in fields] + [{'action':'click','label':'查询'}], ensure_ascii=False))\n",
    )
    _write_script(
        skill / "scripts" / "cdp_executor.py",
        "import json, pathlib, sys\n"
        "actions = json.loads(pathlib.Path(sys.argv[sys.argv.index('-a') + 1]).read_text(encoding='utf-8'))\n"
        "labels = [a.get('label') for a in actions if a.get('action') != 'reset']\n"
        "log = pathlib.Path(sys.argv[sys.argv.index('--save-log') + 1])\n"
        "if '风险等级' in labels:\n"
        "    raise SystemExit('probe field failed')\n"
        "log.write_text('当前SQL: select ' + ','.join(label for label in labels if label), encoding='utf-8')\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(config_path),
        case_dir=str(case_dir),
        case_plan_path=str(case_plan_path),
    )

    result = build_default_step_handlers(skill)["1b"](state)

    case_plan = json.loads(case_plan_path.read_text(encoding="utf-8"))
    query_log = (case_dir / "query_log.txt").read_text(encoding="utf-8")
    attempts = {item["label"]: item["status"] for item in case_plan["sql_probe"]["attempts"]}
    assert result.completed_steps == ["1b"]
    assert "产品名称" in query_log
    assert attempts["产品名称"] == "success"
    assert attempts["风险等级"] == "failed"
    assert attempts["产品状态"] == "skipped"


def test_log_capture_runs_full_stable_survey_probe_without_polluting_main_query(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    config_path = case_dir / "config.json"
    case_plan_path = case_dir / "case_plan.json"
    explore_path = case_dir / "explore_result.json"
    calls_path = case_dir / "calls.jsonl"
    config_path.write_text(json.dumps({"env": {}, "computed": {}}, ensure_ascii=False), encoding="utf-8")
    case_plan_path.write_text(
        json.dumps(
            {
                "form_fields": {
                    "all": [
                        {"label": "TA代码", "type": "single-select", "value": "HT:HT基金", "options": []},
                        {"label": "产品代码", "type": "single-select", "value": "P1:产品1", "options": []},
                        {"label": "产品名称", "type": "input", "value": "产品1", "options": []},
                        {"label": "产品状态", "type": "single-select", "value": "__FIRST_OPTION__", "options": []},
                        {"label": "交易机构", "type": "tree-select", "value": "__FIRST_OPTION__", "options": []},
                        {"label": "申请编号", "type": "input", "value": "SQ1", "options": []},
                    ]
                },
                "case_inputs": {
                    "querymulti": {
                        "fields": [
                            {"label": "TA代码", "type": "single-select", "value": "HT:HT基金", "reason": "stable_multi"}
                        ],
                        "excluded": {
                            "交易机构": "tree_select_unstable",
                            "申请编号": "volatile_unique",
                        },
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    explore_path.write_text("[]", encoding="utf-8")
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json, pathlib, sys\n"
        "fields = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
        "print(json.dumps([{'action':'select' if f['type'].endswith('select') else 'fill','label':f['label'],'value':f['value']} for f in fields] + [{'action':'click','label':'查询'}], ensure_ascii=False))\n",
    )
    _write_script(
        skill / "scripts" / "cdp_executor.py",
        "import json, pathlib, sys\n"
        "actions = json.loads(pathlib.Path(sys.argv[sys.argv.index('-a') + 1]).read_text(encoding='utf-8'))\n"
        "labels = [a.get('label') for a in actions if a.get('action') != 'reset' and a.get('label') != '查询']\n"
        f"with pathlib.Path({json.dumps(str(calls_path))}).open('a', encoding='utf-8') as f: f.write(json.dumps(labels, ensure_ascii=False) + '\\n')\n"
        "log = pathlib.Path(sys.argv[sys.argv.index('--save-log') + 1])\n"
        "log.write_text('当前SQL: select ' + ','.join(labels), encoding='utf-8')\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(config_path),
        case_dir=str(case_dir),
        case_plan_path=str(case_plan_path),
    )

    result = build_default_step_handlers(skill)["1b"](state)

    case_plan = json.loads(case_plan_path.read_text(encoding="utf-8"))
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    survey_attempt = next(item for item in case_plan["sql_probe"]["attempts"] if item["label"] == "survey_full_stable")
    assert result.completed_steps == ["1b"]
    assert calls[0] == ["TA代码"]
    assert ["TA代码", "产品代码", "产品名称"] in calls
    assert "产品状态" not in survey_attempt["fields"]
    assert "交易机构" not in survey_attempt["fields"]
    assert "申请编号" not in survey_attempt["fields"]


def test_log_capture_probes_customer_identity_group_with_and_without_bound_fields(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    config_path = case_dir / "config.json"
    case_plan_path = case_dir / "case_plan.json"
    explore_path = case_dir / "explore_result.json"
    calls_path = case_dir / "probe_calls.jsonl"
    config_path.write_text(json.dumps({"env": {}, "computed": {}}, ensure_ascii=False), encoding="utf-8")
    case_plan_path.write_text(
        json.dumps(
            {
                "form_fields": {
                    "all": [
                        {
                            "label": "客户标识类型",
                            "type": "single-select",
                            "value": "0:入账账号",
                            "is_required": False,
                            "options": [],
                        },
                        {
                            "label": "客户标识",
                            "type": "input",
                            "value": "A33060210021113",
                            "is_required": False,
                            "options": [],
                        },
                        {
                            "label": "TA代码",
                            "type": "single-select",
                            "value": "HT:HT基金",
                            "is_required": False,
                            "options": [],
                        },
                        {
                            "label": "产品代码",
                            "type": "single-select",
                            "value": "P1:产品1",
                            "is_required": False,
                            "options": [],
                        },
                    ]
                },
                "field_groups": [
                    {
                        "name": "客户标识组",
                        "fields": ["客户标识类型", "客户标识"],
                        "order": ["客户标识类型", "客户标识"],
                        "conditional": True,
                    }
                ],
                "case_inputs": {
                    "querymulti": {
                        "fields": [
                            {
                                "label": "客户标识类型",
                                "type": "single-select",
                                "value": "0:入账账号",
                                "reason": "stable_multi",
                            },
                            {
                                "label": "客户标识",
                                "type": "input",
                                "value": "A33060210021113",
                                "reason": "stable_multi",
                            },
                            {"label": "TA代码", "type": "single-select", "value": "HT:HT基金", "reason": "stable_multi"},
                            {"label": "产品代码", "type": "single-select", "value": "P1:产品1", "reason": "stable_multi"},
                        ],
                        "excluded": {},
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    explore_path.write_text("[]", encoding="utf-8")
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json, pathlib, sys\n"
        "fields = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
        "print(json.dumps([{'action':'select' if f['type'].endswith('select') else 'fill','label':f['label'],'value':f['value']} for f in fields] + [{'action':'click','label':'查询'}], ensure_ascii=False))\n",
    )
    _write_script(
        skill / "scripts" / "cdp_executor.py",
        "import json, pathlib, sys\n"
        "actions = json.loads(pathlib.Path(sys.argv[sys.argv.index('-a') + 1]).read_text(encoding='utf-8'))\n"
        "labels = [a.get('label') for a in actions if a.get('action') != 'reset' and a.get('label') != '查询']\n"
        f"with pathlib.Path({json.dumps(str(calls_path))}).open('a', encoding='utf-8') as f: f.write(json.dumps(labels, ensure_ascii=False) + '\\n')\n"
        "log = pathlib.Path(sys.argv[sys.argv.index('--save-log') + 1])\n"
        "log.write_text('当前SQL: select ' + ','.join(labels), encoding='utf-8')\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(config_path),
        case_dir=str(case_dir),
        case_plan_path=str(case_plan_path),
    )

    result = build_default_step_handlers(skill)["1b"](state)

    case_plan = json.loads(case_plan_path.read_text(encoding="utf-8"))
    calls = [
        json.loads(line)
        for line in calls_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    bound_attempts = [
        item for item in case_plan["sql_probe"]["attempts"]
        if item.get("kind") == "bound_group" and item.get("group") == "客户标识组"
    ]
    assert result.completed_steps == ["1b"]
    assert any("客户标识" not in labels and "客户标识类型" not in labels for labels in calls)
    assert any("客户标识" in labels and "客户标识类型" in labels for labels in calls)
    assert {item["variant"] for item in bound_attempts} == {"without_group", "with_group"}


def test_log_capture_probes_implicit_customer_identity_group_without_configured_field_group(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    config_path = case_dir / "config.json"
    case_plan_path = case_dir / "case_plan.json"
    explore_path = case_dir / "explore_result.json"
    calls_path = case_dir / "probe_calls.jsonl"
    config_path.write_text(json.dumps({"env": {}, "computed": {}}, ensure_ascii=False), encoding="utf-8")
    case_plan_path.write_text(
        json.dumps(
            {
                "form_fields": {
                    "all": [
                        {"label": "客户标识类型", "type": "single-select", "value": "0:入账账号", "is_required": False},
                        {"label": "客户标识", "type": "input", "value": "A33060210021113", "is_required": False},
                        {"label": "TA代码", "type": "single-select", "value": "HT:HT基金", "is_required": False},
                    ]
                },
                "field_groups": [],
                "case_inputs": {
                    "querymulti": {
                        "fields": [
                            {"label": "客户标识类型", "type": "single-select", "value": "0:入账账号"},
                            {"label": "客户标识", "type": "input", "value": "A33060210021113"},
                            {"label": "TA代码", "type": "single-select", "value": "HT:HT基金"},
                        ],
                        "excluded": {},
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    explore_path.write_text("[]", encoding="utf-8")
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json, pathlib, sys\n"
        "fields = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
        "print(json.dumps([{'action':'select' if f['type'].endswith('select') else 'fill','label':f['label'],'value':f['value']} for f in fields] + [{'action':'click','label':'查询'}], ensure_ascii=False))\n",
    )
    _write_script(
        skill / "scripts" / "cdp_executor.py",
        "import json, pathlib, sys\n"
        "actions = json.loads(pathlib.Path(sys.argv[sys.argv.index('-a') + 1]).read_text(encoding='utf-8'))\n"
        "assert actions[0]['action'] == 'reset'\n"
        "labels = [a.get('label') for a in actions if a.get('action') != 'reset' and a.get('label') != '查询']\n"
        f"with pathlib.Path({json.dumps(str(calls_path))}).open('a', encoding='utf-8') as f: f.write(json.dumps(labels, ensure_ascii=False) + '\\n')\n"
        "log = pathlib.Path(sys.argv[sys.argv.index('--save-log') + 1])\n"
        "log.write_text('当前SQL: select ' + ','.join(labels), encoding='utf-8')\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(config_path),
        case_dir=str(case_dir),
        case_plan_path=str(case_plan_path),
    )

    result = build_default_step_handlers(skill)["1b"](state)

    case_plan = json.loads(case_plan_path.read_text(encoding="utf-8"))
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    bound_attempts = [item for item in case_plan["sql_probe"]["attempts"] if item.get("kind") == "bound_group"]
    assert result.completed_steps == ["1b"]
    assert any("客户标识" not in labels and "客户标识类型" not in labels for labels in calls)
    assert any("客户标识" in labels and "客户标识类型" in labels for labels in calls)
    assert {item["variant"] for item in bound_attempts} == {"without_group", "with_group"}


def test_log_capture_skips_duplicate_sql_probe_field_sets(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    config_path = case_dir / "config.json"
    case_plan_path = case_dir / "case_plan.json"
    explore_path = case_dir / "explore_result.json"
    calls_path = case_dir / "probe_calls.jsonl"
    config_path.write_text(json.dumps({"env": {}, "computed": {}}, ensure_ascii=False), encoding="utf-8")
    case_plan_path.write_text(
        json.dumps(
            {
                "form_fields": {
                    "all": [
                        {"label": "客户标识类型", "type": "single-select", "value": "0:入账账号", "is_required": False},
                        {"label": "客户标识", "type": "input", "value": "A33060210021113", "is_required": False},
                        {"label": "TA代码", "type": "single-select", "value": "HT:HT基金", "is_required": False},
                    ]
                },
                "field_groups": [],
                "case_inputs": {
                    "querymulti": {
                        "fields": [
                            {"label": "客户标识类型", "type": "single-select", "value": "0:入账账号"},
                            {"label": "客户标识", "type": "input", "value": "A33060210021113"},
                            {"label": "TA代码", "type": "single-select", "value": "HT:HT基金"},
                        ],
                        "excluded": {},
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    explore_path.write_text("[]", encoding="utf-8")
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json, pathlib, sys\n"
        "fields = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
        "print(json.dumps([{'action':'select' if f['type'].endswith('select') else 'fill','label':f['label'],'value':f['value']} for f in fields] + [{'action':'click','label':'查询'}], ensure_ascii=False))\n",
    )
    _write_script(
        skill / "scripts" / "cdp_executor.py",
        "import json, pathlib, sys\n"
        "actions = json.loads(pathlib.Path(sys.argv[sys.argv.index('-a') + 1]).read_text(encoding='utf-8'))\n"
        "labels = [a.get('label') for a in actions if a.get('action') != 'reset' and a.get('label') != '查询']\n"
        f"with pathlib.Path({json.dumps(str(calls_path))}).open('a', encoding='utf-8') as f: f.write(json.dumps(labels, ensure_ascii=False) + '\\n')\n"
        "log = pathlib.Path(sys.argv[sys.argv.index('--save-log') + 1])\n"
        "log.write_text('当前SQL: select ' + ','.join(labels), encoding='utf-8')\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(config_path),
        case_dir=str(case_dir),
        case_plan_path=str(case_plan_path),
    )

    build_default_step_handlers(skill)["1b"](state)

    case_plan = json.loads(case_plan_path.read_text(encoding="utf-8"))
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    duplicate_attempt = next(
        item
        for item in case_plan["sql_probe"]["attempts"]
        if item.get("kind") == "bound_group" and item.get("variant") == "with_group"
    )
    assert calls.count(["客户标识类型", "客户标识", "TA代码"]) == 1
    assert duplicate_attempt["status"] == "skipped"
    assert duplicate_attempt["reason"] == "duplicate_field_set"


def test_log_capture_probes_account_type_and_account_bound_alias_group(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    config_path = case_dir / "config.json"
    case_plan_path = case_dir / "case_plan.json"
    explore_path = case_dir / "explore_result.json"
    calls_path = case_dir / "probe_calls.jsonl"
    config_path.write_text(json.dumps({"env": {}, "computed": {}}, ensure_ascii=False), encoding="utf-8")
    case_plan_path.write_text(
        json.dumps(
            {
                "form_fields": {
                    "all": [
                        {"label": "账户类型", "type": "single-select", "value": "0:入账账号", "is_required": False},
                        {"label": "账号", "type": "input", "value": "A33060210021113", "is_required": False},
                        {"label": "TA代码", "type": "single-select", "value": "HT:HT基金", "is_required": False},
                    ]
                },
                "field_groups": [
                    {
                        "name": "客户标识组",
                        "fields": ["客户标识类型", "客户标识"],
                        "order": ["客户标识类型", "客户标识"],
                        "aliases": {"账户类型": "客户标识类型", "账号": "客户标识"},
                        "conditional": True,
                    }
                ],
                "case_inputs": {
                    "querymulti": {
                        "fields": [
                            {"label": "账户类型", "type": "single-select", "value": "0:入账账号"},
                            {"label": "账号", "type": "input", "value": "A33060210021113"},
                            {"label": "TA代码", "type": "single-select", "value": "HT:HT基金"},
                        ],
                        "excluded": {},
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    explore_path.write_text("[]", encoding="utf-8")
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json, pathlib, sys\n"
        "fields = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
        "print(json.dumps([{'action':'select' if f['type'].endswith('select') else 'fill','label':f['label'],'value':f['value']} for f in fields] + [{'action':'click','label':'查询'}], ensure_ascii=False))\n",
    )
    _write_script(
        skill / "scripts" / "cdp_executor.py",
        "import json, pathlib, sys\n"
        "actions = json.loads(pathlib.Path(sys.argv[sys.argv.index('-a') + 1]).read_text(encoding='utf-8'))\n"
        "assert actions[0]['action'] == 'reset'\n"
        "labels = [a.get('label') for a in actions if a.get('action') != 'reset' and a.get('label') != '查询']\n"
        f"with pathlib.Path({json.dumps(str(calls_path))}).open('a', encoding='utf-8') as f: f.write(json.dumps(labels, ensure_ascii=False) + '\\n')\n"
        "log = pathlib.Path(sys.argv[sys.argv.index('--save-log') + 1])\n"
        "log.write_text('当前SQL: select ' + ','.join(labels), encoding='utf-8')\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(config_path),
        case_dir=str(case_dir),
        case_plan_path=str(case_plan_path),
    )

    result = build_default_step_handlers(skill)["1b"](state)

    case_plan = json.loads(case_plan_path.read_text(encoding="utf-8"))
    calls = [json.loads(line) for line in calls_path.read_text(encoding="utf-8").splitlines()]
    bound_attempts = [item for item in case_plan["sql_probe"]["attempts"] if item.get("kind") == "bound_group"]
    assert result.completed_steps == ["1b"]
    assert any("账号" not in labels and "账户类型" not in labels for labels in calls)
    assert any("账号" in labels and "账户类型" in labels for labels in calls)
    assert {item["variant"] for item in bound_attempts} == {"without_group", "with_group"}


def test_log_analysis_updates_case_plan_from_sql_extractor(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    case_plan_path = case_dir / "case_plan.json"
    (case_dir / "query_log.txt").write_text("当前SQL: select", encoding="utf-8")
    case_plan_path.write_text("{}", encoding="utf-8")
    analysis = {
        "requirements": {
            "where_columns": {"tbaccreq": ["bank_acc"]},
            "join_tables": ["tbclientseller"],
            "join_key_columns": {"tbaccreq": ["client_no"], "tbclientseller": ["client_no"]},
            "raw_sqls": ["select * from tbclientmap a join tbclientseller b on a.client_no=b.client_no"],
        },
        "cached_tables": ["tbaccreq"],
        "missing_tables": [],
        "physical_tables": {"tbaccreq": ["tbaccreq"]},
    }
    _write_script(
        skill / "scripts" / "sql_extractor.py",
        f"import json\nprint({json.dumps(json.dumps(analysis, ensure_ascii=False))})\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        case_dir=str(case_dir),
        case_plan_path=str(case_plan_path),
    )

    result = build_default_step_handlers(skill)["2a"](state)

    case_plan = json.loads(case_plan_path.read_text(encoding="utf-8"))
    assert result.completed_steps == ["2a"]
    assert case_plan["log_analysis"]["cached_tables"] == ["tbaccreq"]
    # tbclientseller 是业务 JOIN 必需表，不能再按系统表硬过滤
    assert case_plan["log_analysis"]["requirements"]["join_tables"] == ["tbclientseller"]
    assert case_plan["log_analysis"]["requirements"]["join_key_columns"]["tbaccreq"] == ["client_no"]
    assert case_plan["shard"]["physical_tables"]["tbaccreq"] == ["tbaccreq"]


def test_log_analysis_writes_ast_diagnostic_artifacts(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    case_plan_path = case_dir / "case_plan.json"
    (case_dir / "query_log.txt").write_text("当前SQL: select * from biztable where bank_acc='A1'", encoding="utf-8")
    case_plan_path.write_text("{}", encoding="utf-8")
    analysis = {
        "requirements": {
            "where_columns": {"biztable": ["bank_acc"]},
            "raw_sqls": ["select * from biztable where bank_acc='A1'"],
        },
        "physical_tables": {"biztable": ["biztable"]},
        "ast_diagnostics": {
            "select_count": 1,
            "selects": [
                {
                    "index": 1,
                    "raw_sql": "select * from biztable where bank_acc='A1'",
                    "normalized_sql": "SELECT * FROM biztable WHERE bank_acc = 'A1'",
                    "tables": ["biztable"],
                    "join_tables": [],
                    "where_columns": {"biztable": ["bank_acc"]},
                    "join_key_columns": {},
                    "diagnostics": [],
                }
            ],
            "text": "-- raw_sql[1]\nselect * from biztable where bank_acc='A1'\n-- tables\n-- main: biztable\n",
        },
    }
    _write_script(
        skill / "scripts" / "sql_extractor.py",
        f"import json\nprint({json.dumps(json.dumps(analysis, ensure_ascii=False))})\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        case_dir=str(case_dir),
        case_plan_path=str(case_plan_path),
    )

    build_default_step_handlers(skill)["2a"](state)

    case_plan = json.loads(case_plan_path.read_text(encoding="utf-8"))
    ast_paths = case_plan["log_analysis"]["ast_artifacts"]
    ast_sql = case_dir / ast_paths["sql"]
    ast_json = case_dir / ast_paths["json"]
    assert ast_sql.exists()
    assert ast_json.exists()
    assert "-- raw_sql[1]" in ast_sql.read_text(encoding="utf-8")
    assert json.loads(ast_json.read_text(encoding="utf-8"))["select_count"] == 1


def test_log_analysis_filters_system_tables_from_physical_tables(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    case_plan_path = case_dir / "case_plan.json"
    case_plan_path.write_text("{}", encoding="utf-8")
    (case_dir / "query_log.txt").write_text("log", encoding="utf-8")
    analysis = {
        "cached_tables": ["tbaccreq", "tsys_menu", "tbuserbranch"],
        "missing_tables": ["tbsequence"],
        "describe_needed": ["tbaccreq", "tsys_menu", "tbuserbranch"],
        "physical_tables": {
            "tbaccreq": ["tbaccreq"],
            "tsys_menu": ["tsys_menu"],
            "tbsequence": ["tbsequence"],
            "tbuserbranch": ["tbuserbranch"],
        },
    }
    _write_script(
        skill / "scripts" / "sql_extractor.py",
        f"import json\nprint({json.dumps(json.dumps(analysis, ensure_ascii=False))})\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        case_dir=str(case_dir),
        case_plan_path=str(case_plan_path),
    )

    build_default_step_handlers(skill)["2a"](state)

    case_plan = json.loads(case_plan_path.read_text(encoding="utf-8"))
    assert case_plan["log_analysis"]["cached_tables"] == ["tbaccreq"]
    assert case_plan["log_analysis"]["filtered_system_tables"] == ["tbsequence", "tbuserbranch", "tsys_menu"]
    assert case_plan["shard"]["physical_tables"] == {"tbaccreq": ["tbaccreq"]}


def test_log_analysis_filters_workflow_tables_from_physical_tables(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    case_plan_path = case_dir / "case_plan.json"
    case_plan_path.write_text("{}", encoding="utf-8")
    (case_dir / "query_log.txt").write_text("log", encoding="utf-8")
    analysis = {
        "cached_tables": ["tbaccreq", "tbworkflowsubtrans", "ACT_RU_TASK", "flow_instance"],
        "missing_tables": ["ACT_RU_EXECUTION", "flow_cust_nodecfg"],
        "describe_needed": ["tbaccreq", "tbworkflowsubtrans", "ACT_RU_TASK", "flow_instance"],
        "physical_tables": {
            "tbaccreq": ["tbaccreq"],
            "tbworkflowsubtrans": ["tbworkflowsubtrans"],
            "ACT_RU_TASK": ["ACT_RU_TASK"],
            "ACT_RU_EXECUTION": ["ACT_RU_EXECUTION"],
            "flow_instance": ["flow_instance"],
            "flow_cust_nodecfg": ["flow_cust_nodecfg"],
        },
    }
    _write_script(
        skill / "scripts" / "sql_extractor.py",
        f"import json\nprint({json.dumps(json.dumps(analysis, ensure_ascii=False))})\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        case_dir=str(case_dir),
        case_plan_path=str(case_plan_path),
    )

    build_default_step_handlers(skill)["2a"](state)

    case_plan = json.loads(case_plan_path.read_text(encoding="utf-8"))
    assert case_plan["log_analysis"]["cached_tables"] == ["tbaccreq"]
    assert case_plan["log_analysis"]["filtered_system_tables"] == [
        "ACT_RU_EXECUTION",
        "ACT_RU_TASK",
        "flow_cust_nodecfg",
        "flow_instance",
        "tbworkflowsubtrans",
    ]
    assert case_plan["shard"]["physical_tables"] == {"tbaccreq": ["tbaccreq"]}


def test_table_classify_updates_preset_strategy_and_shard_db(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    config_path = case_dir / "config.json"
    case_plan_path = case_dir / "case_plan.json"
    config_path.write_text(
        json.dumps(
            {
                "env": {"env_key": "ifm60-trust", "project_dir": "F:/project"},
                "computed": {"module": "dxtrust"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    case_plan_path.write_text(
        json.dumps({"preset_strategy": {}, "shard": {}}, ensure_ascii=False),
        encoding="utf-8",
    )
    classify = {
        "tbaccreq": {"classification": "routing", "db": "ui_bc"},
        "tbdxtrustshare": {"classification": "business", "db": "ui_trust2"},
        "tbtainfo": {"classification": "system", "db": "ui_bc"},
    }
    _write_script(
        skill / "scripts" / "sql_extractor.py",
        f"import json\nprint({json.dumps(json.dumps(classify, ensure_ascii=False))})\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(config_path),
        case_plan_path=str(case_plan_path),
    )

    result = build_default_step_handlers(skill)["2b"](state)

    case_plan = json.loads(case_plan_path.read_text(encoding="utf-8"))
    assert result.completed_steps == ["2b"]
    assert case_plan["preset_strategy"]["base_tables"] == ["tbaccreq"]
    assert case_plan["preset_strategy"]["biz_tables"] == ["tbdxtrustshare"]
    assert case_plan["preset_strategy"]["no_insert_tables"] == ["tbtainfo"]
    assert case_plan["shard"]["db"] == "ui_trust2"


def test_table_classify_uses_computed_customer_shard_as_routing_db(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    config_path = case_dir / "config.json"
    case_plan_path = case_dir / "case_plan.json"
    config_path.write_text(
        json.dumps(
            {
                "env": {"env_key": "ifm60-dxfund", "project_dir": "F:/project"},
                "computed": {"module": "dxfund"},
                "presets": {"customer_id": "A33060210021113"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    case_plan_path.write_text(
        json.dumps(
            {
                "preset_strategy": {},
                "shard": {"physical_tables": {"tbdxfundtranscfm": ["tbdxfundtranscfm13"]}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json, sys\n"
        "if sys.argv[1:3] == ['compute_shard', 'dxfund']:\n"
        "    print(json.dumps({'db_name': 'ui_fund2', 'in_client_no': '33060210021113', 'last8': '10021113', 'trans_db_num': 2, 'trans_table_num': 13}, ensure_ascii=False))\n"
        "else:\n"
        "    print('{}')\n",
    )
    _write_script(
        skill / "scripts" / "sql_extractor.py",
        "import json, pathlib, sys\n"
        "cp = json.loads(pathlib.Path(sys.argv[2]).read_text(encoding='utf-8'))\n"
        "assert cp['shard']['routing_db'] == 'ui_fund2'\n"
        "print(json.dumps({'tbdxfundtranscfm': {'classification': 'business', 'db': 'ui_fund2'}}, ensure_ascii=False))\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(config_path),
        case_plan_path=str(case_plan_path),
    )

    result = build_default_step_handlers(skill)["2b"](state)

    case_plan = json.loads(case_plan_path.read_text(encoding="utf-8"))
    assert result.completed_steps == ["2b"]
    assert case_plan["shard"]["db"] == "ui_fund2"
    assert case_plan["shard"]["routing_db"] == "ui_fund2"
    assert case_plan["shard"]["formula"]["trans_table_num"] == 13


def test_table_classify_writes_routed_ast_diagnostic_sql(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    analysis_dir = case_dir / "sql_analysis"
    case_dir.mkdir()
    analysis_dir.mkdir()
    config_path = case_dir / "config.json"
    case_plan_path = case_dir / "case_plan.json"
    ast_sql_path = analysis_dir / "ast_selects.sql"
    ast_sql_path.write_text("SELECT * FROM tbdxfundtranscfm13 WHERE bank_acc = 'A1';", encoding="utf-8")
    config_path.write_text(json.dumps({"env": {}, "computed": {}}, ensure_ascii=False), encoding="utf-8")
    case_plan_path.write_text(
        json.dumps(
            {
                "preset_strategy": {},
                "shard": {
                    "db": "ui_fund2",
                    "physical_tables": {"tbdxfundtranscfm": ["tbdxfundtranscfm13"]},
                },
                "log_analysis": {
                    "ast_artifacts": {"sql": "sql_analysis/ast_selects.sql"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_script(
        skill / "scripts" / "sql_extractor.py",
        "import json\n"
        "print(json.dumps({'tbdxfundtranscfm': {'classification': 'business', 'db': 'ui_fund2'}}, ensure_ascii=False))\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(config_path),
        case_plan_path=str(case_plan_path),
    )

    build_default_step_handlers(skill)["2b"](state)

    case_plan = json.loads(case_plan_path.read_text(encoding="utf-8"))
    routed_sql = case_dir / case_plan["log_analysis"]["ast_artifacts"]["routed_sql"]
    assert routed_sql.read_text(encoding="utf-8") == "SELECT * FROM ui_fund2.tbdxfundtranscfm13 WHERE bank_acc = 'A1';"


def test_table_classify_applies_llm_sql_no_insert_tables_safely(tmp_path):
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    config_path = case_dir / "config.json"
    case_plan_path = case_dir / "case_plan.json"
    config_path.write_text(json.dumps({"env": {}, "computed": {}}, ensure_ascii=False), encoding="utf-8")
    case_plan_path.write_text(
        json.dumps(
            {
                "preset_strategy": {},
                "shard": {"physical_tables": {"biztable": ["biztable"], "tbmenu_like": ["tbmenu_like"]}},
                "log_analysis": {"requirements": {"raw_sqls": ["select"]}},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    classify = {
        "biztable": {"classification": "business", "db": "ui_fund"},
        "tbmenu_like": {"classification": "business", "db": "ui_fund"},
    }
    _write_script(
        skill / "scripts" / "sql_extractor.py",
        f"import json\nprint({json.dumps(json.dumps(classify, ensure_ascii=False))})\n",
    )

    class SQLAdvisor:
        def judge(self, case_plan):
            return {
                "preset_tables": ["biztable"],
                "base_tables": [],
                "no_insert_tables": ["tbmenu_like", "missing_table"],
                "join_key_columns": {},
                "risks": ["tbmenu_like behaves like a system menu table"],
                "confidence": 0.8,
                "reason": "remove non-business table",
            }

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(config_path),
        case_plan_path=str(case_plan_path),
    )

    build_default_step_handlers(skill, sql_advisor=SQLAdvisor())["2b"](state)

    case_plan = json.loads(case_plan_path.read_text(encoding="utf-8"))
    strategy = case_plan["preset_strategy"]
    assert strategy["biz_tables"] == ["biztable"]
    assert strategy["preset_tables"] == ["biztable"]
    assert "tbmenu_like" in strategy["no_insert_tables"]
    assert case_plan["sql_decision"]["source"] == "llm"
    assert case_plan["sql_decision"]["risks"] == ["tbmenu_like behaves like a system menu table"]


def test_sql_generate_refreshes_schema_cache_on_dry_run_failure(tmp_path):
    """When dry-run fails, build_schema_cache.py should be called to refresh stale cache."""
    skill = tmp_path / "skill"
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    config_path = case_dir / "config.json"
    case_plan_path = case_dir / "case_plan.json"
    base_sql_path = case_dir / "test_base.sql"
    preset_sql_path = case_dir / "test.sql"
    calls_path = case_dir / "calls.jsonl"

    base_sql_path.write_text("DELETE FROM tbt WHERE id = '1';\nINSERT INTO tbt (id) VALUES ('1');\n", encoding="utf-8")
    preset_sql_path.write_text("DELETE FROM tbt WHERE id = '1';\nINSERT INTO tbt (id) VALUES ('1');\n", encoding="utf-8")
    config_path.write_text(
        json.dumps(
            {
                "env": {"env_key": "ifm60-trust", "project_dir": "F:/project"},
                "computed": {"module": "dxtrust"},
                "sql_paths": {"base_sql": str(base_sql_path), "preset_sql": str(preset_sql_path)},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    case_plan_path.write_text(
        json.dumps(
            {
                "menu": {"name": "菜单A", "code": "FUNC_A"},
                "preset_strategy": {"base_tables": [], "biz_tables": ["tbt"], "no_insert_tables": [], "preset_tables": ["tbt"]},
                "shard": {"db": "ui_bc", "physical_tables": {"tbt": ["tbt"]}},
                "sql_paths": {"base_sql": str(base_sql_path), "preset_sql": str(preset_sql_path)},
                "insert_plans": [{"table": "tbt", "physical_table": "tbt", "role": "business", "columns": [{"name": "id", "value": "1", "source": "test"}], "delete_where": "id = '1'"}],
                "case_inputs": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    # dry_run_sql.py always fails
    _write_script(
        skill / "scripts" / "dry_run_sql.py",
        "import json, sys\n"
        "print(json.dumps({'ok': False, 'total': 1, 'passed': 0, 'failed': 1, 'errors': [{'line': 1, 'error': 'Unknown column id'}]}))\n",
    )
    # build_schema_cache.py records calls
    _write_script(
        skill / "scripts" / "build_schema_cache.py",
        "import json, pathlib, sys\n"
        f"pathlib.Path({json.dumps(str(calls_path))}).write_text(json.dumps(sys.argv[1:], ensure_ascii=False), encoding='utf-8')\n"
        "print('schema cache refreshed')\n",
    )
    # config_reader.py minimal stub
    _write_script(
        skill / "scripts" / "config_reader.py",
        "import json, sys\n"
        "print(json.dumps({'project_dir': 'F:/project'}, ensure_ascii=False))\n",
    )

    state = HUITestState.new("菜单A").with_paths(
        config_path=str(config_path),
        case_dir=str(case_dir),
        case_plan_path=str(case_plan_path),
    )

    # Run sql_generate step — it should attempt dry-run, fail, refresh cache, then fail again
    try:
        build_default_step_handlers(skill)["2c"](state)
    except Exception:
        pass  # Expected to fail since dry-run always fails

    # Verify build_schema_cache.py was called with the correct tables
    assert calls_path.exists(), "build_schema_cache.py was NOT called — schema cache refresh is missing"
    calls = json.loads(calls_path.read_text(encoding="utf-8"))
    assert "--tables" in calls
    assert "tbt" in calls
