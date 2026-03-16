"""Claude CLI command builders for interactive and headless invocations."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from autoskillit.core import ClaudeFlags, ValidatedAddDir


@dataclass(frozen=True)
class ClaudeInteractiveCmd:
    cmd: list[str]
    env: dict[str, str]


@dataclass(frozen=True)
class ClaudeHeadlessCmd:
    cmd: list[str]
    env: dict[str, str] = field(default_factory=dict)  # always {}


def build_interactive_cmd(
    *,
    initial_prompt: str | None = None,
    model: str | None = None,
    plugin_dir: Path | None = None,
    add_dirs: Sequence[Path | str | ValidatedAddDir] = (),
) -> ClaudeInteractiveCmd:
    """Build a Claude interactive session command.

    Parameters
    ----------
    initial_prompt
        When provided, appended as a positional argument. Claude Code treats
        positional arguments as the user's first message, auto-submitted on
        session start.
    model
        Optional model override.
    plugin_dir
        When provided, appended as ``--plugin-dir <path>``.
    add_dirs
        Each entry is appended as ``--add-dir <path>``.
    """
    cmd = ["claude", ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
    if model:
        cmd += [ClaudeFlags.MODEL, model]
    if plugin_dir is not None:
        cmd += [ClaudeFlags.PLUGIN_DIR, str(plugin_dir)]
    for d in add_dirs:
        cmd += [ClaudeFlags.ADD_DIR, str(d)]
    if initial_prompt is not None:
        cmd.append(initial_prompt)
    return ClaudeInteractiveCmd(cmd=cmd, env={})


def build_headless_cmd(prompt: str, *, model: str | None = None) -> ClaudeHeadlessCmd:
    """Build a Claude headless session command for skill execution."""
    cmd = ["claude", ClaudeFlags.PRINT, prompt, ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
    if model:
        cmd += [ClaudeFlags.MODEL, model]
    return ClaudeHeadlessCmd(cmd=cmd, env={})
