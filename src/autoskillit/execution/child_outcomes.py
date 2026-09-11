"""Execution-layer reader/collector for the child-terminal-reason snapshot (issue #4623).

Reads the durable snapshot written by the stdlib-only hook authority
(``hooks/_child_outcome_snapshot``), and backfills metadata that only a full
transcript read can supply: Claude native subagent transcript enumeration
(role/model/attribution via ``attributionAgent``/``attributionSkill``,
deduplicated by ``message.id``), Codex unplanned-child discovery via
``sub_agent_activity`` structural rollout evidence, and direct recording of
every physical managed-leaf attempt's outcome using the rich internal
``SkillResult`` evidence only the executor observes (Step 5).

The stdlib-only hook module is the canonical write authority; this module is
a reader, a narrow Codex-observation writer for children the hook observer
cannot see (native ``spawn_agent`` calls confirmed only by rollout replay),
and the managed-executor's own direct writer for physical attempts it alone
observes. Never re-derives or overrides a reason the hook already classified.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from autoskillit.core import AGENT_BACKEND_CLAUDE_CODE, ChildOutcomeDict, get_logger
from autoskillit.execution.backends._codex_execution_identity import (
    linked_child_thread_ids,
    read_codex_rollout_events,
)
from autoskillit.execution.session_log import resolve_log_dir
from autoskillit.hooks import (
    observe_child,
    project_outcomes,
    read_snapshot,
    reconcile_ended_children,
    record_terminal_evidence,
    resolve_snapshot_path,
)

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend, SkillResult

logger = get_logger(__name__)

#: Bound on how much of one transcript is scanned per read (mirrors
#: _codex_execution_identity.py's _MAX_ROLLOUT_IDENTITY_BYTES bound).
_MAX_TRANSCRIPT_SCAN_BYTES = 16 * 1024 * 1024


def normalize_backend_name(backend_name: str) -> str:
    """Normalize a ``CodingAgentBackend.name`` value to the snapshot's backend key.

    ``core.AGENT_BACKEND_CLAUDE_CODE`` is ``"claude-code"`` (hyphen, matching
    ``sessions.jsonl``'s ``backend`` field and every ``_step_backend.name``
    call site), while the stdlib-only hook layer and ``hook_registry``'s own
    ``Literal["claude_code", "codex"]`` backend convention use ``"claude_code"``
    (underscore) throughout. Every execution-layer caller that receives a
    backend name from ``CodingAgentBackend`` must normalize it through this
    function before it becomes a snapshot path component — an unnormalized
    hyphenated name would silently split-brain the snapshot into a second,
    never-read directory next to the hook-written one.
    """
    return "claude_code" if backend_name == AGENT_BACKEND_CLAUDE_CODE else backend_name


def collect_native_children_for_backend(
    *,
    step_backend: CodingAgentBackend,
    cwd: str,
    evidence_session_id: str,
    diagnostic_log_dir: str,
) -> None:
    """Observe every structurally discoverable native child for one just-completed parent.

    Called from ``execution/headless/_headless_execute.py`` after
    ``_drain_model_evidence()`` establishes ``evidence_session_id``. Resolves
    the parent's own transcript/rollout path via the backend's
    ``SessionLocator`` (uniform across both backends — Codex's locator
    already delegates ``session_log_path`` to ``locate_session`` internally),
    then dispatches to the matching collector. Purely observational and
    best-effort: a collection failure never fails the parent's own result.
    """
    if not evidence_session_id:
        return
    try:
        own_transcript_path = step_backend.session_locator().session_log_path(
            cwd, evidence_session_id
        )
        if own_transcript_path is None:
            return
        log_root = resolve_log_dir(diagnostic_log_dir)
        backend = normalize_backend_name(step_backend.name)
        if backend == "codex":
            collect_codex_observed_children(
                parent_rollout_path=own_transcript_path,
                parent_session_id=evidence_session_id,
                log_root=log_root,
            )
        else:
            collect_claude_native_children(
                parent_session_id=evidence_session_id,
                parent_transcript_path=own_transcript_path,
                log_root=log_root,
            )
    except Exception:
        logger.debug("child_outcome_collection_failed", exc_info=True)


def collect_and_project_child_outcomes(
    *,
    step_backend: CodingAgentBackend,
    cwd: str,
    evidence_session_id: str,
    diagnostic_log_dir: str,
) -> tuple[ChildOutcomeDict, ...]:
    """Observe this parent's native children, then read back its full projected snapshot.

    Combines ``collect_native_children_for_backend`` (write) with
    ``collect_child_outcomes`` (read) using the same resolved backend/log
    root, for callers — ``_headless_execute.py``'s telemetry construction —
    that need the final tuple rather than the write's side effect alone.
    """
    collect_native_children_for_backend(
        step_backend=step_backend,
        cwd=cwd,
        evidence_session_id=evidence_session_id,
        diagnostic_log_dir=diagnostic_log_dir,
    )
    return collect_child_outcomes(
        backend=normalize_backend_name(step_backend.name),
        parent_session_id=evidence_session_id,
        log_root=resolve_log_dir(diagnostic_log_dir),
    )


def collect_child_outcomes(
    *, backend: str, parent_session_id: str, log_root: Path
) -> tuple[ChildOutcomeDict, ...]:
    """Read and project the durable snapshot for one parent.

    Returns an empty tuple when no snapshot exists (no children observed) or
    the parent identity is malformed — never raises for a missing/absent
    snapshot, matching the "silent children remain countable unknown, never
    omitted" design; an absent snapshot simply means zero observed children.
    """
    try:
        snapshot_path = resolve_snapshot_path(
            log_root, backend=backend, parent_session_id=parent_session_id
        )
    except Exception:
        logger.debug("child_outcome_snapshot_path_invalid", exc_info=True)
        return ()
    document = read_snapshot(snapshot_path)
    wire_rows = project_outcomes(document)
    return tuple(cast(ChildOutcomeDict, dict(row)) for row in wire_rows)


def _read_jsonl_bounded(path: Path, *, max_bytes: int) -> list[dict[str, Any]]:
    """Stream-read a JSONL transcript, bounded, tolerant of truncation/corruption."""
    records: list[dict[str, Any]] = []
    total = 0
    try:
        with path.open("rb") as handle:
            for raw_line in handle:
                total += len(raw_line)
                if total > max_bytes:
                    break
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if isinstance(value, dict):
                    records.append(value)
    except OSError:
        return records
    return records


def enumerate_claude_subagent_transcripts(parent_transcript_path: Path) -> tuple[Path, ...]:
    """Return the parent's structural subagent transcript collection, sorted.

    Layout: ``<project>/<parent-session-id>/subagents/agent-*.jsonl``, a
    sibling of the parent's own ``<parent-session-id>.jsonl`` transcript file
    (pinned by real on-disk capture in the Step 1 investigation notes).
    """
    parent_session_id = parent_transcript_path.stem
    subagents_dir = parent_transcript_path.parent / parent_session_id / "subagents"
    if not subagents_dir.is_dir():
        return ()
    try:
        return tuple(sorted(subagents_dir.glob("agent-*.jsonl")))
    except OSError:
        return ()


def _child_id_from_subagent_transcript_path(transcript_path: Path) -> str:
    # agent-<agentId>.jsonl -> <agentId>
    return transcript_path.stem.removeprefix("agent-")


def _extract_claude_child_metadata(records: list[dict[str, Any]]) -> dict[str, str]:
    """Extract role/model/attribution from one subagent transcript's records.

    Deduplicates assistant records by ``message.id`` (mirrors
    ``execution/session/_turn_usage.py:merge_turn_usage``'s dedup pattern);
    later records for the same message id win.
    """
    seen_message_ids: set[str] = set()
    attribution_skill = ""
    effective_model = ""
    for record in records:
        if record.get("type") != "assistant":
            continue
        message = record.get("message")
        message_id = message.get("id") if isinstance(message, dict) else None
        if isinstance(message_id, str) and message_id:
            if message_id in seen_message_ids:
                continue
            seen_message_ids.add(message_id)
        skill = record.get("attributionSkill")
        if isinstance(skill, str) and skill:
            attribution_skill = skill
        model = message.get("model") if isinstance(message, dict) else None
        if isinstance(model, str) and model:
            effective_model = model
    role = ""
    for record in records:
        agent = record.get("attributionAgent")
        if isinstance(agent, str) and agent:
            role = agent
            break
    return {
        "role": role,
        "attribution_skill": attribution_skill,
        "effective_model": effective_model,
    }


def collect_claude_native_children(
    *,
    parent_session_id: str,
    parent_transcript_path: Path,
    log_root: Path,
) -> None:
    """Observe every structurally discoverable Claude native subagent transcript.

    An observed child transcript without terminal evidence is recorded
    unknown, never omitted (matches Step 4.3's requirement). Transcript
    path/role/model and stop-marker absence are never used to infer a
    terminal cause here — only metadata backfill.
    """
    transcripts = enumerate_claude_subagent_transcripts(parent_transcript_path)
    if not transcripts:
        return
    try:
        snapshot_path = resolve_snapshot_path(
            log_root, backend="claude_code", parent_session_id=parent_session_id
        )
    except Exception:
        logger.debug("claude_child_outcome_snapshot_path_invalid", exc_info=True)
        return
    for transcript_path in transcripts:
        child_id = _child_id_from_subagent_transcript_path(transcript_path)
        if not child_id:
            continue
        observe_child(
            snapshot_path,
            backend="claude_code",
            parent_session_id=parent_session_id,
            child_id=child_id,
        )
        records = _read_jsonl_bounded(transcript_path, max_bytes=_MAX_TRANSCRIPT_SCAN_BYTES)
        metadata = _extract_claude_child_metadata(records)
        evidence: dict[str, Any] = {"evidence_source": "transcript_metadata"}
        evidence.update({key: value for key, value in metadata.items() if value})
        if len(evidence) > 1:
            record_terminal_evidence(
                snapshot_path,
                backend="claude_code",
                parent_session_id=parent_session_id,
                child_id=child_id,
                evidence_key=f"claude_code:{child_id}:transcript_metadata:{transcript_path.name}",
                evidence=evidence,
            )


def collect_codex_observed_children(
    *,
    parent_rollout_path: Path,
    parent_session_id: str,
    log_root: Path,
) -> None:
    """Observe every Codex child thread structurally linked in the parent rollout.

    Every observed child (planned or not) gets a durable unknown row even if
    its own child rollout is missing — this only reads the parent's rollout,
    never assumes a planned identity. A child already carrying a typed
    ``ChildExecutionIdentity`` (from ``extract_codex_execution_identity``)
    still receives this call; ``observe_child`` is idempotent, so it is a
    no-op refinement rather than a duplicate row. Reuses
    ``extract_codex_execution_identity``'s exact rollout reader
    (``read_codex_rollout_events``, tolerant of ``.zst``-compressed rollouts)
    and its exact ``sub_agent_activity``/``kind == "started"``/
    ``agent_thread_id`` filter (``linked_child_thread_ids``) — defined once,
    not re-derived here.
    """
    try:
        events = read_codex_rollout_events(parent_rollout_path)
    except (OSError, ValueError):
        return
    linked_child_ids = linked_child_thread_ids(events, parent_id=parent_session_id)
    if not linked_child_ids:
        return
    try:
        snapshot_path = resolve_snapshot_path(
            log_root, backend="codex", parent_session_id=parent_session_id
        )
    except Exception:
        logger.debug("codex_child_outcome_snapshot_path_invalid", exc_info=True)
        return
    for child_id in linked_child_ids:
        observe_child(
            snapshot_path,
            backend="codex",
            parent_session_id=parent_session_id,
            child_id=child_id,
        )


def _resolve_claude_parent_transcript(parent_session_id: str) -> Path | None:
    from autoskillit.execution.backends._claude_session_locator import ClaudeSessionLocator

    return ClaudeSessionLocator().locate_session(parent_session_id)


def reconcile_child_outcome_snapshots(log_root: Path) -> int:
    """Refresh every canonical child-outcome snapshot from currently available evidence.

    Runs independently of tmpfs trace-file enrollment — orphaned snapshots
    (parent crashed or was never captured by the enrolled-trace mechanism)
    have no tmpfs trace at all; the hook observer logs them directly. For
    Claude parents whose own transcript is still resolvable, opportunistically
    re-runs subagent-transcript collection (a crashed parent will never run
    it itself). Every snapshot is then explicitly reconciled — known reasons
    are left untouched, still-unknown rows stay unknown. Never prunes a
    snapshot and never invents a cause. Returns the number of snapshots
    visited.
    """
    snapshot_root = log_root / "child-outcomes"
    if not snapshot_root.is_dir():
        return 0
    visited = 0
    try:
        backend_dirs = sorted(p for p in snapshot_root.iterdir() if p.is_dir())
    except OSError:
        return 0
    for backend_dir in backend_dirs:
        backend = backend_dir.name
        try:
            snapshot_files = sorted(backend_dir.glob("*.json"))
        except OSError:
            continue
        for snapshot_file in snapshot_files:
            parent_session_id = snapshot_file.stem
            if backend == "claude_code":
                parent_transcript = _resolve_claude_parent_transcript(parent_session_id)
                if parent_transcript is not None:
                    collect_claude_native_children(
                        parent_session_id=parent_session_id,
                        parent_transcript_path=parent_transcript,
                        log_root=log_root,
                    )
            try:
                reconcile_ended_children(
                    snapshot_file, backend=backend, parent_session_id=parent_session_id
                )
            except Exception:
                logger.debug("child_outcome_snapshot_reconcile_row_failed", exc_info=True)
                continue
            visited += 1
    return visited


def observe_managed_child_attempt(
    *,
    log_root: Path,
    backend: str,
    parent_session_id: str,
    child_id: str,
    role: str = "",
    attribution_skill: str = "",
) -> None:
    """Record a confirmed managed-leaf physical attempt as a durable unknown row.

    Called once a physical attempt is confirmed spawned (never for a
    reservation that was cancelled or rejected before spawn — see the Step 2
    design decision). ``child_id`` is the physical-attempt identity
    (``managed_attempt_id`` when a lineage observer allocated one, else a
    plain diagnostic attempt id), not a backend-native session id.
    """
    try:
        snapshot_path = resolve_snapshot_path(
            log_root, backend=backend, parent_session_id=parent_session_id
        )
    except Exception:
        logger.debug("managed_child_attempt_snapshot_path_invalid", exc_info=True)
        return
    observe_child(
        snapshot_path, backend=backend, parent_session_id=parent_session_id, child_id=child_id
    )
    evidence: dict[str, Any] = {"evidence_source": "managed_attempt_reservation"}
    if role:
        evidence["role"] = role
    if attribution_skill:
        evidence["attribution_skill"] = attribution_skill
    if len(evidence) > 1:
        record_terminal_evidence(
            snapshot_path,
            backend=backend,
            parent_session_id=parent_session_id,
            child_id=child_id,
            evidence_key=f"{backend}:{child_id}:managed_attempt:metadata",
            evidence=evidence,
        )


def bind_managed_child_launch_alias(
    *,
    log_root: Path,
    backend: str,
    parent_session_id: str,
    child_id: str,
    launch_alias: str,
) -> None:
    """Bind the resolved backend-native session id onto a physical attempt's row.

    Called from the ``on_session_id_resolved`` callback as soon as the
    backend-native session/thread id is captured — merges into the same
    attempt row rather than creating a second one (Step 5.3).
    """
    if not launch_alias:
        return
    try:
        snapshot_path = resolve_snapshot_path(
            log_root, backend=backend, parent_session_id=parent_session_id
        )
    except Exception:
        logger.debug("managed_child_launch_alias_snapshot_path_invalid", exc_info=True)
        return
    observe_child(
        snapshot_path,
        backend=backend,
        parent_session_id=parent_session_id,
        child_id=child_id,
        launch_alias=launch_alias,
    )


def record_managed_child_attempt_outcome(
    *,
    log_root: Path,
    backend: str,
    parent_session_id: str,
    child_id: str,
    skill_result: SkillResult,
    evidence_source: str,
) -> None:
    """Record one physical managed-leaf attempt's final outcome.

    Uses the rich internal ``SkillResult`` fields (``cli_subtype``,
    ``api_failure.terminal_reason``/``error_code``, ``infra.exit_category``)
    that only the executor observes — never re-derived from a generic
    tool-result status. ``confirmed_completed`` is set only for an explicit
    successful, non-error result; ``confirmed_interrupted`` only for the
    executor's own confirmed cancellation path (``subtype == "cancelled"``).
    A crash/infrastructure-fault result carries no matching evidence key by
    design — SkillResult.crashed()/.infrastructure_fault() never populate
    api_failure or cli_subtype, so recording the raw subtype/exception text
    here still cannot classify a cause without inventing one; it stays
    unknown with the raw evidence preserved for diagnosis.
    """
    try:
        snapshot_path = resolve_snapshot_path(
            log_root, backend=backend, parent_session_id=parent_session_id
        )
    except Exception:
        logger.debug("managed_child_attempt_outcome_snapshot_path_invalid", exc_info=True)
        return
    evidence: dict[str, Any] = {
        "evidence_source": evidence_source,
        "terminal_reason": skill_result.subtype,
    }
    if skill_result.cli_subtype:
        evidence["cli_subtype"] = skill_result.cli_subtype
    if skill_result.api_failure.terminal_reason:
        evidence["api_terminal_reason"] = skill_result.api_failure.terminal_reason
    if skill_result.api_failure.error_code:
        evidence["error_code"] = skill_result.api_failure.error_code
    if skill_result.infra.exit_category:
        evidence["infra_exit_category"] = skill_result.infra.exit_category
    if skill_result.subtype == "cancelled":
        evidence["confirmed_interrupted"] = True
    if skill_result.success and not skill_result.is_error:
        evidence["confirmed_completed"] = True
    record_terminal_evidence(
        snapshot_path,
        backend=backend,
        parent_session_id=parent_session_id,
        child_id=child_id,
        evidence_key=f"{backend}:{child_id}:attempt_result:{evidence_source}",
        evidence=evidence,
    )


class ManagedAttemptRecorder:
    """Tracks and records one physical managed-leaf attempt at a time (Step 5).

    Constructed once per ``_execute_claude_headless`` call (a no-op recorder
    when ``role`` is empty, i.e. an ordinary non-managed session); one
    ``start_attempt`` call per provider-retry loop iteration resets it for
    that iteration's physical attempt. Kept invocation-local and cache-free —
    no module-level state.
    """

    def __init__(
        self,
        *,
        log_root: Path | None,
        backend: str,
        parent_session_id: str,
        role: str,
        attribution_skill: str,
    ) -> None:
        self._log_root = log_root
        self._backend = backend
        self._parent_session_id = parent_session_id
        self._role = role
        self._attribution_skill = attribution_skill
        self.child_id: str | None = None
        self.spawn_confirmed = False

    def start_attempt(self, child_id: str | None) -> None:
        """Reset tracking for a fresh physical attempt (a new loop iteration)."""
        self.child_id = child_id
        self.spawn_confirmed = False

    def on_spawn(self, pid: int, extra: int, *, downstream: Any) -> None:
        """``on_spawn`` wrapper: confirms the attempt, then chains to ``downstream``."""
        self.spawn_confirmed = True
        if self.child_id is not None and self._log_root is not None:
            try:
                observe_managed_child_attempt(
                    log_root=self._log_root,
                    backend=self._backend,
                    parent_session_id=self._parent_session_id,
                    child_id=self.child_id,
                    role=self._role,
                    attribution_skill=self._attribution_skill,
                )
            except Exception:
                logger.debug("managed_child_attempt_observe_failed", exc_info=True)
        if downstream is not None:
            downstream(pid, extra)

    def bind_launch_alias(self, native_session_id: str, *, downstream: Any) -> None:
        """``on_session_id_resolved`` wrapper: binds the alias, then chains to ``downstream``."""
        if self.child_id is not None and self._log_root is not None and native_session_id:
            try:
                bind_managed_child_launch_alias(
                    log_root=self._log_root,
                    backend=self._backend,
                    parent_session_id=self._parent_session_id,
                    child_id=self.child_id,
                    launch_alias=native_session_id,
                )
            except Exception:
                logger.debug("managed_child_launch_alias_bind_failed", exc_info=True)
        downstream(native_session_id)

    def record_outcome(self, skill_result: SkillResult, evidence_source: str) -> None:
        """Record the current attempt's final outcome (success or exception path)."""
        if self.child_id is None or self._log_root is None:
            return
        try:
            record_managed_child_attempt_outcome(
                log_root=self._log_root,
                backend=self._backend,
                parent_session_id=self._parent_session_id,
                child_id=self.child_id,
                skill_result=skill_result,
                evidence_source=evidence_source,
            )
        except Exception:
            logger.debug("managed_child_attempt_outcome_record_failed", exc_info=True)

    def record_exception_outcome(self, skill_result: SkillResult, evidence_source: str) -> None:
        """Record an exception-path outcome — only if this attempt's spawn was confirmed.

        Cancellation or preparation failure before a confirmed spawn leaves
        no confirmed child (Step 5.3).
        """
        if not self.spawn_confirmed:
            return
        self.record_outcome(skill_result, evidence_source)
