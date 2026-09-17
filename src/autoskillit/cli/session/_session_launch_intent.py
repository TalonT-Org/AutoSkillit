"""Narrow CLI resume spelling into a prompt-free interactive launch intent."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import assert_never

from autoskillit.core import (
    BareResume,
    CodingAgentBackend,
    FreshLaunch,
    InteractiveLaunch,
    NamedResume,
    NoResume,
    RestoreSession,
    ResumeSpec,
    SessionLocator,
    SessionSummary,
    get_logger,
)
from autoskillit.execution import default_tether_dir, sweep_orphaned_tethers

_Registry = Mapping[str, Mapping[str, object]]  # registry row shape produced by read_registry
logger = get_logger(__name__)


def prepare_resume_housekeeping(backend: CodingAgentBackend) -> None:
    """Run the resume-related side effects that previously lived inside resolve_interactive_launch.

    Cook and order invoke this before resolving a launch so the resolver stays pure.
    """
    try:
        sweep_orphaned_tethers(default_tether_dir())
    except (OSError, PermissionError, FileNotFoundError):
        logger.warning(
            "interactive_startup_tether_sweep_failed",
            tether_dir=str(default_tether_dir()),
            exc_info=True,
        )
    backend.recover_cook_history()


def resolve_interactive_launch(
    *,
    resume_spec: ResumeSpec,
    session_type: str,
    project_dir: Path,
    backend: CodingAgentBackend,
) -> InteractiveLaunch:
    """Resolve CLI resume input to either a fresh launch or a concrete restore."""
    match resume_spec:
        case NamedResume(session_id=session_id):
            return RestoreSession(session_id=session_id)
        case BareResume():
            selected_id = pick_session(session_type, project_dir, backend.session_locator())
            if selected_id is not None:
                return RestoreSession(session_id=selected_id)
            return FreshLaunch()
        case NoResume():
            return FreshLaunch()
        case _ as unreachable:
            assert_never(unreachable)


def _run_fresh_launch_ceremony(
    *,
    launch: InteractiveLaunch,
    is_tty: bool,
    label: str,
    before_prompt: Callable[[], None] | None = None,
    timeout: int = 120,
) -> bool:
    """Run the fresh-launch + TTY confirm ceremony shared by cook and order.

    Returns True when the launch should proceed; False when the user cancelled
    the confirmation prompt. The ceremony is skipped when ``launch`` is not a
    ``FreshLaunch`` or the TTY is unavailable. ``before_prompt`` is invoked
    only for fresh launches; it can render previews or warnings.
    """
    if not isinstance(launch, FreshLaunch):
        return True
    if before_prompt is not None:
        before_prompt()
    if not is_tty:
        return True
    from autoskillit.cli.ui._timed_input import timed_prompt

    confirm = timed_prompt(
        "\nLaunch session? [Enter/n]",
        default="",
        timeout=timeout,
        label=label,
    )
    return confirm.lower() not in ("n", "no")


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
    if summary.launch_id is None:
        return None
    return registry.get(summary.launch_id)


def _classify_session(summary: SessionSummary, registry: _Registry) -> str:
    """Classify a session from registry authority, then backend evidence."""
    registry_entry = _registry_entry(summary, registry)
    if registry_entry is not None:
        return str(registry_entry.get("session_type", "cook"))
    if summary.session_type_hint is not None:
        return summary.session_type_hint
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
    return "  ".join(part for part in (badge, display_summary, branch, modified) if part)


def _run_picker(
    sessions: Sequence[SessionSummary],
    session_type: str,
    registry: _Registry,
) -> str | None:
    """Prompt for one filtered session, returning ``None`` for a fresh launch."""
    print(f"\nResume a {session_type} session:")
    print("  0. Start fresh session")
    for index, entry in enumerate(sessions, 1):
        print(f"  {index}. {_format_session_row(entry, session_type, registry)}")

    from autoskillit.cli.ui._timed_input import timed_prompt

    for _ in range(3):
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
