"""Claude CLI command builders for interactive and headless invocations."""

from __future__ import annotations

from dataclasses import dataclass, field

from autoskillit.core import ClaudeFlags


@dataclass(frozen=True)
class ClaudeInteractiveCmd:
    cmd: list[str]
    env: dict[str, str]


@dataclass(frozen=True)
class ClaudeHeadlessCmd:
    cmd: list[str]
    env: dict[str, str] = field(default_factory=dict)  # always {}


def build_interactive_cmd(
    *, initial_prompt: str | None = None, model: str | None = None
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
    """
    cmd = ["claude", ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
    if model:
        cmd += [ClaudeFlags.MODEL, model]
    if initial_prompt is not None:
        cmd.append(initial_prompt)
    return ClaudeInteractiveCmd(cmd=cmd, env={})


def build_headless_cmd(prompt: str, *, model: str | None = None) -> ClaudeHeadlessCmd:
    """Build a Claude headless session command for skill execution."""
    cmd = ["claude", ClaudeFlags.PRINT, prompt, ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
    if model:
        cmd += [ClaudeFlags.MODEL, model]
    return ClaudeHeadlessCmd(cmd=cmd, env={})
