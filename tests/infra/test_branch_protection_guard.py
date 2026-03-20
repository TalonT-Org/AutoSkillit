"""Tests for hooks/branch_protection_guard.py — PreToolUse branch protection."""

import json
import os
import subprocess
from pathlib import Path

HOOK_SCRIPT = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "autoskillit"
    / "hooks"
    / "branch_protection_guard.py"
)


def _decision(out: dict) -> str | None:
    """Extract permissionDecision from hookSpecificOutput, or None if absent."""
    return out.get("hookSpecificOutput", {}).get("permissionDecision")


def _run_hook(tool_name: str, tool_input: dict, env_override: dict | None = None) -> dict:
    """Run the hook script with a tool call on stdin, return parsed JSON."""
    payload = json.dumps({"tool_name": tool_name, "tool_input": tool_input})
    env = {**os.environ, "AUTOSKILLIT_PROTECTED_BRANCHES": "main,integration,stable"}
    if env_override:
        env.update(env_override)
    result = subprocess.run(
        ["python", str(HOOK_SCRIPT)],
        input=payload,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    return json.loads(result.stdout) if result.stdout.strip() else {}


class TestMergeWorktreeGuard:
    def test_denies_merge_into_main(self) -> None:
        out = _run_hook(
            "mcp__autoskillit__merge_worktree",
            {"worktree_path": "/tmp/wt", "base_branch": "main"},
        )
        assert _decision(out) == "deny"

    def test_allows_merge_into_feature_branch(self) -> None:
        out = _run_hook(
            "mcp__autoskillit__merge_worktree",
            {"worktree_path": "/tmp/wt", "base_branch": "feat/foo"},
        )
        assert _decision(out) != "deny"

    def test_denies_merge_into_integration(self) -> None:
        out = _run_hook(
            "mcp__autoskillit__merge_worktree",
            {"worktree_path": "/tmp/wt", "base_branch": "integration"},
        )
        assert _decision(out) == "deny"


class TestPushToRemoteGuard:
    def test_denies_push_to_main(self) -> None:
        out = _run_hook(
            "mcp__autoskillit__push_to_remote",
            {"clone_path": "/tmp/clone", "branch": "main"},
        )
        assert _decision(out) == "deny"

    def test_allows_push_to_feature_branch(self) -> None:
        out = _run_hook(
            "mcp__autoskillit__push_to_remote",
            {"clone_path": "/tmp/clone", "branch": "impl-123"},
        )
        assert _decision(out) != "deny"


class TestCustomProtectedList:
    def test_env_var_overrides_defaults(self) -> None:
        out = _run_hook(
            "mcp__autoskillit__merge_worktree",
            {"worktree_path": "/tmp/wt", "base_branch": "production"},
            env_override={"AUTOSKILLIT_PROTECTED_BRANCHES": "production,release"},
        )
        assert _decision(out) == "deny"

    def test_main_allowed_when_not_in_custom_list(self) -> None:
        out = _run_hook(
            "mcp__autoskillit__merge_worktree",
            {"worktree_path": "/tmp/wt", "base_branch": "main"},
            env_override={"AUTOSKILLIT_PROTECTED_BRANCHES": "production"},
        )
        assert _decision(out) != "deny"

    def test_hook_respects_custom_protected_branches_env_var(self) -> None:
        """Hook blocks write to a custom protected branch injected via env var."""
        out = _run_hook(
            "mcp__autoskillit__merge_worktree",
            {"worktree_path": "/tmp/wt", "base_branch": "release"},
            env_override={"AUTOSKILLIT_PROTECTED_BRANCHES": "main,release"},
        )
        assert _decision(out) == "deny"

    def test_hook_allows_branch_not_in_custom_list(self) -> None:
        """Hook allows write to integration when it is not in the injected list."""
        out = _run_hook(
            "mcp__autoskillit__merge_worktree",
            {"worktree_path": "/tmp/wt", "base_branch": "integration"},
            env_override={"AUTOSKILLIT_PROTECTED_BRANCHES": "main,release"},
        )
        assert _decision(out) != "deny"


class TestNonMatchingTools:
    def test_ignores_unrelated_tools(self) -> None:
        out = _run_hook(
            "mcp__autoskillit__run_skill",
            {"skill_command": "/investigate", "cwd": "/tmp"},
        )
        # Should produce no output or an allow decision
        assert _decision(out) != "deny"
