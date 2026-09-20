"""Fleet campaign state records with atomic crash-safe writes and resume support."""

from __future__ import annotations

import dataclasses
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any

from autoskillit.core import (
    DispatchIdentity,
    SerializedTokenMeasure,
    TokenMeasure,
    get_logger,
    read_versioned_json,
    resolve_provider_used,
    write_versioned_json,
)
from autoskillit.fleet.campaign_state._state_lock import CampaignStateMutatorOwnership
from autoskillit.fleet.campaign_state.state_effects import (
    DispatchAggregatePhase,
    DispatchEffectName,
    DispatchEffectPhase,
    DispatchEffectProvenance,
    DispatchEffectRecord,
    DispatchProvenanceTracker,
    DispatchRetryDisposition,
)
from autoskillit.fleet.campaign_state.state_gates import record_gate_outcome
from autoskillit.fleet.campaign_state.state_outcomes import (
    DispatchCompleted,
    DispatchRejected,
    DispatchResult,
    GateRecordResult,
)
from autoskillit.fleet.campaign_state.state_records import (
    _RETRY_IDENTITY_FIELDS,
    FLEET_HALTED_SENTINEL,
    FLEET_STATE_SCHEMA_VERSION,
    CampaignState,
    DispatchRecord,
    ResumeDecision,
    _clear_dispatch_for_retry,
)
from autoskillit.fleet.campaign_state.state_recovery import (
    find_completed_dispatch,
    has_blocking_dispatch,
    has_completed_dispatch,
    has_failed_dispatch,
    resume_campaign_from_state,
)
from autoskillit.fleet.campaign_state.state_transitions import (
    TERMINAL_DISPATCH_STATUSES,
    TERMINAL_UNCLEANED_STATUSES,
    DispatchStatus,
    _validate_transition,
)

_resume_lock = threading.Lock()
_FLEET_STATE_LOCK_TIMEOUT_SECONDS = 2.0

__all__ = [
    # re-exported from state_gates
    "record_gate_outcome",
    # re-exported from state_recovery
    "find_completed_dispatch",
    "has_blocking_dispatch",
    "has_completed_dispatch",
    "has_failed_dispatch",
    "resume_campaign_from_state",
    # re-exported from state_effects
    "DispatchAggregatePhase",
    "DispatchEffectName",
    "DispatchEffectPhase",
    "DispatchEffectProvenance",
    "DispatchEffectRecord",
    "DispatchProvenanceTracker",
    "DispatchRetryDisposition",
    # re-exported from state_outcomes
    "DispatchCompleted",
    "DispatchRejected",
    "DispatchResult",
    "GateRecordResult",
    # re-exported from state_records
    "FLEET_HALTED_SENTINEL",
    "CampaignState",
    "DispatchRecord",
    "ResumeDecision",
    # re-exported from state_transitions
    "TERMINAL_DISPATCH_STATUSES",
    "TERMINAL_UNCLEANED_STATUSES",
    "DispatchStatus",
    # local
    "CampaignStateMutator",
    "DispatchStateHandle",
    "write_initial_state",
    "read_state",
    "mark_dispatch_running",
    "mark_dispatch_interrupted",
    "mark_dispatch_session_identity",
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


_LEGACY_SCHEMA_VERSIONS: frozenset[int] = frozenset({4, 5, 6, 7, 8, 9, 10, 11, 12})


def read_fleet_state_payload(state_path: Path) -> dict[str, Any] | None:
    """Read a current or supported legacy fleet-state payload."""
    if (
        data := read_versioned_json(state_path, FLEET_STATE_SCHEMA_VERSION, logger=logger)
    ) is not None:
        return data
    import json as _json

    try:
        legacy = _json.loads(state_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, _json.JSONDecodeError, OSError):
        return None
    if isinstance(legacy, dict):
        observed_version = legacy.get("schema_version")
        if isinstance(observed_version, int) and observed_version > FLEET_STATE_SCHEMA_VERSION:
            logger.warning(
                "fleet_state_unsupported_future",
                path=str(state_path),
                observed_version=observed_version,
                current_version=FLEET_STATE_SCHEMA_VERSION,
            )
            return None
    if isinstance(legacy, dict) and legacy.get("schema_version") in _LEGACY_SCHEMA_VERSIONS:
        return legacy
    return None


def read_state(state_path: Path) -> CampaignState | None:
    """Load campaign state from disk.

    Returns None on missing file, malformed JSON, or schema mismatch.
    Accepts the current schema version and legacy versions in _LEGACY_SCHEMA_VERSIONS.
    Dispatch records with unsupported persisted values are quarantined and round-tripped.
    Never raises.
    """
    data = read_fleet_state_payload(state_path)
    if data is None:
        return None
    try:
        raw_dispatches = data["dispatches"]
        if not isinstance(raw_dispatches, list):
            raise TypeError("dispatches must be a list")
        campaign = CampaignState(
            campaign_id=data["campaign_id"],
            campaign_name=data["campaign_name"],
            manifest_path=data["manifest_path"],
            started_at=data["started_at"],
            captured_values=data.get("captured_values", {}),
            orchestrator_session_id=data.get("orchestrator_session_id") or "",
            ended_at=data.get("ended_at", 0.0),
            recipe_snapshot=data.get("recipe_snapshot", {}),
        )
    except (KeyError, ValueError, TypeError) as exc:
        logger.warning("read_state_corrupt_payload", path=str(state_path), exc=str(exc))
        return None

    for raw_dispatch in raw_dispatches:
        try:
            if not isinstance(raw_dispatch, dict):
                raise TypeError("dispatch record must be an object")
            if raw_dispatch.get("token_usage"):
                raw_dispatch = {
                    **raw_dispatch,
                    "token_usage": normalize_dispatch_token_usage(
                        raw_dispatch["token_usage"],
                        backend=raw_dispatch.get("backend_name", ""),
                        legacy=data.get("schema_version") in _LEGACY_SCHEMA_VERSIONS,
                    ),
                }
            dispatch = DispatchRecord.from_dict(raw_dispatch)
        except (KeyError, ValueError, TypeError):
            campaign.opaque_dispatches.append(raw_dispatch)
            continue
        if dispatch.status == DispatchStatus.UNKNOWN:
            campaign.opaque_dispatches.append(raw_dispatch)
            continue
        campaign.dispatches.append(dispatch)
    return campaign


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
        self._ownership: CampaignStateMutatorOwnership | None = None
        self._dirty: bool = False

    def __enter__(self) -> CampaignStateMutator:
        ownership = CampaignStateMutatorOwnership()
        try:
            ownership.acquire(
                self._lock_path,
                process_lock=_resume_lock,
                timeout=_FLEET_STATE_LOCK_TIMEOUT_SECONDS,
            )
            self._ownership = ownership
            self._state = read_state(self._state_path)
            return self
        except BaseException:
            ownership.close()
            if self._ownership is ownership:
                self._ownership = None
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
            ownership = self._ownership
            try:
                if ownership is not None:
                    ownership.close()
            finally:
                self._ownership = None
                self._state = None
                self._dirty = False


def _write_state(state_path: Path, state: CampaignState) -> None:
    """Internal: atomic write of full state to disk."""
    payload = {
        "campaign_id": state.campaign_id,
        "campaign_name": state.campaign_name,
        "manifest_path": state.manifest_path,
        "started_at": state.started_at,
        "dispatches": [d.to_dict() for d in state.dispatches] + state.opaque_dispatches,
        "captured_values": state.captured_values,
        "orchestrator_session_id": state.orchestrator_session_id,
        "ended_at": state.ended_at,
        "recipe_snapshot": state.recipe_snapshot,
    }
    write_versioned_json(state_path, payload, schema_version=FLEET_STATE_SCHEMA_VERSION)


class ResumeCountExceeded(ValueError):
    """Raised when ``mark_dispatch_running`` is invoked with ``enforce_max_resume_attempts=True``
    and the dispatch has already accumulated ``MAX_CONSECUTIVE_RESUME_ATTEMPTS``
    consecutive RESUMABLE entries in its ``attempt_history``.

    Distinct from the ValueError raised by ``_validate_transition`` so callers
    can differentiate state-machine violations from cap exhaustion.
    """


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
    enforce_max_resume_attempts: bool = False,
) -> None:
    """Atomically mark a dispatch as running with its dispatch_id and dispatched_pid.

    L3 — Resume-count cap on the headless path: when ``enforce_max_resume_attempts``
    is True, the transition is gated by ``_count_consecutive_resumable_timeouts``
    over the dispatch's ``attempt_history``. The cap check is computed from the
    history (preserved across FAILURE→PENDING auto-resets by ``_RETRY_IDENTITY_FIELDS``),
    so the existing campaign-level cap semantics are extended to the headless
    path without skipping the cap on a reset-rewriting-the-status path.
    """
    from autoskillit.fleet.campaign_state.state_recovery import (  # noqa: PLC0415
        MAX_CONSECUTIVE_RESUME_ATTEMPTS,
        _count_consecutive_resumable_timeouts,
    )

    with CampaignStateMutator(state_path) as m:
        if m.state is None:
            raise FileNotFoundError(f"State file not found or corrupted: {state_path}")
        for d in m.state.dispatches:
            if d.name == dispatch_name:
                d.retry_reason = ""
                d.infra_exit_category = ""
                _validate_transition(d.status, DispatchStatus.RUNNING, d.name)
                if enforce_max_resume_attempts:
                    consecutive_timeouts = _count_consecutive_resumable_timeouts(d.attempt_history)
                    if consecutive_timeouts >= MAX_CONSECUTIVE_RESUME_ATTEMPTS:
                        raise ResumeCountExceeded(
                            f"Resume cap ({MAX_CONSECUTIVE_RESUME_ATTEMPTS}) "
                            f"exhausted for {dispatch_name!r}"
                        )
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
    dispatched_session_id: str = "",
    session_chain: list[str] | None = None,
    dispatched_session_log_dir: str = "",
    effect_provenance: dict[str, Any] | None = None,
) -> None:
    """Atomically mark a dispatch as interrupted with a reason.

    Identity fields (dispatched_session_id, session_chain, dispatched_session_log_dir)
    are additive — they are only written if the corresponding field on the record is
    still empty, preventing the cancellation handler from overwriting values already
    eagerly persisted by the on_session_id_resolved callback. When supplied, the
    effect-provenance snapshot replaces the record's pre-cancellation snapshot.
    """
    with CampaignStateMutator(state_path) as m:
        if m.state is None:
            raise FileNotFoundError(f"State file not found or corrupted: {state_path}")
        for d in m.state.dispatches:
            if d.name == dispatch_name:
                _validate_transition(d.status, DispatchStatus.INTERRUPTED, d.name)
                d.status = DispatchStatus.INTERRUPTED
                d.reason = reason
                d.ended_at = time.time()
                if dispatched_session_id and not d.dispatched_session_id:
                    d.dispatched_session_id = dispatched_session_id
                if session_chain is not None and not d.session_chain:
                    d.session_chain = session_chain
                if dispatched_session_log_dir and not d.dispatched_session_log_dir:
                    d.dispatched_session_log_dir = dispatched_session_log_dir
                if effect_provenance is not None:
                    d.effect_provenance = dict(effect_provenance)
                m.mark_dirty()
                return
        else:
            raise ValueError(f"Dispatch '{dispatch_name}' not found in state")


def mark_dispatch_session_identity(
    state_path: Path,
    dispatch_name: str,
    *,
    dispatched_session_id: str,
) -> None:
    """Eagerly persist the dispatched_session_id once it is discovered during execution.

    Idempotent and status-guarded: only writes when the dispatch is RUNNING and
    dispatched_session_id is still empty. If the dispatch has already transitioned
    to INTERRUPTED (race condition), this is a no-op.
    """
    with CampaignStateMutator(state_path) as m:
        if m.state is None:
            raise FileNotFoundError(f"State file not found or corrupted: {state_path}")
        for d in m.state.dispatches:
            if d.name == dispatch_name:
                if d.status == DispatchStatus.RUNNING and not d.dispatched_session_id:
                    d.dispatched_session_id = dispatched_session_id
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
                if record.managed_lineage_ref is None:
                    record.managed_lineage_ref = d.managed_lineage_ref
                if record.backend_authority is None:
                    record.backend_authority = d.backend_authority
                if record.launch_contract is None:
                    record.launch_contract = d.launch_contract
                    record.launch_contract_digest = d.launch_contract_digest
                m.state.dispatches[i] = record
                break
        else:
            m.state.dispatches.append(record)
        if (
            m.state.ended_at == 0.0
            and m.state.dispatches
            and not m.state.opaque_dispatches
            and all(d.status in TERMINAL_DISPATCH_STATUSES for d in m.state.dispatches)
        ):
            m.state.ended_at = time.time()
        m.mark_dirty()


def _snapshot_prior_failure(dispatch: DispatchRecord) -> dict[str, Any]:
    """Capture retry-relevant diagnostics before replacing a failed record."""
    snapshot: dict[str, Any] = {}
    for field in dataclasses.fields(dispatch):
        if field.name in _RETRY_IDENTITY_FIELDS:
            continue
        value = getattr(dispatch, field.name)
        if field.name == "status":
            snapshot[field.name] = str(value)
        elif isinstance(value, dict):
            snapshot[field.name] = dict(value)
        elif isinstance(value, list):
            snapshot[field.name] = list(value)
        else:
            snapshot[field.name] = value
    return snapshot


def _merge_upsert_dispatch_record(existing: DispatchRecord, incoming: DispatchRecord) -> None:
    """Apply protected replacement and diagnostic inheritance to an incoming record."""
    if existing.status == DispatchStatus.FAILURE and incoming.status == DispatchStatus.SUCCESS:
        raise ValueError(
            f"Cannot overwrite FAILURE dispatch {incoming.name!r} with SUCCESS — "
            f"use mark_dispatch_* state machine methods for valid transitions"
        )
    if existing.status == DispatchStatus.SUCCESS and incoming.status != DispatchStatus.SUCCESS:
        raise ValueError(
            f"Cannot overwrite SUCCESS dispatch {incoming.name!r} "
            f"with {incoming.status!r} — "
            f"use mark_dispatch_* state machine methods for valid transitions"
        )
    if (
        existing.status == DispatchStatus.FAILURE
        and incoming.status == DispatchStatus.FAILURE
        and existing.reason
    ):
        incoming.attempt_history = [_snapshot_prior_failure(existing)] + list(
            incoming.attempt_history
        )
    if existing.reaper_reason and not incoming.reaper_reason:
        incoming.reaper_reason = existing.reaper_reason
    if existing.reaper_dispatch_id and not incoming.reaper_dispatch_id:
        incoming.reaper_dispatch_id = existing.reaper_dispatch_id
    if incoming.managed_lineage_ref is None:
        incoming.managed_lineage_ref = existing.managed_lineage_ref


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
                _merge_upsert_dispatch_record(d, record)
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
            data = read_fleet_state_payload(state_file)
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
        data = read_fleet_state_payload(path)
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


def normalize_dispatch_token_usage(
    raw: dict[str, Any],
    *,
    backend: str = "",
    provider_used: str = "",
    legacy: bool = False,
) -> dict[str, Any]:
    """Preserve one source pair and five accounting measures in fleet state."""
    backend = backend or str(raw.get("backend") or "unknown")
    provider_used = provider_used or str(raw.get("provider_used") or "")
    if not provider_used:
        provider_used = resolve_provider_used(backend, anthropic_provider_capable=False)

    def measure(*keys: str) -> SerializedTokenMeasure:
        for key in keys:
            if key not in raw:
                continue
            value = raw[key]
            if isinstance(value, dict):
                return TokenMeasure.from_dict(value).to_dict()
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                if legacy and value == 0:
                    return TokenMeasure.unknown().to_dict()
                return TokenMeasure.observed(value).to_dict()
            return TokenMeasure.unknown().to_dict()
        return TokenMeasure.unknown().to_dict()

    return {
        "backend": backend,
        "provider_used": provider_used,
        "input_tokens": measure("input", "input_tokens"),
        "output_tokens": measure("output", "output_tokens"),
        "cache_read_tokens": measure("cache_read", "cache_read_tokens", "cache_read_input_tokens"),
        "cache_write_tokens": measure(
            "cache_creation", "cache_write_tokens", "cache_creation_input_tokens"
        ),
        "peak_context": measure("peak_context"),
    }
