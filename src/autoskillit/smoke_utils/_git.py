"""Git and merge-queue helpers for smoke_utils sub-modules."""

from __future__ import annotations

import json
from pathlib import Path


def check_bug_report_non_empty(workspace: str) -> dict[str, str]:
    """Return {"non_empty": "true"} if bug_report.json exists and is non-empty.

    Called by run_python from the check_summary step in smoke-test.yaml.
    The workspace argument is the root directory initialised by the setup step.
    """
    if not Path(workspace).is_absolute():
        raise ValueError(f"workspace must be absolute, got {workspace!r}")
    report = Path(workspace) / "bug_report.json"
    if not report.exists():
        return {"non_empty": "false"}
    try:
        data = json.loads(report.read_text())
        return {"non_empty": "true" if data else "false"}
    except (json.JSONDecodeError, OSError):
        return {"non_empty": "false"}


def compute_domain_partitions(
    batch_branch: str, base_branch: str, cwd: str, output_dir: str
) -> dict[str, str]:
    """Pre-compute domain partitions for open-integration-pr and write to disk.

    Called by run_python from the compute_domain_partitions step in merge-prs.yaml.
    Runs git diff to get changed files, partitions them by domain, and writes the
    result JSON to output_dir/domain_partitions.json.
    """
    import subprocess  # noqa: PLC0415

    from autoskillit.core import atomic_write  # noqa: PLC0415
    from autoskillit.execution import partition_files_by_domain  # noqa: PLC0415

    result = subprocess.run(
        ["git", "diff", "--name-only", f"{base_branch}..{batch_branch}"],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
        timeout=60,
    )
    files = [f for f in result.stdout.strip().split("\n") if f]
    partitions = partition_files_by_domain(files)
    if not Path(output_dir).is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")
    out_path = Path(output_dir) / "domain_partitions.json"
    atomic_write(out_path, json.dumps(partitions))
    return {"domain_partitions_path": str(out_path)}


def fetch_merge_queue_data(base_branch: str, cwd: str, output_dir: str) -> dict[str, str]:
    """Fetch and parse GitHub merge queue data server-side for analyze-prs.

    Called by run_python from the fetch_merge_queue_data step in merge-prs.yaml.
    Runs the GraphQL query used in analyze-prs Step 0.5 and parses the response
    with parse_merge_queue_response, writing the result to disk.
    """
    import subprocess  # noqa: PLC0415

    from autoskillit.core import atomic_write  # noqa: PLC0415
    from autoskillit.execution import parse_merge_queue_response  # noqa: PLC0415

    repo_info = subprocess.run(
        ["gh", "repo", "view", "--json", "owner,name"],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
        timeout=60,
    )
    info = json.loads(repo_info.stdout)
    owner = info["owner"]["login"]
    repo = info["name"]

    query = (
        f'{{repository(owner: "{owner}", name: "{repo}") {{'
        f'mergeQueue(branch: "{base_branch}") {{'
        f"entries(first: 50) {{nodes {{position state pullRequest {{number title}}}}}}"
        f"}}}}}}"
    )
    graphql_result = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={query}"],
        capture_output=True,
        text=True,
        cwd=cwd,
        timeout=60,
    )
    if graphql_result.returncode != 0:
        entries: list = []
    else:
        try:
            data = json.loads(graphql_result.stdout)
        except (json.JSONDecodeError, ValueError):
            entries = []
        else:
            entries = parse_merge_queue_response(data)

    if not Path(output_dir).is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")
    out_path = Path(output_dir) / "merge_queue_data.json"
    atomic_write(out_path, json.dumps(entries))
    return {"merge_queue_data_path": str(out_path)}


def detect_zero_changes(worktree_path: str, base_branch: str) -> dict[str, str]:
    """Count commits since branch creation using merge-base."""
    import subprocess

    result = subprocess.run(
        ["git", "rev-list", "--count", f"{base_branch}..HEAD"],
        cwd=worktree_path,
        capture_output=True,
        text=True,
        check=True,
        timeout=60,
    )
    count = int(result.stdout.strip())
    return {"has_changes": "true" if count > 0 else "false", "commit_count": str(count)}


def check_commits_ahead(cwd: str, base_branch: str) -> dict[str, str]:
    """Return {"has_commits": "true"/"false"} based on commits ahead of base_branch.

    Used by the check_has_commits recipe guard to short-circuit pipelines on
    zero-changes branches (feature already merged).
    """
    if not base_branch:
        raise ValueError("base_branch must be non-empty")
    import subprocess  # noqa: PLC0415

    result = subprocess.run(
        ["git", "rev-list", "--count", f"{base_branch}..HEAD"],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    count = int(result.stdout.strip())
    return {"has_commits": "true" if count > 0 else "false"}


def close_issue_already_done(issue_url: str) -> dict[str, str]:
    """Remove in-progress label and close issue as already-implemented.

    Called by close_issue_already_done recipe step when check_has_commits
    detects zero commits ahead of base (feature already merged).
    """
    import subprocess  # noqa: PLC0415

    subprocess.run(
        ["gh", "issue", "edit", issue_url, "--remove-label", "in-progress"],
        capture_output=True,
        check=False,
        timeout=60,
    )
    subprocess.run(
        [
            "gh",
            "issue",
            "close",
            issue_url,
            "--comment",
            "Closing: branch has zero commits ahead of base — feature already implemented.",
        ],
        capture_output=True,
        check=False,
        timeout=60,
    )
    return {"closed": "true"}
