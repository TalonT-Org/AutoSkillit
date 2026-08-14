"""PreToolUse session-replay fixture corpus for tests/hooks/test_session_replay.py.

Each ``.jsonl`` file's first line is a header object
``{"session_env": {...}, "state_setup": [...]}``; every subsequent line is one
event ``{"payload": {...}, "expectations": {"allowed": bool, "max_severity":
"none"|"neutral"|"failure"}}``. String values anywhere in the file may
contain the placeholder tokens ``{{ORCHESTRATING_ROOT}}`` and
``{{WORKTREE_ROOT}}``, substituted by the harness with real per-test tmp_path
directories before use.
"""

from __future__ import annotations

from pathlib import Path

INCIDENT_TRANSCRIPT: str = "incident_transcript_v1.jsonl"
INTERACTIVE_GITHUB_MUTATION: str = "interactive_github_mutation_v1.jsonl"


def fixture_path(name: str) -> Path:
    """Return the absolute path to a fixture file in this directory."""
    return Path(__file__).parent / name


__all__ = ["INCIDENT_TRANSCRIPT", "INTERACTIVE_GITHUB_MUTATION", "fixture_path"]
