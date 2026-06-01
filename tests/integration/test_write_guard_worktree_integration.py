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

    def test_worktree_path_denied_with_single_prefix(self, monkeypatch: pytest.MonkeyPatch):
        """Regression lock: single prefix blocks worktree writes."""
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
        monkeypatch.setenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIX", "/clone/")
        monkeypatch.delenv("AUTOSKILLIT_ALLOWED_WRITE_PREFIXES", raising=False)
        event = _build_event("Edit", "/clone/../worktrees/impl-fix/src/main.py")
        result = _run_hook(event)
        parsed = json.loads(result)
        assert parsed["hookSpecificOutput"]["permissionDecision"] == "deny"

    def test_worktree_path_allowed_with_multi_prefix(self, monkeypatch: pytest.MonkeyPatch):
        """Multi-prefix allows worktree writes."""
        monkeypatch.setenv("AUTOSKILLIT_HEADLESS", "1")
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
        self, tmp_path: Path, tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
    ):
        """run_skill for a worktree skill derives both clone and worktree parent prefixes."""
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
        from tests.fakes import InMemoryHeadlessExecutor

        executor = InMemoryHeadlessExecutor()
        tool_ctx_kitchen_open.executor = executor
        monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()
        plan = clone_dir / "plan.md"
        plan.write_text("content")

        from autoskillit.server.tools.tools_execution import run_skill

        await run_skill(
            skill_command=f"/autoskillit:implement-worktree-no-merge {plan}",
            cwd=str(clone_dir),
            output_dir=".",
        )
        assert len(executor.calls) == 1
        prefixes = executor.calls[0].allowed_write_prefixes
        assert str(clone_dir.resolve()) + "/" in prefixes
        worktree_parent = str((clone_dir.parent / "worktrees").resolve()) + "/"
        assert worktree_parent in prefixes

    @pytest.mark.anyio
    async def test_non_worktree_skill_gets_single_prefix(
        self, tmp_path: Path, tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
    ):
        """Non-worktree skill gets only the cwd-based prefix."""
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
        from tests.fakes import InMemoryHeadlessExecutor

        executor = InMemoryHeadlessExecutor()
        tool_ctx_kitchen_open.executor = executor
        monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)
        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()
        plan = clone_dir / "plan.md"
        plan.write_text("content")

        from autoskillit.server.tools.tools_execution import run_skill

        await run_skill(
            skill_command=f"/autoskillit:dry-walkthrough {plan}",
            cwd=str(clone_dir),
            output_dir=".",
        )
        assert len(executor.calls) == 1
        prefixes = executor.calls[0].allowed_write_prefixes
        worktree_parent = str((clone_dir.parent / "worktrees").resolve()) + "/"
        assert worktree_parent not in prefixes

    @pytest.mark.anyio
    async def test_closure_write_paths_extend_allowed_prefixes(
        self, tmp_path: Path, tool_ctx_kitchen_open, monkeypatch: pytest.MonkeyPatch
    ):
        """run_skill for a skill with deps declaring write_paths includes dep paths in prefixes."""
        monkeypatch.delenv("AUTOSKILLIT_HEADLESS", raising=False)
        from unittest.mock import MagicMock

        from autoskillit.workspace.skills import SkillInfo, SkillSource
        from tests.fakes import InMemoryHeadlessExecutor

        executor = InMemoryHeadlessExecutor()
        tool_ctx_kitchen_open.executor = executor

        # Create a synthetic SKILL.md for the dep skill with write_paths
        dep_dir = tmp_path / "dep-skill"
        dep_dir.mkdir()
        dep_skill_md = dep_dir / "SKILL.md"
        dep_skill_md.write_text(
            "---\nname: dep-skill\ndescription: A dep.\n"
            'write_paths: ["{{AUTOSKILLIT_TEMP}}/dep-skill/"]\n---\nbody\n'
        )

        # Mock skill_resolver to return the dep's SkillInfo
        mock_resolver = MagicMock()
        mock_resolver.resolve.side_effect = lambda name: (
            SkillInfo(
                name="dep-skill",
                source=SkillSource.BUNDLED_EXTENDED,
                path=dep_skill_md,
            )
            if name == "dep-skill"
            else SkillInfo(
                name=name,
                source=SkillSource.BUNDLED_EXTENDED,
                path=dep_dir / "SKILL.md",
            )
        )
        tool_ctx_kitchen_open.skill_resolver = mock_resolver

        # Mock session_skill_manager to return a closure containing dep-skill
        mock_ssm = MagicMock()
        mock_ssm.compute_skill_closure.return_value = frozenset({"target-skill", "dep-skill"})
        mock_ssm.init_session.return_value = MagicMock(path=str(tmp_path / "session"))
        mock_ssm.activate_skill_deps.return_value = True
        tool_ctx_kitchen_open.session_skill_manager = mock_ssm

        monkeypatch.setattr("autoskillit.server._ctx", tool_ctx_kitchen_open)

        clone_dir = tmp_path / "clone"
        clone_dir.mkdir()
        (clone_dir / ".autoskillit" / "temp" / "target-skill").mkdir(parents=True)

        from autoskillit.server.tools.tools_execution import run_skill

        await run_skill(skill_command="/autoskillit:target-skill", cwd=str(clone_dir))

        assert len(executor.calls) == 1
        prefixes = executor.calls[0].allowed_write_prefixes
        expected_dep_prefix = (
            str((clone_dir / ".autoskillit" / "temp" / "dep-skill").resolve()) + "/"
        )
        assert expected_dep_prefix in prefixes
