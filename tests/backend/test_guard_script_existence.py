"""Per-backend contract tests for applicable_guards filesystem resolution."""

from __future__ import annotations

import pytest

from autoskillit.execution.backends import ClaudeCodeBackend, CodexBackend
from autoskillit.hook_registry import HOOKS_DIR

pytestmark = [pytest.mark.small]


def test_claude_code_applicable_guards_exist_on_disk() -> None:
    caps = ClaudeCodeBackend().capabilities
    assert caps.applicable_guards, "applicable_guards must be non-empty"
    for name in sorted(caps.applicable_guards):
        script = HOOKS_DIR / "guards" / f"{name}.py"
        assert script.is_file(), (
            f"ClaudeCodeBackend applicable_guard {name!r}: {script} does not exist"
        )


def test_codex_applicable_guards_exist_on_disk() -> None:
    caps = CodexBackend().capabilities
    assert caps.applicable_guards, "applicable_guards must be non-empty"
    for name in sorted(caps.applicable_guards):
        script = HOOKS_DIR / "guards" / f"{name}.py"
        assert script.is_file(), f"CodexBackend applicable_guard {name!r}: {script} does not exist"
