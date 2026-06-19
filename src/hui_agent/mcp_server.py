from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from hui_agent.tools.script_runner import ScriptRunner


def default_skill_root() -> Path:
    agent_root = Path(__file__).resolve().parents[2]
    return agent_root / "knowledge"


def scan_hui_form(
    *,
    config_path: str,
    skill_root: str | Path | None = None,
    mode: str = "fast",
    output_path: str | None = None,
    llm_model: str = "gpt-4o",
    timeout_ms: int = 60000,
    match_presets: bool = True,
    runner: ScriptRunner | None = None,
) -> dict[str, Any]:
    """Scan the current HUI page form through the existing Stagehand/CDP wrapper.

    This function is intentionally a thin tool boundary: Playwright/CDP/Stagehand
    still do the real browser work, while MCP exposes the capability through a
    stable JSON-shaped interface.
    """
    if mode not in {"fast", "deep"}:
        raise ValueError("mode must be 'fast' or 'deep'")
    active_runner = runner or ScriptRunner(skill_root=skill_root or default_skill_root())
    timeout_seconds = max(1, int(timeout_ms / 1000) + 30)

    if output_path:
        explore_path = Path(output_path)
        explore_path.parent.mkdir(parents=True, exist_ok=True)
        return _scan_hui_form_to_path(
            active_runner,
            config_path=config_path,
            mode=mode,
            explore_path=explore_path,
            llm_model=llm_model,
            timeout_ms=timeout_ms,
            timeout_seconds=timeout_seconds,
            match_presets=match_presets,
        )

    with tempfile.TemporaryDirectory(prefix="hui-mcp-scan-") as temp_dir:
        explore_path = Path(temp_dir) / "explore_result.json"
        result = _scan_hui_form_to_path(
            active_runner,
            config_path=config_path,
            mode=mode,
            explore_path=explore_path,
            llm_model=llm_model,
            timeout_ms=timeout_ms,
            timeout_seconds=timeout_seconds,
            match_presets=match_presets,
        )
        result["explore_path"] = ""
        return result


def build_query_actions(
    *,
    fields: list[dict[str, Any]],
    skill_root: str | Path | None = None,
    runner: ScriptRunner | None = None,
) -> dict[str, Any]:
    """Build CDP query actions from scanned form fields."""
    active_runner = runner or ScriptRunner(skill_root=skill_root or default_skill_root())
    with tempfile.TemporaryDirectory(prefix="hui-mcp-actions-") as temp_dir:
        fields_path = Path(temp_dir) / "form_fields_all.json"
        fields_path.write_text(json.dumps(fields, ensure_ascii=False, indent=2), encoding="utf-8")
        actions = active_runner.run_json_value("config_reader.py", ["build_actions", str(fields_path)])
    return {
        "status": "success",
        "action_count": len(actions) if isinstance(actions, list) else 0,
        "actions": actions,
    }


def check_stagehand_node(
    *,
    config_path: str = "",
    skill_root: str | Path | None = None,
    runner: ScriptRunner | None = None,
) -> dict[str, Any]:
    """Check that the Stagehand Node.js dependency chain is available."""
    active_runner = runner or ScriptRunner(skill_root=skill_root or default_skill_root())
    args = ["--check-node"]
    if config_path:
        args.extend(["--config", config_path])
    output = active_runner.run_text("stagehand_explorer/src/explore_wrapper.py", args)
    return {"status": "success", "output": output}


def create_mcp_server(*, skill_root: str | Path | None = None) -> Any:
    try:
        from mcp.server.fastmcp import FastMCP
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "MCP server support requires the optional dependency. "
            "Install with: python -m pip install -e .[mcp]"
        ) from exc

    resolved_skill_root = str(Path(skill_root or default_skill_root()).resolve())
    mcp = FastMCP("hui-web-agent")

    @mcp.tool()
    def scan_form(
        config_path: str,
        mode: str = "fast",
        output_path: str = "",
        match_presets: bool = True,
    ) -> dict[str, Any]:
        """Scan the current HUI page and return form fields plus preset matches."""
        return scan_hui_form(
            config_path=config_path,
            skill_root=resolved_skill_root,
            mode=mode,
            output_path=output_path or None,
            match_presets=match_presets,
        )

    @mcp.tool()
    def build_actions(fields: list[dict[str, Any]]) -> dict[str, Any]:
        """Build deterministic query actions from scanned form fields."""
        return build_query_actions(fields=fields, skill_root=resolved_skill_root)

    @mcp.tool()
    def check_node(config_path: str = "") -> dict[str, Any]:
        """Check whether the local Stagehand Node.js runtime is usable."""
        return check_stagehand_node(config_path=config_path, skill_root=resolved_skill_root)

    return mcp


def run_mcp_server(*, skill_root: str | Path | None = None) -> None:
    create_mcp_server(skill_root=skill_root).run()


def _scan_hui_form_to_path(
    runner: ScriptRunner,
    *,
    config_path: str,
    mode: str,
    explore_path: Path,
    llm_model: str,
    timeout_ms: int,
    timeout_seconds: int,
    match_presets: bool,
) -> dict[str, Any]:
    args = [
        f"--mode={mode}",
        "--config",
        config_path,
        "--llm-model",
        llm_model,
        "--timeout",
        str(timeout_ms),
        "-o",
        str(explore_path),
    ]
    runner.run_text("stagehand_explorer/src/explore_wrapper.py", args, timeout=timeout_seconds)
    explore_result = _load_json_value(explore_path)
    payload: dict[str, Any] = {
        "status": "success",
        "mode": mode,
        "explore_path": str(explore_path),
        "explore_result": explore_result,
    }
    if match_presets:
        payload["form_fields"] = runner.run_json(
            "config_reader.py",
            ["match_presets", str(explore_path)],
        )
    return payload


def _load_json_value(path: Path) -> Any:
    if not path.exists():
        raise FileNotFoundError(f"Explore result was not written: {path}")
    return json.loads(path.read_text(encoding="utf-8"))
