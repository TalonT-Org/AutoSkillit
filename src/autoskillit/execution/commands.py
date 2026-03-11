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


def build_interactive_cmd(*, model: str | None = None) -> ClaudeInteractiveCmd:
    """Build a Claude interactive session command."""
    cmd = ["claude", ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
    if model:
        cmd += [ClaudeFlags.MODEL, model]
    return ClaudeInteractiveCmd(cmd=cmd, env={})


def build_headless_cmd(prompt: str, *, model: str | None = None) -> ClaudeHeadlessCmd:
    """Build a Claude headless session command for skill execution."""
    cmd = ["claude", ClaudeFlags.PRINT, prompt, ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
    if model:
        cmd += [ClaudeFlags.MODEL, model]
    return ClaudeHeadlessCmd(cmd=cmd, env={})


def build_subrecipe_cmd(prompt: str, *, model: str | None = None) -> ClaudeHeadlessCmd:
    """Build a Claude headless command for sub-recipe orchestration.

    Sets AUTOSKILLIT_KITCHEN_OPEN=1 so make_context() (factory.py) pre-enables
    the gate and server/__init__.py pre-reveals all kitchen tools at import time.
    AUTOSKILLIT_HEADLESS is intentionally NOT set — sub-recipe sessions must not
    trigger the open_kitchen_guard hook since they never call open_kitchen.
    """
    cmd = ["claude", ClaudeFlags.PRINT, prompt, ClaudeFlags.DANGEROUSLY_SKIP_PERMISSIONS]
    if model:
        cmd += [ClaudeFlags.MODEL, model]
    return ClaudeHeadlessCmd(cmd=cmd, env={"AUTOSKILLIT_KITCHEN_OPEN": "1"})
