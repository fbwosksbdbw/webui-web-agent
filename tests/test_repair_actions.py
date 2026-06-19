from hui_agent.repair_actions import apply_llm_repair_actions, repair_duplicate_insert_keys


def test_apply_llm_repair_actions_patches_allowed_sql_file(tmp_path):
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    case_dir.mkdir()
    sql_dir.mkdir()
    preset_sql = sql_dir / "preset.sql"
    preset_sql.write_text("INSERT INTO t(prd_code) VALUES ('OLD');\n", encoding="utf-8")
    case_plan = {"sql_paths": {"preset_sql": str(preset_sql)}}

    actions = apply_llm_repair_actions(
        case_plan,
        case_dir,
        {
            "actions": [
                {
                    "type": "patch_sql",
                    "file": str(preset_sql),
                    "reason": "replace stale code",
                    "changes": [{"old": "'OLD'", "new": "'NEW'"}],
                }
            ]
        },
    )

    assert actions == [
        {
            "type": "llm_patch_sql",
            "status": "patched",
            "file": "../sql/preset.sql",
            "reason": "replace stale code",
            "changes": [{"old": "'OLD'", "new": "'NEW'", "count": 1}],
        }
    ]
    assert preset_sql.read_text(encoding="utf-8") == "INSERT INTO t(prd_code) VALUES ('NEW');\n"


def test_repair_duplicate_insert_keys_makes_serial_key_distinct_and_rewrites_sql(tmp_path):
    case_dir = tmp_path / "case"
    sql_dir = tmp_path / "sql"
    case_dir.mkdir()
    sql_dir.mkdir()
    base_sql = sql_dir / "base.sql"
    case_plan = {
        "sql_paths": {"base_sql": str(base_sql)},
        "insert_plans": [
            {
                "table": "tbrequest",
                "physical_table": "tbrequest",
                "role": "base",
                "columns": [
                    {"name": "serial_no", "value": "REQ001", "source": "test"},
                    {"name": "client_no", "value": "C1", "source": "test"},
                ],
                "delete_where": "serial_no = 'REQ001'",
                "rows": [
                    [
                        {"name": "serial_no", "value": "REQ001", "source": "test"},
                        {"name": "client_no", "value": "C1", "source": "test"},
                    ],
                    [
                        {"name": "serial_no", "value": "REQ001", "source": "test"},
                        {"name": "client_no", "value": "C2", "source": "test"},
                    ],
                ],
            }
        ],
    }

    actions = repair_duplicate_insert_keys(case_plan, case_dir)

    assert actions[0]["type"] == "patch_duplicate_insert_keys"
    assert actions[0]["changes"] == [
        {"action": "make_distinct", "old_key": ["REQ001"], "new_key": ["REQ002"]}
    ]
    assert case_plan["insert_plans"][0]["rows"][1][0]["value"] == "REQ002"
    assert "REQ001" in base_sql.read_text(encoding="utf-8")
    assert "REQ002" in base_sql.read_text(encoding="utf-8")


def test_apply_llm_repair_actions_rejects_invalid_regenerated_case(tmp_path):
    case_dir = tmp_path / "case"
    test_file = case_dir / "test_preview.py"
    case_dir.mkdir()
    test_file.write_text("def test_preview():\n    assert len([]) == 1\n", encoding="utf-8")
    case_plan = {"generated_tests": [str(test_file)]}

    actions = apply_llm_repair_actions(
        case_plan,
        case_dir,
        {
            "actions": [
                {
                    "type": "regenerate_case",
                    "file": str(test_file),
                    "content": "def test_preview():\n    assert True\n",
                }
            ]
        },
    )

    assert actions[0]["status"] == "skipped"
    assert actions[0]["reason"] == "LLM regenerate_case content contains a fake assertion"
    assert test_file.read_text(encoding="utf-8") == "def test_preview():\n    assert len([]) == 1\n"
