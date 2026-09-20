"""Structural enforcement tests: session-scope metadata on HookDef.

These tests ensure that HookDef declares a session_scope field. The hook-runtime
parity and prologue contracts live in tests/hooks and tests/arch.
"""

from __future__ import annotations

import dataclasses

import pytest

from autoskillit.hook_registry import (
    HOOK_REGISTRY,
    HookDef,
    hook_applies_to_backend,
)

pytestmark = [pytest.mark.layer("infra"), pytest.mark.small]


def test_hookdef_has_session_scope() -> None:
    """HookDef must have a session_scope field."""
    fields = {f.name for f in dataclasses.fields(HookDef)}
    assert "session_scope" in fields, (
        "HookDef is missing the 'session_scope' field. "
        "Add: session_scope: Literal['any', 'headless_only', 'interactive_only'] = 'any'"
    )


def test_session_scope_default_is_any() -> None:
    """The default session_scope must be 'any' so existing entries remain unchanged."""
    default_def = HookDef(matcher="test.*", scripts=["test.py"])
    assert default_def.session_scope == "any"  # type: ignore[attr-defined]


@pytest.mark.parametrize("backend", ["claude_code", "codex"])
@pytest.mark.parametrize("session_scope", ["headless", "interactive"])
def test_git_ops_guard_applies_to_headless_and_interactive_sessions(
    backend: str, session_scope: str
) -> None:
    hook = next(hook for hook in HOOK_REGISTRY if "guards/git_ops_guard.py" in hook.scripts)
    assert hook.session_scope == "any"
    assert hook_applies_to_backend(
        hook,
        backend=backend,  # type: ignore[arg-type]
        session_scope=session_scope,  # type: ignore[arg-type]
    )
