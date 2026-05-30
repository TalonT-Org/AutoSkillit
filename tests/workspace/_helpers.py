"""Shared test constants for tests/workspace/."""

from __future__ import annotations

from autoskillit.core import BackendCapabilities

_CODEX_CAPABILITIES = BackendCapabilities(
    channel_b_capable=False,
    pty_required=False,
    session_resume_capable=True,
    skill_injection_capable=True,
    supports_thinking_blocks=False,
    supports_claude_format_stdout=False,
    exit_code_is_terminal=True,
    mcp_config_capable=True,
    food_truck_capable=True,
    completion_record_types=frozenset({"turn.completed", "turn.failed", "error"}),
    session_record_types=frozenset({"item.completed"}),
    required_session_files=frozenset({"config.toml"}),
    session_dir_symlinks=frozenset({"auth.json", ".env", "sessions"}),
    skills_subdir="skills",
)
