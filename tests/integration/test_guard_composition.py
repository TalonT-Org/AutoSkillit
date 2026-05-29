"""Guard composition integration tests.

Verifies the full prefix derivation -> env var injection -> write guard
enforcement chain works correctly for worktree and non-worktree skills.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = [pytest.mark.small]


def _run_hook(event: dict | str) -> str:
    from autoskillit.hooks.guards.write_guard import main

    stdin_text = json.dumps(event) if isinstance(event, dict) else event
    buf = io.StringIO()
    with (
        patch("sys.stdin", io.StringIO(stdin_text)),
        redirect_stdout(buf),
    ):
        try:
            main()
        except SystemExit:
            pass
    return buf.getvalue()


def _build_event(tool_name: str, file_path: str) -> dict:
    return {"tool_name": tool_name, "tool_input": {"file_path": file_path}}


class TestGuardComposition:
    """End-to-end guard composition tests."""

    @pytest.fixture(autouse=True)
    def _enable_headless(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")

    def test_worktree_skill_can_write_to_external_worktree(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Simulate the full chain: clone prefix + worktree parent both writable."""
        clone = tmp_path / "clone"
        clone.mkdir()
        wt_parent = tmp_path / "worktrees"
        wt_parent.mkdir()

        monkeypatch.setenv(
            "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES",
            f"{clone}/:{wt_parent}/",
        )
        monkeypatch.delenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", raising=False)

        assert _run_hook(_build_event("Edit", f"{clone}/src/main.py")) == ""
        assert _run_hook(_build_event("Edit", f"{wt_parent}/impl-fix/src/main.py")) == ""

        result = _run_hook(_build_event("Edit", "/outside/file.py"))
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_non_worktree_skill_cannot_write_to_worktree_parent(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ):
        """Non-worktree skill with output_dir=. gets only clone prefix."""
        clone = tmp_path / "clone"
        clone.mkdir()
        wt_parent = tmp_path / "worktrees"
        wt_parent.mkdir()

        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", f"{clone}/")
        monkeypatch.delenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", raising=False)

        assert _run_hook(_build_event("Edit", f"{clone}/src/main.py")) == ""

        result = _run_hook(_build_event("Edit", f"{wt_parent}/impl-fix/src/main.py"))
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"
