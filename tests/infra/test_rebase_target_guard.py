"""Tests for rebase_target_guard.py PreToolUse hook."""

from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autoskillit.hooks.guards.rebase_target_guard import main

pytestmark = pytest.mark.layer("infra")


def _run_guard(
    tool_name: str,
    tool_input: dict,
    headless: bool = False,
    session_type: str | None = None,
    git_common_dir: str | None = None,
    tmp_path: Path | None = None,
) -> str:
    """Run the guard and return stdout."""
    stdin_content = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    env_updates: dict[str, str] = {}
    if headless:
        env_updates["AUTOSKILLIT_HEADLESS"] = "1"
    if session_type is not None:
        env_updates["AUTOSKILLIT_SESSION_TYPE"] = session_type

    def _fake_run(cmd, **_kwargs):
        result = MagicMock()
        if "--git-common-dir" in cmd:
            git_dir = git_common_dir if git_common_dir else str(tmp_path / "clone" / ".git")
            result.returncode = 0
            result.stdout = git_dir + "\n"
        else:
            result.returncode = 1
            result.stdout = ""
        return result

    with (
        patch.dict(os.environ, env_updates, clear=False),
        patch("sys.stdin", io.StringIO(stdin_content)),
        patch("subprocess.run", side_effect=_fake_run),
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
        return buf.getvalue()


def _parse_decision(output: str) -> str | None:
    """Extract permissionDecision from hook output, or None if empty."""
    if not output.strip():
        return None
    data = json.loads(output)
    return data.get("hookSpecificOutput", {}).get("permissionDecision")


class TestRebaseTargetGuardPassthrough:
    """Cases where the guard must pass (exit 0, no deny output)."""

    def test_interactive_session_passes(self) -> None:
        result = _run_guard(
            "Bash",
            {"command": "git rebase origin/main"},
            headless=False,
        )
        assert _parse_decision(result) != "deny"

    def test_headless_orchestrator_passes(self) -> None:
        result = _run_guard(
            "Bash",
            {"command": "git rebase origin/main"},
            headless=True,
            session_type="orchestrator",
        )
        assert _parse_decision(result) != "deny"

    def test_headless_fleet_passes(self) -> None:
        result = _run_guard(
            "Bash",
            {"command": "git rebase origin/main"},
            headless=True,
            session_type="fleet",
        )
        assert _parse_decision(result) != "deny"

    def test_non_rebase_bash_passes(self) -> None:
        result = _run_guard(
            "Bash",
            {"command": "git status"},
            headless=True,
            session_type="skill",
        )
        assert _parse_decision(result) != "deny"

    def test_variable_pattern_passes(self) -> None:
        """git rebase with shell variable — guard cannot validate, must fail-open."""
        result = _run_guard(
            "Bash",
            {"command": 'git rebase "$REMOTE/${BASE_BRANCH}"'},
            headless=True,
            session_type="skill",
        )
        assert _parse_decision(result) != "deny"

    def test_no_sidecar_passes(self, tmp_path: Path) -> None:
        """No sidecar file → guard cannot validate → fail-open."""
        # git_common_dir points to a path where no sidecar file exists
        result = _run_guard(
            "Bash",
            {"command": "git rebase origin/main", "cwd": str(tmp_path / "clone" / "wt")},
            headless=True,
            session_type="skill",
            git_common_dir=str(tmp_path / "clone" / ".git"),
            tmp_path=tmp_path,
        )
        assert _parse_decision(result) != "deny"

    def test_malformed_json_passes(self) -> None:
        """Malformed stdin → fail-open."""
        env_updates = {"AUTOSKILLIT_HEADLESS": "1", "AUTOSKILLIT_SESSION_TYPE": "skill"}
        with (
            patch.dict(os.environ, env_updates, clear=False),
            patch("sys.stdin", io.StringIO("not json")),
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                try:
                    main()
                except SystemExit:
                    pass
            assert _parse_decision(buf.getvalue()) != "deny"

    def test_correct_target_passes(self, tmp_path: Path) -> None:
        """Rebase target matches sidecar → allow."""
        clone = tmp_path / "clone"
        wt = clone / "wt"
        wt.mkdir(parents=True)
        sidecar = clone / ".autoskillit" / "temp" / "worktrees" / "wt" / "base-branch"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text("feature/123\n")

        result = _run_guard(
            "Bash",
            {"command": "git rebase origin/feature/123", "cwd": str(wt)},
            headless=True,
            session_type="skill",
            git_common_dir=str(clone / ".git"),
            tmp_path=tmp_path,
        )
        assert _parse_decision(result) != "deny"


class TestRebaseTargetGuardDeny:
    """Cases where the guard must deny."""

    def test_wrong_target_denied(self, tmp_path: Path) -> None:
        """Rebase target does not match sidecar → deny."""
        clone = tmp_path / "clone"
        wt = clone / "wt"
        wt.mkdir(parents=True)
        sidecar = clone / ".autoskillit" / "temp" / "worktrees" / "wt" / "base-branch"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text("feature/123\n")

        result = _run_guard(
            "Bash",
            {"command": "git rebase origin/main", "cwd": str(wt)},
            headless=True,
            session_type="skill",
            git_common_dir=str(clone / ".git"),
            tmp_path=tmp_path,
        )
        assert _parse_decision(result) == "deny"

    def test_deny_message_contains_expected_branch(self, tmp_path: Path) -> None:
        """Deny message must include the sidecar branch so the session can self-correct."""
        clone = tmp_path / "clone"
        wt = clone / "wt"
        wt.mkdir(parents=True)
        sidecar = clone / ".autoskillit" / "temp" / "worktrees" / "wt" / "base-branch"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text("feature/456\n")

        result = _run_guard(
            "Bash",
            {"command": "git rebase origin/main", "cwd": str(wt)},
            headless=True,
            session_type="skill",
            git_common_dir=str(clone / ".git"),
            tmp_path=tmp_path,
        )
        data = json.loads(result)
        reason = data["hookSpecificOutput"]["permissionDecisionReason"]
        assert "feature/456" in reason

    def test_upstream_remote_wrong_target_denied(self, tmp_path: Path) -> None:
        """upstream remote variant is also matched."""
        clone = tmp_path / "clone"
        wt = clone / "wt"
        wt.mkdir(parents=True)
        sidecar = clone / ".autoskillit" / "temp" / "worktrees" / "wt" / "base-branch"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text("feature/123\n")

        result = _run_guard(
            "Bash",
            {"command": "git rebase upstream/main", "cwd": str(wt)},
            headless=True,
            session_type="skill",
            git_common_dir=str(clone / ".git"),
            tmp_path=tmp_path,
        )
        assert _parse_decision(result) == "deny"

    def test_run_cmd_variant_denied(self, tmp_path: Path) -> None:
        """run_cmd tool with cmd key (not command) is also checked."""
        clone = tmp_path / "clone"
        wt = clone / "wt"
        wt.mkdir(parents=True)
        sidecar = clone / ".autoskillit" / "temp" / "worktrees" / "wt" / "base-branch"
        sidecar.parent.mkdir(parents=True)
        sidecar.write_text("feature/123\n")

        result = _run_guard(
            "mcp__autoskillit__run_cmd",
            {"cmd": "git rebase origin/main", "cwd": str(wt)},
            headless=True,
            session_type="skill",
            git_common_dir=str(clone / ".git"),
            tmp_path=tmp_path,
        )
        assert _parse_decision(result) == "deny"
