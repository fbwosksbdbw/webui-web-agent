import importlib.util
from pathlib import Path


def _load_explore_wrapper(path: Path):
    spec = importlib.util.spec_from_file_location(f"explore_wrapper_under_test_{abs(hash(path))}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _wrapper_path():
    root = Path(__file__).resolve().parents[1]
    return root / "knowledge" / "scripts" / "stagehand_explorer" / "src" / "explore_wrapper.py"


def test_select_preset_wins_when_explored_options_are_stale():
    wrapper = _load_explore_wrapper(_wrapper_path())

    result = wrapper.generate_field_values(
        [
            {
                "label": "产品代码",
                "type": "single-select",
                "is_required": False,
                "options": ["11:AI自动化测试产品"],
            }
        ],
        {"prd_code": {"name": "产品代码", "value": "UI_HT_P001:UI自动化HT信托产品"}},
    )

    field = result["all"][0]
    assert field["value"] == "UI_HT_P001:UI自动化HT信托产品"
    assert field["value_source"] == "preset"


def test_stagehand_explorer_invokes_locator_first_method():
    explorer = (
        Path(__file__).resolve().parents[1]
        / "knowledge"
        / "scripts"
        / "stagehand_explorer"
        / "src"
        / "explore.ts"
    )

    source = explorer.read_text(encoding="utf-8")

    assert ".first;" not in source
    assert ".first()" in source


def test_stagehand_explorer_falls_back_to_visible_portal_dropdown_options():
    explorer = (
        Path(__file__).resolve().parents[1]
        / "knowledge"
        / "scripts"
        / "stagehand_explorer"
        / "src"
        / "explore.ts"
    )

    source = explorer.read_text(encoding="utf-8")

    assert "collectOptionsFromContainers" in source
    assert "visibleDropdowns" in source
    assert ".h-select-dropdown-list, .h-select-dropdown-content, .h-select-dropdown, ul.h-select-dropdown" in source
