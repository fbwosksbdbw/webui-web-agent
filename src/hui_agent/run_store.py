from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from hui_agent.state import HUITestState


class RunStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def run_dir(self, thread_id: str) -> Path:
        path = self.root / thread_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def write_event(
        self,
        thread_id: str,
        step: str,
        event: str,
        payload: dict[str, Any] | None = None,
    ) -> Path:
        record = {
            "timestamp": datetime.now().isoformat(),
            "thread_id": thread_id,
            "step": step,
            "event": event,
            "payload": payload or {},
        }
        path = self.run_dir(thread_id) / "events.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        return path

    def write_log_line(
        self,
        thread_id: str,
        step: str,
        event: str,
        message: str,
    ) -> Path:
        timestamp = datetime.now().isoformat(timespec="seconds")
        path = self.run_dir(thread_id) / "agent.log"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} [{step}] {event} - {message}\n")
        return path

    def read_events(self, thread_id: str) -> list[dict[str, Any]]:
        path = self.run_dir(thread_id) / "events.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def write_result(self, state: HUITestState) -> Path:
        path = self.run_dir(state.thread_id) / "result.json"
        path.write_text(
            json.dumps(state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def read_result(self, thread_id: str) -> HUITestState:
        path = self.run_dir(thread_id) / "result.json"
        return HUITestState.from_dict(json.loads(path.read_text(encoding="utf-8")))
