"""Shared helper utilities for recipe semantic rules."""

from __future__ import annotations

from typing import Any

import regex as re

from autoskillit.recipe._analysis import ValidationContext

# Trigger regex: matches multiple sentinel-indicating phrases
_SENTINEL_TRIGGER_RE = re.compile(
    r"[Ee]xample\s+sentinel:|sentinel\s+JSON:|sentinel:\s*(?=\{)",
    re.DOTALL,
)


def extract_sentinel_json_blocks(text: str) -> list[str]:
    """Extract complete JSON object strings from a text using bracket-aware parsing.

    Handles nested braces and arrays, unlike a simple regex that stops at the first `}`.
    Matches any sentinel-indicating trigger phrase (e.g., "Example sentinel:",
    "sentinel JSON:", "sentinel: {…}") and then uses bracket counting to find the matching
    closing brace for the JSON object that follows.

    Returns a list of raw JSON strings (still serialized) that can be passed to json.loads().
    """
    blocks: list[str] = []
    for match in _SENTINEL_TRIGGER_RE.finditer(text):
        # Position right after the trigger phrase
        start = match.end()
        # Find first non-whitespace character
        while start < len(text) and text[start] in " \t\n\r":
            start += 1
        if start >= len(text) or text[start] != "{":
            continue
        # Bracket-counting scan to find matching closing brace
        depth = 0
        i = start
        while i < len(text):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    blocks.append(text[start : i + 1])
                    break
            i += 1
    return blocks


def _is_failure_sentinel_value(val: Any) -> bool:
    """Return True if *val* represents a failure sentinel success field."""
    return val is False or (isinstance(val, str) and val.lower() == "false")


_PATH_SAFE_LOOKBEHIND = r"(?<![.a-zA-Z0-9_/])"
_PATH_SAFE_LOOKAHEAD = r"(?![.a-zA-Z0-9_/])"


def cmd_keyword_pattern(
    keywords: str,
    *,
    flags: int = 0,
    lookahead: bool = True,
) -> re.Pattern[str]:
    """Build a keyword-matching regex with automatic path-safe guards.

    The returned pattern wraps the keyword alternation with:
    - A negative lookbehind rejecting ``.``, letters, digits, ``_``, ``/`` before the match
    - A negative lookahead rejecting ``.``, letters, digits, ``_``, ``/`` after the match
      (or ``(?!\\w)`` when lookahead=False for symmetric word-boundary semantics)

    Args:
        keywords: A regex alternation string (e.g., ``r"mapfile|declare|local|export"``).
        flags: Additional ``regex`` flags (e.g., ``re.VERBOSE``).
        lookahead: If True (default), adds path-safe lookahead. If False, uses ``(?!\\w)``
            for symmetric word-boundary semantics without path-char filtering.
    """
    tail = _PATH_SAFE_LOOKAHEAD if lookahead else r"(?!\w)"
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
