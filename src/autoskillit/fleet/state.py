"""Campaign state file management — DispatchRecord, atomic writes, resume algorithm.

Provides the single-file state format for fleet campaign execution.
All writes use core.io.atomic_write for crash-safety (tmp + os.replace).
"""

from __future__ import annotations

import dataclasses
import fcntl
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from types import TracebackType
    from typing import IO

from autoskillit.core import (
    DispatchIdentity,
    get_logger,
    read_versioned_json,
    write_versioned_json,
)
from autoskillit.fleet.state_gates import record_gate_outcome
from autoskillit.fleet.state_recovery import (
    find_completed_dispatch,
    has_blocking_dispatch,
    has_completed_dispatch,
    has_failed_dispatch,
    resume_campaign_from_state,
)
from autoskillit.fleet.state_types import (
    _ABANDON_REASONS,  # noqa: F401
    _ALLOWED_TRANSITIONS,  # noqa: F401
    _COMPLETED_STATUSES,  # noqa: F401
    _INFRASTRUCTURE_FAILURE_REASONS,  # noqa: F401
    _RETRY_IDENTITY_FIELDS,
    _VISIBLE_IN_BLOCK_STATUSES,  # noqa: F401
    FLEET_HALTED_SENTINEL,
    FLEET_STATE_SCHEMA_VERSION,
    TERMINAL_DISPATCH_STATUSES,
    TERMINAL_UNCLEANED_STATUSES,
    CampaignState,
    DispatchCompleted,
    DispatchRecord,
    DispatchRejected,
    DispatchResult,
    DispatchStatus,
    GateRecordResult,
    ResumeDecision,
    _resume_lock,
    _validate_transition,
)

__all__ = [
    # re-exported from state_gates
    "record_gate_outcome",
    # re-exported from state_recovery
    "find_completed_dispatch",
    "has_blocking_dispatch",
    "has_completed_dispatch",
    "has_failed_dispatch",
    "resume_campaign_from_state",
    # re-exported from state_types
    "FLEET_HALTED_SENTINEL",
    "TERMINAL_DISPATCH_STATUSES",
    "TERMINAL_UNCLEANED_STATUSES",
    "CampaignState",
    "DispatchCompleted",
    "DispatchRecord",
    "DispatchRejected",
    "DispatchResult",
    "DispatchStatus",
    "GateRecordResult",
    "ResumeDecision",
    # local
    "CampaignStateMutator",
    "DispatchStateHandle",
    "write_initial_state",
    "read_state",
    "mark_dispatch_running",
    "mark_dispatch_interrupted",
    "mark_dispatch_resumable",
    "reset_blocking_dispatch",
    "append_dispatch_record",
    "upsert_dispatch_record_by_name",
    "build_protected_campaign_ids",
    "write_captured_values",
    "read_all_campaign_captures",
    "update_orchestrator_session_id",
]

logger = get_logger(__name__)


def write_initial_state(
    state_path: Path,
    campaign_id: str,
    campaign_name: str,
    manifest_path: str,
    dispatches: list[DispatchRecord],
    recipe_snapshot: dict[str, Any] | None = None,
) -> None:
    """Create the campaign state file with all dispatches in pending status.

    Uses write_versioned_json for schema_version convention compliance.
    """
    payload = {
        "campaign_id": campaign_id,
        "campaign_name": campaign_name,
        "manifest_path": manifest_path,
        "started_at": time.time(),
        "dispatches": [d.to_dict() for d in dispatches],
        "recipe_snapshot": recipe_snapshot or {},
    }
    write_versioned_json(state_path, payload, schema_version=FLEET_STATE_SCHEMA_VERSION)


@dataclass(frozen=True, slots=True)
class DispatchStateHandle:
    """Proof that a per-dispatch state file exists.

    Cannot be constructed without verifying or creating the file.
    All state mutations in _run_dispatch use handle.state_path,
    which is guaranteed to point to an existing file.
    """

    state_path: Path
    identity: DispatchIdentity

    @classmethod
    def create_fresh(
        cls,
        dispatches_dir: Path,
        campaign_id: str,
        campaign_name: str,
        manifest_path: str,
        dispatches: list[DispatchRecord],
        recipe_snapshot: dict[str, Any] | None = None,
    ) -> DispatchStateHandle:
        identity = DispatchIdentity.fresh()
        state_path = dispatches_dir / f"{identity.dispatch_id}.json"
        write_initial_state(
            state_path, campaign_id, campaign_name, manifest_path, dispatches, recipe_snapshot
        )
        return cls(state_path=state_path, identity=identity)

    @classmethod
    def open_continued(
        cls,
        dispatches_dir: Path,
        prior_dispatch_id: str,
    ) -> DispatchStateHandle:
        identity = DispatchIdentity.from_dispatch_id(prior_dispatch_id)
        state_path = dispatches_dir / f"{identity.dispatch_id}.json"
        if not state_path.exists():
            raise FileNotFoundError(f"Cannot resume dispatch: state file missing at {state_path}")
        return cls(state_path=state_path, identity=identity)


def _clear_dispatch_for_retry(d: DispatchRecord) -> None:
    """Clear a dispatch record for retry."""
    _validate_transition(d.status, DispatchStatus.PENDING, d.name)

    # Snapshot all non-identity fields before resetting
    snapshot: dict[str, Any] = {}
    for f in dataclasses.fields(d):
        if f.name in _RETRY_IDENTITY_FIELDS:
            continue
        val = getattr(d, f.name)
        if f.name == "status":
            snapshot[f.name] = str(val)
        elif isinstance(val, dict):
            snapshot[f.name] = dict(val)
        else:
            snapshot[f.name] = val
    d.attempt_history.append(snapshot)

    # Reset all non-identity fields to their defaults
    for f in dataclasses.fields(d):
        if f.name in _RETRY_IDENTITY_FIELDS:
            continue
        default = (
            f.default_factory() if f.default_factory is not dataclasses.MISSING else f.default
        )
        if default is dataclasses.MISSING:
            raise RuntimeError(
                f"Field {f.name!r} has neither a default nor a default_factory; "
                "cannot reset to default for retry"
            )
        setattr(d, f.name, default)


def reset_blocking_dispatch(state_path: Path, dispatch_name: str) -> bool:
    """Reset a blocking dispatch (FAILURE, INTERRUPTED, or REFUSED) to PENDING.

    Returns True if the dispatch was found in a blocking state and reset,
    False if the dispatch was not found, not in a blocking state, or the
    state file is missing/corrupted. OSError raised by _write_state propagates
    to the caller — write failures are not silently converted to False.
    """
    with CampaignStateMutator(state_path) as m:
        if m.state is None:
            return False
        for d in m.state.dispatches:
            if d.name == dispatch_name and d.status in {
                DispatchStatus.FAILURE,
                DispatchStatus.INTERRUPTED,
                DispatchStatus.REFUSED,
            }:
                _clear_dispatch_for_retry(d)
                m.mark_dirty()
                return True
        return False


_LEGACY_SCHEMA_VERSIONS: frozenset[int] = frozenset({4, 5, 6, 7})


def _read_raw_json(state_path: Path) -> dict[str, Any] | None:
    import json as _json

    try:
        raw = _json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, _json.JSONDecodeError, OSError):
        return None
    return raw if isinstance(raw, dict) else None


def read_state(state_path: Path) -> CampaignState | None:
    """Load campaign state from disk.

    Returns None on missing file, malformed JSON, or schema mismatch.
    Accepts the current schema version and legacy versions in _LEGACY_SCHEMA_VERSIONS.
    Never raises.
    """
    data = read_versioned_json(state_path, FLEET_STATE_SCHEMA_VERSION, logger=logger)
    if data is None:
        raw = _read_raw_json(state_path)
        if raw is None or raw.get("schema_version") not in _LEGACY_SCHEMA_VERSIONS:
            return None
        data = raw
    try:
        dispatches = [DispatchRecord.from_dict(d) for d in data["dispatches"]]
        return CampaignState(
            campaign_id=data["campaign_id"],
            campaign_name=data["campaign_name"],
            manifest_path=data["manifest_path"],
            started_at=data["started_at"],
            dispatches=dispatches,
            captured_values=data.get("captured_values", {}),
            orchestrator_session_id=data.get("orchestrator_session_id") or "",
            ended_at=data.get("ended_at", 0.0),
            recipe_snapshot=data.get("recipe_snapshot", {}),
        )
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("read_state_corrupt_payload", path=str(state_path), exc=str(exc))
        return None


class CampaignStateMutator:
    """Context manager for exclusive fleet state mutation.

    Dual-layer lock: _resume_lock (intra-process threading) + fcntl.LOCK_EX
    on state_path.with_suffix(".lock") (cross-process). Reads state on enter,
    writes atomically on exit if dirty.
    """

    def __init__(self, state_path: Path) -> None:
        self._state_path = state_path
        self._lock_path = state_path.with_suffix(".lock")
        self._state: CampaignState | None = None
        self._flock_handle: IO[bytes] | None = None
        self._dirty: bool = False

    def __enter__(self) -> CampaignStateMutator:
        _resume_lock.acquire()
        try:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            # Lock files are intentionally not deleted after use. Safe cleanup after
            # fcntl release requires inode-comparison to avoid a TOCTOU race; the
            # files are empty and bounded to one per state file, so accumulation cost
            # is negligible compared to the complexity of safe deletion.
            fh = open(self._lock_path, "wb")
            try:
                fcntl.flock(fh, fcntl.LOCK_EX)
            except BaseException:
                fh.close()
                raise
            self._flock_handle = fh
            self._state = read_state(self._state_path)
            return self
        except BaseException:
            _resume_lock.release()
            raise

    @property
    def state(self) -> CampaignState | None:
        return self._state

    def mark_dirty(self) -> None:
        self._dirty = True

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        try:
            if self._dirty and self._state is not None and exc_type is None:
                try:
                    _write_state(self._state_path, self._state)
                except Exception:
                    logger.error(
                        "CampaignStateMutator.__exit__: _write_state failed for %s",
                        self._state_path,
                        exc_info=True,
                    )
                    raise
        finally:
            try:
                if self._flock_handle is not None:
                    try:
                        self._flock_handle.close()
                    except Exception:
                        logger.debug(
                            "CampaignStateMutator.__exit__: flock close failed", exc_info=True
                        )
            finally:
                _resume_lock.release()


def _write_state(state_path: Path, state: CampaignState) -> None:
    """Internal: atomic write of full state to disk."""
    payload = {
        "campaign_id": state.campaign_id,
        "campaign_name": state.campaign_name,
        "manifest_path": state.manifest_path,
        "started_at": state.started_at,
        "dispatches": [d.to_dict() for d in state.dispatches],
        "captured_values": state.captured_values,
        "orchestrator_session_id": state.orchestrator_session_id,
        "ended_at": state.ended_at,
        "recipe_snapshot": state.recipe_snapshot,
    }
    write_versioned_json(state_path, payload, schema_version=FLEET_STATE_SCHEMA_VERSION)


def mark_dispatch_running(
    state_path: Path,
    dispatch_name: str,
    *,
    dispatch_id: str,
    dispatched_pid: int,
    starttime_ticks: int = 0,
    boot_id: str = "",
    dispatched_create_time: float = 0.0,
    sidecar_path: str | None = None,
    identity_degraded: bool = False,
    issue_url: str = "",
) -> None:
    """Atomically mark a dispatch as running with its dispatch_id and dispatched_pid."""
    with CampaignStateMutator(state_path) as m:
        if m.state is None:
            raise FileNotFoundError(f"State file not found or corrupted: {state_path}")
        for d in m.state.dispatches:
            if d.name == dispatch_name:
                d.retry_reason = ""
                d.infra_exit_category = ""
                _validate_transition(d.status, DispatchStatus.RUNNING, d.name)
                was_resumable = d.status == DispatchStatus.RESUMABLE
                d.status = DispatchStatus.RUNNING
                if was_resumable:
                    d.resume_count += 1
                d.dispatch_id = dispatch_id
                d.dispatched_pid = dispatched_pid
                d.dispatched_starttime_ticks = starttime_ticks
                d.dispatched_boot_id = boot_id
                d.dispatched_create_time = dispatched_create_time
                d.identity_degraded = identity_degraded
                d.started_at = time.time()
                d.sidecar_path = sidecar_path
                d.issue_url = issue_url
                m.mark_dirty()
                return
        else:
            raise ValueError(f"Dispatch '{dispatch_name}' not found in state")


def mark_dispatch_interrupted(
    state_path: Path,
    dispatch_name: str,
    *,
    reason: str,
) -> None:
    """Atomically mark a dispatch as interrupted with a reason."""
    with CampaignStateMutator(state_path) as m:
        if m.state is None:
            raise FileNotFoundError(f"State file not found or corrupted: {state_path}")
        for d in m.state.dispatches:
            if d.name == dispatch_name:
                _validate_transition(d.status, DispatchStatus.INTERRUPTED, d.name)
                d.status = DispatchStatus.INTERRUPTED
                d.reason = reason
                d.ended_at = time.time()
                m.mark_dirty()
                return
        else:
            raise ValueError(f"Dispatch '{dispatch_name}' not found in state")


def mark_dispatch_resumable(
    state_path: Path,
    dispatch_name: str,
    *,
    sidecar_path: str,
) -> None:
    """Atomically transition a RUNNING dispatch to RESUMABLE, preserving the sidecar path."""
    with CampaignStateMutator(state_path) as m:
        if m.state is None:
            raise FileNotFoundError(f"State file not found or corrupted: {state_path}")
        for d in m.state.dispatches:
            if d.name == dispatch_name:
                _validate_transition(d.status, DispatchStatus.RESUMABLE, d.name)
                d.status = DispatchStatus.RESUMABLE
                d.sidecar_path = sidecar_path
                d.ended_at = time.time()
                m.mark_dirty()
                return
        else:
            raise ValueError(f"Dispatch '{dispatch_name}' not found in state")


def append_dispatch_record(
    state_path: Path,
    record: DispatchRecord,
) -> None:
    """Atomically append or replace a dispatch record by name.

    If a dispatch with the same name exists, it is replaced in-place.
    Otherwise the record is appended to the end.

    Thread-safe: uses _resume_lock + fcntl.LOCK_EX.
    """
    with CampaignStateMutator(state_path) as m:
        if m.state is None:
            raise FileNotFoundError(f"State file not found or corrupted: {state_path}")
        for i, d in enumerate(m.state.dispatches):
            if d.name == record.name:
                _validate_transition(d.status, record.status, d.name)
                m.state.dispatches[i] = record
                break
        else:
            m.state.dispatches.append(record)
        if (
            m.state.ended_at == 0.0
            and m.state.dispatches
            and all(d.status in TERMINAL_DISPATCH_STATUSES for d in m.state.dispatches)
        ):
            m.state.ended_at = time.time()
        m.mark_dirty()


def upsert_dispatch_record_by_name(state_path: Path, record: DispatchRecord) -> None:
    """Upsert a dispatch record by name without transition validation.

    Intended for external writes (e.g. from result envelopes) where the prior
    state is unknown and _validate_transition enforcement is not appropriate.
    If the state file is missing or corrupted, this is a no-op.

    Terminal-status protection: FAILURE→SUCCESS and SUCCESS→FAILURE overwrites are
    blocked — terminal status transitions must go through mark_dispatch_* state machine
    methods.
    """
    with CampaignStateMutator(state_path) as m:
        if m.state is None:
            return
        for i, d in enumerate(m.state.dispatches):
            if d.name == record.name:
                # Block terminal-status overwrites in both directions
                if d.status == DispatchStatus.FAILURE and record.status == DispatchStatus.SUCCESS:
                    raise ValueError(
                        f"Cannot overwrite FAILURE dispatch {record.name!r} with SUCCESS — "
                        f"use mark_dispatch_* state machine methods for valid transitions"
                    )
                if d.status == DispatchStatus.SUCCESS and record.status != DispatchStatus.SUCCESS:
                    raise ValueError(
                        f"Cannot overwrite SUCCESS dispatch {record.name!r} "
                        f"with {record.status!r} — "
                        f"use mark_dispatch_* state machine methods for valid transitions"
                    )
                # FAILURE-to-FAILURE: snapshot prior failure diagnostics before overwrite
                if (
                    d.status == DispatchStatus.FAILURE
                    and record.status == DispatchStatus.FAILURE
                    and d.reason
                ):
                    snapshot: dict[str, Any] = {}
                    for f in dataclasses.fields(d):
                        if f.name in _RETRY_IDENTITY_FIELDS:
                            continue
                        val = getattr(d, f.name)
                        if f.name == "status":
                            snapshot[f.name] = str(val)
                        elif isinstance(val, dict):
                            snapshot[f.name] = dict(val)
                        elif isinstance(val, list):
                            snapshot[f.name] = list(val)
                        else:
                            snapshot[f.name] = val
                    record.attempt_history = [snapshot] + list(record.attempt_history)
                if d.reaper_reason and not record.reaper_reason:
                    record.reaper_reason = d.reaper_reason
                if d.reaper_dispatch_id and not record.reaper_dispatch_id:
                    record.reaper_dispatch_id = d.reaper_dispatch_id
                m.state.dispatches[i] = record
                m.mark_dirty()
                return
        m.state.dispatches.append(record)
        m.mark_dirty()


def build_protected_campaign_ids(project_dir: Path) -> frozenset[str]:
    """Return campaign IDs with at least one non-terminal dispatch.

    Reads fleet state files from ``{project_dir}/.autoskillit/temp/dispatches/``.
    A campaign is protected if any of its dispatch records has a status that is NOT
    in the terminal set {success, failure, skipped, released}.
    Returns partially-accumulated results on unexpected errors rather than empty
    frozenset, so active campaigns processed before a failure are still protected.
    """
    protected: set[str] = set()
    try:
        dispatches_dir = project_dir / ".autoskillit" / "temp" / "dispatches"
        if not dispatches_dir.is_dir():
            return frozenset()
        for state_file in dispatches_dir.glob("*.json"):
            data = read_versioned_json(state_file, FLEET_STATE_SCHEMA_VERSION, logger=logger)
            if data is None:
                continue
            try:
                cid = data.get("campaign_id", "")
                if not cid:
                    continue
                dispatches = data.get("dispatches", [])
                if not dispatches:
                    protected.add(cid)
                    continue
                for record in dispatches:
                    status = record.get("status", "")
                    if status not in TERMINAL_DISPATCH_STATUSES:
                        protected.add(cid)
                        break
            except (KeyError, TypeError):
                continue
        return frozenset(protected)
    except Exception:
        logger.warning("campaign_ids_protection_error", exc_info=True)
        return frozenset(protected)


def write_captured_values(state_path: Path, captures: dict[str, str]) -> None:
    """Atomically merge new captures into an existing state file.

    Merges `captures` into the existing `captured_values` dict (new keys win).
    Raises FileNotFoundError if the state file does not exist.
    No-op if state file is corrupted (logs a warning).
    """
    if not state_path.exists():
        raise FileNotFoundError(f"write_captured_values: state file not found at {state_path}")
    with CampaignStateMutator(state_path) as m:
        if m.state is None:
            logger.warning("write_captured_values: state corrupt at %s", state_path)
            return
        m.state.captured_values = {**m.state.captured_values, **captures}
        m.mark_dirty()


def update_orchestrator_session_id(state_path: Path, session_id: str) -> None:
    """Persist the L3 orchestrator's Claude Code session ID to campaign state."""
    if not session_id:
        return
    with CampaignStateMutator(state_path) as m:
        if m.state is None:
            logger.warning(
                "update_orchestrator_session_id: state not found at %s",
                state_path,
            )
            return
        m.state.orchestrator_session_id = session_id
        m.mark_dirty()


def read_all_campaign_captures(
    dispatches_dir: Path,
    campaign_id: str,
) -> dict[str, str]:
    """Accumulate captured_values from all SUCCESS dispatches for a campaign.

    Scans all *.json files in `dispatches_dir`. For each file matching
    `campaign_id` where every dispatch record has status SUCCESS, merges
    its `captured_values` into the result. Later files win on key collision.
    """
    result: dict[str, str] = {}
    if not dispatches_dir.is_dir():
        return result
    entries: list[tuple[float, dict[str, str]]] = []
    for path in dispatches_dir.glob("*.json"):
        data = read_versioned_json(path, FLEET_STATE_SCHEMA_VERSION, logger=logger)
        if data is None:
            continue
        try:
            if data.get("campaign_id") != campaign_id:
                continue
            caps = data.get("captured_values", {})
            if not caps:
                continue
            dispatches = data.get("dispatches", [])
            all_success = all(d.get("status") == DispatchStatus.SUCCESS for d in dispatches)
            if all_success and dispatches:
                started = data.get("started_at")
                entries.append((float(started) if started is not None else 0.0, caps))
        except (KeyError, TypeError) as exc:
            logger.warning("read_all_campaign_captures: skipping %s: %s", path, exc)
            continue
    entries.sort(key=lambda e: e[0])
    for _, caps in entries:
        result.update(caps)
    return result


def normalize_dispatch_token_usage(raw: dict[str, Any]) -> dict[str, int]:
    """Map raw Claude session token keys to canonical DispatchTokenUsage key set.

    Idempotent: handles both raw keys (input_tokens/output_tokens) and canonical
    keys (input/output) so that double-normalization is safe. Canonical keys take
    priority when both are present.
    """
    return {
        "input": int(raw["input"] if "input" in raw else raw.get("input_tokens", 0)),
        "output": int(raw["output"] if "output" in raw else raw.get("output_tokens", 0)),
        "cache_creation": int(
            raw["cache_creation"]
            if "cache_creation" in raw
            else raw.get("cache_creation_input_tokens", 0)
        ),
        "cache_read": int(
            raw["cache_read"] if "cache_read" in raw else raw.get("cache_read_input_tokens", 0)
        ),
    }
