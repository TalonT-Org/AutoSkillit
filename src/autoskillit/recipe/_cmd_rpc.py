"""Recipe cmd externalization callables — run_python entry points (IL-006)."""

from __future__ import annotations

import json
import re
import subprocess
import time
from datetime import date
from pathlib import Path

from autoskillit.core import atomic_write


def compute_branch(
    issue_slug: str = "",
    run_name: str = "",
    issue_number: str = "",
) -> dict[str, str]:
    """Compute branch name from slug + issue or date."""
    prefix = issue_slug or run_name
    if issue_number:
        return {"branch_name": f"{prefix}/{issue_number}"}
    return {"branch_name": f"{prefix}/{date.today().strftime('%Y%m%d')}"}


def check_eject_limit(
    counter_file: str,
    max_ejects: str = "3",
) -> dict[str, str]:
    """Increment counter file; return EJECT_OK or EJECT_LIMIT_EXCEEDED."""
    max_ejects = max_ejects or "3"
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
    """Increment dropped-healthy counter; return DROPPED_OK or DROPPED_LIMIT_EXCEEDED."""
    max_drops = max_drops or "2"
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
    """Auto-commit pending changes if worktree is dirty."""
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
    """Fetch and rebase onto base branch; return clean or conflicts."""
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
    """Extract PR number and poll until mergeability resolves."""

    max_polls = max_polls or "12"
    poll_interval = poll_interval or "15"
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
    """Create and push persistent integration branch from default branch."""
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
    batch_branch: str,
    review_pr_number: str,
    max_polls: str = "12",
    poll_interval: str = "15",
) -> dict[str, str]:
    """Force-push integration branch and wait for mergeability."""

    max_polls = max_polls or "12"
    poll_interval = poll_interval or "15"
    remote = _detect_remote(work_dir)
    push = subprocess.run(
        ["git", "push", remote, batch_branch, "--force-with-lease"],
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
    """Build GraphQL query from issue URLs, fetch open issues."""
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
            (
                '[.data[] | select(.issue != null and .issue.state == "OPEN")'
                ' | .issue.number | tostring] | join(" ")'
            ),
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
    """Build fallback execution map JSON from issue URLs."""
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
    """Ensure experiment_results file exists; create placeholder if empty."""
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
    """Copy research dir to source_dir/research-bundles/{slug}/."""
    import shutil  # noqa: PLC0415

    local_root = Path(source_dir) / "research-bundles"
    local_root.mkdir(parents=True, exist_ok=True)
    slug = Path(research_dir).name
    dest = local_root / slug
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(research_dir, dest)
    return {"local_bundle_path": str(dest)}


# ─── batch_create_issues helpers ────────────────────────────────────────────


def _extract_title(raw: str) -> str:
    """Return the text following '# ' from the first H1 line, or a fallback."""
    m = re.search(r"^#\s+(.+)$", raw, re.MULTILINE)
    return m.group(1).strip() if m else "Untitled audit finding"


def _strip_ticket_body(raw: str) -> str:
    """Remove internal metadata and exception details from a ticket body."""
    lines = raw.splitlines()
    result: list[str] = []
    skip_exceptions_section = False
    for line in lines:
        if line.strip().startswith("validated: true"):
            continue
        if ".autoskillit/" in line:
            continue
        if "contested_findings_" in line:
            continue
        if "| CONTESTED |" in line or "| VALID BUT EXCEPTION WARRANTED |" in line:
            continue
        if re.search(r"\*\*Contested:\*\s*\d+", line) or re.search(
            r"\*\*Exception warranted:\*\s*\d+", line
        ):
            continue
        if "**Exception note:**" in line:
            continue
        if re.match(r"## Findings with Exceptions\s*$", line):
            skip_exceptions_section = True
            continue
        if skip_exceptions_section:
            if line.strip().startswith("---"):
                skip_exceptions_section = False
            continue
        result.append(line)
    return "\n".join(result)


def _resolve_repo_identity(cwd: str) -> tuple[str, str, str]:
    """Return (owner, repo_name, repo_node_id) for the given workspace."""
    result = subprocess.run(
        ["gh", "repo", "view", "--json", "owner,name", "-q", '.owner.login + " " + .name'],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        msg = f"gh repo view failed: {result.stderr}"
        raise RuntimeError(msg)
    parts = result.stdout.strip().split()
    owner, repo_name = parts[0], parts[1]
    query = '{ repository(owner: "%s", name: "%s") { id } }' % (owner, repo_name)
    result = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        msg = f"gh graphql repo ID query failed: {result.stderr}"
        raise RuntimeError(msg)
    data = json.loads(result.stdout)
    node_id = data["data"]["repository"]["id"]
    return owner, repo_name, node_id


def _ensure_and_resolve_labels(cwd: str, owner: str, repo_name: str) -> list[str]:
    """Create labels if absent, resolve and return their node IDs."""
    label_defs = [
        ("recipe:implementation", "0E8A16"),
        ("enhancement", "a2eeef"),
    ]
    for name, color in label_defs:
        subprocess.run(
            ["gh", "label", "create", name, "--force", "--color", color],
            cwd=cwd,
            capture_output=True,
        )
        time.sleep(1)
    query = (
        '{ repository(owner: "%s", name: "%s") { '
        'impl: label(name: "recipe:implementation") { id } '
        'enh: label(name: "enhancement") { id } } }' % (owner, repo_name)
    )
    result = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"],
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        msg = f"gh graphql label query failed: {result.stderr}"
        raise RuntimeError(msg)
    data = json.loads(result.stdout)
    repo = data["data"]["repository"]
    return [repo["impl"]["id"], repo["enh"]["id"]]


def batch_create_issues(
    workspace: str,
    chunk_size: str = "20",
) -> dict[str, str]:
    """Batch-create GitHub issues from validated ticket body files via GraphQL."""
    temp_dir = Path(workspace) / ".autoskillit" / "temp" / "validate-audit"
    ticket_bodies = sorted(temp_dir.glob("ticket_body_*.md"))
    if not ticket_bodies:
        return {"issue_urls": "", "issue_count": "0"}

    parsed: list[tuple[str, str, str]] = []
    for f in ticket_bodies:
        raw = f.read_text()
        m = re.match(r"ticket_body_(\w+)_\d+_(.+)\.md", f.name)
        source = m.group(1) if m else "unknown"
        ts = m.group(2) if m else ""
        title = _extract_title(raw)
        body = _strip_ticket_body(raw)
        summary_path = temp_dir / f"validation_summary_{source}_{ts}.md"
        if summary_path.exists():
            body += "\n\n---\n\n" + summary_path.read_text()
        parsed.append((title, body, ts))

    owner, repo_name, repo_id = _resolve_repo_identity(workspace)
    label_ids = _ensure_and_resolve_labels(workspace, owner, repo_name)

    all_urls: list[str] = []
    chunk_sz = int(chunk_size) if chunk_size else 20
    for offset in range(0, len(parsed), chunk_sz):
        chunk = parsed[offset : offset + chunk_sz]
        mutation_parts = []
        variables: dict[str, object] = {"repoId": repo_id, "labelIds": label_ids}
        for idx, (title, body, _) in enumerate(chunk):
            alias = f"issue{idx}"
            mutation_parts.append(
                f"{alias}: createIssue(input: $i{idx}) {{ issue {{ number url }} }}"
            )
            variables[f"i{idx}"] = {
                "repositoryId": repo_id,
                "title": title,
                "body": body,
                "labelIds": label_ids,
            }
        mutation = (
            "mutation("
            + ",".join(f"$i{k}: CreateIssueInput!" for k in range(len(chunk)))
            + ") {"
            + " ".join(mutation_parts)
            + "}"
        )
        payload = json.dumps({"query": mutation, "variables": variables})
        result = subprocess.run(
            ["gh", "api", "graphql", "--input", "-"],
            input=payload,
            cwd=workspace,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            msg = f"gh graphql createIssue failed: {result.stderr}"
            raise RuntimeError(msg)
        data = json.loads(result.stdout)
        for idx in range(len(chunk)):
            alias = f"issue{idx}"
            issue_data = data["data"][alias]["issue"]
            all_urls.append(issue_data["url"])
        if offset + chunk_sz < len(parsed):
            time.sleep(1)

    return {"issue_urls": ",".join(all_urls), "issue_count": str(len(all_urls))}
