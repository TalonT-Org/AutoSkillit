"""Tests for the remove_clone_guard PreToolUse hook."""

import io
import json
import subprocess
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch


def _make_proc(returncode: int, stdout: str) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    proc = MagicMock(spec=subprocess.CompletedProcess)
    proc.returncode = returncode
    proc.stdout = stdout
    return proc


def _run_hook_mocked(event: dict, side_effects: list) -> str:
    """Run main() with the given event and subprocess.run side effects."""
    from autoskillit.hooks.remove_clone_guard import main

    with (
        patch("subprocess.run", side_effect=side_effects),
        patch("sys.stdin", io.StringIO(json.dumps(event))),
    ):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
        return buf.getvalue()


def _run_hook_with_git(event: dict, git_responses: list[tuple[int, str]]) -> str:
    """Convenience wrapper: converts (rc, stdout) tuples to mock CompletedProcess objects."""
    side_effects = [_make_proc(rc, out) for rc, out in git_responses]
    return _run_hook_mocked(event, side_effects)


def _run_hook(event: dict) -> str:
    """Run main() with no subprocess calls (keep=true or no clone_path path)."""
    from autoskillit.hooks.remove_clone_guard import main

    with patch("sys.stdin", io.StringIO(json.dumps(event))):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
        return buf.getvalue()


# ── Tests retained from original (behavior unchanged) ──────────────────────


def test_approve_silently_when_keep_true():
    """keep=true skips sync check; hook exits silently with no subprocess calls."""
    out = _run_hook({"tool_input": {"keep": "true", "clone_path": "/tmp/clone"}})
    assert out.strip() == ""


def test_approve_on_malformed_json():
    """Any parse error silently approves."""
    from autoskillit.hooks.remove_clone_guard import main

    with patch("sys.stdin", io.StringIO("not-json")):
        buf = io.StringIO()
        with redirect_stdout(buf):
            try:
                main()
            except SystemExit:
                pass
        assert buf.getvalue().strip() == ""


# ── New tests: sync-check behavior ─────────────────────────────────────────


def test_approve_when_synced():
    """keep=false + 0 ahead commits → approve silently."""
    event = {"tool_input": {"keep": "false", "clone_path": "/some/clone"}}
    out = _run_hook_with_git(
        event,
        git_responses=[
            (0, ".git"),  # rev-parse --git-dir
            (0, "main"),  # rev-parse --abbrev-ref HEAD
            (0, "0"),  # rev-list --count @{upstream}..HEAD
        ],
    )
    assert out.strip() == ""


def test_deny_when_unpushed_commits():
    """keep=false + unpushed commits → deny with count and branch in reason."""
    event = {"tool_input": {"keep": "false", "clone_path": "/some/clone"}}
    out = _run_hook_with_git(
        event,
        git_responses=[
            (0, ".git"),  # rev-parse --git-dir
            (0, "feat/thing"),  # rev-parse --abbrev-ref HEAD
            (0, "2"),  # rev-list --count @{upstream}..HEAD
            (0, "abc123 Add X\ndef456 Add Y"),  # log --oneline @{upstream}..HEAD
        ],
    )
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = data["hookSpecificOutput"]["permissionDecisionReason"]
    assert "2" in reason
    assert "feat/thing" in reason


def test_deny_when_no_tracking_branch():
    """keep=false + rev-list fails (no upstream) + ls-remote finds no branch → deny."""
    event = {"tool_input": {"keep": "false", "clone_path": "/some/clone"}}
    out = _run_hook_with_git(
        event,
        git_responses=[
            (0, ".git"),  # rev-parse --git-dir
            (0, "feat/thing"),  # rev-parse --abbrev-ref HEAD
            (128, ""),  # rev-list --count (no upstream)
            (2, ""),  # ls-remote --exit-code origin (no matching ref)
        ],
    )
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
    reason = data["hookSpecificOutput"]["permissionDecisionReason"].lower()
    assert "no remote tracking branch" in reason


def test_deny_when_detached_head():
    """keep=false + detached HEAD → deny."""
    event = {"tool_input": {"keep": "false", "clone_path": "/some/clone"}}
    out = _run_hook_with_git(
        event,
        git_responses=[
            (0, ".git"),  # rev-parse --git-dir
            (0, "HEAD"),  # rev-parse --abbrev-ref HEAD (detached)
        ],
    )
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "detached" in data["hookSpecificOutput"]["permissionDecisionReason"].lower()


def test_approve_when_not_git_repo():
    """keep=false + not a git repo → approve silently (fail-open)."""
    event = {"tool_input": {"keep": "false", "clone_path": "/tmp/notgit"}}
    out = _run_hook_with_git(
        event,
        git_responses=[
            (128, ""),  # rev-parse --git-dir fails
        ],
    )
    assert out.strip() == ""


def test_approve_on_git_timeout():
    """keep=false + subprocess timeout → approve silently (fail-open)."""
    event = {"tool_input": {"keep": "false", "clone_path": "/some/clone"}}
    out = _run_hook_mocked(event, side_effects=[subprocess.TimeoutExpired("git", 10)])
    assert out.strip() == ""


def test_approve_when_no_clone_path():
    """Missing clone_path → approve silently (fail-open), no subprocess calls."""
    out = _run_hook({"tool_input": {"keep": "false"}})
    assert out.strip() == ""


def test_approve_when_keep_false_string_and_synced():
    """keep='false' string (not missing) still triggers sync check and approves when synced."""
    event = {"tool_input": {"keep": "false", "clone_path": "/some/clone"}}
    out = _run_hook_with_git(
        event,
        git_responses=[
            (0, ".git"),  # rev-parse --git-dir
            (0, "main"),  # rev-parse --abbrev-ref HEAD
            (0, "0"),  # rev-list --count @{upstream}..HEAD
        ],
    )
    assert out.strip() == ""


def test_approve_when_no_upstream_but_sha_matches_remote():
    """Fallback: no @{upstream} but branch is on remote with matching SHA → approve."""
    event = {"tool_input": {"keep": "false", "clone_path": "/some/clone"}}
    sha = "abc1234def5678abc1234def5678abc1234def56"
    out = _run_hook_with_git(
        event,
        git_responses=[
            (0, ".git"),  # rev-parse --git-dir
            (0, "feat/thing"),  # rev-parse --abbrev-ref HEAD
            (128, ""),  # rev-list --count @{upstream}..HEAD → no upstream
            (0, f"{sha}\trefs/heads/feat/thing\n"),  # ls-remote --exit-code origin
            (0, sha),  # rev-parse HEAD → matches remote
        ],
    )
    assert out.strip() == ""  # approved silently


def test_deny_when_no_upstream_and_not_on_remote():
    """Fallback: no @{upstream} and branch absent from remote → deny with original message."""
    event = {"tool_input": {"keep": "false", "clone_path": "/some/clone"}}
    out = _run_hook_with_git(
        event,
        git_responses=[
            (0, ".git"),  # rev-parse --git-dir
            (0, "feat/thing"),  # rev-parse --abbrev-ref HEAD
            (128, ""),  # rev-list --count → no upstream
            (2, ""),  # ls-remote --exit-code origin (rc=2: no matching ref)
        ],
    )
    data = json.loads(out)
    assert data["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert (
        "no remote tracking branch"
        in data["hookSpecificOutput"]["permissionDecisionReason"].lower()
    )
