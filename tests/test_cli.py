import json
import sys
from pathlib import Path

from hui_agent.cli import (
    _format_runtime_event_detail,
    _record_pytest_log_line,
    _record_runtime_event,
    _schema_refresh_dbs,
    build_parser,
    main,
)
from hui_agent.doctor import run_doctor
from hui_agent.run_store import RunStore
from hui_agent.tools.script_runner import ScriptRunner


def test_cli_accepts_required_public_commands():
    parser = build_parser()

    assert parser.parse_args(["menus"]).command == "menus"
    assert parser.parse_args(["doctor"]).command == "doctor"
    assert parser.parse_args(["doctor", "--probe-cdp"]).probe_cdp is True
    assert parser.parse_args(["mcp"]).command == "mcp"
    skill_root = Path(parser.parse_args(["menus"]).skill_root)
    assert skill_root.name == "knowledge"
    assert (skill_root / "scripts" / "config_reader.py").exists()
    assert parser.parse_args(["--runs-root", "runs", "menus"]).runs_root == "runs"
    assert parser.parse_args(["run", "--menu", "基金分红查询"]).menu == "基金分红查询"
    assert parser.parse_args(["batch"]).command == "batch"
    refresh_args = parser.parse_args([
        "batch",
        "--refresh-schema-db",
        "ui_bc",
        "--refresh-schema-module",
        "trust",
        "--refresh-schema-force",
    ])
    assert refresh_args.refresh_schema_db == ["ui_bc"]
    assert refresh_args.refresh_schema_module == ["trust"]
    assert refresh_args.refresh_schema_force is True
    assert parser.parse_args(["status", "--thread-id", "t1"]).thread_id == "t1"
    assert parser.parse_args(["resume", "--thread-id", "t1"]).thread_id == "t1"
    retry_args = parser.parse_args(["retry", "--thread-id", "t1", "--from-step", "2c"])
    assert retry_args.thread_id == "t1"
    assert retry_args.from_step == "2c"


def test_cli_schema_refresh_dbs_keeps_common_db_explicit():
    assert _schema_refresh_dbs(["trust"], ["ui_bc", "ui_trust1"]) == [
        "ui_trust1",
        "ui_trust2",
        "ui_bc",
    ]


def test_cli_formats_verification_and_llm_events_for_live_logs():
    assert _format_runtime_event_detail(
        {
            "event": "pytest_finished",
            "attempt": 1,
            "returncode": 1,
            "status": "failed",
            "summary": "4 failed, 3 passed",
            "failed_tests": ["test_branch", "test_headquarter", "test_querymulti", "test_preview"],
            "passed_tests": ["test_queryall", "test_querysingle", "test_reset"],
        }
    ) == (
        "attempt=1 status=failed returncode=1 summary=4 failed, 3 passed "
        "failed=test_branch,test_headquarter,test_querymulti,+1 more "
        "passed=test_queryall,test_querysingle,test_reset"
    )
    assert _format_runtime_event_detail(
        {
            "event": "llm_repair_finished",
            "attempt": 1,
            "failure_type": "preset_sql_mismatch",
            "confidence": 0.95,
            "summary": "预埋SQL主键冲突",
            "elapsed_ms": 1234,
        }
    ) == "attempt=1 type=preset_sql_mismatch confidence=0.95 elapsed_ms=1234 summary=预埋SQL主键冲突"
    assert _format_runtime_event_detail(
        {
            "event": "repair_actions_finished",
            "attempt": 1,
            "status": "patched",
            "actions": ["patch_sql_delete_where"],
        }
    ) == "attempt=1 status=patched actions=patch_sql_delete_where"
    assert _format_runtime_event_detail(
        {
            "event": "pytest_case_result",
            "attempt": 1,
            "status": "FAILED",
            "case": "test_menu_branch",
        }
    ) == "attempt=1 FAILED test_menu_branch"
    assert _format_runtime_event_detail(
        {
            "event": "sql_dry_run_finished",
            "attempt": 2,
            "kind": "preset_sql",
            "file": "sql/preset.sql",
            "db": "ui_fund",
            "status": "failed",
            "failed": 1,
            "error": "bad account",
        }
    ) == "attempt=2 kind=preset_sql status=failed file=sql/preset.sql db=ui_fund failed=1 error=bad account"
    assert _format_runtime_event_detail(
        {
            "event": "llm_sql_repair_finished",
            "attempt": 1,
            "failure_type": "preset_sql_execution_failed",
            "confidence": 0.91,
            "summary": "rewrite invalid preset SQL",
            "elapsed_ms": 2345,
        }
    ) == "attempt=1 type=preset_sql_execution_failed confidence=0.91 elapsed_ms=2345 summary=rewrite invalid preset SQL"
    assert _format_runtime_event_detail(
        {
            "event": "sql_repair_actions_finished",
            "attempt": 1,
            "status": "patched",
            "actions": ["llm_regenerate_sql:patched@sql/preset.sql"],
        }
    ) == "attempt=1 status=patched actions=llm_regenerate_sql:patched@sql/preset.sql"


def test_cli_records_full_pytest_log_lines_to_attempt_file(tmp_path):
    store = RunStore(tmp_path / "runs")

    _record_pytest_log_line(
        store,
        "thread-1",
        "4",
        {"event": "pytest_log_line", "attempt": 2, "line": "test_a.py::test_ok PASSED"},
    )

    assert (tmp_path / "runs" / "thread-1" / "pytest_attempt_2.log").read_text(encoding="utf-8") == (
        "test_a.py::test_ok PASSED\n"
    )


def test_cli_runtime_event_survives_closed_stderr_pipe(tmp_path, monkeypatch):
    store = RunStore(tmp_path / "runs")

    class BrokenStderr:
        def write(self, value):
            raise OSError(22, "Invalid argument")

        def flush(self):
            raise OSError(22, "Invalid argument")

    monkeypatch.setattr(sys, "stderr", BrokenStderr())

    _record_runtime_event(store, "thread-1", "4", "step_finished", "status=running next=5")

    assert "step_finished" in (tmp_path / "runs" / "thread-1" / "agent.log").read_text(encoding="utf-8")


def test_cli_run_status_and_retry_use_run_store(tmp_path, capsys):
    skill_root = tmp_path / "skill"
    scripts_dir = skill_root / "scripts"
    scripts_dir.mkdir(parents=True)
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    config_reader = scripts_dir / "config_reader.py"
    config_line = json.dumps(f"config.json 已写入: {case_dir / 'config.json'}", ensure_ascii=False)
    config_payload = json.dumps(
        {
            "env": {},
            "menu": {"菜单名称": "基金分红查询"},
            "computed": {"menu_url": "/menu/a", "func_code": "FUNC_A"},
            "field_groups": [],
        },
        ensure_ascii=False,
    )
    config_reader.write_text(
        "import pathlib, sys\n"
        "if sys.argv[1] == 'init':\n"
        f"    pathlib.Path({json.dumps(str(case_dir / 'config.json'))}).write_text({json.dumps(config_payload)}, encoding='utf-8')\n"
        f"    print({config_line})\n"
        "elif sys.argv[1] == 'menus':\n"
        "    print('  基金分红查询')\n",
        encoding="utf-8",
    )
    runs_root = tmp_path / "runs"

    assert main([
        "--skill-root",
        str(skill_root),
        "--runs-root",
        str(runs_root),
        "run",
        "--menu",
        "基金分红查询",
    ]) == 0
    run_output = capsys.readouterr().out
    thread_id = json.loads(run_output)["thread_id"]

    assert main([
        "--runs-root",
        str(runs_root),
        "status",
        "--thread-id",
        thread_id,
    ]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["completed_steps"] == ["0a"]
    assert status["status"] == "blocked"
    assert status["failure_scope"] == "environment"

    assert main([
        "--runs-root",
        str(runs_root),
        "retry",
        "--thread-id",
        thread_id,
        "--from-step",
        "0a",
    ]) == 0
    retried = json.loads(capsys.readouterr().out)
    assert retried["completed_steps"] == []
    assert retried["current_step"] == "0a"
    assert retried["status"] == "retrying"


def test_cli_batch_runs_menus_serially_and_summarizes(tmp_path, capsys):
    skill_root = tmp_path / "skill"
    scripts_dir = skill_root / "scripts"
    scripts_dir.mkdir(parents=True)
    config_reader = scripts_dir / "config_reader.py"
    config_reader.write_text(
        "import pathlib, sys\n"
        "if sys.argv[1] == 'menus':\n"
        "    print('  菜单A')\n"
        "    print('  菜单B')\n"
        "elif sys.argv[1] == 'init':\n"
        "    p = pathlib.Path(f'C:/tmp/{sys.argv[2]}/config.json')\n"
        "    p.parent.mkdir(parents=True, exist_ok=True)\n"
        "    p.write_text('{\"env\":{},\"menu\":{\"菜单名称\":\"x\"},\"computed\":{\"menu_url\":\"/m\",\"func_code\":\"F\"}}', encoding='utf-8')\n"
        "    print(f'config.json 已写入: C:/tmp/{sys.argv[2]}/config.json')\n",
        encoding="utf-8",
    )
    for rel in [
        "launch_chrome.py",
        "cdp_executor.py",
        "stagehand_explorer/src/explore_wrapper.py",
    ]:
        path = scripts_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("import sys\nprint('ok')\n", encoding="utf-8")
    runs_root = tmp_path / "runs"

    assert main([
        "--skill-root",
        str(skill_root),
        "--runs-root",
        str(runs_root),
        "batch",
    ]) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["total"] == 2
    assert summary["failed"] == 2
    assert summary["skipped"] == 0
    assert summary["blocked"] == 0
    assert [item["menu_name"] for item in summary["results"]] == ["菜单A", "菜单B"]


def test_cli_batch_prewarms_schema_cache_once_before_menus(tmp_path, capsys, monkeypatch):
    skill_root = tmp_path / "skill"
    scripts_dir = skill_root / "scripts"
    scripts_dir.mkdir(parents=True)
    calls = tmp_path / "calls.jsonl"
    config_reader = scripts_dir / "config_reader.py"
    config_reader.write_text(
        "import json, pathlib, sys\n"
        f"calls = pathlib.Path({json.dumps(str(calls))})\n"
        "if sys.argv[1] == 'menus':\n"
        "    print('  菜单A')\n"
        "elif sys.argv[1] == 'env':\n"
        "    if sys.argv[2] == 'project_dir':\n"
        "        print('project_dir = F:/python_project')\n"
        "    elif sys.argv[2] == 'env_key':\n"
        "        print('env_key = ifm60-trust')\n"
        "elif sys.argv[1] == 'init':\n"
        "    p = pathlib.Path(f'C:/tmp/{sys.argv[2]}/config.json')\n"
        "    p.parent.mkdir(parents=True, exist_ok=True)\n"
        "    p.write_text('{\"env\":{},\"menu\":{\"菜单名称\":\"x\"},\"computed\":{\"menu_url\":\"/m\",\"func_code\":\"F\"}}', encoding='utf-8')\n"
        "    print(f'config.json 已写入: C:/tmp/{sys.argv[2]}/config.json')\n",
        encoding="utf-8",
    )
    for rel in [
        "launch_chrome.py",
        "cdp_executor.py",
        "stagehand_explorer/src/explore_wrapper.py",
        "build_schema_cache.py",
    ]:
        path = scripts_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if rel == "build_schema_cache.py":
            path.write_text(
                "import json, pathlib, sys\n"
                f"pathlib.Path({json.dumps(str(calls))}).write_text(json.dumps(sys.argv[1:], ensure_ascii=False), encoding='utf-8')\n"
                "print('schema refreshed')\n",
                encoding="utf-8",
            )
        else:
            path.write_text("import sys\nprint('ok')\n", encoding="utf-8")
    runs_root = tmp_path / "runs"

    assert main([
        "--skill-root",
        str(skill_root),
        "--runs-root",
        str(runs_root),
        "batch",
        "--refresh-schema-db",
        "ui_bc",
        "--refresh-schema-module",
        "trust",
        "--refresh-schema-force",
    ]) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["total"] == 1
    args = json.loads(calls.read_text(encoding="utf-8"))
    assert args[:4] == ["--auto", "--dbs", "ui_trust1", "ui_trust2"]
    assert "ui_bc" in args
    assert "--force" in args


def test_cli_doctor_reports_environment_diagnostics(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HUI_LLM_API_KEY", "key")
    monkeypatch.setenv("HUI_LLM_MODEL", "model")
    skill_root = _write_doctor_skill(tmp_path)
    project_dir = tmp_path / "project"
    (project_dir / "conf").mkdir(parents=True)
    (project_dir / "conf" / "config.py").write_text("# config\n", encoding="utf-8")
    (skill_root / "scripts" / "config_reader.py").write_text(
        "import sys\n"
        "if sys.argv[1] == 'menus':\n"
        "    print('菜单A')\n"
        "elif sys.argv[1] == 'env':\n"
        "    values = {\n"
        f"        'project_dir': {json.dumps(str(project_dir))},\n"
        "        'env_key': 'ifm60-trust',\n"
        "        'module': 'dxtrust',\n"
        "    }\n"
        "    print(f'{sys.argv[2]} = {values.get(sys.argv[2], \"\")}')\n",
        encoding="utf-8",
    )

    assert main(["--skill-root", str(skill_root), "doctor"]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["status"] in {"ok", "warn"}
    checks = {check["name"]: check for check in output["checks"]}
    assert checks["config_reader"]["status"] == "ok"
    assert checks["config_reader"]["details"]["menu_count"] == 1
    assert checks["llm"]["status"] == "ok"
    assert checks["schema_cache"]["details"]["table_count"] == 1
    assert checks["project_config"]["status"] == "ok"


def test_doctor_marks_missing_required_script_as_error(tmp_path):
    skill_root = _write_doctor_skill(tmp_path)
    (skill_root / "scripts" / "dry_run_sql.py").unlink()

    result = run_doctor(ScriptRunner(skill_root=skill_root))

    assert result["status"] == "error"
    checks = {check["name"]: check for check in result["checks"]}
    assert checks["script:dry_run_sql.py"]["status"] == "error"


def _write_doctor_skill(tmp_path: Path) -> Path:
    skill_root = tmp_path / "skill"
    scripts_dir = skill_root / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "config_reader.py").write_text(
        "import sys\n"
        "if sys.argv[1] == 'menus':\n"
        "    print('菜单A')\n"
        "elif sys.argv[1] == 'env':\n"
        "    print(f'{sys.argv[2]} = ')\n",
        encoding="utf-8",
    )
    for rel in [
        "launch_chrome.py",
        "cdp_executor.py",
        "stagehand_explorer/src/explore_wrapper.py",
        "build_schema_cache.py",
        "dry_run_sql.py",
        "sql_extractor.py",
    ]:
        path = scripts_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("print('ok')\n", encoding="utf-8")
    stagehand = scripts_dir / "stagehand_explorer"
    (stagehand / "package.json").write_text('{"scripts":{}}\n', encoding="utf-8")
    cache_dir = skill_root / "config" / "cache"
    cache_dir.mkdir(parents=True)
    (cache_dir / "trust.json").write_text('{"tbclient": {"columns": {}}}\n', encoding="utf-8")
    return skill_root
