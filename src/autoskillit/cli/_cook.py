"""cook command: interactive skill session launcher."""

from __future__ import annotations

import os
import random
import shutil
import subprocess
import uuid
from pathlib import Path


def cook() -> None:
    """Launch Claude with all bundled AutoSkillit skills as slash commands."""
    from autoskillit.workspace import (
        DefaultSessionSkillManager,
        SkillsDirectoryProvider,
        resolve_ephemeral_root,
    )

    if not shutil.which("claude"):
        print("'claude' not found on PATH. Install Claude Code to use cook.")
        raise SystemExit(1)

    from autoskillit import __version__
    from autoskillit.cli._ansi import supports_color
    from autoskillit.core import TOOL_CATEGORIES

    color = supports_color()
    _B = "\x1b[1m" if color else ""
    _C = "\x1b[96m" if color else ""
    _D = "\x1b[2m" if color else ""
    _G = "\x1b[32m" if color else ""
    _Y = "\x1b[33m" if color else ""
    _R = "\x1b[0m" if color else ""

    print(f"{_B}{_C}AUTOSKILLIT {__version__}{_R} {_D}Kitchen open. All tools active.{_R}")
    skip = {"Telemetry & Diagnostics", "Kitchen"}
    for name, tools in TOOL_CATEGORIES:
        if name in skip:
            continue
        tool_list = f"{_D}, {_R}".join(f"{_G}{t}{_R}" for t in tools)
        print(f"  {_Y}{name:>20}{_R}  {tool_list}")
    print()

    from autoskillit.cli._ansi import permissions_warning

    print(permissions_warning())
    confirm = input("\nLaunch session? [Enter/n]: ").strip().lower()
    if confirm in ("n", "no"):
        return

    from autoskillit.cli._prompts import _OPEN_KITCHEN_GREETINGS, _build_open_kitchen_prompt
    from autoskillit.config import load_config
    from autoskillit.core import ClaudeFlags, configure_logging, pkg_root
    from autoskillit.execution import build_interactive_cmd

    configure_logging()

    session_id = uuid.uuid4().hex[:16]
    ephemeral_root = resolve_ephemeral_root()
    session_mgr = DefaultSessionSkillManager(SkillsDirectoryProvider(), ephemeral_root)
    config = load_config()
    skills_dir = session_mgr.init_session(
        session_id, cook_session=True, config=config, project_dir=Path.cwd()
    )

    spec = build_interactive_cmd(
        plugin_dir=pkg_root(),
        add_dirs=[skills_dir],
        initial_prompt=random.choice(_OPEN_KITCHEN_GREETINGS),
    )
    cmd = spec.cmd + [ClaudeFlags.APPEND_SYSTEM_PROMPT, _build_open_kitchen_prompt()]
    env = {**os.environ}
    try:
        result = subprocess.run(cmd, env=env)
        if result.returncode != 0:
            raise SystemExit(result.returncode)
    finally:
        shutil.rmtree(skills_dir, ignore_errors=True)
