from pathlib import Path

from hui_agent.repair_transaction import (
    record_repair_restore,
    restore_repair_snapshot,
    snapshot_repair_targets,
)


def test_repair_transaction_records_diff_and_restores_files_and_case_plan(tmp_path):
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    existing = case_dir / "preset.sql"
    created = case_dir / "generated.py"
    existing.write_text("INSERT INTO t(id) VALUES ('old');\n", encoding="utf-8")
    case_plan = {"insert_plans": [{"table": "t", "columns": [{"name": "id", "value": "old"}]}]}

    snapshot = snapshot_repair_targets(
        case_plan,
        case_dir,
        paths=[existing, created],
        label_path=lambda path, base: Path(path).relative_to(base).as_posix(),
    )
    existing.write_text("INSERT INTO t(id) VALUES ('new');\n", encoding="utf-8")
    created.write_text("def test_generated():\n    assert len([1]) == 1\n", encoding="utf-8")
    case_plan["insert_plans"][0]["columns"][0]["value"] = "new"
    attempt = {}

    record_repair_restore(attempt, snapshot, case_plan, status="restored", reason="retry failed")

    assert attempt["restore"] == {
        "status": "restored",
        "reason": "retry failed",
        "files": ["generated.py", "preset.sql"],
        "case_plan_keys": ["insert_plans"],
    }
    assert attempt["diff"]["file_details"] == [
        {
            "file": "generated.py",
            "old_exists": False,
            "new_exists": True,
            "old_size": 0,
            "new_size": len("def test_generated():\n    assert len([1]) == 1\n"),
        },
        {
            "file": "preset.sql",
            "old_exists": True,
            "new_exists": True,
            "old_size": len("INSERT INTO t(id) VALUES ('old');\n"),
            "new_size": len("INSERT INTO t(id) VALUES ('new');\n"),
        },
    ]

    restore_repair_snapshot(snapshot, case_plan)

    assert existing.read_text(encoding="utf-8") == "INSERT INTO t(id) VALUES ('old');\n"
    assert not created.exists()
    assert case_plan == {"insert_plans": [{"table": "t", "columns": [{"name": "id", "value": "old"}]}]}


def test_repair_transaction_restores_missing_case_plan_key(tmp_path):
    case_plan = {}
    snapshot = snapshot_repair_targets(case_plan, tmp_path, paths=[])
    case_plan["insert_plans"] = []

    restore_repair_snapshot(snapshot, case_plan)

    assert "insert_plans" not in case_plan
