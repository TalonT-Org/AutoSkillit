"""Utility callables for smoke-test pipeline run_python steps.

Known limitation: functions use hardcoded path conventions from the pipeline recipe.
"""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.core import get_logger

logger = get_logger(__name__)


def check_bug_report_non_empty(workspace: str) -> dict[str, str]:
    """Return {"non_empty": "true"} if bug_report.json exists and is non-empty.

    Called by run_python from the check_summary step in smoke-test.yaml.
    The workspace argument is the root directory initialised by the setup step.
    """
    report = Path(workspace) / "bug_report.json"
    if not report.exists():
        return {"non_empty": "false"}
    try:
        data = json.loads(report.read_text())
        return {"non_empty": "true" if data else "false"}
    except (json.JSONDecodeError, OSError):
        return {"non_empty": "false"}


def compute_domain_partitions(
    integration_branch: str, base_branch: str, cwd: str, output_dir: str
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
        ["git", "diff", "--name-only", f"{base_branch}..{integration_branch}"],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
        timeout=60,
    )
    files = [f for f in result.stdout.strip().split("\n") if f]
    partitions = partition_files_by_domain(files)
    out_path = Path(output_dir) / "domain_partitions.json"
    atomic_write(out_path, json.dumps(partitions))
    return {"domain_partitions_path": str(out_path)}


def annotate_pr_diff(pr_number: str, cwd: str, output_dir: str) -> dict[str, str]:
    """Fetch and annotate a PR diff server-side for review-pr.

    Called by run_python from the annotate_pr_diff step in merge-prs.yaml.
    Fetches the diff via `gh pr diff`, annotates it, and writes both the
    annotated diff and hunk ranges to disk.
    """
    import subprocess  # noqa: PLC0415

    from autoskillit.core import atomic_write  # noqa: PLC0415
    from autoskillit.execution import annotate_diff, parse_hunk_ranges  # noqa: PLC0415

    result = subprocess.run(
        ["gh", "pr", "diff", pr_number],
        capture_output=True,
        text=True,
        check=True,
        cwd=cwd,
        timeout=60,
    )
    diff = result.stdout
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    annotated_path = out / f"annotated_diff_{pr_number}.txt"
    ranges_path = out / f"ranges_{pr_number}.json"
    atomic_write(annotated_path, annotate_diff(diff))
    atomic_write(ranges_path, json.dumps(parse_hunk_ranges(diff)))
    return {
        "annotated_diff_path": str(annotated_path),
        "hunk_ranges_path": str(ranges_path),
    }


def check_review_loop(
    pr_number: str,
    cwd: str,
    current_iteration: str = "",
    max_iterations: str = "3",
) -> dict[str, str]:
    """Check GitHub review threads and determine if the review-resolve loop should continue.

    Called by run_python from the check_review_loop step in recipe pipelines.
    Fetches all review threads via GraphQL, counts unresolved threads with [critical]
    or [warning] severity markers, and returns routing data for the recipe loop gate.

    On any subprocess failure or GraphQL error, degrades gracefully: returns
    has_blocking=false so the pipeline is never blocked by an API failure.
    """
    import subprocess  # noqa: PLC0415

    iteration = int(current_iteration.strip()) if current_iteration.strip() else 0
    next_iteration = iteration + 1
    max_iter = int(max_iterations.strip()) if max_iterations.strip() else 3

    # Compute next_iteration string for degraded path before any subprocess calls
    degraded: dict[str, str] = {
        "has_blocking": "false",
        "next_iteration": str(next_iteration),
        "max_exceeded": "false",
        "blocking_count": "0",
    }

    # Derive owner/repo
    try:
        repo_result = subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
            timeout=60,
        )
    except Exception:
        logger.warning("check_review_loop: failed to get repo info", exc_info=True)
        return degraded

    name_with_owner = repo_result.stdout.strip()
    if "/" not in name_with_owner:
        logger.warning("check_review_loop: unexpected nameWithOwner format: %r", name_with_owner)
        return degraded
    owner, repo = name_with_owner.split("/", 1)

    # GraphQL query with pagination
    graphql_query = """
query($owner:String!, $repo:String!, $number:Int!, $after:String) {
  repository(owner:$owner, name:$repo) {
    pullRequest(number:$number) {
      reviewThreads(first:100, after:$after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          isResolved
          line
          originalLine
          comments(first:1) {
            nodes { body }
          }
        }
      }
    }
  }
}
""".strip()

    all_threads: list[dict] = []
    cursor: str | None = None

    while True:
        try:
            gql_result = subprocess.run(
                [
                    "gh",
                    "api",
                    "graphql",
                    "-f",
                    f"query={graphql_query}",
                    "-F",
                    f"owner={owner}",
                    "-F",
                    f"repo={repo}",
                    "-F",
                    f"number={int(pr_number)}",
                    *((["-F", f"after={cursor}"]) if cursor is not None else []),
                ],
                capture_output=True,
                text=True,
                cwd=cwd,
                timeout=60,
            )
        except Exception:
            logger.warning("check_review_loop: GraphQL subprocess error", exc_info=True)
            return degraded

        if gql_result.returncode != 0:
            logger.warning("check_review_loop: gh api graphql failed: %s", gql_result.stderr)
            return degraded

        try:
            data = json.loads(gql_result.stdout)
        except (json.JSONDecodeError, ValueError):
            logger.warning("check_review_loop: JSON parse error", exc_info=True)
            return degraded

        if "errors" in data:
            logger.warning("GraphQL errors: %s", data["errors"])
            return degraded

        try:
            pr_data = data["data"]["repository"]["pullRequest"]
            threads_page = pr_data["reviewThreads"]
            nodes = threads_page["nodes"]
            page_info = threads_page["pageInfo"]
        except (KeyError, TypeError):
            logger.warning("check_review_loop: unexpected GraphQL response shape", exc_info=True)
            return degraded

        all_threads.extend(nodes)

        if page_info.get("hasNextPage"):
            cursor = page_info.get("endCursor")
        else:
            break

    # Classify threads
    blocking_count = 0
    for thread in all_threads:
        if thread.get("isResolved"):
            continue
        comments_nodes = thread.get("comments", {}).get("nodes", [])
        body = comments_nodes[0].get("body", "") if comments_nodes else ""
        body_lower = body.lower()
        if "[critical]" in body_lower or "[warning]" in body_lower:
            blocking_count += 1

    return {
        "has_blocking": "true" if blocking_count > 0 else "false",
        "next_iteration": str(next_iteration),
        "max_exceeded": "true" if next_iteration >= max_iter else "false",
        "blocking_count": str(blocking_count),
    }


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

    out_path = Path(output_dir) / "merge_queue_data.json"
    atomic_write(out_path, json.dumps(entries))
    return {"merge_queue_data_path": str(out_path)}
