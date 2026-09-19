"""Per-backend guard-file filesystem consistency."""

from __future__ import annotations

import pytest

from autoskillit.execution.backends.claude import ClaudeCodeBackend
from autoskillit.execution.backends.codex import CodexBackend
from autoskillit.hook_registry import HOOKS_DIR

pytestmark = [pytest.mark.medium]


def test_claude_code_applicable_guards_exist_on_disk() -> None:
    backend = ClaudeCodeBackend()
    guards = backend.capabilities.applicable_guards
    assert guards, "applicable_guards must be non-empty"
    for name in sorted(guards):
        script = HOOKS_DIR / "guards" / f"{name}.py"
        assert script.is_file(), (
            f"ClaudeCodeBackend declares applicable_guard {name!r} but {script} does not exist"
        )


def test_codex_applicable_guards_exist_on_disk() -> None:
    backend = CodexBackend()
    guards = backend.capabilities.applicable_guards
    assert guards, "applicable_guards must be non-empty"
    for name in sorted(guards):
        script = HOOKS_DIR / "guards" / f"{name}.py"
        assert script.is_file(), (
            f"CodexBackend declares applicable_guard {name!r} but {script} does not exist"
        )
