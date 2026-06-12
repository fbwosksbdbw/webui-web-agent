import importlib.util
import io
import json
from pathlib import Path


def _load_cdp_executor():
    scripts_dir = Path(__file__).resolve().parents[1] / "knowledge" / "scripts"
    spec = importlib.util.spec_from_file_location("cdp_executor_under_test", scripts_dir / "cdp_executor.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_resolve_cdp_websocket_url_from_json_version(monkeypatch):
    module = _load_cdp_executor()
    calls = []

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def fake_urlopen(url, timeout=0):
        calls.append((url, timeout))
        return Response(json.dumps({"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/browser/abc"}).encode())

    monkeypatch.setattr(module.urllib.request, "urlopen", fake_urlopen)

    assert module._resolve_cdp_websocket_url("http://127.0.0.1:9222") == "ws://127.0.0.1:9222/devtools/browser/abc"
    assert calls == [("http://127.0.0.1:9222/json/version", 5)]


def test_navigate_uses_legacy_tree_click_for_tree_idx():
    module = _load_cdp_executor()
    calls = []

    class FakePage:
        url = "http://example.test/ifmmanage/bizframe/homepage/facestyle/indexFrame.jsp"

        def goto(self, *_args, **_kwargs):
            calls.append("goto")

        def wait_for_timeout(self, *_args):
            calls.append("wait")

    executor = module.CDPExecutor.__new__(module.CDPExecutor)
    executor._page = FakePage()
    executor._navigate_legacy_tree = lambda menu_url: calls.append(("legacy", menu_url))
    executor._has_expected_navigation_content = lambda: True

    executor.navigate("ifmquerymanage#ifmCCxglPrdInfo#ifmCCxglProductDetail#")

    assert calls == [("legacy", "ifmquerymanage#ifmCCxglPrdInfo#ifmCCxglProductDetail#")]


def test_translate_locator_supports_frame_locator_and_first():
    module = _load_cdp_executor()
    calls = []

    class Chain:
        def __init__(self, name="page"):
            self.name = name

        def frame_locator(self, selector):
            calls.append(("frame_locator", selector))
            return self

        def locator(self, selector):
            calls.append(("locator", selector))
            return self

        def filter(self, **kwargs):
            calls.append(("filter", kwargs))
            return self

        @property
        def first(self):
            calls.append(("first", None))
            return self

    page = Chain()

    result = module.translate_locator(
        page,
        "page.frameLocator('iframe[name=\"share_cost_btn\"]').locator('.x-form-item')"
        ".filter({ hasText: '账号' }).locator('input:not([type=\"hidden\"])').first()",
    )

    assert result is page
    assert calls == [
        ("frame_locator", 'iframe[name="share_cost_btn"]'),
        ("locator", ".x-form-item"),
        ("filter", {"has_text": "账号"}),
        ("locator", 'input:not([type="hidden"])'),
        ("first", None),
    ]


def test_sort_actions_by_deps_ignores_label_spaces():
    module = _load_cdp_executor()
    actions = [
        {"action": "select", "label": "产品代码", "value": "__FIRST_OPTION__"},
        {"action": "select", "label": "TA 代码", "value": "__FIRST_OPTION__"},
        {"action": "fill", "label": "账    号", "value": "A1"},
        {"action": "select", "label": "账户类型", "value": "0:入账账号"},
    ]

    sorted_actions = module.CDPExecutor._sort_actions_by_deps(
        actions,
        {"产品代码": ["TA代码"], "账号": ["账户类型"]},
    )

    assert [item["label"] for item in sorted_actions] == ["TA 代码", "产品代码", "账户类型", "账    号"]


def test_translate_locator_falls_back_to_frame_name_when_iframe_name_attr_missing():
    module = _load_cdp_executor()
    calls = []

    class Frame:
        name = "share_cost_btn"

        def locator(self, selector):
            calls.append(("frame.locator", selector))
            return self

        def filter(self, **kwargs):
            calls.append(("filter", kwargs))
            return self

        @property
        def first(self):
            calls.append(("first", None))
            return self

    class Page:
        frames = [Frame()]

        def frame_locator(self, _selector):
            raise AssertionError("should use frame.name fallback")

    page = Page()

    result = module.translate_locator(
        page,
        "page.frameLocator('iframe[name=\"share_cost_btn\"]').locator('.x-form-item')"
        ".filter({ hasText: '合约编号' }).first()",
    )

    assert result is page.frames[0]
    assert calls == [
        ("frame.locator", ".x-form-item"),
        ("filter", {"has_text": "合约编号"}),
        ("first", None),
    ]
