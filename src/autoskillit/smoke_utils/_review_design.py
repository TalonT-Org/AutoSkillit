"""Review-design and verdict logic for smoke_utils.

Companion to ``_review.py`` (which holds the PR annotation pipeline and generic
iteration guards). This module owns the review-loop guard, context enrichment,
counter initialization, and verdict scoring.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC
from pathlib import Path
from typing import Any, cast

from autoskillit.core import (
    atomic_write,
    get_logger,
)
from autoskillit.recipe import get_experiment_type_by_name

logger = get_logger(__name__)

# Verdicts in this set yield ``had_blocking=false`` unconditionally regardless
# of local rounds: approved comments are one-shot, while needs_human skips review.
LOCAL_ROUND_EXEMPT_VERDICTS: frozenset[str] = frozenset(
    {
        "approved_with_comments",
        "needs_human",
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
    is_blocking_verdict = verdict in {"changes_requested", "stale_snapshot"}
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


def _parse_context_lines(context_lines: str) -> int:
    try:
        return int(context_lines) if context_lines else 50
    except ValueError as exc:
        raise ValueError(f"context_lines must be numeric, got: {context_lines!r}") from exc


def _validate_v1_context_entry(entry: object) -> str | None:
    if not isinstance(entry, dict):
        return "invalid_context_entry"
    if "anchor_digest" in entry:
        return "unexpected_anchor_digest"
    if not isinstance(entry.get("path"), str) or not entry["path"]:
        return "invalid_context_path"
    if not isinstance(entry.get("code_region"), str):
        return "invalid_code_region"
    line = entry.get("line")
    if line is not None and (type(line) is not int or line < 1):
        return "invalid_context_line"
    return None


def _load_complete_v1_handoff(handoff_target: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not handoff_target.exists():
        return None, "handoff_not_found"
    try:
        handoff = json.loads(handoff_target.read_text())
    except (OSError, json.JSONDecodeError):
        return None, "invalid_handoff"
    if not isinstance(handoff, dict) or handoff.get("schema_version") != 1:
        return None, "handoff_not_complete_v1"

    entries = handoff.get("context_entries")
    if not isinstance(entries, list):
        return None, "invalid_context_entries"
    for entry in entries:
        if reason := _validate_v1_context_entry(entry):
            return None, reason
    return handoff, None


def _validate_handoff_checkout_head(handoff: dict[str, Any], project_dir: str) -> str | None:
    if not Path(project_dir).is_absolute():
        raise ValueError(f"project_dir must be absolute, got {project_dir!r}")
    expected_head = handoff.get("_head_sha")
    if expected_head is None or not (Path(project_dir) / ".git").exists():
        return None
    if not isinstance(expected_head, str):
        return "invalid_handoff_head"
    try:
        checkout_head = subprocess.run(
            ["git", "-C", project_dir, "rev-parse", "HEAD"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "checkout_head_unavailable"
    if checkout_head.returncode != 0:
        return "checkout_head_unavailable"
    return "checkout_head_mismatch" if checkout_head.stdout.strip() != expected_head else None


def _read_annotated_diff(
    output_dir: Path,
    pr_number: str,
    entries: list[object],
) -> tuple[str, str | None]:
    if not output_dir.is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")
    if not any(type(entry.get("line")) is int for entry in entries if isinstance(entry, dict)):
        return "", None
    annotated_path = output_dir / f"annotated_diff_{pr_number}.txt"
    if not annotated_path.exists():
        return "", "annotated_diff_not_found"
    return annotated_path.read_text(), None


def _build_enriched_handoff(
    handoff: dict[str, Any],
    annotated_diff: str,
    context_lines: int,
) -> tuple[dict[str, Any] | None, int, str | None]:
    """Build a v2 replacement in memory, leaving the v1 handoff untouched."""
    from autoskillit.execution import (  # noqa: PLC0415
        extract_annotated_source_line,
        extract_code_region,
        hash_source_line,
    )

    enriched_handoff = json.loads(json.dumps(handoff))
    enriched_entries = cast(list[dict[str, Any]], enriched_handoff["context_entries"])
    enriched_count = 0
    for entry in enriched_entries:
        line = entry["line"]
        if type(line) is not int:
            continue
        source_line = extract_annotated_source_line(annotated_diff, entry["path"], line)
        if source_line is None:
            return None, 0, "invalid_context_anchor"
        entry["anchor_digest"] = hash_source_line(source_line)
        if not entry["code_region"]:
            entry["code_region"] = extract_code_region(
                annotated_diff,
                entry["path"],
                line,
                context_lines=context_lines,
            )
            if entry["code_region"]:
                enriched_count += 1

    enriched_handoff["schema_version"] = 2
    return enriched_handoff, enriched_count, None


def enrich_diff_context(
    pr_number: str,
    project_dir: str,
    output_dir: str,
    context_lines: str = "50",
) -> dict[str, str]:
    """Atomically enrich a complete v1 diff-context handoff to schema version 2."""
    if not Path(project_dir).is_absolute():
        raise ValueError(f"project_dir must be absolute, got {project_dir!r}")
    out = Path(output_dir)
    if not out.is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")

    ctx_lines = _parse_context_lines(context_lines)
    handoff_path = out / f"diff_context_{pr_number}.json"
    handoff, reason = _load_complete_v1_handoff(handoff_path)
    if reason:
        return {"enriched": "false", "reason": reason}
    if handoff is None:
        return {"enriched": "false", "reason": "handoff_not_complete_v1"}

    if reason := _validate_handoff_checkout_head(handoff, project_dir):
        return {"enriched": "false", "reason": reason}
    entries = cast(list[object], handoff["context_entries"])
    annotated_diff, reason = _read_annotated_diff(out, pr_number, entries)
    if reason:
        return {"enriched": "false", "reason": reason}
    enriched_handoff, enriched_count, reason = _build_enriched_handoff(
        handoff,
        annotated_diff,
        ctx_lines,
    )
    if reason:
        return {"enriched": "false", "reason": reason}
    if enriched_handoff is None:
        return {"enriched": "false", "reason": "invalid_context_anchor"}

    atomic_write(handoff_path, json.dumps(enriched_handoff, indent=2))
    return {
        "enriched": "true",
        "enriched_count": str(enriched_count),
        "total_entries": str(len(entries)),
    }


def clear_review_annotation_context() -> dict[str, str]:
    """Clear every captured annotation authority before a new publication attempt."""
    return {
        "annotated_diff_path": "",
        "hunk_ranges_path": "",
        "valid_lines_path": "",
        "anchor_authority_path": "",
        "diff_metrics_path": "",
        "review_mode": "",
        "pr_head_sha": "",
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
    output_dir: str = "",
) -> dict[str, str]:
    """Derive dimension weights from the experiment type registry and write a manifest.

    Called by run_python from review-design recipe steps. Looks up the
    experiment type in the registry, filters silent (S) dimensions,
    sorts by tier, and writes a dimensions manifest.
    """

    _EMPTY = {"selected_lenses": "", "lens_context_paths": "", "dimensions_manifest_path": ""}

    out = Path(output_dir)
    if not out.is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")

    if not experiment_type.strip():
        return _EMPTY

    spec = get_experiment_type_by_name(experiment_type.strip())
    if spec is None:
        return _EMPTY

    weights: dict[str, str] = spec.dimension_weights
    if not weights:
        return _EMPTY

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

    out = Path(output_dir)
    if not out.is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")

    if findings_manifest_path.strip():
        findings_path = Path(findings_manifest_path.strip())
        if not findings_path.exists():
            return {"error": f"findings manifest not found: {findings_manifest_path}"}
        try:
            findings: list[dict[str, str | None]] = json.loads(findings_path.read_text())
        except json.JSONDecodeError as exc:
            return {"error": f"corrupt findings manifest: {exc}"}
    else:
        findings = []

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
