"""Recipe cmd externalization merge — rebase, PR polling, branch management."""

from __future__ import annotations

import json
import time

from autoskillit.core import get_logger, run_gh, run_git

logger = get_logger(__name__)


def _detect_remote(cwd: str) -> str:
    """Detect preferred remote: upstream (non-file) or origin."""
    result = run_git(["remote", "get-url", "upstream"], cwd=cwd)
    if result.returncode == 0 and not result.stdout.strip().startswith("file://"):
        return "upstream"
    return "origin"


def queue_ejected_fix(
    work_dir: str,
    base_branch: str,
) -> dict[str, str]:
    """Fetch and rebase onto base branch; return clean or conflicts."""
    remote = _detect_remote(work_dir)
    fetch = run_git(["fetch", remote, base_branch], cwd=work_dir)
    if fetch.returncode != 0:
        return {"status": "conflicts"}
    rebase = run_git(["rebase", f"{remote}/{base_branch}"], cwd=work_dir)
    if rebase.returncode == 0:
        return {"status": "clean"}
    run_git(["rebase", "--abort"], cwd=work_dir)
    return {"status": "conflicts"}


def direct_merge_conflict_fix(
    work_dir: str,
    base_branch: str,
) -> dict[str, str]:
    """Attempt rebase for direct-merge path; return clean or conflicts."""
    return queue_ejected_fix(work_dir=work_dir, base_branch=base_branch)


def immediate_merge_conflict_fix(
    work_dir: str,
    base_branch: str,
) -> dict[str, str]:
    """Attempt rebase for immediate-merge path; return clean or conflicts."""
    return queue_ejected_fix(work_dir=work_dir, base_branch=base_branch)


def wait_for_direct_merge(
    pr_number: str,
    max_polls: str = "90",
    poll_interval: str = "10",
) -> dict[str, str]:
    """Poll PR state until merged/closed/timeout."""

    max_polls = max_polls or "90"
    poll_interval = poll_interval or "10"
    for _ in range(int(max_polls)):
        result = run_gh(["pr", "view", str(pr_number), "--json", "state", "--jq", ".state"])
        if result.returncode != 0:
            time.sleep(int(poll_interval))
            continue
        state = result.stdout.strip()
        if state == "MERGED":
            return {"state": "merged"}
        if state == "CLOSED":
            return {"state": "closed"}
        time.sleep(int(poll_interval))
    return {"state": "timeout"}


def wait_for_immediate_merge(
    pr_number: str,
    max_polls: str = "30",
    poll_interval: str = "10",
) -> dict[str, str]:
    """Poll PR state until merged/closed/timeout (shorter)."""
    return wait_for_direct_merge(
        pr_number=pr_number, max_polls=max_polls, poll_interval=poll_interval
    )


def attempt_cheap_rebase(
    work_dir: str,
    ejected_pr_branch: str,
    base_branch: str,
) -> dict[str, str]:
    """Checkout ejected branch and attempt rebase."""
    remote = _detect_remote(work_dir)
    run_git(["fetch", remote, ejected_pr_branch], cwd=work_dir, check=True)
    run_git(["checkout", ejected_pr_branch], cwd=work_dir, check=True)
    rebase = run_git(["rebase", f"{remote}/{base_branch}"], cwd=work_dir)
    if rebase.returncode == 0:
        return {"status": "clean"}
    run_git(["rebase", "--abort"], cwd=work_dir)
    return {"status": "conflicts"}


def wait_for_review_pr_mergeability(
    pr_url: str,
    max_polls: str = "12",
    poll_interval: str = "15",
) -> dict[str, str]:
    """Extract PR number and poll until mergeability resolves."""

    max_polls = max_polls or "12"
    poll_interval = poll_interval or "15"
    result = run_gh(["pr", "view", pr_url, "--json", "number", "-q", ".number"])
    if result.returncode != 0:
        msg = f"failed to resolve PR number: {result.stderr}"
        raise RuntimeError(msg)
    pr_number = result.stdout.strip()
    for _ in range(int(max_polls)):
        r = run_gh(["pr", "view", pr_number, "--json", "mergeable", "-q", ".mergeable"])
        if r.returncode != 0:
            time.sleep(int(poll_interval))
            continue
        status = r.stdout.strip()
        if status != "UNKNOWN":
            return {"pr_number": pr_number}
        time.sleep(int(poll_interval))
    msg = "Timed out waiting for mergeability"
    raise RuntimeError(msg)


def create_persistent_integration(
    work_dir: str,
    base_branch: str,
) -> dict[str, str]:
    """Create and push persistent integration branch from default branch."""
    remote = _detect_remote(work_dir)
    result = run_git(["symbolic-ref", "refs/remotes/origin/HEAD"], cwd=work_dir)
    default_branch = "main"
    if result.returncode == 0:
        ref = result.stdout.strip()
        default_branch = ref.replace("refs/remotes/origin/", "")
    run_git(["checkout", default_branch], cwd=work_dir, check=True)
    run_git(["pull"], cwd=work_dir, check=True)
    run_git(["checkout", "-b", base_branch], cwd=work_dir, check=True)
    push = run_git(["push", remote, base_branch], cwd=work_dir)
    if push.returncode != 0:
        msg = f"push failed: {push.stderr}"
        raise RuntimeError(msg)
    return {"ok": "true"}


def force_push_and_wait_mergeability(
    work_dir: str,
    batch_branch: str,
    review_pr_number: str,
    max_polls: str = "12",
    poll_interval: str = "15",
) -> dict[str, str]:
    """Force-push integration branch and wait for mergeability."""

    max_polls = max_polls or "12"
    poll_interval = poll_interval or "15"
    remote = _detect_remote(work_dir)
    push = run_git(["push", remote, batch_branch, "--force-with-lease"], cwd=work_dir)
    if push.returncode != 0:
        msg = f"force-push failed: {push.stderr}"
        raise RuntimeError(msg)
    for _ in range(int(max_polls)):
        r = run_gh(
            ["pr", "view", str(review_pr_number), "--json", "mergeable", "-q", ".mergeable"],
            cwd=work_dir,
        )
        if r.returncode != 0:
            time.sleep(int(poll_interval))
            continue
        status = r.stdout.strip()
        if status != "UNKNOWN":
            return {"ok": "true"}
        time.sleep(int(poll_interval))
    msg = "Timed out waiting for post-rebase mergeability"
    raise RuntimeError(msg)


def advance_queue_pr(
    current_pr_number: str,
    pr_order_file: str,
) -> dict[str, str]:
    """Find next PR in queue order file."""
    if not current_pr_number:
        return {"error": f"current_pr_number is required, got {current_pr_number!r}"}
    try:
        with open(pr_order_file) as f:
            order = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(str(exc)) from exc
    current = int(current_pr_number)
    idx = None
    for i, entry in enumerate(order):
        if entry.get("number") == current:
            idx = i
            break
    if idx is None:
        return {"current_pr_number": "done"}
    if (idx + 1) < len(order):
        return {"current_pr_number": str(order[idx + 1]["number"])}
    return {"current_pr_number": "done"}


def proactive_rebase_next_pr(
    work_dir: str,
    next_pr_branch: str,
    base_branch: str,
) -> dict[str, str]:
    """Fetch, checkout, and rebase next PR branch."""
    remote = _detect_remote(work_dir)
    run_git(["fetch", remote, next_pr_branch], cwd=work_dir, check=True)
    run_git(
        ["checkout", "-B", next_pr_branch, f"{remote}/{next_pr_branch}"], cwd=work_dir, check=True
    )
    rebase = run_git(["rebase", f"{remote}/{base_branch}"], cwd=work_dir)
    if rebase.returncode == 0:
        return {"status": "clean"}
    run_git(["rebase", "--abort"], cwd=work_dir)
    return {"status": "conflicts"}
