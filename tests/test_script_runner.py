import json
import sys

from hui_agent.tools.script_runner import ScriptRunner


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_build_command_targets_existing_skill_script(tmp_path):
    skill_root = tmp_path / "skill"
    scripts_dir = skill_root / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "config_reader.py").write_text("print('ok')", encoding="utf-8")

    runner = ScriptRunner(skill_root=skill_root)

    command = runner.build_command("config_reader.py", ["menus"])

    assert command == [sys.executable, str(scripts_dir / "config_reader.py"), "menus"]


def test_run_text_sets_windows_safe_environment(tmp_path):
    skill_root = tmp_path / "skill"
    scripts_dir = skill_root / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "config_reader.py").write_text("print('ok')", encoding="utf-8")
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return FakeCompletedProcess(stdout="  ok\n")

    runner = ScriptRunner(skill_root=skill_root, run_impl=fake_run)

    output = runner.run_text("config_reader.py", ["menus"])

    assert output == "ok"
    assert captured["env"]["PYTHONIOENCODING"] == "utf-8"
    assert captured["env"]["MSYS_NO_PATHCONV"] == "1"
    assert "localhost" in captured["env"]["NO_PROXY"]
    assert "127.0.0.1" in captured["env"]["NO_PROXY"]
    assert "::1" in captured["env"]["NO_PROXY"]
    assert captured["env"]["no_proxy"] == captured["env"]["NO_PROXY"]


def test_run_text_preserves_existing_no_proxy_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("NO_PROXY", "example.internal,localhost")
    skill_root = tmp_path / "skill"
    scripts_dir = skill_root / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "config_reader.py").write_text("print('ok')", encoding="utf-8")
    captured = {}

    def fake_run(command, **kwargs):
        captured["env"] = kwargs["env"]
        return FakeCompletedProcess(stdout="ok")

    runner = ScriptRunner(skill_root=skill_root, run_impl=fake_run)

    runner.run_text("config_reader.py", ["menus"])

    no_proxy = captured["env"]["NO_PROXY"]
    assert "example.internal" in no_proxy
    assert no_proxy.count("localhost") == 1
    assert "127.0.0.1" in no_proxy


def test_run_json_parses_last_json_object_after_log_lines(tmp_path):
    skill_root = tmp_path / "skill"
    scripts_dir = skill_root / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "config_reader.py").write_text("print('ok')", encoding="utf-8")

    payload = {"menu": "基金分红查询", "ok": True}

    def fake_run(command, **kwargs):
        return FakeCompletedProcess(stdout="[OK] generated\n" + json.dumps(payload))

    runner = ScriptRunner(skill_root=skill_root, run_impl=fake_run)

    assert runner.run_json("config_reader.py", ["init", "基金分红查询"]) == payload


def test_run_text_emits_script_lifecycle_events(tmp_path):
    skill_root = tmp_path / "skill"
    scripts_dir = skill_root / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "config_reader.py").write_text("print('ok')", encoding="utf-8")
    events = []

    def fake_run(command, **kwargs):
        return FakeCompletedProcess(stdout="ok")

    runner = ScriptRunner(skill_root=skill_root, run_impl=fake_run, event_logger=events.append)

    runner.run_text("config_reader.py", ["menus"])

    assert events[0]["event"] == "script_started"
    assert events[0]["script"] == "config_reader.py"
    assert events[0]["args"] == ["menus"]
    assert events[1]["event"] == "script_finished"
    assert events[1]["returncode"] == 0


def test_run_text_redacts_sensitive_args_from_lifecycle_events(tmp_path):
    skill_root = tmp_path / "skill"
    scripts_dir = skill_root / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "cdp_executor.py").write_text("print('ok')", encoding="utf-8")
    events = []
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        return FakeCompletedProcess(stdout="ok")

    runner = ScriptRunner(skill_root=skill_root, run_impl=fake_run, event_logger=events.append)

    runner.run_text("cdp_executor.py", ["--ssh-user", "u", "--ssh-pass", "secret"])

    assert "secret" in captured["command"]
    assert "secret" not in json.dumps(events, ensure_ascii=False)
    assert "***" in json.dumps(events, ensure_ascii=False)
