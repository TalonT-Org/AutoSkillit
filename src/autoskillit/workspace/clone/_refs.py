"""Offline materialization and verification of clone-local base branches."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

from autoskillit.core import (
    ResolvedRef,
    local_branch_ref,
    remote_tracking_ref,
    verify_qualified_ref_sync,
)


@dataclass(frozen=True, slots=True)
class BaseBranchResolution:
    """Result of establishing the clone-local base-branch invariant."""

    resolved: ResolvedRef | None
    created_local_branch: bool
    ambiguous_refs: tuple[str, ...]
    failure_reason: str


def _run_git(
    clone_path: Path, argv: list[str], timeout: float
) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            argv,
            cwd=str(clone_path),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None


def _failure(
    reason: str,
    *,
    created_local_branch: bool = False,
    ambiguous_refs: tuple[str, ...] = (),
) -> BaseBranchResolution:
    return BaseBranchResolution(
        resolved=None,
        created_local_branch=created_local_branch,
        ambiguous_refs=ambiguous_refs,
        failure_reason=reason,
    )


def _tracking_candidate(
    clone_path: Path, base_branch: str, tracking_remote: str, timeout: float
) -> tuple[ResolvedRef | None, str]:
    if tracking_remote:
        candidate_ref = remote_tracking_ref(tracking_remote, base_branch)
        return verify_qualified_ref_sync(clone_path, candidate_ref, timeout=timeout), candidate_ref

    remotes_result = _run_git(clone_path, ["git", "remote"], timeout)
    if remotes_result is None:
        return None, "git remote could not be executed"
    if remotes_result.returncode != 0:
        return None, remotes_result.stderr.strip() or "git remote failed"

    remotes = {line.strip() for line in remotes_result.stdout.splitlines() if line.strip()}
    ordered_remotes = (["origin"] if "origin" in remotes else []) + sorted(remotes - {"origin"})
    attempted: list[str] = []
    for remote in ordered_remotes:
        candidate_ref = remote_tracking_ref(remote, base_branch)
        attempted.append(candidate_ref)
        resolved = verify_qualified_ref_sync(clone_path, candidate_ref, timeout=timeout)
        if resolved is not None:
            return resolved, candidate_ref
    return None, f"no tracking ref resolved; tried: {', '.join(attempted) or '(no remotes)'}"


def _find_shadowing_refs(
    clone_path: Path, base_branch: str, timeout: float
) -> tuple[tuple[str, ...], str]:
    found: list[str] = []
    for candidate in (f"refs/{base_branch}", f"refs/tags/{base_branch}"):
        result = _run_git(
            clone_path,
            ["git", "show-ref", "--verify", "--quiet", candidate],
            timeout,
        )
        if result is None:
            return (), f"could not inspect possible shadowing ref {candidate}"
        if result.returncode == 0:
            found.append(candidate)
        elif result.returncode != 1:
            return (), result.stderr.strip() or f"could not inspect {candidate}"
    return tuple(found), ""


def ensure_base_branch_local(
    clone_path: Path,
    base_branch: str,
    tracking_remote: str,
    *,
    timeout: float = 10.0,
) -> BaseBranchResolution:
    """Establish and prove that a bare base name resolves to its local branch."""
    local_ref = local_branch_ref(base_branch)
    local = verify_qualified_ref_sync(clone_path, local_ref, timeout=timeout)
    created_local_branch = False

    if local is None:
        tracking, tracking_detail = _tracking_candidate(
            clone_path, base_branch, tracking_remote, timeout
        )
        if tracking is None:
            return _failure(
                f"base branch {base_branch!r} did not resolve locally or as a tracking ref: "
                f"{tracking_detail}"
            )

        create_result = _run_git(
            clone_path,
            ["git", "branch", "--no-track", "--", base_branch, tracking.qualified_ref],
            timeout,
        )
        if create_result is None:
            return _failure(f"could not create local branch {base_branch!r}")
        if create_result.returncode != 0:
            return _failure(
                create_result.stderr.strip()
                or create_result.stdout.strip()
                or f"git branch failed for {base_branch!r}"
            )
        created_local_branch = True
        local = verify_qualified_ref_sync(clone_path, local_ref, timeout=timeout)
        if local is None:
            return _failure(
                f"created {local_ref} but could not resolve it",
                created_local_branch=True,
            )

    bare_result = _run_git(
        clone_path,
        [
            "git",
            "rev-parse",
            "--verify",
            "--end-of-options",
            f"{base_branch}^{{commit}}",
        ],
        timeout,
    )
    if bare_result is not None and bare_result.returncode == 0:
        if bare_result.stdout.strip() == local.sha:
            return BaseBranchResolution(
                resolved=local,
                created_local_branch=created_local_branch,
                ambiguous_refs=(),
                failure_reason="",
            )

    ambiguous_refs, probe_failure = _find_shadowing_refs(clone_path, base_branch, timeout)
    if probe_failure:
        return _failure(probe_failure, created_local_branch=created_local_branch)
    if ambiguous_refs:
        return _failure(
            f"bare ref {base_branch!r} is shadowed by {', '.join(ambiguous_refs)}",
            created_local_branch=created_local_branch,
            ambiguous_refs=ambiguous_refs,
        )
    bare_error = ""
    if bare_result is not None:
        bare_error = bare_result.stderr.strip()
    return _failure(
        bare_error or f"bare ref {base_branch!r} did not resolve to {local_ref}",
        created_local_branch=created_local_branch,
    )


__all__ = ["BaseBranchResolution", "ensure_base_branch_local"]
