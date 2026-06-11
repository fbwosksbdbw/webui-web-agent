import importlib.util
from pathlib import Path


def _load_dry_run_module():
    module_path = Path(__file__).parents[1] / "knowledge" / "scripts" / "dry_run_sql.py"
    spec = importlib.util.spec_from_file_location("hui_test_dry_run_sql", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeDuplicateError(Exception):
    def __init__(self, value: str):
        super().__init__(1062, f"Duplicate entry '{value}' for key 'PRIMARY'")


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.commands = []

    def execute(self, stmt):
        self.commands.append(stmt)
        upper = stmt.upper().strip()
        if upper.startswith("SET "):
            return
        if upper == "START TRANSACTION":
            self.conn.tx_values = set(self.conn.persisted_values)
            return
        if upper.startswith("DELETE"):
            self.conn.tx_values.discard("A")
            return
        if upper.startswith("INSERT"):
            if "A" in self.conn.tx_values:
                raise FakeDuplicateError("A")
            self.conn.tx_values.add("A")
            return
        raise AssertionError(f"Unexpected SQL: {stmt}")

    def close(self):
        return None


class FakeConnection:
    def __init__(self):
        self.persisted_values = {"A"}
        self.tx_values = set(self.persisted_values)
        self.cursor_obj = FakeCursor(self)
        self.rollback_count = 0

    def cursor(self):
        return self.cursor_obj

    def rollback(self):
        self.rollback_count += 1
        self.tx_values = set(self.persisted_values)


def test_dry_run_executes_dependent_delete_insert_in_one_rollback_transaction():
    dry_run_sql = _load_dry_run_module()
    conn = FakeConnection()

    result = dry_run_sql.dry_run_statements(
        conn,
        [
            ("DELETE FROM t WHERE id = 'A'", 1),
            ("INSERT INTO t(id) VALUES ('A')", 2),
        ],
    )

    assert result["ok"] is True
    assert result["passed"] == 2
    assert result["failed"] == 0
    assert conn.persisted_values == {"A"}
