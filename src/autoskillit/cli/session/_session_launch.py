"""Shared interactive session launch prelude for CLI commands."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from autoskillit.core import NamedResume, NoResume

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
    from autoskillit.cli.session._reload import consume_reload_sentinel
    from autoskillit.cli._terminal import terminal_guard
    from autoskillit.core import (
        MARKETPLACE_PREFIX,
        BareResume,
        ClaudeFlags,
        NamedResume,
        NoResume,
        detect_autoskillit_mcp_prefix,
        pkg_root,
    )
    from autoskillit.execution import build_interactive_cmd

    _project_dir = project_dir if project_dir is not None else Path.cwd()
    spec = build_interactive_cmd(
        initial_prompt=initial_message,
        resume_spec=resume_spec if resume_spec is not None else NoResume(),
        env_extras=extra_env,
    )
    plugin_flags = (
        []
        if detect_autoskillit_mcp_prefix() == MARKETPLACE_PREFIX
        else [ClaudeFlags.PLUGIN_DIR, str(pkg_root())]
    )
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


def _write_order_entry(project_dir: Path, recipe_name: str | None) -> dict[str, str]:
    import uuid

    from autoskillit.core import (
        LAUNCH_ID_ENV_VAR,
        SESSION_TYPE_ENV_VAR,
        SESSION_TYPE_ORDER,
        write_registry_entry,
    )

    lid = uuid.uuid4().hex[:16]
    write_registry_entry(project_dir, lid, SESSION_TYPE_ORDER, recipe_name)
    return {SESSION_TYPE_ENV_VAR: SESSION_TYPE_ORDER, LAUNCH_ID_ENV_VAR: lid}


def _launch_cook_session(
    system_prompt: str,
    *,
    initial_message: str | None = None,
    extra_env: dict[str, str] | None = None,
    resume_spec: ResumeSpec = NoResume(),
    project_dir: Path | None = None,
) -> None:
    """Launch an interactive Claude Code cook session with reload loop support."""
    _max_reloads = 10
    current_resume_spec = resume_spec
    _current_initial_message = initial_message
    seen_reload_ids: set[str] = set()
    while True:
        reload_id = _run_interactive_session(
            system_prompt,
            initial_message=_current_initial_message,
            extra_env=extra_env,
            resume_spec=current_resume_spec,
            project_dir=project_dir,
        )
        if reload_id is None:
            break
        if len(seen_reload_ids) >= _max_reloads:
            raise SystemExit(f"Too many reloads ({_max_reloads} max). Check for infinite loop.")
        if reload_id in seen_reload_ids:
            raise SystemExit(f"Repeated reload_id {reload_id!r} — aborting.")
        seen_reload_ids.add(reload_id)
        current_resume_spec = NamedResume(session_id=reload_id)
        _current_initial_message = None
