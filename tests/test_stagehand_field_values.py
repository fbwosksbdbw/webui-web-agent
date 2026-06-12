import importlib.util
from pathlib import Path


def _load_explore_wrapper(path: Path):
    spec = importlib.util.spec_from_file_location(f"explore_wrapper_under_test_{abs(hash(path))}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _wrapper_paths():
    repo_root = Path(__file__).resolve().parents[1]
    return [
        repo_root / "knowledge" / "scripts" / "stagehand_explorer" / "src" / "explore_wrapper.py",
    ]


def test_select_preset_wins_when_explored_options_are_stale():
    for path in _wrapper_paths():
        wrapper = _load_explore_wrapper(path)

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


def test_generate_field_values_fills_inputs_and_selects_without_presets():
    for path in _wrapper_paths():
        wrapper = _load_explore_wrapper(path)

        result = wrapper.generate_field_values(
            [
                {"label": "客户姓名", "type": "input", "is_required": False, "options": []},
                {"label": "账户类型", "type": "single-select", "is_required": False, "options": []},
            ],
            {},
        )

        by_label = {field["label"]: field for field in result["all"]}

        assert by_label["客户姓名"]["value"]
        assert by_label["客户姓名"]["value_source"] == "generated"
        assert by_label["账户类型"]["value"] == "__FIRST_OPTION__"
        assert by_label["账户类型"]["value_source"] == "first_option"


def test_generate_field_values_matches_spaced_account_and_account_type_presets():
    for path in _wrapper_paths():
        wrapper = _load_explore_wrapper(path)

        result = wrapper.generate_field_values(
            [
                {"label": "账    号", "type": "input", "is_required": False, "options": []},
                {"label": "账户类型", "type": "single-select", "is_required": False, "options": []},
                {"label": "份额所属机构", "type": "single-select", "is_required": False, "options": []},
            ],
            {
                "account": {"name": "账号", "value": "A33060210021113"},
                "account_type": {"name": "账户类型", "value": "0:入账账号"},
                "branch_no": {"name": "分行机构编号", "value": "0001"},
                "hq_branch_no": {"name": "总行机构编号", "value": "100"},
            },
        )

        by_label = {field["label"]: field for field in result["all"]}

        assert by_label["账    号"]["value"] == "A33060210021113"
        assert by_label["账    号"]["value_source"] == "preset"
        assert by_label["账户类型"]["value"] == "0:入账账号"
        assert by_label["账户类型"]["value_source"] == "preset"
        assert by_label["份额所属机构"]["value"] == "0001"
        assert by_label["份额所属机构"]["value_source"] == "preset"


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
