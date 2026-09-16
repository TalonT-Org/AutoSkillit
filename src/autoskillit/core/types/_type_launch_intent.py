"""Closed launch intent for an interactive coding-agent session.

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
    "FreshLaunch",
    "RestoreSession",
    "ResumeWithBriefing",
    "InteractiveLaunch",
]


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
