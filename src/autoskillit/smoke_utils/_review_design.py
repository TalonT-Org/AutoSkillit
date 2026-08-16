"""Review-design, receipt, and verdict logic for smoke_utils.

Companion to ``_review.py`` (which holds the PR annotation pipeline and generic
iteration guards). This module owns the review-loop guard, receipt validation,
context enrichment, counter initialization, and verdict scoring.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC
from pathlib import Path
from typing import Any, TypeGuard

from autoskillit.core import (
    atomic_write,
    get_logger,
    is_final_github_review_state,
    is_valid_github_review_head_sha,
    is_valid_github_review_logical_iteration,
    is_valid_github_review_operation_key,
    is_valid_github_review_repository,
    review_receipt_validation_error,
)
from autoskillit.recipe import get_experiment_type_by_name

logger = get_logger(__name__)

_REVIEW_RECEIPT_MAX_BYTES = 1_048_576

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


def _review_check_failed(sentinel: str) -> dict[str, str]:
    return {"reviews_posted": "false", "sentinel": sentinel}


def _load_unique_json_object(raw: bytes) -> dict[str, Any] | None:
    def reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        decoded = raw.decode("utf-8")
        value = json.loads(decoded, object_pairs_hook=reject_duplicate_keys)
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _read_stable_private_receipt(path: Path) -> bytes | None:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_size > _REVIEW_RECEIPT_MAX_BYTES
        ):
            return None
        descriptor = os.open(path, flags)
    except OSError:
        return None
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
        ):
            return None
        raw = os.read(descriptor, _REVIEW_RECEIPT_MAX_BYTES + 1)
        after = os.fstat(descriptor)
        if len(raw) > _REVIEW_RECEIPT_MAX_BYTES or (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
            opened.st_mtime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
        ):
            return None
        return raw
    except OSError:
        return None
    finally:
        os.close(descriptor)


def _is_positive_int(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def check_review_posted(
    *,
    cwd: str,
    receipt_path: str,
    mode: str,
    repository: str,
    pr_number: int,
    head_sha: str,
    logical_iteration: str,
    operation_key: str,
    post_state: str,
) -> dict[str, str]:
    """Validate an authoritative, identity-bound PR-review receipt."""
    if mode == "local":
        return {"reviews_posted": "true", "sentinel": ""}
    if mode != "github":
        return _review_check_failed("invalid_review_mode")
    if (
        not is_valid_github_review_repository(repository)
        or not _is_positive_int(pr_number)
        or not is_valid_github_review_head_sha(head_sha)
        or not is_valid_github_review_logical_iteration(logical_iteration)
        or not is_valid_github_review_operation_key(operation_key)
        or not is_final_github_review_state(post_state)
    ):
        return _review_check_failed("invalid_expected_identity")

    cwd_path = Path(cwd)
    receipt = Path(receipt_path)
    if not cwd_path.is_absolute() or not receipt.is_absolute():
        return _review_check_failed("invalid_receipt_path")
    if receipt.name != f"batch_review_response_{pr_number}.json":
        return _review_check_failed("invalid_receipt_basename")
    try:
        root = cwd_path.resolve(strict=True)
        managed_temp = (root / ".autoskillit" / "temp").resolve(strict=True)
        resolved_receipt = receipt.resolve(strict=True)
        resolved_receipt.relative_to(managed_temp)
    except (OSError, ValueError):
        return _review_check_failed("receipt_outside_managed_temp")
    if resolved_receipt != receipt:
        return _review_check_failed("receipt_symlink")

    raw = _read_stable_private_receipt(receipt)
    payload = _load_unique_json_object(raw) if raw is not None else None
    if payload is None:
        return _review_check_failed("invalid_receipt")

    validation_error = review_receipt_validation_error(
        payload,
        operation_key=operation_key,
        repository=repository,
        pr_number=pr_number,
        head_sha=head_sha,
        logical_iteration=logical_iteration,
        post_state=post_state,
    )
    if validation_error is not None:
        return _review_check_failed(validation_error)
    return {"reviews_posted": "true", "sentinel": ""}


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


def clear_review_annotation_context() -> dict[str, str]:
    """Clear every captured annotation authority before a new publication attempt."""
    return {
        "annotated_diff_path": "",
        "hunk_ranges_path": "",
        "valid_lines_path": "",
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
