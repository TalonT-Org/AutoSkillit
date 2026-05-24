"""MCP tool handlers: dispatch_food_truck, record_gate_dispatch."""

from __future__ import annotations

import functools
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.fleet import DispatchOutcome

from fastmcp import Context
from fastmcp.dependencies import CurrentContext

from autoskillit.core import get_logger
from autoskillit.server import mcp
from autoskillit.server._guards import _require_enabled, _require_fleet
from autoskillit.server._notify import track_response_size

logger = get_logger(__name__)

_MAX_CALLER_INSTRUCTIONS_LEN = 2000


def _write_dispatch_to_campaign_state(
    campaign_state_path_str: str,
    effective_name: str,
    outcome: DispatchOutcome,
    per_dispatch_state_path: Path | None = None,
) -> None:
    """Write the dispatch outcome to the campaign state file.

    Accepts a DispatchOutcome (DispatchCompleted or DispatchRejected) and persists
    the dispatch record to AUTOSKILLIT_CAMPAIGN_STATE_PATH. Never raises — state
    write failures are non-fatal.

    When per_dispatch_state_path is provided, reads the authoritative DispatchRecord
    from the per-dispatch state file and forwards it directly, avoiding manual
    field reconstruction and eliminating double-normalization of token_usage.
    """
    try:
        from autoskillit.fleet import (  # noqa: PLC0415
            DispatchCompleted,
            DispatchRecord,
            DispatchRejected,
            read_state,
            upsert_dispatch_record_by_name,
        )

        match outcome:
            case DispatchRejected(error_code=code, message=msg):
                upsert_dispatch_record_by_name(
                    Path(campaign_state_path_str),
                    DispatchRecord.for_refusal(
                        name=effective_name,
                        error_code=code,
                        diagnostic_message=msg,
                    ),
                )
            case DispatchCompleted() as completed:
                if per_dispatch_state_path is not None:
                    per_dispatch_state = read_state(per_dispatch_state_path)
                    if per_dispatch_state is None:
                        logger.warning(
                            "_write_dispatch_to_campaign_state: read_state(%s) returned None "
                            "— falling back to manual reconstruction",
                            per_dispatch_state_path,
                        )
                    else:
                        for d in per_dispatch_state.dispatches:
                            if d.name == effective_name:
                                upsert_dispatch_record_by_name(
                                    Path(campaign_state_path_str),
                                    d,
                                )
                                return
                        logger.warning(
                            "_write_dispatch_to_campaign_state: no dispatch named %r in %s "
                            "— falling back to manual reconstruction",
                            effective_name,
                            per_dispatch_state_path,
                        )
                upsert_dispatch_record_by_name(
                    Path(campaign_state_path_str),
                    DispatchRecord(
                        name=effective_name,
                        status=completed.dispatch_status,
                        dispatch_id=completed.dispatch_id,
                        dispatched_session_id=completed.dispatched_session_id,
                        reason=completed.reason,
                        token_usage=completed.token_usage,
                    ),
                )
    except Exception:
        logger.warning("_write_dispatch_to_campaign_state: failed", exc_info=True)


def _get_food_truck_prompt_builder() -> Callable[..., str]:
    """Return the food truck prompt builder with mcp_prefix pre-bound."""
    from autoskillit.core import detect_autoskillit_mcp_prefix
    from autoskillit.fleet import _build_food_truck_prompt

    mcp_prefix = detect_autoskillit_mcp_prefix()
    return functools.partial(_build_food_truck_prompt, mcp_prefix=mcp_prefix)


@mcp.tool(
    tags={"autoskillit", "kitchen-core", "fleet"},
    annotations={"readOnlyHint": True},
)
@track_response_size("dispatch_food_truck")
async def dispatch_food_truck(
    recipe: str,
    task: str,
    ingredients: dict[str, str] | None = None,
    dispatch_name: str | None = None,
    timeout_sec: int | None = None,
    capture: dict[str, str] | None = None,
    resume_session_id: str | None = None,
    resume_checkpoint: dict[str, object] | None = None,
    idle_output_timeout: int | None = None,
    prior_dispatch_id: str | None = None,
    skip_when: str | None = None,
    resume_message: str | None = None,
    caller_instructions: str | None = None,
    ctx: Context = CurrentContext(),
) -> str:
    """Dispatch a single food truck L2 session for one recipe.

    Spawns a headless subprocess that executes the given recipe with the
    provided task and ingredient overrides. Returns a JSON envelope with
    dispatch_id, dispatched_session_id, l3_payload, and token_usage.

    Args:
        recipe: Recipe name to dispatch (must be kind=standard).
        task: Task description for the L2 food truck session.
        ingredients: Optional ingredient overrides (all values must be strings).
        dispatch_name: Optional display name for the dispatch record.
        timeout_sec: Optional L2 session timeout override in seconds.
        capture: Optional dict mapping capture keys to "${{ result.field }}" templates.
            Extracted values are persisted in the campaign context for downstream
            dispatches to reference via "${{ campaign.key }}" in their ingredients.
        resume_checkpoint: Checkpoint dict from a prior RESUMABLE dispatch envelope.
            Pass the "resume_checkpoint" field from the prior result to inject completed
            items context into the resume prompt.
        skip_when: Optional condition expression. If evaluation against accumulated
            campaign captures returns true, the dispatch is recorded as SKIPPED without
            executing the recipe.
        resume_message: Optional caller-supplied context for the resumed session.
            Injected as a CALLER CONTEXT section in the resume prompt, allowing the
            caller to communicate changed conditions (e.g. "quota guard is now
            disabled") that the LLM should act on.
        caller_instructions: Optional free-text instructions from the dispatching caller to
            the food truck session. Injected as a CALLER INSTRUCTIONS section in
            the food truck system prompt. Use to forward user guidance such as "use model opus
            for the implement step" or "skip review if diff is under 20 lines."
            Only meaningful on fresh dispatches (not resumes). When None or empty,
            the L2 prompt is unchanged.

    Resume vs. Fresh Dispatch:
        - ``resume_session_id``: Session ID to resume (``--resume <id>``).
        - ``resume_checkpoint``: Completed-items context from the prior session.
        - ``resume_message``: Free-text caller context injected into the resume prompt.
        All three are optional and only meaningful when resuming a prior dispatch.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    if (fleet_gate := _require_fleet("dispatch_food_truck")) is not None:
        return fleet_gate

    try:
        if caller_instructions and len(caller_instructions) > _MAX_CALLER_INSTRUCTIONS_LEN:
            caller_instructions = caller_instructions[:_MAX_CALLER_INSTRUCTIONS_LEN]

        # Feature guard: config authority check independent of MCP visibility state.
        # Fleet sessions open the gate unconditionally at boot; this catch-all ensures
        # dispatch_food_truck never executes when features.fleet is disabled in config.
        from autoskillit.core import FleetErrorCode, fleet_error, is_feature_enabled
        from autoskillit.server import _get_ctx as _get_ctx_for_feature_check

        _feature_ctx = _get_ctx_for_feature_check()
        if not is_feature_enabled(
            "fleet",
            _feature_ctx.config.features,
            experimental_enabled=_feature_ctx.config.experimental_enabled,
        ):
            return fleet_error(
                FleetErrorCode.FLEET_FEATURE_DISABLED,
                "Fleet feature is disabled. Set features.experimental_enabled: true to enable.",
            )

        campaign_state_path_str = os.environ.get("AUTOSKILLIT_CAMPAIGN_STATE_PATH")
        continue_on_failure = (
            os.environ.get("AUTOSKILLIT_CONTINUE_ON_FAILURE", "false").lower() == "true"
        )
        if campaign_state_path_str and not continue_on_failure:
            from autoskillit.fleet import (  # noqa: PLC0415
                has_blocking_dispatch,
                reset_blocking_dispatch,
            )

            campaign_sp = Path(campaign_state_path_str)
            if dispatch_name:
                if not reset_blocking_dispatch(campaign_sp, dispatch_name):
                    still_blocked = has_blocking_dispatch(campaign_sp)
                    logger.warning(
                        "reset_blocking_dispatch: dispatch %r not found in a blocking state — %s",
                        dispatch_name,
                        "campaign is blocked by a different dispatch"
                        if still_blocked
                        else "no active campaign block detected",
                    )
                    if still_blocked:
                        return fleet_error(
                            FleetErrorCode.FLEET_CAMPAIGN_HALTED,
                            "Campaign halted: a prior dispatch failed and "
                            "continue_on_failure is false. "
                            "No further dispatches permitted.",
                        )
            if has_blocking_dispatch(campaign_sp):
                return fleet_error(
                    FleetErrorCode.FLEET_CAMPAIGN_HALTED,
                    "Campaign halted: a prior dispatch failed and "
                    "continue_on_failure is false. "
                    "No further dispatches permitted.",
                )

        from autoskillit.core import SessionCheckpoint  # noqa: PLC0415
        from autoskillit.fleet import (  # noqa: PLC0415
            _INFRASTRUCTURE_FAILURE_REASONS,
            DispatchCompleted,
            DispatchRecord,
            DispatchResult,
            DispatchStatus,
            evaluate_skip_when,
            execute_dispatch,
            read_all_campaign_captures,
            upsert_dispatch_record_by_name,
        )
        from autoskillit.server import _get_ctx
        from autoskillit.server._misc import (  # noqa: PLC0415
            _refresh_quota_cache,
            check_and_sleep_if_needed,
            invalidate_cache,
            resolve_provider,
        )

        parsed_checkpoint = (
            SessionCheckpoint.from_dict(resume_checkpoint) if resume_checkpoint else None
        )
        tool_ctx = _get_ctx()
        from autoskillit.core import find_caller_session_id

        caller_session_id = find_caller_session_id(project_dir=tool_ctx.project_dir)
        effective_name = dispatch_name or recipe

        if skip_when:
            dispatches_dir = tool_ctx.temp_dir / "dispatches"
            accumulated_captures = read_all_campaign_captures(dispatches_dir, tool_ctx.kitchen_id)
            error_code, error_message, skip_condition_true = evaluate_skip_when(
                skip_when, accumulated_captures, ingredients
            )
            if error_code is not None:
                return fleet_error(error_code, error_message or "")

            if skip_condition_true:
                if campaign_state_path_str:
                    upsert_dispatch_record_by_name(
                        Path(campaign_state_path_str),
                        DispatchRecord(
                            name=effective_name,
                            status=DispatchStatus.SKIPPED,
                            reason="skip_when condition evaluated to true",
                        ),
                    )
                return fleet_error(
                    FleetErrorCode.FLEET_DISPATCH_SKIPPED,
                    "Dispatch skipped: skip_when condition evaluated to true",
                )

        result = await execute_dispatch(
            tool_ctx=tool_ctx,
            recipe=recipe,
            task=task,
            ingredients=ingredients,
            dispatch_name=dispatch_name,
            timeout_sec=timeout_sec,
            prompt_builder=_get_food_truck_prompt_builder(),
            quota_checker=lambda cfg: check_and_sleep_if_needed(
                cfg,
                provider=resolve_provider(tool_ctx.config.providers.default_provider),
            ),
            quota_refresher=_refresh_quota_cache,
            cache_invalidator=invalidate_cache,
            capture=capture,
            resume_session_id=resume_session_id,
            resume_checkpoint=parsed_checkpoint,
            idle_output_timeout=idle_output_timeout,
            caller_session_id=caller_session_id,
            prior_dispatch_id=prior_dispatch_id,
            resume_message=resume_message,
            caller_instructions=caller_instructions,
        )

        if campaign_state_path_str and isinstance(result, DispatchResult):
            _write_dispatch_to_campaign_state(
                campaign_state_path_str,
                effective_name,
                result.outcome,
                result.per_dispatch_state_path,
            )

        outcome = result.outcome

        # Post-dispatch halt: if continue_on_failure=false and the dispatch failed
        # (logic failure, not infrastructure), return FLEET_CAMPAIGN_HALTED immediately.
        # Infrastructure failures (fleet_l3_no_result_block, fleet_quota_exhausted) do
        # not halt — they are retriable at the L3 level without campaign-level impact.
        # Also skip halt if dispatch_name was provided — the pre-dispatch gate already
        # handled the reset case, and a retry of the blocking dispatch should proceed.
        if (
            campaign_state_path_str
            and not continue_on_failure
            and isinstance(outcome, DispatchCompleted)
            and outcome.dispatch_status == DispatchStatus.FAILURE
            and outcome.reason not in _INFRASTRUCTURE_FAILURE_REASONS
            and not dispatch_name
        ):
            return fleet_error(
                FleetErrorCode.FLEET_CAMPAIGN_HALTED,
                "Campaign halted: a prior dispatch failed and "
                "continue_on_failure is false. "
                "No further dispatches permitted.",
            )

        if (
            campaign_state_path_str
            and isinstance(outcome, DispatchCompleted)
            and outcome.dispatch_status != DispatchStatus.SUCCESS
            and (continue_on_failure or dispatch_name)
        ):
            logger.warning(
                "dispatch_non_success_allowed_past_halt_gate",
                dispatch_name=effective_name,
                dispatch_status=outcome.dispatch_status,
                reason=outcome.reason,
                continue_on_failure=continue_on_failure,
                has_dispatch_name=bool(dispatch_name),
            )

        return outcome.to_envelope()
    except Exception as exc:
        logger.error("dispatch_food_truck unhandled exception", exc_info=True)
        from autoskillit.core import FleetErrorCode, fleet_error

        return fleet_error(
            FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
            f"{type(exc).__name__}: {exc}",
        )


@mcp.tool(
    tags={"autoskillit", "kitchen-core", "fleet"},
    annotations={"readOnlyHint": True},
)
@track_response_size("record_gate_dispatch")
async def record_gate_dispatch(
    dispatch_name: str,
    approved: bool,
    ctx: Context = CurrentContext(),
) -> str:
    """Record the outcome of a gate dispatch to the campaign state file.

    Gate dispatches are handled by AskUserQuestion (no L3 session). This tool
    persists the user's approval/rejection so that campaign resume can skip
    completed gates.

    Args:
        dispatch_name: Name of the gate dispatch in the campaign manifest.
        approved: True if the user approved the gate, False if rejected.

    Never raises.
    """
    if (gate := _require_enabled()) is not None:
        return gate
    if (fleet_gate := _require_fleet("record_gate_dispatch")) is not None:
        return fleet_gate

    try:
        from autoskillit.core import FleetErrorCode, fleet_error, is_feature_enabled
        from autoskillit.fleet import record_gate_outcome
        from autoskillit.server import _get_ctx as _get_ctx_for_feature_check

        _feature_ctx = _get_ctx_for_feature_check()
        if not is_feature_enabled(
            "fleet",
            _feature_ctx.config.features,
            experimental_enabled=_feature_ctx.config.experimental_enabled,
        ):
            return fleet_error(
                FleetErrorCode.FLEET_FEATURE_DISABLED,
                "Fleet feature is disabled. Set features.experimental_enabled: true to enable.",
            )

        campaign_state_path_str = os.environ.get("AUTOSKILLIT_CAMPAIGN_STATE_PATH")
        if not campaign_state_path_str:
            return fleet_error(
                FleetErrorCode.FLEET_GATE_NO_CAMPAIGN,
                "No AUTOSKILLIT_CAMPAIGN_STATE_PATH set — not running in campaign mode.",
            )

        result = record_gate_outcome(Path(campaign_state_path_str), dispatch_name, approved)
        if not result.success:
            try:
                error_code = FleetErrorCode(result.error_code)
            except ValueError:
                error_code = FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH
            return fleet_error(error_code, result.error_message)

        return json.dumps(
            {"success": True, "dispatch_name": result.dispatch_name, "status": result.status}
        )
    except Exception as exc:
        logger.error("record_gate_dispatch unhandled exception", exc_info=True)
        from autoskillit.core import FleetErrorCode, fleet_error

        return fleet_error(
            FleetErrorCode.FLEET_L3_STARTUP_OR_CRASH,
            f"{type(exc).__name__}: {exc}",
        )
