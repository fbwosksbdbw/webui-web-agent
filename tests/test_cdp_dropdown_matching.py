import importlib.util
from pathlib import Path


def _load_cdp_executor(path: Path):
    spec = importlib.util.spec_from_file_location(f"cdp_executor_under_test_{abs(hash(path))}", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _cdp_paths():
    root = Path(__file__).resolve().parents[2]
    return [
        root / "scripts" / "cdp_executor.py",
        root / "hui-playwright-agent" / "knowledge" / "scripts" / "cdp_executor.py",
    ]


def test_dropdown_matcher_rejects_pagination_text_and_requires_exact_code_or_label():
    for path in _cdp_paths():
        module = _load_cdp_executor(path)

        assert module._dropdown_option_matches("UI_HT_P001:UI自动化HT信托产品", "UI_HT_P001:UI自动化HT信托产品")
        assert module._dropdown_option_matches("UI_HT_P001", "UI_HT_P001:UI自动化HT信托产品")
        assert module._dropdown_option_matches("UI自动化HT信托产品", "UI_HT_P001:UI自动化HT信托产品")
        assert not module._dropdown_option_matches("UI_HT_P001:UI自动化HT信托产品", "10 条/页")
        assert not module._dropdown_option_matches("UI_HT_P001:UI自动化HT信托产品", "共 12 条 10 条/页")
        assert not module._dropdown_option_matches("UI_HT_P001:UI自动化HT信托产品", "UI_HT_P001 搜索")


def test_select_single_should_not_fallback_when_specific_option_requested():
    for path in _cdp_paths():
        module = _load_cdp_executor(path)

        assert module._allow_first_option_fallback("__FIRST_OPTION__")
        assert module._allow_first_option_fallback("_")
        assert not module._allow_first_option_fallback("UI_HT_P001:UI自动化HT信托产品")
