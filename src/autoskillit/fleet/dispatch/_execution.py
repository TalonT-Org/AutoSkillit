"""Phase C: spawn / execute / classify envelope — moved from fleet/_api.py (#4851).

Holds ``run_execution`` — the orchestrator calls this after the lineage has
been prepared and we hold a dispatch_id / state_path. ``SpawnContext`` carries
closure-scoped state populated by the ``on_spawn`` / ``on_session_id`` /
``on_launch_resolved`` callbacks; ``ExecutionResult`` is the contract the
caller consumes to drive outcome classification.
"""

from __future__ import annotations

import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import psutil

from autoskillit.core import (
    FLEET_INSPECTOR_MODEL_ENV_VAR,
    BackendAuthority,
    BackendAuthorityKind,
    BackendAuthorityTier,
    FleetErrorCode,
    get_logger,
    select_child_session_deadline,
)
from autoskillit.fleet.dispatch._errors import complete_failure_with_state
from autoskillit.fleet.dispatch._heartbeat import _dispatch_heartbeat
from autoskillit.fleet.dispatch._pid import _write_pid
from autoskillit.fleet.state import DispatchStatus
from autoskillit.fleet.state_types import (
    DispatchEffectName,
    DispatchProvenanceTracker,
    DispatchRejected,
    DispatchResult,
)

if TYPE_CHECKING:
    from autoskillit.core import (
        CodingAgentBackend,
        ManagedHeadlessSessionLineageRef,
        NativeShellCaptureDecision,
        ResolvedLaunchContract,
        SessionCheckpoint,
        SkillResult,
    )
    from autoskillit.fleet.state_recovery import ResumePreflight
    from autoskillit.pipeline.context import ToolContext

_logger = get_logger(__name__)


@dataclass
class SpawnContext:
    """Closure-scoped state populated by on_spawn/on_session_id callbacks.

    Lists rather than scalars because the original source uses lists so multiple
    spawn callbacks can append without rebinding.
    """

    dispatched_pid: list[int] = field(default_factory=list)
    dispatched_ticks: list[int] = field(default_factory=list)
    dispatched_create_time: list[float] = field(default_factory=list)
    dispatched_boot_id: list[str] = field(default_factory=list)
    dispatched_session_id: list[str] = field(default_factory=list)
    issue_urls_raw: str = ""
    prior_ids: list[str] = field(default_factory=list)
    prior_completion_markers: list[str | None] = field(default_factory=list)
    spawn_error: list[str] = field(default_factory=list)


@dataclass
class ExecutionResult:
    """What ``run_execution`` hands back to the orchestrator.

    ``skill_result`` is None only when ``spawn_failure_dispatch_result`` carries
    the L2 fail-closed envelope. ``dispatch_completed_normally`` distinguishes
    "spawn-side aborted" from "ran to completion but outcome is failure" so the
    orchestrator can decide whether to invoke ``run_finally_label_cleanup``.
    """

    skill_result: SkillResult | None
    spawn_context: SpawnContext
    started_at: float
    ended_at: float | None
    dispatch_completed_normally: bool
    marker_dir: Path | None
    dispatch_sidecar_path: str
    spawn_failure_dispatch_result: DispatchResult | None


async def run_execution(
    *,
    tool_ctx: ToolContext,
    spawn_ctx: SpawnContext,
    dispatch_id: str,
    state_path: Path,
    effective_name: str,
    managed_lineage_ref: ManagedHeadlessSessionLineageRef | None,
    capture_decision: NativeShellCaptureDecision | None,
    resume_session_id: str | None,
    resume_checkpoint: SessionCheckpoint | None,
    resume_message: str | None,
    prompt: str,
    plugin_authority: Any,
    capability_preparation: Any,
    authoritative_cwd: Path,
    preflight: ResumePreflight | None,
    full_recipe: Any,
    provenance: DispatchProvenanceTracker,
    started_at: float,
    prior_session_chain: list[str],
    prior_dispatched_session_id: str | None,
    effective_backend: CodingAgentBackend | None,
    caller_session_id: str,
    idle_output_timeout: int | None,
    lineage_backend_name: str,
    dispatch_sidecar_path: str,
    issue_urls_raw: str,
    prior_ids: list[str],
    prior_completion_markers: list[str | None] | None,
    dispatch_backend: CodingAgentBackend | None,
    completion_marker: str = "",
    sentinel_contract: Any = None,
    halted_reason: str | None = None,
    caller_backend_name: str = "",
    dispatches_dir: Path | None = None,
    recipe: str = "",
    resolved_timeout: float = 0.0,
    effective_ingredients: dict[str, str] | None = None,
) -> ExecutionResult:
    """Phase C: lines 905-1207 of the legacy ``_run_dispatch``.

    Halt check → state upsert → resume resolution → spawn → spawn-error gate.

    The returned ``ExecutionResult`` carries either a populated ``skill_result``
    (the normal path) or a populated ``spawn_failure_dispatch_result`` (the L2
    fail-closed path). Cancellation and unexpected exceptions are propagated to
    the orchestrator's outer try/except — this function only manages
    state-internal cleanup for the spawn-error gate.
    """
    # Populate the spawn_ctx with the inputs the closures will need.
    spawn_ctx.issue_urls_raw = issue_urls_raw
    spawn_ctx.prior_ids = list(prior_ids)
    spawn_ctx.prior_completion_markers = (
        list(prior_completion_markers) if prior_completion_markers is not None else []
    )

    # 944: derive the session locator for JSONL resolution.
    _locator = effective_backend.session_locator() if effective_backend is not None else None

    # 1110-1117: marker_dir resolution — moved up so the early-return
    # ExecutionResult constructors can populate marker_dir even when the
    # function exits before reaching the original resolution site.
    marker_dir: Path | None = None
    if _locator is not None:
        try:
            marker_dir = _locator.project_log_dir(str(tool_ctx.project_dir))
        except OSError:
            pass

    # 905-915: halt-reason check — short-circuit before any state mutation.
    if halted_reason is not None:
        return ExecutionResult(
            skill_result=None,
            spawn_context=spawn_ctx,
            started_at=started_at,
            ended_at=None,
            dispatch_completed_normally=False,
            marker_dir=marker_dir,
            dispatch_sidecar_path=dispatch_sidecar_path,
            spawn_failure_dispatch_result=DispatchResult(
                outcome=DispatchRejected(
                    error_code=FleetErrorCode.FLEET_CAMPAIGN_HALTED,
                    message=halted_reason,
                    effect_provenance=provenance.snapshot(),
                    dispatch_id=dispatch_id,
                ),
                per_dispatch_state_path=state_path,
            ),
        )

    # 917-942: state-record upsert before spawn.
    from autoskillit.fleet.state import (  # noqa: PLC0415
        DispatchRecord,
        read_state,
        upsert_dispatch_record_by_name,
    )

    try:
        current_state = read_state(state_path)
        current_record = (
            next(
                (d for d in current_state.dispatches if d.name == effective_name),
                None,
            )
            if current_state is not None
            else None
        )
        if current_record is None:
            current_record = DispatchRecord(name=effective_name)
        current_record.dispatch_id = dispatch_id
        current_record.campaign_id = tool_ctx.kitchen_id
        current_record.caller_session_id = caller_session_id
        current_record.caller_backend_name = caller_backend_name
        current_record.backend_name = lineage_backend_name
        current_record.effect_provenance = provenance.snapshot().to_dict()
        current_record.managed_lineage_ref = managed_lineage_ref
        upsert_dispatch_record_by_name(state_path, current_record)
    except Exception:
        _logger.warning("managed_food_truck_lineage_state_write_failed", exc_info=True)
        return ExecutionResult(
            skill_result=None,
            spawn_context=spawn_ctx,
            started_at=started_at,
            ended_at=None,
            dispatch_completed_normally=False,
            marker_dir=marker_dir,
            dispatch_sidecar_path=dispatch_sidecar_path,
            spawn_failure_dispatch_result=complete_failure_with_state(
                error_code=FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
                message="Food-truck dispatch initialization failed.",
                dispatch_status=DispatchStatus.REFUSED,
                dispatched_session_id="",
                dispatch_id=dispatch_id,
                managed_lineage_ref=managed_lineage_ref,
                provenance=provenance,
                state_path=state_path,
                effective_name=effective_name,
                tool_ctx=tool_ctx,
            ),
        )

    # 944: derive the session locator — moved to the function preamble so
    # the early-return ExecutionResult constructors can populate marker_dir.
    # (Original location below is removed.)

    # 946-994: resume JSONL resolution + EFFECTIVE_RESUME_BINDING provenance.
    if resume_session_id:
        _primary_jsonl = (
            _locator.session_log_path(str(tool_ctx.project_dir), resume_session_id)
            if _locator is not None
            else None
        )
        if _primary_jsonl is None or not _primary_jsonl.exists():
            _logger.warning(
                "resume_jsonl_missing",
                resume_session_id=resume_session_id,
                expected_path=str(_primary_jsonl) if _primary_jsonl else "none",
            )
            _fallback_session_id = prior_session_chain[-1] if prior_session_chain else ""
            if _fallback_session_id:
                _fallback_jsonl = (
                    _locator.session_log_path(str(tool_ctx.project_dir), _fallback_session_id)
                    if _locator is not None
                    else None
                )
                if _fallback_jsonl is not None and _fallback_jsonl.exists():
                    _logger.info(
                        "resume_session_fallback",
                        original_session_id=resume_session_id,
                        fallback_session_id=_fallback_session_id,
                    )
                    resume_session_id = _fallback_session_id
                else:
                    return ExecutionResult(
                        skill_result=None,
                        spawn_context=spawn_ctx,
                        started_at=started_at,
                        ended_at=None,
                        dispatch_completed_normally=False,
                        marker_dir=marker_dir,
                        dispatch_sidecar_path=dispatch_sidecar_path,
                        spawn_failure_dispatch_result=complete_failure_with_state(
                            error_code=FleetErrorCode.FLEET_RESUME_SESSION_MISSING,
                            message=f"JSONL log for session {resume_session_id} not found",
                            dispatch_status=DispatchStatus.REFUSED,
                            dispatched_session_id="",
                            dispatch_id=dispatch_id,
                            managed_lineage_ref=managed_lineage_ref,
                            provenance=provenance,
                            state_path=state_path,
                            effective_name=effective_name,
                            tool_ctx=tool_ctx,
                        ),
                    )
            else:
                return ExecutionResult(
                    skill_result=None,
                    spawn_context=spawn_ctx,
                    started_at=started_at,
                    ended_at=None,
                    dispatch_completed_normally=False,
                    marker_dir=marker_dir,
                    dispatch_sidecar_path=dispatch_sidecar_path,
                    spawn_failure_dispatch_result=complete_failure_with_state(
                        error_code=FleetErrorCode.FLEET_RESUME_SESSION_MISSING,
                        message=f"JSONL log for session {resume_session_id} not found",
                        dispatch_status=DispatchStatus.REFUSED,
                        dispatched_session_id="",
                        dispatch_id=dispatch_id,
                        managed_lineage_ref=managed_lineage_ref,
                        provenance=provenance,
                        state_path=state_path,
                        effective_name=effective_name,
                        tool_ctx=tool_ctx,
                    ),
                )

    if resume_session_id:
        provenance.start(
            DispatchEffectName.EFFECTIVE_RESUME_BINDING,
            retry_relevant=False,
            identities={"resume_session_id": resume_session_id},
        )
        provenance.confirm(
            DispatchEffectName.EFFECTIVE_RESUME_BINDING,
            receipt="effective resume session resolved",
            retry_relevant=False,
            identities={"resume_session_id": resume_session_id},
        )

    # 996-1004: resume line offset.
    resume_line_offset = 0  # noqa: F841 — preserved for source parity
    if resume_session_id:
        _resume_jsonl = (
            _locator.session_log_path(str(tool_ctx.project_dir), resume_session_id)
            if _locator is not None
            else None
        )
        if _resume_jsonl is not None and _resume_jsonl.exists():
            _ = len(_resume_jsonl.read_text(encoding="utf-8").splitlines())  # noqa: F841 — preserved for original source parity

    # 1006-1017: completion_marker and sentinel_contract come from the identity
    # object held by the orchestrator's lineage preparation; the orchestrator
    # passes them in as explicit parameters (preserving the closure-captured
    # values from the original ``_run_dispatch``).
    # (sidecar_path, started_at, closure-list init are handled in the function
    # parameter list / SpawnContext population.)

    _dispatch_completed_normally = False
    skill_result: SkillResult | None = None
    ended_at: float | None = None

    # execution_marker is needed by the spawn-context blocks further below.
    from autoskillit.core import execution_marker  # noqa: PLC0415

    # 1040-1108: closures captured by tool_ctx.executor.dispatch_food_truck.
    # They mutate spawn_ctx in place.
    def _on_spawn(pid: int, ticks: int) -> None:
        from autoskillit.core import read_boot_id

        spawn_ctx.dispatched_pid.append(pid)
        provenance.start(
            DispatchEffectName.CHILD_DISCOVERY,
            identities={"pid": pid, "dispatch_id": dispatch_id},
        )
        try:
            create_time = psutil.Process(pid).create_time()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            create_time = 0.0
        boot_id = read_boot_id() or ""
        spawn_ctx.dispatched_ticks.append(ticks)
        spawn_ctx.dispatched_create_time.append(create_time)
        spawn_ctx.dispatched_boot_id.append(boot_id)
        provenance.confirm(
            DispatchEffectName.CHILD_DISCOVERY,
            receipt="captured one process identity tuple",
            identities={
                "pid": pid,
                "starttime_ticks": ticks,
                "create_time": create_time,
                "boot_id": boot_id,
                "identity_degraded": ticks == 0 or create_time == 0.0 or not boot_id,
            },
        )
        # Resume branch iff preflight was returned by prepare_resume above.
        # Cap enforcement (MAX_CONSECUTIVE_RESUME_ATTEMPTS) lives one layer down
        # in mark_dispatch_running.
        is_resume_branch = preflight is not None
        # Record instead of raising: the executor converts callback errors to a crashed result.
        err = _write_pid(
            state_path,
            effective_name,
            dispatch_id,
            pid,
            ticks,
            dispatch_sidecar_path,
            create_time,
            identity_degraded=(ticks == 0 or create_time == 0.0 or not boot_id),
            issue_url=issue_urls_raw,
            dispatched_boot_id=boot_id,
            provenance=provenance,
            enforce_max_resume_attempts=is_resume_branch,
        )
        if err is not None:
            spawn_ctx.spawn_error.append(err)

    def _on_session_id(session_id: str) -> None:
        from autoskillit.fleet.state import mark_dispatch_session_identity

        spawn_ctx.dispatched_session_id.append(session_id)
        mark_dispatch_session_identity(
            state_path, effective_name, dispatched_session_id=session_id
        )
        provenance.confirm(
            DispatchEffectName.PROCESS_SPAWN,
            receipt="executor reported spawned process and authoritative session identity",
            identities={
                "pid": spawn_ctx.dispatched_pid[0] if spawn_ctx.dispatched_pid else 0,
                "starttime_ticks": spawn_ctx.dispatched_ticks[0]
                if spawn_ctx.dispatched_ticks
                else 0,
                "dispatch_id": dispatch_id,
                "dispatched_session_id": session_id,
            },
        )

    def _on_launch_resolved(launch_contract: ResolvedLaunchContract) -> None:
        from autoskillit.fleet._checkpoint_bridge import bind_dispatch_launch_contract

        bind_dispatch_launch_contract(state_path, effective_name, launch_contract)

    # 1119-1202: dispatch the inner work.
    provenance.start(
        DispatchEffectName.PROCESS_SPAWN,
        identities={"dispatch_id": dispatch_id},
    )
    async with execution_marker(
        marker_dir,
        caller_session_id,
        "dispatch",
    ):
        async with _dispatch_heartbeat(
            dispatches_dir or tool_ctx.temp_dir / "dispatches", dispatch_id
        ):
            skill_result = await tool_ctx.executor.dispatch_food_truck(  # type: ignore[union-attr]
                orchestrator_prompt=prompt,
                cwd=str(tool_ctx.project_dir),
                completion_marker=completion_marker,
                plugin_authority=plugin_authority,
                capability_preparation=capability_preparation,
                prior_completion_markers=(
                    cast(
                        "Sequence[str] | None",
                        prior_completion_markers if prior_completion_markers else None,
                    )
                ),
                resume_session_id=resume_session_id,
                resume_checkpoint=resume_checkpoint,
                kitchen_id=tool_ctx.kitchen_id,
                order_id=dispatch_id,
                campaign_id=tool_ctx.kitchen_id,
                dispatch_id=dispatch_id,
                caller_session_id=caller_session_id,
                project_dir=str(tool_ctx.project_dir),
                marker_dir=marker_dir,
                session_id=caller_session_id,
                on_session_id_resolved=_on_session_id,
                timeout=resolved_timeout,
                idle_output_timeout=float(idle_output_timeout)
                if idle_output_timeout is not None
                else None,
                env_extras={
                    "AUTOSKILLIT_PROJECT_DIR": str(tool_ctx.project_dir),
                    "AUTOSKILLIT_CAMPAIGN_ID": tool_ctx.kitchen_id,
                    "AUTOSKILLIT_DISPATCH_ID": dispatch_id,
                    "AUTOSKILLIT_SESSION_DEADLINE": select_child_session_deadline(
                        started_at + resolved_timeout,
                        os.environ.get("AUTOSKILLIT_SESSION_DEADLINE", ""),
                    ),
                    **(
                        {FLEET_INSPECTOR_MODEL_ENV_VAR: (tool_ctx.config.fleet.inspector_model)}
                        if tool_ctx.config.fleet.inspector_model
                        else {}
                    ),
                },
                requires_packs=list(full_recipe.requires_packs) or ["kitchen-core"],
                on_spawn=_on_spawn,
                sentinel_contract=sentinel_contract,
                resume_message=resume_message,
                backend_authority=(
                    BackendAuthority(
                        backend=dispatch_backend.name,
                        kind=BackendAuthorityKind.CALLER,
                        tier=BackendAuthorityTier.CALLER,
                        key_path="dispatch.backend",
                    )
                    if dispatch_backend is not None
                    else None
                ),
                native_shell_capture_decision=capture_decision,
                managed_lineage_ref=managed_lineage_ref,
                on_launch_resolved=_on_launch_resolved,
            )

    # L2 fail-closed spawn gate: check closure-scoped error state.
    # If _on_spawn recorded a transition failure (and killed the child
    # via kill_process_tree), translate it to a structured envelope
    # instead of letting the dispatch proceed on a stale record.
    if spawn_ctx.spawn_error:
        return ExecutionResult(
            skill_result=None,
            spawn_context=spawn_ctx,
            started_at=started_at,
            ended_at=None,
            dispatch_completed_normally=False,
            marker_dir=marker_dir,
            dispatch_sidecar_path=dispatch_sidecar_path,
            spawn_failure_dispatch_result=complete_failure_with_state(
                error_code=FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
                message=spawn_ctx.spawn_error[0],
                dispatch_status=DispatchStatus.FAILURE,
                dispatched_session_id=(
                    spawn_ctx.dispatched_session_id[0] if spawn_ctx.dispatched_session_id else ""
                ),
                dispatch_id=dispatch_id,
                managed_lineage_ref=managed_lineage_ref,
                provenance=provenance,
                state_path=state_path,
                effective_name=effective_name,
                tool_ctx=tool_ctx,
            ),
        )
    if (
        skill_result is not None
        and skill_result.session_id
        and not spawn_ctx.dispatched_session_id
    ):
        _on_session_id(skill_result.session_id)

    ended_at = max(time.time(), started_at + 1e-6)
    _dispatch_completed_normally = True

    return ExecutionResult(
        skill_result=skill_result,
        spawn_context=spawn_ctx,
        started_at=started_at,
        ended_at=ended_at,
        dispatch_completed_normally=_dispatch_completed_normally,
        marker_dir=marker_dir,
        dispatch_sidecar_path=dispatch_sidecar_path,
        spawn_failure_dispatch_result=None,
    )
