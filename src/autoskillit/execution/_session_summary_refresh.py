"""Refresh a single field of an already-committed session summary.json.

Reused recovery (``flush_session_log``'s ``reuse_committed_recovery``) skips
the full artifact rewrite, but later, more precise child-terminal-reason
evidence (issue #4623) must still reach the committed summary. A leaf module
with no dependency on ``session_log.py`` — the reverse direction is already
established (``session_log.py`` is imported by sibling recovery/collector
modules), so this logic cannot live there without a cycle.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from autoskillit.core import atomic_write, fast_dumps, fast_loads, get_logger

logger = get_logger(__name__)


def refresh_summary_child_outcomes(
    summary_path: Path, child_outcomes: Sequence[Mapping[str, Any]]
) -> None:
    """Patch only ``child_outcomes`` into ``summary_path``, preserving the rest."""
    try:
        committed = fast_loads(summary_path.read_text(encoding="utf-8"))
        if committed.get("child_outcomes") != child_outcomes:
            committed["child_outcomes"] = child_outcomes
            atomic_write(summary_path, fast_dumps(committed, sort_keys=True, indent=True) + "\n")
    except (OSError, ValueError):
        logger.debug("summary_child_outcomes_refresh_failed", exc_info=True)
