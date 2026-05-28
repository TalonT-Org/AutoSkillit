"""Cross-cutting integration tests: write guard + worktree placement."""

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


class TestWriteGuardWorktreeIntegration:
    """Verify write guard + worktree path interaction."""

    @pytest.fixture(autouse=True)
    def _enable_headless(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")

    def test_worktree_path_denied_with_single_prefix(self, monkeypatch: pytest.MonkeyPatch):
        """Regression lock: single prefix blocks worktree writes."""
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/clone/")
        monkeypatch.delenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", raising=False)
        event = _build_event("Edit", "/clone/../worktrees/impl-fix/src/main.py")
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_worktree_path_allowed_with_multi_prefix(self, monkeypatch: pytest.MonkeyPatch):
        """Multi-prefix allows worktree writes."""
        monkeypatch.setenv(
            "AUTOSKILLIT_ALLOWED_WRITE_PREFIXES",
            "/clone/:/parent/worktrees/",
        )
        monkeypatch.delenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", raising=False)
        event = _build_event("Edit", "/parent/worktrees/impl-fix/src/main.py")
        result = _run_hook(event)
        assert result == ""

    @pytest.mark.anyio
    async def test_worktree_skill_prefix_derivation_includes_worktree_parent(
        self, tmp_path: Path, tool_ctx_kitchen_open
    ):
        """run_skill for a worktree skill derives both clone and worktree parent prefixes."""
        from tests.fakes import InMemoryHeadlessExecutor

        executor = InMemoryHeadlessExecutor()
        tool_ctx_kitchen_open.executor = executor
        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()

        from autoskillit.server.tools.tools_execution import run_skill

        await run_skill(
            skill_command="/autoskillit:implement-worktree-no-merge plan.md",
            cwd=str(clone_dir),
            output_dir=".",
        )
        assert len(executor.calls) == 1
        prefixes = executor.calls[0].allowed_write_prefixes
        assert any(str(clone_dir) in p for p in prefixes)
        worktree_parent = str((clone_dir.parent / "worktrees").resolve()) + "/"
        assert worktree_parent in prefixes

    @pytest.mark.anyio
    async def test_non_worktree_skill_gets_single_prefix(
        self, tmp_path: Path, tool_ctx_kitchen_open
    ):
        """Non-worktree skill gets only the cwd-based prefix."""
        from tests.fakes import InMemoryHeadlessExecutor

        executor = InMemoryHeadlessExecutor()
        tool_ctx_kitchen_open.executor = executor
        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()

        from autoskillit.server.tools.tools_execution import run_skill

        await run_skill(
            skill_command="/autoskillit:dry-walkthrough plan.md",
            cwd=str(clone_dir),
            output_dir=".",
        )
        assert len(executor.calls) == 1
        prefixes = executor.calls[0].allowed_write_prefixes
        worktree_parent = str((clone_dir.parent / "worktrees").resolve()) + "/"
        assert worktree_parent not in prefixes
