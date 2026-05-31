"""Shared helper utilities for recipe semantic rules."""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import regex as re

from autoskillit.core import get_logger
from autoskillit.recipe._analysis import ValidationContext

if TYPE_CHECKING:
    from autoskillit.recipe._contracts_types import SkillContract
    from autoskillit.recipe.schema import CampaignDispatch, Recipe, RecipeStep

logger = get_logger(__name__)


def _find_cycle_members(
    graph: dict[str, set[str]], recipe_steps: Mapping[str, RecipeStep]
) -> list[frozenset[str]]:
    """Find all sets of steps that participate in a routing cycle.

    Uses DFS back-edge detection. Returns a list of frozensets of step names
    that form cycles.
    """
    visited: set[str] = set()
    rec_stack: set[str] = set()
    cycles: list[frozenset[str]] = []

    def dfs(node: str, path: list[str]) -> None:
        visited.add(node)
        rec_stack.add(node)
        for neighbor in sorted(graph.get(node, set())):
            if neighbor not in recipe_steps:
                continue
            if neighbor not in visited:
                dfs(neighbor, path + [neighbor])
            elif neighbor in rec_stack and neighbor in path:
                cycles.append(frozenset(path[path.index(neighbor) :]))
        rec_stack.discard(node)

    for step_name in recipe_steps:
        if step_name not in visited:
            dfs(step_name, [step_name])

    return cycles


_SKILL_CMD_PATTERN = re.compile(r"/(?:autoskillit:)?([\w-]+)")
_ARG_TOKEN_PATTERN = re.compile(r"\$\{\{[^}]+\}\}|[^\s]+")


def count_skill_args(skill_command: str) -> int:
    """Count positional args in a skill_command after the skill name."""
    tokens = _ARG_TOKEN_PATTERN.findall(skill_command)
    return max(0, len(tokens) - 1)


# Maximum hops for BFS push-reachability checks.
_MAX_HOPS = 6

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


def _extract_sentinel_fields(recipe: Recipe) -> frozenset[str]:
    """Extract declared field names from all sentinel stop step JSON examples."""
    fields: set[str] = set()
    for step in recipe.steps.values():
        if step.action != "stop" or not step.message:
            continue
        if "sentinel" not in step.message.lower():
            continue
        for block in extract_sentinel_json_blocks(step.message):
            try:
                parsed = json.loads(block)
                if isinstance(parsed, dict):
                    fields.update(parsed.keys())
            except (json.JSONDecodeError, ValueError):
                continue
    return frozenset(fields)


def _load_dispatch_target(dispatch: CampaignDispatch, project_dir: Path | None) -> Recipe | None:
    """Load the target recipe for a dispatch. Returns None if not loadable."""
    if project_dir is None:
        return None
    try:
        from autoskillit.recipe.io import find_recipe_by_name, load_recipe  # noqa: PLC0415

        info = find_recipe_by_name(dispatch.recipe, project_dir)
        if info is None:
            return None
        return load_recipe(info.path)
    except Exception:
        logger.warning("dispatch_target_load_failed", recipe=dispatch.recipe, exc_info=True)
        return None


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


def _build_graph_without_nodes(
    graph: dict[str, set[str]], remove: set[str] | frozenset[str]
) -> dict[str, set[str]]:
    """Return graph copy with specified nodes removed from keys and successor sets."""
    return {k: v - remove for k, v in graph.items() if k not in remove}


def push_reachable(
    graph: dict[str, set[str]],
    start: str,
    recipe: Recipe,
    max_hops: int = _MAX_HOPS,
) -> tuple[bool, str | None]:
    """Return (reachable, push_step_name) if push_to_remote is reachable within max_hops.

    Returns (False, None) if no push_to_remote step is reachable.
    """
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque([(start, 0)])
    while queue:
        name, hops = queue.popleft()
        if name in visited:
            continue
        if hops > max_hops:
            continue
        visited.add(name)
        step = recipe.steps.get(name)
        if step is not None and step.tool == "push_to_remote":
            return True, name
        for succ in graph.get(name, set()):
            queue.append((succ, hops + 1))
    return False, None


def _identify_optional_output_fields(contract: SkillContract) -> set[str]:
    """Return output field names whose contract patterns allow an empty value.

    Cross-references ``contract.outputs`` names with ``expected_output_patterns``:
    a field is considered optional when its pattern contains a fully-optional capture
    group ``(...)? `` at the end (same check as ``_has_optional_capture_group``).
    Patterns that don't start with a recognized output name are skipped.
    """
    output_names = {o.name for o in contract.outputs}
    optional: set[str] = set()
    for pattern in contract.expected_output_patterns:
        if not re.search(r"\((?!\?:)[^)]+\)\?$", pattern):
            continue
        m = re.match(r"^([\w-]+)", pattern)
        if m and m.group(1) in output_names:
            optional.add(m.group(1))
    return optional
