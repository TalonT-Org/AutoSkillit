"""Review and PR-annotation helpers for smoke_utils sub-modules."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from autoskillit.core import (
    get_logger,
    is_valid_github_review_head_sha,
)

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
    *,
    mode: str | None = None,
) -> dict[str, str]:
    """Publish one snapshot-bound PR annotation bundle for review-pr."""
    import subprocess  # noqa: PLC0415

    from autoskillit.core import atomic_write, parse_github_repo  # noqa: PLC0415
    from autoskillit.execution import (
        annotate_diff,
        compute_diff_metrics,
        extract_valid_lines,
        parse_hunk_ranges,
        select_review_agents,
    )  # noqa: PLC0415

    if mode not in (None, "local", "github"):
        raise ValueError(f"invalid review mode: {mode!r}")
    if mode == "local" and not base_branch.strip():
        raise ValueError("explicit local review mode requires base_branch")
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
    derived_review_mode = "local" if local_rounds > 0 and iteration < local_rounds else "github"
    selected_review_mode = mode if mode is not None else derived_review_mode

    def _stdout_bytes(result: subprocess.CompletedProcess[bytes]) -> bytes:
        return result.stdout

    def _run(
        args: list[str], *, timeout: int, ok_returncodes: frozenset[int] = frozenset({0})
    ) -> subprocess.CompletedProcess[bytes]:
        result = subprocess.run(
            args,
            capture_output=True,
            text=False,
            check=False,
            cwd=str(review_root),
            timeout=timeout,
        )
        if result.returncode not in ok_returncodes:
            detail = result.stderr.decode("utf-8", errors="backslashreplace").strip()
            raise RuntimeError(
                f"annotation command failed ({' '.join(args)}, rc={result.returncode}): {detail}"
            )
        return result

    def _required_scalar(args: list[str], *, timeout: int) -> str:
        value = _stdout_bytes(_run(args, timeout=timeout)).decode("utf-8", errors="strict").strip()
        if not is_valid_github_review_head_sha(value):
            raise RuntimeError(f"annotation command returned an invalid ref ({' '.join(args)})")
        return value

    def _read_pr_refs() -> tuple[str, str]:
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
            raise RuntimeError("unable to resolve live PR head/base refs")
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

    def _read_provider_authority() -> tuple[str, str, str, str]:
        result = _run(
            [
                "gh",
                "api",
                f"repos/{{owner}}/{{repo}}/pulls/{pr_number}",
                "--jq",
                (
                    "{headRefOid:.head.sha,baseRefOid:.base.sha,"
                    "baseRepoFullName:.base.repo.full_name}"
                ),
            ],
            timeout=30,
        )
        try:
            payload = json.loads(_stdout_bytes(result).decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("provider PR authority was malformed") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("provider PR authority was malformed")
        provider_head_sha = payload.get("headRefOid")
        provider_base_snapshot_sha = payload.get("baseRefOid")
        provider_base_repo_full_name = payload.get("baseRepoFullName")
        if not isinstance(provider_head_sha, str) or not is_valid_github_review_head_sha(
            provider_head_sha.strip()
        ):
            raise RuntimeError(f"provider PR head authority was missing: {provider_head_sha!r}")
        if not isinstance(provider_base_snapshot_sha, str) or not is_valid_github_review_head_sha(
            provider_base_snapshot_sha.strip()
        ):
            raise RuntimeError(f"base authority was missing: {provider_base_snapshot_sha!r}")
        if (
            not isinstance(provider_base_repo_full_name, str)
            or len(provider_base_repo_full_name.strip().split("/")) != 2
        ):
            raise RuntimeError(f"provider base repo was missing: {provider_base_repo_full_name!r}")
        provider_head_sha = provider_head_sha.strip()
        provider_base_snapshot_sha = provider_base_snapshot_sha.strip()
        provider_base_repo_full_name = provider_base_repo_full_name.strip()
        compare = _run(
            [
                "gh",
                "api",
                (
                    f"repos/{provider_base_repo_full_name}/compare/"
                    f"{provider_base_snapshot_sha}...{provider_head_sha}"
                ),
                "--jq",
                "{mergeBaseOid:.merge_base_commit.sha}",
            ],
            timeout=30,
        )
        try:
            compare_payload = json.loads(_stdout_bytes(compare).decode("utf-8", errors="strict"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("provider merge base authority was malformed") from exc
        provider_merge_base_sha = (
            compare_payload.get("mergeBaseOid") if isinstance(compare_payload, dict) else None
        )
        if not isinstance(provider_merge_base_sha, str) or not is_valid_github_review_head_sha(
            provider_merge_base_sha.strip()
        ):
            raise RuntimeError(f"merge base authority was missing: {provider_merge_base_sha!r}")
        return (
            provider_head_sha,
            provider_base_snapshot_sha,
            provider_merge_base_sha.strip(),
            provider_base_repo_full_name,
        )

    def _ensure_provider_objects(shas: tuple[str, ...], repository: str) -> None:
        missing: list[str] = []
        # dict.fromkeys preserves order while deduping; iteration order matters
        # because we want the first occurrence of each sha to drive the
        # fetch loop below (avoiding duplicate fetch invocations).
        for sha in dict.fromkeys(shas):
            try:
                _run(
                    ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                    timeout=10,
                )
            except RuntimeError:
                missing.append(sha)
        if not missing:
            return
        remote_names = _stdout_bytes(_run(["git", "remote"], timeout=10)).decode(
            "utf-8", errors="strict"
        )
        matches: list[str] = []
        for remote_name in remote_names.splitlines():
            remote_name = remote_name.strip()
            if not remote_name:
                continue
            remote_url = (
                _stdout_bytes(_run(["git", "remote", "get-url", remote_name], timeout=10))
                .decode("utf-8", errors="strict")
                .strip()
            )
            parsed_repository = parse_github_repo(remote_url)
            if (
                parsed_repository is not None
                and parsed_repository.casefold() == repository.casefold()
            ):
                matches.append(remote_name)
        if len(matches) != 1:
            raise RuntimeError(
                f"expected one canonical remote for {repository}, found {len(matches)}"
            )
        for sha in missing:
            _run(
                ["git", "fetch", "--no-write-fetch-head", matches[0], sha],
                timeout=60,
            )

    merge_base_sha = ""
    provider_base_repo_full_name = ""
    if selected_review_mode == "local" and not base_branch.strip():
        logger.warning(
            "local_review_mode_downgrade: base_branch empty, falling back to gh pr diff"
        )
        selected_review_mode = "github"

    if selected_review_mode == "local":
        checkout_head_sha = _required_scalar(["git", "rev-parse", "HEAD"], timeout=10)
        provider_authority = _read_provider_authority()
        (
            head_sha,
            base_sha,
            provider_merge_base_sha,
            provider_base_repo_full_name,
        ) = provider_authority
        if checkout_head_sha != head_sha:
            raise RuntimeError("checkout head does not match provider head authority")
        _ensure_provider_objects((base_sha, provider_merge_base_sha), provider_base_repo_full_name)
        local_computed_merge_base_sha = _required_scalar(
            ["git", "merge-base", base_sha, checkout_head_sha],
            timeout=10,
        )
        if local_computed_merge_base_sha != provider_merge_base_sha:
            raise RuntimeError("local and provider merge base authorities disagree")
        merge_base_sha = provider_merge_base_sha
        diff_args = [
            "git",
            "diff",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--find-renames=50%",
            "--unified=3",
            merge_base_sha,
            checkout_head_sha,
        ]
        diff_bytes = _stdout_bytes(_run(diff_args, timeout=60))
        if _read_provider_authority() != provider_authority:
            raise RuntimeError("provider PR authority moved during diff acquisition")
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
        refs_before = _read_pr_refs()
        head_sha, base_sha = refs_before
        diff_bytes = _stdout_bytes(_run(["gh", "pr", "diff", str(pr_number)], timeout=60))
        refs_after = _read_pr_refs()
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
            "base_repo_full_name": provider_base_repo_full_name,
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
        "_base_repo_full_name": provider_base_repo_full_name,
        "review_mode": selected_review_mode,
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
    _writes_succeeded = False
    try:
        atomic_write(annotated_path, annotated_text)
        atomic_write(ranges_path, ranges_text)
        atomic_write(valid_lines_path, valid_lines_text)
        atomic_write(metrics_path, metrics_text)
        _writes_succeeded = True
    finally:
        if not _writes_succeeded:
            metrics_path.unlink(missing_ok=True)

    return {
        "head_sha": head_sha,
        "pr_head_sha": head_sha,
        "review_mode": selected_review_mode,
        "annotated_diff_path": str(annotated_path),
        "hunk_ranges_path": str(ranges_path),
        "valid_lines_path": str(valid_lines_path),
        "diff_metrics_path": str(metrics_path),
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
