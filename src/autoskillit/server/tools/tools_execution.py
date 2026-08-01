"""MCP tool handlers: run_cmd, run_python, run_skill."""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

import anyio
import regex as re
import structlog
from fastmcp import Context
from fastmcp.dependencies import CurrentContext

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    CODEX_SESSIONS_SUBDIR,
    DISPATCH_ID_ENV_VAR,
    RECIPE_EXECUTION_ATTESTATION_MISSING_MESSAGE,
    RECIPE_EXECUTION_INACTIVE_MESSAGE,
    SKILL_COMMAND_DISPLAY_MAX,
    WORKTREE_SKILLS,
    AuditAttemptId,
    AuditCycleVerificationError,
    AuditCycleVerifier,
    AuditIdentityReservation,
    AuditMaterializationResult,
    AuditMaterializationStatus,
    AuditOutcome,
    AuditOutcomeStatus,
    AuditPrepareRequest,
    AuditReservationRequest,
    AuditResultOutcome,
    AuditVerdict,
    BoundScalar,
    ClosureAuthoritySpec,
    CodingAgentBackend,
    EffectiveSkillInvocationAuthority,
    InvocationTemplate,
    KillReason,
    ManagedHeadlessSessionLineageRef,
    NativeShellCaptureDecision,
    RecipeExecutionId,
    ReservationDecision,
    SkillContractError,
    SkillExecutionRole,
    SkillResult,
    TerminationReason,
    ValidatedAddDir,
    WriteBehaviorSpec,
    closure_authority_spec_from_args,
    compute_audit_slot_intent_digest,
    compute_bytes_hash,
    compute_runtime_binding_digest,
    execution_marker,
    extract_skill_name,
    find_caller_session_id,
    get_logger,
    is_feature_enabled,
    parse_plan_paths,
    render_target_skill_command,
    resolve_temp_dir,
)
from autoskillit.core import current_order_id as _current_order_id
from autoskillit.core import current_step_name as _current_step_name
from autoskillit.core import resolve_skill_temp_dir as _resolve_skill_temp_dir
from autoskillit.execution import (
    CaptureSetupError,
)
from autoskillit.pipeline import canonical_step_name as _canonical_step_name
from autoskillit.pipeline import gate_error_result
from autoskillit.server import mcp
from autoskillit.server._audit_authority_materializer import (
    derive_initial_lifecycle_ids,
    load_current_prior_authority,
    normalize_audited_plan_refs,
)
from autoskillit.server._guards import (
    _check_dry_walkthrough,
    _check_input_contracts,
    _check_recipe_read_prohibition,
    _check_write_target_boundary,
    _require_enabled,
    _require_orchestrator_exact,
    _require_orchestrator_or_higher,
    _validate_skill_command,
)
from autoskillit.server._misc import (
    SCENARIO_STEP_NAME_ENV,
    SkillProjectionContext,
    _hook_config_overlay_path,
    resolve_closure_write_dirs,
)
from autoskillit.server._misc import (
    get_backend as _get_backend,
)
from autoskillit.server._notify import _notify, track_response_size
from autoskillit.server._recipe_execution import (
    RecipeExecutionAdmissionError,
    bind_attested_runtime_invocation,
    build_bound_child_prompt,
    build_standalone_child_prompt,
    get_recipe_execution,
    record_runtime_binding_digest,
    resolve_attested_input_preflight,
)
from autoskillit.server._recipe_execution import (
    complete_audit_finalization_effects as _complete_audit_finalization_effects,
)
from autoskillit.server._recipe_execution import (
    required_audit_finalization_effect_names as _required_audit_finalization_effect_names,
)
from autoskillit.server._subprocess import _run_subprocess_captured
from autoskillit.server.tools._backend_compat import (
    _check_backend_compat,
    _is_backend_incompatible,  # noqa: F401  (re-exported for tests/arch/test_cross_registry_dispatch_sufficiency.py and tests/server/test_run_skill_backend_compat.py)
)
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._execution_helpers import (
    AuditOutputMode,
    _import_and_call,
    _RunSkillContractLifecycle,
    _spill_spec,
    _summarize_streams,
    bind_projection_backend,
    build_fresh_projection_context,
    build_validated_skill_dispatch_contract,
    clear_run_skill_state,
    invocation_member_names,
    maybe_promote_work_dir,
    persist_run_skill_state,
    propagate_session_deadline,
    resolve_relative_path_args,
    resolve_skill_dispatch_metadata,
    run_cmd_artifact_root,
    select_audit_output_contract,
    server_injected_run_python_args,
    shape_execution_response,
    spill_run_cmd_result,
    validate_path_arg_anchoring,
)
from autoskillit.server.tools._execution_helpers import (
    aggregate_sandbox_overrides as _aggregate_sandbox_overrides,
)
from autoskillit.server.tools._execution_helpers import (
    build_skill_session_contract as _build_skill_session_contract,
)
from autoskillit.server.tools._execution_helpers import (
    check_review_approach_plan_path as _check_review_approach_plan_path,
)
from autoskillit.server.tools._execution_helpers import (
    compute_write_prefixes as _compute_write_prefixes,
)
from autoskillit.server.tools._execution_helpers import (
    derive_run_cmd_write_prefixes as _derive_run_cmd_write_prefixes,
)
from autoskillit.server.tools._execution_helpers import (
    get_routing_caps as _get_routing_caps,
)
from autoskillit.server.tools._execution_helpers import (
    has_routing_capability as _has_routing_capability,
)
from autoskillit.server.tools._execution_helpers import (
    make_project_skill_resolver as _make_project_skill_resolver,
)
from autoskillit.server.tools._execution_helpers import (
    rehydrate_skill_invocation as _rehydrate_skill_invocation,
)
from autoskillit.server.tools._execution_helpers import (
    resolve_step_name_from_recipe as _resolve_step_name_from_recipe,
)
from autoskillit.server.tools._execution_helpers import (
    scope_covers_cwd as _scope_covers_cwd,
)
from autoskillit.server.tools._execution_helpers import (
    serialize_skill_contract as _serialize_skill_contract,
)
from autoskillit.server.tools._execution_helpers import (
    validate_resumed_skill_contract as _validate_resumed_skill_contract,
)
from autoskillit.server.tools._native_shell_capture import (
    prepare_skill_native_shell_lineage,
    rebind_verified_final_session,
)
from autoskillit.server.tools._preflight import (
    _get_fix_required_hook_matchers,  # noqa: F401  (re-exported for tests/server/test_admission_dispatch_agreement.py)
)
from autoskillit.server.tools._types import ToolFailureEnvelope, deny_envelope

logger = get_logger(__name__)

_PURE_SLEEP_RE = re.compile(
    r'^(?:python3?\s+-c\s+["\']import time;\s*time\.sleep\((?P<py_secs>\d+(?:\.\d+)?)\)["\']'
    r"|sleep\s+(?P<sh_secs>\d+(?:\.\d+)?))$"
)

INGREDIENT_LOCK_DENY_PREFIX = "INGREDIENT LOCK ENFORCED"
DEPENDENCY_DENY_PREFIX = "DEPENDENCY UNMET"


def _recipe_execution_deny(code: str, message: str) -> str:
    return json.dumps(
        deny_envelope(
            f"RECIPE EXECUTION REJECTED [{code}]: {message}",
            stage="preflight:recipe_execution",
            retriable=False,
        )
    )


def _audit_preflight_step_names(
    tool_ctx: ToolContext,
    installed,
) -> tuple[str, ...]:
    resolver = tool_ctx.skill_contract_resolver
    if resolver is None:
        raise RecipeExecutionAdmissionError(
            "audit_preflight_resolver_unavailable",
            "audit publication requires the compiled skill-contract resolver",
        )
    names = tuple(
        step_name
        for step_name, template in installed.snapshot.templates.items()
        if (
            template.invocation.skill_name is not None
            and (contract := resolver(f"/autoskillit:{template.invocation.skill_name}"))
            is not None
            and getattr(contract, "input_preflight", None) == "audit_cycle_inventory"
        )
    )
    if not names:
        raise RecipeExecutionAdmissionError(
            "audit_preflight_consumers_missing",
            "audit publication has no compiled preflight consumer steps",
        )
    return names


def _audit_response(
    *,
    status: AuditOutcomeStatus,
    attempt_id: AuditAttemptId,
    verdict: AuditVerdict | None,
    path: Path | None,
    error: str | None,
    kill_reason: KillReason = KillReason.NATURAL_EXIT,
) -> str:
    success = status in {
        AuditOutcomeStatus.PUBLISHED,
        AuditOutcomeStatus.EXACT_REPLAY,
    }
    return json.dumps(
        {
            "success": success,
            "exit_code": 0 if success else 1,
            "kill_reason": kill_reason.value,
            "result": (
                f"Server-authored audit outcome: {status.value}"
                if success
                else f"Audit admission failed: {error or status.value}"
            ),
            "audit_status": status.value,
            "audit_verdict": verdict.value if verdict is not None else None,
            "audit_cycle_path": str(path) if path is not None else None,
            "audit_attempt_id": attempt_id.value,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _materialization_outcome_status(
    result: AuditMaterializationResult,
) -> AuditOutcomeStatus:
    match result.status:
        case AuditMaterializationStatus.PUBLISHED_PENDING_FINALIZATION:
            return AuditOutcomeStatus.PUBLISHED
        case AuditMaterializationStatus.SEMANTIC_REJECTED:
            return AuditOutcomeStatus.SEMANTIC_REJECTED
        case AuditMaterializationStatus.CONFLICT:
            return AuditOutcomeStatus.CONFLICT
        case AuditMaterializationStatus.STORAGE_FAILURE:
            return AuditOutcomeStatus.STORAGE_FAILURE
        case AuditMaterializationStatus.QUARANTINED:
            return AuditOutcomeStatus.QUARANTINED


def _reject_missing_semantic_result(
    tool_ctx: ToolContext,
    reservation: AuditIdentityReservation,
) -> AuditMaterializationResult:
    """Terminally reject a successful child that omitted its semantic artifact."""
    prepared = tool_ctx.audit_admission_ledger.prepare(
        AuditPrepareRequest(
            attempt_id=reservation.current_attempt_id,
            installation_version=reservation.slot_key.installation_version,
            semantic_digest=compute_bytes_hash(b""),
            accepted=False,
        )
    )
    if prepared.conflict_detail is not None:
        return AuditMaterializationResult(
            status=AuditMaterializationStatus.CONFLICT,
            attempt_id=reservation.current_attempt_id,
            verdict=None,
            path=None,
            error=prepared.conflict_detail,
        )
    return AuditMaterializationResult(
        status=AuditMaterializationStatus.SEMANTIC_REJECTED,
        attempt_id=reservation.current_attempt_id,
        verdict=None,
        path=None,
        error="successful audit child omitted audit_semantic_result_path",
    )


def _complete_resumed_audit(
    tool_ctx: ToolContext,
    *,
    result: AuditMaterializationResult,
    skill_command: str,
    step_name: str,
    order_id: str,
) -> str:
    status = _materialization_outcome_status(result)
    if status is AuditOutcomeStatus.PUBLISHED:
        assert result.verdict is not None
        assert result.path is not None
        response = _audit_response(
            status=status,
            attempt_id=result.attempt_id,
            verdict=result.verdict,
            path=result.path,
            error=None,
        )
        replay_payload = json.loads(response)
        replay_payload["audit_status"] = AuditOutcomeStatus.EXACT_REPLAY.value
        replay_response = json.dumps(
            replay_payload,
            ensure_ascii=False,
            separators=(",", ":"),
        )
        _complete_audit_finalization_effects(
            tool_ctx,
            attempt_id=result.attempt_id,
            skill_command=skill_command,
            step_name=step_name,
            order_id=order_id,
            mark_step_complete=_mark_step_complete_server_side,
        )
        outcome = AuditOutcome(
            status=AuditOutcomeStatus.PUBLISHED,
            attempt_id=result.attempt_id,
            verdict=result.verdict,
            path=result.path,
            error=None,
            replay_response_json=replay_response,
        )
        tool_ctx.audit_admission_ledger.finalize_response(
            result.attempt_id,
            outcome,
            required_effect_names=_required_audit_finalization_effect_names(step_name),
        )
        return response
    return _audit_response(
        status=status,
        attempt_id=result.attempt_id,
        verdict=result.verdict,
        path=result.path,
        error=result.error,
    )


def _check_ingredient_locks(step_name: str, order_id: str) -> str | None:
    """Check if step_name is locked out by ingredient locks. Returns deny JSON or None."""
    from autoskillit.server import _get_ctx  # circular-break

    ctx = _get_ctx()
    overlay_path = _hook_config_overlay_path(ctx.project_dir)
    if not overlay_path.exists():
        return None
    try:
        overlay = json.loads(overlay_path.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    locked_steps = overlay.get("locked_steps", {})
    effective_oid = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")

    if effective_oid and effective_oid in locked_steps:
        if locked_steps[effective_oid].get(step_name) is False:
            ingredient_info = overlay.get("locked_ingredients", {}).get(effective_oid, {})
            return json.dumps(
                deny_envelope(
                    (
                        f"{INGREDIENT_LOCK_DENY_PREFIX}: Step '{step_name}' is locked out. "
                        f"Locked ingredients for pipeline '{effective_oid}': {ingredient_info}. "
                        f"Call lock_ingredients(unlock=[...]) to release."
                    ),
                    stage="preflight:ingredient_locks",
                    retriable=False,
                )
            )
    elif not effective_oid:
        for pid, steps in locked_steps.items():
            if steps.get(step_name) is False:
                return json.dumps(
                    deny_envelope(
                        (
                            f"{INGREDIENT_LOCK_DENY_PREFIX}: Step '{step_name}' is locked out "
                            f"by pipeline '{pid}'. Pass order_id to scope the check, "
                            f"or call lock_ingredients(unlock=[...]) to release."
                        ),
                        stage="preflight:ingredient_locks",
                        retriable=False,
                    )
                )
    return None


def _check_pipeline_deps(step_name: str, order_id: str) -> str | None:
    """Check if step_name's dependencies are satisfied. Returns deny JSON or None."""
    from autoskillit.server import _get_ctx  # circular-break
    from autoskillit.server.tools.tools_pipeline_tracker import (  # circular-break
        ResolutionRefusal,
        resolve_tracker_order_id,
    )

    ctx = _get_ctx()
    resolved = resolve_tracker_order_id(ctx, order_id)
    if isinstance(resolved, ResolutionRefusal):
        if resolved.multi_pipeline:
            return json.dumps(
                deny_envelope(
                    f"{DEPENDENCY_DENY_PREFIX}: {resolved.reason}",
                    stage="preflight:pipeline_deps",
                    retriable=False,
                )
            )
        return None
    if not resolved.path.exists():
        return None
    try:
        tracker = json.loads(resolved.path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    canonical = _canonical_step_name(step_name)
    deps = tracker.get("dependencies", {}).get(canonical, [])
    if not deps:
        return None
    steps = tracker.get("steps", {})
    unmet = [d for d in deps if steps.get(d, {}).get("status") not in ("complete", "skipped")]
    if not unmet:
        return None
    dep_status = {d: steps.get(d, {}).get("status", "unknown") for d in unmet}
    return json.dumps(
        deny_envelope(
            (
                f"{DEPENDENCY_DENY_PREFIX}: Step '{step_name}' requires {unmet} to complete "
                f"first. Pipeline '{resolved.order_id}': {dep_status}."
            ),
            stage="preflight:pipeline_deps",
            retriable=True,
            recovery=(
                "This denial is deterministic but may reflect stale tracker state. "
                "Call record_pipeline_step(op='status') to inspect the current tracker. "
                "If the prerequisite step genuinely has not run, run it first. "
                "If the tracker is stale, escalate with the status output."
            ),
        )
    )


def _has_active_locks(order_id: str) -> bool:
    """Return True if any ingredient locks are actively denying steps."""
    from autoskillit.server import _get_ctx  # circular-break

    ctx = _get_ctx()
    overlay_path = _hook_config_overlay_path(ctx.project_dir)
    if not overlay_path.exists():
        return False
    try:
        overlay = json.loads(overlay_path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    locked_steps = overlay.get("locked_steps", {})
    if not locked_steps:
        return False
    effective_oid = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")
    if effective_oid:
        return any(v is False for v in locked_steps.get(effective_oid, {}).values())
    return any(v is False for steps in locked_steps.values() for v in steps.values())


def _has_active_deps() -> bool:
    """Return True if a kitchen-scoped tracker exists with any dependencies defined."""
    from autoskillit.server import _get_ctx  # circular-break
    from autoskillit.server.tools.tools_pipeline_tracker import (  # circular-break
        ResolvedTracker,
        resolve_tracker_order_id,
    )

    ctx = _get_ctx()
    resolved = resolve_tracker_order_id(ctx, "")
    if not isinstance(resolved, ResolvedTracker):
        return False
    if not resolved.path.exists():
        return False
    try:
        tracker = json.loads(resolved.path.read_text())
    except (json.JSONDecodeError, OSError):
        return False
    return bool(tracker.get("dependencies"))


def _mark_step_complete_server_side(
    tool_ctx: ToolContext,
    step_name: str,
    order_id: str,
) -> dict | None:
    """Mark a pipeline step complete at the run_skill adjudication point.

    Uses the shared ``resolve_tracker_order_id`` so the marker resolves the
    same tracker file as ``_check_pipeline_deps``. Failures are logged but
    never fail the tool call.
    """
    from autoskillit.server.tools.tools_pipeline_tracker import (  # circular-break
        ResolvedTracker,
        mark_step_complete,
        resolve_tracker_order_id,
    )

    try:
        resolved = resolve_tracker_order_id(tool_ctx, order_id)
        if not isinstance(resolved, ResolvedTracker):
            logger.debug("pipeline_marker_skip_no_tracker", reason=resolved.reason)
            return None
        if not resolved.path.exists():
            logger.debug("pipeline_marker_skip_no_file", path=str(resolved.path))
            return None
        result = mark_step_complete(resolved.path, step_name, resolved.order_id)
        if result.get("success"):
            logger.info(
                "pipeline_step_marked_complete",
                step=result["step"],
                order_id=result["order_id"],
                done=result["done"],
                total=result["total"],
            )
        else:
            logger.warning(
                "pipeline_marker_failed",
                error=result.get("error", "unknown"),
            )
        marker = {
            "step": result.get("step", step_name),
            "order_id": resolved.order_id,
            "status": "complete" if result.get("success") else "marker_failed",
        }
        if "advisory" in result:
            marker["advisory"] = result["advisory"]
        return marker
    except Exception:
        logger.warning("pipeline_marker_exception", exc_info=True)
        return None


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield(result_type="run_cmd")
@track_response_size("run_cmd")
async def run_cmd(
    cmd: str,
    cwd: str,
    timeout: int = 600,
    step_name: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Run an arbitrary shell command in the specified directory.

    Args:
        cmd: The full command to run (e.g. "make build").
        cwd: Working directory for the command.
        timeout: Max seconds before killing the process (default 600).
        step_name: Optional YAML step key for wall-clock timing accumulation.

    Never raises.
    """
    if (tier_gate := _require_orchestrator_or_higher("run_cmd")) is not None:
        return tier_gate
    if (gate := _require_enabled()) is not None:
        return gate
    if (gate := _check_recipe_read_prohibition(cmd=cmd)) is not None:
        return gate
    if (
        gate := _check_write_target_boundary(cmd, cwd, _derive_run_cmd_write_prefixes())
    ) is not None:
        return gate
    try:
        with structlog.contextvars.bound_contextvars(tool="run_cmd", cwd=cwd):
            if not _derive_run_cmd_write_prefixes():
                logger.debug(
                    "run_cmd: no write prefixes configured — write boundary guard inactive"
                )
            logger.info("run_cmd", cmd=cmd[:80], cwd=cwd)
            await _notify(
                ctx, "info", f"run_cmd: {cmd[:80]}", "autoskillit.run_cmd", extra={"cwd": cwd}
            )

            from autoskillit.server import _get_ctx  # circular-break

            tool_ctx = _get_ctx()
            _start = time.monotonic()
            try:
                m = _PURE_SLEEP_RE.match(cmd.strip())
                if m:
                    seconds = float(m.group("py_secs") or m.group("sh_secs"))
                    await asyncio.sleep(seconds)
                    return json.dumps(
                        {"success": True, "exit_code": 0, "stdout": "", "stderr": ""}
                    )
                _env: dict[str, str] | None = (
                    {**os.environ, SCENARIO_STEP_NAME_ENV: step_name} if step_name else None
                )
                artifact_root = run_cmd_artifact_root(tool_ctx, cwd)
                _timeout_f = float(timeout)
                try:
                    sub_result = await _run_subprocess_captured(
                        ["bash", "-c", cmd],
                        cwd=cwd,
                        timeout=_timeout_f,
                        env=_env,
                        capture_dir=artifact_root,
                    )
                except CaptureSetupError as exc:
                    result = spill_run_cmd_result(
                        tool_ctx,
                        cwd=cwd,
                        returncode=-1,
                        stdout="",
                        stderr="",
                        capture_error=str(exc),
                    )
                    return json.dumps(result)

                spec = _spill_spec(tool_ctx)
                returncode = sub_result.returncode
                execution_error: str | None = None
                complete = True

                term = sub_result.termination
                if term == TerminationReason.NATURAL_EXIT:
                    returncode = sub_result.returncode
                elif term == TerminationReason.TIMED_OUT:
                    returncode = -1
                    execution_error = f"Process timed out after {_timeout_f}s"
                    complete = False
                elif term == TerminationReason.SIGNAL_DEATH:
                    execution_error = (
                        f"Process died to signal (returncode={sub_result.returncode})"
                    )
                    complete = False
                else:
                    execution_error = f"Unexpected termination: {term.value}"
                    complete = False

                stdout_capture, stderr_capture, capture_error = _summarize_streams(
                    sub_result, spec, complete
                )

                result = spill_run_cmd_result(
                    tool_ctx,
                    cwd=cwd,
                    returncode=returncode,
                    stdout="",
                    stderr="",
                    stdout_capture=stdout_capture,
                    stderr_capture=stderr_capture,
                    capture_error=capture_error,
                    execution_error=execution_error,
                )
                if not result.get("success"):
                    await _notify(
                        ctx,
                        "error",
                        "run_cmd failed",
                        "autoskillit.run_cmd",
                        extra={"exit_code": returncode},
                    )
                return json.dumps(result)
            finally:
                if step_name:
                    tool_ctx.timing_log.record(step_name, time.monotonic() - _start)
    except Exception as exc:
        logger.error("run_cmd unhandled exception", exc_info=True)
        return json.dumps(
            {
                "success": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": f"{type(exc).__name__}: {exc}",
            }
        )


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield(result_type="run_python")
@track_response_size("run_python")
async def run_python(
    callable: str,
    args: dict[str, object] | None = None,
    timeout: int = 30,
    work_dir: str = "",
    ctx: Context = CurrentContext(),
) -> str:
    """Call a Python function directly by dotted module path.

    Imports the module, resolves the function, and calls it with the
    provided arguments. Use for lightweight decision logic that does
    not need an LLM session (counter checks, status lookups, eligibility
    decisions).

    Both sync and async functions are supported. Async functions are
    awaited directly; sync functions run in a thread pool.

    Args:
        callable: Dotted path to the function (e.g. "mypackage.module.function").
        args: Keyword arguments to pass to the function.
        timeout: Max seconds before aborting the call (default 30).
        work_dir: When set, relative path-like args (output_dir, etc.) are
            anchored to this directory before the callable is invoked.

    Never raises.
    """
    if (tier_gate := _require_orchestrator_or_higher("run_python")) is not None:
        return tier_gate
    if (gate := _require_enabled()) is not None:
        return gate
    if (gate := _check_recipe_read_prohibition(callable_name=callable)) is not None:
        return gate
    try:
        from autoskillit.server import _get_ctx  # circular-break

        tool_ctx = _get_ctx()
        with structlog.contextvars.bound_contextvars(tool="run_python"):
            logger.info("run_python", callable=callable, timeout=timeout)
            await _notify(
                ctx,
                "info",
                f"run_python: {callable}",
                "autoskillit.run_python",
                extra={"callable": callable},
            )
            anchor_err = validate_path_arg_anchoring(args, work_dir)
            if anchor_err:
                return json.dumps({"success": False, "error": anchor_err})
            promoted = maybe_promote_work_dir(args, work_dir)
            if promoted != work_dir:
                logger.warning(
                    "run_python auto-promoted work_dir from args to tool level",
                    callable=callable,
                    work_dir=promoted,
                )
                work_dir = promoted
            if work_dir and not Path(work_dir).is_absolute():
                return json.dumps(
                    {
                        "success": False,
                        "error": f"run_python: work_dir must be absolute, got {work_dir!r}",
                    }
                )
            resolved_args = args
            if work_dir:
                resolved_args = resolve_relative_path_args(args or {}, work_dir)
            result = await _import_and_call(
                callable,
                args=resolved_args,
                timeout=float(timeout),
                server_injected_args=server_injected_run_python_args(callable, tool_ctx),
            )
            if not result.get("success"):
                await _notify(
                    ctx,
                    "error",
                    "run_python failed",
                    "autoskillit.run_python",
                    extra={"callable": callable},
                )
            return shape_execution_response(
                tool_ctx,
                result,
                tool_name="run_python",
                work_dir=work_dir,
            )
    except Exception as exc:
        logger.error("run_python unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield()
@track_response_size("run_skill")
async def run_skill(
    skill_command: str,
    cwd: str,
    model: str = "",
    step_name: str = "",
    recipe_execution_id: str = "",
    invocation_template_digest: str = "",
    step_provider: str = "",
    order_id: str = "",
    stale_threshold: int | None = None,
    idle_output_timeout: int | None = None,
    output_dir: str = "",
    resume_session_id: str = "",
    retry_after_audit_attempt_id: str = "",
    native_shell_capture_mode: str = "",
    closure_authority_path: str = "",
    closure_authority_hash: str = "",
    closure_plan_paths: str = "",
    closure_base_sha: str = "",
    closure_diff_sha: str = "",
    closure_target_sha: str = "",
    skill_inputs: dict[str, str | int | bool] | None = None,
    ctx: Context = CurrentContext(),
) -> str:
    """Delegate one already-selected recipe step to a separate L1 headless coding-agent worker.

    Use this tool only when a headless recipe orchestrator operating at L2 or an
    interactive AutoSkillit cook/order session intends separate-worker delegation. The
    recipe step must already be selected before this call.

    When a user names or asks to use an available local skill for the current interactive
    conversation, load and follow its SKILL.md in the current interactive session.
    Do not call run_skill merely because the skill was named.

    Returns JSON with success, result, session_id, subtype, is_error, exit_code, needs_retry,
    and retry_reason. When needs_retry is true, follow the recipe's declared retry route.

    Args:
        skill_command: Full recipe-declared skill invocation or resume continuation.
        cwd: Absolute working directory for the separate coding-agent worker.
        model: Optional model identifier. Empty string uses the configured default.
        step_name: Optional YAML step key (e.g. "implement"). When set, token usage is
            accumulated in the server-side token log, grouped by this name.
        order_id: Optional per-issue/order identifier for token telemetry scoping. When set,
            token and timing entries are keyed by this value, enabling per-issue isolation
            in get_token_summary/get_timing_summary and in the token_summary_appender hook.
        stale_threshold: Override the staleness kill threshold in seconds. When set on
            a RecipeStep, the recipe orchestrator passes it here. None uses the global
            config default (RunSkillConfig.stale_threshold, default 1200s).
        idle_output_timeout: Override the idle stdout kill threshold in seconds.
            0 = disabled for this step. None = use global config
            (RunSkillConfig.idle_output_timeout, default 1000s).
        resume_session_id: Session ID from a previous run_skill call that was interrupted.
            When set, resume that coding-agent session instead of starting fresh. The
            skill_command becomes a continuation instruction; pass the prior result's session_id.
        retry_after_audit_attempt_id: Server-issued rejected audit attempt to correct.
            This is attested control data and is never passed to the child as a skill input.
        native_shell_capture_mode: Optional managed Codex shell I/O mode. Omission
            defaults fresh launches to capture. Resumes inherit their durable lineage.

    Never raises.
    """
    if (tier_gate := _require_orchestrator_exact("run_skill")) is not None:
        return tier_gate
    if (gate := _require_enabled()) is not None:
        return gate
    if cwd and not Path(cwd).is_absolute():
        return json.dumps(
            deny_envelope(
                (
                    f"run_skill: cwd must be an absolute path, got: {cwd!r}. "
                    "Check that the skill resolved the worktree_path to absolute "
                    '(e.g. WORKTREE_PATH="$(cd "${WORKTREE_PATH}" && pwd)").'
                ),
                stage="preflight:cwd",
                retriable=False,
            )
        )
    if cwd and not os.path.isdir(cwd):
        return json.dumps(
            deny_envelope(
                f"run_skill: cwd does not exist: {cwd}",
                stage="preflight:cwd",
                retriable=False,
            )
        )
    if (
        step_name
        and not resume_session_id
        and (_lock_denial := _check_ingredient_locks(step_name, order_id)) is not None
    ):
        return _lock_denial
    if (
        step_name
        and not resume_session_id
        and (_dep_denial := _check_pipeline_deps(step_name, order_id)) is not None
    ):
        return _dep_denial
    if (
        step_name
        and not resume_session_id
        and not (recipe_execution_id or invocation_template_digest)
        and (_plan_path_denial := _check_review_approach_plan_path(step_name, skill_command))
        is not None
    ):
        return _plan_path_denial
    try:
        contract_lifecycle = _RunSkillContractLifecycle()
        _sn_token = _oid_token = None
        from autoskillit.server import _get_ctx  # circular-break

        _cleanup_session_id: str | None = None
        tool_ctx = _get_ctx()
        _installed_execution = get_recipe_execution(tool_ctx)
        _contract_store = tool_ctx.skill_session_contract_store
        contract_lifecycle.store = _contract_store
        _stored_contract_entry = None
        _session_contract = None
        _session_snapshot = None
        _native_shell_capture_decision: NativeShellCaptureDecision | None = None
        _managed_lineage_ref: ManagedHeadlessSessionLineageRef | None = None
        _resume_backend_obj: CodingAgentBackend | None = None
        _effective_skill_resolver = None
        invocation: EffectiveSkillInvocationAuthority | None = None
        projection_context: SkillProjectionContext | None = None
        target_name: str | None = None
        if resume_session_id:
            try:
                _stored_contract_entry = _contract_store.load(resume_session_id)
                _resume_backend_obj = _get_backend(_stored_contract_entry.contract.backend)
                _validate_resumed_skill_contract(
                    _stored_contract_entry.contract,
                    cwd=cwd,
                    project_root=tool_ctx.project_dir,
                    backend=_resume_backend_obj,
                )
                if _resume_backend_obj is None:
                    raise SkillContractError("Resume contract backend is unavailable")
                invocation, projection_context = _rehydrate_skill_invocation(
                    _stored_contract_entry.contract,
                    _resume_backend_obj,
                )
            except (OSError, ValueError, SkillContractError) as exc:
                return SkillResult.crashed(
                    exception=SkillContractError(
                        f"Cannot resume session {resume_session_id!r}: {exc}"
                    ),
                    skill_command=skill_command,
                    order_id=order_id,
                ).to_json()
            contract_lifecycle.bound_session_id = resume_session_id
            target_name = _stored_contract_entry.contract.root_name
        else:
            if (cmd_error := _validate_skill_command(skill_command)) is not None:
                return cmd_error
            _effective_skill_resolver = tool_ctx.skill_resolver
            if _effective_skill_resolver is None:
                _effective_skill_resolver = _make_project_skill_resolver()
            target_name = extract_skill_name(skill_command)
            if target_name is None:
                return SkillResult.crashed(
                    exception=SkillContractError(
                        f"Cannot resolve a logical skill target from {skill_command!r}"
                    ),
                    skill_command=skill_command,
                    order_id=order_id,
                ).to_json()
            try:
                invocation = _effective_skill_resolver.resolve_invocation(
                    target_name,
                    tool_ctx.project_dir,
                    SkillExecutionRole.SESSION,
                    visibility=tool_ctx.config.skill_visibility_spec(),
                    recipe_packs=tool_ctx.active_recipe_packs,
                    recipe_features=tool_ctx.active_recipe_features,
                )
                projection_context = build_fresh_projection_context(cwd, invocation)
            except SkillContractError as exc:
                return SkillResult.crashed(
                    exception=exc,
                    skill_command=skill_command,
                    order_id=order_id,
                ).to_json()
            if _installed_execution is None and not step_name and tool_ctx.active_recipe_steps:
                _resolved, _ambiguous = _resolve_step_name_from_recipe(
                    skill_command, tool_ctx.active_recipe_steps
                )
                if _resolved:
                    step_name = _resolved
                    logger.warning(
                        "step_name_resolved_from_recipe",
                        step=step_name,
                        command=skill_command[:80],
                    )
                    if (_lock_denial := _check_ingredient_locks(step_name, order_id)) is not None:
                        return _lock_denial
                    if (_dep_denial := _check_pipeline_deps(step_name, order_id)) is not None:
                        return _dep_denial
                    if (
                        _plan_path_denial := _check_review_approach_plan_path(
                            step_name, skill_command
                        )
                    ) is not None:
                        return _plan_path_denial
                elif _ambiguous:
                    if _has_active_deps():
                        return json.dumps(
                            deny_envelope(
                                (
                                    f"{DEPENDENCY_DENY_PREFIX}: step_name is empty and matched "
                                    "multiple recipe steps by skill_command prefix (ambiguous). "
                                    "Cannot verify dependency status. Pass step_name explicitly."
                                ),
                                stage="preflight:ambiguous_step",
                                retriable=False,
                            )
                        )
                elif _has_active_locks(order_id):
                    return json.dumps(
                        deny_envelope(
                            (
                                f"{INGREDIENT_LOCK_DENY_PREFIX}: step_name is empty and could "
                                "not be resolved from the recipe. Cannot verify lock "
                                "status. Pass step_name explicitly or call "
                                "lock_ingredients(unlock=[...]) to release all locks."
                            ),
                            stage="preflight:ingredient_locks",
                            retriable=False,
                        )
                    )
                elif _has_active_deps():
                    return json.dumps(
                        deny_envelope(
                            (
                                f"{DEPENDENCY_DENY_PREFIX}: step_name is empty and could "
                                "not be resolved from the recipe. Cannot verify dependency "
                                "status. Pass step_name explicitly."
                            ),
                            stage="preflight:unresolved_step",
                            retriable=False,
                        )
                    )
        if invocation is None or projection_context is None:
            raise SkillContractError("Skill dispatch branches did not produce a bound contract")

        _preflight_result = None
        _bound_recipe_inputs: tuple[tuple[str, BoundScalar], ...] = ()
        _invocation_template: InvocationTemplate | None = None
        _audit_reservation: AuditIdentityReservation | None = None
        _audit_preflight_steps: tuple[str, ...] = ()
        _target_contract = (
            tool_ctx.skill_contract_resolver(skill_command)
            if (_stored_contract_entry is None and tool_ctx.skill_contract_resolver is not None)
            else None
        )
        _audit_publication = getattr(
            _target_contract,
            "audit_authority_publication",
            None,
        )
        child_skill_command = skill_command
        _claims_recipe_execution = bool(recipe_execution_id or invocation_template_digest)
        _dynamic_recipe_call = bool(
            _installed_execution is not None
            and step_name
            and step_name in _installed_execution.snapshot.dynamic_skill_step_names
        )
        _audit_output_mode: AuditOutputMode | None = None
        if _audit_publication is not None and not resume_session_id:
            _audit_output_mode = (
                AuditOutputMode.ATTESTED
                if _installed_execution is not None and not _dynamic_recipe_call
                else AuditOutputMode.STANDALONE
            )
            if _target_contract is None:
                raise SkillContractError("audit output contract is unavailable")
            select_audit_output_contract(_target_contract, _audit_output_mode)
        # Resolved from cwd so the audit-cycle containment anchor matches the
        # clone's actual artifact directory (orchestrator's tool_ctx.temp_dir
        # is disjoint from the clone's temp tree in clone-based pipelines).
        # See #4387 — this must be defined BEFORE the if/elif chain so the
        # publish call site (later in the function) can reach it regardless
        # of which branch was taken above. An empty cwd is only rejected here
        # (rather than by the earlier boundary guards) because it is only
        # security-relevant once a recipe execution is active and this anchor
        # is actually consumed as a containment root — ad-hoc skill calls with
        # no active recipe execution never read _clone_allowed_root.
        if _installed_execution is not None and not cwd:
            return json.dumps(
                deny_envelope(
                    "run_skill: cwd must not be empty when a recipe execution is active.",
                    stage="preflight:cwd",
                    retriable=False,
                )
            )
        _clone_allowed_root = resolve_temp_dir(Path(cwd), tool_ctx.config.workspace.temp_dir)
        if _dynamic_recipe_call:
            if _claims_recipe_execution:
                return _recipe_execution_deny(
                    "recipe_execution_dynamic_attestation",
                    "a dynamic recipe skill step cannot claim a concrete invocation template",
                )
            if not resume_session_id:
                try:
                    child_skill_command = build_standalone_child_prompt(
                        skill_command,
                        cwd,
                        skill_inputs,
                        audit_output_mode=_audit_output_mode,
                    )
                except RecipeExecutionAdmissionError as exc:
                    return _recipe_execution_deny(exc.code, str(exc))
        elif _installed_execution is not None:
            if not recipe_execution_id or not invocation_template_digest:
                return _recipe_execution_deny(
                    "recipe_execution_attestation_missing",
                    RECIPE_EXECUTION_ATTESTATION_MISSING_MESSAGE,
                )
            if not step_name:
                return _recipe_execution_deny(
                    "recipe_execution_step_missing",
                    "an attested recipe invocation requires its exact step_name",
                )
            _actual_mcp_kwargs: dict[str, BoundScalar] = {
                "skill_command": skill_command,
                "cwd": cwd,
                "model": model,
                "step_name": step_name,
                "recipe_execution_id": recipe_execution_id,
                "invocation_template_digest": invocation_template_digest,
                "step_provider": step_provider,
                "order_id": order_id,
                "output_dir": output_dir,
                "resume_session_id": resume_session_id,
                "closure_authority_path": closure_authority_path,
                "closure_authority_hash": closure_authority_hash,
                "closure_plan_paths": closure_plan_paths,
                "closure_base_sha": closure_base_sha,
                "closure_diff_sha": closure_diff_sha,
                "closure_target_sha": closure_target_sha,
                "retry_after_audit_attempt_id": retry_after_audit_attempt_id,
                "native_shell_capture_mode": native_shell_capture_mode,
            }
            if stale_threshold is not None:
                _actual_mcp_kwargs["stale_threshold"] = stale_threshold
            if idle_output_timeout is not None:
                _actual_mcp_kwargs["idle_output_timeout"] = idle_output_timeout
            try:
                _bound_recipe_inputs, _invocation_template = bind_attested_runtime_invocation(
                    _installed_execution,
                    execution_id=recipe_execution_id,
                    step_name=step_name,
                    template_digest=invocation_template_digest,
                    skill_command=skill_command,
                    skill_inputs=skill_inputs,
                    actual_mcp_kwargs=_actual_mcp_kwargs,
                )
            except RecipeExecutionAdmissionError as exc:
                return _recipe_execution_deny(exc.code, str(exc))
            try:
                _preflight_result = resolve_attested_input_preflight(
                    tool_ctx,
                    _installed_execution,
                    skill_command=skill_command,
                    execution_id=recipe_execution_id,
                    step_name=step_name,
                    template=_invocation_template,
                    bound_inputs=_bound_recipe_inputs,
                    allowed_root=_clone_allowed_root,
                )
            except RecipeExecutionAdmissionError as exc:
                return _recipe_execution_deny(exc.code, str(exc))
            _runtime_digest = compute_runtime_binding_digest(
                execution_id=recipe_execution_id,
                step_name=step_name,
                template_digest=invocation_template_digest,
                bound_inputs=_bound_recipe_inputs,
                actual_mcp_kwargs=_actual_mcp_kwargs,
                preflight=_preflight_result,
                retry_after_audit_attempt_id=retry_after_audit_attempt_id or None,
            )
            try:
                _installed_execution = record_runtime_binding_digest(
                    tool_ctx,
                    execution_id=recipe_execution_id,
                    step_name=step_name,
                    digest=_runtime_digest,
                )
            except RecipeExecutionAdmissionError as exc:
                return _recipe_execution_deny(exc.code, str(exc))
            if _audit_publication is not None:
                try:
                    _slot_intent_digest = compute_audit_slot_intent_digest(
                        execution_id=recipe_execution_id,
                        step_name=step_name,
                        template_digest=invocation_template_digest,
                        bound_inputs=_bound_recipe_inputs,
                        actual_mcp_kwargs=_actual_mcp_kwargs,
                        preflight=_preflight_result,
                        retry_after_audit_attempt_id=(retry_after_audit_attempt_id or None),
                    )
                    _bound_input_map = dict(_bound_recipe_inputs)
                    _prior_input_field = _audit_publication.prior_input_field
                    _prior_path = _bound_input_map.get(_prior_input_field)
                    _recipe_execution_key = RecipeExecutionId(recipe_execution_id)
                    if isinstance(_prior_path, str) and _prior_path:
                        _prior_authority = load_current_prior_authority(
                            _prior_path,
                            allowed_root=_clone_allowed_root,
                            ledger=tool_ctx.audit_admission_ledger,
                            recipe_execution_id=_recipe_execution_key,
                        )
                        _audited_plan_refs = (
                            normalize_audited_plan_refs(
                                str(_bound_input_map.get("all_plan_paths") or ""),
                                allowed_root=_clone_allowed_root,
                            )
                            if _bound_input_map.get("all_plan_paths")
                            else _prior_authority.audited_plan_refs
                        )
                        _cycle_id = _prior_authority.cycle_id
                        _scope_id = _prior_authority.scope_id
                        _part_id = _prior_authority.part_id
                        _parent_digest = _prior_authority.authority_digest
                    else:
                        _audited_plan_refs = normalize_audited_plan_refs(
                            str(_bound_input_map.get("all_plan_paths") or ""),
                            allowed_root=_clone_allowed_root,
                        )
                        _cycle_id, _scope_id, _part_id = derive_initial_lifecycle_ids(
                            recipe_execution_id=_recipe_execution_key,
                            step_name=step_name,
                            slot_intent_digest=_slot_intent_digest,
                        )
                        _parent_digest = None
                    _audit_preflight_steps = _audit_preflight_step_names(
                        tool_ctx,
                        _installed_execution,
                    )
                    with tool_ctx.recipe_execution_lock:
                        if get_recipe_execution(tool_ctx) is not _installed_execution:
                            raise RecipeExecutionAdmissionError(
                                "recipe_execution_replaced",
                                "active recipe execution changed before audit reservation",
                            )
                        _reservation_outcome = tool_ctx.audit_admission_ledger.reserve(
                            AuditReservationRequest(
                                recipe_execution_id=_recipe_execution_key,
                                installation_version=(_installed_execution.installation_version),
                                step_name=step_name,
                                invocation_template_digest=(invocation_template_digest),
                                slot_intent_digest=_slot_intent_digest,
                                runtime_binding_digest=_runtime_digest,
                                audited_plan_refs=_audited_plan_refs,
                                cycle_id=_cycle_id,
                                scope_id=_scope_id,
                                part_id=_part_id,
                                allowed_root=_clone_allowed_root,
                                parent_authority_digest=_parent_digest,
                                retry_after_audit_attempt_id=(
                                    AuditAttemptId(retry_after_audit_attempt_id)
                                    if retry_after_audit_attempt_id
                                    else None
                                ),
                            )
                        )
                    match _reservation_outcome.decision:
                        case (
                            ReservationDecision.DISPATCH_NEW | ReservationDecision.REDISPATCH_OPEN
                        ):
                            assert _reservation_outcome.reservation is not None
                            assert _reservation_outcome.reservation_handle is not None
                            _audit_reservation = _reservation_outcome.reservation
                            child_skill_command = build_bound_child_prompt(
                                skill_command,
                                _bound_recipe_inputs,
                                _preflight_result,
                                audit_reservation_handle=(_reservation_outcome.reservation_handle),
                                audit_reserved_plan_refs=_audited_plan_refs,
                                audit_output_mode=_audit_output_mode,
                            )
                        case ReservationDecision.EXACT_REPLAY:
                            assert _reservation_outcome.replay_outcome is not None
                            _replay = _reservation_outcome.replay_outcome
                            if _replay.replay_response_json is not None:
                                return _replay.replay_response_json
                            return _audit_response(
                                status=AuditOutcomeStatus.EXACT_REPLAY,
                                attempt_id=_replay.attempt_id,
                                verdict=_replay.verdict,
                                path=_replay.path,
                                error=_replay.error,
                                kill_reason=_replay.kill_reason,
                            )
                        case ReservationDecision.RESUME_PREPARED:
                            assert _reservation_outcome.reservation is not None
                            with tool_ctx.recipe_execution_lock:
                                if get_recipe_execution(tool_ctx) is not _installed_execution:
                                    raise RecipeExecutionAdmissionError(
                                        "recipe_execution_replaced",
                                        "active recipe execution changed before audit recovery",
                                    )
                                _resumed = tool_ctx.audit_authority_materializer.materialize(
                                    reservation=_reservation_outcome.reservation,
                                    semantic_result_path=(
                                        _reservation_outcome.reservation.semantic_result_path
                                    ),
                                    preflight_step_names=_audit_preflight_steps,
                                )
                            return _complete_resumed_audit(
                                tool_ctx,
                                result=_resumed,
                                skill_command=skill_command,
                                step_name=step_name,
                                order_id=order_id,
                            )
                        case ReservationDecision.PUBLISHED_PENDING_FINALIZATION:
                            assert _reservation_outcome.reservation is not None
                            _authority = AuditCycleVerifier(_clone_allowed_root).load_authority(
                                _reservation_outcome.reservation.authority_path
                            )
                            _published = AuditMaterializationResult(
                                status=(AuditMaterializationStatus.PUBLISHED_PENDING_FINALIZATION),
                                attempt_id=_reservation_outcome.attempt_id,
                                verdict=_authority.verdict,
                                path=_reservation_outcome.reservation.authority_path,
                                error=None,
                            )
                            return _complete_resumed_audit(
                                tool_ctx,
                                result=_published,
                                skill_command=skill_command,
                                step_name=step_name,
                                order_id=order_id,
                            )
                        case ReservationDecision.CONFLICT:
                            return _audit_response(
                                status=AuditOutcomeStatus.CONFLICT,
                                attempt_id=_reservation_outcome.attempt_id,
                                verdict=None,
                                path=None,
                                error=_reservation_outcome.conflict_detail,
                            )
                except (
                    AuditCycleVerificationError,
                    OSError,
                    RecipeExecutionAdmissionError,
                    ValueError,
                ) as exc:
                    code = (
                        exc.code
                        if isinstance(exc, RecipeExecutionAdmissionError)
                        else "audit_reservation_failed"
                    )
                    return _recipe_execution_deny(code, str(exc))
            else:
                child_skill_command = build_bound_child_prompt(
                    skill_command,
                    _bound_recipe_inputs,
                    _preflight_result,
                    audit_output_mode=_audit_output_mode,
                )
        elif _claims_recipe_execution:
            return _recipe_execution_deny(
                "recipe_execution_inactive",
                RECIPE_EXECUTION_INACTIVE_MESSAGE,
            )
        elif not resume_session_id:
            try:
                child_skill_command = build_standalone_child_prompt(
                    skill_command,
                    cwd,
                    skill_inputs,
                    audit_output_mode=_audit_output_mode,
                )
            except RecipeExecutionAdmissionError as exc:
                return _recipe_execution_deny(
                    exc.code,
                    str(exc),
                )

        with structlog.contextvars.bound_contextvars(tool="run_skill", cwd=cwd):
            logger.info("run_skill", command=skill_command[:80], cwd=cwd)
            await _notify(
                ctx,
                "info",
                f"run_skill: {skill_command[:80]}",
                "autoskillit.run_skill",
                extra={"cwd": cwd, "model": model or "default"},
            )

            from autoskillit.server import _get_config  # circular-break

            # Auto-enrich order_id from the fleet dispatcher's env variable when the
            # caller did not pass an explicit value. AUTOSKILLIT_DISPATCH_ID is injected
            # by fleet/_api.py into every L2 food truck session environment and inherited by all
            # sub-sessions, ensuring token log entries carry the correct order_id without
            # requiring recipe authors to thread it through every run_skill call.
            effective_order_id = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")

            if not resume_session_id and _installed_execution is None and skill_inputs is None:
                if (
                    input_error := _check_input_contracts(
                        skill_command, cwd, tool_ctx.input_contract_resolver
                    )
                ) is not None:
                    return input_error

            if _get_config().safety.require_dry_walkthrough and _installed_execution is None:
                if (gate_error := _check_dry_walkthrough(skill_command, cwd)) is not None:
                    return gate_error

            if tool_ctx.executor is None:
                return json.dumps({"success": False, "error": "Executor not configured"})

            provider_extras: dict[str, str] | None = None
            profile_name_out: str = ""
            effective_model = model

            _cfg = _get_config()
            _in_fleet_dispatch = bool(os.environ.get(DISPATCH_ID_ENV_VAR))
            _inspector_model = _cfg.fleet.inspector_model if _in_fleet_dispatch else ""

            if not step_provider and step_name and tool_ctx.active_recipe_steps is not None:
                _recipe_step_pre = tool_ctx.active_recipe_steps.get(step_name)
                if _recipe_step_pre is not None and _recipe_step_pre.provider:
                    step_provider = _recipe_step_pre.provider
                    logger.warning(
                        "step_provider_resolved_from_recipe",
                        step=step_name,
                        provider=step_provider,
                    )

            if is_feature_enabled(
                "providers", _cfg.features, experimental_enabled=_cfg.experimental_enabled
            ):
                from autoskillit.server._guards import (  # circular-break
                    _resolve_model_as_profile,
                    _resolve_provider_profile,
                )

                _profile, _env_dict = _resolve_provider_profile(
                    step_name or "",
                    tool_ctx.recipe_name or "",
                    _cfg.providers,
                    step_provider=step_provider or "",
                )
                if _profile != "anthropic":
                    provider_extras = _env_dict
                    profile_name_out = _profile
                else:
                    effective_model, prof_name, prof_extras = _resolve_model_as_profile(
                        model, _cfg.providers
                    )
                    if prof_extras is not None:
                        provider_extras = prof_extras
                        profile_name_out = prof_name

            if _cfg.model.model_override:
                effective_model = _cfg.model.model_override
            else:
                if tool_ctx.recipe_name:
                    _mo_recipe_map = _cfg.providers.model_overrides.get(tool_ctx.recipe_name)
                    if _mo_recipe_map:
                        _step_mo = _mo_recipe_map.get(step_name) if step_name else None
                        if _step_mo is None:
                            _step_mo = _mo_recipe_map.get("*")
                        if _step_mo:
                            effective_model = _step_mo

            # The fresh branch resolved the complete effective invocation before any
            # notification or provider/executor work. Backend-specific rendering waits
            # until capability-driven backend selection is complete.
            _stored_contract = (
                _stored_contract_entry.contract if _stored_contract_entry is not None else None
            )
            resolved_command = (
                _stored_contract.resolved_command
                if _stored_contract is not None
                else child_skill_command
            )
            _effective_skill_contract = invocation if invocation is not None else _stored_contract

            _provider_override = (
                provider_extras
                and "ANTHROPIC_BASE_URL" in provider_extras
                and tool_ctx.backend is not None
                and not tool_ctx.backend.capabilities.anthropic_provider_capable
            )

            # Explicit config backend override — highest authority, suppresses
            # capability-driven routing for this step (REQ-RES-001).
            from autoskillit.server._guards import _resolve_backend_override  # circular-break

            _explicit_resolution = _resolve_backend_override(
                step_name or "",
                tool_ctx.recipe_name or "",
                _cfg.agent_backend,
            )
            _explicit_backend_override: str | None = (
                _explicit_resolution.backend if _explicit_resolution else None
            )

            _skill_caps: frozenset[str] = (
                invocation.capability_union
                if invocation is not None
                else _stored_contract.capability_union
                if _stored_contract is not None
                else frozenset()
            )
            _sandbox_overrides = _aggregate_sandbox_overrides(_skill_caps)
            _network_access = "sandbox_workspace_write.network_access=true" in _sandbox_overrides
            _has_routing_cap = _has_routing_capability(_skill_caps)
            _routing_caps = _get_routing_caps(_skill_caps) if _has_routing_cap else []
            _skill_requires_claude = bool(
                _has_routing_cap
                and tool_ctx.backend is not None
                and not tool_ctx.backend.capabilities.anthropic_provider_capable
            )

            # When an explicit backend override pins a step to a non-claude
            # backend, capability-driven routing must NOT crash on the missing
            # claude binary — the operator has explicitly chosen the backend.
            if (
                _stored_contract is None
                and _skill_requires_claude
                and _explicit_backend_override is None
            ):
                if shutil.which("claude") is None:
                    return SkillResult.crashed(
                        exception=RuntimeError(
                            f"Skill {target_name!r} requires claude-code backend "
                            f"({', '.join(_routing_caps)} capability) but 'claude' binary "
                            f"is not found on PATH. Install Claude Code CLI to "
                            f"enable capability-driven routing."
                        ),
                        skill_command=resolved_command,
                        order_id=effective_order_id,
                    ).to_json()

            # If an explicit override points to a non-claude backend whose
            # binary is absent, fail closed with a clear message.
            if (
                _stored_contract is None
                and _explicit_backend_override is not None
                and _explicit_backend_override
                != (tool_ctx.backend.name if tool_ctx.backend else None)
            ):
                try:
                    _explicit_backend_obj_check = _get_backend(_explicit_backend_override)
                except Exception:
                    logger.warning(
                        "explicit_backend_resolve_failed",
                        backend=_explicit_backend_override,
                        exc_info=True,
                    )
                    return SkillResult.crashed(
                        exception=RuntimeError(
                            f"Step explicitly pinned to backend "
                            f"{_explicit_backend_override!r} but that backend "
                            f"is not registered. Check step_overrides / "
                            f"recipe_overrides for typos."
                        ),
                        skill_command=resolved_command,
                        order_id=effective_order_id,
                    ).to_json()
                if _explicit_backend_obj_check is not None:
                    _explicit_binary = getattr(
                        _explicit_backend_obj_check.capabilities, "process_name", ""
                    )
                    if _explicit_binary and shutil.which(_explicit_binary) is None:
                        return SkillResult.crashed(
                            exception=RuntimeError(
                                f"Step explicitly pinned to backend "
                                f"{_explicit_backend_override!r} but required binary "
                                f"{_explicit_binary!r} is not found on PATH."
                            ),
                            skill_command=resolved_command,
                            order_id=effective_order_id,
                        ).to_json()

            if _stored_contract is not None:
                backend_override = _stored_contract.backend
            elif _explicit_backend_override is not None:
                backend_override = _explicit_backend_override
            elif _provider_override or _skill_requires_claude:
                backend_override = AGENT_BACKEND_CLAUDE_CODE
            else:
                backend_override = None

            _effective_backend_obj: CodingAgentBackend | None = (
                _resume_backend_obj
                if _stored_contract is not None
                else _get_backend(backend_override)
                if backend_override is not None and tool_ctx.backend is not None
                else tool_ctx.backend
            )
            if _stored_contract is None:
                projection_context = bind_projection_backend(
                    projection_context, _effective_backend_obj
                )
            if invocation is not None and _stored_contract is None:
                if invocation.root.source_ref is None:
                    raise SkillContractError("Effective skill source identity is missing")
                resolved_command = render_target_skill_command(
                    child_skill_command,
                    invocation.root.source_ref,
                    (
                        _effective_backend_obj.conventions
                        if _effective_backend_obj is not None
                        else None
                    ),
                )

            _backend_override_source: str | None = None
            if _stored_contract is not None:
                _backend_override_source = "stored_contract"
            elif _explicit_resolution is not None:
                _backend_override_source = _explicit_resolution.key_path
            elif _skill_requires_claude:
                _backend_override_source = "skill_requirement"
            elif _provider_override:
                _backend_override_source = "provider_profile"

            if backend_override:
                _override_reasons: list[str] = (
                    [_backend_override_source] if _backend_override_source else []
                )
                logger.info(
                    "backend_override_activated",
                    reason=(
                        _override_reasons[0] if len(_override_reasons) == 1 else _override_reasons
                    ),
                    skill=skill_command,
                    original_backend=tool_ctx.backend.name if tool_ctx.backend else "none",
                    target_backend=backend_override,
                    routing_capabilities=_routing_caps,
                )

            expected_output_patterns, write_spec, _skill_contract = (
                resolve_skill_dispatch_metadata(
                    tool_ctx,
                    skill_command,
                    _stored_contract,
                    audit_output_mode=_audit_output_mode,
                )
            )

            # Resolve closure spec from explicit MCP tool parameters.
            # Closure args are first-class parameters (not embedded in skill_command text)
            # because the skill_command string is prompt text consumed by the LLM session,
            # not parsed by Python code.
            closure_spec: ClosureAuthoritySpec | None = closure_authority_spec_from_args(
                path=closure_authority_path or None,
                hash_=closure_authority_hash or None,
                plan_paths=parse_plan_paths(closure_plan_paths) if closure_plan_paths else (),
                base_sha=closure_base_sha,
                diff_sha=closure_diff_sha,
                target_sha=closure_target_sha,
            )

            # Build validated add_dirs via DefaultSessionSkillManager
            from uuid import uuid4

            # Backend compatibility gate — fail-closed, fires before replay and live session paths.
            if compat_error := _check_backend_compat(
                skill_command=skill_command,
                resolved_command=resolved_command,
                effective_order_id=effective_order_id,
                target_name=target_name,
                skill_info=_effective_skill_contract,
                effective_backend_obj=_effective_backend_obj,
                skill_resolver=(
                    _effective_skill_resolver
                    if _effective_skill_resolver is not None
                    else _stored_contract_entry
                ),
            ):
                return compat_error

            # Server-side recipe step parameter resolution.
            # When a step_name is provided and the recipe's step definition is cached,
            # auto-fill parameters the LLM may have omitted.
            if step_name and tool_ctx.active_recipe_steps is not None:
                _recipe_step = tool_ctx.active_recipe_steps.get(step_name)
                if _recipe_step is not None:
                    if not output_dir and "output_dir" in _recipe_step.with_args:
                        _recipe_output_dir = _recipe_step.with_args["output_dir"]
                        # Skip values containing unresolved template references —
                        # load() returns raw YAML without ingredient resolution,
                        # so ${{ context.* }} placeholders may survive.
                        if "${{" not in _recipe_output_dir:
                            output_dir = _recipe_output_dir
                            logger.warning(
                                "output_dir_resolved_from_recipe",
                                step=step_name,
                                output_dir=output_dir,
                            )

                    if stale_threshold is None and _recipe_step.stale_threshold is not None:
                        stale_threshold = _recipe_step.stale_threshold
                        logger.warning(
                            "stale_threshold_resolved_from_recipe",
                            step=step_name,
                            value=stale_threshold,
                        )

                    if (
                        idle_output_timeout is None
                        and _recipe_step.idle_output_timeout is not None
                    ):
                        idle_output_timeout = _recipe_step.idle_output_timeout
                        logger.warning(
                            "idle_output_timeout_resolved_from_recipe",
                            step=step_name,
                            value=idle_output_timeout,
                        )

            closure_report_root: Path | None = None
            if output_dir and closure_spec:
                _closure_root = Path(output_dir)
                if not _closure_root.is_absolute():
                    _closure_root = Path(cwd) / output_dir
                closure_report_root = _closure_root
            elif closure_spec and not output_dir:
                return json.dumps(
                    ToolFailureEnvelope(
                        success=False,
                        error=(
                            "closure_spec requires output_dir to locate"
                            " the closure report, but output_dir is empty"
                        ),
                        stage="validate_args:run_skill",
                        retriable=False,
                    )
                )

            write_watch_dirs: list[Path] = []
            if output_dir:
                resolved_dir = Path(output_dir)
                if not resolved_dir.is_absolute():
                    resolved_dir = Path(cwd) / output_dir
                write_watch_dirs.append(resolved_dir)

            if not write_watch_dirs:
                _default_temp = _resolve_skill_temp_dir(cwd, skill_command)
                if _default_temp:
                    write_watch_dirs.append(_default_temp)

            if _stored_contract is not None:
                is_read_only = _stored_contract.read_only
                completion_required = _stored_contract.completion_required
            else:
                is_read_only = bool(
                    tool_ctx.read_only_resolver and tool_ctx.read_only_resolver(skill_command)
                )
                completion_required = bool(
                    tool_ctx.completion_required_resolver
                    and tool_ctx.completion_required_resolver(skill_command)
                )
            invocation_marker = f"%%ORDER_UP::{uuid4().hex[:8]}%%"

            skill_add_dirs: list[ValidatedAddDir] = []
            replay_snapshot_used = False
            _runner = tool_ctx.runner
            if _stored_contract_entry is not None:
                skill_add_dirs.append(
                    ValidatedAddDir(path=str(_stored_contract_entry.snapshot_dir))
                )
                replay_snapshot_used = True
            elif (
                step_name
                and _runner is not None
                and getattr(_runner, "skill_snapshots", None)
                and hasattr(_runner, "restore_skill_snapshot")
                and tool_ctx.ephemeral_root is not None
            ):
                _ephemeral_root = tool_ctx.ephemeral_root
                if invocation is None:
                    raise SkillContractError(
                        "Fresh replay requires a validated effective invocation"
                    )
                if hasattr(_runner, "validate_skill_snapshot"):
                    _runner.validate_skill_snapshot(  # type: ignore[attr-defined]
                        step_name,
                        invocation_member_names(invocation),
                    )
                session_id = f"headless-{uuid4().hex[:12]}"
                _cleanup_session_id = session_id
                _restored = _runner.restore_skill_snapshot(  # type: ignore[attr-defined]
                    step_name, _ephemeral_root, session_id
                )
                if _restored is not None:
                    if not Path(_restored.path).is_dir():
                        logger.warning(
                            "stale_snapshot_path",
                            session_id=session_id,
                            path=_restored.path,
                        )
                        return SkillResult.crashed(
                            exception=RuntimeError(
                                f"Snapshot path {_restored.path!r} does not exist. "
                                f"The /dev/shm directory may have been reclaimed."
                            ),
                            skill_command=resolved_command,
                            session_id=session_id,
                            order_id=effective_order_id,
                        ).to_json()
                    skill_add_dirs.append(_restored)
                    replay_snapshot_used = True
                    logger.debug(
                        "replay_skill_snapshot_restored",
                        step=step_name,
                        session_id=session_id,
                    )

            if not replay_snapshot_used and tool_ctx.session_skill_manager is not None:
                if _stored_contract_entry is not None:
                    assert resume_session_id is not None
                    session_root = ValidatedAddDir(path=str(_stored_contract_entry.snapshot_dir))
                    session_id = resume_session_id
                elif invocation is not None:
                    session_id = f"headless-{uuid4().hex[:12]}"
                    _cleanup_session_id = session_id
                    if projection_context is None:
                        raise SkillContractError("Projection context was not prepared")
                    session_root = tool_ctx.session_skill_manager.materialize_invocation(
                        session_id,
                        invocation,
                        projection_context,
                    )
                else:
                    raise SkillContractError(
                        "Fresh execution requires a resolved skill invocation"
                    )
                if _stored_contract_entry is None and (
                    not session_id
                    or not tool_ctx.session_skill_manager.validate_session_exists(session_id)
                ):
                    logger.warning(
                        "stale_session_path",
                        session_id=session_id,
                        path=session_root.path,
                    )
                    return SkillResult.crashed(
                        exception=RuntimeError(
                            f"Session path {session_root.path!r} does not exist. "
                            f"The /dev/shm directory may have been reclaimed."
                        ),
                        skill_command=resolved_command,
                        session_id=session_id,
                        order_id=effective_order_id,
                    ).to_json()
                skill_add_dirs.append(session_root)

            # Both fresh and rehydrated invocations extend scope from their
            # validated closure, independent of whether a snapshot was replayed.
            if invocation is not None:
                write_watch_dirs.extend(
                    resolve_closure_write_dirs(
                        invocation.closure,
                        cwd,
                        write_watch_dirs,
                    )
                )

            if invocation is not None and _stored_contract is None:
                if not skill_add_dirs:
                    raise SkillContractError(
                        "Fresh execution requires a materialized skill snapshot"
                    )
                if projection_context is None:
                    raise SkillContractError("Projection context was not prepared")
                _session_contract, _session_snapshot = _build_skill_session_contract(
                    session_root=skill_add_dirs[0],
                    invocation=invocation,
                    projection_context=projection_context,
                    resolved_command=resolved_command,
                    expected_output_patterns=tuple(expected_output_patterns),
                    write_behavior=write_spec or WriteBehaviorSpec(),
                    read_only=is_read_only,
                    completion_required=completion_required,
                    skill_contract_json=_serialize_skill_contract(_skill_contract),
                )

            _capability_contract = build_validated_skill_dispatch_contract(
                resolved_command,
                projection_context,
                skill_add_dirs,
                _stored_contract,
            )
            _lineage_store = tool_ctx.managed_headless_session_lineage_store
            _lineage_preparation = prepare_skill_native_shell_lineage(
                store=_lineage_store,
                backend=_effective_backend_obj,
                lineage_anchor=Path(_capability_contract.cwd),
                stored_reference=getattr(_stored_contract_entry, "managed_lineage_ref", None),
                resume_session_id=resume_session_id,
                requested_mode=native_shell_capture_mode,
                is_resume=_stored_contract_entry is not None,
            )
            _native_shell_capture_decision = _lineage_preparation.decision
            _managed_lineage_ref = _lineage_preparation.reference
            if _stored_contract_entry is None:
                if _session_contract is None or _session_snapshot is None:
                    raise SkillContractError(
                        "Fresh execution did not produce a provisional skill contract"
                    )
                contract_lifecycle.correlation_key = _contract_store.create_provisional(
                    contract=_session_contract,
                    snapshot=_session_snapshot,
                    managed_lineage_ref=_managed_lineage_ref,
                )
            allowed_write_prefix = ""
            allowed_write_prefixes: tuple[str, ...] = ()
            if write_watch_dirs:
                allowed_write_prefix, allowed_write_prefixes = _compute_write_prefixes(
                    write_watch_dirs, cwd, skill_command
                )
            elif is_read_only:
                _skill_temp_name = target_name or ""
                if _skill_temp_name:
                    allowed_write_prefix = os.path.join(
                        cwd, ".autoskillit", "temp", _skill_temp_name, ""
                    )
                else:
                    logger.warning(
                        "read_only_skill_no_target_name",
                        skill_command=skill_command[:SKILL_COMMAND_DISPLAY_MAX],
                    )
            # Preflight: for WORKTREE_SKILLS dispatches, the computed scope must cover cwd
            # so the session can write to its own tracked tree. Fail-fast BEFORE spawning
            # a session — otherwise the session locks itself out and burns N turns.
            if allowed_write_prefixes and target_name and target_name in WORKTREE_SKILLS and cwd:
                if not _scope_covers_cwd(allowed_write_prefixes, cwd):
                    return gate_error_result(
                        f"Write scope does not cover target worktree: "
                        f"cwd={cwd!r} not under any allowed prefix "
                        f"{allowed_write_prefixes!r}. "
                        f"Likely missing output_dir or malformed dispatch."
                    )

            _sn_token = _current_step_name.set(_canonical_step_name(step_name))
            _oid_token = _current_order_id.set(effective_order_id)

            _marker_dir: Path | None = (
                tool_ctx.backend.session_locator().project_log_dir(str(tool_ctx.project_dir))
                if tool_ctx.backend is not None
                else None
            )
            _orchestrator_sid = find_caller_session_id(project_dir=tool_ctx.project_dir)

            # Propagate AUTOSKILLIT_SESSION_DEADLINE to L1 sessions.
            provider_extras = propagate_session_deadline(tool_ctx.project_dir, provider_extras)

            def _observe_contract_session_id(candidate_session_id: str) -> None:
                contract_lifecycle.observe_candidate(candidate_session_id)

            _start = time.monotonic()
            try:
                try:
                    with anyio.fail_after(_cfg.run_skill.mcp_tool_timeout_sec):
                        async with execution_marker(
                            _marker_dir,
                            _orchestrator_sid,
                            "run-skill",
                        ):
                            contract_lifecycle.execution_started = True
                            skill_result = await tool_ctx.executor.run(
                                resolved_command,
                                _capability_contract.cwd,
                                model=effective_model,
                                add_dirs=skill_add_dirs,
                                step_name=step_name,
                                kitchen_id=tool_ctx.kitchen_id,
                                order_id=effective_order_id,
                                expected_output_patterns=expected_output_patterns,
                                write_behavior=write_spec,
                                stale_threshold=float(stale_threshold)
                                if stale_threshold is not None
                                else None,
                                idle_output_timeout=float(idle_output_timeout)
                                if idle_output_timeout is not None
                                else None,
                                completion_marker=invocation_marker,
                                recipe_name=tool_ctx.recipe_name,
                                recipe_content_hash=tool_ctx.recipe_content_hash,
                                recipe_composite_hash=tool_ctx.recipe_composite_hash,
                                recipe_version=tool_ctx.recipe_version,
                                allowed_write_prefix=allowed_write_prefix,
                                allowed_write_prefixes=allowed_write_prefixes,
                                readonly_skill=is_read_only,
                                completion_required=completion_required,
                                write_watch_dirs=write_watch_dirs,
                                provider_extras=provider_extras,
                                profile_name=profile_name_out,
                                provider_name=profile_name_out,
                                backend_override=backend_override,
                                backend_override_source=_backend_override_source,
                                resume_session_id=resume_session_id,
                                marker_dir=_marker_dir,
                                caller_session_id=_orchestrator_sid,
                                inspector_eligible=_in_fleet_dispatch and bool(_inspector_model),
                                inspector_model=_inspector_model,
                                network_access=_network_access,
                                closure_spec=closure_spec,
                                closure_report_root=closure_report_root,
                                skill_contract=_skill_contract,
                                capability_contract=_capability_contract,
                                native_shell_capture_decision=(_native_shell_capture_decision),
                                managed_lineage_ref=_managed_lineage_ref,
                                on_session_id_resolved=(
                                    _observe_contract_session_id
                                    if contract_lifecycle.correlation_key is not None
                                    else None
                                ),
                            )
                except TimeoutError as exc:
                    contract_lifecycle.retain_bound = False
                    logger.error(
                        "run_skill_mcp_tool_timeout",
                        timeout_sec=_cfg.run_skill.mcp_tool_timeout_sec,
                    )
                    _timeout_exc = TimeoutError(
                        f"MCP tool timeout ({_cfg.run_skill.mcp_tool_timeout_sec}s) exceeded"
                    )
                    _timeout_exc.__cause__ = exc
                    return SkillResult.crashed(
                        exception=_timeout_exc,
                        skill_command=resolved_command,
                        order_id=effective_order_id,
                    ).to_json()

                contract_lifecycle.finalize(skill_result.session_id)

                rebind_verified_final_session(
                    store=_lineage_store,
                    backend=_effective_backend_obj,
                    reference=_managed_lineage_ref,
                    is_resume=_stored_contract_entry is not None,
                    requested_session_id=resume_session_id,
                    returned_session_id=skill_result.session_id,
                    on_rebind=contract_lifecycle.rebind_final,
                )
                contract_lifecycle.apply_retention(skill_result.needs_retry)

                _audit_outcome_to_finalize: AuditOutcome | None = None
                if skill_result.success:
                    if _audit_reservation is not None:
                        _semantic_path = (skill_result.outcome_fields or {}).get(
                            "audit_semantic_result_path"
                        )
                        if not isinstance(_semantic_path, str) or not _semantic_path:
                            _materialized = _reject_missing_semantic_result(
                                tool_ctx,
                                _audit_reservation,
                            )
                        else:
                            with tool_ctx.recipe_execution_lock:
                                if get_recipe_execution(tool_ctx) is not _installed_execution:
                                    _materialized = AuditMaterializationResult(
                                        status=AuditMaterializationStatus.CONFLICT,
                                        attempt_id=_audit_reservation.current_attempt_id,
                                        verdict=None,
                                        path=None,
                                        error=(
                                            "active recipe execution changed before "
                                            "audit materialization"
                                        ),
                                    )
                                else:
                                    _materialized = (
                                        tool_ctx.audit_authority_materializer.materialize(
                                            reservation=_audit_reservation,
                                            semantic_result_path=Path(_semantic_path),
                                            preflight_step_names=_audit_preflight_steps,
                                        )
                                    )
                        _materialized_status = _materialization_outcome_status(_materialized)
                        match _materialized_status:
                            case AuditOutcomeStatus.PUBLISHED:
                                assert _materialized.verdict is not None
                                assert _materialized.path is not None
                                skill_result.result = (
                                    "Server-authored audit outcome: "
                                    f"{AuditOutcomeStatus.PUBLISHED.value}"
                                )
                                skill_result.outcome_fields = None
                                skill_result.audit = AuditResultOutcome(
                                    status=AuditOutcomeStatus.PUBLISHED,
                                    verdict=_materialized.verdict,
                                    cycle_path=str(_materialized.path),
                                    attempt_id=_materialized.attempt_id,
                                )
                                _audit_outcome_to_finalize = AuditOutcome(
                                    status=AuditOutcomeStatus.PUBLISHED,
                                    attempt_id=_materialized.attempt_id,
                                    verdict=_materialized.verdict,
                                    path=_materialized.path,
                                    error=None,
                                    kill_reason=skill_result.kill_reason,
                                )
                            case AuditOutcomeStatus.EXACT_REPLAY:
                                return _audit_response(
                                    status=_materialized_status,
                                    attempt_id=_materialized.attempt_id,
                                    verdict=_materialized.verdict,
                                    path=_materialized.path,
                                    error=_materialized.error,
                                    kill_reason=skill_result.kill_reason,
                                )
                            case (
                                AuditOutcomeStatus.SEMANTIC_REJECTED
                                | AuditOutcomeStatus.CONFLICT
                                | AuditOutcomeStatus.STORAGE_FAILURE
                                | AuditOutcomeStatus.QUARANTINED
                                | AuditOutcomeStatus.NON_PUBLISHED_STANDALONE
                            ):
                                skill_result.result = ""
                                skill_result.outcome_fields = None
                                return _audit_response(
                                    status=_materialized_status,
                                    attempt_id=_materialized.attempt_id,
                                    verdict=None,
                                    path=None,
                                    error=_materialized.error,
                                    kill_reason=skill_result.kill_reason,
                                )
                    if _audit_outcome_to_finalize is not None:
                        _pipeline_marker = _complete_audit_finalization_effects(
                            tool_ctx,
                            attempt_id=_audit_outcome_to_finalize.attempt_id,
                            skill_command=skill_command,
                            step_name=step_name,
                            order_id=order_id,
                            mark_step_complete=_mark_step_complete_server_side,
                        )
                    else:
                        tool_ctx.audit.record_success(skill_command)
                        clear_run_skill_state(tool_ctx.project_dir)
                        if step_name:
                            _pipeline_marker = _mark_step_complete_server_side(
                                tool_ctx, step_name, order_id
                            )
                        else:
                            _pipeline_marker = None
                else:
                    _pipeline_marker = None
                    await _notify(
                        ctx,
                        "error",
                        "run_skill failed",
                        "autoskillit.run_skill",
                        extra={
                            "exit_code": skill_result.exit_code,
                            "subtype": skill_result.subtype,
                        },
                    )
                    persist_run_skill_state(skill_result, tool_ctx.project_dir)
                if effective_order_id:
                    skill_result.order_id = effective_order_id
                from autoskillit.server._misc import (  # circular-break
                    _refresh_quota_cache,
                )

                if tool_ctx.background is not None:
                    tool_ctx.background.submit(
                        _refresh_quota_cache(tool_ctx.config.quota_guard),
                        label="quota_post_run_refresh",
                    )
                _json_str = skill_result.to_json()
                try:
                    _parsed = json.loads(_json_str)
                except Exception as exc:
                    logger.warning("run_skill_json_parse_failed", exc_info=True)
                    return json.dumps(
                        ToolFailureEnvelope(
                            success=False,
                            error=f"Degraded SkillResult payload: JSON parse failed: {exc}",
                            stage="validate_result:run_skill",
                            retriable=True,
                        )
                    )
                _missing = {"success", "exit_code"} - _parsed.keys()
                if _missing:
                    logger.warning(
                        "run_skill_degraded_payload",
                        absent_fields=sorted(_missing),
                    )
                    return json.dumps(
                        ToolFailureEnvelope(
                            success=False,
                            error=f"Degraded SkillResult payload: missing keys {sorted(_missing)}",
                            stage="validate_result:run_skill",
                            retriable=True,
                        )
                    )
                if _pipeline_marker is not None:
                    _parsed["pipeline_tracker"] = _pipeline_marker
                _shaped_response = shape_execution_response(
                    tool_ctx,
                    _parsed,
                    tool_name="run_skill",
                    work_dir=cwd,
                )
                if _audit_outcome_to_finalize is not None:
                    _replay_payload = json.loads(_shaped_response)
                    _replay_payload["audit_status"] = AuditOutcomeStatus.EXACT_REPLAY.value
                    _replay_response = json.dumps(
                        _replay_payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    tool_ctx.audit_admission_ledger.finalize_response(
                        _audit_outcome_to_finalize.attempt_id,
                        AuditOutcome(
                            status=_audit_outcome_to_finalize.status,
                            attempt_id=_audit_outcome_to_finalize.attempt_id,
                            verdict=_audit_outcome_to_finalize.verdict,
                            path=_audit_outcome_to_finalize.path,
                            error=_audit_outcome_to_finalize.error,
                            kill_reason=_audit_outcome_to_finalize.kill_reason,
                            replay_response_json=_replay_response,
                        ),
                        required_effect_names=_required_audit_finalization_effect_names(step_name),
                    )
                return _shaped_response
            except Exception as exc:
                contract_lifecycle.retain_bound = False
                logger.error("run_skill executor raised unexpectedly", exc_info=True)
                return SkillResult.crashed(
                    exception=exc,
                    skill_command=resolved_command,
                    order_id=effective_order_id,
                ).to_json()
            finally:
                if step_name:
                    tool_ctx.timing_log.record(
                        step_name, time.monotonic() - _start, order_id=effective_order_id
                    )
    except Exception as exc:
        logger.error("run_skill unhandled exception", exc_info=True)
        return SkillResult.crashed(
            exception=exc,
            skill_command=skill_command,
            order_id=order_id,
        ).to_json()
    except asyncio.CancelledError:
        with anyio.CancelScope(shield=True):
            logger.warning("run_skill cancelled", exc_info=True)
        _cmd = locals().get("resolved_command", skill_command)
        _oid = locals().get("effective_order_id", order_id)
        return SkillResult.cancelled(
            skill_command=_cmd,  # type: ignore[arg-type]
            order_id=_oid,  # type: ignore[arg-type]
        ).to_json()
    finally:
        contract_lifecycle.cleanup()
        if _sn_token is not None:
            _current_step_name.reset(_sn_token)  # type: ignore[possibly-undefined]
        if _oid_token is not None:
            _current_order_id.reset(_oid_token)  # type: ignore[possibly-undefined]
        _sid: str | None = locals().get("_cleanup_session_id")  # type: ignore[assignment]
        if _sid is not None:
            _ssm = tool_ctx.session_skill_manager  # type: ignore[possibly-undefined]
            if _ssm is not None:
                try:
                    _ssm.cleanup_session(_sid)
                except Exception:
                    logger.warning(
                        "session_skill_cleanup_failed",
                        session_id=_sid,
                        exc_info=True,
                    )
            elif tool_ctx.ephemeral_root is not None:  # type: ignore[possibly-undefined]
                _cleanup_dir = tool_ctx.ephemeral_root / _sid  # type: ignore[possibly-undefined]
                if _cleanup_dir.is_dir():
                    try:
                        shutil.rmtree(_cleanup_dir)
                    except Exception:
                        logger.warning(
                            "session_dir_rmtree_failed", path=str(_cleanup_dir), exc_info=True
                        )
                else:
                    _codex_fallback = tool_ctx.temp_dir / CODEX_SESSIONS_SUBDIR / _sid
                    if _codex_fallback.is_dir():
                        try:
                            shutil.rmtree(_codex_fallback)
                        except Exception:
                            logger.warning(
                                "session_dir_rmtree_failed",
                                path=str(_codex_fallback),
                                exc_info=True,
                            )
