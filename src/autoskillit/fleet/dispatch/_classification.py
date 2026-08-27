"""Phase E: outcome classification + final state write — moved from fleet/_api.py (#4851).

Holds the two final-pass functions:
* ``run_outcome_classification`` parses the L3 result block, classifies the
  dispatch outcome, runs the sidecar-synthesis fallback, applies the
  tracker-authority error override, branches on ``SUCCESS`` to fire
  ``COMMIT`` provenance, and computes the terminal lineage state.
* ``finalize_state_write`` persists the ``DispatchRecord`` + captures,
  updates effect-provenance, runs ``_post_dispatch_cleanup``, and returns
  the ``DispatchResult`` envelope.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

# Resolve the test-patch surface through the public facade so that
# ``monkeypatch.setattr('autoskillit.fleet._api.parse_l3_result_block', fake)``
# and ``..._extract_captures`` actually intercept the engine. Routing through
# the facade attribute at call time keeps the public patch surface stable
# across shard refactors. The facade is partial during module import, but
# fully populated by the time these functions execute.
import autoskillit.fleet._api as _facade  # noqa: PLC0415
from autoskillit.core import (
    CaptureEntrySpec,
    ManagedHeadlessSessionTerminalState,
    SessionCheckpoint,
    SkillResult,
    get_logger,
)
from autoskillit.fleet._checkpoint_bridge import load_dispatch_progress
from autoskillit.fleet._native_shell_capture import set_lineage_terminal_state
from autoskillit.fleet._outcome import (
    _checkpoint_to_dict,
    build_dispatch_result,
    classify_dispatch_outcome,
)
from autoskillit.fleet.dispatch._cleanup import _post_dispatch_cleanup
from autoskillit.fleet.dispatch._execution import SpawnContext
from autoskillit.fleet.state import (
    DispatchRecord,
    DispatchStatus,
    normalize_dispatch_token_usage,
    upsert_dispatch_record_by_name,
    write_captured_values,
)
from autoskillit.fleet.state_types import (
    DispatchEffectName,
    DispatchProvenanceTracker,
    DispatchResult,
)

if TYPE_CHECKING:
    from autoskillit.core import CodingAgentBackend
    from autoskillit.pipeline.context import ToolContext

_logger = get_logger(__name__)


@dataclass
class ClassificationResult:
    """Outputs of ``run_outcome_classification`` consumed by ``finalize_state_write``."""

    parsed_result: Any  # L3ParseResult | None
    final_status: DispatchStatus
    reason: str | None
    sidecar_file: Path
    tracker_authority_error: str | None
    branch_name: str
    labels_cleaned: bool
    project_log_dir: str
    extended_chain: list[str]
    dispatched_session_id: str | None  # for finalize to override skill_result.session_id
    skill_result: SkillResult  # for finalize_state_write
    result_success: bool


async def run_outcome_classification(
    *,
    skill_result: SkillResult,
    spawn_ctx: SpawnContext,
    tool_ctx: ToolContext,
    tracker_lease: Any,
    dispatch_id: str,
    effective_name: str,
    managed_lineage_ref: Any,
    provenance: DispatchProvenanceTracker,
    prior_session_chain: list[str],
    prior_dispatched_session_id: str | None,
    resume_session_id: str | None,
    dispatch_checkpoint: SessionCheckpoint | None,
    marker_dir: Path | None,
    effective_backend: CodingAgentBackend | None,
    recipe: str,
    dispatch_sidecar_path: str,
    resume_line_offset: int = 0,
    prior_ids: list[str] | None = None,
) -> ClassificationResult:
    """Phase E — classifies the dispatch outcome.

    Returns a ``ClassificationResult`` carrying every field the orchestrator's
    finalize shard needs.
    """
    # Load progress (sidecar + tracker authority).
    (
        sidecar_file,
        sidecar_entries,
        dispatch_checkpoint_loaded,
        tracker_authority_error,
    ) = load_dispatch_progress(
        tool_ctx=tool_ctx,
        dispatch_sidecar_path=dispatch_sidecar_path,
        dispatch_id=dispatch_id,
        backend_name=effective_backend.name if effective_backend else "",
        recipe=recipe,
        tracker_lease=tracker_lease,
    )
    if dispatch_checkpoint is None:
        dispatch_checkpoint = dispatch_checkpoint_loaded

    extended_chain = prior_session_chain[:]
    additional_jsonl_paths: list[Path] = []
    parsed_result: Any = None

    if skill_result.subtype == "timeout":
        parsed_result = None
    else:
        if prior_dispatched_session_id and prior_dispatched_session_id not in extended_chain:
            extended_chain.append(prior_dispatched_session_id)

        _locator = effective_backend.session_locator() if effective_backend is not None else None
        for sid in extended_chain:
            path = (
                _locator.session_log_path(str(tool_ctx.project_dir), sid)
                if _locator is not None
                else None
            )
            if path is not None:
                additional_jsonl_paths.append(path)

        jsonl_path = (
            _locator.session_log_path(str(tool_ctx.project_dir), skill_result.session_id or "")
            if _locator is not None
            else None
        )

        if resume_line_offset and skill_result.session_id and resume_session_id:
            if skill_result.session_id != resume_session_id:
                _logger.warning(
                    "resume_line_offset_invalidated",
                    resume_session_id=resume_session_id,
                    actual_session_id=skill_result.session_id,
                )
                resume_line_offset = 0
        parsed_result = _facade.parse_l3_result_block(
            stdout=skill_result.result or "",
            expected_dispatch_id=dispatch_id,
            assistant_messages_path=jsonl_path,
            prior_dispatch_ids=prior_ids if prior_ids else None,
            additional_jsonl_paths=additional_jsonl_paths or None,
            resume_line_offset=resume_line_offset,
        )

    _issue_urls_raw = spawn_ctx.issue_urls_raw
    _dispatched_issue_list = [u.strip() for u in _issue_urls_raw.split(",") if u.strip()]
    dispatched_issue_count = len(_dispatched_issue_list)
    if parsed_result is not None and parsed_result.outcome == "no_sentinel" and sidecar_entries:
        from autoskillit.fleet._sidecar_synthesis import (  # noqa: PLC0415
            synthesize_from_sidecar,
        )

        parsed_result = synthesize_from_sidecar(
            parsed_result,
            sidecar_entries,
            dispatched_issue_count=dispatched_issue_count,
        )

    final_status, reason = classify_dispatch_outcome(
        parsed_result,
        skill_result,
        sidecar_exists=sidecar_file.exists(),
        checkpoint=dispatch_checkpoint,
        subtype=skill_result.subtype,
    )
    result_success = bool(
        parsed_result is not None
        and parsed_result.outcome == "completed_clean"
        and parsed_result.payload
        and parsed_result.payload.get("success", False)
    )
    if tracker_authority_error is not None:
        final_status = DispatchStatus.FAILURE
        reason = tracker_authority_error
        result_success = False

    if final_status != DispatchStatus.RESUMABLE:
        terminal_state = (
            ManagedHeadlessSessionTerminalState.SUCCEEDED
            if final_status == DispatchStatus.SUCCESS
            else ManagedHeadlessSessionTerminalState.FAILED
        )
        try:
            set_lineage_terminal_state(
                tool_ctx,
                managed_lineage_ref,
                terminal_state,
            )
        except Exception:
            _logger.warning(
                "failed to record managed lineage terminal state",
                dispatch_name=effective_name,
                terminal_state=terminal_state.value,
                exc_info=True,
            )

    _branch_name = ""
    if sidecar_entries and tool_ctx.runner is not None:
        for _entry in sidecar_entries:
            if _entry.pr_url:
                try:
                    _pr_info = await tool_ctx.runner(
                        ["gh", "pr", "view", _entry.pr_url, "--json", "headRefName"],
                        cwd=tool_ctx.project_dir,
                        timeout=15,
                    )
                    if _pr_info.returncode == 0 and _pr_info.stdout:
                        import json as _json  # noqa: PLC0415

                        _branch_name = _json.loads(_pr_info.stdout).get("headRefName", "")
                except Exception:
                    _logger.debug("branch_name_extraction_failed", exc_info=True)
                break

    _labels_cleaned = False
    if final_status not in (DispatchStatus.SUCCESS, DispatchStatus.RESUMABLE):
        from autoskillit.fleet._label_cleanup import cleanup_orphaned_labels  # noqa: PLC0415

        provenance.start(
            DispatchEffectName.LABEL_CLEANUP,
            identities={"dispatch_id": dispatch_id},
        )
        _labels_cleaned = await cleanup_orphaned_labels(
            str(sidecar_file),  # type: ignore[arg-type]
            tool_ctx.github_client,
            issue_url=_issue_urls_raw,
        )
        provenance.record_labels_cleanup(confirmed=_labels_cleaned)
        if _labels_cleaned:
            provenance.confirm(
                DispatchEffectName.LABEL_CLEANUP,
                receipt="label cleanup helper confirmed cleanup",
                identities={"dispatch_id": dispatch_id},
            )
        else:
            provenance.mark_ambiguous(
                DispatchEffectName.LABEL_CLEANUP,
                evidence="label cleanup helper did not confirm cleanup",
                identities={"dispatch_id": dispatch_id},
            )

    # The orchestrator threads marker_dir through (resolved from
    # `_locator.project_log_dir` inside run_execution), so we just stringify it.
    project_log_dir = str(marker_dir) if marker_dir is not None else ""

    if (
        resume_session_id
        and skill_result.session_id
        and resume_session_id != skill_result.session_id
    ):
        _logger.warning(
            "session_id_continuity_mismatch",
            resume_session_id=resume_session_id,
            returned_session_id=skill_result.session_id,
        )

    # SUCCESS → COMMIT provenance.
    if final_status == DispatchStatus.SUCCESS:
        provenance.start(
            DispatchEffectName.COMMIT,
            identities={
                "dispatch_id": dispatch_id,
                "dispatched_session_id": skill_result.session_id or "",
            },
        )
        provenance.confirm(
            DispatchEffectName.COMMIT,
            receipt="dispatch outcome classifier confirmed success",
            identities={
                "dispatch_id": dispatch_id,
                "dispatched_session_id": skill_result.session_id or "",
            },
        )

    return ClassificationResult(
        parsed_result=parsed_result,
        final_status=final_status,
        reason=reason or "",  # type: ignore[arg-type]
        sidecar_file=sidecar_file,
        tracker_authority_error=tracker_authority_error,
        branch_name=_branch_name,
        labels_cleaned=_labels_cleaned,
        project_log_dir=project_log_dir,
        extended_chain=extended_chain,
        dispatched_session_id=(
            spawn_ctx.dispatched_session_id[0]
            if spawn_ctx.dispatched_session_id
            else skill_result.session_id
        ),
        skill_result=skill_result,
        result_success=result_success,
    )


async def finalize_state_write(
    *,
    classification: ClassificationResult,
    spawn_ctx: SpawnContext,
    tool_ctx: ToolContext,
    dispatch_id: str,
    state_path: Path,
    effective_name: str,
    campaign_id: str,
    caller_session_id: str,
    caller_backend_name: str,
    managed_lineage_ref: Any,
    provenance: DispatchProvenanceTracker,
    capture: dict[str, CaptureEntrySpec] | None,
    dispatch_checkpoint: SessionCheckpoint | None,
    started_at: float,
    ended_at: float,
    cache_invalidator: Callable[[str], None] | None,
    quota_refresher: Callable[..., Any],
    effective_backend_name: str = "",
) -> DispatchResult:
    """Phase E — persist the DispatchRecord + captures and return the envelope."""
    skill_result = classification.skill_result
    parsed_result = classification.parsed_result
    final_status = classification.final_status
    reason = classification.reason
    _labels_cleaned = classification.labels_cleaned
    _branch_name = classification.branch_name
    extended_chain = classification.extended_chain
    project_log_dir = classification.project_log_dir
    _issue_urls_raw = spawn_ctx.issue_urls_raw

    # Build the DispatchRecord.
    record = DispatchRecord(
        name=effective_name,
        status=final_status,
        dispatch_id=dispatch_id,
        campaign_id=campaign_id,
        caller_session_id=caller_session_id,
        caller_backend_name=caller_backend_name,
        dispatched_session_id=classification.dispatched_session_id or "",  # type: ignore[arg-type]
        session_chain=extended_chain,
        dispatched_session_log_dir=project_log_dir,
        dispatched_pid=spawn_ctx.dispatched_pid[0] if spawn_ctx.dispatched_pid else 0,
        dispatched_starttime_ticks=spawn_ctx.dispatched_ticks[0]
        if spawn_ctx.dispatched_ticks
        else 0,
        dispatched_boot_id=spawn_ctx.dispatched_boot_id[0] if spawn_ctx.dispatched_boot_id else "",
        dispatched_create_time=spawn_ctx.dispatched_create_time[0]
        if spawn_ctx.dispatched_create_time
        else 0.0,
        reason=reason or "",  # type: ignore[arg-type]
        retry_reason=skill_result.retry_reason or "",
        infra_exit_category=skill_result.infra.exit_category or "",
        token_usage=normalize_dispatch_token_usage(skill_result.token_usage or {}),
        started_at=started_at,
        ended_at=ended_at,
        sidecar_path=(
            str(classification.sidecar_file) if classification.sidecar_file is not None else ""
        ),
        labels_cleaned=_labels_cleaned,
        issue_url=_issue_urls_raw,
        branch_name=_branch_name,
        backend_name=effective_backend_name,
        resume_checkpoint=_checkpoint_to_dict(dispatch_checkpoint),
        effect_provenance=provenance.snapshot().to_dict(),
        managed_lineage_ref=managed_lineage_ref,
    )

    # SUCCESS + capture extraction.
    extracted: dict[str, str] = {}
    if (
        final_status == DispatchStatus.SUCCESS
        and capture
        and parsed_result is not None
        and parsed_result.payload
        and parsed_result.source != "sidecar"
    ):
        extracted = _facade._extract_captures(capture, parsed_result.payload)

    # Persist state + captures + provenance confirmation.
    provenance.start(
        DispatchEffectName.CAMPAIGN_STATE_WRITE,
        identities={"dispatch_id": dispatch_id, "state_path": state_path},
    )
    upsert_dispatch_record_by_name(state_path, record)
    if extracted:
        write_captured_values(state_path, extracted)
    provenance.confirm(
        DispatchEffectName.CAMPAIGN_STATE_WRITE,
        receipt="per-dispatch state and captures persisted",
        identities={"dispatch_id": dispatch_id, "state_path": state_path},
    )

    # Refresh provenance snapshot on the persisted record.
    record.effect_provenance = provenance.snapshot().to_dict()
    upsert_dispatch_record_by_name(state_path, record)

    # Post-dispatch cleanup (quota cache invalidation + background refresh).
    _post_dispatch_cleanup(tool_ctx, skill_result, cache_invalidator, quota_refresher)

    # Build and return the DispatchResult envelope.
    return build_dispatch_result(
        parsed_result=parsed_result,
        result_success=classification.result_success,
        final_status=final_status,
        reason=reason or "",
        dispatch_id=dispatch_id,
        skill_result=skill_result,
        dispatch_checkpoint=dispatch_checkpoint,
        started_at=started_at,
        ended_at=ended_at,
        state_path=state_path,
        effect_provenance=provenance.snapshot(),
    )
