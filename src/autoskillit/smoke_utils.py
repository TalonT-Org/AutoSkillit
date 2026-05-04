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
    out_path = Path(output_dir) / "domain_partitions.json"
    atomic_write(out_path, json.dumps(partitions))
    return {"domain_partitions_path": str(out_path)}


def annotate_pr_diff(
    pr_number: str,
    cwd: str,
    output_dir: str,
    loc_threshold: str = "",
    file_threshold: str = "",
) -> dict[str, str]:
    """Fetch and annotate a PR diff server-side for review-pr.

    Called by run_python from the annotate_pr_diff step in merge-prs.yaml.
    Fetches the diff via `gh pr diff`, annotates it, and writes both the
    annotated diff and hunk ranges to disk.
    """
    import subprocess  # noqa: PLC0415

    from autoskillit.core import atomic_write  # noqa: PLC0415
    from autoskillit.execution import (
        annotate_diff,
        compute_diff_metrics,
        parse_hunk_ranges,
        select_review_agents,
    )  # noqa: PLC0415

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
    metrics = compute_diff_metrics(diff)
    loc_thresh = int(loc_threshold) if loc_threshold else 200
    file_thresh = int(file_threshold) if file_threshold else 5
    dispatch = select_review_agents(
        metrics,
        loc_threshold=loc_thresh,
        file_threshold=file_thresh,
    )
    metrics_data = {
        "added_lines": metrics.added_lines,
        "removed_lines": metrics.removed_lines,
        "changed_files": metrics.changed_files,
        "file_paths": metrics.file_paths,
        "dispatch_agents": dispatch,
    }
    metrics_path = out / f"metrics_{pr_number}.json"
    atomic_write(metrics_path, json.dumps(metrics_data))
    return {
        "annotated_diff_path": str(annotated_path),
        "hunk_ranges_path": str(ranges_path),
        "diff_metrics_path": str(metrics_path),
    }


def check_review_loop(
    pr_number: str,
    current_iteration: str = "",
    max_iterations: str = "3",
    previous_verdict: str = "",
) -> dict[str, str]:
    """Pure iteration guard for the review-resolve loop.

    Returns next_iteration, max_exceeded, and had_blocking to determine
    whether to re-review (blocking + iterations remain) or proceed to ci_watch.

    ``had_blocking`` is true only when ``previous_verdict == "changes_requested"``.
    ``approved_with_comments`` intentionally yields ``had_blocking=false`` — the
    resolve_review pass is one-shot and does not trigger a re-review cycle.
    """
    current_iteration = current_iteration or ""
    max_iterations = max_iterations or ""
    previous_verdict = previous_verdict or ""
    iteration = int(current_iteration.strip()) if current_iteration.strip() else 0
    next_iteration = iteration + 1
    max_iter = int(max_iterations.strip()) if max_iterations.strip() else 3

    return {
        "next_iteration": str(next_iteration),
        "max_exceeded": "true" if next_iteration >= max_iter else "false",
        "had_blocking": "true" if previous_verdict.strip() == "changes_requested" else "false",
    }


def check_loop_iteration(
    current_iteration: str = "",
    max_iterations: str = "2",
) -> dict[str, str]:
    """Generic loop iteration guard for recipe cycles.

    Increments the iteration counter and returns whether the budget is exhausted.
    Designed to be called via run_python in a recipe step with on_result routing
    based on max_exceeded.
    """
    current_iteration = current_iteration or ""
    max_iterations = max_iterations or ""
    try:
        iteration = int(current_iteration.strip()) if current_iteration.strip() else 0
    except ValueError as exc:
        raise ValueError(f"current_iteration must be numeric, got: {current_iteration!r}") from exc
    next_iteration = iteration + 1
    try:
        max_iter = int(max_iterations.strip()) if max_iterations.strip() else 2
    except ValueError as exc:
        raise ValueError(f"max_iterations must be numeric, got: {max_iterations!r}") from exc
    return {
        "next_iteration": str(next_iteration),
        "max_exceeded": "true" if next_iteration >= max_iter else "false",
    }


def patch_pr_token_summary(
    pr_url: str,
    cwd: str = "",
    order_id: str = "",
    log_dir: str = "",
) -> dict[str, str]:
    import os  # noqa: PLC0415
    import re  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import time  # noqa: PLC0415

    from autoskillit.execution import resolve_log_dir  # noqa: PLC0415
    from autoskillit.pipeline import DefaultTokenLog, TelemetryFormatter  # noqa: PLC0415

    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_url)
    if not m:
        return {"success": "false", "error": f"Invalid PR URL: {pr_url}"}

    owner, repo, pr_number = m.group(1), m.group(2), m.group(3)

    # Auto-discover order_id from environment when not explicitly provided.
    # AUTOSKILLIT_DISPATCH_ID is set by the fleet dispatcher on all L2 sessions
    # and inherited by L3 sub-sessions, providing correct multi-clone scoping
    # without requiring recipe authors to pass order_id explicitly.
    effective_order_id = order_id or os.environ.get("AUTOSKILLIT_DISPATCH_ID", "")

    log_root = resolve_log_dir(log_dir)
    token_log = DefaultTokenLog()
    if effective_order_id:
        count = token_log.load_from_log_dir(log_root, order_id_filter=effective_order_id)
    else:
        count = token_log.load_from_log_dir(log_root, cwd_filter=cwd)

    if count == 0:
        return {"success": "false", "error": "No sessions found", "sessions_loaded": "0"}

    scope_kwargs: dict[str, str] = {"order_id": effective_order_id} if effective_order_id else {}
    steps = token_log.get_report(**scope_kwargs)
    total = token_log.compute_total(**scope_kwargs)
    table = TelemetryFormatter.format_token_table(steps, total)
    efficiency = TelemetryFormatter.format_efficiency_table(steps, total)
    combined = table + ("\n\n" + efficiency if efficiency else "")

    try:
        read_result = subprocess.run(
            ["gh", "api", f"repos/{owner}/{repo}/pulls/{pr_number}", "--jq", ".body"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"success": "false", "error": f"Failed to read PR body: {exc}"}

    if read_result.returncode != 0:
        return {"success": "false", "error": f"Failed to read PR: {read_result.stderr.strip()}"}

    current_body = read_result.stdout.strip()
    if not current_body or current_body == "null":
        return {"success": "false", "error": "PR body is empty"}

    # Match from "## Token Usage Summary" through an optional "## Token Efficiency"
    # block, stopping at the next "## " heading or end-of-string.
    section_re = re.compile(
        r"\n*## Token Usage Summary\n.*?(?:\n## Token Efficiency\n.*?)?(?=\n## |\Z)",
        re.DOTALL,
    )
    if section_re.search(current_body):
        new_body = section_re.sub("\n\n" + combined, current_body)
    else:
        new_body = current_body + "\n\n" + combined

    time.sleep(1)

    try:
        patch_result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{owner}/{repo}/pulls/{pr_number}",
                "--method",
                "PATCH",
                "--raw-field",
                f"body={new_body}",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"success": "false", "error": f"Failed to patch PR: {exc}"}

    if patch_result.returncode != 0:
        detail = patch_result.stderr.strip() or patch_result.stdout.strip()
        return {
            "success": "false",
            "error": f"Failed to patch PR: {detail}",
        }

    return {"success": "true", "sessions_loaded": str(count)}


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


def enrich_diff_context(
    pr_number: str,
    work_dir: str,
    context_lines: str = "50",
) -> dict[str, str]:
    """Fill empty code_region fields in the review-pr diff_context handoff.

    Called by run_python from the enrich_diff_context step in implementation.yaml.
    Reads the existing diff_context_{pr_number}.json and the annotated diff,
    then uses extract_code_region() to populate any empty code_region entries.
    Overwrites the handoff file in place.
    """
    from autoskillit.core import atomic_write  # noqa: PLC0415
    from autoskillit.execution import extract_code_region  # noqa: PLC0415

    ctx_lines = int(context_lines) if context_lines else 50
    temp_dir = Path(work_dir) / ".autoskillit" / "temp"
    handoff_path = temp_dir / "review-pr" / f"diff_context_{pr_number}.json"

    if not handoff_path.exists():
        return {"enriched": "false", "reason": "handoff_not_found"}

    handoff = json.loads(handoff_path.read_text())
    entries = handoff.get("context_entries", [])

    annotated_path = temp_dir / "review-pr" / f"annotated_diff_{pr_number}.txt"
    if not annotated_path.exists():
        return {"enriched": "false", "reason": "annotated_diff_not_found"}

    annotated_diff = annotated_path.read_text()
    enriched_count = 0

    for entry in entries:
        if not entry.get("code_region"):
            region = extract_code_region(
                annotated_diff,
                entry["path"],
                entry["line"],
                context_lines=ctx_lines,
            )
            entry["code_region"] = region
            if region:
                enriched_count += 1

    atomic_write(handoff_path, json.dumps(handoff, indent=2))
    return {
        "enriched": "true",
        "enriched_count": str(enriched_count),
        "total_entries": str(len(entries)),
    }
