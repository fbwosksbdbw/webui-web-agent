import importlib.util
from pathlib import Path

import pytest


def _load_wrapper(path: Path):
    spec = importlib.util.spec_from_file_location("stagehand_explore_wrapper_for_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_knowledge_stagehand_wrapper_uses_agent_local_node_modules():
    repo_root = Path(__file__).resolve().parents[1]
    node_modules = (
        repo_root
        / "knowledge"
        / "scripts"
        / "stagehand_explorer"
        / "node_modules"
    )
    if not (node_modules / "ts-node").is_dir():
        pytest.skip("node_modules not installed; run 'npm ci' in knowledge/scripts/stagehand_explorer/")

    wrapper = _load_wrapper(
        repo_root
        / "knowledge"
        / "scripts"
        / "stagehand_explorer"
        / "src"
        / "explore_wrapper.py"
    )

    result = wrapper._find_stagehand_node_modules(wrapper._EXPLORER_DIR)

    assert result == node_modules


def test_stagehand_command_requires_agent_local_ts_node_register():
    repo_root = Path(__file__).resolve().parents[1]
    ts_node_dir = (
        repo_root
        / "knowledge"
        / "scripts"
        / "stagehand_explorer"
        / "node_modules"
        / "ts-node"
    )
    if not ts_node_dir.is_dir():
        pytest.skip("node_modules not installed; run 'npm ci' in knowledge/scripts/stagehand_explorer/")

    wrapper = _load_wrapper(
        repo_root
        / "knowledge"
        / "scripts"
        / "stagehand_explorer"
        / "src"
        / "explore_wrapper.py"
    )

    command = wrapper._build_command(
        cdp_url="http://localhost:9222",
        target_url="/demo",
        mode="fast",
        llm_model="gpt-4o",
        timeout=1000,
        verbose=False,
        output=None,
        node_path=None,
    )

    require_index = command.index("--require") + 1
    ts_register = ts_node_dir / "register" / "index.js"
    assert Path(command[require_index]) == ts_register
