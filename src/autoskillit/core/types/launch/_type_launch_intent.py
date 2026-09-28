"""Closed CLI and launch intents for an interactive coding-agent session.

The variants are sealed for static callers with :func:`typing.final`; Python
does not prohibit subclassing them at runtime. Backend builders match directly
on ``InteractiveLaunch`` and terminate with :func:`typing.assert_never`, so a
new variant must be handled by every backend.

There is deliberately no bare-resume variant. ``BareResume`` describes CLI
spelling and must be narrowed to a concrete session id before launch. A plain
restore has no prompt field, while ``ResumeWithBriefing`` is the explicit
channel for resuming a session in order to hand it new work.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import final

__all__ = [
    "NoResume",
    "BareResume",
    "NamedResume",
    "ResumeSpec",
    "resume_spec_from_cli",
    "FreshLaunch",
    "RestoreSession",
    "ResumeWithBriefing",
    "InteractiveLaunch",
]


@dataclass(frozen=True, slots=True)
class NoResume:
    """No resume: start a fresh session."""


@dataclass(frozen=True, slots=True)
class BareResume:
    """Bare --resume: ask AutoSkillit to select a session."""


@dataclass(frozen=True, slots=True)
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


@final
@dataclass(frozen=True, slots=True)
class FreshLaunch:
    """Start a new interactive session with optional initial instructions."""

    system_prompt: str | None = None
    initial_prompt: str | None = None


@final
@dataclass(frozen=True, slots=True)
class RestoreSession:
    """Restore a session without submitting a new prompt."""

    session_id: str

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session ID must not be empty")


@final
@dataclass(frozen=True, slots=True)
class ResumeWithBriefing:
    """Resume a session and submit one explicitly authorized briefing."""

    session_id: str
    briefing: str

    def __post_init__(self) -> None:
        if not self.briefing:
            raise ValueError("briefing must not be empty")


InteractiveLaunch = FreshLaunch | RestoreSession | ResumeWithBriefing
