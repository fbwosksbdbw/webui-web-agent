import importlib.util
from pathlib import Path


def _load_wrapper(path: Path):
    spec = importlib.util.spec_from_file_location("stagehand_explore_wrapper_for_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_knowledge_stagehand_wrapper_uses_agent_local_node_modules():
    repo_root = Path(__file__).resolve().parents[2]
    wrapper = _load_wrapper(
        repo_root
        / "hui-playwright-agent"
        / "knowledge"
        / "scripts"
        / "stagehand_explorer"
        / "src"
        / "explore_wrapper.py"
    )

    node_modules = wrapper._find_stagehand_node_modules(wrapper._EXPLORER_DIR)

    assert (
        node_modules
        == repo_root
        / "hui-playwright-agent"
        / "knowledge"
        / "scripts"
        / "stagehand_explorer"
        / "node_modules"
    )
    assert (node_modules / "ts-node").is_dir()


def test_stagehand_command_requires_agent_local_ts_node_register():
    repo_root = Path(__file__).resolve().parents[2]
    wrapper = _load_wrapper(
        repo_root
        / "hui-playwright-agent"
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
    assert (
        Path(command[require_index])
        == repo_root
        / "hui-playwright-agent"
        / "knowledge"
        / "scripts"
        / "stagehand_explorer"
        / "node_modules"
        / "ts-node"
        / "register"
        / "index.js"
    )
