"""Review and PR-annotation helpers for smoke_utils sub-modules."""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path

from autoskillit.core import get_logger

logger = get_logger(__name__)


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
        extract_valid_lines,
        parse_hunk_ranges,
        select_review_agents,
    )  # noqa: PLC0415

    out = Path(output_dir)
    if not out.is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")

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
    if review_mode == "github":
        head_sha_result = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--json", "headRefOid", "-q", ".headRefOid"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=30,
        )
    else:
        head_sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=10,
        )
    head_sha = head_sha_result.stdout.strip() if head_sha_result.returncode == 0 else ""
    out.mkdir(parents=True, exist_ok=True)
    annotated_path = out / f"annotated_diff_{pr_number}.txt"
    ranges_path = out / f"ranges_{pr_number}.json"
    valid_lines_path = out / f"valid_lines_{pr_number}.json"
    atomic_write(annotated_path, f"# sha: {head_sha}\n{annotate_diff(diff)}")
    atomic_write(ranges_path, json.dumps(parse_hunk_ranges(diff)))
    atomic_write(valid_lines_path, json.dumps(extract_valid_lines(diff)))
    metrics = compute_diff_metrics(diff)
    loc_thresh = int(loc_threshold) if loc_threshold else 200
    file_thresh = int(file_threshold) if file_threshold else 5
    dispatch = select_review_agents(
        metrics,
        loc_threshold=loc_thresh,
        file_threshold=file_thresh,
    )
    metrics_data = {
        "_head_sha": head_sha,
        "added_lines": metrics.added_lines,
        "removed_lines": metrics.removed_lines,
        "changed_files": metrics.changed_files,
        "file_paths": metrics.file_paths,
        "dispatch_agents": dispatch,
    }
    metrics_path = out / f"metrics_{pr_number}.json"
    atomic_write(metrics_path, json.dumps(metrics_data))
    return {
        "head_sha": head_sha,
        "review_mode": review_mode,
        "annotated_diff_path": str(annotated_path),
        "hunk_ranges_path": str(ranges_path),
        "valid_lines_path": str(valid_lines_path),
        "diff_metrics_path": str(metrics_path),
    }


# Verdicts exempt from local_review_rounds re-review.
# Verdicts in this set yield ``had_blocking=false`` unconditionally regardless
# of ``local_review_rounds``. ``approved_with_comments`` is exempt because its
# resolve pass is one-shot — re-reviewing after resolved warnings adds no value.
LOCAL_ROUND_EXEMPT_VERDICTS: frozenset[str] = frozenset(
    {
        "approved_with_comments",
    }
)

SEVERITY_RANK: dict[str, int] = {"info": 0, "warning": 1, "critical": 2}
TIER_RANK: dict[str, int] = {"H": 0, "M": 1, "L": 2}
_STRUCTURAL_FIXABILITY_VALUES: frozenset[str | None] = frozenset({"STRUCTURAL", None})


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
      AND verdict is not in ``LOCAL_ROUND_EXEMPT_VERDICTS``
      (``approved`` must re-review until local rounds exhausted;
      ``approved_with_comments`` is exempt — its resolve pass is one-shot)

    ``approved_with_comments`` intentionally yields ``had_blocking=false``
    regardless of ``local_review_rounds`` — the resolve_review pass is
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

    verdict = previous_verdict.strip()
    is_blocking_verdict = verdict == "changes_requested"
    local_rounds_not_exhausted = (
        local_rounds > 0
        and iteration < local_rounds
        and verdict not in LOCAL_ROUND_EXEMPT_VERDICTS
    )
    had_blocking = "true" if (is_blocking_verdict or local_rounds_not_exhausted) else "false"

    return {
        "next_iteration": str(next_iteration),
        "prev_iteration": str(iteration),
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
    zero_progress = current_fixed == "0" and prev_fixed == "0"

    return {
        "next_iteration": str(next_iteration),
        "max_exceeded": "true" if next_iteration >= max_iter else "false",
        "zero_progress": "true" if zero_progress else "false",
        "prev_issues_fixed_count": current_fixed,
    }


def enrich_diff_context(
    pr_number: str,
    project_dir: str,
    output_dir: str,
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

    if not Path(project_dir).is_absolute():
        raise ValueError(f"project_dir must be absolute, got {project_dir!r}")
    out = Path(output_dir)
    if not out.is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")
    try:
        ctx_lines = int(context_lines) if context_lines else 50
    except ValueError as exc:
        raise ValueError(f"context_lines must be numeric, got: {context_lines!r}") from exc
    handoff_path = out / f"diff_context_{pr_number}.json"

    if not handoff_path.exists():
        return {"enriched": "false", "reason": "handoff_not_found"}

    handoff = json.loads(handoff_path.read_text())
    entries = handoff.get("context_entries", [])

    annotated_path = out / f"annotated_diff_{pr_number}.txt"
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


def init_counter(counter_value: str = "") -> dict[str, str]:
    """Initialize a loop counter, defaulting to '0' when the value is absent or blank.

    Called by run_python from the init_review_loop_count step to ensure
    review_loop_count is always a valid integer string before annotate_pr_diff runs.
    """
    stripped = counter_value.strip()
    return {"value": stripped if stripped else "0"}


def pre_iteration_cleanup(
    output_dir: str,
    preserve_patterns: str = "",
) -> dict[str, str]:
    """Remove files from a prior iteration's output directory.

    Called by run_python from the pre_review_cleanup step on the loop-back path.
    With iteration-scoped directories this is defense-in-depth; the primary
    isolation comes from writing to iter_N/ subdirectories.
    """
    import fnmatch  # noqa: PLC0415

    out = Path(output_dir)
    if not out.is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")
    if not out.exists():
        return {"cleaned": "false", "reason": "not_found"}

    patterns = [p.strip() for p in preserve_patterns.split(",") if p.strip()]
    removed = 0
    for f in out.iterdir():
        if not f.is_file():
            continue
        if patterns and any(fnmatch.fnmatch(f.name, p) for p in patterns):
            continue
        f.unlink(missing_ok=True)
        removed += 1

    return {"cleaned": "true", "removed_count": str(removed)}


def select_review_dimensions(
    experiment_type: str = "",
    dimension_weights_json: str = "",
    secondary_modifiers_json: str = "",
    output_dir: str = "",
) -> dict[str, str]:
    """Transform dimension weights and modifiers into the dialing contract format.

    Called by run_python from review-design recipe steps. Parses dimension
    weights, applies secondary modifiers, filters silent (S) dimensions,
    sorts by tier, and writes a dimensions manifest.
    """
    from autoskillit.core import atomic_write  # noqa: PLC0415

    _EMPTY = {"selected_lenses": "", "lens_context_paths": "", "dimensions_manifest_path": ""}

    out = Path(output_dir)
    if not out.is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")

    weights_str = dimension_weights_json.strip()
    if not weights_str:
        return _EMPTY

    try:
        weights: dict[str, str] = json.loads(weights_str)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in dimension_weights_json: {exc}") from exc
    if not weights:
        return _EMPTY

    modifiers_str = secondary_modifiers_json.strip()
    try:
        modifiers: list[str] = json.loads(modifiers_str) if modifiers_str else []
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in secondary_modifiers_json: {exc}") from exc

    tier_order = ["H", "M", "L", "S"]

    def _upgrade_one_tier(current: str) -> str:
        if current not in tier_order:
            raise ValueError(f"unknown tier {current!r}; expected one of {tier_order}")
        idx = tier_order.index(current)
        return tier_order[max(0, idx - 1)]

    for mod in modifiers:
        if mod == "+causal" and "causal_structure" in weights:
            weights["causal_structure"] = _upgrade_one_tier(weights["causal_structure"])
        elif mod == "+high_cost" and "resource_proportionality" in weights:
            if weights["resource_proportionality"] == "L":
                weights["resource_proportionality"] = "M"
        elif mod == "+deployment" and "ecological_validity" in weights:
            if tier_order.index(weights["ecological_validity"]) > tier_order.index("M"):
                weights["ecological_validity"] = "M"
        elif mod == "+multi_metric" and "statistical_corrections" in weights:
            weights["statistical_corrections"] = _upgrade_one_tier(
                weights["statistical_corrections"]
            )

    active = {dim: w for dim, w in weights.items() if w != "S"}
    if not active:
        return _EMPTY

    sorted_dims = sorted(active.items(), key=lambda x: TIER_RANK.get(x[1], 3))

    selected_lenses = ",".join(d for d, _ in sorted_dims)
    lens_context_paths = ",".join("" for _ in sorted_dims)

    out.mkdir(parents=True, exist_ok=True)
    manifest_path = out / "dimensions_manifest.json"
    manifest_data = dict(sorted_dims)
    atomic_write(manifest_path, json.dumps(manifest_data))

    return {
        "selected_lenses": selected_lenses,
        "lens_context_paths": lens_context_paths,
        "dimensions_manifest_path": str(manifest_path),
    }


def aggregate_review_verdict(
    findings_manifest_path: str = "",
    dimensions_manifest_path: str = "",
    experiment_type: str = "",
    rt_max_severity: str = "",
    output_dir: str = "",
) -> dict[str, str]:
    """Compute GO/REVISE/STOP verdict from review findings and dimension weights.

    Called by run_python from the review-design verdict step. Applies red-team
    severity caps, computes proportional warning thresholds, identifies
    structural stop triggers, and writes evaluation artifacts.
    """
    from datetime import datetime  # noqa: PLC0415

    from autoskillit.core import atomic_write  # noqa: PLC0415

    if not findings_manifest_path.strip():
        return {"error": "findings_manifest_path is empty"}

    out = Path(output_dir)
    if not out.is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")

    findings_path = Path(findings_manifest_path.strip())
    if not findings_path.exists():
        return {"error": f"findings manifest not found: {findings_manifest_path}"}

    try:
        findings: list[dict[str, str | None]] = json.loads(findings_path.read_text())
    except json.JSONDecodeError as exc:
        return {"error": f"corrupt findings manifest: {exc}"}

    active_dimensions = 0
    dim_data: dict[str, str] = {}
    if dimensions_manifest_path.strip():
        dim_path = Path(dimensions_manifest_path.strip())
        if dim_path.exists():
            try:
                dim_data = json.loads(dim_path.read_text())
            except json.JSONDecodeError as exc:
                return {"error": f"corrupt dimensions manifest: {exc}"}
            active_dimensions = sum(1 for w in dim_data.values() if w != "S")

    rt_cap = rt_max_severity.strip() if rt_max_severity.strip() else "critical"
    if not rt_max_severity.strip() and experiment_type.strip():
        from autoskillit.recipe import get_experiment_type_by_name  # noqa: PLC0415

        spec = get_experiment_type_by_name(experiment_type.strip())
        if spec is not None:
            rt_cap = spec.red_team_focus.get("severity_cap", "critical")

    for f in findings:
        if f.get("dimension") == "red_team":
            f_sev = f.get("severity", "info")
            if SEVERITY_RANK.get(str(f_sev), 0) > SEVERITY_RANK.get(rt_cap, 2):
                f["severity"] = rt_cap

    warning_threshold = active_dimensions * 5

    critical_findings = [f for f in findings if f.get("severity") == "critical"]
    warning_findings = [f for f in findings if f.get("severity") == "warning"]
    info_findings = [f for f in findings if f.get("severity") == "info"]

    l1_criticals = [
        f
        for f in critical_findings
        if f.get("dimension") in {"estimand_clarity", "hypothesis_falsifiability"}
    ]
    structural_stop_triggers = [
        f for f in l1_criticals if f.get("fixability") in _STRUCTURAL_FIXABILITY_VALUES
    ]
    rt_stop = [f for f in critical_findings if f.get("dimension") == "red_team"]
    stop_triggers = structural_stop_triggers + rt_stop

    if stop_triggers:
        verdict = "STOP"
    elif critical_findings or (
        active_dimensions > 0 and len(warning_findings) >= warning_threshold
    ):
        verdict = "REVISE"
    else:
        verdict = "GO"

    out.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(tz=UTC).strftime("%Y-%m-%d_%H%M%S")

    scorecard_rows = []
    for dim, weight in dim_data.items():
        c = sum(
            1 for f in findings if f.get("dimension") == dim and f.get("severity") == "critical"
        )
        w = sum(
            1 for f in findings if f.get("dimension") == dim and f.get("severity") == "warning"
        )
        i = sum(1 for f in findings if f.get("dimension") == dim and f.get("severity") == "info")
        scorecard_rows.append(f"| {dim} | {weight} | {c} | {w} | {i} |")

    dashboard_lines = [
        "# Evaluation Dashboard\n",
        f"## Verdict: {verdict}\n",
        "## Dimension Scorecard\n",
        "| Dimension | Weight | Critical | Warning | Info |",
        "|-----------|--------|----------|---------|------|",
        *scorecard_rows,
        "",
        "## Finding Summary\n",
        f"- **Critical:** {len(critical_findings)}",
        f"- **Warning:** {len(warning_findings)}",
        f"- **Info:** {len(info_findings)}",
        f"- **Stop triggers:** {len(stop_triggers)}",
        "",
        "## Summary\n",
        "```yaml",
        f"verdict: {verdict}",
        f"total_findings: {len(findings)}",
        f"critical_count: {len(critical_findings)}",
        f"warning_count: {len(warning_findings)}",
        f"info_count: {len(info_findings)}",
        f"active_dimensions: {active_dimensions}",
        f"warning_threshold: {warning_threshold}",
        f"stop_triggers: {len(stop_triggers)}",
        "```",
    ]
    dashboard_path = out / f"evaluation_dashboard_{timestamp}.md"
    atomic_write(dashboard_path, "\n".join(dashboard_lines))

    result: dict[str, str] = {
        "verdict": verdict,
        "evaluation_dashboard_path": str(dashboard_path),
    }

    if verdict == "REVISE":
        required = [
            f"- **[{f.get('dimension', '?')}]** {f.get('message', f.get('finding', ''))}"
            for f in critical_findings
        ]
        recommended = [
            f"- **[{f.get('dimension', '?')}]** {f.get('message', f.get('finding', ''))}"
            for f in warning_findings
        ]
        guidance_lines = [
            "# Revision Guidance\n",
            "## Required Revisions (Critical)\n",
            *(required if required else ["- (none)"]),
            "",
            "## Recommended Revisions (Warning)\n",
            *(recommended if recommended else ["- (none)"]),
        ]
        guidance_path = out / f"revision_guidance_{timestamp}.md"
        atomic_write(guidance_path, "\n".join(guidance_lines))
        result["revision_guidance_path"] = str(guidance_path)

    return result
