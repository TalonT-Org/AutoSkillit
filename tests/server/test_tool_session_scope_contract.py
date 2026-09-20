"""Session-scope admission contracts for registered MCP tools."""

from __future__ import annotations

import asyncio

import pytest

from autoskillit.core import SessionShape, SessionType
from autoskillit.server.lifecycle._session_scope import (
    SCOPE_FLEET,
    SCOPE_ORCHESTRATOR_EXACT,
    SCOPE_ORCHESTRATOR_OR_HIGHER,
    TOOL_SESSION_SCOPES,
    admit_tool_session_scope,
    session_scoped,
)

pytestmark = [pytest.mark.layer("server"), pytest.mark.small]


@pytest.mark.parametrize(
    ("scope", "shape", "admitted"),
    [
        (SCOPE_ORCHESTRATOR_EXACT, SessionShape(True, SessionType.ORCHESTRATOR), True),
        (SCOPE_ORCHESTRATOR_EXACT, SessionShape(True, SessionType.FLEET), False),
        (SCOPE_ORCHESTRATOR_OR_HIGHER, SessionShape(True, SessionType.FLEET), True),
        (SCOPE_FLEET, SessionShape(False, SessionType.SKILL), False),
    ],
)
def test_tool_scope_admission(scope, shape, admitted) -> None:
    result = admit_tool_session_scope("tool", scope, shape)

    assert (result is None) is admitted


def test_session_scoped_records_scope_in_registry() -> None:
    """The decorator must register ``fn.__name__`` -> ``scope`` at decoration time."""
    registered_before = dict(TOOL_SESSION_SCOPES)

    @session_scoped(SCOPE_FLEET)
    async def _decorated_test_tool_a() -> str:
        return "ok"

    try:
        assert TOOL_SESSION_SCOPES["_decorated_test_tool_a"] is SCOPE_FLEET
    finally:
        TOOL_SESSION_SCOPES.pop("_decorated_test_tool_a", None)
        TOOL_SESSION_SCOPES.clear()
        TOOL_SESSION_SCOPES.update(registered_before)


def test_session_scoped_calls_inner_when_admitted(monkeypatch: pytest.MonkeyPatch) -> None:
    """The decorator must call the inner coroutine when ``admit_tool_session_scope`` admits."""
    monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "fleet")

    @session_scoped(SCOPE_FLEET)
    async def _decorated_admit_call() -> str:
        return "inner-called"

    registered_before = dict(TOOL_SESSION_SCOPES)
    try:
        assert TOOL_SESSION_SCOPES["_decorated_admit_call"] is SCOPE_FLEET
        result = asyncio.run(_decorated_admit_call())
        assert result == "inner-called"
    finally:
        TOOL_SESSION_SCOPES.pop("_decorated_admit_call", None)
        TOOL_SESSION_SCOPES.clear()
        TOOL_SESSION_SCOPES.update(registered_before)


def test_session_scoped_returns_refusal_when_denied(monkeypatch: pytest.MonkeyPatch) -> None:
    """The decorator must return a refusal envelope when the shape is outside the scope."""
    monkeypatch.setenv("AUTOSKILLIT_SESSION_TYPE", "skill")

    @session_scoped(SCOPE_FLEET)
    async def _decorated_refusal_call() -> str:
        return "should-not-run"

    registered_before = dict(TOOL_SESSION_SCOPES)
    try:
        refusal = asyncio.run(_decorated_refusal_call())
        assert isinstance(refusal, str)
        assert "fleet" in refusal.lower()
    finally:
        TOOL_SESSION_SCOPES.pop("_decorated_refusal_call", None)
        TOOL_SESSION_SCOPES.clear()
        TOOL_SESSION_SCOPES.update(registered_before)
