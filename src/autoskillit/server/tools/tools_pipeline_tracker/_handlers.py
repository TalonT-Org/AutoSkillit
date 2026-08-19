"""MCP tool handlers for the pipeline tracker, run_skill recovery, and completion."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import (
    DISPATCH_ID_ENV_VAR,
    ArtifactLease,
    TrackerAuthorityTarget,
    get_logger,
    initialize_manual_tracker,
    mutate_tracker,
)
from autoskillit.pipeline import canonical_step_name
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled, _require_orchestrator_exact
from autoskillit.server._notify import track_response_size
from autoskillit.server._recipe_segment_delivery import attach_recipe_segment
from autoskillit.server._run_skill_completion import _request_session_identity
from autoskillit.server.tools import (
    tools_pipeline_tracker,  # noqa: F401 — late-binding for monkeypatch reach
)
from autoskillit.server.tools._cancellation_shield import _cancellation_shield
from autoskillit.server.tools._types import deny_envelope
from autoskillit.server.tools.tools_pipeline_tracker._authority import (
    _release_context_tracker,
    _resolve_skipped_steps,
    _retain_context_tracker,
    select_tracker_target,
)
from autoskillit.server.tools.tools_pipeline_tracker._status import (
    _build_tracker_steps,
    _compute_status_counts,
)

if TYPE_CHECKING:
    from autoskillit.pipeline import ToolContext

logger = get_logger(__name__)


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
            try:
                result = tools_pipeline_tracker._handle_init(ctx, target, lease, dependencies)
            except Exception:
                _release_context_tracker(ctx, key)
                raise
            if not json.loads(result).get("success"):
                _release_context_tracker(ctx, key)
            return result

        if op == "status":
            try:
                result = tools_pipeline_tracker._handle_status(target, lease)
            except Exception:
                _release_context_tracker(ctx, key)
                raise
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
    ctx: ToolContext,
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
    authority = tools_pipeline_tracker.read_tracker_authority(target, lease)
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


def _handle_complete(ctx: ToolContext, effective_pipeline_id: str, step_name: str) -> str:
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
    try:
        tracker_authority = tools_pipeline_tracker.read_tracker_authority(target, lease)
    except Exception:
        _release_context_tracker(ctx, key)
        raise
    if tracker_authority.data is None:
        _release_context_tracker(ctx, key)
        identity_error = cast(str, tracker_authority.error)
        return json.dumps(
            deny_envelope(
                f"record_pipeline_step: failed to read tracker identity: {identity_error}",
                stage="preflight:pipeline_tracker",
                retriable=identity_error.startswith("Cannot read pipeline tracker"),
            )
        )
    tracker_kitchen_id = tracker_authority.data.get("kitchen_id")
    tracker_incarnation_id = tracker_authority.data.get("tracker_incarnation_id")
    if not isinstance(tracker_kitchen_id, str) or not isinstance(tracker_incarnation_id, str):
        _release_context_tracker(ctx, key)
        return json.dumps(
            deny_envelope(
                "record_pipeline_step: tracker incarnation identity is missing.",
                stage="preflight:pipeline_tracker",
                retriable=False,
            )
        )
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
            effect=lambda: tools_pipeline_tracker.mark_step_complete(
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
            cast(str, authority.error),
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
        request_session_id = _request_session_identity(ctx)
        receipt = authority.acknowledge(
            receipt_id,
            kitchen_id=tool_ctx.kitchen_id,
            request_session_id=request_session_id,
        )
        prepared_segment = tools_pipeline_tracker.prepare_recipe_segment_delivery(
            tool_ctx, receipt.step_name
        )

        def _apply_tracker_outcome() -> Mapping[str, object]:
            if not receipt.tracker_incarnation_id:
                return {"success": True, "status": "not_applicable"}
            if not receipt.success:
                return {"success": True, "status": "not_applied"}
            try:
                target = TrackerAuthorityTarget.for_project(
                    tool_ctx.project_dir,
                    receipt.tracker_order_id,
                    expected=True,
                )
                if Path(receipt.tracker_path).resolve() != target.path.resolve():
                    raise ValueError(
                        "receipt tracker path is outside the project tracker authority"
                    )
                key, lease = _retain_context_tracker(
                    tool_ctx,
                    target,
                    owner_kind="manual",
                    owner_id=f"receipt:{receipt.receipt_id}",
                )
                try:
                    return authority.apply_tracker_credit(
                        tracker_order_id=receipt.tracker_order_id,
                        tracker_path=receipt.tracker_path,
                        tracker_kitchen_id=receipt.tracker_kitchen_id,
                        tracker_incarnation_id=receipt.tracker_incarnation_id,
                        step_name=receipt.step_name,
                        receipt_id=receipt.receipt_id,
                        effect=lambda: tools_pipeline_tracker.mark_step_complete(
                            target,
                            lease,
                            receipt.step_name,
                            expected_tracker_kitchen_id=receipt.tracker_kitchen_id,
                            expected_tracker_incarnation_id=receipt.tracker_incarnation_id,
                        ),
                    )
                finally:
                    _release_context_tracker(tool_ctx, key)
            except Exception:
                logger.exception("complete_run_skill_result_tracker_credit_deferred")
                return deny_envelope(
                    "complete_run_skill_result: tracker credit was not applied; use "
                    "record_pipeline_step(op='complete') to repair it.",
                    stage="tracker_credit",
                    retriable=True,
                )

        tracker_result = authority.apply_acknowledged_tracker_outcome(
            receipt.receipt_id,
            kitchen_id=tool_ctx.kitchen_id,
            request_session_id=request_session_id,
            effect=_apply_tracker_outcome,
        )
        response = attach_recipe_segment(
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
                    and not tracker_result.get("success")
                ),
            },
            prepared_segment,
            success=receipt.success,
        )
        return json.dumps(
            response,
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
