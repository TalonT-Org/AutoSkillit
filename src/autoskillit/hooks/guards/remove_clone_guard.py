#!/usr/bin/env python3
"""PreToolUse hook: sync-check before remove_clone.

Approves removal only when the clone's current branch has been pushed to
its remote tracking branch (0 commits ahead of upstream). Denies with an
explanatory message if:
  - the branch has unpushed commits
  - no remote tracking branch is configured
  - the clone is in detached HEAD state

Approves silently (fail-open) when:
  - keep="true" (explicit preserve intent)
  - clone_path is absent or not a git repository
  - any git subprocess fails or times out
"""

import json
import subprocess
import sys

REMOVE_CLONE_DENY_TRIGGER: str = "Clone at"


def _resolve_push_remote(clone_path: str) -> str:
    """Return the git remote name for real (non-file://) push operations."""
    for name in ("upstream", "origin"):
        try:
            result = subprocess.run(
                ["git", "-C", clone_path, "remote", "get-url", name],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0:
                continue
            if result.stdout.strip().startswith("file://"):
                continue
            return name
        except (subprocess.TimeoutExpired, OSError):
            continue
    return "origin"


def _git(clone_path: str, *args: str, timeout: int = 10) -> tuple[int, str]:
    """Run a git command in clone_path. Returns (returncode, stdout.strip())."""
    try:
        proc = subprocess.run(
            ["git", "-C", clone_path, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return proc.returncode, proc.stdout.strip()
    except (subprocess.TimeoutExpired, OSError):
        return -1, ""


def _check_live_worktrees(clone_path: str) -> tuple[bool, str]:
    """Return (approved, deny_reason). Denies if linked worktrees exist."""
    rc, output = _git(clone_path, "worktree", "list", "--porcelain")
    if rc != 0:
        return True, ""
    worktrees = []
    for line in output.splitlines():
        if line.startswith("worktree "):
            worktrees.append(line.split(" ", 1)[1].strip())
    linked = worktrees[1:] if len(worktrees) > 1 else []
    if linked:
        paths = "\n  ".join(linked)
        return False, (
            f"Clone at {clone_path!r} has {len(linked)} active linked "
            f"worktree(s):\n  {paths}\n\n"
            "Remove worktrees first (git worktree remove <path>), "
            "then re-run remove_clone."
        )
    return True, ""


def _check_sync(clone_path: str) -> tuple[bool, str]:
    """Return (approved, deny_reason).

    approved=True  → allow deletion (no output printed)
    approved=False → deny deletion (deny_reason is the message to display)
    """
    # Check for active linked worktrees first
    approved, reason = _check_live_worktrees(clone_path)
    if not approved:
        return approved, reason

    # Verify it's a git repo (fail-open if not)
    rc, _ = _git(clone_path, "rev-parse", "--git-dir")
    if rc != 0:
        return True, ""

    # Get current branch name
    rc, branch = _git(clone_path, "rev-parse", "--abbrev-ref", "HEAD")
    if rc != 0 or not branch:
        return True, ""  # fail-open

    # Detached HEAD — cannot determine sync status
    if branch == "HEAD":
        return False, (
            f"Clone at {clone_path!r} is in detached HEAD state. "
            "Cannot verify sync status. Push the relevant branch first, "
            "then re-run remove_clone."
        )

    # Count commits ahead of upstream tracking branch
    rc, count_str = _git(clone_path, "rev-list", "--count", "@{upstream}..HEAD")
    if rc != 0:
        # No tracking branch — try ls-remote fallback to check if branch exists on remote
        resolved_remote = _resolve_push_remote(clone_path)
        ls_rc, ls_out = _git(
            clone_path,
            "ls-remote",
            "--exit-code",
            resolved_remote,
            f"refs/heads/{branch}",
            timeout=15,
        )
        if ls_rc == 0:
            # Branch is on remote — compare SHA to verify fully pushed
            parts = ls_out.strip().split()
            if not parts:
                return True, ""  # fail-open: ls-remote returned empty output
            remote_sha = parts[0]
            head_rc, local_sha = _git(clone_path, "rev-parse", "HEAD")
            if head_rc == 0 and local_sha.strip() == remote_sha:
                return True, ""  # synced via ls-remote
        # No tracking branch and ls-remote confirms not pushed (rc=2) or error
        return False, (
            f"Clone at {clone_path!r} (branch: {branch!r}) has no remote "
            "tracking branch. Push the branch first before removing the clone:\n"
            f"  git -C {clone_path} push -u {resolved_remote} {branch}"
        )

    try:
        ahead = int(count_str)
    except ValueError:
        return True, ""  # fail-open on parse error

    if ahead == 0:
        return True, ""  # synced — safe to remove

    # Collect a short list of unpushed commit summaries
    _rc2, log = _git(clone_path, "log", "--oneline", "@{upstream}..HEAD")
    commits = log if _rc2 == 0 and log else "(could not list commits)"

    return False, (
        f"Clone at {clone_path!r} (branch: {branch!r}) has {ahead} unpushed "
        f"commit(s):\n{commits}\n\n"
        "Push to remote first, then re-run remove_clone."
    )


def main() -> None:
    try:
        event = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)  # malformed event — approve

    tool_input = event.get("tool_input", {})
    keep = str(tool_input.get("keep", "false")).strip().lower()
    clone_path = tool_input.get("clone_path", "")

    if keep == "true":
        sys.exit(0)  # explicit preserve intent — skip check

    if not clone_path:
        sys.exit(0)  # no path to inspect — fail-open

    approved, reason = _check_sync(clone_path)
    if not approved:
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": reason,
                    }
                }
            )
        )
    sys.exit(0)


if __name__ == "__main__":
    main()
