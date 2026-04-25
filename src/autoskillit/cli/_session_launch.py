"""Shared interactive session launch prelude for CLI commands."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from autoskillit.core import ResumeSpec


def _run_interactive_session(
    system_prompt: str,
    *,
    initial_message: str | None = None,
    extra_env: dict[str, str] | None = None,
    resume_spec: ResumeSpec | None = None,
    project_dir: Path | None = None,
) -> str | None:
    """Launch an interactive Claude Code session; return session_id if reload sentinel found."""
    if shutil.which("claude") is None:
        print("ERROR: 'claude' not found. Install: https://docs.anthropic.com/en/docs/claude-code")
        sys.exit(1)
    from autoskillit.cli._init_helpers import _is_plugin_installed
    from autoskillit.cli._reload import consume_reload_sentinel
    from autoskillit.cli._terminal import terminal_guard
    from autoskillit.core import BareResume, ClaudeFlags, NamedResume, NoResume, pkg_root
    from autoskillit.execution import build_interactive_cmd

    _project_dir = project_dir if project_dir is not None else Path.cwd()
    spec = build_interactive_cmd(
        initial_prompt=initial_message,
        resume_spec=resume_spec if resume_spec is not None else NoResume(),
        env_extras=extra_env,
    )
    plugin_flags = [] if _is_plugin_installed() else [ClaudeFlags.PLUGIN_DIR, str(pkg_root())]
    _is_resume = isinstance(resume_spec, (BareResume, NamedResume))
    cmd = [
        *spec.cmd,
        *plugin_flags,
        ClaudeFlags.TOOLS,
        "AskUserQuestion",
        *([] if _is_resume else [ClaudeFlags.APPEND_SYSTEM_PROMPT, system_prompt]),
    ]
    with terminal_guard():
        result = subprocess.run(cmd, env=spec.env)
    reload_session_id = consume_reload_sentinel(_project_dir)
    if reload_session_id is not None:
        return reload_session_id
    if result.returncode != 0:
        sys.exit(result.returncode)
    return None
