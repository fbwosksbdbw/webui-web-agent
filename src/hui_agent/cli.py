from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Sequence

from hui_agent.graph import build_workflow
from hui_agent.llm import (
    build_permission_advisor_from_env,
    build_repair_advisor_from_env,
    build_sql_advisor_from_env,
    build_template_reviewer_from_env,
    load_env_file,
)
from hui_agent.nodes import build_default_step_handlers
from hui_agent.run_store import RunStore
from hui_agent.state import HUITestState
from hui_agent.tools.script_runner import ScriptRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="hui-agent")
    parser.add_argument(
        "--skill-root",
        default=str(_default_skill_root()),
        help="Path to the existing hui-playwright-test skill root.",
    )
    parser.add_argument(
        "--runs-root",
        default="runs",
        help="Directory for per-thread events.jsonl and result.json files.",
    )
    parser.add_argument(
        "--checkpoint-db",
        default="runs/checkpoints.sqlite",
        help="SQLite checkpoint DB used when langgraph-checkpoint-sqlite is installed.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    subcommands.add_parser("menus")

    run_parser = subcommands.add_parser("run")
    run_parser.add_argument("--menu", required=True)

    subcommands.add_parser("batch")

    status_parser = subcommands.add_parser("status")
    status_parser.add_argument("--thread-id", required=True)

    resume_parser = subcommands.add_parser("resume")
    resume_parser.add_argument("--thread-id", required=True)

    retry_parser = subcommands.add_parser("retry")
    retry_parser.add_argument("--thread-id", required=True)
    retry_parser.add_argument("--from-step", required=True)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _load_default_env_files()
    runner = ScriptRunner(skill_root=args.skill_root)
    store = RunStore(args.runs_root)

    if args.command == "menus":
        print(runner.run_text("config_reader.py", ["menus"]))
        return 0

    if args.command == "run":
        result = run_menu_runtime(
            args.menu,
            skill_root=args.skill_root,
            runs_root=args.runs_root,
            checkpoint_db=args.checkpoint_db,
        )
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "batch":
        menus = _parse_menu_lines(runner.run_text("config_reader.py", ["menus"]))
        results = []
        for menu_name in menus:
            state = HUITestState.new(menu_name)
            store.write_event(state.thread_id, "START", "batch_menu_started", {"menu": menu_name})
            store.write_log_line(state.thread_id, "START", "batch_menu_started", f"menu={menu_name}")
            result = _invoke_runtime(state, args.skill_root, args.checkpoint_db, store)
            store.write_result(result)
            results.append(result)
            if result.status == "blocked":
                break
        summary = _summarize_batch(results)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    if args.command == "status":
        state = store.read_result(args.thread_id)
        print(json.dumps(state.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "resume":
        state = store.read_result(args.thread_id)
        result = _invoke_runtime(
            state.merge(status="running", failure_scope=None, failure_reason=None),
            args.skill_root,
            args.checkpoint_db,
            store,
        )
        store.write_result(result)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    if args.command == "retry":
        state = store.read_result(args.thread_id)
        result = state.retry_from(args.from_step)
        store.write_result(result)
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


def _default_skill_root() -> Path:
    agent_root = Path(__file__).resolve().parents[2]
    bundled_knowledge = agent_root / "knowledge"
    if (bundled_knowledge / "scripts" / "config_reader.py").exists():
        return bundled_knowledge
    cwd = Path.cwd()
    for candidate in (cwd, *cwd.parents):
        if (candidate / "scripts" / "config_reader.py").exists():
            return candidate
    return bundled_knowledge


def _load_default_env_files() -> None:
    agent_root = Path(__file__).resolve().parents[2]
    load_env_file(agent_root / ".env")
    load_env_file(Path.cwd() / ".env")


def _parse_menu_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def _summarize_batch(results: list[HUITestState]) -> dict[str, object]:
    return {
        "total": len(results),
        "success": sum(1 for result in results if result.status == "success"),
        "skipped": sum(1 for result in results if result.status == "skipped"),
        "failed": sum(1 for result in results if result.status == "failed"),
        "blocked": sum(1 for result in results if result.status == "blocked"),
        "results": [
            {
                "thread_id": result.thread_id,
                "menu_name": result.menu_name,
                "status": result.status,
                "failure_scope": result.failure_scope,
                "failure_reason": result.failure_reason,
                "completed_steps": result.completed_steps,
            }
            for result in results
        ],
    }


def _wrap_step_handlers_for_logging(
    handlers: dict[str, Callable[[HUITestState], HUITestState]],
    store: RunStore,
    runtime_context: dict[str, str],
) -> dict[str, Callable[[HUITestState], HUITestState]]:
    return {
        step: _logged_step_handler(step, handler, store, runtime_context)
        for step, handler in handlers.items()
    }


def _logged_step_handler(
    step: str,
    handler: Callable[[HUITestState], HUITestState],
    store: RunStore,
    runtime_context: dict[str, str],
) -> Callable[[HUITestState], HUITestState]:
    def wrapped(state: HUITestState) -> HUITestState:
        if step in state.completed_steps or state.status in {"skipped", "failed", "blocked"}:
            return handler(state)
        runtime_context["thread_id"] = state.thread_id
        runtime_context["step"] = step
        _record_runtime_event(store, state.thread_id, step, "step_started", _step_label(step), {"menu": state.menu_name})
        try:
            result = handler(state)
        except Exception as exc:
            _record_runtime_event(store, state.thread_id, step, "step_failed", str(exc), {"error": str(exc)})
            runtime_context["step"] = "SCRIPT"
            raise
        _record_runtime_event(
            store,
            result.thread_id,
            step,
            "step_finished",
            f"status={result.status} next={result.current_step}",
            {"status": result.status, "next_step": result.current_step},
        )
        runtime_context["step"] = "SCRIPT"
        return result

    return wrapped


def _record_runtime_event(
    store: RunStore,
    thread_id: str,
    step: str,
    event: str,
    message: str,
    payload: dict[str, object] | None = None,
) -> None:
    store.write_event(thread_id, step, event, payload or {"message": message})
    store.write_log_line(thread_id, step, event, message)
    try:
        print(f"[{thread_id}] [{step}] {event}: {message}", file=sys.stderr, flush=True)
    except OSError:
        # The CLI can be run under a parent process that times out and closes stderr
        # while child browser/pytest work continues. Persisted run logs are the source
        # of truth, so a broken console pipe must not fail the workflow node.
        pass


def _record_pytest_log_line(
    store: RunStore,
    thread_id: str,
    step: str,
    payload: dict[str, object],
) -> None:
    attempt = int(payload.get("attempt") or 0)
    line = str(payload.get("line", ""))
    path = store.run_dir(thread_id) / f"pytest_attempt_{attempt}.log"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _format_runtime_event_detail(payload: dict[str, object]) -> str:
    event = str(payload.get("event", "script_event"))
    if event == "pytest_started":
        return _join_detail_parts(
            [
                _detail("attempt", payload.get("attempt")),
                _detail("phase", payload.get("phase")),
                _detail("tests", payload.get("test_count")),
            ]
        )
    if event == "pytest_finished":
        return _join_detail_parts(
            [
                _detail("attempt", payload.get("attempt")),
                _detail("phase", payload.get("phase")),
                _detail("status", payload.get("status")),
                _detail("returncode", payload.get("returncode")),
                _detail("summary", payload.get("summary")),
                _detail("failed", _format_short_list(payload.get("failed_tests"))),
                _detail("passed", _format_short_list(payload.get("passed_tests"))),
            ]
        )
    if event == "pytest_case_result":
        return _join_detail_parts(
            [
                _detail("attempt", payload.get("attempt")),
                str(payload.get("status", "")),
                str(payload.get("case", "")),
            ]
        )
    if event == "sql_dry_run_started":
        return _join_detail_parts(
            [
                _detail("attempt", payload.get("attempt")),
                _detail("kind", payload.get("kind")),
                _detail("file", payload.get("file")),
                _detail("db", payload.get("db")),
            ]
        )
    if event == "sql_dry_run_finished":
        return _join_detail_parts(
            [
                _detail("attempt", payload.get("attempt")),
                _detail("kind", payload.get("kind")),
                _detail("status", payload.get("status")),
                _detail("file", payload.get("file")),
                _detail("db", payload.get("db")),
                _detail("failed", payload.get("failed")),
                _detail("error", payload.get("error")),
            ]
        )
    if event == "sql_coverage_checked":
        return _join_detail_parts(
            [
                _detail("status", payload.get("status")),
                _detail("missing_expected", payload.get("missing_expected")),
                _detail("observed_missing_strategy", payload.get("observed_missing_strategy")),
                _detail("reason", payload.get("failure_reason")),
            ]
        )
    if event == "sql_return_precheck_started":
        return _join_detail_parts([_detail("db", payload.get("db"))])
    if event == "sql_return_precheck_finished":
        return _join_detail_parts(
            [
                _detail("db", payload.get("db")),
                _detail("status", payload.get("status")),
                _detail("permission", payload.get("permission")),
                _detail("reason", payload.get("failure_reason")),
            ]
        )
    if event == "llm_sql_repair_started":
        return _join_detail_parts([_detail("attempt", payload.get("attempt"))])
    if event == "llm_sql_repair_finished":
        return _join_detail_parts(
            [
                _detail("attempt", payload.get("attempt")),
                _detail("type", payload.get("failure_type")),
                _detail("confidence", payload.get("confidence")),
                _detail("elapsed_ms", payload.get("elapsed_ms")),
                _detail("summary", payload.get("summary")),
            ]
        )
    if event == "llm_sql_repair_failed":
        return _join_detail_parts(
            [
                _detail("attempt", payload.get("attempt")),
                _detail("error", payload.get("error")),
            ]
        )
    if event == "sql_repair_actions_finished":
        actions = payload.get("actions", [])
        action_text = ",".join(str(action) for action in actions) if isinstance(actions, list) else str(actions)
        return _join_detail_parts(
            [
                _detail("attempt", payload.get("attempt")),
                _detail("status", payload.get("status")),
                _detail("actions", action_text),
            ]
        )
    if event == "sql_repair_attempt_recorded":
        return _join_detail_parts(
            [
                _detail("attempt", payload.get("attempt")),
                _detail("status", payload.get("status")),
                _detail("action_count", payload.get("action_count")),
                _detail("llm", payload.get("has_llm_decision")),
            ]
        )
    if event == "llm_repair_started":
        return _join_detail_parts([_detail("attempt", payload.get("attempt"))])
    if event == "llm_repair_finished":
        return _join_detail_parts(
            [
                _detail("attempt", payload.get("attempt")),
                _detail("type", payload.get("failure_type")),
                _detail("confidence", payload.get("confidence")),
                _detail("elapsed_ms", payload.get("elapsed_ms")),
                _detail("summary", payload.get("summary")),
            ]
        )
    if event == "llm_repair_failed":
        return _join_detail_parts(
            [
                _detail("attempt", payload.get("attempt")),
                _detail("error", payload.get("error")),
            ]
        )
    if event == "repair_actions_finished":
        actions = payload.get("actions", [])
        action_text = ",".join(str(action) for action in actions) if isinstance(actions, list) else str(actions)
        return _join_detail_parts(
            [
                _detail("attempt", payload.get("attempt")),
                _detail("status", payload.get("status")),
                _detail("actions", action_text),
            ]
        )
    if event == "repair_action_detail":
        return _join_detail_parts(
            [
                _detail("attempt", payload.get("attempt")),
                _detail("type", payload.get("type")),
                _detail("status", payload.get("status")),
                _detail("file", payload.get("file")),
                _detail("reason", payload.get("reason")),
            ]
        )
    if event == "repair_attempt_recorded":
        return _join_detail_parts(
            [
                _detail("attempt", payload.get("attempt")),
                _detail("status", payload.get("status")),
                _detail("action_count", payload.get("action_count")),
                _detail("llm", payload.get("has_llm_decision")),
            ]
        )

    script = str(payload.get("script", ""))
    args = payload.get("args", [])
    detail = f"{script} {args}".strip()
    parts = [detail]
    for key in ("returncode", "error", "stderr"):
        if payload.get(key) is not None and payload.get(key) != "":
            parts.append(f"{key}={payload[key]}")
    return _join_detail_parts(parts)


def _detail(label: str, value: object) -> str:
    if value is None or value == "":
        return ""
    return f"{label}={value}"


def _join_detail_parts(parts: list[object]) -> str:
    return " ".join(str(part) for part in parts if part not in (None, ""))


def _format_short_list(value: object, limit: int = 3) -> str:
    if not isinstance(value, list) or not value:
        return ""
    items = [str(item) for item in value[:limit]]
    if len(value) > limit:
        items.append(f"+{len(value) - limit} more")
    return ",".join(items)


def _step_label(step: str) -> str:
    labels = {
        "0a": "menu_init",
        "0b": "browser_login",
        "1a": "form_scan",
        "1b": "log_capture",
        "2a": "log_analysis",
        "2b": "table_classify",
        "2c": "sql_generate",
        "2d": "plan_verify",
        "3": "case_generate",
        "4": "verify",
        "5": "report",
    }
    return labels.get(step, step)


def run_menu_runtime(
    menu_name: str,
    *,
    skill_root: str | None = None,
    runs_root: str = "runs",
    checkpoint_db: str = "runs/checkpoints.sqlite",
) -> HUITestState:
    """Execute a single menu through the full agent workflow.

    This is the public entry point that wraps the internal ``_invoke_runtime``
    used by the CLI ``run`` sub-command.  It creates the initial state and
    run-store, delegates execution to ``_invoke_runtime``, persists the result,
    and returns the final ``HUITestState``.
    """
    if skill_root is None:
        skill_root = str(_default_skill_root())
    _load_default_env_files()
    state = HUITestState.new(menu_name)
    store = RunStore(runs_root)
    store.write_event(state.thread_id, "START", "run_started", {"menu": menu_name})
    store.write_log_line(state.thread_id, "START", "run_started", f"menu={menu_name}")
    result = _invoke_runtime(state, skill_root, checkpoint_db, store)
    store.write_result(result)
    store.write_event(
        result.thread_id,
        result.current_step,
        "run_finished",
        {"status": result.status, "failure_scope": result.failure_scope},
    )
    store.write_log_line(
        result.thread_id,
        result.current_step,
        "run_finished",
        f"status={result.status} failure_scope={result.failure_scope or ''}",
    )
    return result


def _invoke_runtime(
    state: HUITestState,
    skill_root: str,
    checkpoint_db: str,
    store: RunStore,
) -> HUITestState:
    runtime_context = {"thread_id": state.thread_id, "step": "SCRIPT"}

    def script_event_logger(payload: dict[str, object]) -> None:
        event = str(payload.get("event", "script_event"))
        if event == "pytest_log_line":
            _record_pytest_log_line(store, runtime_context["thread_id"], runtime_context["step"], payload)
            return
        detail = _format_runtime_event_detail(payload)
        _record_runtime_event(store, runtime_context["thread_id"], runtime_context["step"], event, detail, payload)

    repair_advisor = build_repair_advisor_from_env()
    permission_advisor = build_permission_advisor_from_env()
    sql_advisor = build_sql_advisor_from_env()
    template_reviewer = build_template_reviewer_from_env()
    handlers = build_default_step_handlers(
        skill_root,
        event_logger=script_event_logger,
        repair_advisor=repair_advisor,
        permission_advisor=permission_advisor,
        sql_advisor=sql_advisor,
        template_reviewer=template_reviewer,
    )
    handlers = _wrap_step_handlers_for_logging(handlers, store, runtime_context)
    try:
        from hui_agent.checkpoint import make_sqlite_checkpointer
        from hui_agent.graph import build_langgraph

        with make_sqlite_checkpointer(checkpoint_db) as checkpointer:
            graph = build_langgraph(
                step_handlers=handlers,
                checkpointer=checkpointer,
            )
            result = graph.invoke(
                state.to_dict(),
                {"configurable": {"thread_id": state.thread_id}},
            )
        return HUITestState.from_dict(result)
    except RuntimeError:
        workflow = build_workflow(step_handlers=handlers)
        return workflow.invoke(state)


if __name__ == "__main__":
    raise SystemExit(main())
