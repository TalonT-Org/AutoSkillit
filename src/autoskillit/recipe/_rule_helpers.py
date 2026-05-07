"""Shared helper utilities for recipe semantic rules."""

from __future__ import annotations

from autoskillit.core import get_logger
from autoskillit.recipe._analysis import ValidationContext

logger = get_logger(__name__)


def _is_loop_guard_step(step_name: str, ctx: ValidationContext) -> bool:
    """Return True if step_name is a loop iteration guard via check_loop_iteration."""
    step = ctx.recipe.steps.get(step_name)
    if step is None:
        return False
    if step.tool != "run_python":
        return False
    callable_str = step.with_args.get("callable", "")
    return callable_str == "autoskillit.smoke_utils.check_loop_iteration"
