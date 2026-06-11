from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


@contextmanager
def make_sqlite_checkpointer(path: str | Path) -> Iterator[Any]:
    """Create a LangGraph SQLite checkpointer when the optional package is installed."""
    try:
        from langgraph.checkpoint.sqlite import SqliteSaver
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "SQLite checkpointing requires langgraph-checkpoint-sqlite. "
            "Install project dependencies with `pip install -e .` from hui-playwright-agent."
        ) from exc

    db_path = Path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    saver_or_context = SqliteSaver.from_conn_string(str(db_path))
    if hasattr(saver_or_context, "__enter__"):
        with saver_or_context as saver:
            yield saver
    else:
        yield saver_or_context
