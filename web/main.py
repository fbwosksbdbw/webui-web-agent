"""FastAPI application entry-point for the HUI Playwright Agent web layer."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from web.adapters.agent_adapter import AgentAdapter
from web.routes import tasks


def create_app(
    *,
    skill_root: str | None = None,
    runs_root: str = "runs",
    checkpoint_db: str = "runs/checkpoints.sqlite",
) -> FastAPI:
    """Build and return the FastAPI application.

    Parameters mirror the CLI flags exposed by ``hui-agent``.
    """
    app = FastAPI(
        title="HUI Playwright Agent",
        description="Web API for the HUI automated test generation agent.",
        version="0.1.0",
    )

    # Wire the adapter into the routes module.
    adapter = AgentAdapter(
        skill_root=skill_root,
        runs_root=runs_root,
        checkpoint_db=checkpoint_db,
    )
    tasks.set_adapter(adapter)

    # Mount routes.
    app.include_router(tasks.router)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


# ---------------------------------------------------------------------------
# Standalone entry-point:  python -m web.main [--port 8000]
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m web.main")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--skill-root", default=None)
    parser.add_argument("--runs-root", default="runs")
    parser.add_argument("--checkpoint-db", default="runs/checkpoints.sqlite")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args()
    # Ensure the agent source is importable.
    agent_root = str(Path(__file__).resolve().parents[1] / "src")
    if agent_root not in sys.path:
        sys.path.insert(0, agent_root)

    app = create_app(
        skill_root=args.skill_root,
        runs_root=args.runs_root,
        checkpoint_db=args.checkpoint_db,
    )
    uvicorn.run(app, host=args.host, port=args.port)
