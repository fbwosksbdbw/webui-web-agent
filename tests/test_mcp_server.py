import json

import pytest

from hui_agent.mcp_server import build_query_actions, check_stagehand_node, scan_hui_form


class FakeRunner:
    def __init__(self):
        self.calls = []

    def run_text(self, script_name, args, timeout=None):
        self.calls.append(("run_text", script_name, list(args), timeout))
        if script_name.endswith("explore_wrapper.py") and "-o" in args:
            output_path = args[args.index("-o") + 1]
            with open(output_path, "w", encoding="utf-8") as handle:
                json.dump(
                    {
                        "fields": [
                            {"label": "客户标识", "type": "input", "is_required": True}
                        ]
                    },
                    handle,
                    ensure_ascii=False,
                )
        return "ok"

    def run_json(self, script_name, args):
        self.calls.append(("run_json", script_name, list(args), None))
        return {
            "all": [{"label": "客户标识", "type": "input", "value": "C001"}],
            "required": [{"label": "客户标识"}],
        }

    def run_json_value(self, script_name, args):
        self.calls.append(("run_json_value", script_name, list(args), None))
        return [{"type": "fill", "label": "客户标识", "value": "C001"}]


def test_scan_hui_form_wraps_stagehand_and_preset_matching(tmp_path):
    runner = FakeRunner()
    output_path = tmp_path / "explore_result.json"

    result = scan_hui_form(
        config_path=str(tmp_path / "config.json"),
        output_path=str(output_path),
        runner=runner,
    )

    assert result["status"] == "success"
    assert result["explore_path"] == str(output_path)
    assert result["explore_result"]["fields"][0]["label"] == "客户标识"
    assert result["form_fields"]["all"][0]["value"] == "C001"
    assert runner.calls[0][1] == "stagehand_explorer/src/explore_wrapper.py"
    assert runner.calls[1][1] == "config_reader.py"
    assert runner.calls[1][2][0] == "match_presets"


def test_scan_hui_form_rejects_unknown_mode(tmp_path):
    with pytest.raises(ValueError):
        scan_hui_form(config_path=str(tmp_path / "config.json"), mode="slow", runner=FakeRunner())


def test_build_query_actions_uses_config_reader():
    runner = FakeRunner()

    result = build_query_actions(
        fields=[{"label": "客户标识", "type": "input", "value": "C001"}],
        runner=runner,
    )

    assert result["status"] == "success"
    assert result["action_count"] == 1
    assert result["actions"][0]["type"] == "fill"
    assert runner.calls[0][1] == "config_reader.py"
    assert runner.calls[0][2][0] == "build_actions"


def test_check_stagehand_node_uses_wrapper_check_mode(tmp_path):
    runner = FakeRunner()

    result = check_stagehand_node(config_path=str(tmp_path / "config.json"), runner=runner)

    assert result == {"status": "success", "output": "ok"}
    assert runner.calls[0][1] == "stagehand_explorer/src/explore_wrapper.py"
    assert "--check-node" in runner.calls[0][2]
