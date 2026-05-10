"""Shared helper utilities for recipe semantic rules."""

from __future__ import annotations

import regex as re

from autoskillit.recipe._analysis import ValidationContext

_PATH_SAFE_LOOKBEHIND = r"(?<![.a-zA-Z_/])"
_PATH_SAFE_LOOKAHEAD = r"(?![.a-zA-Z_/])"


def cmd_keyword_pattern(
    keywords: str,
    *,
    flags: int = 0,
    lookahead: bool = True,
) -> re.Pattern[str]:
    """Build a keyword-matching regex with automatic path-safe guards.

    The returned pattern wraps the keyword alternation with:
    - A negative lookbehind rejecting ``.``, letters, ``_``, ``/`` before the match
    - A negative lookahead rejecting ``.``, letters, ``_``, ``/`` after the match
      (unless disabled)

    Args:
        keywords: A regex alternation string (e.g., ``r"mapfile|declare|local|export"``).
        flags: Additional ``regex`` flags (e.g., ``re.VERBOSE``).
        lookahead: Whether to add a trailing path-safe lookahead (default True).
    """
    tail = _PATH_SAFE_LOOKAHEAD if lookahead else r"\b"
    return re.compile(rf"{_PATH_SAFE_LOOKBEHIND}(?:{keywords}){tail}", flags)


def _is_loop_guard_step(step_name: str, ctx: ValidationContext) -> bool:
    """Return True if step_name is a loop iteration guard via check_loop_iteration."""
    step = ctx.recipe.steps.get(step_name)
    if step is None:
        return False
    if step.tool != "run_python":
        return False
    callable_str = step.with_args.get("callable", "")
    return callable_str == "autoskillit.smoke_utils.check_loop_iteration"
