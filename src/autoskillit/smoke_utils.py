"""Utility callables for smoke-test pipeline run_python steps.

Known limitation: functions use hardcoded path conventions from the pipeline recipe.
"""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.core import DISPATCH_ID_ENV_VAR, get_logger

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
    base_branch: str = "",
    local_review_rounds: str = "",
    current_iteration: str = "",
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

    try:
        local_rounds = int(local_review_rounds.strip()) if local_review_rounds.strip() else 0
    except ValueError:
        local_rounds = 0
    try:
        iteration = int(current_iteration.strip()) if current_iteration.strip() else 0
    except ValueError:
        iteration = 0
    review_mode = "local" if local_rounds > 0 and iteration < local_rounds else "github"

    if review_mode == "local" and base_branch.strip():
        result = subprocess.run(
            ["git", "diff", f"{base_branch.strip()}...HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=cwd,
            timeout=60,
        )
    else:
        if review_mode == "local":
            logger.warning(
                "local_review_mode_downgrade: base_branch empty, falling back to gh pr diff"
            )
            review_mode = "github"
        result = subprocess.run(
            ["gh", "pr", "diff", str(pr_number)],
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
        "review_mode": review_mode,
        "annotated_diff_path": str(annotated_path),
        "hunk_ranges_path": str(ranges_path),
        "diff_metrics_path": str(metrics_path),
    }


def check_review_loop(
    pr_number: str,
    current_iteration: str = "",
    max_iterations: str = "3",
    previous_verdict: str = "",
    local_review_rounds: str = "",
) -> dict[str, str]:
    """Pure iteration guard for the review-resolve loop.

    Returns next_iteration, max_exceeded, and had_blocking to determine
    whether to re-review (blocking + iterations remain) or proceed to ci_watch.

    ``had_blocking`` is true when:
    - ``previous_verdict == "changes_requested"`` (always blocking), OR
    - ``local_review_rounds > 0`` and ``current_iteration < local_review_rounds``
      (any verdict, including ``approved``, must re-review until local rounds exhausted)

    ``approved_with_comments`` intentionally yields ``had_blocking=false`` when
    ``local_review_rounds`` is absent or exhausted — the resolve_review pass is
    one-shot and does not trigger a re-review cycle.
    """
    current_iteration = current_iteration or ""
    max_iterations = max_iterations or ""
    previous_verdict = previous_verdict or ""
    local_review_rounds = local_review_rounds or ""
    iteration = int(current_iteration.strip()) if current_iteration.strip() else 0
    next_iteration = iteration + 1
    max_iter = int(max_iterations.strip()) if max_iterations.strip() else 3
    try:
        local_rounds = int(local_review_rounds.strip()) if local_review_rounds.strip() else 0
    except ValueError:
        logger.warning(
            "Invalid local_review_rounds value %r, defaulting to 0",
            local_review_rounds.strip(),
        )
        local_rounds = 0

    is_blocking_verdict = previous_verdict.strip() == "changes_requested"
    local_rounds_not_exhausted = local_rounds > 0 and iteration < local_rounds
    had_blocking = "true" if (is_blocking_verdict or local_rounds_not_exhausted) else "false"

    return {
        "next_iteration": str(next_iteration),
        "max_exceeded": "true" if next_iteration >= max_iter else "false",
        "had_blocking": had_blocking,
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


def check_loop_with_progress(
    current_iteration: str = "",
    max_iterations: str = "5",
    issues_fixed_count: str = "",
    prev_issues_fixed_count: str = "",
) -> dict[str, str]:
    """Progress-aware loop iteration guard.

    Extends check_loop_iteration with zero-progress detection: if
    issues_fixed_count == "0" for two consecutive iterations (current and
    previous), returns zero_progress="true" for early-exit routing.
    """
    try:
        iteration = int(current_iteration.strip()) if current_iteration.strip() else 0
    except ValueError as exc:
        raise ValueError(f"current_iteration must be numeric, got: {current_iteration!r}") from exc
    next_iteration = iteration + 1
    try:
        max_iter = int(max_iterations.strip()) if max_iterations.strip() else 5
    except ValueError as exc:
        raise ValueError(f"max_iterations must be numeric, got: {max_iterations!r}") from exc

    current_fixed = issues_fixed_count.strip() or "0"
    prev_fixed = prev_issues_fixed_count.strip()
    # Normalize current (missing capture → "0") but not prev: prev is legitimately ""
    # on iteration 1, and normalizing it would trigger a false zero_progress on the first
    # call with no prior value.
    zero_progress = current_fixed == "0" and prev_fixed == "0"

    return {
        "next_iteration": str(next_iteration),
        "max_exceeded": "true" if next_iteration >= max_iter else "false",
        "zero_progress": "true" if zero_progress else "false",
        "prev_issues_fixed_count": current_fixed,
    }


def patch_pr_token_summary(
    pr_url: str,
    cwd: str = "",
    order_id: str = "",
    log_dir: str = "",
    timeout: int = 60,
) -> dict[str, str]:
    import os  # noqa: PLC0415
    import subprocess  # noqa: PLC0415
    import time  # noqa: PLC0415

    import regex as re  # noqa: PLC0415

    from autoskillit.execution import resolve_log_dir  # noqa: PLC0415
    from autoskillit.pipeline import DefaultTokenLog, TelemetryFormatter  # noqa: PLC0415

    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_url)
    if not m:
        return {"success": "false", "error": f"Invalid PR URL: {pr_url}"}

    owner, repo, pr_number = m.group(1), m.group(2), m.group(3)

    # Auto-discover order_id from environment when not explicitly provided.
    # AUTOSKILLIT_DISPATCH_ID is set by the fleet dispatcher on all L2 food truck sessions
    # and inherited by sub-sessions, providing correct multi-clone scoping
    # without requiring recipe authors to pass order_id explicitly.
    effective_order_id = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")

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


def _load_json(src: str) -> list | dict:
    """Load JSON from a string or file path. Returns a list or dict."""
    try:
        return json.loads(src)
    except (json.JSONDecodeError, TypeError) as string_err:
        try:
            return json.loads(Path(src).read_text())
        except (OSError, json.JSONDecodeError) as file_err:
            raise file_err from string_err


def parse_eval_manifests(
    canary_manifest: str,
    variant_manifest: str,
    output_dir: str,
    temp_dir: str = "",
) -> dict[str, str]:
    """Read canary and variant manifest files, create eval run directory tree.

    Creates a timestamped eval_run_dir under output_dir/runs/, writes per-canary
    resolved.json files with inlined task_text and overlay_text, and writes
    manifest_index.json with canary_ids, variant_ids, and directory paths.
    """
    from datetime import datetime

    from autoskillit.core import atomic_write  # noqa: PLC0415

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    eval_run_dir = Path(output_dir) / "runs" / timestamp
    eval_run_dir.mkdir(parents=True, exist_ok=True)

    try:
        canaries = _load_json(canary_manifest)
        variants = _load_json(variant_manifest)
    except (OSError, json.JSONDecodeError) as exc:
        return {"success": "false", "error": f"Failed to read manifest: {exc}"}

    try:
        canary_ids = [c["id"] for c in canaries]
        variant_ids = [v["id"] for v in variants]
    except (KeyError, TypeError) as exc:
        return {"success": "false", "error": f"Invalid manifest schema: {exc}"}

    for canary in canaries:
        canary_dir = eval_run_dir / canary["id"]
        canary_dir.mkdir(parents=True, exist_ok=True)

        resolved = dict(canary)
        resolved["variants"] = {}
        resolved["task_text"] = ""

        task_file = canary.get("task_file")
        if not task_file:
            return {
                "success": "false",
                "error": f"Canary {canary.get('id', '?')} missing task_file",
            }
        try:
            resolved["task_text"] = Path(task_file).read_text()
        except OSError as exc:
            return {"success": "false", "error": f"Failed to read task_file: {exc}"}

        for variant in variants:
            variant_dir = canary_dir / variant["id"]
            variant_dir.mkdir(parents=True, exist_ok=True)

            overlay_text: str | None = None
            if variant.get("overlay_file"):
                try:
                    overlay_text = Path(variant["overlay_file"]).read_text()
                except OSError as exc:
                    return {"success": "false", "error": f"Failed to read overlay_file: {exc}"}

            resolved["variants"][variant["id"]] = {
                "label": variant.get("label", variant["id"]),
                "overlay_text": overlay_text,
            }

        atomic_write(canary_dir / "resolved.json", json.dumps(resolved, indent=2))

    manifest_index = {
        "canary_ids": canary_ids,
        "variant_ids": variant_ids,
        "variant_labels": {v["id"]: v.get("label", v["id"]) for v in variants},
    }
    for canary_id in canary_ids:
        manifest_index[f"path_{canary_id}"] = str(eval_run_dir / canary_id)

    manifest_index_path = eval_run_dir / "manifest_index.json"
    atomic_write(manifest_index_path, json.dumps(manifest_index, indent=2))

    return {
        "success": "true",
        "eval_run_dir": str(eval_run_dir),
        "canary_count": str(len(canary_ids)),
        "variant_count": str(len(variant_ids)),
        "manifest_index_path": str(manifest_index_path),
    }


def build_eval_context(
    canary_id: str,
    plan_paths_json: str,
    eval_run_dir: str,
) -> dict[str, str]:
    """Assemble eval_context.json from resolved manifest and plan paths.

    Reads the resolved.json for the given canary, builds the eval_context dict
    with reference and candidate entries, and writes eval_context.json.
    """
    from autoskillit.core import atomic_write  # noqa: PLC0415

    resolved_path = Path(eval_run_dir) / canary_id / "resolved.json"
    try:
        resolved = json.loads(resolved_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        return {"success": "false", "error": f"Failed to read resolved.json: {exc}"}

    try:
        plan_paths = json.loads(plan_paths_json)
    except json.JSONDecodeError as exc:
        return {"success": "false", "error": f"Failed to parse plan_paths_json: {exc}"}

    skill_name = resolved.get("skill", "")
    if skill_name.startswith("/"):
        skill_name = skill_name.lstrip("/")
    if skill_name.startswith("autoskillit:"):
        skill_name = skill_name[len("autoskillit:") :]

    reference_path_raw = resolved.get("reference_path")
    if not reference_path_raw:
        return {
            "success": "false",
            "error": f"Canary {canary_id} resolved.json missing reference_path",
        }
    reference_path = Path(reference_path_raw).resolve()
    candidates = []
    for variant_id, path in plan_paths.items():
        variant_meta = resolved.get("variants", {}).get(variant_id, {})
        label = variant_meta.get("label", variant_id)
        if path is not None:
            candidates.append(
                {
                    "id": variant_id,
                    "path": str(Path(path).resolve()),
                    "label": label,
                    "status": "completed",
                }
            )
        else:
            candidates.append(
                {
                    "id": variant_id,
                    "path": None,
                    "label": label,
                    "status": "failed",
                }
            )

    codebase_root = ""
    eval_run_path = Path(eval_run_dir)
    for parent in eval_run_path.parents:
        if (parent / ".git").exists():
            codebase_root = str(parent)
            break

    eval_context = {
        "eval_id": resolved.get("id", canary_id),
        "subject": skill_name,
        "gap_description": resolved.get("gap_description", ""),
        "detection_criteria": resolved.get("detection_criteria", []),
        "reference": {
            "path": str(reference_path),
            "label": "Original plan (introduced the bug)",
            "artifact_type": resolved.get("reference_type", "plan"),
        },
        "candidates": candidates,
        "codebase_root": codebase_root,
        "eval_run_dir": str(eval_run_path.resolve()),
    }

    out_path = eval_run_path / canary_id / "eval_context.json"
    atomic_write(out_path, json.dumps(eval_context, indent=2))
    return {"success": "true", "eval_context_path": str(out_path)}


def compile_eval_scorecard(
    eval_run_dir: str,
    canary_manifest: str,
    variant_manifest: str,
) -> dict[str, str]:
    """Walk verdict.json files and produce scorecard.json + scorecard.md.

    Reads canary and variant manifests to determine expected combinations,
    counts PASS/FAIL verdicts, and writes both machine-readable and
    human-readable scorecard outputs.
    """
    from autoskillit.core import atomic_write  # noqa: PLC0415

    eval_run_path = Path(eval_run_dir)

    try:
        canaries = _load_json(canary_manifest)
        variants = _load_json(variant_manifest)
    except (OSError, json.JSONDecodeError) as exc:
        return {"success": "false", "error": f"Failed to read manifest: {exc}"}

    try:
        canary_ids = [c["id"] for c in canaries]
        variant_ids = [v["id"] for v in variants]
    except (KeyError, TypeError) as exc:
        return {"success": "false", "error": f"Invalid manifest schema: {exc}"}
    if not canary_ids or not variant_ids:
        return {"success": "false", "error": "Empty canary or variant manifest"}
    total_runs = len(canary_ids) * len(variant_ids)
    passed_runs = 0

    canary_results: dict[str, dict[str, str]] = {}
    variant_summary: dict[str, dict[str, int]] = {
        v["id"]: {"pass": 0, "fail": 0} for v in variants
    }

    for canary in canaries:
        cid = canary["id"]
        canary_results[cid] = {}
        verdict_path = eval_run_path / cid / "verdict.json"
        verdict_data: dict | None = None
        try:
            verdict_data = json.loads(verdict_path.read_text())
        except (OSError, json.JSONDecodeError):
            pass

        for variant in variants:
            vid = variant["id"]
            if verdict_data and verdict_data.get("verdicts", {}).get(vid) is not None:
                overall = verdict_data["verdicts"][vid].get("overall", "FAIL")
            else:
                overall = "FAIL"
            canary_results[cid][vid] = overall
            if overall == "PASS":
                passed_runs += 1
                variant_summary[vid]["pass"] += 1
            else:
                variant_summary[vid]["fail"] += 1

    pass_rate = passed_runs / total_runs if total_runs > 0 else 0.0

    scorecard = {
        "pass_rate": pass_rate,
        "total_runs": total_runs,
        "passed_runs": passed_runs,
        "canary_results": canary_results,
        "variant_summary": {
            vid: {"pass": s["pass"], "fail": s["fail"]} for vid, s in variant_summary.items()
        },
    }

    scorecard_json_path = eval_run_path / "scorecard.json"
    atomic_write(scorecard_json_path, json.dumps(scorecard, indent=2))

    verdict_cache: dict[str, dict | None] = {}
    for canary in canaries:
        verdict_cache[canary["id"]] = try_load_json(eval_run_path / canary["id"] / "verdict.json")

    rows = []
    for canary in canaries:
        cid = canary["id"]
        for variant in variants:
            vid = variant["id"]
            overall = canary_results[cid].get(vid, "FAIL")
            criteria = ""
            if overall == "PASS":
                if vd := verdict_cache.get(cid):
                    verdicts_for_variant = vd.get("verdicts", {}).get(vid, {})
                    pass_count = sum(
                        1
                        for c in verdicts_for_variant.get("criteria", [])
                        if c.get("result") == "PASS"
                    )
                    total_count = len(verdicts_for_variant.get("criteria", []))
                    criteria = f"{pass_count}/{total_count}"
            rows.append(f"| {cid} | {vid} | {overall} | {criteria} |")

    md_lines = ["# Skill Eval Scorecard", "", "| Canary | Variant | Overall | Criteria Passed |"]
    md_lines.append("|--------|---------|---------|-----------------|")
    md_lines.extend(rows)
    md_lines.append("")
    md_lines.append(f"**Pass Rate:** {passed_runs}/{total_runs} ({pass_rate * 100:.1f}%)")

    scorecard_md_path = eval_run_path / "scorecard.md"
    atomic_write(scorecard_md_path, "\n".join(md_lines))

    return {
        "success": "true",
        "scorecard_path": str(scorecard_json_path),
        "pass_rate": str(pass_rate),
        "total_runs": str(total_runs),
        "passed_runs": str(passed_runs),
    }


def try_load_json(path: Path) -> dict | None:
    """Attempt to load JSON from path, returning None on failure."""
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None


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
