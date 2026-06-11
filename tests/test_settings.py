from hui_agent.settings import AgentSettings


def test_settings_loads_yaml_relative_to_config_file(tmp_path):
    config = tmp_path / "agent_config.yaml"
    config.write_text(
        "runtime:\n"
        "  skill_root: ./knowledge\n"
        "  runs_root: ./runs\n"
        "  checkpoint_db: ./runs/checkpoints.sqlite\n"
        "models:\n"
        "  default: test-model\n"
        "limits:\n"
        "  max_step_retries: 5\n",
        encoding="utf-8",
    )

    settings = AgentSettings.from_yaml(config)

    assert settings.skill_root == tmp_path / "knowledge"
    assert settings.runs_root == tmp_path / "runs"
    assert settings.checkpoint_db == tmp_path / "runs" / "checkpoints.sqlite"
    assert settings.default_model == "test-model"
    assert settings.max_step_retries == 5
