"""Review and PR-annotation helpers for smoke_utils sub-modules."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import UTC
from pathlib import Path
from typing import Any, TypeGuard

from autoskillit.core import (
    get_logger,
    is_valid_github_review_head_sha,
    is_valid_github_review_logical_iteration,
    is_valid_github_review_operation_key,
    is_valid_github_review_repository,
)

logger = get_logger(__name__)

_REVIEW_RECEIPT_MAX_BYTES = 1_048_576
_FINAL_REVIEW_STATES = frozenset({"SUCCEEDED", "RECONCILED"})
_FINAL_RECONCILIATION_RESULTS = frozenset({"NOT_NEEDED", "MATCHED", "ENRICHED"})
_REMOTE_FINDING_KINDS = frozenset({"POSTED", "ALREADY_PRESENT"})


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
    """Publish one snapshot-bound PR annotation bundle for review-pr."""
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
    review_root = Path(cwd)
    if not review_root.is_absolute():
        raise ValueError(f"cwd must be absolute, got {cwd!r}")
    review_root = review_root.resolve()
    out.mkdir(parents=True, exist_ok=True)
    annotated_path = out / f"annotated_diff_{pr_number}.txt"
    ranges_path = out / f"hunk_ranges_{pr_number}.json"
    valid_lines_path = out / f"valid_lines_{pr_number}.json"
    metrics_path = out / f"metrics_{pr_number}.json"
    metrics_path.unlink(missing_ok=True)

    try:
        local_rounds = int(local_review_rounds.strip()) if local_review_rounds.strip() else 0
    except ValueError:
        local_rounds = 0
    try:
        iteration = int(current_iteration.strip()) if current_iteration.strip() else 0
    except ValueError:
        iteration = 0
    review_mode = "local" if local_rounds > 0 and iteration < local_rounds else "github"

    def _stdout_bytes(result: subprocess.CompletedProcess[bytes]) -> bytes:
        return result.stdout

    def _run(args: list[str], *, timeout: int) -> subprocess.CompletedProcess[bytes]:
        result = subprocess.run(
            args,
            capture_output=True,
            text=False,
            check=False,
            cwd=str(review_root),
            timeout=timeout,
        )
        if result.returncode != 0:
            detail = result.stderr.decode("utf-8", errors="backslashreplace").strip()
            raise RuntimeError(f"annotation command failed ({' '.join(args)}): {detail}")
        return result

    def _required_scalar(args: list[str], *, timeout: int) -> str:
        value = _stdout_bytes(_run(args, timeout=timeout)).decode("utf-8", errors="strict").strip()
        if not is_valid_github_review_head_sha(value):
            raise RuntimeError(f"annotation command returned an invalid ref ({' '.join(args)})")
        return value

    def _read_pr_refs(*, required: bool) -> tuple[str, str] | None:
        result = subprocess.run(
            [
                "gh",
                "api",
                f"repos/{{owner}}/{{repo}}/pulls/{pr_number}",
                "--jq",
                "{headRefOid: .head.sha, baseRefOid: .base.sha}",
            ],
            capture_output=True,
            text=False,
            check=False,
            cwd=str(review_root),
            timeout=30,
        )
        if result.returncode != 0:
            if required:
                raise RuntimeError("unable to resolve live PR head/base refs")
            return None
        try:
            payload = json.loads(_stdout_bytes(result).decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("live PR head/base refs were malformed") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("live PR head/base refs were malformed")
        head_sha = payload.get("headRefOid")
        base_sha = payload.get("baseRefOid")
        if not isinstance(head_sha, str) or not is_valid_github_review_head_sha(head_sha.strip()):
            raise RuntimeError("live PR head ref was missing")
        if not isinstance(base_sha, str) or not is_valid_github_review_head_sha(base_sha.strip()):
            raise RuntimeError("live PR base ref was missing")
        return head_sha.strip(), base_sha.strip()

    try:
        merge_base_sha = ""
        if review_mode == "local" and not base_branch.strip():
            logger.warning(
                "local_review_mode_downgrade: base_branch empty, falling back to gh pr diff"
            )
            review_mode = "github"

        if review_mode == "local":
            head_sha = _required_scalar(["git", "rev-parse", "HEAD"], timeout=10)
            base_sha = _required_scalar(
                ["git", "rev-parse", base_branch.strip()],
                timeout=10,
            )
            live_refs = _read_pr_refs(required=False)
            if live_refs is not None and live_refs[1] != base_sha:
                raise RuntimeError(
                    "local base ref does not match the live PR baseRefOid authority"
                )
            merge_base_sha = _required_scalar(
                ["git", "merge-base", base_sha, head_sha],
                timeout=10,
            )
            diff_args = [
                "git",
                "diff",
                "--no-color",
                "--no-ext-diff",
                "--no-textconv",
                "--find-renames=50%",
                "--unified=3",
                merge_base_sha,
                head_sha,
            ]
            diff_bytes = _stdout_bytes(_run(diff_args, timeout=60))
            profile_id = "local_git_pinned_v1"
            diff_source = {
                "kind": "local_git",
                "comparison": "merge_base_to_head",
                "context_lines": 3,
                "rename_detection": "50%",
                "external_diff": False,
                "text_conversion": False,
                "profile_id": profile_id,
            }
        else:
            refs_before = _read_pr_refs(required=True)
            assert refs_before is not None
            head_sha, base_sha = refs_before
            diff_bytes = _stdout_bytes(_run(["gh", "pr", "diff", str(pr_number)], timeout=60))
            refs_after = _read_pr_refs(required=True)
            if refs_after != refs_before:
                raise RuntimeError("live PR head/base refs moved during diff acquisition")
            profile_id = "github_pr_diff_v1"
            diff_source = {
                "kind": "github_pr",
                "comparison": "pull_request",
                "context_lines": 3,
                "rename_detection": "provider_default",
                "external_diff": False,
                "text_conversion": False,
                "profile_id": profile_id,
            }

        diff_text = diff_bytes.decode("utf-8", errors="strict")
        annotated_text = f"# sha: {head_sha}\n{annotate_diff(diff_text)}"
        ranges_text = json.dumps(
            parse_hunk_ranges(diff_text),
            sort_keys=True,
            separators=(",", ":"),
        )
        valid_lines_text = json.dumps(
            extract_valid_lines(diff_text),
            sort_keys=True,
            separators=(",", ":"),
        )
        metrics = compute_diff_metrics(diff_text)
        loc_thresh = int(loc_threshold) if loc_threshold else 200
        file_thresh = int(file_threshold) if file_threshold else 5
        dispatch = select_review_agents(
            metrics,
            loc_threshold=loc_thresh,
            file_threshold=file_thresh,
        )
        diff_sha256 = hashlib.sha256(diff_bytes).hexdigest()

        def _artifact_record(path: Path, text: str) -> dict[str, str | int]:
            encoded = text.encode("utf-8")
            return {
                "basename": path.name,
                "sha256": hashlib.sha256(encoded).hexdigest(),
                "byte_length": len(encoded),
            }

        artifacts = {
            "annotated_diff": _artifact_record(annotated_path, annotated_text),
            "hunk_ranges": _artifact_record(ranges_path, ranges_text),
            "valid_lines": _artifact_record(valid_lines_path, valid_lines_text),
        }
        generation_material = json.dumps(
            {
                "head_sha": head_sha,
                "base_sha": base_sha,
                "merge_base_sha": merge_base_sha,
                "diff_sha256": diff_sha256,
                "profile": diff_source,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        metrics_data = {
            "_head_sha": head_sha,
            "_base_sha": base_sha,
            "_merge_base_sha": merge_base_sha,
            "review_mode": review_mode,
            "generation_id": hashlib.sha256(generation_material).hexdigest(),
            "diff_sha256": diff_sha256,
            "diff_byte_length": len(diff_bytes),
            "diff_source": diff_source,
            "artifacts": artifacts,
            "added_lines": metrics.added_lines,
            "removed_lines": metrics.removed_lines,
            "changed_files": metrics.changed_files,
            "file_paths": metrics.file_paths,
            "dispatch_agents": dispatch,
            "run_overengineering_audits": (metrics.added_lines + metrics.removed_lines) > 2000,
        }
        metrics_text = json.dumps(metrics_data, sort_keys=True, separators=(",", ":"))
        atomic_write(annotated_path, annotated_text)
        atomic_write(ranges_path, ranges_text)
        atomic_write(valid_lines_path, valid_lines_text)
        atomic_write(metrics_path, metrics_text)
    except Exception:
        metrics_path.unlink(missing_ok=True)
        raise

    return {
        "head_sha": head_sha,
        "pr_head_sha": head_sha,
        "review_mode": review_mode,
        "annotated_diff_path": str(annotated_path),
        "hunk_ranges_path": str(ranges_path),
        "valid_lines_path": str(valid_lines_path),
        "diff_metrics_path": str(metrics_path),
    }


# Verdicts exempt from local_review_rounds re-review.
# Verdicts in this set yield ``had_blocking=false`` unconditionally regardless
# of ``local_review_rounds``. ``approved_with_comments`` is exempt because its
# resolve pass is one-shot. ``needs_human`` is exempt because it indicates review
# was skipped (graceful degradation) and re-review would be pointless.
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


def _valid_finding_partition(
    payload: dict[str, Any],
    *,
    comment_ids: list[int],
) -> bool:
    count = payload.get("canonical_finding_count")
    dispositions = payload.get("finding_dispositions")
    if (
        not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        or not isinstance(dispositions, list)
        or len(dispositions) != count
    ):
        return False

    indexes: set[int] = set()
    disposition_remote_ids: set[int] = set()
    for disposition in dispositions:
        if not isinstance(disposition, dict):
            return False
        original_index = disposition.get("original_index")
        kind = disposition.get("kind")
        if (
            not isinstance(original_index, int)
            or isinstance(original_index, bool)
            or original_index < 0
            or original_index >= count
            or original_index in indexes
            or kind not in _REMOTE_FINDING_KINDS | {"OMITTED_INVALID"}
        ):
            return False
        indexes.add(original_index)
        remote_comment_id = disposition.get("remote_comment_id")
        if kind in _REMOTE_FINDING_KINDS:
            if (
                not _is_positive_int(remote_comment_id)
                or remote_comment_id in disposition_remote_ids
            ):
                return False
            disposition_remote_ids.add(remote_comment_id)
        elif (
            remote_comment_id is not None
            or not isinstance(disposition.get("reason"), str)
            or not disposition["reason"].strip()
        ):
            return False

    return indexes == set(range(count)) and disposition_remote_ids == set(comment_ids)


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
        or post_state not in _FINAL_REVIEW_STATES
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

    required_fields = {
        "schema_version",
        "operation_key",
        "repository",
        "pr_number",
        "head_sha",
        "logical_iteration",
        "state",
        "review_id",
        "comment_ids",
        "canonical_finding_count",
        "finding_dispositions",
        "reconciliation_result",
    }
    if not required_fields.issubset(payload):
        return _review_check_failed("incomplete_receipt")
    if (
        payload.get("schema_version") != 1
        or isinstance(payload.get("schema_version"), bool)
        or payload.get("operation_key") != operation_key
        or payload.get("repository") != repository
        or payload.get("pr_number") != pr_number
        or payload.get("head_sha") != head_sha
        or payload.get("logical_iteration") != logical_iteration
        or payload.get("state") != post_state
        or payload.get("state") not in _FINAL_REVIEW_STATES
        or payload.get("reconciliation_result") not in _FINAL_RECONCILIATION_RESULTS
        or payload.get("dry_run", False) is not False
        or not _is_positive_int(payload.get("review_id"))
    ):
        return _review_check_failed("receipt_identity_mismatch")

    comment_ids = payload.get("comment_ids")
    if (
        not isinstance(comment_ids, list)
        or any(not _is_positive_int(value) for value in comment_ids)
        or len(set(comment_ids)) != len(comment_ids)
        or not _valid_finding_partition(payload, comment_ids=comment_ids)
    ):
        return _review_check_failed("incomplete_finding_accounting")
    return {"reviews_posted": "true", "sentinel": ""}


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
    from autoskillit.core import atomic_write  # noqa: PLC0415

    _EMPTY = {"selected_lenses": "", "lens_context_paths": "", "dimensions_manifest_path": ""}

    out = Path(output_dir)
    if not out.is_absolute():
        raise ValueError(f"output_dir must be absolute, got {output_dir!r}")

    if not experiment_type.strip():
        return _EMPTY

    from autoskillit.recipe import get_experiment_type_by_name  # noqa: PLC0415

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

    from autoskillit.core import atomic_write  # noqa: PLC0415

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
