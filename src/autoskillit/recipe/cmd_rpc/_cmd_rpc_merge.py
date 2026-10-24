"""Recipe cmd externalization merge — rebase, PR polling, branch management."""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from autoskillit.core import (
    atomic_write,
    get_logger,
    load_pinned_plan_set_authority,
    run_gh,
    run_git,
    truncate_text,
)

logger = get_logger(__name__)


def verify_stage_obligations(
    plan_set_authority_path: str,
    plan_set_authority_digest: str,
    cwd: str,
    base_branch: str,
    pr_number: str = "",
) -> dict[str, str]:
    """Discharge sealed publication obligations against an observed PR."""
    verified = load_pinned_plan_set_authority(
        plan_set_authority_path, expected_digest=plan_set_authority_digest
    )
    if not verified.accepted or verified.evidence is None:
        reason = verified.reason.value if verified.reason else "authority rejected"
        return {"success": "false", "detail": "; ".join((reason, *verified.detail))}
    obligations = verified.evidence.stage_obligations
    if not obligations:
        return {"success": "true", "detail": "no stage obligations"}
    if not pr_number:
        required = ", ".join(f"{o.requirement_id} → {o.obligation.value}" for o in obligations)
        return {
            "success": "false",
            "detail": f"no pull request was published in this run; required: {required}",
        }
    pr_state, pr_base = _observe_pull_request(str(pr_number), cwd)
    violations = []
    for obligation in obligations:
        violation = obligation.obligation.violation(
            pr_state=pr_state, pr_base=pr_base, base_branch=base_branch
        )
        if violation:
            violations.append(
                f"{obligation.requirement_id} → {obligation.obligation.value}: {violation}"
            )
    return {
        "success": "false" if violations else "true",
        "detail": "; ".join(violations) if violations else "stage obligations verified",
    }


def _observe_pull_request(pr_number: str, cwd: str) -> tuple[str | None, str | None]:
    """Read a complete PR observation, retrying transient command and payload failures."""
    for attempt in range(3):
        try:
            result = run_gh(
                ["pr", "view", str(pr_number), "--json", "state,baseRefName"],
                cwd=cwd,
                timeout=30,
            )
            if result.returncode == 0:
                observed = json.loads(result.stdout)
                if (
                    isinstance(observed, dict)
                    and isinstance(observed.get("state"), str)
                    and isinstance(observed.get("baseRefName"), str)
                ):
                    return observed["state"], observed["baseRefName"]
        except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
            logger.debug(
                "stage_obligation_pr_observation_failed",
                pr_number=pr_number,
                attempt=attempt + 1,
                error=str(exc),
            )
        if attempt < 2:
            time.sleep(5)
    return None, None


def _detect_remote(cwd: str) -> str:
    """Detect preferred remote: upstream (non-file) or origin."""
    result = run_git(["remote", "get-url", "upstream"], cwd=cwd)
    if result.returncode == 0 and not result.stdout.strip().startswith("file://"):
        return "upstream"
    return "origin"


def _write_rebase_conflict_report(
    *, work_dir: str, conflict_report_path: str, rebase_stderr: str
) -> None:
    """Publish bounded rebase evidence before the rebase is aborted."""
    unmerged = run_git(["diff", "--name-only", "--diff-filter=U"], cwd=work_dir)
    bounded_stderr = truncate_text(rebase_stderr)
    stderr_prefix = rebase_stderr[:120]
    if stderr_prefix and not bounded_stderr.startswith(stderr_prefix):
        bounded_stderr = f"{stderr_prefix}\n{bounded_stderr}"
    report = (
        "# Rebase Conflict\n\n"
        "## Unmerged files\n\n"
        f"{truncate_text(unmerged.stdout)}\n\n"
        "## Rebase stderr\n\n"
        f"{bounded_stderr}\n"
    )
    atomic_write(Path(conflict_report_path), report)


def queue_ejected_fix(
    work_dir: str,
    base_branch: str,
) -> dict[str, str]:
    """Fetch and rebase onto base branch; return clean or conflicts."""
    remote = _detect_remote(work_dir)
    fetch = run_git(["fetch", remote, base_branch], cwd=work_dir)
    if fetch.returncode != 0:
        return {"status": "fetch_error", "stderr": fetch.stderr}
    rebase = run_git(["rebase", f"{remote}/{base_branch}"], cwd=work_dir)
    if rebase.returncode == 0:
        return {"status": "clean"}
    run_git(["rebase", "--abort"], cwd=work_dir)
    return {"status": "conflicts", "stderr": rebase.stderr}


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
    conflict_report_path: str,
) -> dict[str, str]:
    """Checkout ejected branch and attempt rebase."""
    remote = _detect_remote(work_dir)
    return _attempt_rebase_with_conflict_report(
        work_dir=work_dir,
        branch=ejected_pr_branch,
        base_branch=base_branch,
        checkout_args=[ejected_pr_branch],
        remote=remote,
        conflict_report_path=conflict_report_path,
    )


def _attempt_rebase_with_conflict_report(
    *,
    work_dir: str,
    branch: str,
    base_branch: str,
    checkout_args: list[str],
    remote: str,
    conflict_report_path: str,
) -> dict[str, str]:
    """Fetch, checkout, rebase; on conflict write a report and abort the rebase."""
    run_git(["fetch", remote, branch], cwd=work_dir, check=True)
    fetch = run_git(["fetch", remote, base_branch], cwd=work_dir)
    if fetch.returncode != 0:
        return {"status": "fetch_error", "stderr": fetch.stderr}
    run_git(["checkout", *checkout_args], cwd=work_dir, check=True)
    rebase = run_git(["rebase", f"{remote}/{base_branch}"], cwd=work_dir)
    if rebase.returncode == 0:
        return {"status": "clean"}
    try:
        _write_rebase_conflict_report(
            work_dir=work_dir,
            conflict_report_path=conflict_report_path,
            rebase_stderr=rebase.stderr,
        )
    finally:
        run_git(["rebase", "--abort"], cwd=work_dir)
    return {
        "status": "conflicts",
        "conflict_report_path": conflict_report_path,
    }


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
    result = run_git(["symbolic-ref", f"refs/remotes/{remote}/HEAD"], cwd=work_dir)
    default_branch = "main"
    if result.returncode == 0:
        ref = result.stdout.strip()
        default_branch = ref.removeprefix(f"refs/remotes/{remote}/")
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


_PR_COMPLEXITIES = frozenset({"simple", "needs_check"})


def _load_pr_order(pr_order_file: str) -> list[tuple[str, str]]:
    if not Path(pr_order_file).is_absolute():
        raise ValueError(f"pr_order_file must be absolute: {pr_order_file!r}")
    try:
        with open(pr_order_file) as f:
            order = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"{pr_order_file}: {exc}") from exc
    if not isinstance(order, dict) or not isinstance(order.get("prs"), list):
        raise ValueError(f"{pr_order_file}: expected an object with a prs list")
    entries: list[tuple[str, str]] = []
    seen: set[int] = set()
    for index, entry in enumerate(order["prs"]):
        location = f"{pr_order_file}: prs[{index}]"
        if not isinstance(entry, dict):
            raise ValueError(f"{location}: expected an object")
        number, complexity = entry.get("number"), entry.get("complexity")
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise ValueError(f"{location}: number must be a positive integer")
        if not isinstance(complexity, str) or complexity not in _PR_COMPLEXITIES:
            raise ValueError(f"{location}: complexity must be simple or needs_check")
        if number in seen:
            raise ValueError(f"{location}: duplicate PR number {number}")
        seen.add(number)
        entries.append((str(number), complexity))
    return entries


def _cursor_result(entry: tuple[str, str], *, done: bool) -> dict[str, str]:
    return {"pr_number": entry[0], "complexity": entry[1], "done": "true" if done else "false"}


def first_ordered_pr(pr_order_file: str) -> dict[str, str]:
    """Select the first eligible PR from the analyze-prs order artifact."""
    entries = _load_pr_order(pr_order_file)
    return _cursor_result(entries[0] if entries else ("", ""), done=not entries)


def next_ordered_pr(pr_order_file: str, current_pr_number: str) -> dict[str, str]:
    """Advance by file order, retaining the last PR when the order is exhausted."""
    entries = _load_pr_order(pr_order_file)
    for index, entry in enumerate(entries):
        if entry[0] == current_pr_number:
            done = index + 1 == len(entries)
            return _cursor_result(entry if done else entries[index + 1], done=done)
    raise ValueError(f"{pr_order_file}: current_pr_number {current_pr_number!r} is not in prs")


def review_path_rebase(
    work_dir: str,
    base_branch: str,
) -> dict[str, str]:
    """Fetch and rebase for review/resolve paths; return clean or conflicts."""
    return queue_ejected_fix(work_dir=work_dir, base_branch=base_branch)


def proactive_rebase_next_pr(
    work_dir: str,
    next_pr_branch: str,
    base_branch: str,
    conflict_report_path: str,
) -> dict[str, str]:
    """Fetch, checkout, and rebase next PR branch."""
    remote = _detect_remote(work_dir)
    return _attempt_rebase_with_conflict_report(
        work_dir=work_dir,
        branch=next_pr_branch,
        base_branch=base_branch,
        checkout_args=["-B", next_pr_branch, f"{remote}/{next_pr_branch}"],
        remote=remote,
        conflict_report_path=conflict_report_path,
    )
