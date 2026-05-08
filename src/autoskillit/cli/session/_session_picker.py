"""Scoped resume picker: shows a filtered list of cook or order sessions."""

from __future__ import annotations

import json
from pathlib import Path

from autoskillit.core import claude_code_project_dir

_ORDER_GREETING_PREFIXES = (
    "Today's special:",
    "Order up! Today's special:",
    "Order up! The kitchen",
    "Kitchen's open!",
    "Table for one!",
    "Fresh off the menu",
    "Welcome to Good Burger, home of the Good Burger, can I take your order?",
)


def pick_session(session_type: str, project_dir: Path) -> str | None:
    """Show filtered picker. Returns selected Claude session UUID or None (fresh start)."""
    from autoskillit.core import read_registry

    registry = read_registry(project_dir)
    sessions = _load_sessions_index(project_dir)
    filtered = [s for s in sessions if _classify_session(s, registry) == session_type]

    if not filtered:
        print(f"No {session_type} sessions found. Starting fresh.")
        return None

    return _run_picker(filtered, session_type, registry)


def _load_sessions_index(project_dir: Path) -> list[dict]:
    """Load sessions-index.json, filtering out sidechain entries."""
    index_path = claude_code_project_dir(str(project_dir)) / "sessions-index.json"
    try:
        entries: list[dict] = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    return [e for e in entries if not e.get("isSidechain")]


def _classify_session(entry: dict, registry: dict[str, dict]) -> str:
    """Classify session as 'cook' or 'order'.

    Uses registry lookup first, then greeting heuristic fallback.
    """
    session_id = entry.get("sessionId", "")
    for reg_entry in registry.values():
        if reg_entry.get("claude_session_id") == session_id:
            return str(reg_entry.get("session_type", "cook"))

    first_prompt = entry.get("firstPrompt", "")
    for prefix in _ORDER_GREETING_PREFIXES:
        if first_prompt.startswith(prefix):
            return "order"
    return "cook"


def _format_session_row(entry: dict, session_type: str, registry: dict[str, dict]) -> str:
    """Format a session entry as a display row."""
    recipe_name: str | None = None
    session_id = entry.get("sessionId", "")
    for reg_entry in registry.values():
        if reg_entry.get("claude_session_id") == session_id:
            recipe_name = reg_entry.get("recipe_name")
            break

    if session_type == "order" and recipe_name:
        badge = f"[order: {recipe_name}]"
    elif session_type == "order":
        badge = "[order]"
    else:
        badge = "[cook]"

    summary = (entry.get("summary") or entry.get("firstPrompt", ""))[:60]
    branch = entry.get("gitBranch", "")
    modified = entry.get("modified", "")

    parts = [badge, summary]
    if branch:
        parts.append(branch)
    if modified:
        parts.append(modified)
    return "  ".join(p for p in parts if p)


def _run_picker(sessions: list[dict], session_type: str, registry: dict[str, dict]) -> str | None:
    """Print numbered list and prompt user for selection.

    Returns sessions[n-1]["sessionId"] on valid selection, None on 0.
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
            return str(sessions[choice - 1]["sessionId"])
        print(f"Out of range. Enter a number between 0 and {len(sessions)}.")

    return None
