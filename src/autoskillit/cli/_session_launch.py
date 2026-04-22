"""Shared interactive session launch prelude for CLI commands."""

from __future__ import annotations

import shutil
import subprocess
import sys


def _run_interactive_session(
    system_prompt: str,
    *,
    initial_message: str | None = None,
    extra_env: dict[str, str] | None = None,
    resume_session_id: str | None = None,
) -> None:
    """Launch an interactive Claude Code session with the given system prompt."""
    if shutil.which("claude") is None:
        print("ERROR: 'claude' not found. Install: https://docs.anthropic.com/en/docs/claude-code")
        sys.exit(1)
    from autoskillit.cli._init_helpers import _is_plugin_installed
    from autoskillit.cli._terminal import terminal_guard
    from autoskillit.core import ClaudeFlags, pkg_root
    from autoskillit.execution import build_interactive_cmd

    spec = build_interactive_cmd(
        initial_prompt=initial_message,
        resume_session_id=resume_session_id,
        env_extras=extra_env,
    )
    plugin_flags = [] if _is_plugin_installed() else [ClaudeFlags.PLUGIN_DIR, str(pkg_root())]
    cmd = [
        *spec.cmd,
        *plugin_flags,
        ClaudeFlags.TOOLS,
        "AskUserQuestion",
        ClaudeFlags.APPEND_SYSTEM_PROMPT,
        system_prompt,
    ]
    with terminal_guard():
        result = subprocess.run(cmd, env=spec.env)
    if result.returncode != 0:
        sys.exit(result.returncode)
