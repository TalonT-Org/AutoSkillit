"""Recipe cmd externalization callables — run_python entry points (IL-006)."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import date
from pathlib import Path

from autoskillit.core import atomic_write


def compute_branch(
    issue_slug: str = "",
    run_name: str = "",
    issue_number: str = "",
) -> dict[str, str]:
    """Compute branch name from slug + issue or date. Callable via run_python."""
    prefix = issue_slug or run_name
    if issue_number:
        return {"branch_name": f"{prefix}/{issue_number}"}
    return {"branch_name": f"{prefix}/{date.today().strftime('%Y%m%d')}"}


def check_eject_limit(
    counter_file: str,
    max_ejects: str = "3",
) -> dict[str, str]:
    """Increment counter file; return EJECT_OK or EJECT_LIMIT_EXCEEDED. Callable via run_python."""
    path = Path(counter_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        count = int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        count = 0
    count += 1
    atomic_write(path, str(count))
    status = "EJECT_LIMIT_EXCEEDED" if count > int(max_ejects) else "EJECT_OK"
    return {"status": status, "count": str(count)}


def check_dropped_healthy_loop(
    counter_file: str,
    max_drops: str = "2",
) -> dict[str, str]:
    """Increment dropped-healthy counter; return DROPPED_OK or DROPPED_LIMIT_EXCEEDED. Callable via run_python."""
    path = Path(counter_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        count = int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        count = 0
    count += 1
    atomic_write(path, str(count))
    status = "DROPPED_LIMIT_EXCEEDED" if count > int(max_drops) else "DROPPED_OK"
    return {"status": status, "count": str(count)}


def commit_guard(worktree_path: str) -> dict[str, str]:
    """Auto-commit pending changes if worktree is dirty. Callable via run_python."""
    result = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        subprocess.run(["git", "add", "-A"], cwd=worktree_path, check=True)
        subprocess.run(
            ["git", "commit", "-m", "chore: commit pending session changes"],
            cwd=worktree_path,
            check=True,
        )
        return {"committed": "true"}
    return {"committed": "false"}


def _detect_remote(cwd: str) -> str:
    """Detect preferred remote: upstream (non-file) or origin."""
    result = subprocess.run(
        ["git", "remote", "get-url", "upstream"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode == 0 and not result.stdout.strip().startswith("file://"):
        return "upstream"
    return "origin"


def queue_ejected_fix(
    work_dir: str,
    base_branch: str,
) -> dict[str, str]:
    """Fetch and rebase onto base branch; return clean or conflicts. Callable via run_python."""
    remote = _detect_remote(work_dir)
    fetch = subprocess.run(
        ["git", "fetch", remote, base_branch],
        cwd=work_dir,
        capture_output=True,
        text=True,
    )
    if fetch.returncode != 0:
        return {"status": "conflicts"}
    rebase = subprocess.run(
        ["git", "rebase", f"{remote}/{base_branch}"],
        cwd=work_dir,
        capture_output=True,
        text=True,
    )
    if rebase.returncode == 0:
        return {"status": "clean"}
    subprocess.run(
        ["git", "rebase", "--abort"],
        cwd=work_dir,
        capture_output=True,
    )
    return {"status": "conflicts"}


def direct_merge_conflict_fix(
    work_dir: str,
    base_branch: str,
) -> dict[str, str]:
    """Attempt rebase for direct-merge path; return clean or conflicts. Callable via run_python."""
    return queue_ejected_fix(work_dir=work_dir, base_branch=base_branch)


def immediate_merge_conflict_fix(
    work_dir: str,
    base_branch: str,
) -> dict[str, str]:
    """Attempt rebase for immediate-merge path; return clean or conflicts. Callable via run_python."""
    return queue_ejected_fix(work_dir=work_dir, base_branch=base_branch)


def wait_for_direct_merge(
    pr_number: str,
    max_polls: str = "90",
    poll_interval: str = "10",
) -> dict[str, str]:
    """Poll PR state until merged/closed/timeout. Callable via run_python."""
    import time  # noqa: PLC0415

    for _ in range(int(max_polls)):
        result = subprocess.run(
            ["gh", "pr", "view", pr_number, "--json", "state", "--jq", ".state"],
            capture_output=True,
            text=True,
        )
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
    """Poll PR state until merged/closed/timeout (shorter). Callable via run_python."""
    return wait_for_direct_merge(
        pr_number=pr_number, max_polls=max_polls, poll_interval=poll_interval
    )


def attempt_cheap_rebase(
    work_dir: str,
    ejected_pr_branch: str,
    base_branch: str,
) -> dict[str, str]:
    """Checkout ejected branch and attempt rebase. Callable via run_python."""
    remote = _detect_remote(work_dir)
    subprocess.run(
        ["git", "fetch", remote, ejected_pr_branch],
        cwd=work_dir,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "checkout", ejected_pr_branch],
        cwd=work_dir,
        capture_output=True,
        check=True,
    )
    rebase = subprocess.run(
        ["git", "rebase", f"{remote}/{base_branch}"],
        cwd=work_dir,
        capture_output=True,
        text=True,
    )
    if rebase.returncode == 0:
        return {"status": "clean"}
    subprocess.run(
        ["git", "rebase", "--abort"],
        cwd=work_dir,
        capture_output=True,
    )
    return {"status": "conflicts"}


def wait_for_review_pr_mergeability(
    pr_url: str,
    max_polls: str = "12",
    poll_interval: str = "15",
) -> dict[str, str]:
    """Extract PR number and poll until mergeability resolves. Callable via run_python."""
    import time  # noqa: PLC0415

    result = subprocess.run(
        ["gh", "pr", "view", pr_url, "--json", "number", "-q", ".number"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        msg = f"failed to resolve PR number: {result.stderr}"
        raise RuntimeError(msg)
    pr_number = result.stdout.strip()
    for _ in range(int(max_polls)):
        r = subprocess.run(
            ["gh", "pr", "view", pr_number, "--json", "mergeable", "-q", ".mergeable"],
            capture_output=True,
            text=True,
        )
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
    """Create and push persistent integration branch from default branch. Callable via run_python."""
    remote = _detect_remote(work_dir)
    result = subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
        cwd=work_dir,
        capture_output=True,
        text=True,
    )
    default_branch = "main"
    if result.returncode == 0:
        ref = result.stdout.strip()
        default_branch = ref.replace("refs/remotes/origin/", "")
    subprocess.run(
        ["git", "checkout", default_branch], cwd=work_dir, check=True, capture_output=True
    )
    subprocess.run(["git", "pull"], cwd=work_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "checkout", "-b", base_branch], cwd=work_dir, check=True, capture_output=True
    )
    push = subprocess.run(
        ["git", "push", remote, base_branch],
        cwd=work_dir,
        capture_output=True,
        text=True,
    )
    if push.returncode != 0:
        msg = f"push failed: {push.stderr}"
        raise RuntimeError(msg)
    return {"ok": "true"}


def force_push_and_wait_mergeability(
    work_dir: str,
    integration_branch: str,
    review_pr_number: str,
    max_polls: str = "12",
    poll_interval: str = "15",
) -> dict[str, str]:
    """Force-push integration branch and wait for mergeability. Callable via run_python."""
    import time  # noqa: PLC0415

    remote = _detect_remote(work_dir)
    push = subprocess.run(
        ["git", "push", remote, integration_branch, "--force-with-lease"],
        cwd=work_dir,
        capture_output=True,
        text=True,
    )
    if push.returncode != 0:
        msg = f"force-push failed: {push.stderr}"
        raise RuntimeError(msg)
    for _ in range(int(max_polls)):
        r = subprocess.run(
            ["gh", "pr", "view", review_pr_number, "--json", "mergeable", "-q", ".mergeable"],
            capture_output=True,
            text=True,
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
    """Find next PR in queue order file via jq. Callable via run_python."""
    try:
        with open(pr_order_file) as f:
            order = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        return {"ok": "false", "error": str(exc)}
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
    """Fetch, checkout, and rebase next PR branch. Callable via run_python."""
    remote = _detect_remote(work_dir)
    subprocess.run(
        ["git", "fetch", remote, next_pr_branch],
        cwd=work_dir,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "-B", next_pr_branch, f"{remote}/{next_pr_branch}"],
        cwd=work_dir,
        capture_output=True,
        check=True,
    )
    rebase = subprocess.run(
        ["git", "rebase", f"{remote}/{base_branch}"],
        cwd=work_dir,
        capture_output=True,
        text=True,
    )
    if rebase.returncode == 0:
        return {"status": "clean"}
    subprocess.run(
        ["git", "rebase", "--abort"],
        cwd=work_dir,
        capture_output=True,
    )
    return {"status": "conflicts"}


def refetch_issues(issue_urls: str) -> dict[str, str]:
    """Build GraphQL query from issue URLs, fetch open issues. Callable via run_python."""
    urls = issue_urls.split(",")
    parts = []
    for i, url in enumerate(urls):
        url = url.strip()
        if not url:
            continue
        m = re.match(r"https://github\.com/([^/]+)/([^/]+)/issues/(\d+)", url)
        if m:
            owner, repo, num = m.groups()
            parts.append(
                f'i{i}: repository(owner: "{owner}", name: "{repo}") '
                f"{{ issue(number: {num}) {{ number state }} }}"
            )
    if not parts:
        return {"issue_numbers": ""}
    query = "{" + " ".join(parts) + "}"
    result = subprocess.run(
        [
            "gh",
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "--jq",
            '[.data[] | select(.issue != null and .issue.state == "OPEN") | .issue.number | tostring] | join(" ")',
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        msg = f"gh graphql failed: {result.stderr}"
        raise RuntimeError(msg)
    return {"issue_numbers": result.stdout.strip()}


def emit_fallback_map(
    issue_urls: str,
    temp_dir: str,
) -> dict[str, str]:
    """Build fallback execution map JSON from issue URLs. Callable via run_python."""
    nums: list[int] = []
    for url in issue_urls.split(","):
        m = re.search(r"issues/(\d+)", url.strip())
        if m:
            nums.append(int(m.group(1)))
    if not nums:
        msg = "no issue numbers extracted from issue URLs"
        raise RuntimeError(msg)
    issues = [{"number": n, "title": str(n)} for n in nums]
    data = {
        "groups": [{"group": 1, "parallel": False, "issues": issues}],
        "merge_order": nums,
        "pairwise_assessments": [],
    }
    map_file = Path(temp_dir) / "bem-fallback-map.json"
    map_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(map_file, json.dumps(data))
    return {"execution_map": str(map_file)}


def ensure_results(
    experiment_results: str,
    worktree_path: str,
    temp_subdir: str = ".autoskillit/temp",
) -> dict[str, str]:
    """Ensure experiment_results file exists; create placeholder if empty. Callable via run_python."""
    if experiment_results:
        return {"experiment_results": experiment_results}
    results_path = Path(worktree_path) / temp_subdir / "run-experiment" / "results-inconclusive.md"
    results_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(
        results_path,
        "# Experiment Results\n\n## Status\nINCONCLUSIVE\n\n"
        "Experiment did not produce results — retries exhausted or adjustment failed.\n",
    )
    return {"experiment_results": str(results_path)}


def export_local_bundle(
    source_dir: str,
    research_dir: str,
) -> dict[str, str]:
    """Copy research dir to source_dir/research-bundles/{slug}/. Callable via run_python."""
    import shutil  # noqa: PLC0415

    local_root = Path(source_dir) / "research-bundles"
    local_root.mkdir(parents=True, exist_ok=True)
    slug = Path(research_dir).name
    dest = local_root / slug
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(research_dir, dest)
    return {"local_bundle_path": str(dest)}
