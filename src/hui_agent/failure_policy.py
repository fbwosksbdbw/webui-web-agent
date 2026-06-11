from __future__ import annotations

from typing import Literal

FailureScope = Literal["menu", "environment", "config"]


class WorkflowFailure(RuntimeError):
    scope: FailureScope = "menu"


class MenuFailure(WorkflowFailure):
    scope: FailureScope = "menu"


class EnvironmentFailure(WorkflowFailure):
    scope: FailureScope = "environment"


class ConfigFailure(WorkflowFailure):
    scope: FailureScope = "config"


def scope_for_exception(exc: Exception) -> FailureScope:
    if isinstance(exc, WorkflowFailure):
        return exc.scope
    return "menu"
