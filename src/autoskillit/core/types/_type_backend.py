"""Backend capability declaration type. Zero autoskillit imports."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["BackendCapabilities", "CLAUDE_CODE_CAPABILITIES"]


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    channel_b_capable: bool
    pty_required: bool
    session_resume_capable: bool
    skill_injection_capable: bool
    supports_thinking_blocks: bool
    exit_code_is_terminal: bool
    completion_record_types: frozenset[str]
    session_record_types: frozenset[str]


CLAUDE_CODE_CAPABILITIES: BackendCapabilities = BackendCapabilities(
    channel_b_capable=True,
    pty_required=True,
    session_resume_capable=True,
    skill_injection_capable=True,
    supports_thinking_blocks=True,
    exit_code_is_terminal=False,
    completion_record_types=frozenset({"result"}),
    session_record_types=frozenset({"assistant"}),
)
