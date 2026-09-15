"""Session-log directory retention — apply_session_retention().

Extracted out of session_log.py's flush_session_log() (S2-2) so
tests/_retention_surface.py's reclamation retention-decision registry can target
this exact unit, rather than the ~600 unrelated lines around it in
flush_session_log, and so session_log.py stays under its 750-line warning-zone
budget (tests/arch/test_file_size_budgets.py).
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autoskillit.core import (
    ARTIFACT_LEASE_TIMEOUT_SECONDS,
    VANISHED_ERRORS,
    ArtifactLease,
    atomic_write,
    fast_dumps,
    fast_loads,
    get_logger,
    scan_observed,
)

logger = get_logger(__name__)

_MAX_SESSIONS = 2000
_CLEAR_MARKER_FILENAME = ".telemetry_cleared_at"


def write_telemetry_clear_marker(log_root: Path) -> None:
    """Write the current UTC timestamp as a telemetry-clear fence."""
    try:
        log_root = Path(log_root)
        log_root.mkdir(parents=True, exist_ok=True)
        atomic_write(log_root / _CLEAR_MARKER_FILENAME, datetime.now(UTC).isoformat())
    except (OSError, ValueError, TypeError) as exc:
        # Narrow catch for filesystem ops and atomic_write's known exception set;
        # broader ``Exception`` would mask programmatic bugs (AttributeError, KeyError)
        # as routine retention failures.
        logger.debug(
            "write_telemetry_clear_marker failed",
            error=str(exc),
            error_type=type(exc).__name__,
            exc_info=True,
        )


def read_telemetry_clear_marker(log_root: Path) -> datetime | None:
    """Read the persisted telemetry-clear timestamp, or None if absent/corrupt.

    ``write_telemetry_clear_marker`` always writes a UTC-aware timestamp, but
    ``datetime.fromisoformat`` returns a naive datetime for any source string
    that lacks a timezone offset (e.g. a hand-edited or older-format marker
    file). Consumers compare the result against UTC-aware timestamps, so
    naive values are normalized to UTC here, mirroring
    ``quota_constraints.normalize_naive_utc``.
    """
    try:
        text = (Path(log_root) / _CLEAR_MARKER_FILENAME).read_text(encoding="utf-8").strip()
        parsed = datetime.fromisoformat(text)
    except (OSError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def apply_session_retention(
    sessions_dir: Path,
    *,
    max_sessions: int | None,
    dir_name: str,
    reuse_committed_recovery: bool,
    protected_ids: frozenset[str],
) -> set[str]:
    """Delete committed session dirs beyond the retention window; return survivors.

    A survivor is either not expired, or expired but protected (the just-
    recommitted crash-recovery dir, a campaign-protected id, or a dir whose
    deletion itself failed).
    """
    committed_dirs = sorted(
        (
            entry
            for entry in scan_observed(sessions_dir)
            if entry.is_dir and (entry.path / "summary.json").is_file()
        ),
        key=lambda entry: entry.status.st_mtime_ns,
    )
    effective_max_sessions = max_sessions if max_sessions is not None else _MAX_SESSIONS
    expired = committed_dirs[: max(0, len(committed_dirs) - effective_max_sessions)]
    surviving_names = {entry.name for entry in committed_dirs[len(expired) :]}
    for entry in expired:
        if reuse_committed_recovery and entry.name == dir_name:
            surviving_names.add(entry.name)
            continue
        if protected_ids:
            try:
                meta = json.loads((entry.path / "meta.json").read_text(encoding="utf-8"))
            except VANISHED_ERRORS:
                meta = {}
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning(
                    "session_retention_meta_read_failed",
                    path=entry.path,
                    error=str(exc),
                    exc_info=True,
                )
                meta = {}
            if meta.get("campaign_id") in protected_ids:
                surviving_names.add(entry.name)
                continue
        try:
            shutil.rmtree(entry.path)
        except OSError:
            logger.warning("session_retention_delete_failed", path=entry.path, exc_info=True)
            surviving_names.add(entry.name)
    return surviving_names


def apply_execution_candidate_manifest_retention(
    manifests_dir: Path,
    *,
    max_sessions: int | None,
    current_manifest_name: str = "",
    protected_ids: frozenset[str],
    clear_marker: datetime | None,
) -> set[str]:
    """Retain candidate manifests that still describe a live selection.

    Completed manifests and pending manifests whose deadline has elapsed are
    eligible for the normal size window. A clear marker makes pre-clear
    manifests eligible immediately. The selection being written, manifests for
    protected campaigns, and pending selections remain durable evidence.
    """
    manifests = sorted(
        (
            entry
            for entry in scan_observed(manifests_dir)
            if not entry.is_dir and not entry.is_symlink and entry.path.suffix == ".json"
        ),
        key=lambda entry: entry.status.st_mtime_ns,
    )
    effective_max_sessions = max_sessions if max_sessions is not None else _MAX_SESSIONS
    current_time = time.time()
    clear_epoch = clear_marker.timestamp() if clear_marker is not None else None

    protected_names: set[str] = {current_manifest_name} if current_manifest_name else set()
    for entry in manifests:
        if entry.name in protected_names:
            continue
        try:
            payload = json.loads(entry.path.read_text(encoding="utf-8"))
        except VANISHED_ERRORS:
            continue
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(
                "execution_candidate_manifest_read_failed",
                path=entry.path,
                error=str(exc),
                exc_info=True,
            )
            continue
        campaign_id = payload.get("campaign_id")
        if isinstance(campaign_id, str) and campaign_id in protected_ids:
            protected_names.add(entry.name)
            continue
        deadline = payload.get("invocation_deadline_epoch")
        completed = payload.get("completed") is True
        if not completed and isinstance(deadline, int) and deadline > current_time:
            protected_names.add(entry.name)

    retained = list(manifests)
    for entry in tuple(retained):
        if clear_epoch is None or entry.status.st_mtime > clear_epoch:
            continue
        if entry.name in protected_names:
            continue
        try:
            entry.path.unlink()
        except VANISHED_ERRORS:
            retained.remove(entry)
        except OSError:
            logger.warning(
                "execution_candidate_manifest_delete_failed", path=entry.path, exc_info=True
            )
            protected_names.add(entry.name)
        else:
            retained.remove(entry)

    for entry in tuple(retained):
        if len(retained) <= effective_max_sessions:
            break
        if entry.name in protected_names:
            continue
        try:
            entry.path.unlink()
        except VANISHED_ERRORS:
            retained.remove(entry)
        except OSError:
            logger.warning(
                "execution_candidate_manifest_delete_failed", path=entry.path, exc_info=True
            )
            protected_names.add(entry.name)
        else:
            retained.remove(entry)
    return {entry.name for entry in retained}


def _execution_candidate_manifest_path(log_root: Path, manifest_ref: str) -> Path:
    manifests_dir = log_root / "execution-candidates"
    manifest_path = log_root / manifest_ref
    if manifest_path.parent != manifests_dir or manifest_path.suffix != ".json":
        raise ValueError(f"Invalid execution candidate manifest reference: {manifest_ref!r}")
    return manifest_path


def _execution_candidate_manifest_lock_path(log_root: Path) -> Path:
    return log_root / ".locks" / "execution-candidates.lock"


def write_execution_candidate_manifest_at_root(
    selection: Any,
    log_root: Path,
    *,
    max_sessions: int | None,
    protected_ids: frozenset[str],
) -> str:
    """Atomically publish selection evidence before candidate execution starts."""
    selection_id = selection.selection_id
    manifest_ref = selection.manifest_ref
    if not selection_id or not manifest_ref:
        return ""
    manifest_path = _execution_candidate_manifest_path(log_root, manifest_ref)
    lease = ArtifactLease.acquire_exclusive(
        _execution_candidate_manifest_lock_path(log_root),
        timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
    )
    with lease:
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(
            manifest_path,
            fast_dumps(selection.to_payload(), sort_keys=True, indent=True) + "\n",
        )
        apply_execution_candidate_manifest_retention(
            manifest_path.parent,
            max_sessions=max_sessions,
            current_manifest_name=manifest_path.name,
            protected_ids=protected_ids,
            clear_marker=read_telemetry_clear_marker(log_root),
        )
    return manifest_ref


def prune_execution_candidate_manifests_at_root(
    log_root: Path,
    *,
    max_sessions: int | None,
    protected_ids: frozenset[str],
) -> set[str]:
    """Apply candidate-manifest retention outside a session flush."""
    manifests_dir = log_root / "execution-candidates"
    if not manifests_dir.is_dir():
        return set()
    lease = ArtifactLease.acquire_exclusive(
        _execution_candidate_manifest_lock_path(log_root),
        timeout=ARTIFACT_LEASE_TIMEOUT_SECONDS,
    )
    with lease:
        return apply_execution_candidate_manifest_retention(
            manifests_dir,
            max_sessions=max_sessions,
            protected_ids=protected_ids,
            clear_marker=read_telemetry_clear_marker(log_root),
        )


def refresh_summary_child_outcomes(
    summary_path: Path, child_outcomes: Sequence[Mapping[str, Any]]
) -> None:
    """Patch only ``child_outcomes`` into an already-committed summary.json.

    Reused recovery (``reuse_committed_recovery``) skips the full artifact
    rewrite in ``flush_session_log``, but later, more precise
    terminal-reason evidence (issue #4623) must still reach the committed
    summary — every other already-published field is preserved.
    """
    try:
        committed = fast_loads(summary_path.read_text(encoding="utf-8"))
        if child_outcomes and committed.get("child_outcomes") != child_outcomes:
            committed["child_outcomes"] = child_outcomes
            atomic_write(summary_path, fast_dumps(committed, sort_keys=True, indent=True) + "\n")
    except (OSError, ValueError):
        logger.warning("summary_child_outcomes_refresh_failed", path=summary_path, exc_info=True)
