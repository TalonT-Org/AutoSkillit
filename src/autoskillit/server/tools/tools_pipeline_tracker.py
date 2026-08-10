"""MCP tool: record_pipeline_step — pipeline step tracker init, status, and complete."""

from __future__ import annotations

import json
import os
import threading
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import (
    DISPATCH_ID_ENV_VAR,
    RunSkillCompletionAuthority,
    ArtifactLease,
    AuditIdentityReservation,
    KitchenProcessIdentity,
    TrackerAuthorityReadResult,
    TrackerAuthorityTarget,
    TrackerParticipantKey,
    get_logger,
    initialize_manual_tracker,
    mutate_tracker,
    read_tracker_authority,
    release_tracker_lease,
    retain_tracker_lease,
    sample_kitchen_process_identity,
)
from autoskillit.pipeline import canonical_step_name
from autoskillit.server.tools._pipeline_deps import _derive_phase_a_deps

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled, _require_orchestrator_exact
from autoskillit.server._notify import track_response_size
from autoskillit.server._run_skill_completion import _request_session_identity
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._overlay_state import read_overlay
from autoskillit.server.tools._types import deny_envelope

logger = get_logger(__name__)


class _TrackerCtx(Protocol):
    """Minimal ToolContext duck-type — avoids circular import with tools_execution.py."""

    kitchen_id: str
    kitchen_process_identity: KitchenProcessIdentity | None
    project_dir: Path
    run_skill_completion: RunSkillCompletionAuthority | None
    active_recipe_steps: dict[str, object] | None
    tracker_leases: dict[TrackerParticipantKey, ArtifactLease]
    tracker_leases_lock: threading.RLock


def read_tracker_identity(
    target: TrackerAuthorityTarget,
    lease: ArtifactLease,
) -> tuple[str, str] | None:
    """Read kitchen and incarnation identity under the target's retained lease."""
    authority = read_tracker_authority(target, lease)
    if authority.data is None:
        return None
    kitchen_id = authority.data.get("kitchen_id")
    incarnation_id = authority.data.get("tracker_incarnation_id")
    if not isinstance(kitchen_id, str) or not isinstance(incarnation_id, str):
        return None
    return kitchen_id, incarnation_id


def select_tracker_target(
    tool_ctx: _TrackerCtx,
    order_id: str,
    *,
    expected: bool,
) -> TrackerAuthorityTarget | None:
    """Select one explicit target without scanning for ambient tracker files."""
    effective_oid = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "") or tool_ctx.kitchen_id
    if not effective_oid:
        return None
    return TrackerAuthorityTarget.for_project(
        tool_ctx.project_dir,
        effective_oid,
        expected=expected,
    )


def _retain_context_tracker(
    tool_ctx: _TrackerCtx,
    target: TrackerAuthorityTarget,
    *,
    owner_kind: Literal["kitchen", "dispatch", "manual"],
    owner_id: str,
) -> tuple[TrackerParticipantKey, ArtifactLease]:
    with tool_ctx.tracker_leases_lock:
        identity = tool_ctx.kitchen_process_identity
        if identity is None:
            identity = sample_kitchen_process_identity(
                tool_ctx.kitchen_id or owner_id,
                os.getpid(),
                tool_ctx.project_dir,
            )
            tool_ctx.kitchen_process_identity = identity
        key = TrackerParticipantKey(
            target=target,
            owner_kind=owner_kind,
            owner_id=owner_id,
            pid=identity.pid,
            create_time=identity.create_time,
            project_path=identity.project_path,
        )
        lease = retain_tracker_lease(tool_ctx.tracker_leases, key)
    return key, lease


def _release_context_tracker(tool_ctx: _TrackerCtx, key: TrackerParticipantKey) -> None:
    with tool_ctx.tracker_leases_lock:
        release_tracker_lease(tool_ctx.tracker_leases, key)


def _select_tracker_authority(
    tool_ctx: ToolContext,
    order_id: str,
) -> tuple[
    TrackerAuthorityTarget | None,
    TrackerAuthorityReadResult | None,
    TrackerParticipantKey | None,
    ArtifactLease | None,
]:
    explicit_target = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")
    target_order_id = explicit_target or tool_ctx.kitchen_id
    if not target_order_id:
        return None, None, None, None
    expected = bool(explicit_target)
    if not expected and tool_ctx.active_recipe_steps:
        try:
            expected = bool(_derive_phase_a_deps(tool_ctx.active_recipe_steps))
        except (AttributeError, TypeError):
            expected = False
    target = TrackerAuthorityTarget.for_project(
        tool_ctx.project_dir,
        target_order_id,
        expected=expected,
    )
    key, lease = _retain_context_tracker(
        tool_ctx,
        target,
        owner_kind="kitchen",
        owner_id=tool_ctx.kitchen_id or target_order_id,
    )
    return target, read_tracker_authority(target, lease), key, lease


def _restore_reserved_tracker_authority(
    tool_ctx: ToolContext,
    reservation: AuditIdentityReservation,
    current_key: TrackerParticipantKey | None,
) -> tuple[
    TrackerAuthorityTarget | None,
    TrackerAuthorityReadResult | None,
    TrackerParticipantKey | None,
    ArtifactLease | None,
]:
    if current_key is not None:
        _release_context_tracker(tool_ctx, current_key)
    target_order_id = reservation.tracker_target_order_id
    if target_order_id is None:
        return None, None, None, None
    target = TrackerAuthorityTarget.for_project(
        tool_ctx.project_dir,
        target_order_id,
        expected=reservation.tracker_expected,
    )
    key, lease = _retain_context_tracker(
        tool_ctx,
        target,
        owner_kind="kitchen",
        owner_id=tool_ctx.kitchen_id or target_order_id,
    )
    return target, read_tracker_authority(target, lease), key, lease


def _authority_blocks_dependency_check(authority: TrackerAuthorityReadResult | None) -> bool:
    return bool(
        authority is not None
        and (authority.error is not None or (authority.data or {}).get("dependencies"))
    )


def _resolve_skipped_steps(project_dir: Path, pipeline_id: str) -> set[str]:
    try:
        overlay = read_overlay(project_dir)
        pid_locks = overlay.get("locked_steps", {}).get(pipeline_id, {})
        return {s for s, v in pid_locks.items() if v is False}
    except OSError:
        return set()


def _build_tracker_steps(
    active_steps: dict[str, object], skipped: set[str]
) -> dict[str, dict[str, str]]:
    return {
        name: {"status": "skipped"} if name in skipped else {"status": "pending"}
        for name in active_steps
    }


def _compute_status_counts(
    steps: dict[str, dict[str, object]], dependencies: dict[str, list[str]]
) -> dict[str, int]:
    return {
        "complete": sum(1 for s in steps.values() if s.get("status") == "complete"),
        "pending": sum(1 for s in steps.values() if s.get("status") == "pending"),
        "skipped": sum(1 for s in steps.values() if s.get("status") == "skipped"),
        "blocked": sum(
            1
            for sname, sdata in steps.items()
            if sdata.get("status") == "pending" and sname in dependencies
        ),
    }


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield()
@track_response_size("record_pipeline_step")
async def record_pipeline_step(
    pipeline_id: str = "",
    op: str = "status",
    dependencies: dict[str, list[str]] | None = None,
    step_name: str = "",
) -> str:
    """Initialize, query, or mark completion on the pipeline step tracker.

    **op="init"**: Creates the tracker file with the server-authoritative step list
    from the currently open recipe. The LLM provides the dependency graph at init time.
    Idempotent — calling init twice with the same pipeline_id returns an error.

    **op="status"**: Returns the current state of all tracked steps.

    **op="complete"**: Marks a tracked step as complete. Requires ``step_name``
    parameter. Canonicalizes retry suffixes (e.g. ``rectify-2`` → ``rectify``).
    Operator repair tool — do not use to bypass running a prerequisite skill.

    Requires the kitchen to be open. Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate

    try:
        effective_pipeline_id = pipeline_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")

        from autoskillit.server import (  # circular-break
            _get_ctx,
        )  # circular-break: server-internal circular dependency

        ctx = _get_ctx()

        if op == "complete":
            return _handle_complete(ctx, effective_pipeline_id, step_name)

        if not effective_pipeline_id:
            return json.dumps(
                deny_envelope(
                    "record_pipeline_step: pipeline_id is required. "
                    "Pass pipeline_id explicitly or set "
                    "AUTOSKILLIT_DISPATCH_ID in the environment.",
                    stage="preflight:pipeline_tracker",
                    retriable=False,
                )
            )

        target = TrackerAuthorityTarget.for_project(
            ctx.project_dir,
            effective_pipeline_id,
            expected=True,
        )
        key, lease = _retain_context_tracker(
            ctx,
            target,
            owner_kind="manual",
            owner_id=effective_pipeline_id,
        )

        if op == "init":
            result = _handle_init(ctx, target, lease, dependencies)
            if not json.loads(result).get("success"):
                _release_context_tracker(ctx, key)
            return result

        if op == "status":
            result = _handle_status(target, lease)
            if not json.loads(result).get("success"):
                _release_context_tracker(ctx, key)
            return result

        _release_context_tracker(ctx, key)

        return json.dumps(
            deny_envelope(
                f"record_pipeline_step: unknown op '{op}'. Use 'init', 'status', or 'complete'.",
                stage="preflight:pipeline_tracker",
                retriable=False,
            )
        )
    except Exception:
        logger.exception("record_pipeline_step_unexpected_error")
        return json.dumps(
            {
                "success": False,
                "is_error": True,
                "error": "record_pipeline_step: unexpected internal error.",
            }
        )


def _handle_init(
    ctx: _TrackerCtx,
    target: TrackerAuthorityTarget,
    lease: ArtifactLease,
    dependencies: dict[str, list[str]] | None,
) -> str:
    active_steps = ctx.active_recipe_steps  # type: ignore[attr-defined]
    if active_steps is None:
        return json.dumps(
            {
                "success": False,
                "is_error": True,
                "error": (
                    "record_pipeline_step: no active recipe steps found. "
                    "Ensure the kitchen is open with a loaded recipe."
                ),
            }
        )

    skipped = _resolve_skipped_steps(ctx.project_dir, target.target_order_id)
    steps = _build_tracker_steps(active_steps, skipped)

    tracker_data = {
        "pipeline_id": target.target_order_id,
        "kitchen_id": ctx.kitchen_id,
        "initialized_at": datetime.now(UTC).isoformat(),
        "tracker_incarnation_id": uuid.uuid4().hex,
        "steps": steps,
        "dependencies": dependencies or {},
    }

    authority = initialize_manual_tracker(target, lease, tracker_data)
    if authority.error is not None:
        return json.dumps(
            {
                "success": False,
                "is_error": True,
                "error": (
                    f"record_pipeline_step: pipeline '{target.target_order_id}' "
                    f"has already been initialized. {authority.error}"
                ),
            }
        )

    return json.dumps(
        {
            "success": True,
            "pipeline_id": target.target_order_id,
            "step_count": len(steps),
            "dependency_count": len(dependencies or {}),
        }
    )


def _handle_status(target: TrackerAuthorityTarget, lease: ArtifactLease) -> str:
    authority = read_tracker_authority(target, lease)
    if authority.data is None:
        return json.dumps(
            {
                "success": False,
                "is_error": True,
                "error": f"record_pipeline_step: {authority.error}",
            }
        )
    tracker_data = authority.data
    steps = tracker_data["steps"]
    deps = tracker_data["dependencies"]
    counts = _compute_status_counts(steps, deps)

    return json.dumps(
        {
            "success": True,
            "pipeline_id": target.target_order_id,
            "steps": steps,
            "dependencies": deps,
            **counts,
            "total": len(steps),
        }
    )


def _handle_complete(ctx: _TrackerCtx, effective_pipeline_id: str, step_name: str) -> str:
    if not step_name:
        return json.dumps(
            deny_envelope(
                "record_pipeline_step: step_name is required for op='complete'.",
                stage="preflight:pipeline_tracker",
                retriable=False,
            )
        )

    target = select_tracker_target(ctx, effective_pipeline_id, expected=True)
    if target is None:
        return json.dumps(
            deny_envelope(
                "record_pipeline_step: cannot resolve pipeline tracker: no explicit target",
                stage="preflight:pipeline_tracker",
                retriable=False,
            )
        )
    key, lease = _retain_context_tracker(
        ctx,
        target,
        owner_kind="manual",
        owner_id=target.target_order_id,
    )
    tracker_identity = read_tracker_identity(target, lease)
    if tracker_identity is None:
        _release_context_tracker(ctx, key)
        return json.dumps(
            deny_envelope(
                "record_pipeline_step: tracker incarnation identity is missing.",
                stage="preflight:pipeline_tracker",
                retriable=False,
            )
        )
    tracker_kitchen_id, tracker_incarnation_id = tracker_identity
    authority = ctx.run_skill_completion
    if authority is None:
        _release_context_tracker(ctx, key)
        return json.dumps(
            deny_envelope(
                "record_pipeline_step: completion authority is unavailable.",
                stage="preflight:pipeline_tracker",
                retriable=True,
            )
        )
    try:
        result = authority.apply_tracker_credit(
            tracker_order_id=target.target_order_id,
            tracker_path=str(target.path.resolve()),
            tracker_kitchen_id=tracker_kitchen_id,
            tracker_incarnation_id=tracker_incarnation_id,
            step_name=step_name,
            effect=lambda: mark_step_complete(
                target,
                lease,
                step_name,
                expected_tracker_kitchen_id=tracker_kitchen_id,
                expected_tracker_incarnation_id=tracker_incarnation_id,
            ),
        )
    except ValueError as exc:
        result = deny_envelope(
            f"record_pipeline_step: {exc}",
            stage="preflight:pipeline_tracker_credit",
            retriable=False,
        )
    except Exception:
        _release_context_tracker(ctx, key)
        logger.exception("record_pipeline_step_marker_failed")
        return json.dumps(
            {
                "success": False,
                "is_error": True,
                "error": "record_pipeline_step: pipeline marker failed.",
                "stage": "pipeline_marker",
            }
        )
    if not result.get("success") or result.get("done") == result.get("total"):
        _release_context_tracker(ctx, key)
    return json.dumps(result)


def mark_step_complete(
    target: TrackerAuthorityTarget,
    lease: ArtifactLease,
    step_name: str,
    *,
    expected_tracker_kitchen_id: str = "",
    expected_tracker_incarnation_id: str = "",
) -> dict:
    """Mark a single step as complete in the tracker file.

    Used by both ``op="complete"`` (operator repair) and
    ``complete_run_skill_result``. Returns a result dict (always includes ``success``).
    """
    canonical = canonical_step_name(step_name)
    outcome: dict[str, object] = {}

    def _mark(tracker: dict[str, Any]) -> dict[str, Any]:
        if expected_tracker_kitchen_id and (
            tracker.get("kitchen_id") != expected_tracker_kitchen_id
            or tracker.get("tracker_incarnation_id") != expected_tracker_incarnation_id
        ):
            outcome["incarnation_matches"] = False
            return tracker
        steps = tracker["steps"]
        if canonical not in steps:
            raise KeyError(
                f"step '{canonical}' not found in tracker; known steps: {sorted(steps)}"
            )
        state = steps[canonical]
        if not isinstance(state, dict):
            raise ValueError(f"tracker step '{canonical}' must be a JSON object")
        state["status"] = "complete"
        state["completed_at"] = datetime.now(UTC).isoformat()
        tracker["steps"] = steps

        done = sum(
            1
            for item in steps.values()
            if isinstance(item, dict) and item.get("status") in ("complete", "skipped")
        )
        total = len(steps)
        pipeline_id = tracker.get("pipeline_id", target.target_order_id)

        dependencies = tracker["dependencies"]
        dependents_with_unmet = sorted(
            dependent
            for dependent, prereqs in dependencies.items()
            if isinstance(dependent, str) and isinstance(prereqs, list)
            if canonical in prereqs
            and any(
                not isinstance(steps.get(prereq), dict)
                or steps[prereq].get("status") not in ("complete", "skipped")
                for prereq in prereqs
                if isinstance(prereq, str)
            )
        )
        outcome.update(
            step=canonical,
            order_id=target.target_order_id,
            pipeline_id=pipeline_id,
            done=done,
            total=total,
            dependents_with_unmet=dependents_with_unmet,
        )
        return tracker

    try:
        authority = mutate_tracker(target, lease, _mark)
    except (KeyError, ValueError) as exc:
        return deny_envelope(
            f"mark_step_complete: {exc}",
            stage="mark_step_complete",
            retriable=False,
        )
    if authority.data is None:
        return deny_envelope(
            authority.error or "mark_step_complete: tracker authority unavailable",
            stage="mark_step_complete",
            retriable=False,
        )
    if outcome.get("incarnation_matches") is False:
        return {
            **deny_envelope(
                "mark_step_complete: tracker incarnation changed.",
                stage="mark_step_complete",
                retriable=False,
            ),
            "incarnation_matches": False,
        }

    result = {
        "success": True,
        "incarnation_matches": True,
        "step": outcome["step"],
        "order_id": outcome["order_id"],
        "status": "complete",
        "pipeline_id": outcome["pipeline_id"],
        "done": outcome["done"],
        "total": outcome["total"],
    }
    dependents_with_unmet = outcome["dependents_with_unmet"]
    if isinstance(dependents_with_unmet, list) and dependents_with_unmet:
        result["advisory"] = (
            f"Step '{canonical}' marked complete but dependent steps "
            f"{dependents_with_unmet} still show unmet prerequisites — verify correctness"
        )
    return result


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield()
@track_response_size("recover_run_skill_result")
async def recover_run_skill_result(
    ctx: Context = CurrentContext(),
) -> str:
    """Recover the sole delivered ``run_skill`` receipt after transport loss. Never raises."""
    if (tier_gate := _require_orchestrator_exact("recover_run_skill_result")) is not None:
        return tier_gate
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        from autoskillit.server import _get_ctx  # circular-break

        tool_ctx = _get_ctx()
        authority = tool_ctx.run_skill_completion
        if authority is None:
            raise RuntimeError("run_skill completion authority is unavailable")
        receipt = authority.recover(
            kitchen_id=tool_ctx.kitchen_id,
            request_session_id=_request_session_identity(ctx),
        )
        return json.dumps(
            {
                "success": True,
                "receipt_id": receipt.receipt_id,
                "run_skill_success": receipt.success,
                "classification": receipt.classification,
                "session_id": receipt.child_session_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except ValueError as exc:
        return json.dumps(
            deny_envelope(
                f"recover_run_skill_result: {exc}",
                stage="preflight:run_skill_completion",
                retriable=False,
            )
        )
    except Exception:
        logger.exception("recover_run_skill_result_unexpected_error")
        return json.dumps(
            deny_envelope(
                "recover_run_skill_result: unexpected internal error.",
                stage="complete:run_skill_completion",
                retriable=True,
            )
        )


@mcp.tool(tags={"autoskillit", "kitchen", "kitchen-core"}, annotations={"readOnlyHint": True})
@_cancellation_shield()
@track_response_size("complete_run_skill_result")
async def complete_run_skill_result(
    receipt_id: str,
    ctx: Context = CurrentContext(),
) -> str:
    """Acknowledge one exactly delivered ``run_skill`` result. Never raises."""
    if (tier_gate := _require_orchestrator_exact("complete_run_skill_result")) is not None:
        return tier_gate
    if (gate := _require_enabled()) is not None:
        return gate
    try:
        from autoskillit.server import _get_ctx  # circular-break

        tool_ctx = _get_ctx()
        authority = tool_ctx.run_skill_completion
        if authority is None:
            raise RuntimeError("run_skill completion authority is unavailable")
        receipt = authority.acknowledge(
            receipt_id,
            kitchen_id=tool_ctx.kitchen_id,
            request_session_id=_request_session_identity(ctx),
        )
        tracker_result: Mapping[str, object] | None = None
        if receipt.tracker_incarnation_id:
            target = TrackerAuthorityTarget(
                target_order_id=receipt.tracker_order_id,
                path=Path(receipt.tracker_path),
                expected=True,
            )
            key, lease = _retain_context_tracker(
                tool_ctx,
                target,
                owner_kind="manual",
                owner_id=target.target_order_id,
            )
            try:
                if receipt.success:
                    tracker_result = authority.apply_tracker_credit(
                        tracker_order_id=receipt.tracker_order_id,
                        tracker_path=receipt.tracker_path,
                        tracker_kitchen_id=receipt.tracker_kitchen_id,
                        tracker_incarnation_id=receipt.tracker_incarnation_id,
                        step_name=receipt.step_name,
                        receipt_id=receipt.receipt_id,
                        effect=lambda: mark_step_complete(
                            target,
                            lease,
                            receipt.step_name,
                            expected_tracker_kitchen_id=receipt.tracker_kitchen_id,
                            expected_tracker_incarnation_id=receipt.tracker_incarnation_id,
                        ),
                    )
            finally:
                _release_context_tracker(tool_ctx, key)
        return json.dumps(
            {
                "success": True,
                "receipt_id": receipt.receipt_id,
                "run_skill_success": receipt.success,
                "classification": receipt.classification,
                "session_id": receipt.child_session_id,
                "tracker": tracker_result,
                "tracker_repairable": bool(
                    receipt.success
                    and receipt.tracker_incarnation_id
                    and not (tracker_result or {}).get("success")
                ),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
    except ValueError as exc:
        return json.dumps(
            deny_envelope(
                f"complete_run_skill_result: {exc}",
                stage="preflight:run_skill_completion",
                retriable=False,
            )
        )
    except Exception:
        logger.exception("complete_run_skill_result_unexpected_error")
        return json.dumps(
            deny_envelope(
                "complete_run_skill_result: unexpected internal error.",
                stage="complete:run_skill_completion",
                retriable=True,
            )
        )
