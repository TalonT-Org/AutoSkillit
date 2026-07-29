"""Recipe cmd externalization guards — counter guards and git workspace ops."""

from __future__ import annotations

import shutil
import subprocess
from datetime import date
from enum import StrEnum
from pathlib import Path

import regex as re

from autoskillit.core import (
    _MAX_ASSOCIATION_FILES,
    _PLAN_ASSOCIATION_DOMAIN,
    _PLAN_ASSOCIATION_KEYS,
    AUDIT_CYCLE_SCHEMA_VERSION,
    ArtifactRef,
    AuditCycleVerifier,
    AuditVerdict,
    atomic_write,
    compute_canonical_hash,
    decode_versioned_json_bytes,
    get_logger,
    is_generated_path,
    read_stable_contained_bytes,
    run_git,
)

logger = get_logger(__name__)


class _RegressionVerdict(StrEnum):
    POSSIBLE_REVERSION = "possible_reversion"


def compute_branch(
    issue_slug: str = "",
    run_name: str = "",
    issue_number: str = "",
) -> dict[str, str]:
    """Compute branch name from slug + issue or date."""
    prefix = issue_slug or run_name
    if issue_number:
        return {"branch_name": f"{prefix}/{issue_number}"}
    return {"branch_name": f"{prefix}/{date.today().strftime('%Y%m%d')}"}


def check_eject_limit(
    counter_file: str,
    max_ejects: str = "3",
) -> dict[str, str]:
    """Increment counter file; return EJECT_OK or EJECT_LIMIT_EXCEEDED."""
    max_ejects = max_ejects or "3"
    path = Path(counter_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        count = int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        count = 0
    count += 1
    atomic_write(path, str(count))
    status = "EJECT_LIMIT_EXCEEDED" if count > int(max_ejects) else "EJECT_OK"
    return {"status": status, "count": str(count)}


def check_dropped_healthy_loop(
    counter_file: str,
    max_drops: str = "2",
) -> dict[str, str]:
    """Increment dropped-healthy counter; return DROPPED_OK or DROPPED_LIMIT_EXCEEDED."""
    max_drops = max_drops or "2"
    path = Path(counter_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        count = int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        count = 0
    count += 1
    atomic_write(path, str(count))
    status = "DROPPED_LIMIT_EXCEEDED" if count > int(max_drops) else "DROPPED_OK"
    return {"status": status, "count": str(count)}


def check_dropped_ci_loop(
    counter_file: str,
    max_drops: str = "2",
) -> dict[str, str]:
    """Increment dropped-merge-group-CI counter; return DROPPED_CI_OK or DROPPED_CI_LIMIT_EXCEEDED.

    Mirrors ``check_dropped_healthy_loop`` for the ``dropped_merge_group_ci`` branch.
    On the first drop, the recipe re-enqueues (``DROPPED_CI_OK``). After the cap,
    the route escalates to ``diagnose_ci`` (``DROPPED_CI_LIMIT_EXCEEDED``) — the
    merge-group CI keeps failing, so a re-enqueue is unlikely to help.
    """
    try:
        limit = int(max_drops) if max_drops else 2
    except ValueError:
        limit = 2
    path = Path(counter_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        count = int(path.read_text().strip())
    except (FileNotFoundError, ValueError):
        count = 0
    count += 1
    atomic_write(path, str(count))
    status = "DROPPED_CI_LIMIT_EXCEEDED" if count >= limit else "DROPPED_CI_OK"
    return {"status": status, "count": str(count)}


def main_repo_guard(clone_path: str) -> dict[str, str]:
    """Stash dirty state from the main repo before merge.

    Primary path uses ``git stash --include-untracked``.  When stash
    fails (e.g. index corruption), falls back to destructive cleanup
    (``git checkout -- .`` + ``git clean -fd``).  In the fallback path,
    working-tree modifications are irrecoverable — the stash attempt
    already failed, so there is no preservation mechanism available.
    """
    result = run_git(["status", "--porcelain"], cwd=clone_path)
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, result.args, result.stdout, result.stderr
        )

    if not result.stdout.strip():
        return {"cleaned": "false"}

    # Detect and remove linked worktrees nested inside the clone.
    clone_resolved = Path(clone_path).resolve()
    wt_list = run_git(["worktree", "list", "--porcelain"], cwd=clone_path)
    if wt_list.returncode == 0:
        first = True
        for line in wt_list.stdout.splitlines():
            if not line.startswith("worktree "):
                continue
            if first:
                first = False
                continue  # main worktree is always first in porcelain output
            wt_path = Path(line.split(" ", 1)[1].strip())
            if wt_path.resolve().is_relative_to(clone_resolved):
                rm_result = run_git(
                    ["worktree", "remove", "--force", str(wt_path)], cwd=clone_path
                )
                if rm_result.returncode != 0 and wt_path.exists():
                    shutil.rmtree(wt_path, ignore_errors=True)

    stash_result = run_git(
        ["stash", "--include-untracked", "-m", "autoskillit: main_repo_guard pre-merge stash"],
        cwd=clone_path,
    )
    if stash_result.returncode != 0:
        logger.warning(
            "git stash failed (rc=%d) — falling back to force-clean: %s",
            stash_result.returncode,
            stash_result.stderr.strip(),
        )
        reset_result = run_git(["reset", "HEAD"], cwd=clone_path)
        if reset_result.returncode != 0:
            logger.warning(
                "git reset HEAD failed (rc=%d): %s",
                reset_result.returncode,
                reset_result.stderr.strip(),
            )
        co = run_git(["checkout", "--", "."], cwd=clone_path)
        if co.returncode != 0:
            logger.warning(
                "git checkout force-clean failed (rc=%d): %s",
                co.returncode,
                co.stderr.strip(),
            )
        cl = run_git(["clean", "-fd"], cwd=clone_path)
        if cl.returncode != 0:
            logger.warning(
                "git clean force-clean failed (rc=%d): %s",
                cl.returncode,
                cl.stderr.strip(),
            )
        if co.returncode != 0 and cl.returncode != 0:
            return {"cleaned": "failed"}
        verify = run_git(["status", "--porcelain"], cwd=clone_path)
        if verify.returncode == 0 and verify.stdout.strip():
            remaining = ", ".join(ln.strip() for ln in verify.stdout.splitlines() if ln.strip())[
                :200
            ]
            return {"cleaned": "failed", "remaining": remaining}
        return {"cleaned": "force"}

    verify = run_git(["status", "--porcelain"], cwd=clone_path)
    if verify.returncode == 0 and verify.stdout.strip():
        remaining = ", ".join(ln.strip() for ln in verify.stdout.splitlines() if ln.strip())[:200]
        return {"cleaned": "failed", "remaining": remaining}
    return {"cleaned": "true"}


def _normalize_plan_parts(plan_parts: str) -> list[str] | None:
    """Parse a capture-sourced plan_parts rendering into a list of absolute paths.

    Accepts every plausible capture-substitution rendering: bracket-wrapped list
    literals, newline- or comma-delimited path lists, with optional per-item
    quoting. Returns None if any non-empty item is not an absolute path.
    """
    s = plan_parts.strip()
    if len(s) >= 2 and s[0] == "[" and s[-1] == "]":
        s = s[1:-1]
    items: list[str] = []
    for raw in re.split(r"[\n,]", s):
        item = raw.strip()
        if len(item) >= 2 and item[0] == item[-1] and item[0] in "\"'":
            item = item[1:-1].strip()
        if not item:
            continue
        if not item.startswith("/"):
            return None
        items.append(item)
    return items


def _log_plan_disposition_rejection(
    reason: str,
    *,
    authority_path: Path,
    current_plan_path: Path,
    error: BaseException | None = None,
) -> None:
    logger.warning(
        "plan_disposition_validation_rejected",
        reason=reason,
        audit_cycle_path=str(authority_path),
        current_plan_path=str(current_plan_path),
        error=None if error is None else f"{type(error).__name__}: {error}",
        exc_info=error is not None,
    )


def _resolve_plan_disposition(*, audit_cycle_path: str, current_plan_path: Path) -> str | None:
    authority_path = Path(audit_cycle_path)
    if not authority_path.is_absolute() or not current_plan_path.is_absolute():
        return None
    cycle_dir = authority_path.parent
    try:
        authority = AuditCycleVerifier(cycle_dir).load_authority(authority_path)
    except (OSError, ValueError) as exc:
        _log_plan_disposition_rejection(
            "authority loading or validation failed",
            authority_path=authority_path,
            current_plan_path=current_plan_path,
            error=exc,
        )
        return None
    if authority.verdict is not AuditVerdict.NO_GO:
        return None

    try:
        plan_ref_candidates: list[ArtifactRef] = []
        associations_dir = cycle_dir / "associations"
        candidates = tuple(sorted(associations_dir.glob("*.json")))
        if len(candidates) > _MAX_ASSOCIATION_FILES:
            _log_plan_disposition_rejection(
                f"association file count exceeds {_MAX_ASSOCIATION_FILES}: {len(candidates)}",
                authority_path=authority_path,
                current_plan_path=current_plan_path,
            )
            return None
        records: list[tuple[Path, dict[str, object]]] = []
        invalid_candidate_count = 0
        for candidate in candidates:
            _, association_bytes = read_stable_contained_bytes(
                candidate,
                associations_dir,
                max_size_bytes=1_000_000,
            )
            raw = decode_versioned_json_bytes(
                association_bytes,
                expected_version=AUDIT_CYCLE_SCHEMA_VERSION,
                require_canonical=True,
            )
            if raw is None or frozenset(raw) != _PLAN_ASSOCIATION_KEYS:
                invalid_candidate_count += 1
                continue
            try:
                plan_ref = ArtifactRef.from_dict(raw["plan_ref"])
            except (TypeError, ValueError):
                invalid_candidate_count += 1
                continue
            if Path(plan_ref.locator) == current_plan_path:
                plan_ref_candidates.append(plan_ref)
                records.append((candidate, raw))
        if len(plan_ref_candidates) != 1 or len(records) != 1:
            _log_plan_disposition_rejection(
                "expected exactly one matching plan association; "
                f"matches={len(plan_ref_candidates)}, records={len(records)}, "
                f"invalid_candidates={invalid_candidate_count}, candidates={len(candidates)}",
                authority_path=authority_path,
                current_plan_path=current_plan_path,
            )
            return None

        plan_ref = plan_ref_candidates[0]
        association_path, association = records[0]
        if association_path.name != f"{plan_ref.content_digest}.json":
            _log_plan_disposition_rejection(
                "association filename does not match the plan content digest",
                authority_path=authority_path,
                current_plan_path=current_plan_path,
            )
            return None
        if association["schema_version"] != AUDIT_CYCLE_SCHEMA_VERSION:
            _log_plan_disposition_rejection(
                "association schema version does not match the audit-cycle schema",
                authority_path=authority_path,
                current_plan_path=current_plan_path,
            )
            return None
        if association["parent_authority_digest"] != authority.authority_digest:
            _log_plan_disposition_rejection(
                "association parent digest does not match the active authority",
                authority_path=authority_path,
                current_plan_path=current_plan_path,
            )
            return None
        association_payload = {
            key: value for key, value in association.items() if key != "association_digest"
        }
        if association["association_digest"] != compute_canonical_hash(
            association_payload,
            domain=_PLAN_ASSOCIATION_DOMAIN,
        ):
            _log_plan_disposition_rejection(
                "association digest does not attest its payload",
                authority_path=authority_path,
                current_plan_path=current_plan_path,
            )
            return None

        AuditCycleVerifier(current_plan_path.parent).verify_artifact_ref(plan_ref)
        disposition_data = association["disposition_ref"]
        if not isinstance(disposition_data, dict):
            _log_plan_disposition_rejection(
                "association disposition_ref is not an object",
                authority_path=authority_path,
                current_plan_path=current_plan_path,
            )
            return None
        disposition_ref = ArtifactRef.from_dict(disposition_data)
        cycle_verifier = AuditCycleVerifier(cycle_dir)
        cycle_verifier.verify_artifact_ref(disposition_ref)
        report = cycle_verifier.load_report(disposition_ref.locator)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        _log_plan_disposition_rejection(
            "association or disposition validation failed",
            authority_path=authority_path,
            current_plan_path=current_plan_path,
            error=exc,
        )
        return None

    identity_checks = {
        "execution_generation": report.execution_generation == authority.execution_generation,
        "cycle_id": report.cycle_id == authority.cycle_id,
        "plan_set_id": report.plan_set_id == authority.plan_set_id,
        "scope_id": report.scope_id == authority.scope_id,
        "part_id": report.part_id == authority.part_id,
        "audit_round": report.audit_round == authority.audit_round,
        "parent_authority_digest": (report.parent_authority_digest == authority.authority_digest),
        "inventory_digest": report.inventory_digest == authority.inventory_ref.content_digest,
        "findings_digest": report.findings_digest == authority.findings_digest,
        "current_plan_ref": report.current_plan_ref == plan_ref,
        "current_plan_path": Path(report.current_plan_ref.locator) == current_plan_path,
    }
    mismatched_fields = tuple(
        field_name for field_name, matches in identity_checks.items() if not matches
    )
    if mismatched_fields:
        _log_plan_disposition_rejection(
            f"disposition identity mismatch: {', '.join(mismatched_fields)}",
            authority_path=authority_path,
            current_plan_path=current_plan_path,
        )
        return None
    return disposition_ref.locator


def verify_plan_artifacts(
    plan_parts: str,
    audit_cycle_path: str = "",
) -> dict[str, str]:
    """Deterministically verify captured plan_parts artifacts for context-limit salvage.

    Verdict is 'salvaged' iff the normalized plan_parts list is non-empty and
    every listed path exists as a non-empty regular file; 'unsalvageable'
    otherwise. Under an explicit audit cycle, salvage additionally requires exactly
    one canonical plan-digest-keyed association matching the current NO GO
    authority, recovered plan, and disposition report. No latest-file discovery or
    report synthesis is permitted.
    """
    items = _normalize_plan_parts(plan_parts)
    if not items:
        return {"verdict": "unsalvageable"}
    for item in items:
        path = Path(item)
        try:
            if not path.is_file() or path.stat().st_size == 0:
                return {"verdict": "unsalvageable"}
        except OSError:
            return {"verdict": "unsalvageable"}
    result = {
        "verdict": "salvaged",
        "plan_parts": "\n".join(items),
        "plan_path": items[0],
    }
    if audit_cycle_path:
        disposition_path = _resolve_plan_disposition(
            audit_cycle_path=audit_cycle_path,
            current_plan_path=Path(items[0]),
        )
        if disposition_path is None:
            return {"verdict": "unsalvageable"}
        result["plan_disposition_path"] = disposition_path
    return result


def _count_numstat_net(numstat_output: str) -> int:
    """Sum net line changes (insertions - deletions) from git diff --numstat output."""
    total = 0
    for line in numstat_output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 2:
            continue
        try:
            total += int(parts[0]) - int(parts[1])
        except ValueError:
            continue  # binary files show "-"
    return total


def _parse_numstat_per_file(numstat_output: str) -> dict[str, int]:
    """Parse per-file net line changes from git diff --numstat output."""
    result: dict[str, int] = {}
    for line in numstat_output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        try:
            result[parts[2]] = int(parts[0]) - int(parts[1])
        except ValueError:
            continue
    return result


def _count_lines_in_files(worktree_path: str, files: list[str]) -> int:
    """Count total lines across the given files (for new-file accounting)."""
    total = 0
    for f in files:
        path = Path(worktree_path) / f
        if not path.is_file():
            continue
        try:
            with path.open("rb") as fh:
                total += sum(1 for _ in fh)
        except OSError:
            continue
    return total


def _check_regression(
    worktree_path: str,
    files_to_add: list[str],
    base_branch: str,
    file_status: dict[str, str],
) -> dict[str, str] | None:
    """Detect if pending changes would revert implementation commits.

    Returns a regression dict if the pending dirty files would net-revert
    more than 10 implementation lines, with corroborating per-file evidence
    that is not explained by content movement into new untracked files.
    Returns None to proceed normally.
    """
    mb = run_git(["merge-base", "HEAD", base_branch], cwd=worktree_path)
    if mb.returncode != 0:
        return None  # fresh repo, no merge-base
    merge_base = mb.stdout.strip()

    committed_diff = run_git(
        ["diff", "--numstat", merge_base, "HEAD", "--"] + files_to_add,
        cwd=worktree_path,
    )
    if committed_diff.returncode != 0:
        return None
    committed_net = _count_numstat_net(committed_diff.stdout)
    if committed_net <= 0:
        return None  # no implementation lines to protect

    wt_diff = run_git(
        ["diff", "--numstat", merge_base, "--"] + files_to_add,
        cwd=worktree_path,
    )
    if wt_diff.returncode != 0:
        return None
    wt_net = _count_numstat_net(wt_diff.stdout)

    regression_lines = committed_net - wt_net
    if regression_lines <= 10:
        return None  # within tolerance

    committed_per_file = _parse_numstat_per_file(committed_diff.stdout)
    wt_per_file = _parse_numstat_per_file(wt_diff.stdout)

    sources_with_regression: set[str] = set()
    for f in files_to_add:
        c_net = committed_per_file.get(f, 0)
        w_net = wt_per_file.get(f, 0)
        if c_net - w_net > 5:
            sources_with_regression.add(f)

    untracked_destinations: list[str] = []
    for f in files_to_add:
        if file_status.get(f) != "??":
            continue
        f_dir = str(Path(f).parent)
        for source in sources_with_regression:
            s_dir = str(Path(source).parent)
            if f_dir == s_dir or f.startswith(s_dir + "/") or s_dir == ".":
                untracked_destinations.append(f)
                break

    if untracked_destinations:
        new_file_lines = _count_lines_in_files(worktree_path, untracked_destinations)
        adjusted_regression = regression_lines - new_file_lines
        if adjusted_regression <= 10:
            return None  # CONTENT_MOVED: accounted for by new untracked files

    reverted_files: list[str] = []
    for f in files_to_add:
        c_net = committed_per_file.get(f, 0)
        w_net = wt_per_file.get(f, 0)
        if c_net - w_net > 5:
            reverted_files.append(f)

    if not reverted_files:
        return None  # CLEAR: no per-file reversion evidence

    return {
        "committed": "regression_detected",
        "verdict": _RegressionVerdict.POSSIBLE_REVERSION.value,
        "reverted_files": ", ".join(reverted_files),
        "implementation_net": str(committed_net),
        "working_tree_net": str(wt_net),
        "regression_lines": str(regression_lines),
    }


def commit_guard(worktree_path: str, base_branch: str = "") -> dict[str, str]:
    """Auto-commit pending changes if worktree is dirty, excluding generated files."""
    result = subprocess.run(
        ["git", "status", "--porcelain=v1", "-z", "-uall"],
        cwd=worktree_path,
        capture_output=True,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode, result.args, result.stdout, result.stderr
        )
    files_to_add: list[str] = []
    file_status: dict[str, str] = {}
    parts = result.stdout.decode("utf-8", errors="surrogateescape").split("\0")
    i = 0
    while i < len(parts):
        entry = parts[i]
        if len(entry) < 3:
            i += 1
            continue
        xy = entry[:2]
        path = entry[3:]
        if xy[0] in "RC":
            i += 1
        if path and not is_generated_path(path):
            files_to_add.append(path)
            file_status[path] = xy
        i += 1

    if not files_to_add:
        return {"committed": "false"}

    if base_branch:
        regression = _check_regression(worktree_path, files_to_add, base_branch, file_status)
        if regression is not None:
            return regression

    run_git(["add", "--", *files_to_add], cwd=worktree_path, check=True)
    run_git(
        ["commit", "-m", "chore: commit pending session changes"], cwd=worktree_path, check=True
    )
    return {"committed": "true"}
