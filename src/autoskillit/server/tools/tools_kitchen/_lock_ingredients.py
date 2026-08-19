"""lock_ingredients tool and ingredient-lock overlay helpers."""

from __future__ import annotations

import difflib
import json
import os
from pathlib import Path

from autoskillit.config import SERVER_AUTHORITATIVE_INGREDIENTS
from autoskillit.core import (
    DISPATCH_ID_ENV_VAR,
    get_logger,
)
from autoskillit.server import mcp
from autoskillit.server._guards import _require_orchestrator_exact
from autoskillit.server._misc import _hook_config_path
from autoskillit.server._notify import track_response_size

# Late-binding for monkeypatch reach: tests patch
# "autoskillit.server.tools.tools_kitchen.<name>" (the package facade), so
# cross-submodule helpers must be resolved via attribute access on the
# package at call time rather than imported by name into this submodule.
from autoskillit.server.tools import tools_kitchen as _tk_pkg
from autoskillit.server.tools._authority_feedback import build_authority_rejection_envelope
from autoskillit.server.tools._cancellation_shield import _cancellation_shield

logger = get_logger(__name__)


def _write_ingredient_locks(
    project_dir: Path,
    pipeline_id: str,
    new_locked: dict[str, str] | None,
    unlock_keys: list[str] | None,
    active_steps: dict,
) -> dict:
    """Atomically read-modify-write ingredient locks under the session lock."""

    def _mutate(existing: dict) -> None:
        locked_ingredients = existing.setdefault("locked_ingredients", {})
        current = dict(locked_ingredients.get(pipeline_id, {}))
        if unlock_keys:
            _apply_unlock_keys(current, unlock_keys)
        if new_locked:
            current.update(new_locked)
        if new_locked or unlock_keys:
            locked_ingredients[pipeline_id] = current
            existing.setdefault("locked_steps", {})[pipeline_id] = _compute_unlocked_steps(
                active_steps,
                current,
            )

    return _tk_pkg.update_overlay(project_dir, _mutate)


def _compute_unlocked_steps(
    active_steps: dict, current_pipeline_li: dict[str, str]
) -> dict[str, bool]:
    """Compute unlocked_steps from active_recipe_steps and remaining ingredients.

    For each step with a skip_when_false ingredient present in current_pipeline_li,
    compute the truthiness of the remaining ingredient value.
    """
    unlocked_steps: dict[str, bool] = {}
    for step_name, step_obj in active_steps.items():
        swf = (
            getattr(step_obj, "skip_when_false", None)
            if hasattr(step_obj, "skip_when_false")
            else None
        )
        if swf:
            ingredient_name = swf.removeprefix("inputs.")
            if ingredient_name in current_pipeline_li:
                val = current_pipeline_li[ingredient_name]
                is_truthy = val.lower() not in ("false", "0", "no", "off", "")
                unlocked_steps[step_name] = is_truthy
    return unlocked_steps


def _apply_unlock_keys(current_pipeline_li: dict[str, str], unlock_keys: list[str]) -> None:
    """Remove unlock keys from the current pipeline ingredients dict in-place."""
    for key in unlock_keys:
        current_pipeline_li.pop(key, None)


def _build_ingredient_key_suggestions(
    unknown: set[str], declared: frozenset[str]
) -> dict[str, list[str]]:
    suggestions: dict[str, list[str]] = {}
    declared_sorted = sorted(declared)
    for key in sorted(unknown):
        matches = difflib.get_close_matches(key, declared_sorted, n=2, cutoff=0.5)
        if matches:
            suggestions[key] = list(matches)
    return suggestions


@mcp.tool(
    tags={"autoskillit"}, annotations={"readOnlyHint": True}, meta={"anthropic/alwaysLoad": True}
)
@_cancellation_shield()
@track_response_size("lock_ingredients")
async def lock_ingredients(
    locked: dict[str, str] | None = None,
    pipeline_id: str = "",
    unlock: list[str] | None = None,
) -> str:
    """Lock recipe ingredient values for this session.

    Call at session start to bind ingredient values structurally.
    Locked ingredients are enforced by a server-side check in run_skill
    and supplementally by the ingredient_lock_guard PreToolUse hook.
    run_skill calls for steps whose skip_when_false ingredient is locked
    to a falsy value will be denied.

    Server-authoritative ingredients (base_branch, local_review_rounds,
    adversarial_review_level, is_fleet_dispatch,
    dispatch_id) are rejected with a structured error envelope; the
    rejected key names appear in both ``error`` and ``user_visible_message``.

    Call with unlock=["ingredient_name"] to release a lock.

    Never raises.
    """
    try:
        if (h := _require_orchestrator_exact("lock_ingredients")) is not None:
            return h
        from autoskillit.server import _get_ctx  # circular-break

        ctx = _get_ctx()
        hook_cfg_path = _hook_config_path(ctx.project_dir)
        if not hook_cfg_path.exists():
            return json.dumps(
                {
                    "success": False,
                    "error": "Kitchen is not open — hook config file absent.",
                }
            )
        effective_pipeline_id = pipeline_id or os.environ.get(DISPATCH_ID_ENV_VAR, "")

        if locked:
            server_auth_overlap = set(locked.keys()) & SERVER_AUTHORITATIVE_INGREDIENTS
            if server_auth_overlap:
                return json.dumps(build_authority_rejection_envelope(server_auth_overlap))

        if not locked and not unlock:
            return json.dumps(
                {
                    "success": False,
                    "error": "At least one of 'locked' or 'unlock' must be provided.",
                }
            )

        active_steps = getattr(ctx, "active_recipe_steps", None) or {}
        declared_ingredients = ctx.active_recipe_ingredients
        if declared_ingredients is not None:
            all_supplied_keys: set[str] = set()
            if locked:
                all_supplied_keys |= set(locked.keys())
            if unlock:
                all_supplied_keys |= set(unlock)
            unknown = all_supplied_keys - declared_ingredients - SERVER_AUTHORITATIVE_INGREDIENTS
            if unknown:
                return json.dumps(
                    {
                        "success": False,
                        "error": (
                            f"Unknown ingredient keys: {sorted(unknown)}. "
                            f"Valid keys: {sorted(declared_ingredients)}."
                        ),
                        "suggestions": _build_ingredient_key_suggestions(
                            unknown, declared_ingredients
                        ),
                    }
                )

        updated = _write_ingredient_locks(
            ctx.project_dir,
            effective_pipeline_id,
            locked,
            unlock,
            active_steps,
        )

        return json.dumps(
            {
                "success": True,
                "locked": updated.get("locked_ingredients", {}).get(effective_pipeline_id, {}),
                "locked_steps": updated.get("locked_steps", {}).get(effective_pipeline_id, {}),
            }
        )
    except Exception as exc:
        logger.error("lock_ingredients unhandled exception", exc_info=True)
        return json.dumps({"success": False, "error": f"{type(exc).__name__}: {exc}"})
