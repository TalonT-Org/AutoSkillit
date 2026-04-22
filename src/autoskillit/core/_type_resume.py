"""Resume intent discriminated union for interactive session launch.

Three sealed variants make the three user intents structurally distinct:
- NoResume: start a fresh session (no --resume flag)
- BareResume: pass --resume without an ID (delegate to Claude Code's picker)
- NamedResume: pass --resume <id> (resume a specific session)
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = [
    "NoResume",
    "BareResume",
    "NamedResume",
    "ResumeSpec",
    "resume_spec_from_cli",
]


@dataclass(frozen=True)
class NoResume:
    """No resume: start a fresh session."""


@dataclass(frozen=True)
class BareResume:
    """Bare --resume: delegate session selection to Claude Code's picker."""


@dataclass(frozen=True)
class NamedResume:
    """--resume <id>: resume a specific named session."""

    session_id: str


ResumeSpec = NoResume | BareResume | NamedResume


def resume_spec_from_cli(*, resume: bool, session_id: str | None) -> ResumeSpec:
    """Construct a ResumeSpec from CLI input without any I/O."""
    if session_id:
        return NamedResume(session_id=session_id)
    if resume:
        return BareResume()
    return NoResume()
