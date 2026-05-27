"""Recipe cmd externalization issues — issue creation, bundles, audit run dirs."""

from __future__ import annotations

import json
import secrets
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

import regex as re

from autoskillit.core import atomic_write, get_logger, run_gh

logger = get_logger(__name__)


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
    result = run_gh(
        [
            "api",
            "graphql",
            "-f",
            f"query={query}",
            "--jq",
            '[.data[] | select(.issue != null and .issue.state == "OPEN") '
            '| .issue.number | tostring] | join(" ")',
        ]
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
        "deferred_groups": [],
        "deferred_merge_order": [],
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
        if re.search(r"\*\*Contested:\*\*\s+\d+", line) or re.search(
            r"\*\*Exception warranted:\*\*\s+\d+", line
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
    result = run_gh(
        ["repo", "view", "--json", "owner,name", "-q", '.owner.login + " " + .name'], cwd=cwd
    )
    if result.returncode != 0:
        msg = f"gh repo view failed: {result.stderr}"
        raise RuntimeError(msg)
    parts = result.stdout.strip().split()
    if len(parts) < 2:
        msg = f"Unexpected gh repo view output: {result.stdout!r}"
        raise RuntimeError(msg)
    owner, repo_name = parts[0], parts[1]
    safe_owner = json.dumps(owner)[1:-1]
    safe_repo = json.dumps(repo_name)[1:-1]
    query = f'{{ repository(owner: "{safe_owner}", name: "{safe_repo}") {{ id }} }}'
    result = run_gh(["api", "graphql", "-f", f"query={query}"], cwd=cwd)
    if result.returncode != 0:
        msg = f"gh graphql repo ID query failed: {result.stderr}"
        raise RuntimeError(msg)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        msg = f"gh graphql repo ID: non-JSON output: {result.stdout!r}"
        raise RuntimeError(msg) from exc
    if "errors" in data:
        raise RuntimeError(f"gh graphql repo ID errors: {data['errors']}")
    node_id = data["data"]["repository"]["id"]
    return owner, repo_name, node_id


def _ensure_and_resolve_labels(cwd: str, owner: str, repo_name: str) -> list[str]:
    """Create labels if absent, resolve and return their node IDs."""
    label_defs = [
        ("recipe:implementation", "0E8A16"),
        ("enhancement", "a2eeef"),
    ]
    for name, color in label_defs:
        run_gh(["label", "create", name, "--force", "--color", color], cwd=cwd)
        time.sleep(1)
    safe_owner = json.dumps(owner)[1:-1]
    safe_repo = json.dumps(repo_name)[1:-1]
    query = (
        f'{{ repository(owner: "{safe_owner}", name: "{safe_repo}") {{'
        f' impl: label(name: "recipe:implementation") {{ id }}'
        f' enh: label(name: "enhancement") {{ id }} }} }}'
    )
    result = run_gh(["api", "graphql", "-f", f"query={query}"], cwd=cwd)
    if result.returncode != 0:
        msg = f"gh graphql label query failed: {result.stderr}"
        raise RuntimeError(msg)
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        msg = f"gh graphql label query: non-JSON output: {result.stdout!r}"
        raise RuntimeError(msg) from exc
    if "errors" in data:
        raise RuntimeError(f"gh graphql label query errors: {data['errors']}")
    repo = data["data"]["repository"]
    if repo["impl"] is None or repo["enh"] is None:
        raise RuntimeError(
            f"Label resolution returned null: impl={repo['impl']!r} enh={repo['enh']!r}"
        )
    return [repo["impl"]["id"], repo["enh"]["id"]]


def create_audit_run_dir(temp_dir: str) -> dict[str, str]:
    """Create a unique per-run directory for validate-audit outputs.

    Follows the same pattern as ``create_run_dir`` in planner/manifests.py:
    creates ``{temp_dir}/validate-audit/run-{stamp}-{hex}/`` so that each
    pipeline invocation writes to an isolated subdirectory, preventing cross-run
    file accumulation in the flat ``validate-audit/`` namespace.
    """
    if not temp_dir:
        raise ValueError("temp_dir must be a non-empty path")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    run_dir = Path(temp_dir) / "validate-audit" / f"run-{stamp}-{secrets.token_hex(4)}"
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ValueError(f"cannot create audit run directory {run_dir}: {exc}") from exc
    return {"audit_run_dir": str(run_dir)}


def batch_create_issues(
    workspace: str,
    chunk_size: str = "20",
    timeout: int = 120,
    audit_run_dir: str = "",
) -> dict[str, str]:
    """Batch-create GitHub issues from validated ticket body files via GraphQL.

    The ``audit_run_dir`` parameter scopes file discovery to a per-run directory
    (created by ``create_audit_run_dir``). When provided, only ticket body files
    within that directory are processed. When empty, falls back to the
    workspace-derived path for direct CLI invocation compatibility.
    """
    if not workspace or not Path(workspace).is_dir():
        raise ValueError(f"workspace must be an existing directory, got: {workspace!r}")
    if audit_run_dir:
        temp_dir = Path(audit_run_dir)
        if not temp_dir.is_dir():
            raise ValueError(
                f"audit_run_dir must be an existing directory, got: {audit_run_dir!r}"
            )
    else:
        temp_dir = Path(workspace) / ".autoskillit" / "temp" / "validate-audit"
    ticket_bodies = sorted(temp_dir.glob("ticket_body_*.md"))
    if not ticket_bodies:
        return {"issue_urls": "", "issue_count": "0"}

    parsed: list[tuple[str, str, str]] = []
    for f in ticket_bodies:
        raw = f.read_text()
        m = re.match(r"ticket_body_\w+_\d+_(.+)\.md", f.name)
        ts = m.group(1) if m else ""
        title = _extract_title(raw)
        body = _strip_ticket_body(raw)
        parsed.append((title, body, ts))

    owner, repo_name, repo_id = _resolve_repo_identity(workspace)
    label_ids = _ensure_and_resolve_labels(workspace, owner, repo_name)

    all_urls: list[str] = []
    try:
        chunk_sz = int(chunk_size) if chunk_size else 20
    except ValueError as exc:
        raise ValueError(f"chunk_size must be a positive integer, got: {chunk_size!r}") from exc
    if chunk_sz <= 0:
        raise ValueError(f"chunk_size must be positive, got: {chunk_sz}")
    for offset in range(0, len(parsed), chunk_sz):
        chunk = parsed[offset : offset + chunk_sz]
        mutation_parts = []
        variables: dict[str, object] = {}
        for idx, (title, body, _) in enumerate(chunk):
            alias = f"issue{idx}"
            mutation_parts.append(
                f"{alias}: createIssue(input: \$i{idx}) {{ issue {{ number url }} }}"
            )
            variables[f"i{idx}"] = {
                "repositoryId": repo_id,
                "title": title,
                "body": body,
                "labelIds": label_ids,
            }
        mutation = (
            "mutation("
            + ",".join(f"\$i{k}: CreateIssueInput!" for k in range(len(chunk)))
            + ") {"
            + " ".join(mutation_parts)
            + "}"
        )
        payload = json.dumps({"query": mutation, "variables": variables})
        result = run_gh(["api", "graphql", "--input", "-"], cwd=workspace, input_data=payload)
        if result.returncode != 0:
            msg = f"gh graphql createIssue failed: {result.stderr}"
            raise RuntimeError(msg)
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            msg = f"gh graphql createIssue: non-JSON output: {result.stdout!r}"
            raise RuntimeError(msg) from exc
        if "errors" in data:
            raise RuntimeError(f"gh graphql createIssue errors: {data['errors']}")
        resp_data = data.get("data") or {}
        for idx in range(len(chunk)):
            alias = f"issue{idx}"
            alias_result = resp_data.get(alias)
            if alias_result is None:
                raise RuntimeError(f"createIssue response missing alias {alias!r}: {data}")
            issue_data = alias_result.get("issue")
            if issue_data is None:
                raise RuntimeError(f"createIssue alias {alias!r} returned null issue: {data}")
            all_urls.append(issue_data["url"])
        if offset + chunk_sz < len(parsed):
            time.sleep(1)

    return {"issue_urls": ",".join(all_urls), "issue_count": str(len(all_urls))}
