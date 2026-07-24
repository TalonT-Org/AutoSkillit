"""Scoped resume picker: shows a filtered list of cook or order sessions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from autoskillit.core import (
    AGENT_BACKEND_CLAUDE_CODE,
    SessionLocator,
    SessionSummary,
)

_ORDER_GREETING_PREFIXES = (
    "Today's special:",
    "Order up! Today's special:",
    "Order up! The kitchen",
    "Kitchen's open!",
    "Table for one!",
    "Fresh off the menu",
    "Welcome to Good Burger, home of the Good Burger, can I take your order?",
)

_Registry = Mapping[str, Mapping[str, object]]


def pick_session(
    session_type: str,
    project_dir: Path,
    summaries_or_locator: Sequence[SessionSummary] | SessionLocator,
) -> str | None:
    """Show the filtered picker and return the selected backend session ID."""
    from autoskillit.core import read_registry

    registry = read_registry(project_dir)
    if isinstance(summaries_or_locator, Sequence):
        summaries = summaries_or_locator
    else:
        summaries = summaries_or_locator.list_sessions(str(project_dir))
    filtered = [
        summary
        for summary in summaries
        if not summary.is_sidechain and _classify_session(summary, registry) == session_type
    ]

    if not filtered:
        print(f"No {session_type} sessions found. Starting fresh.")
        return None

    return _run_picker(filtered, session_type, registry)


def _registry_entry(
    summary: SessionSummary,
    registry: _Registry,
) -> Mapping[str, object] | None:
    if summary.launch_id is not None:
        return registry.get(summary.launch_id)
    if summary.backend_name != AGENT_BACKEND_CLAUDE_CODE:
        return None
    for entry in registry.values():
        if entry.get("claude_session_id") == summary.session_id:
            return entry
    return None


def _classify_session(summary: SessionSummary, registry: _Registry) -> str:
    """Classify session as 'cook' or 'order'.

    Uses registry lookup first, then locator and greeting hints.
    """
    registry_entry = _registry_entry(summary, registry)
    if registry_entry is not None:
        return str(registry_entry.get("session_type", "cook"))

    if summary.session_type_hint is not None:
        return summary.session_type_hint

    for prefix in _ORDER_GREETING_PREFIXES:
        if summary.first_prompt.startswith(prefix):
            return "order"
    return "cook"


def _format_session_row(
    summary: SessionSummary,
    session_type: str,
    registry: _Registry,
) -> str:
    """Format a session entry as a display row."""
    recipe_name: str | None = None
    registry_entry = _registry_entry(summary, registry)
    if registry_entry is not None:
        raw_recipe_name = registry_entry.get("recipe_name")
        recipe_name = raw_recipe_name if isinstance(raw_recipe_name, str) else None

    if session_type == "order" and recipe_name:
        badge = f"[order: {recipe_name}]"
    elif session_type == "order":
        badge = "[order]"
    else:
        badge = "[cook]"

    display_summary = (summary.summary or summary.first_prompt)[:60]
    branch = summary.git_branch or ""
    modified = summary.modified or ""

    parts = [badge, display_summary]
    if branch:
        parts.append(branch)
    if modified:
        parts.append(modified)
    return "  ".join(p for p in parts if p)


def _run_picker(
    sessions: Sequence[SessionSummary],
    session_type: str,
    registry: _Registry,
) -> str | None:
    """Print numbered list and prompt user for selection.

    Returns the selected backend session ID on valid selection, None on 0.
    Re-prompts on invalid input (max 3 retries, then returns None).
    """
    print(f"\nResume a {session_type} session:")
    print("  0. Start fresh session")
    for i, entry in enumerate(sessions, 1):
        row = _format_session_row(entry, session_type, registry)
        print(f"  {i}. {row}")

    from autoskillit.cli.ui._timed_input import timed_prompt

    max_retries = 3
    for _ in range(max_retries):
        try:
            raw = timed_prompt(
                f"\nSelect [0-{len(sessions)}]: ", timeout=0, label="session picker"
            )
        except KeyboardInterrupt:
            return None

        if not raw:
            continue
        try:
            choice = int(raw)
        except ValueError:
            print(f"Invalid input '{raw}'. Enter a number between 0 and {len(sessions)}.")
            continue

        if choice == 0:
            return None
        if 1 <= choice <= len(sessions):
            return sessions[choice - 1].session_id
        print(f"Out of range. Enter a number between 0 and {len(sessions)}.")

    return None
