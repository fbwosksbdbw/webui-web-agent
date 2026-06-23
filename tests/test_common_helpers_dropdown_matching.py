import importlib.util
from pathlib import Path


def _load_helpers(path: Path):
    spec = importlib.util.spec_from_file_location(f"helpers_under_test_{abs(hash(path))}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _helper_path() -> Path:
    root = Path(__file__).resolve().parents[1]
    return root / "knowledge" / "assets" / "common" / "helpers.py"


def test_common_helper_dropdown_matcher_rejects_pagination_and_substring_noise():
    module = _load_helpers(_helper_path())

    assert module._dropdown_option_matches("UI_HT_P001:UI自动化HT信托产品", "UI_HT_P001:UI自动化HT信托产品")
    assert module._dropdown_option_matches("UI_HT_P001", "UI_HT_P001:UI自动化HT信托产品")
    assert module._dropdown_option_matches("UI自动化HT信托产品", "UI_HT_P001:UI自动化HT信托产品")
    assert not module._dropdown_option_matches("UI_HT_P001:UI自动化HT信托产品", "10 条/页")
    assert not module._dropdown_option_matches("UI_HT_P001:UI自动化HT信托产品", "共 12 条 10 条/页")
    assert not module._dropdown_option_matches("UI_HT_P001:UI自动化HT信托产品", "UI_HT_P001 搜索")
