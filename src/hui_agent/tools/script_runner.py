from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable, Sequence


class ScriptError(RuntimeError):
    def __init__(self, command: Sequence[str], returncode: int, stdout: str, stderr: str):
        self.command = list(command)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"Script failed ({returncode}): {' '.join(self.command)}\n{stderr or stdout}"
        )


class ScriptRunner:
    def __init__(
        self,
        *,
        skill_root: str | Path,
        run_impl: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        event_logger: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.skill_root = Path(skill_root).resolve()
        self.scripts_dir = self.skill_root / "scripts"
        self._run = run_impl or subprocess.run
        self._event_logger = event_logger

    def build_command(self, script_name: str, args: Sequence[str] = ()) -> list[str]:
        script_path = self.scripts_dir / script_name
        if not script_path.exists():
            raise FileNotFoundError(f"Skill script not found: {script_path}")
        return [sys.executable, str(script_path), *[str(arg) for arg in args]]

    def run_text(
        self,
        script_name: str,
        args: Sequence[str] = (),
        *,
        cwd: str | Path | None = None,
        timeout: int | None = None,
    ) -> str:
        command = self.build_command(script_name, args)
        arg_list = [str(arg) for arg in args]
        safe_args = _redact_args(arg_list)
        safe_command = [*command[:2], *safe_args]
        self._emit(
            {
                "event": "script_started",
                "script": script_name,
                "args": safe_args,
                "command": safe_command,
            }
        )
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        env["MSYS_NO_PATHCONV"] = "1"
        env["NO_PROXY"] = _merge_no_proxy(env.get("NO_PROXY") or env.get("no_proxy", ""))
        env["no_proxy"] = env["NO_PROXY"]
        try:
            result = self._run(
                command,
                cwd=str(cwd or self.skill_root),
                env=env,
                text=True,
                capture_output=True,
                encoding="utf-8",
                timeout=timeout,
            )
        except Exception as exc:
            self._emit(
                {
                    "event": "script_failed",
                    "script": script_name,
                    "args": safe_args,
                    "error": str(exc),
                }
            )
            raise
        if result.returncode != 0:
            self._emit(
                {
                    "event": "script_failed",
                    "script": script_name,
                    "args": safe_args,
                    "returncode": result.returncode,
                    "stderr": (result.stderr or result.stdout or "")[:1000],
                }
            )
            raise ScriptError(command, result.returncode, result.stdout, result.stderr)
        self._emit(
            {
                "event": "script_finished",
                "script": script_name,
                "args": safe_args,
                "returncode": result.returncode,
                "stdout_preview": (result.stdout or "")[:1000],
            }
        )
        return result.stdout.strip()

    def run_json(
        self,
        script_name: str,
        args: Sequence[str] = (),
        *,
        cwd: str | Path | None = None,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        text = self.run_text(script_name, args, cwd=cwd, timeout=timeout)
        value = _parse_last_json_value(text)
        if not isinstance(value, dict):
            raise ValueError(f"Expected JSON object, got {type(value).__name__}")
        return value

    def run_json_value(
        self,
        script_name: str,
        args: Sequence[str] = (),
        *,
        cwd: str | Path | None = None,
        timeout: int | None = None,
    ) -> Any:
        text = self.run_text(script_name, args, cwd=cwd, timeout=timeout)
        return _parse_last_json_value(text)

    def emit(self, payload: dict[str, Any]) -> None:
        self._emit(payload)

    @property
    def has_event_logger(self) -> bool:
        return self._event_logger is not None

    def _emit(self, payload: dict[str, Any]) -> None:
        if self._event_logger is None:
            return
        try:
            self._event_logger(payload)
        except Exception:
            return


def _parse_last_json_object(text: str) -> dict[str, Any]:
    value = _parse_last_json_value(text)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object, got {type(value).__name__}")
    return value


def _parse_last_json_value(text: str) -> Any:
    decoder = json.JSONDecoder()
    last: Any | None = None
    last_span = -1
    for index, char in enumerate(text):
        if char not in "[{":
            continue
        try:
            value, end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, (dict, list)) and end > last_span:
            last = value
            last_span = end
    if last is None:
        raise ValueError(f"No JSON value found in script output: {text[:200]}")
    return last


def _merge_no_proxy(current: str) -> str:
    entries = [entry.strip() for entry in current.split(",") if entry.strip()]
    seen = {entry.lower() for entry in entries}
    for entry in ("localhost", "127.0.0.1", "::1"):
        if entry.lower() not in seen:
            entries.append(entry)
            seen.add(entry.lower())
    return ",".join(entries)


def _redact_args(args: list[str]) -> list[str]:
    sensitive_options = {"--ssh-pass", "--password", "--passwd", "--token", "--secret"}
    result: list[str] = []
    redact_next = False
    for arg in args:
        if redact_next:
            result.append("***")
            redact_next = False
            continue
        lower = arg.lower()
        if lower in sensitive_options:
            result.append(arg)
            redact_next = True
            continue
        if any(lower.startswith(option + "=") for option in sensitive_options):
            key, _, _value = arg.partition("=")
            result.append(f"{key}=***")
            continue
        result.append(arg)
    return result
