from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from hui_agent.tools.script_runner import ScriptRunner

CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


REQUIRED_SCRIPTS = (
    "config_reader.py",
    "launch_chrome.py",
    "cdp_executor.py",
    "stagehand_explorer/src/explore_wrapper.py",
    "build_schema_cache.py",
    "dry_run_sql.py",
    "sql_extractor.py",
)


def run_doctor(
    runner: ScriptRunner,
    *,
    probe_cdp: bool = False,
    command_runner: CommandRunner | None = None,
) -> dict[str, Any]:
    checks = [
        _check_python_runtime(),
        _check_skill_root(runner),
        *_check_required_scripts(runner),
        _check_config_reader(runner),
        _check_stagehand(runner, command_runner=command_runner),
        _check_llm_env(),
        _check_schema_cache(runner),
        _check_project_config(runner),
    ]
    if probe_cdp:
        checks.append(_check_cdp_endpoint(runner))
    return {
        "status": _overall_status(checks),
        "skill_root": str(runner.skill_root),
        "checks": checks,
    }


def _check_python_runtime() -> dict[str, Any]:
    return _check(
        "python",
        "ok",
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        {"executable": sys.executable},
    )


def _check_skill_root(runner: ScriptRunner) -> dict[str, Any]:
    if runner.skill_root.exists():
        return _check("skill_root", "ok", "skill root exists", {"path": str(runner.skill_root)})
    return _check("skill_root", "error", "skill root does not exist", {"path": str(runner.skill_root)})


def _check_required_scripts(runner: ScriptRunner) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    for rel_path in REQUIRED_SCRIPTS:
        path = runner.scripts_dir / rel_path
        status = "ok" if path.exists() else "error"
        message = "script exists" if path.exists() else "script missing"
        checks.append(_check(f"script:{rel_path}", status, message, {"path": str(path)}))
    return checks


def _check_config_reader(runner: ScriptRunner) -> dict[str, Any]:
    details: dict[str, Any] = {}
    try:
        menus_output = runner.run_text("config_reader.py", ["menus"], timeout=60)
        menus = [line.strip() for line in menus_output.splitlines() if line.strip()]
        details["menu_count"] = len(menus)
        details["sample_menus"] = menus[:5]
    except Exception as exc:  # noqa: BLE001 - surfaced as a doctor check
        return _check("config_reader", "error", str(exc), details)
    env_key = _read_config_env_value(runner, "env_key")
    module = _read_config_env_value(runner, "module")
    if env_key:
        details["env_key"] = env_key
    if module:
        details["module"] = module
    if not menus:
        return _check("config_reader", "warn", "no pending menus found", details)
    return _check("config_reader", "ok", "configuration can be read", details)


def _check_stagehand(
    runner: ScriptRunner,
    *,
    command_runner: CommandRunner | None = None,
) -> dict[str, Any]:
    stagehand_root = runner.scripts_dir / "stagehand_explorer"
    package_json = stagehand_root / "package.json"
    node_modules = stagehand_root / "node_modules"
    details: dict[str, Any] = {
        "path": str(stagehand_root),
        "package_json": package_json.exists(),
        "node_modules": node_modules.exists(),
    }
    node_path = shutil.which("node")
    npm_path = shutil.which("npm")
    details["node"] = node_path or ""
    details["npm"] = npm_path or ""
    if node_path:
        try:
            result = (command_runner or subprocess.run)(
                [node_path, "--version"],
                text=True,
                capture_output=True,
                timeout=10,
            )
            details["node_version"] = (result.stdout or result.stderr or "").strip()
        except Exception as exc:  # noqa: BLE001 - diagnostic only
            details["node_version_error"] = str(exc)
    if not package_json.exists():
        return _check("stagehand", "error", "stagehand package.json is missing", details)
    if not node_path:
        return _check("stagehand", "warn", "node executable was not found on PATH", details)
    if not node_modules.exists():
        return _check("stagehand", "warn", "node_modules is missing; run npm install in stagehand_explorer", details)
    return _check("stagehand", "ok", "stagehand dependencies look available", details)


def _check_llm_env() -> dict[str, Any]:
    api_key_name = _first_present_env(("HUI_LLM_API_KEY", "OPENAI_API_KEY"))
    model_name = _first_present_env(("HUI_LLM_MODEL", "OPENAI_MODEL"))
    base_url_name = _first_present_env(("HUI_LLM_BASE_URL", "OPENAI_BASE_URL"))
    details = {
        "api_key_env": api_key_name,
        "model_env": model_name,
        "base_url_env": base_url_name,
    }
    if not api_key_name:
        return _check("llm", "warn", "no LLM API key env var found; LLM repairs will be disabled", details)
    return _check("llm", "ok", "LLM environment is configured", details)


def _check_schema_cache(runner: ScriptRunner) -> dict[str, Any]:
    cache_dir = runner.skill_root / "config" / "cache"
    files: list[dict[str, Any]] = []
    total_tables = 0
    if cache_dir.exists():
        for path in sorted(cache_dir.glob("*.json")):
            info: dict[str, Any] = {"file": path.name, "path": str(path)}
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001 - diagnostic only
                info["status"] = "invalid"
                info["error"] = str(exc)
            else:
                table_count = len(payload) if isinstance(payload, dict) else 0
                info["status"] = "ok" if isinstance(payload, dict) else "invalid"
                info["table_count"] = table_count
                total_tables += table_count
            files.append(info)
    details = {"cache_dir": str(cache_dir), "file_count": len(files), "table_count": total_tables, "files": files}
    if not cache_dir.exists():
        return _check("schema_cache", "warn", "schema cache directory is missing", details)
    if not files:
        return _check("schema_cache", "warn", "schema cache is empty", details)
    if any(item.get("status") == "invalid" for item in files):
        return _check("schema_cache", "warn", "one or more schema cache files are invalid", details)
    return _check("schema_cache", "ok", "schema cache is readable", details)


def _check_project_config(runner: ScriptRunner) -> dict[str, Any]:
    project_dir = _read_config_env_value(runner, "project_dir")
    env_key = _read_config_env_value(runner, "env_key")
    details = {"project_dir": project_dir, "env_key": env_key}
    if not project_dir:
        return _check("project_config", "warn", "project_dir is not configured", details)
    project_path = Path(project_dir)
    config_py = project_path / "conf" / "config.py"
    details["config_py"] = str(config_py)
    if not config_py.exists():
        return _check("project_config", "warn", "project conf/config.py was not found", details)
    if not env_key:
        return _check("project_config", "warn", "env_key is not configured", details)
    return _check("project_config", "ok", "project config path and env key are configured", details)


def _check_cdp_endpoint(runner: ScriptRunner) -> dict[str, Any]:
    cdp_url = _read_config_env_value(runner, "cdp_url") or "http://127.0.0.1:9222"
    version_url = cdp_url.rstrip("/") + "/json/version"
    details = {"url": version_url}
    try:
        with urllib.request.urlopen(version_url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - diagnostic only
        return _check("cdp", "warn", f"CDP endpoint is not reachable: {exc}", details)
    details["browser"] = payload.get("Browser", "")
    details["websocket_debugger_url"] = bool(payload.get("webSocketDebuggerUrl"))
    return _check("cdp", "ok", "CDP endpoint is reachable", details)


def _read_config_env_value(runner: ScriptRunner, key: str) -> str:
    try:
        output = runner.run_text("config_reader.py", ["env", key], timeout=60)
    except Exception:
        return ""
    prefix = f"{key} = "
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(prefix):
            value = stripped[len(prefix):].strip()
            if value.startswith("<") or "请配置" in value:
                return ""
            return value
    return ""


def _first_present_env(names: Sequence[str]) -> str:
    for name in names:
        if os.getenv(name):
            return name
    return ""


def _overall_status(checks: list[dict[str, Any]]) -> str:
    statuses = {str(check.get("status")) for check in checks}
    if "error" in statuses:
        return "error"
    if "warn" in statuses:
        return "warn"
    return "ok"


def _check(name: str, status: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "message": message,
        "details": details or {},
    }
