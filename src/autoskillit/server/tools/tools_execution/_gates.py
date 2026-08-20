"""Ingredient-lock and pipeline-dependency preflight gates, plus run_skill
completion-receipt bookkeeping (begin/finalize).
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, cast

from autoskillit.core import DISPATCH_ID_ENV_VAR, compute_bytes_hash
from autoskillit.pipeline import canonical_step_name as _canonical_step_name
from autoskillit.server._run_skill_completion import (
    FinalizedRunSkillCompletionResponse,
    _request_session_identity,
    stage_run_skill_completion_response,
)
from autoskillit.server.tools import tools_execution as _te_pkg
from autoskillit.server.tools._overlay_state import OverlayStateError
from autoskillit.server.tools._types import deny_envelope

if TYPE_CHECKING:
    from fastmcp import Context

    from autoskillit.core import TrackerAuthorityReadResult, TrackerAuthorityTarget
    from autoskillit.pipeline import ToolContext

INGREDIENT_LOCK_DENY_PREFIX = "INGREDIENT LOCK ENFORCED"
DEPENDENCY_DENY_PREFIX = "DEPENDENCY UNMET"


def _check_ingredient_locks(step_name: str, order_id: str) -> str | None:
    """Check if step_name is locked out by ingredient locks. Returns deny JSON or None."""
    from autoskillit.server import _get_ctx  # circular-break

    ctx = _get_ctx()
    try:
        overlay = _te_pkg.read_overlay(ctx.project_dir)
    except (OSError, OverlayStateError) as exc:
        storage_error = isinstance(exc, OSError) or isinstance(exc.__cause__, OSError)
        error_kind = "Unable to read" if storage_error else "Invalid"
        return json.dumps(
            deny_envelope(
                f"{INGREDIENT_LOCK_DENY_PREFIX}: {error_kind} persisted lock state: {exc}",
                stage="preflight:ingredient_locks",
                retriable=storage_error,
            )
        )

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


def _check_pipeline_deps(
    step_name: str,
    authority: TrackerAuthorityReadResult | None,
) -> str | None:
    """Check if step_name's dependencies are satisfied. Returns deny JSON or None."""
    if authority is None:
        return None
    if authority.error is not None:
        return json.dumps(
            deny_envelope(
                authority.error,
                stage="preflight:pipeline_deps",
                retriable=False,
            )
        )
    tracker = authority.data
    if tracker is None:
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
                f"first. Pipeline '{authority.target_order_id}': {dep_status}."
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
    overlay = _te_pkg.read_overlay(ctx.project_dir)
    locked_steps = overlay.get("locked_steps", {})
    if not locked_steps:
        return False
    effective_oid = order_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")
    if effective_oid:
        return any(v is False for v in locked_steps.get(effective_oid, {}).values())
    return any(v is False for steps in locked_steps.values() for v in steps.values())


def _completion_tracker_binding(
    tool_ctx: ToolContext,
    order_id: str,
    *,
    tracker_target: TrackerAuthorityTarget | None = None,
) -> tuple[str, str, str, str]:
    """Resolve immutable tracker identity for a new completion receipt."""
    from autoskillit.server.tools.tools_pipeline_tracker import (  # circular-break
        _release_context_tracker,
        _retain_context_tracker,
        read_tracker_identity,
        select_tracker_target,
    )

    target = tracker_target or select_tracker_target(tool_ctx, order_id, expected=bool(order_id))
    if target is None or not target.path.exists():
        return "", "", "", ""
    key, lease = _retain_context_tracker(
        tool_ctx,
        target,
        owner_kind="manual",
        owner_id=target.target_order_id,
    )
    try:
        tracker_identity = read_tracker_identity(target, lease)
    except Exception:
        _release_context_tracker(tool_ctx, key)
        raise
    if tracker_identity is None:
        _release_context_tracker(tool_ctx, key)
        return "", "", "", ""
    kitchen_id, incarnation_id = tracker_identity
    _release_context_tracker(tool_ctx, key)
    return target.target_order_id, str(target.path.resolve()), kitchen_id, incarnation_id


def _begin_run_skill_completion(
    tool_ctx: ToolContext,
    *,
    request_context: Context,
    order_id: str,
    step_name: str,
    tracker_target: TrackerAuthorityTarget | None = None,
) -> str:
    authority = tool_ctx.run_skill_completion
    if authority is None:
        raise RuntimeError("run_skill completion authority is unavailable")
    request_session_id = _request_session_identity(request_context)

    tracker_order_id, tracker_path, tracker_kitchen_id, tracker_incarnation_id = (
        _completion_tracker_binding(
            tool_ctx,
            order_id,
            tracker_target=tracker_target,
        )
    )
    return authority.begin(
        kitchen_id=tool_ctx.kitchen_id,
        request_session_id=request_session_id,
        tracker_order_id=tracker_order_id,
        tracker_path=tracker_path,
        tracker_kitchen_id=tracker_kitchen_id,
        tracker_incarnation_id=tracker_incarnation_id,
        step_name=step_name,
    )


def _finalize_run_skill_completion(
    tool_ctx: ToolContext,
    invocation_id: str,
    rendered: str,
    *,
    child_session_id: str = "",
) -> str:
    """Draft a receipt and bind its rendered carrier to this request."""
    authority = tool_ctx.run_skill_completion
    if authority is None:
        raise RuntimeError("run_skill completion authority is unavailable")
    try:
        payload = json.loads(rendered)
    except json.JSONDecodeError:
        payload = {
            "success": False,
            "is_error": True,
            "exit_code": -1,
            "subtype": "response_adjudication_error",
            "error": "run_skill produced a non-JSON terminal response",
        }
    if not isinstance(payload, dict):
        payload = {
            "success": False,
            "is_error": True,
            "exit_code": -1,
            "subtype": "response_adjudication_error",
            "error": "run_skill produced a non-object terminal response",
        }
    success = payload.get("success") is True
    classification = str(payload.get("subtype") or ("success" if success else "failed"))
    receipt = authority.draft(
        invocation_id,
        classification=classification,
        success=success,
        result_digest=compute_bytes_hash(rendered.encode("utf-8")),
        child_session_id=child_session_id,
    )
    payload["receipt_id"] = receipt.receipt_id
    finalized = FinalizedRunSkillCompletionResponse(
        rendered=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        authority=authority,
        receipt=receipt,
    )
    return cast(str, stage_run_skill_completion_response(finalized))
