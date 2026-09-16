"""Narrow CLI resume spelling into a prompt-free interactive launch intent."""

from __future__ import annotations

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
)

from ._session_picker import pick_session


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
