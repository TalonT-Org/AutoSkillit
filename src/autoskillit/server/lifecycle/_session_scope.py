"""Session-shape admission for every registered MCP tool."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import ParamSpec, TypeVar

from autoskillit.core import (
    SESSION_SCOPE_ANY,
    SessionScope,
    SessionShape,
    SessionType,
    session_shape,
)
from autoskillit.pipeline import headless_error_result

P = ParamSpec("P")
R = TypeVar("R")

SCOPE_ORCHESTRATOR_EXACT = SessionScope.of(headless="interactive_only") | SessionScope.of(
    headless="headless_only", tiers={SessionType.ORCHESTRATOR}
)
SCOPE_ORCHESTRATOR_OR_HIGHER = SessionScope.of(headless="interactive_only") | SessionScope.of(
    headless="headless_only", tiers={SessionType.ORCHESTRATOR, SessionType.FLEET}
)
SCOPE_FLEET = SessionScope.of(tiers={SessionType.FLEET})

TOOL_SESSION_SCOPES: dict[str, SessionScope] = {}
SCOPE_ANY = SESSION_SCOPE_ANY


def admit_tool_session_scope(
    tool_name: str, scope: SessionScope, shape: SessionShape
) -> str | None:
    """Return a refusal envelope when ``shape`` is outside ``scope``."""
    if scope.admits(shape):
        return None
    admitted = ", ".join(sorted(candidate.label for candidate in scope.admitted))
    if scope is SCOPE_FLEET:
        message = f"{tool_name} requires a fleet session. Current session is {shape.label}."
    else:
        message = (
            f"{tool_name} cannot be called from {shape.label} sessions. Admitted: {admitted}."
        )
    return headless_error_result(message)


def session_scoped(
    scope: SessionScope,
    *,
    refusal: Callable[[str, SessionShape, SessionScope], str] | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R | str]]]:
    """Record and enforce a tool's declared session scope before its handler."""

    def decorate(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R | str]]:
        TOOL_SESSION_SCOPES[fn.__name__] = scope

        @wraps(fn)
        async def wrapped(*args: P.args, **kwargs: P.kwargs) -> R | str:
            try:
                shape = session_shape()
            except ValueError as exc:
                return headless_error_result(f"{fn.__name__}: {exc}")
            refusal_result = admit_tool_session_scope(fn.__name__, scope, shape)
            if refusal_result is not None:
                return (
                    refusal(fn.__name__, shape, scope) if refusal is not None else refusal_result
                )
            return await fn(*args, **kwargs)

        return wrapped

    return decorate
